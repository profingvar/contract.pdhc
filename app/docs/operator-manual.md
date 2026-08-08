# Contract Service — Operator Manual

This manual covers day-to-day operation of the PDHC Contract Manager: starting, stopping, backing up, configuring authentication, running the consent reconciler, testing, and recovering from common failures.

---

## 1) Prerequisites

### 1.1 Software requirements

The service is **all-Docker** — the database, API, and web SPA all run as containers under one Docker Compose project. On the host you need:

- **Docker** (Colima on the macmini) + a `docker-compose` binary. `start.sh` prefers `/opt/homebrew/bin/docker-compose`, then `docker-compose`, then the `docker compose` v2 plugin.
- **Ports 9020–9022** free on `localhost`. All three containers bind to `127.0.0.1` only (never `0.0.0.0`) — external traffic reaches the service exclusively through the reverse proxy.
- Python is **not** required to run the service; it is only needed to run the backend test suite (§6).

### 1.2 Environment variables

The `.env` file lives at `./app/.env` and is read by Docker Compose, which passes the values into the `api` and `db` containers. A committed `.env.example` documents the shape; `.env` itself is gitignored and filled in by the operator on the server.

The list below reflects the actual variables read by `config.py`, `main.py`, and `docker-compose.yml`.

**Required in `.env`:**

- **`POSTGRES_PASSWORD`** — Postgres superuser password. Compose uses `${POSTGRES_PASSWORD:?…}` for both the `db` container and the `api` container's `DATABASE_URL`, so **the stack refuses to start if it is unset**.

**Required by the app, but supplied automatically by Compose (override only if you know why):**

- **`DATABASE_URL`** — SQLAlchemy URL. `config.py` treats it as mandatory (`getenv_required`); Compose constructs it as `postgresql+psycopg://<user>:<password>@db:5432/<db>`.
- **`JWT_SECRET_KEY`** — signing key for the local JWTs the callback mints. Mandatory in `config.py`; Compose defaults it to `dev-secret-change-me`. **Change it before any real use.**

**Database naming (optional, defaulted):**

- **`POSTGRES_DB`** — default `contracts`.
- **`POSTGRES_USER`** — default `contracts`.

**Authentication / SSO (see §4):**

- **`AUTH_DISABLED`** — default `false`. When `true`, SSO is bypassed and the local `/auth/login` + bootstrap admin path becomes active. `config.py` **refuses to boot with `AUTH_DISABLED=true` unless `FLASK_ENV=development`** — this is a dev-only escape hatch, never for the server.
- **`FLASK_ENV`** — default `production`. Must be `development` for `AUTH_DISABLED=true` to be accepted.
- **`SSO_BASE_URL`** — default `https://sso.pdhc.se`.
- **`SSO_CLIENT_ID`** / **`SSO_CLIENT_SECRET`** — this service's SSO client credentials (the `_CONTRACT` pair on sso.pdhc). Empty by default; token validation returns 401 without them.
- **`SSO_CALLBACK_URL`** — default `https://contract.pdhc.se/api/v1/auth/callback`.
- **`PUBLIC_WEB_URL`** — default `https://contract.pdhc.se`. Where the callback redirects the browser after minting a token (`/?sso_token=…`).

**Service-to-service integration:**

- **`INTERNAL_SERVICE_KEY`** — shared secret for `X-Service-Key` on the internal scope endpoint, for PAT auto-provisioning to request.pdhc, and as the fallback key for IPS consent calls. Empty by default; the internal scope endpoint rejects **all** callers when it is unset.
- **`REQUEST_BASE_URL`** — request.pdhc base, default `http://localhost:9060`. Target for PAT auto-provisioning.
- **`IPS_BASE_URL`** — ips.pdhc base. **Empty by default, which makes consent emission a no-op** (local dev / standalone install).
- **`IPS_API_KEY`** — key for IPS consent calls; optional. When absent, the emitter falls back to `INTERNAL_SERVICE_KEY`.
- **`PLAN_BASE_URL`** — plan.pdhc base, default `https://plan.pdhc.se`. Used to verify scope concepts exist.

**Validation strictness (both default `true`):**

- **`STRICT_SIGNER_VALIDATION`** — when `true`, a `signer[]` pointing at a non-existent Patient/User/Practitioner rejects the write with 400. Set `false` in local dev to write without IPS/SSO running.
- **`STRICT_SCOPE_CONCEPTS`** — when `true`, every concept GUID in `term[]` must exist in plan.pdhc; a plan.pdhc outage refuses the write with 503. Set `false` in local dev.

**Miscellaneous:**

- **`READ_RATE_LIMIT`** — public read rate limit, default `100 per hour`.
- **`CORS_ORIGINS`** — allowed CORS origins, default `*` (restrict to `https://contract.pdhc.se` in production).
- **`DB_WAIT_TIMEOUT_S`** — seconds the API waits for Postgres readiness on boot, default `30`.
- **`LIMITER_STORAGE_URI`** — flask-limiter backend, default `memory://`.
- **`APP_VERSION`** — reported in `/health`, default `dev`.

> Note: `BOOTSTRAP_ADMIN_USERNAME` / `BOOTSTRAP_ADMIN_PASSWORD` exist in `docker-compose.yml`, but the bootstrap admin is **only seeded when `AUTH_DISABLED=true`** (i.e. dev only). See §4.3.

---

## 2) Start and stop

### 2.1 Starting the stack

From the repository root:

```bash
./start.sh
```

`start.sh` is deliberately conservative — it does **no `kill -9` on ports** (the header says so explicitly; `docker compose down` handles teardown). In order, it:

1. Locates a `docker-compose` binary (prefers `/opt/homebrew/bin/docker-compose`).
2. Checks that Docker is running (`docker info`); on failure it points you at `restart_all.sh` and exits.
3. Runs `docker compose down` to stop the previous containers.
4. **Backs up the database**: brings up only the `db` service, waits, and if `pg_isready` succeeds runs `pg_dumpall -U contracts | gzip` into `./db_backups/contracts_<UTC-timestamp>.sql.gz`, keeping the 10 most recent dumps.
5. Runs `docker compose up -d --build` — **detached**, rebuilding images. This is what picks up code changes (`COPY . .` bakes source into the image; a restart without `--build` would run stale code).
6. Polls `http://localhost:9021/health` up to 30 times (2 s apart) and prints the service URLs.

The three containers exposed:

- **`db`** — PostgreSQL 16 on `127.0.0.1:9020`
- **`api`** — Flask API on `127.0.0.1:9021`
- **`web`** — nginx SPA + docs on `127.0.0.1:9022`

### 2.2 Stopping and cleanup

Because `start.sh` runs the stack **detached**, `Ctrl+C` does nothing — there is no foreground process to interrupt. Stop the service with Compose:

- **Graceful stop / full teardown (keeps data)**: `cd app && docker compose down`
- **Follow logs**: `cd app && docker compose logs -f`
- **Full teardown + delete the database volume**: `cd app && docker compose down -v`

> `docker compose down -v` deletes the `app_contracts_pgdata` volume and all contract data. Take a backup first (§3), and never run it on the server without explicit authorization (CLAUDE.md §14).

---

## 3) Backup and restore

`start.sh` already snapshots the DB on every start into `./db_backups/`. For an on-demand dump/restore:

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

## 4) Authentication

### 4.1 Production: SSO (the normal path)

Production always runs with `AUTH_DISABLED=false`. Users authenticate through sso.pdhc; the Contract Manager never holds their password.

The flow:

1. Browser hits **`GET /api/v1/auth/login`**. The service stores a random CSRF `state` in the session and redirects to the SSO login page (`SSO_BASE_URL/login?next=<callback>&state=<state>`).
2. After the user authenticates at SSO, SSO redirects back to **`GET /api/v1/auth/callback?token=…&state=…`**.
3. The service validates the `state` (CSRF), then validates the `token` by calling SSO's `/api/auth/me/service` with the `X-SSO-Client-Id` / `X-SSO-Client-Secret` headers. This returns the **access blob**.
4. If the blob has **`must_change_password`**, the user is bounced to `SSO_BASE_URL/change-password` and no local token is minted. After clearing it at SSO, a second login lands here with the flag off.
5. Otherwise the service maps the blob to a local role (§4.2), mints an 8-hour local JWT, and redirects to `PUBLIC_WEB_URL/?sso_token=<jwt>`. The SPA reads the token from the query string.

`GET /api/v1/auth/me` returns the current user's claims; `GET /api/v1/auth/logout` clears the session.

### 4.2 Role derivation

Roles come from the SSO access blob, not from a local user table:

- `is_su_admin == true` → **admin**
- `user_type == "professional"` → **admin**
- any other authenticated user → **reader**

`admin` can create/update/delete contracts and manage users; `reader` is read-only (functionally the same as anonymous, but identified). Write endpoints require the `admin` role.

### 4.3 Dev-only: local login + bootstrap admin

The local `POST /auth/login` (username/password against the `users` table, bcrypt) and the `BOOTSTRAP_ADMIN_*` seeding are **only active when `AUTH_DISABLED=true`**, which `config.py` refuses to boot outside `FLASK_ENV=development`. With SSO enabled, `POST /auth/login` returns `400` with a pointer to `/api/v1/auth/login`.

So on a normal (production-shaped) install:

- There is **no** bootstrap admin user — admins are whoever SSO says is an SU admin or a professional.
- The `/admin/users` endpoints exist but are only reachable with an admin JWT, which in practice means SSO-minted.

---

## 5) Consent reconciler (`flask reconcile-consents`)

When a contract in a lifecycle status is written, the service emits or revokes matching `PatientConsent` rows on ips.pdhc (see the architecture doc §3 and §6). That emission is **best-effort**: if IPS is briefly unreachable, the write still succeeds and the consent silently drops.

The reconciler is the recovery path. It **lives on this service** as a Flask CLI command:

```bash
docker exec $(docker compose -f app/docker-compose.yml ps -q api) \
  flask reconcile-consents
```

It walks every `ContractRecord` in a grant or revoke status, re-calls the (idempotent) emitter/revoker, and prints a one-line summary:

```
reconcile-consents checked=<n> grants_re_emitted=<n> revokes_re_called=<n> \
  grant_attempts=<n> revoke_attempts=<n> errors=<n>
```

On the macmini this runs **hourly via cron (#246)**. A clean DB reports `grants_re_emitted=0 revokes_re_called=0`; a second run within the same window does ~zero work.

---

## 6) Running tests

The backend suite runs in-process against an in-memory SQLite database — no Docker required — provided a local Python venv is set up under `app/backend/`:

```bash
cd app/backend
python -m pytest tests/ -v
```

If a test-results convention is used, outputs follow `./results/<ISO-8601-UTC>_results/`.

---

## 7) Common failures

### 7.1 Stack won't start: `POSTGRES_PASSWORD must be set`

Compose uses `${POSTGRES_PASSWORD:?…}`. Set `POSTGRES_PASSWORD` in `app/.env` (§1.2).

### 7.2 API refuses to boot: `AUTH_DISABLED=true requires FLASK_ENV=development`

A stale `.env` shipped `AUTH_DISABLED=true` to a production-shaped install. Either remove `AUTH_DISABLED` (defaults to `false` → SSO) or, for genuine local dev, also set `FLASK_ENV=development`.

### 7.3 Port conflicts

If a container fails to bind:

```bash
lsof -i :9020-9022
```

`start.sh` does **not** free ports for you (by design). Stop whatever owns the port, or `cd app && docker compose down` to clear a previous instance of this stack.

### 7.4 Database not ready

If the API exits with "Database not ready after waiting":

- Increase `DB_WAIT_TIMEOUT_S` (e.g. `60`).
- Check Docker resource allocation.
- Inspect DB logs: `docker compose -f app/docker-compose.yml logs db`.

### 7.5 Credential drift (`password authentication failed`)

If the `db` container reports `(healthy)` but the `api` container fails auth, the Postgres password hash predates the current `.env` (the `postgres` password is only applied on first volume init; the `trust` rule masks it for local clients). Reset it via the trust side door:

```bash
docker exec $(docker compose -f app/docker-compose.yml ps -q db) \
  psql -h 127.0.0.1 -U contracts -d contracts \
  -c "ALTER USER contracts WITH PASSWORD '<POSTGRES_PASSWORD from .env>';"
docker restart $(docker compose -f app/docker-compose.yml ps -q api)
```

See CLAUDE.md §9 for the full write-up.

### 7.6 Contract writes rejected

- **`422 scope_concept_missing`** — a concept GUID in `term[]` doesn't exist in plan.pdhc. Fix the reference or create the concept.
- **`503 scope_validation_unavailable`** — plan.pdhc is unreachable and `STRICT_SCOPE_CONCEPTS=true`. Restore plan.pdhc, or set the flag `false` in local dev.
- **`400 signer_unresolved`** — a `signer[]` reference doesn't resolve and `STRICT_SIGNER_VALIDATION=true`. Fix the reference or relax the flag in local dev.
