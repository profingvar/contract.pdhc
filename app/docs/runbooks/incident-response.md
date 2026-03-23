# Incident Response Runbook

Triage checklist and recovery procedures for the PDHC Contract Manager.

---

## 1) Triage checklist

When an incident is reported, work through these checks in order:

### 1.1 Service health

```bash
curl -s http://localhost:9021/health
```

- **200 + `{"status":"ok"}`** — API is up, problem is elsewhere
- **Connection refused** — API container is down
- **502/504** — reverse proxy issue (production only)

### 1.2 Container status

```bash
docker compose -f app/docker-compose.yml ps
```

Check that all three containers (`db`, `api`, `web`) show `Up` or `running`. If any are `Exited` or `Restarting`, check logs.

### 1.3 Container logs

```bash
docker compose -f app/docker-compose.yml logs --tail=50 api
docker compose -f app/docker-compose.yml logs --tail=50 db
```

Look for: connection errors, OOM kills, unhandled exceptions, authentication failures.

### 1.4 Database connectivity

```bash
docker exec -it $(docker compose -f app/docker-compose.yml ps -q db) \
  psql -U contracts -d contracts -c "SELECT count(*) FROM contract_records;"
```

If this fails, the database may be corrupted or the container unhealthy.

### 1.5 Rate limit status

If users report 429 errors:

- This is expected behaviour for high-traffic IPs
- Check if the rate limit is misconfigured: `READ_RATE_LIMIT` env var
- For legitimate high-volume consumers, increase the limit or whitelist

---

## 2) Common incidents

### 2.1 API container crash-looping

**Symptoms**: `api` container status is `Restarting`, logs show repeated errors.

**Actions**:
1. Check logs: `docker compose -f app/docker-compose.yml logs api`
2. Common causes: missing env vars, database unreachable, port conflict
3. Fix the root cause, then: `docker compose -f app/docker-compose.yml up -d api`

### 2.2 Database corruption

**Symptoms**: SQL errors in API logs, SELECT queries failing.

**Actions**:
1. Stop the stack: `cd app && docker compose down`
2. If you have a backup: restore from backup (see operator manual 3.2)
3. If no backup: `docker compose down -v` to start fresh (data loss)
4. Restart: `docker compose up --build`

### 2.3 Disk full

**Symptoms**: database write errors, container failures.

**Actions**:
1. Check disk: `df -h`
2. Prune Docker: `docker system prune -f`
3. Check PostgreSQL volume size: `docker system df -v`
4. If the volume is large, consider archiving old contracts

### 2.4 Unauthorized access detected

**Symptoms**: unexpected admin operations in logs, unknown users.

**Actions**:
1. Rotate JWT_SECRET_KEY immediately (see `credential-rotation.md` section 1)
2. List all users and deactivate unknown accounts
3. Reset all passwords
4. Review API logs for the scope of unauthorized actions

---

## 3) Recovery verification

After resolving any incident, run the full verification:

```bash
# 1. Health check
curl http://localhost:9021/health

# 2. Endpoint tests
source app/.venv/bin/activate
python app/scripts/test_endpoints.py

# 3. Review results
cat results/$(ls -t results/ | head -1)/endpoint-test.json | python3 -m json.tool
```

All checks should pass before declaring the incident resolved.
