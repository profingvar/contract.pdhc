#!/bin/bash
# start.sh — Single entry-point for contract.pdhc (Rule 16)
# Ports: 9020 (PostgreSQL), 9021 (Flask API), 9022 (nginx SPA), 9023 (reserved)
set -e

# macOS gunicorn fork safety fix
export OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES

# Detect Colima socket
COLIMA_SOCK="$HOME/.colima/default/docker.sock"
if [ -S "$COLIMA_SOCK" ]; then
    # docker CLI: use -H flag (works in scripts, unlike context)
    DOCKER="docker -H unix://$COLIMA_SOCK"
    # docker-compose: uses DOCKER_HOST env var
    export DOCKER_HOST="unix://$COLIMA_SOCK"
elif [ -S /var/run/docker.sock ]; then
    DOCKER="docker"
    export DOCKER_HOST="unix:///var/run/docker.sock"
else
    DOCKER="docker"
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
APP_DIR="$SCRIPT_DIR/app"
BACKUP_DIR="$SCRIPT_DIR/db_backups"

# Detect docker-compose binary (server uses hyphenated standalone)
if [ -x /opt/homebrew/bin/docker-compose ]; then
    DC="/opt/homebrew/bin/docker-compose"
elif command -v docker-compose >/dev/null 2>&1; then
    DC="docker-compose"
else
    DC="docker compose"
fi

echo "=== contract.pdhc startup ==="
echo "  Docker: $DOCKER"
echo "  DOCKER_HOST: ${DOCKER_HOST:-not set}"
echo "  Compose: $DC"

# 1. Kill any processes on project ports
echo "Checking ports 9020-9023..."
for port in 9020 9021 9022 9023; do
    pid=$(lsof -ti :$port 2>/dev/null || true)
    if [ -n "$pid" ]; then
        echo "  Killing process on port $port (PID: $pid)"
        kill -9 $pid 2>/dev/null || true
    fi
done
echo "  Ports cleared."

# 2. Ensure Docker is running
echo "Checking Docker..."
if ! $DOCKER ps >/dev/null 2>&1; then
    echo "  Docker not reachable. Attempting to start Colima..."
    if command -v colima >/dev/null 2>&1; then
        colima start 2>/dev/null || true
        sleep 5
        # Re-detect socket after start
        if [ -S "$COLIMA_SOCK" ]; then
            DOCKER="docker -H unix://$COLIMA_SOCK"
            export DOCKER_HOST="unix://$COLIMA_SOCK"
        fi
    fi
    if ! $DOCKER ps >/dev/null 2>&1; then
        echo "ERROR: Docker is not running and could not be started."
        echo "  DOCKER cmd: $DOCKER"
        echo "  Socket exists: $(ls -la $COLIMA_SOCK 2>&1)"
        echo "  Try: colima stop && colima start"
        exit 1
    fi
fi
echo "  Docker is running."

# 3. Back up database if container is running (before any restart)
mkdir -p "$BACKUP_DIR"
DB_CONTAINER=$(cd "$APP_DIR" && $DC ps -q db 2>/dev/null || true)
if [ -n "$DB_CONTAINER" ] && $DOCKER ps -q --filter "id=$DB_CONTAINER" 2>/dev/null | grep -q .; then
    echo "Backing up database..."
    TIMESTAMP=$(date -u +%Y-%m-%dT%H-%M-%SZ)
    $DOCKER exec "$DB_CONTAINER" pg_dumpall -U contracts 2>/dev/null | gzip > "$BACKUP_DIR/contracts_${TIMESTAMP}.sql.gz" || echo "  Warning: backup failed (non-fatal)"
    ls -t "$BACKUP_DIR"/contracts_*.sql.gz 2>/dev/null | tail -n +11 | xargs rm -f 2>/dev/null || true
    echo "  Backup saved to db_backups/contracts_${TIMESTAMP}.sql.gz"
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
cd "$APP_DIR"
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
echo "Press Ctrl+C to stop..."

# 7. Tail logs; Ctrl+C triggers graceful shutdown
trap 'echo ""; echo "Shutting down..."; cd "$APP_DIR" && $DC down; deactivate 2>/dev/null; echo "Stopped."; exit 0' INT TERM

$DC logs -f
