#!/bin/bash
# 5MP Conservation Monitoring - New Instance Setup
# Run this on a fresh VM to install everything

set -e

echo "=== 5MP Installation Script ==="
echo "Started at $(date)"

# Configuration
GITHUB_REPO=""  # Code should already be on VM
DB_BACKUP_URL="https://five-megapixel-conservation.exe.xyz:8000/static/downloads/5mp_backup_20260221.sqlite3?pwd=test2026"
FIRMS_API_KEY="${FIRMS_API_KEY:-}"  # Set via environment
INSTALL_DIR="/home/exedev/5mp"
LOG_FILE="/tmp/5mp_install.log"

# Log everything
exec > >(tee -a "$LOG_FILE") 2>&1

echo "=== Installing System Dependencies ==="
sudo apt-get update
sudo apt-get install -y \
    golang-go \
    python3 python3-pip python3-venv \
    sqlite3 \
    git curl wget \
    build-essential \
    gdal-bin libgdal-dev \
    jq

echo "=== Cloning Repository ==="
if [ -d "$INSTALL_DIR" ]; then
    cd "$INSTALL_DIR"
    git pull || echo "Git pull failed, continuing..."
else
    git clone "$GITHUB_REPO" "$INSTALL_DIR"
    cd "$INSTALL_DIR"
fi

echo "=== Setting Up Python Virtual Environment ==="
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

echo "=== Downloading Database Backup ==="
if [ ! -f "db.sqlite3" ] || [ "$FORCE_DB_DOWNLOAD" = "1" ]; then
    wget -q "$DB_BACKUP_URL" -O db.sqlite3.download
    mv db.sqlite3.download db.sqlite3
    echo "Database downloaded successfully"
else
    echo "Database exists, skipping download (set FORCE_DB_DOWNLOAD=1 to override)"
fi

echo "=== Verifying Database ==="
sqlite3 db.sqlite3 "PRAGMA integrity_check" || {
    echo "ERROR: Database integrity check failed!"
    exit 1
}
echo "Database integrity OK"

# Database stats
echo "Database stats:"
sqlite3 db.sqlite3 "SELECT 'fire_detections: ' || COUNT(*) FROM fire_detections;"
sqlite3 db.sqlite3 "SELECT 'feature_geometries: ' || COUNT(*) FROM feature_geometries;"
sqlite3 db.sqlite3 "SELECT 'parks: ' || COUNT(DISTINCT park_id) FROM park_fire_analysis;"

echo "=== Building Go Server ==="
make build

echo "=== Setting Up Systemd Service ==="
sudo cp srv.service /etc/systemd/system/5mp.service
sudo systemctl daemon-reload
sudo systemctl enable 5mp

echo "=== Starting Server ==="
sudo systemctl start 5mp
sleep 3
sudo systemctl status 5mp --no-pager

echo "=== Verifying Server ==="
curl -s "http://localhost:8000/" | head -5 || echo "Server may need a moment to start"

echo ""
echo "=== Installation Complete ==="
echo "Finished at $(date)"
echo "Log file: $LOG_FILE"
echo ""
echo "Access the app at: https://YOUR_DOMAIN:8000/?pwd=test2026"
echo ""
echo "To run fire pipeline:"
echo "  source .venv/bin/activate"
echo "  python3 scripts/rebuild_park_fire_analysis_v2.py"
echo "  python3 scripts/analyze_fire_trajectories_v4.py"
echo "  python3 scripts/precompute_narratives_v4.py"
