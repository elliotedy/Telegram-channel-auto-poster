#!/data/data/com.termux/files/usr/bin/bash
set -euo pipefail

cd "$(dirname "$0")"

echo "==> Installing Termux packages…"
pkg update -y
pkg install -y python git

echo "==> Installing Python dependencies…"
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

mkdir -p data logs

if [ ! -f .env ]; then
  cp .env.example .env
  echo
  echo "==> Created .env"
  echo "    Edit it now:  nano .env"
  echo "    Set TELEGRAM_BOT_TOKEN and ADMIN_IDS."
fi

chmod +x run-termux.sh install-boot.sh run.sh 2>/dev/null || true

echo
echo "==> Setup complete."
echo "    1. Edit your settings:  nano .env"
echo "    2. Test the bot:        python main.py"
echo "    3. Run 24/7:            bash run-termux.sh"
echo "    4. Start on boot:       bash install-boot.sh"
