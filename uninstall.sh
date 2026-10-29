#!/bin/sh
# Remove Overclock from a MaixCAM Pro.
#
# Usage on the device:
#   sh uninstall.sh
#
# Note: frequency changes are runtime-only. If the CPU/NPU are currently
# overclocked, open the app and press Reset first, or simply reboot.
set -e

[ "$(id -u)" = "0" ] || { echo "请用 root 运行 / please run as root"; exit 1; }
DEST=/maixapp/apps/overclock
LAUNCHER=/maixapp/apps/launcher

echo "Removing $DEST ..."
rm -rf "$DEST"
if [ -f /maixapp/apps/gen_app_info.py ]; then
    python3 /maixapp/apps/gen_app_info.py /maixapp/apps
fi
rm -f "$LAUNCHER/icons/overclock.png"

PID=$(pidof launcher 2>/dev/null || true)
if [ -n "$PID" ]; then
    kill $PID 2>/dev/null || true
fi
echo "Removed. Reboot the device to restore factory clocks if needed."
