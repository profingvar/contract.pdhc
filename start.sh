#!/usr/bin/env bash
# ============================================================
# contract.pdhc — start.sh
# All-Docker service: DB + API + Web via docker-compose.
# IMPORTANT: No kill -9 on ports — docker-compose down handles it.
# ============================================================

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
APP_DIR="$SCRIPT_DIR/app"
BACKUP_DIR="$SCRIPT_DIR/db_backups"

export OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES

# Detect docker-compose
if [ -x /opt/homebrew/bin/docker-compose ]; then
    DC="/opt/homebrew/bin/docker-compose"
elif command -v docker-compose >/dev/null 2>&1; then
    DC="docker-compose"
elif docker compose version >/dev/null 2>&1; then
    DC="docker compose"
else
    echo "[Contract] ERROR: No docker-compose found."
    exit 1
fi

echo "[Contract] === contract.pdhc starting ==="

# ── 1. Docker check ──────────────────────────────────────────
if ! docker info >/dev/null 2>&1; then
    echo "[Contract] ERROR: Docker is not running."
    echo "  Run: bash /usr/local/www/restart_all.sh"
    exit 1
fi
echo "[Contract] Docker OK"

# ── 2. Stop existing (docker-compose down only — no kill -9) ─
echo "[Contract] Stopping previous containers..."
cd "$APP_DIR"
$DC down 2>/dev/null || true

# ── 3. Backup DB if volume exists ────────────────────────────
mkdir -p "$BACKUP_DIR"
$DC up -d db 2>/dev/null || true
sleep 3
DB_CONTAINER=$($DC ps -q db 2>/dev/null || true)
if [ -n "$DB_CONTAINER" ] && docker ps -q --filter "id=$DB_CONTAINER" 2>/dev/null | grep -q .; then
    if docker exec "$DB_CONTAINER" pg_isready -U contracts >/dev/null 2>&1; then
        echo "[Contract] Backing up database..."
        TIMESTAMP=$(date -u +%Y-%m-%dT%H-%M-%SZ)
        docker exec "$DB_CONTAINER" pg_dumpall -U contracts 2>/dev/null | gzip > "$BACKUP_DIR/contracts_${TIMESTAMP}.sql.gz" || true
        ls -t "$BACKUP_DIR"/contracts_*.sql.gz 2>/dev/null | tail -n +11 | xargs rm -f 2>/dev/null || true
    fi
fi

# ── 4. Start all services ────────────────────────────────────
echo "[Contract] Starting services..."
cd "$APP_DIR"
$DC up -d --build

if [ $? -ne 0 ]; then
    echo "[Contract] ERROR: docker-compose up failed."
    exit 1
fi

# ── 5. Health check ──────────────────────────────────────────
echo "[Contract] Waiting for services..."
for i in $(seq 1 30); do
    if curl -sf http://localhost:9021/health >/dev/null 2>&1; then
        echo "[Contract]   API is healthy!"
        break
    fi
    [ "$i" -eq 30 ] && echo "[Contract]   WARNING: Health check not passing yet"
    sleep 2
done

echo ""
echo "[Contract] === contract.pdhc is running ==="
echo "  Web UI:   http://localhost:9022"
echo "  API:      http://localhost:9021"
echo "  Database: localhost:9020"
echo "  Health:   http://localhost:9021/health"
echo "  Logs:     cd $APP_DIR && $DC logs -f"
echo "  Stop:     cd $APP_DIR && $DC down"
