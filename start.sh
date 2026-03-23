#!/bin/bash
# start.sh — Single entry-point for contract.pdhc (Rule 16)
# Ports: 9020 (PostgreSQL), 9021 (Flask API), 9022 (nginx SPA), 9023 (reserved)
set -e

# macOS gunicorn fork safety fix
export OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES

# Use plain docker — relies on the active docker context (set once via: docker context use colima)
# For standalone docker-compose, export DOCKER_HOST so it finds the socket
COLIMA_SOCK="$HOME/.colima/default/docker.sock"
if [ -S "$COLIMA_SOCK" ]; then
    export DOCKER_HOST="unix://$COLIMA_SOCK"
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
APP_DIR="$SCRIPT_DIR/app"
BACKUP_DIR="$SCRIPT_DIR/db_backups"

# Detect docker-compose binary
if [ -x /opt/homebrew/bin/docker-compose ]; then
    DC="/opt/homebrew/bin/docker-compose"
elif command -v docker-compose >/dev/null 2>&1; then
    DC="docker-compose"
else
    DC="docker compose"
fi

echo "=== contract.pdhc startup ==="
echo "  Compose: $DC"

# 1. Ensure Docker is running (check only, do not restart anything)
echo "Checking Docker..."
if ! docker ps >/dev/null 2>&1; then
    echo "ERROR: Docker is not reachable."
    echo "  Make sure Colima is running: colima status"
    echo "  And context is set: docker context use colima"
    exit 1
fi
echo "  Docker is running."

# 2. Stop our own services if running (only contract.pdhc, not other projects)
echo "Stopping previous contract.pdhc containers..."
cd "$APP_DIR"
$DC down 2>/dev/null || true

# 3. Back up database if volume exists
mkdir -p "$BACKUP_DIR"
# Try to start just db to dump, then stop it
$DC up -d db 2>/dev/null || true
sleep 3
DB_CONTAINER=$($DC ps -q db 2>/dev/null || true)
if [ -n "$DB_CONTAINER" ] && docker ps -q --filter "id=$DB_CONTAINER" 2>/dev/null | grep -q .; then
    if docker exec "$DB_CONTAINER" pg_isready -U contracts >/dev/null 2>&1; then
        echo "Backing up database..."
        TIMESTAMP=$(date -u +%Y-%m-%dT%H-%M-%SZ)
        docker exec "$DB_CONTAINER" pg_dumpall -U contracts 2>/dev/null | gzip > "$BACKUP_DIR/contracts_${TIMESTAMP}.sql.gz" || echo "  Warning: backup failed (non-fatal)"
        ls -t "$BACKUP_DIR"/contracts_*.sql.gz 2>/dev/null | tail -n +11 | xargs rm -f 2>/dev/null || true
        echo "  Backup saved."
    fi
fi

# 4. Activate virtual environment
echo "Activating virtual environment..."
if [ ! -d "$APP_DIR/.venv" ]; then
    echo "  Creating venv..."
    python3 -m venv "$APP_DIR/.venv"
fi
source "$APP_DIR/.venv/bin/activate"

# 5. Start Docker services
echo "Starting Docker services..."
if [ "${START_BUILD:-0}" = "1" ]; then
    $DC up -d --build
else
    $DC up -d
fi

# 6. Wait for health checks
echo "Waiting for services to be healthy..."
for i in $(seq 1 30); do
    if curl -sf http://localhost:9021/health >/dev/null 2>&1; then
        echo "  API is healthy!"
        break
    fi
    if [ $i -eq 30 ]; then
        echo "  Warning: Health check not passing yet. Check logs."
    fi
    sleep 2
done

echo ""
echo "=== contract.pdhc is running ==="
echo "  Web UI:   http://localhost:9022"
echo "  API:      http://localhost:9021"
echo "  Database: localhost:9020"
echo "  Health:   http://localhost:9021/health"
echo "  Metadata: http://localhost:9021/fhir/metadata"
echo ""
echo "  Logs:     cd app && $DC logs -f"
echo "  Stop:     cd app && $DC down"
