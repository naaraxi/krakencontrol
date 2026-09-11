#!/usr/bin/env bash
# Remove the service and, if asked, the files with it.
set -euo pipefail
TARGET="${KC_HOME:-$HOME/krakencontrol}"

systemctl --user disable --now krakencontrol.service 2>/dev/null || true
rm -f "$HOME/.config/systemd/user/krakencontrol.service"
systemctl --user daemon-reload
echo "service removed"

if [ "${1:-}" = "--purge" ]; then
  rm -rf "$TARGET"
  echo "removed $TARGET, settings and pictures included"
else
  echo "left $TARGET in place; pass --purge to delete it too"
fi
