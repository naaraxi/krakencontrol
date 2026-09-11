"""Config file handling.

Three parts: `core` is device plumbing that belongs to krakencontrol itself,
`integrations` holds one settings block per integration URL, so switching between
integrations and back keeps what you had set, and `history` is the list of
integrations that have been loaded, most recent first.
"""

import json
import os
import threading
import time
from pathlib import Path

HISTORY_LIMIT = 20

CORE_DEFAULTS = {
    "integration_url": "",     # filled in with the bundled one on first run
    "interval": 2.5,
    "brightness": 80,
    "orientation": 0,
    "http_port": 8770,
}


def clamp(value, low, high):
    return max(low, min(high, value))


class Settings:
    def __init__(self, path):
        self.path = Path(path)
        self.lock = threading.RLock()
        self.core = dict(CORE_DEFAULTS)
        self.integrations = {}
        self.history = []
        self.load()

    def load(self):
        try:
            with open(self.path) as handle:
                stored = json.load(handle)
        except FileNotFoundError:
            return
        except (OSError, ValueError) as err:
            print(f"config unreadable, using defaults: {err}", flush=True)
            return
        with self.lock:
            self.core.update({k: v for k, v in (stored.get("core") or {}).items()
                              if k in CORE_DEFAULTS})
            self.integrations = dict(stored.get("integrations") or {})
            self.history = [{k: v for k, v in entry.items() if k in ("url", "last_used")}
                            for entry in (stored.get("history") or [])
                            if isinstance(entry, dict) and entry.get("url")][:HISTORY_LIMIT]
            self.core = self.clean_core(self.core)

    def save(self):
        with self.lock:
            payload = {"core": self.core, "integrations": self.integrations,
                       "history": self.history}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        staging = self.path.with_suffix(".tmp")
        with open(staging, "w") as handle:
            json.dump(payload, handle, indent=2)
            handle.write("\n")
        os.replace(staging, self.path)

    @staticmethod
    def clean_core(core):
        clean = dict(CORE_DEFAULTS)
        clean.update({k: v for k, v in core.items() if k in CORE_DEFAULTS})
        clean["interval"] = clamp(float(clean["interval"] or 2.5), 0.5, 60.0)
        clean["brightness"] = int(clamp(int(clean["brightness"]), 0, 100))
        if int(clean["orientation"]) not in (0, 90, 180, 270):
            clean["orientation"] = 0
        clean["orientation"] = int(clean["orientation"])
        clean["http_port"] = int(clamp(int(clean["http_port"]), 1024, 65535))
        clean["integration_url"] = str(clean["integration_url"]).strip()
        return clean

    # ---------- accessors ----------

    def get_core(self):
        with self.lock:
            return dict(self.core)

    def set_core(self, values):
        with self.lock:
            merged = dict(self.core)
            merged.update({k: v for k, v in values.items() if k in CORE_DEFAULTS})
            self.core = self.clean_core(merged)
            result = dict(self.core)
        self.save()
        return result

    def get_integration(self, url):
        with self.lock:
            return dict(self.integrations.get(url) or {})

    def set_integration(self, url, values, replace=False):
        """Store an integration's settings.

        `replace` drops keys the manifest no longer declares - callers that have run
        the values through the manifest already hold the complete set, and merging
        would leave settings from fields that have since been removed.
        """
        with self.lock:
            block = {} if replace else dict(self.integrations.get(url) or {})
            block.update(values)
            self.integrations[url] = block
            result = dict(block)
        self.save()
        return result

    # ---------- history ----------

    def get_history(self):
        with self.lock:
            return [dict(entry) for entry in self.history]

    def record_use(self, url, bump=True):
        """Move this integration to the front of the history, keeping first_used.

        The URL is the identity - nothing here is renamed by what a page calls itself.
        `bump=False` only makes sure the entry exists, for the daemon restoring the
        last integration at startup, which is not a use anyone asked for.
        """
        now = time.time()
        with self.lock:
            previous = next((e for e in self.history if e.get("url") == url), None)
            if previous and not bump:
                return
            entry = {"url": url, "last_used": now}   # last_used only orders the list
            self.history = [e for e in self.history if e.get("url") != url]
            self.history.insert(0, entry)
            del self.history[HISTORY_LIMIT:]
        self.save()

    def forget(self, url):
        """Drop a history entry and the settings that went with it."""
        with self.lock:
            self.history = [e for e in self.history if e.get("url") != url]
            self.integrations.pop(url, None)
        self.save()
        return self.get_history()

    def clear_history(self, keep_url=""):
        """Empty the history, and the settings that went with it.

        The loaded integration is kept: it is not history, it is what is on screen,
        and dropping its settings would reconfigure the panel out from under it.
        """
        with self.lock:
            self.history = [e for e in self.history if e.get("url") == keep_url]
            self.integrations = {url: values for url, values in self.integrations.items()
                                 if url == keep_url}
        self.save()
        return self.get_history()
