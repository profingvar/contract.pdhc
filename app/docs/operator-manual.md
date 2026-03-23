# Contract Service — Operator Manual

This manual covers day-to-day operation of the PDHC Contract Manager: starting, stopping, backing up, testing, and recovering from common failures.

---

## 1) Prerequisites

### 1.1 Software requirements

The following must be installed on the host machine:

- **Docker Desktop** (or Docker Engine + Docker Compose plugin)
- **Python 3.11+** for local tooling (`pytest`, endpoint test script)
- **Ports 9020–9022** must be free on `localhost`

### 1.2 Environment variables

The `api` container reads these from the Docker Compose environment block. Override via a `.env` file in `./app/` or by exporting before running `start.sh`.

- **`JWT_SECRET_KEY`** — signing key for JWT tokens. Change from default before any real use.
- **`BOOTSTRAP_ADMIN_USERNAME`** — initial admin username (default: `admin`)
- **`BOOTSTRAP_ADMIN_PASSWORD`** — initial admin password (default: `change-me`)
- **`READ_RATE_LIMIT`** — public read rate limit (default: `100 per hour`)
- **`CORS_ORIGINS`** — allowed CORS origins (default: `*`)
- **`DB_WAIT_TIMEOUT_S`** — seconds the API waits for PostgreSQL readiness (default: `30`)

---

## 2) Start and stop

### 2.1 Starting the stack

From the repository root:

```bash
./start.sh
```

This script will:

1. Kill any processes on ports 9020–9030 and 9040–9043
2. Verify Docker is running
3. Run `docker compose up --build` inside `./app/`
4. Expose three containers:
   - **`db`** — PostgreSQL on `localhost:9020`
   - **`api`** — Flask API on `localhost:9021`
   - **`web`** — nginx SPA on `localhost:9022`

### 2.2 Stopping and cleanup

- **Graceful stop**: press `Ctrl+C` in the terminal running `start.sh`
- **Full teardown** (keeps data): `cd app && docker compose down`
- **Full teardown + delete database volume**: `cd app && docker compose down -v`

Use `-v` only when you need a clean database (e.g., after changing `POSTGRES_HOST_AUTH_METHOD` or the DB schema fundamentally).

---

## 3) Backup and restore

### 3.1 Database backup

With the stack running:

```bash
docker exec -t $(docker compose -f app/docker-compose.yml ps -q db) \
  pg_dump -U contracts -d contracts > backup_$(date +%Y%m%d_%H%M%S).sql
```

### 3.2 Restore from backup

```bash
cat backup_YYYYMMDD_HHMMSS.sql | docker exec -i \
  $(docker compose -f app/docker-compose.yml ps -q db) \
  psql -U contracts -d contracts
```

For a clean restore, run `docker compose down -v` first to drop the existing volume, then start the stack and pipe in the backup.

---

## 4) Common failures

### 4.1 Port conflicts

If `start.sh` fails with "address already in use":

```bash
lsof -i :9020-9022
```

Kill the conflicting process, or let `start.sh` handle it (it attempts to free ports 9020–9030 automatically).

### 4.2 Database not ready

If the API container exits with "Database not ready after waiting":

- Increase `DB_WAIT_TIMEOUT_S` (e.g., `60`)
- Check Docker resource allocation (CPU/memory)
- Inspect DB logs: `docker compose -f app/docker-compose.yml logs db`

### 4.3 Auth bootstrap not working

If the admin user is not created on startup:

- Verify `BOOTSTRAP_ADMIN_USERNAME` and `BOOTSTRAP_ADMIN_PASSWORD` are set and non-empty
- The bootstrap only runs once — if the username already exists, it is skipped
- To re-bootstrap: delete the user from the database or run `docker compose down -v` for a fresh start

### 4.4 pg_hba authentication error

If the DB container fails with "no pg_hba.conf entry":

- The compose file sets `POSTGRES_HOST_AUTH_METHOD: trust` for local dev
- If you changed this, recreate the volume: `cd app && docker compose down -v && docker compose up --build`

---

## 5) Running tests

### 5.1 Endpoint test script

With the stack running:

```bash
source app/.venv/bin/activate
python app/scripts/test_endpoints.py
```

The script tests all 11 API endpoints (health, auth, FHIR Contract CRUD, user management) and prints pass/fail results. Output is written to `./results/<timestamp>_results/endpoint-test.json`.

### 5.2 Pytest suite

```bash
cd app/backend
source ../. venv/bin/activate
python -m pytest tests/ -v
```

Runs unit/integration tests against an in-memory SQLite database (no Docker required).

### 5.3 Results directory

All test outputs follow the convention:

```
./results/<ISO-8601-UTC>_results/
```

For example: `./results/2026-03-23T14-30-00Z_results/endpoint-test.json`
