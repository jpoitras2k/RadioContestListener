#!/bin/bash
# Quick redeploy on the Droplet: pull the latest code, restart, and check health.
# Collected clues, alerts, guesses and clips in /opt/zone-radio-listener are kept.
set -euo pipefail

APP_DIR="/opt/zone-radio-listener"
cd "$(dirname "$0")"

echo "--> Pulling latest code..."
git pull --ff-only

echo "--> Installing..."
cp -r radio_listener static main.py requirements.txt "$APP_DIR/"
"$APP_DIR/.venv/bin/pip" install -q -r "$APP_DIR/requirements.txt"

echo "--> Restarting service..."
systemctl restart zone-listener.service

echo -n "--> Waiting for the dashboard to come up"
for _ in $(seq 1 60); do
    if curl -sf -o /dev/null localhost:4173/api/status; then break; fi
    echo -n "."; sleep 2
done
echo

STATUS=$(curl -sf localhost:4173/api/status || true)
if [ -z "$STATUS" ]; then
    echo "!! Dashboard did not respond. Recent log:"
    journalctl -u zone-listener.service -n 30 --no-pager
    exit 1
fi

sleep 20
STATUS=$(curl -sf localhost:4173/api/status)
CLUES=$(curl -sf localhost:4173/api/clues | grep -o '"clue_number"' | wc -l)
echo "$STATUS" | python3 -c "
import sys, json
s = json.load(sys.stdin)
ok = lambda b: 'OK ' if b else 'FAIL'
print(f\"[{ok(s['whisper_loaded'])}] Whisper: {s['whisper_model'] if s['whisper_loaded'] else s['whisper_error']}\")
print(f\"[{ok(s['total_audio_frames'] > 0)}] Station audio: {s['total_audio_frames']} frames received\")
print(f\"[{ok(s['discord_enabled'])}] Discord alerts\")
"
echo "[INFO] Clues loaded: $CLUES"
echo "Dashboard: http://$(curl -s -m 3 ifconfig.me 2>/dev/null || echo YOUR_DROPLET_IP):4173"
