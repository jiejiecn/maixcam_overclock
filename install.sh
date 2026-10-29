#!/bin/sh
# Install / update Overclock on a MaixCAM Pro.
#
# Usage on the device:
#   sh install.sh
set -e

SRC=$(cd "$(dirname "$0")" && pwd)
DEST=/maixapp/apps/overclock
LAUNCHER=/maixapp/apps/launcher

[ "$(id -u)" = "0" ] || { echo "请用 root 运行 / please run as root"; exit 1; }
[ -f "$SRC/main.py" ] || { echo "missing $SRC/main.py"; exit 1; }
[ -f "$SRC/assets/modules/mpll_freq.ko" ] || { echo "missing CPU kernel module"; exit 1; }
[ -f "$SRC/assets/modules/npu_freq.ko" ] || { echo "missing NPU kernel module"; exit 1; }

echo "Installing to $DEST ..."
mkdir -p "$DEST/assets/modules"
cp "$SRC/main.py" "$DEST/main.py"
cp "$SRC/app.yaml" "$DEST/app.yaml"
cp "$SRC/README.md" "$DEST/README.md" 2>/dev/null || true
cp "$SRC/assets/icon.png" "$DEST/assets/icon.png"
cp "$SRC/assets/modules/mpll_freq.ko" "$DEST/assets/modules/mpll_freq.ko"
cp "$SRC/assets/modules/npu_freq.ko" "$DEST/assets/modules/npu_freq.ko"
chmod 755 "$DEST/main.py"

# Register the app for both the stock app manager and the custom launcher.
if [ -f /maixapp/apps/gen_app_info.py ]; then
    python3 /maixapp/apps/gen_app_info.py /maixapp/apps
fi

# The custom MaixPy launcher prefers icons/<app_id>.png.
if [ -d "$LAUNCHER/icons" ]; then
    cp "$SRC/assets/icon.png" "$LAUNCHER/icons/overclock.png"
    echo "Installed launcher icon: $LAUNCHER/icons/overclock.png"
fi

echo "Installed."
PID=$(pidof launcher 2>/dev/null || true)
if [ -n "$PID" ]; then
    echo "Restarting launcher GUI (pid=$PID) ..."
    kill $PID 2>/dev/null || true
fi
