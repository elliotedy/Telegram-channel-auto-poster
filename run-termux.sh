#!/data/data/com.termux/files/usr/bin/bash
# Keeps the bot running and restarts it after a crash or /update.
set -u

cd "$(dirname "$0")"
mkdir -p data logs

if command -v termux-wake-lock >/dev/null 2>&1; then
  termux-wake-lock 2>/dev/null || true
fi

PIDFILE="$PWD/data/run-termux.pid"
if [ -f "$PIDFILE" ]; then
  OLD_PID="$(cat "$PIDFILE" 2>/dev/null || true)"
  if [ -n "$OLD_PID" ] && kill -0 "$OLD_PID" 2>/dev/null; then
    echo "Auto poster is already running (PID $OLD_PID)."
    exit 1
  fi
fi
echo "$$" > "$PIDFILE"

cleanup() {
  rm -f "$PIDFILE"
  if command -v termux-wake-unlock >/dev/null 2>&1; then
    termux-wake-unlock 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

echo "[$(date '+%Y-%m-%d %H:%M:%S')] run-termux.sh started (PID $$)" >> logs/termux.log

while true; do
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] starting bot…" >> logs/termux.log
  python -u main.py >> logs/termux.log 2>&1
  CODE=$?
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] bot exited with code $CODE; restarting in 10 seconds" >> logs/termux.log
  sleep 10
done
