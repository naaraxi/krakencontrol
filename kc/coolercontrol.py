"""CoolerControl daemon client.

krakencontrol never touches the device directly. CoolerControl owns it; we read
sensors and hand over finished frames. Note that reads need the bearer token too,
not just writes.
"""

import http.client
import json
import threading

HOST = "127.0.0.1"
PORT = 11987


class CoolerControl:
    def __init__(self, token):
        self.headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        self.conn = None
        self.lock = threading.Lock()
        self.device_uid = ""
        self.device_name = ""
        self.channel = "lcd"
        self.width = 320
        self.height = 320
        self.sensors = []

    def request(self, method, path, body=None):
        payload = json.dumps(body) if body is not None else None
        with self.lock:
            for attempt in (1, 2):
                if self.conn is None:
                    self.conn = http.client.HTTPConnection(HOST, PORT, timeout=5)
                try:
                    self.conn.request(method, path, payload, self.headers)
                    response = self.conn.getresponse()
                    status, data = response.status, response.read()
                    break
                except (http.client.HTTPException, OSError):
                    self.conn.close()
                    self.conn = None
                    if attempt == 2:
                        raise
        if status != 200:
            raise RuntimeError(f"{method} {path} returned {status}: {data[:120]!r}")
        return json.loads(data) if data else {}

    def discover(self):
        """Find the LCD channel and every temperature sensor CoolerControl knows."""
        devices = self.request("GET", "/devices").get("devices", [])
        sensors = []
        lcd = None
        for device in devices:
            info = device.get("info") or {}
            for name, meta in (info.get("temps") or {}).items():
                sensors.append({
                    "id": f"{device['uid']}|{name}",
                    "device_name": device.get("name", "?"),
                    "label": (meta or {}).get("label") or name,
                })
            for channel, meta in (info.get("channels") or {}).items():
                modes = [m.get("name") for m in (meta.get("lcd_modes") or [])]
                if "image" in modes and lcd is None:
                    lcd = (device, channel, meta.get("lcd_info") or {})

        if lcd is None:
            raise RuntimeError("no LCD device with an image mode was found")

        device, channel, lcd_info = lcd
        self.device_uid = device["uid"]
        self.device_name = device.get("name", "LCD")
        self.channel = channel
        self.width = int(lcd_info.get("screen_width") or 320)
        self.height = int(lcd_info.get("screen_height") or 320)
        self.sensors = sensors
        return self

    def default_sensor_id(self):
        for sensor in self.sensors:
            if "package" in sensor["label"].lower():
                return sensor["id"]
        return self.sensors[0]["id"] if self.sensors else ""

    def resolve_sensor(self, hint=""):
        """Turn a stored id, or a manifest hint like "gpu", into a sensor id.

        Integrations should not have to know device UIDs, so a manifest default can
        name what it wants and the first sensor whose device or label matches wins.
        """
        hint = (hint or "").strip()
        if not hint:
            return self.default_sensor_id()
        if any(sensor["id"] == hint for sensor in self.sensors):
            return hint
        needle = hint.lower()
        for sensor in self.sensors:
            if needle in f"{sensor['device_name']} {sensor['label']}".lower():
                return sensor["id"]
        return self.default_sensor_id()

    def sensor_label(self, sensor_id):
        for sensor in self.sensors:
            if sensor["id"] == sensor_id:
                return sensor["label"]
        return sensor_id

    def temperatures(self):
        """Current value for every sensor, keyed the same way as discover()."""
        payload = self.request("POST", "/status", {"all": False})
        values = {}
        for device in payload.get("devices", []):
            history = device.get("status_history") or []
            if not history:
                continue
            for temp in history[-1].get("temps") or []:
                values[f"{device['uid']}|{temp['name']}"] = float(temp["temp"])
        return values

    def push_image(self, path, brightness, orientation):
        body = {
            "mode": "image",
            "image_file_processed": str(path),
            "brightness": int(brightness),
            "orientation": int(orientation),
            "colors": [],
        }
        self.request("PUT",
                     f"/devices/{self.device_uid}/settings/lcd/{self.channel}?log=false",
                     body)
