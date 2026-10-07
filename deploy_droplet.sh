#!/bin/bash
set -euo pipefail

echo "=========================================================="
echo " Setting up The Zone 91-3 Radio Contest Listener on Droplet"
echo "=========================================================="

# 1. Check RAM and configure 2GB swapfile if needed (critical for 512MB/1GB droplets)
TOTAL_RAM_MB=$(free -m | awk '/^Mem:/{print $2}')
if [ "$TOTAL_RAM_MB" -lt 2048 ] && [ ! -f /swapfile ]; then
    echo "--> Detected ${TOTAL_RAM_MB}MB RAM. Creating 2GB swapfile for smooth PyTorch/Whisper operation..."
    fallocate -l 2G /swapfile || dd if=/dev/zero of=/swapfile bs=1M count=2048
    chmod 600 /swapfile
    mkswap /swapfile
    swapon /swapfile
    echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

# 2. Update system packages
export DEBIAN_FRONTEND=noninteractive
apt-get update && apt-get upgrade -y
apt-get install -y ffmpeg python3-venv python3-pip git curl ufw

# 3. Configure firewall (allow SSH and optional dashboard port)
ufw allow OpenSSH || true
ufw allow 4173/tcp || true
ufw --force enable || true

# 4. Create app directory
APP_DIR="/opt/zone-radio-listener"
mkdir -p "$APP_DIR"

# 5. Copy repository files into app directory if running from cloned folder
if [ -f "./main.py" ]; then
    cp -r ./* "$APP_DIR/"
fi

cd "$APP_DIR"

# 6. Create Python virtual environment
python3 -m venv .venv
./.venv/bin/pip install --upgrade pip
./.venv/bin/pip install -r requirements.txt

# 7. Select model size according to available hardware
# For 512MB-1GB droplets, base.en runs sub-second with high accuracy and <250MB memory footprint.
MODEL_SIZE="distil-large-v3"
if [ "$TOTAL_RAM_MB" -lt 1200 ]; then
    MODEL_SIZE="base.en"
fi

# 8. Create environment file with Discord webhook
cat << EOF > "$APP_DIR/.env"
PORT=4173
HOST=0.0.0.0
AUDIO_SOURCE=stream
DISCORD_ENABLED=true
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/REDACTED
PUSHOVER_ENABLED=false
WHISPER_MODEL_SIZE=${MODEL_SIZE}
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
