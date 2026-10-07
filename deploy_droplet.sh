#!/bin/bash
set -euo pipefail

echo "=========================================================="
echo " Setting up The Zone 91-3 Radio Contest Listener on Droplet"
echo "=========================================================="

# 1. Update system packages
export DEBIAN_FRONTEND=noninteractive
apt-get update && apt-get upgrade -y
apt-get install -y ffmpeg python3-venv python3-pip git curl ufw

# 2. Configure firewall (allow SSH and optional dashboard port)
ufw allow OpenSSH || true
ufw allow 4173/tcp || true
ufw --force enable || true

# 3. Create app directory
APP_DIR="/opt/zone-radio-listener"
mkdir -p "$APP_DIR"

# 4. Copy repository files into app directory if running from cloned folder
if [ -f "./main.py" ]; then
    cp -r ./* "$APP_DIR/"
fi

cd "$APP_DIR"

# 5. Create Python virtual environment
python3 -m venv .venv
./.venv/bin/pip install --upgrade pip
./.venv/bin/pip install -r requirements.txt

# 6. Create environment file with Discord webhook
cat << 'EOF' > "$APP_DIR/.env"
PORT=4173
HOST=0.0.0.0
AUDIO_SOURCE=stream
DISCORD_ENABLED=true
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/REDACTED
PUSHOVER_ENABLED=false
WHISPER_MODEL_SIZE=distil-large-v3
WHISPER_DEVICE=cpu
EOF

# 7. Create systemd daemon service
cat << 'EOF' > /etc/systemd/system/zone-listener.service
[Unit]
Description=The Zone 91-3 Radio Contest Listener & Radar
After=network.target sound.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/zone-radio-listener
EnvironmentFile=/opt/zone-radio-listener/.env
ExecStart=/opt/zone-radio-listener/.venv/bin/python3 /opt/zone-radio-listener/main.py
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

# 8. Reload systemd and start service
systemctl daemon-reload
systemctl enable zone-listener.service
systemctl restart zone-listener.service

echo ""
echo "=========================================================="
echo " Service installed and started successfully!"
echo " Status check: systemctl status zone-listener.service"
echo " Live logs:    journalctl -u zone-listener.service -f"
echo " Dashboard:    http://YOUR_DROPLET_IP:4173"
echo "=========================================================="
