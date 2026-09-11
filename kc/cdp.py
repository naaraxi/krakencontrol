"""Minimal Chrome DevTools Protocol client.

Only what krakencontrol needs: open a page, run a bit of JavaScript, take a
screenshot. Speaks WebSocket directly so the project stays on the standard
library - no websockets/websocket-client dependency to install and keep current.
"""

import base64
import json
import os
import secrets
import socket
import struct
import subprocess
import time
import urllib.request
from pathlib import Path

CHROME = os.environ.get("KC_CHROME") or "/usr/bin/google-chrome"


class CDPError(RuntimeError):
    pass


class WebSocket:
    """Client side of RFC 6455, enough for a local DevTools socket."""

    def __init__(self, host, port, path, timeout=15):
        self.sock = socket.create_connection((host, port), timeout=timeout)
        self.sock.settimeout(timeout)
        self.buffer = b""
        key = base64.b64encode(secrets.token_bytes(16)).decode()
        handshake = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n"
        )
        self.sock.sendall(handshake.encode())
        while b"\r\n\r\n" not in self.buffer:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise CDPError("devtools closed during handshake")
            self.buffer += chunk
        head, _, rest = self.buffer.partition(b"\r\n\r\n")
        if b"101" not in head.split(b"\r\n")[0]:
            raise CDPError(f"devtools refused the upgrade: {head[:80]!r}")
        self.buffer = rest

    def _read(self, count):
        while len(self.buffer) < count:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise CDPError("devtools socket closed")
            self.buffer += chunk
        data, self.buffer = self.buffer[:count], self.buffer[count:]
        return data

    def send(self, text):
        payload = text.encode()
        header = bytearray([0x81])  # FIN + text frame
        mask = secrets.token_bytes(4)
        size = len(payload)
        if size < 126:
            header.append(0x80 | size)
        elif size < 1 << 16:
            header.append(0x80 | 126)
            header += struct.pack("!H", size)
        else:
            header.append(0x80 | 127)
            header += struct.pack("!Q", size)
        masked = bytes(byte ^ mask[i % 4] for i, byte in enumerate(payload))
        self.sock.sendall(bytes(header) + mask + masked)

    def recv(self):
        """Return the next complete text message, handling fragments and pings."""
        message = b""
        while True:
            first, second = self._read(2)
            final = first & 0x80
            opcode = first & 0x0F
            size = second & 0x7F
            if size == 126:
                size = struct.unpack("!H", self._read(2))[0]
            elif size == 127:
                size = struct.unpack("!Q", self._read(8))[0]
            payload = self._read(size) if size else b""

            if opcode == 0x9:                       # ping
                self.sock.sendall(b"\x8a\x80" + secrets.token_bytes(4))
                continue
            if opcode == 0x8:                       # close
                raise CDPError("devtools closed the socket")
            if opcode in (0x0, 0x1):
                message += payload
                if final:
                    return message.decode()
                continue
            # binary or anything else is not part of the protocol we use

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass


class Browser:
    """A headless Chrome kept alive across frames."""

    def __init__(self, profile_dir, width, height):
        self.profile_dir = Path(profile_dir)
        self.width = width
        self.height = height
        self.process = None
        self.ws = None
        self.next_id = 0
        self.url = ""

    # ---------- lifecycle ----------

    def start(self):
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        port_file = self.profile_dir / "DevToolsActivePort"
        if port_file.exists():
            port_file.unlink()

        self.process = subprocess.Popen([
            CHROME,
            "--headless=new",
            "--remote-debugging-port=0",     # a free port, reported in DevToolsActivePort
            f"--user-data-dir={self.profile_dir}",
            f"--window-size={self.width},{self.height}",
            "--force-device-scale-factor=1",
            "--hide-scrollbars",
            "--disable-gpu",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-background-timer-throttling",
            "--disable-renderer-backgrounding",
            "--disable-backgrounding-occluded-windows",
            "about:blank",
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        port = self._wait_for_port(port_file)
        target = self._first_page(port)
        path = target["webSocketDebuggerUrl"].split("/devtools/", 1)[1]
        self.ws = WebSocket("127.0.0.1", port, "/devtools/" + path)
        self.call("Page.enable")
        self.call("Runtime.enable")
        # --window-size does not decide the layout viewport: Chrome reported 500x233
        # for a 320x320 window, so the page laid out wrong and the screenshot clip
        # caught a corner of it. Pin the viewport to the panel instead.
        self.call("Emulation.setDeviceMetricsOverride", {
            "width": self.width,
            "height": self.height,
            "deviceScaleFactor": 1,
            "mobile": False,
        })
        self.url = ""

    def _wait_for_port(self, port_file, timeout=20):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise CDPError("chrome exited during startup")
            if port_file.exists():
                text = port_file.read_text().splitlines()
                if text and text[0].strip().isdigit():
                    return int(text[0].strip())
            time.sleep(0.1)
        raise CDPError("chrome never reported a devtools port")

    @staticmethod
    def _first_page(port, timeout=10):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=5) as res:
                    for target in json.load(res):
                        if target.get("type") == "page" and target.get("webSocketDebuggerUrl"):
                            return target
            except OSError:
                pass
            time.sleep(0.2)
        raise CDPError("no devtools page target")

    def stop(self):
        if self.ws:
            self.ws.close()
            self.ws = None
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
        self.process = None

    def alive(self):
        return self.process is not None and self.process.poll() is None and self.ws is not None

    def ensure(self):
        if not self.alive():
            self.stop()
            self.start()

    # ---------- protocol ----------

    def call(self, method, params=None, timeout=20):
        self.next_id += 1
        message_id = self.next_id
        self.ws.send(json.dumps({"id": message_id, "method": method, "params": params or {}}))
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            message = json.loads(self.ws.recv())
            if message.get("id") != message_id:
                continue                     # an event, or a reply we are done with
            if "error" in message:
                raise CDPError(f"{method}: {message['error']}")
            return message.get("result", {})
        raise CDPError(f"{method}: no reply in {timeout}s")

    def wait_for_event(self, name, timeout=20):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            message = json.loads(self.ws.recv())
            if message.get("method") == name:
                return message.get("params", {})
        raise CDPError(f"timed out waiting for {name}")

    # ---------- page ----------

    def navigate(self, url):
        self.call("Page.navigate", {"url": url})
        try:
            self.wait_for_event("Page.loadEventFired", timeout=25)
        except CDPError:
            pass                              # a page that never fires load still renders
        self.url = url

    def evaluate(self, expression):
        result = self.call("Runtime.evaluate", {
            "expression": expression,
            "awaitPromise": True,
            "returnByValue": True,
        })
        details = result.get("exceptionDetails")
        if details:
            raise CDPError(details.get("text", "javascript error"))
        return result.get("result", {}).get("value")

    def screenshot(self):
        result = self.call("Page.captureScreenshot", {
            "format": "png",
            "captureBeyondViewport": False,
            "clip": {"x": 0, "y": 0, "width": self.width, "height": self.height, "scale": 1},
        })
        return base64.b64decode(result["data"])
