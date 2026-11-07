#!/data/data/com.termux/files/usr/bin/bash
# Installs a Termux:Boot script so the bot starts automatically after reboot.
set -euo pipefail

APP_DIR="$(cd "$(dirname "$0")" && pwd)"
BOOT_DIR="$HOME/.termux/boot"

mkdir -p "$BOOT_DIR" "$APP_DIR/logs"

cat > "$BOOT_DIR/autoposter.sh" <<EOF
#!/data/data/com.termux/files/usr/bin/bash
termux-wake-lock 2>/dev/null || true
cd "$APP_DIR" || exit 1
exec bash ./run-termux.sh >> logs/boot.log 2>&1
EOF

chmod +x "$BOOT_DIR/autoposter.sh"

echo "==> Installed $BOOT_DIR/autoposter.sh"
echo
echo "To enable autostart:"
echo "  1. Install Termux:Boot from F-Droid (same source as Termux)."
echo "  2. Open Termux:Boot once."
echo "  3. Reboot the phone."
echo
echo "Also disable battery optimization for Termux in Android settings."
