"""Control panel: serves the UI, the bundled integrations, and a small JSON API."""

import io
import json
import mimetypes
import posixpath
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import fonts as font_list


THUMBS = {}          # (path, mtime, size) -> jpeg bytes; a few dozen small pictures


def thumbnail(path, size=96):
    """Small copy for the picker, so the panel does not load full pictures."""
    from PIL import Image
    key = (str(path), path.stat().st_mtime, size)
    if key not in THUMBS:
        with Image.open(path) as img:
            img = img.convert("RGB")
            img.thumbnail((size, size), Image.LANCZOS)
            buffer = io.BytesIO()
            img.save(buffer, format="JPEG", quality=80)
        THUMBS[key] = buffer.getvalue()
    return THUMBS[key]


class Handler(BaseHTTPRequestHandler):
    controller = None
    web_dir = None
    integrations_dir = None
    images_dir = None
    bundled_dir = None
    fonts_cache = None
    frame_path = None
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass                                  # the journal does not need a line per poll

    def reply(self, status, body, content_type="application/json"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode()
        elif isinstance(body, str):
            body = body.encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def send_file(self, path):
        try:
            body = path.read_bytes()
        except OSError:
            return self.reply(404, {"error": f"{path.name} not found"})
        kind = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        return self.reply(200, body, kind)

    def safe_path(self, root, relative):
        """Keep integration URLs inside the integrations folder."""
        clean = posixpath.normpath("/" + relative).lstrip("/")
        target = (root / clean).resolve()
        if root.resolve() not in target.parents and target != root.resolve():
            return None
        return target

    def do_GET(self):
        path = urllib.parse.urlsplit(self.path).path
        if path in ("/", "/index.html"):
            return self.send_file(self.web_dir / "index.html")

        if path.startswith("/integrations/"):
            relative = path[len("/integrations/"):]
            target = self.safe_path(self.integrations_dir, relative)
            if target is None:
                return self.reply(403, {"error": "outside the integrations folder"})
            if target.is_dir():
                target = target / "index.html"
            return self.send_file(target)

        for prefix, root in (("/images/", self.images_dir), ("/bundled/", self.bundled_dir)):
            if path.startswith(prefix):
                target = self.safe_path(root, path[len(prefix):])
                if target is None:
                    return self.reply(403, {"error": f"outside {prefix}"})
                if "thumb" in urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query):
                    try:
                        return self.reply(200, thumbnail(target), "image/jpeg")
                    except Exception:
                        return self.send_file(target)
                return self.send_file(target)

        if path == "/api/state":
            state = {
                "core": self.controller.settings.get_core(),
                "integration": self.controller.describe(),
                "status": self.controller.status(),
                "sensors": self.controller.cc.sensors,
                "history": self.controller.settings.get_history(),
                "fonts": font_list.catalogue(self.fonts_cache),
                "images": self.list_images(self.images_dir, "/images/"),
                "bundled": self.list_images(self.bundled_dir, "/bundled/"),
            }
            return self.reply(200, state)

        if path == "/api/preview.png":
            try:
                return self.reply(200, self.frame_path.read_bytes(), "image/png")
            except OSError:
                return self.reply(404, {"error": "no frame yet"})

        return self.reply(404, {"error": "not found"})

    @staticmethod
    def list_images(root, prefix):
        """Pictures on offer: images/ is yours to fill, bundled/ ships with the project."""
        allowed = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
        try:
            names = sorted(entry.name for entry in root.iterdir()
                           if entry.is_file() and entry.suffix.lower() in allowed)
        except OSError:
            return []
        return [{"name": name, "url": prefix + name} for name in names]

    def read_json(self):
        length = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(length) or b"{}")

    def do_POST(self):
        path = urllib.parse.urlsplit(self.path).path
        try:
            payload = self.read_json()
        except (ValueError, TypeError) as err:
            return self.reply(400, {"error": f"bad json: {err}"})

        if path == "/api/load":
            url = str(payload.get("url", "")).strip()
            if not url.startswith(("http://", "https://", "file://")):
                return self.reply(400, {"error": "url must start with http://, https:// or file://"})
            return self.reply(200, self.controller.load_integration(url))

        if path == "/api/settings":
            return self.reply(200, self.controller.update_settings(payload))

        if path == "/api/core":
            return self.reply(200, self.controller.settings.set_core(payload))

        if path == "/api/reload":
            return self.reply(200, self.controller.reload())

        if path == "/api/history/clear":
            keep = self.controller.settings.get_core()["integration_url"]
            return self.reply(200, {"history": self.controller.settings.clear_history(keep)})

        if path == "/api/forget":
            url = str(payload.get("url", "")).strip()
            if url == self.controller.settings.get_core()["integration_url"]:
                return self.reply(400, {"error": "that integration is loaded right now"})
            return self.reply(200, {"history": self.controller.settings.forget(url)})

        return self.reply(404, {"error": "not found"})


def serve(controller, base_dir, frame_path, port):
    Handler.controller = controller
    Handler.web_dir = Path(base_dir) / "web"
    Handler.integrations_dir = Path(base_dir) / "integrations"
    Handler.images_dir = Path(base_dir) / "images"
    Handler.images_dir.mkdir(exist_ok=True)
    Handler.fonts_cache = Path(base_dir) / "fonts.json"
    Handler.bundled_dir = Path(base_dir) / "bundled"
    Handler.bundled_dir.mkdir(exist_ok=True)
    Handler.frame_path = Path(frame_path)
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    print(f"control panel on http://127.0.0.1:{port}", flush=True)
    return server
