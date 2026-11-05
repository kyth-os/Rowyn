#!/bin/bash
# Capture the real desktop with the Rowyn Hub window in front.
#
# Why this route: this KDE Wayland host has no spectacle/grim/import/scrot, and
# KWin's own org.kde.KWin.ScreenShot2 D-Bus API refuses an unprivileged process
# (NoAuthorized). The xdg-desktop-portal Screenshot API is authorized for the
# session user, so the capture goes through it; KWin's scripting API raises the
# Hub first, because the portal captures the screen as it is.
#
# Usage: build/tools/desktop-shot/shot.sh OUT.png
# Keep OUT inside .tools/: a capture contains the user's whole desktop.
set -uo pipefail
out="${1:?usage: shot.sh OUT.png}"
mkdir -p "$(dirname "$out")"
here="$(cd "$(dirname "$0")" && pwd)"
name="rowyn-raise-$$"
id=$(gdbus call --session --dest org.kde.KWin --object-path /Scripting \
      --method org.kde.kwin.Scripting.loadScript "$here/raise.js" "$name" 2>/dev/null \
     | tr -dc '0-9' || true)
if [ -n "${id:-}" ]; then
  gdbus call --session --dest org.kde.KWin --object-path "/Scripting/Script$id" \
      --method org.kde.kwin.Script.run >/dev/null 2>&1 || true
fi
sleep 2
python3 "$here/portal-shot.py" "$out" >/dev/null
