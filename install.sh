#!/usr/bin/env bash
# Install krakencontrol for the user running this script. Safe to re-run: it upgrades
# the code and leaves config.json, token and images/ alone.
set -euo pipefail

SOURCE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET="${KC_HOME:-$HOME/krakencontrol}"
UNIT_DIR="${KC_UNIT_DIR:-$HOME/.config/systemd/user}"
SERVICE="${KC_SERVICE:-1}"      # set to 0 to install the files without touching systemd

say() { printf '  %s\n' "$*"; }

echo "krakencontrol -> $TARGET"

# ---- what it needs ----
missing=()
command -v python3 >/dev/null || missing+=("python3")
python3 -c "import PIL" 2>/dev/null || missing+=("python3-pillow")
CHROME="${KC_CHROME:-/usr/bin/google-chrome}"
[ -x "$CHROME" ] || missing+=("google-chrome (looked at $CHROME; set KC_CHROME to override)")
if ! curl -fsS -m 3 -o /dev/null "http://127.0.0.1:11987/handshake"; then
  say "warning: CoolerControl is not answering on 127.0.0.1:11987"
  say "         install it first, it is what owns the LCD"
fi
if [ ${#missing[@]} -gt 0 ]; then
  echo "missing:"
  for item in "${missing[@]}"; do say "$item"; done
  exit 1
fi

# ---- code ----
mkdir -p "$TARGET"
for part in krakencontrol.py kc web integrations bundled README.md; do
  [ -e "$SOURCE/$part" ] || continue
  rm -rf "${TARGET:?}/$part"
  cp -r "$SOURCE/$part" "$TARGET/$part"
done
chmod +x "$TARGET/krakencontrol.py"
mkdir -p "$TARGET/images" "$TARGET/frames"
say "code installed"

# ---- token ----
if [ ! -s "$TARGET/token" ]; then
  if [ -n "${KC_TOKEN:-}" ]; then
    printf '%s\n' "$KC_TOKEN" > "$TARGET/token"
  elif [ -t 0 ]; then
    echo
    echo "CoolerControl needs a token with write access:"
    echo "  its UI -> Access Protection -> Create Token"
    token=""
    read -rsp "  paste it here (or press enter to do this later): " token || true
    echo
    [ -n "$token" ] && printf '%s\n' "$token" > "$TARGET/token"
  else
    say "no terminal to ask on: put the token in $TARGET/token, or set KC_TOKEN"
  fi
  if [ -s "$TARGET/token" ]; then
    chmod 600 "$TARGET/token"
    say "token saved"
  fi
else
  say "token already present, left alone"
fi

# ---- service ----
if [ "$SERVICE" = "1" ] && [ -s "$TARGET/token" ]; then
  mkdir -p "$UNIT_DIR"
  sed "s|@HOME@|$TARGET|g" "$SOURCE/systemd/krakencontrol.service" > "$UNIT_DIR/krakencontrol.service"
  systemctl --user daemon-reload
  systemctl --user enable --now krakencontrol.service
  say "service started: systemctl --user status krakencontrol"
  loginctl show-user "$USER" --property=Linger | grep -q yes \
    || say "note: 'loginctl enable-linger $USER' makes it start without a login"
elif [ "$SERVICE" = "1" ]; then
  say "service not started: there is no token yet"
else
  say "service step skipped"
fi

echo
echo "done. control panel: http://127.0.0.1:8770"
