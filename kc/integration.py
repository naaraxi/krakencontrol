"""Web integrations.

An integration is just a web page. If it also serves `integration.json` next to
itself, that manifest lists the settings it accepts, and krakencontrol builds the
settings form from it - which is why settings only appear once one is loaded.

The page receives values through a global `kcUpdate(payload)` function, so it
never needs the CoolerControl token or any access of its own.
"""

import json
import urllib.parse
import urllib.request

FIELD_TYPES = ("text", "select", "color", "range", "number", "sensor", "image",
               "font", "checkbox")
# Fields are passed to the panel as the manifest wrote them, so extras such as
# section, hint and show_if travel with them.


def manifest_url(page_url):
    """integration.json sits next to the page (or in it, for a directory URL)."""
    parts = urllib.parse.urlsplit(page_url)
    path = parts.path or "/"
    if path.endswith("/"):
        base = path
    else:
        base = path.rsplit("/", 1)[0] + "/"
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, base + "integration.json", "", ""))


def fetch_manifest(page_url, timeout=5):
    """Return (manifest, error). A page without a manifest is still usable."""
    url = manifest_url(page_url)
    try:
        request = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            if response.status != 200:
                return None, f"{url} returned {response.status}"
            data = json.load(response)
    except Exception as err:                       # any failure just means "no settings"
        return None, str(err)

    fields = []
    for field in data.get("settings") or []:
        if not isinstance(field, dict) or "key" not in field:
            continue
        if field.get("type", "text") not in FIELD_TYPES:
            continue
        fields.append(field)
    data["settings"] = fields
    data.setdefault("description", "")
    return data, ""


def defaults_for(manifest):
    if not manifest:
        return {}
    return {field["key"]: field.get("default", "") for field in manifest["settings"]}


def coerce(manifest, values, resolve_sensor=None):
    """Force incoming values into the shapes the manifest declares."""
    if not manifest:
        return dict(values)
    clean = {}
    for field in manifest["settings"]:
        key = field["key"]
        value = values.get(key, field.get("default", ""))
        kind = field.get("type", "text")
        try:
            if kind == "number":
                number = float(value)
                number = max(float(field.get("min", -1e9)),
                             min(float(field.get("max", 1e9)), number))
                clean[key] = int(number) if float(field.get("step", 1)).is_integer() else number
            elif kind == "range":
                number = float(value)
                number = max(float(field.get("min", 0)), min(float(field.get("max", 100)), number))
                clean[key] = int(number) if float(field.get("step", 1)).is_integer() else number
            elif kind == "checkbox":
                clean[key] = bool(value) and value not in ("false", "0", 0)
            elif kind == "select":
                allowed = [str(option["value"]) for option in field.get("options", [])]
                clean[key] = str(value) if str(value) in allowed else str(field.get("default", ""))
            elif kind == "color":
                text = str(value).strip()
                clean[key] = text if len(text) == 7 and text.startswith("#") else field.get("default", "#000000")
            elif kind in ("image", "font"):
                clean[key] = str(value)[:400]
            elif kind == "sensor":
                wanted = str(value) or str(field.get("default", ""))
                clean[key] = resolve_sensor(wanted) if resolve_sensor else wanted
            else:
                clean[key] = str(value)[:int(field.get("max", 120))]
        except (TypeError, ValueError):
            clean[key] = field.get("default", "")
    return clean


def sensor_keys(manifest):
    if not manifest:
        return []
    return [f["key"] for f in manifest["settings"] if f.get("type") == "sensor"]
