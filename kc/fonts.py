"""The font list offered in the panel.

Google's own metadata endpoint lists every family it serves. It is cached on disk so
the panel still has a list when the machine is offline, and the fonts installed locally
are always offered first - those need no network at all.
"""

import json
import time
import urllib.request
from pathlib import Path

METADATA = "https://fonts.google.com/metadata/fonts"
REFRESH_AFTER = 7 * 24 * 3600
BROWSER = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"}

# Present on the machine, so they render even with no network.
INSTALLED = ["Roboto", "Noto Sans", "Liberation Sans", "DejaVu Sans"]


def fetch():
    request = urllib.request.Request(METADATA, headers=BROWSER)
    with urllib.request.urlopen(request, timeout=20) as response:
        raw = response.read().decode("utf-8", "ignore")
    # The reply starts with an anti-hijacking prefix before the JSON.
    data = json.loads(raw[raw.index("{"):])
    return [{"name": item["family"], "category": item.get("category") or "Other"}
            for item in data.get("familyMetadataList", [])]


def load(cache_path):
    """Cached families, refreshed in the background of a normal startup."""
    cache = Path(cache_path)
    fresh = cache.exists() and (time.time() - cache.stat().st_mtime) < REFRESH_AFTER
    if fresh:
        try:
            return json.loads(cache.read_text())
        except (OSError, ValueError):
            pass
    try:
        families = fetch()
        cache.write_text(json.dumps(families))
        return families
    except Exception as err:
        print(f"google fonts unavailable ({err}); offering the installed ones only",
              flush=True)
        if cache.exists():
            try:
                return json.loads(cache.read_text())
            except (OSError, ValueError):
                pass
        return [{"name": name, "category": "Installed"} for name in INSTALLED]


def catalogue(cache_path):
    """Installed families first, then everything Google serves."""
    families = load(cache_path)
    known = {item["name"] for item in families}
    head = [{"name": name, "category": "Installed"} for name in INSTALLED]
    return head + [item for item in families if item["name"] not in set(INSTALLED)] \
        if known else head
