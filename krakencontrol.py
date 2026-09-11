#!/usr/bin/env python3
"""krakencontrol - show a web integration on the NZXT Kraken Z LCD.

The daemon renders a web page headlessly and hands each frame to CoolerControl,
which owns the device. Settings live in the integration, not here: load one by URL
and its own settings appear in the control panel.

Keep CoolerControl's coolerdash plugin stopped - two writers on the same LCD
channel just overwrite each other.
"""

import argparse
import os
import signal
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from kc import panel
from kc.app import Controller
from kc.cdp import Browser
from kc.coolercontrol import CoolerControl
from kc.settings import Settings

BASE = Path(__file__).resolve().parent
CONFIG_PATH = BASE / "config.json"
TOKEN_PATH = BASE / "token"
FRAME_PATH = BASE / "frames" / "frame.png"
PROFILE_DIR = BASE / ".chrome"
BUNDLED = "integrations/single-sensor/"


def read_token():
    token = os.environ.get("KRAKEN_CC_TOKEN")
    if token:
        return token.strip()
    try:
        return TOKEN_PATH.read_text().strip()
    except OSError as err:
        sys.exit(f"no token: set KRAKEN_CC_TOKEN or write {TOKEN_PATH} ({err})")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true", help="render one frame and exit")
    parser.add_argument("--url", help="load this integration instead of the stored one")
    args = parser.parse_args()

    settings = Settings(CONFIG_PATH)
    cooler = CoolerControl(read_token()).discover()
    print(f"LCD: {cooler.device_name} channel {cooler.channel} "
          f"{cooler.width}x{cooler.height}", flush=True)

    browser = Browser(PROFILE_DIR, cooler.width, cooler.height)
    controller = Controller(settings, cooler, browser, FRAME_PATH)

    def shutdown(signum, frame):
        controller.stop()
    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    port = settings.get_core()["http_port"]
    url = args.url or settings.get_core()["integration_url"]
    if not url:
        url = f"http://127.0.0.1:{port}/{BUNDLED}"    # first run: the bundled integration

    if not args.once:
        panel.serve(controller, BASE, FRAME_PATH, port)

    # Restoring what was already loaded is not a use, so it must not reorder history.
    controller.load_integration(url, bump=bool(args.url))
    print(f"integration: {url}", flush=True)

    try:
        if args.once:
            controller.tick()
            print(f"frame written to {FRAME_PATH}", flush=True)
            return 0
        controller.run()
    finally:
        browser.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
