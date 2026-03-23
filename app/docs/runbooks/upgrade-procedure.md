# Upgrade Procedure Runbook

Steps for upgrading the Contract Manager: application code, dependencies, and database migrations.

---

## 1) Pre-upgrade checklist

### 1.1 Backup the database

```bash
docker exec -t $(docker compose -f app/docker-compose.yml ps -q db) \
  pg_dump -U contracts -d contracts > backup_pre_upgrade_$(date +%Y%m%d_%H%M%S).sql
```

### 1.2 Note current state

```bash
# Record current container image digests
docker compose -f app/docker-compose.yml images

# Record current test results
source app/.venv/bin/activate
python app/scripts/test_endpoints.py
```

### 1.3 Verify git state

Ensure all local changes are committed or stashed before pulling updates.

---

## 2) Upgrade steps

### 2.1 Pull latest code

```bash
git pull origin main
```

### 2.2 Rebuild containers

```bash
cd app && docker compose down
docker compose build --no-cache
docker compose up -d
```

The `--no-cache` flag ensures fresh dependency installation.

### 2.3 Update local tooling venv

```bash
cd app
source .venv/bin/activate
pip install -r backend/requirements.txt
```

---

## 3) Post-upgrade verification

### 3.1 Health check

```bash
curl http://localhost:9021/health
```

### 3.2 Run endpoint tests

```bash
source app/.venv/bin/activate
python app/scripts/test_endpoints.py
```

All checks must pass.

### 3.3 Run pytest suite

```bash
cd app/backend
python -m pytest tests/ -v
```

### 3.4 Spot-check the UI

1. Open `http://localhost:9022` in a browser
2. Verify the contract list loads
3. Log in as admin and verify dashboard renders
4. Check the Docs and API pages load

---

## 4) Rollback procedure

If the upgrade causes failures:

### 4.1 Revert code

```bash
git checkout <previous-commit-hash>
```

### 4.2 Rebuild and restore

```bash
cd app && docker compose down
docker compose build --no-cache
docker compose up -d
```

### 4.3 Restore database (if schema changed)

```bash
cd app && docker compose down -v
docker compose up -d
# Wait for db to be healthy, then restore:
cat backup_pre_upgrade_YYYYMMDD_HHMMSS.sql | \
  docker exec -i $(docker compose -f app/docker-compose.yml ps -q db) \
  psql -U contracts -d contracts
```

### 4.4 Verify rollback

Run the full endpoint test suite to confirm the previous version is operational.

---

## 5) Dependency updates

### 5.1 Python packages

Review and update `app/backend/requirements.txt`. Pin versions for production stability.

```bash
cd app/backend
pip install --upgrade -r requirements.txt
pip freeze > requirements.txt
```

Rebuild the API container after updating.

### 5.2 Docker base images

Update `postgres:16` and `nginx:alpine` version tags in `docker-compose.yml` and `Dockerfile` when security patches are released.
