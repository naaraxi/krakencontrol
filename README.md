# krakencontrol

Show a web page on the NZXT Kraken Z LCD.

A headless Chrome renders the page, each frame is sent to CoolerControl, and
CoolerControl draws it on the device. The daemon does not decide what is on screen.
The web integration you load decides that, and it also supplies its own settings. Load
a different URL and the control panel rebuilds itself around that page's settings.

This does the same job as the web integrations in NZXT CAM on Windows, but for
CoolerControl on Linux.

Four integrations are included: one reading, two readings, a number wheel, and
CAM-style arcs.

## Requirements

- Python 3.9 or newer, and Pillow (`python3-pillow`)
- Google Chrome, at `/usr/bin/google-chrome` or wherever `KC_CHROME` points
- CoolerControl, answering on `127.0.0.1:11987`, with a device whose LCD channel
  supports `image` mode
- A CoolerControl access token

That is all. Pillow is the only dependency outside the standard library. Install it
from your distribution as `python3-pillow`, or with `pip install -r requirements.txt`.
The DevTools client needs nothing extra: it speaks WebSocket on the standard library,
so there is no `websockets` or `websocket-client` package to install.

## Installing

    git clone <repo-url> krakencontrol-src
    cd krakencontrol-src
    ./install.sh

`install.sh` copies the code to `~/krakencontrol`, asks for a CoolerControl token,
then installs and starts the user service. You can run it again later to update the
code. It does not touch `config.json`, `token` or `images/`.

    KC_HOME=/opt/krakencontrol ./install.sh   # install somewhere else
    KC_TOKEN=cc_... ./install.sh              # give the token, no prompt
    KC_SERVICE=0 ./install.sh                 # copy the files only

`./uninstall.sh` removes the service. `./uninstall.sh --purge` also deletes the
installed folder.

Get the token from CoolerControl, under **Access Protection -> Create Token**. It
needs write access. CoolerControl also needs it for reads: without a token, every
endpoint except `/handshake` answers `Invalid Credentials`. krakencontrol reads the
token from `KRAKEN_CC_TOKEN` if that is set, otherwise from the `token` file next to
the code, which `install.sh` writes with mode 600.

Run `loginctl enable-linger $USER` if you want the service to start at boot instead of
at your first login.

## Running it

    systemctl --user start krakencontrol      # normal use
    ./krakencontrol.py --once                 # render one frame and exit
    ./krakencontrol.py --url http://host/page # load a different integration

The control panel is at <http://127.0.0.1:8770>. It listens on loopback only. To open
it from another machine, forward the port over ssh instead of exposing it:

    ssh -L 8770:127.0.0.1:8770 <host>

The panel has the URL bar and history at the top, a live preview of the current frame,
and the loaded integration's settings below as cards. The core settings, which are
update interval, brightness and screen orientation, sit alongside them.

Set **orientation** to match how the cooler is mounted. It can be 0, 90, 180 or 270. A
wrong value here is the usual reason the first frame comes out sideways.

**If you have CoolerControl's coolerdash plugin, keep it stopped.** Two programs
writing to the same LCD channel will overwrite each other.

## Background pictures

No pictures are included in this repo. Every integration can show a background, and
there are three ways to give it one:

- **Image (pick)** lists the files in `images/` inside the install folder. Copy `.png`,
  `.jpg`, `.jpeg`, `.gif`, `.webp` or `.bmp` files in there, then press Reload in the
  panel. They show up as a gallery of thumbnails.
- **Image (bundled)** does the same for `bundled/`. Use that one for a set of pictures
  you want to keep with an install, rather than ones you add day to day. Both folders
  are created empty if they are missing.
- **Image (url)** takes any URL the machine can reach, so you do not have to copy the
  file in at all.

The panel serves both folders, so a page just uses the path it is given. Add
`?thumb=1` to a picture URL and the panel returns a small copy instead of the full
file. That is what keeps a gallery of fifty pictures fast.

Pictures look best if they are already square, close to the panel size (320x320 on a
Kraken Z), dark, and fairly plain in the middle, because a reading sits on top.
`Fit` switches between cover and contain. `Dim` darkens the picture so the reading
stays readable.

## The integrations

- **single-sensor** - one reading, centred.
- **dual-sensor** - two readings, stacked or side by side, with a divider and position
  controls for each one.
- **sensor-wheel** - the reading on a number wheel, with the neighbouring numbers
  shrinking away from it.
- **dual-bars** - CAM-style arcs, one per reading, each with its own range.

All four let you pick a font, a background and a colour mode. **Simple** mode uses one
colour for the text. **Gradient** mode takes a low colour at one value and a high
colour at another, and colours each reading by where it falls between them, for
example green at 30 and red at 60. Outside that range it stays at the nearer colour.
`dual-bars` does not ask for values, because a bar already has a range of its own, so
the gradient follows the fill instead.

The font list starts with the families installed on the machine, because those need no
network, and then lists everything Google Fonts serves, grouped by category. The list
is cached in `fonts.json` and refreshed once a week, so the panel still works offline.
The page loads the chosen family when it needs it, and the daemon waits for the font
to be ready before taking the screenshot, so a frame is never drawn in the fallback
font.

## Writing an integration

An integration is a web page. Any URL works. A page with no manifest is just displayed
as it is.

To get settings in the panel, serve `integration.json` next to the page:

    {
      "description": "One sensor, centred, on a plain background.",
      "settings": [
        { "key": "sensor", "label": "Sensor", "type": "sensor", "default": "package" },
        { "key": "caption", "label": "Caption", "type": "text", "default": "", "max": 16 },
        { "key": "text_color", "label": "Text", "type": "color", "default": "#ffffff" },
        { "key": "font_size", "label": "Size", "type": "range",
          "default": 0, "min": 0, "max": 260, "step": 2, "zero_label": "auto" }
      ]
    }

The page defines `window.kcUpdate(payload)`. krakencontrol calls it once per frame and
screenshots the result:

    {
      "settings": { "caption": "CPU", "text_color": "#ffffff" },
      "sensors":  { "sensor": { "id": "<device-uid>|temp1",
                                "label": "CPU Temp Package Id 0",
                                "value": 43.0, "unit": "C" } },
      "device":   { "width": 320, "height": 320 },
      "interval": 2.5,
      "ts": 1789067174.68
    }

### Field types

`text`, `select` (needs `options`), `color`, `range`, `number`, `checkbox`, `sensor`,
`image` and `font`.

A **`sensor`** field is filled in by krakencontrol with every temperature CoolerControl
can see. The value arrives in `payload.sensors` under the field's own key. The
`default` can be a simple hint such as `"package"` or `"gpu temp"`, and the first
sensor whose device name or label contains that text is used. This way a manifest never
has to include device UIDs, and the same manifest works on another machine. A manifest
can declare as many sensor fields as it needs.

**The page never gets the CoolerControl token.** The daemon reads the sensors and
passes the values in, so an integration needs no access of its own.

An **`image`** field is filled from a folder set by `source`: `"user"` lists `images/`,
`"bundled"` lists `bundled/`. A **`font`** field offers the font list. A **`range`** can
have a `suffix` shown after the number, a `zero_label` shown instead of 0 (useful for
"auto"), and a `hint` shown as a tooltip. If a range has a negative `min` it is treated
as a direction, so the panel shows a sign: `+30px`, `-20px`.

### Laying out the panel

Fields stay in manifest order. Give each field a `section` to group them. A new section
value starts a new card, and the cards flow into as many columns as the window allows,
three on a wide screen and one on a phone, so a long list of settings does not turn
into a long scroll.

A field can also have `"show_if": {"key": "other_field", "value": "..."}`, or `values`
for several matches. The panel then shows that field only while the other field holds
one of those values. That is how the colour mode shows one picker in simple mode and
two pickers with a value next to each in gradient mode, and how `Fit` and `Dim` stay
visible across all three picture modes.

### While you are working on one

**Editing an integration's files does not change the running page.** The browser keeps
what it already loaded. Press `Reload` in the panel, or `POST /api/reload`.

Reload re-fetches `integration.json` as well as the page, and this matters. Settings
are checked against the manifest krakencontrol currently holds, and unknown values are
dropped. So a field you have just added does nothing until you reload.

Settings are stored per URL and replaced against the manifest rather than merged. A
field you remove from a manifest also disappears from the stored settings the next time
it loads.

An integration is identified by its URL everywhere: in the history, in the panel
heading, and as the key its settings are stored under. A page cannot name itself.
Because settings are kept per URL, you can switch between two integrations and back
without losing what you set on either.

## HTTP API

The panel serves a small JSON API on the same port.

| Method | Path | What it does |
|---|---|---|
| GET | `/api/state` | everything the panel draws itself from |
| GET | `/api/preview.png` | the current frame |
| POST | `/api/load` | `{"url": "..."}` - load an integration |
| POST | `/api/reload` | re-fetch the page and its manifest |
| POST | `/api/settings` | update the loaded integration's settings |
| POST | `/api/core` | update interval, brightness, orientation, port |
| POST | `/api/history/clear` | clear the history, keeping what is loaded |
| POST | `/api/forget` | `{"url": "..."}` - remove one history entry |

The history keeps the last 20 integrations, newest first. `Clear` keeps the one that is
loaded, since that is not history, it is what is on screen. There is no Forget button
in the panel on purpose: choosing an entry loads it, so the selected entry is always
the one you must not remove. Restarting the daemon reloads the last integration without
changing the order of the history.

## How it works

    krakencontrol.py            entry point
    kc/cdp.py                   headless Chrome over the DevTools protocol
    kc/coolercontrol.py         CoolerControl client: sensors, LCD push
    kc/integration.py           manifest loading and value checking
    kc/app.py                   the render loop
    kc/panel.py                 control panel HTTP server
    kc/settings.py              config file handling
    kc/fonts.py                 the font list
    web/index.html              the control panel
    integrations/               the four included integrations
    install.sh uninstall.sh     set it up, or remove it
    systemd/                    the user unit, filled in at install time

These are written at runtime, not shipped:

    config.json                 core settings, per-URL settings, history
    token                       CoolerControl token, mode 600
    fonts.json                  cached font list
    frames/frame.png            the current frame
    images/ bundled/            your background pictures
    .chrome/                    the headless browser profile

Each tick it reads the sensors, calls `kcUpdate()` on the page, waits for fonts, takes
a screenshot, writes it to `frames/frame.png`, and tells CoolerControl to show it.

The push is a `PUT` containing a **file path**, not an upload. coolercontrold opens the
file itself. That means krakencontrol has to run on the same machine as CoolerControl,
and the image has to already be the size of the panel. The frame is written under a
temporary name and then renamed, so the daemon never reads a half-written file.

If a frame fails, the last one stays on screen and the daemon backs off and retries. If
the browser gets stuck, it is restarted after a few failures in a row.

## Not a CoolerControl plugin

It cannot usefully become one. CoolerControl serves its interface with
`default-src 'self'; connect-src 'self'; frame-ancestors 'none'`, so a plugin tab
cannot embed this panel, call its API, or load its pictures. The only way in is
CoolerControl's own plugin server contract, and that gains nothing, because plugins get
no special device access. Keep the panel in a browser.
