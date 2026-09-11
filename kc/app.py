"""The render loop: page in, frame out."""

import json
import os
import threading
import time
from pathlib import Path

from . import integration as integrations


class Controller:
    def __init__(self, settings, cooler, browser, frame_path):
        self.settings = settings
        self.cc = cooler
        self.browser = browser
        self.frame_path = Path(frame_path)
        self.lock = threading.RLock()
        self.manifest = None
        self.manifest_error = ""
        self.loaded_url = ""
        self.last_error = ""
        self.last_frame_at = 0.0
        self.last_values = {}
        self.wake = threading.Event()
        self.running = True

    # ---------- integrations ----------

    def load_integration(self, url, bump=True):
        """Point the LCD at a page and pick up whatever settings it offers."""
        url = url.strip()
        manifest, error = integrations.fetch_manifest(url) if url else (None, "no url")
        with self.lock:
            self.manifest = manifest
            self.manifest_error = error if manifest is None else ""
        self.settings.set_core({"integration_url": url})

        stored = self.settings.get_integration(url)
        merged = integrations.defaults_for(manifest)
        merged.update(stored)
        self.settings.set_integration(url, integrations.coerce(
            manifest, merged, self.cc.resolve_sensor), replace=True)

        self.settings.record_use(url, bump=bump)

        with self.lock:
            self.loaded_url = ""          # forces a navigate on the next frame
        self.wake.set()
        return self.describe()

    def reload(self):
        """Pick up edits to an integration: the manifest as well as the page.

        Re-navigating alone is not enough - a new field in integration.json would be
        dropped as unknown, because settings are checked against the manifest held here.
        """
        url = self.settings.get_core()["integration_url"]
        if not url:
            return {"url": ""}
        return self.load_integration(url, bump=False)

    def describe(self):
        with self.lock:
            manifest = self.manifest
            error = self.manifest_error
        url = self.settings.get_core()["integration_url"]
        return {
            "url": url,
            "description": (manifest or {}).get("description", ""),
            "settings": (manifest or {}).get("settings", []),
            "values": self.settings.get_integration(url),
            "manifest_error": error,
        }

    def update_settings(self, values):
        url = self.settings.get_core()["integration_url"]
        with self.lock:
            manifest = self.manifest
        stored = self.settings.get_integration(url)
        stored.update(values)
        saved = self.settings.set_integration(url, integrations.coerce(
            manifest, stored, self.cc.resolve_sensor), replace=True)
        self.wake.set()
        return saved

    # ---------- frames ----------

    def payload(self, core, values, temperatures):
        with self.lock:
            manifest = self.manifest
        sensors = {}
        for key in integrations.sensor_keys(manifest):
            sensor_id = values.get(key) or self.cc.default_sensor_id()
            sensors[key] = {
                "id": sensor_id,
                "label": self.cc.sensor_label(sensor_id),
                "value": temperatures.get(sensor_id),
                "unit": "C",
            }
        return {
            "settings": values,
            "sensors": sensors,
            "device": {"width": self.cc.width, "height": self.cc.height},
            "interval": core["interval"],
            "ts": time.time(),
        }

    def write_frame(self, png):
        self.frame_path.parent.mkdir(parents=True, exist_ok=True)
        staging = self.frame_path.with_suffix(".tmp")
        with open(staging, "wb") as handle:
            handle.write(png)
        os.chmod(staging, 0o644)
        os.replace(staging, self.frame_path)   # the daemon never sees a half-written file

    def tick(self):
        core = self.settings.get_core()
        url = core["integration_url"]
        if not url:
            raise RuntimeError("no integration loaded")

        self.browser.ensure()
        if self.loaded_url != url or self.browser.url != url:
            self.browser.navigate(url)
            with self.lock:
                self.loaded_url = url

        temperatures = self.cc.temperatures()
        values = self.settings.get_integration(url)
        payload = self.payload(core, values, temperatures)

        delivered = self.browser.evaluate(
            "typeof kcUpdate === 'function' ? (kcUpdate("
            + json.dumps(payload) + "), true) : false")

        # A family the page has just asked Google for is not there yet, and a frame taken
        # now would be measured and drawn in the fallback face.
        self.browser.evaluate(
            "document.fonts && document.fonts.status === 'loaded' ? true"
            " : new Promise(done => {"
            "   setTimeout(() => done(false), 1500);"
            "   document.fonts.ready.then(() => done(true));"
            " })")
        self.write_frame(self.browser.screenshot())
        self.cc.push_image(self.frame_path, core["brightness"], core["orientation"])

        with self.lock:
            self.last_error = "" if delivered else "page has no kcUpdate(), showing it as-is"
            self.last_frame_at = time.time()
            self.last_values = {k: v["value"] for k, v in payload["sensors"].items()}

    def status(self):
        core = self.settings.get_core()
        with self.lock:
            return {
                "device_name": self.cc.device_name,
                "channel": self.cc.channel,
                "width": self.cc.width,
                "height": self.cc.height,
                "error": self.last_error,
                "last_frame_at": self.last_frame_at,
                "values": dict(self.last_values),
                "browser": self.browser.alive(),
                "core": core,
            }

    def run(self):
        failures = 0
        while self.running:
            started = time.monotonic()
            try:
                self.tick()
                failures = 0
            except Exception as err:            # keep the last frame up and retry
                failures += 1
                with self.lock:
                    self.last_error = str(err)
                print(f"frame failed ({failures}): {err}", flush=True)
                if failures in (3, 9):          # a wedged browser is the usual cause
                    self.browser.stop()

            interval = self.settings.get_core()["interval"]
            delay = interval * min(failures + 1, 8) if failures else interval
            self.wake.wait(max(0.0, delay - (time.monotonic() - started)))
            self.wake.clear()

    def stop(self):
        self.running = False
        self.wake.set()
