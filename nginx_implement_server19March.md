# Generic Service — nginx Server Installation Guide (Isolated Environment)

**Date:** 2026-03-19
**Purpose:** Reusable template for deploying any Docker-contained service behind nginx on a shared server.

---

## Placeholders

Replace these throughout before use:

| Placeholder | Meaning | Example |
|-------------|---------|---------|
| `GENERIC` | Service/repo name | `sso_pdhc`, `formservice`, `analytics` |
| `GENERIC.DOMAIN` | Full domain name | `sso.pdhc.se`, `api.example.com` |
| `PORT#FE` | Frontend / API port (app server) | `9000` |
| `PORT#DB` | Database port (PostgreSQL) | `9003` |
| `DEPLOY_DIR` | Installation directory on server | `/opt/GENERIC` |

---

## 0. Philosophy: Isolation First

This service must **never** disturb other services behind the reverse proxy.
Every step below is designed to be self-contained, reversible, and non-destructive to existing nginx configuration or other running services.

---

## 1. Pre-Flight Checks (Before Touching Anything)

```bash
# 1a. Verify Docker is running
docker info >/dev/null 2>&1 || { echo "Docker not running"; exit 1; }

# 1b. Confirm required ports are FREE
for p in PORT#FE PORT#DB; do
  lsof -i :$p && echo "!! Port $p in use — resolve before continuing"
done

# 1c. Confirm nginx is running and serving other sites
sudo nginx -t            # Config syntax OK?
curl -s -o /dev/null -w "%{http_code}" http://localhost  # Expect 200 or 301

# 1d. Snapshot current nginx config (rollback insurance)
sudo cp -r /etc/nginx/sites-available /etc/nginx/sites-available.bak.$(date +%Y%m%d)
sudo cp -r /etc/nginx/sites-enabled   /etc/nginx/sites-enabled.bak.$(date +%Y%m%d)

# 1e. Check disk space (need ~500 MB for Docker images + DB)
df -h /opt
```

---

## 2. Deploy the Application

### 2a. Transfer the deployment package

On the **dev machine**:

```bash
./pack_deploy.sh
# Creates: GENERIC_deploy_<timestamp>.tar.gz
```

Transfer to server (operator action):

```bash
scp GENERIC_deploy_*.tar.gz user@server:/tmp/
```

### 2b. Run the deployment script

On the **server**:

```bash
cd /tmp
chmod +x server_deploy.sh
./server_deploy.sh GENERIC_deploy_*.tar.gz install
```

This will:
- Unpack to `DEPLOY_DIR`
- Create Python venv and install dependencies
- Start PostgreSQL container on `PORT#DB`
- Initialize database tables
- Create bootstrap superuser
- Start the application server on `PORT#FE`

### 2c. Prepare the .env file

```bash
cd DEPLOY_DIR/app

# Copy template and edit
cp .env.example .env
chmod 600 .env
```

**Critical .env changes for production:**

| Variable | Dev Value | Production Value |
|----------|-----------|------------------|
| `FLASK_ENV` | development | **production** |
| `SECRET_KEY` | dev key | **New 64-char random string** (`python3 -c "import secrets; print(secrets.token_urlsafe(48))"`) |
| `DATABASE_URL` | dev password | **Strong password** (must match `docker-compose.yml` too) |
| `BOOTSTRAP_SU_PASSWORD` | change-me... | **Strong password** (change after first login) |
| `ALLOWED_ORIGINS` | localhost:PORT#FE | **https://GENERIC.DOMAIN** |
| `ALLOWED_CALLBACK_URLS` | localhost:PORT#FE/callback | **https://GENERIC.DOMAIN/callback** |

### 2d. If you changed the DB password

Edit `docker-compose.yml` to match the new password, then:

```bash
docker compose down -v   # Remove old volume with old password
docker compose up -d db  # Recreate with new password
# Re-run init
source venv/bin/activate
python scripts/init_db.py
python scripts/create_su.py
```

### 2e. Verify the app is running

```bash
curl -s http://127.0.0.1:PORT#FE/api/health | python3 -m json.tool
# Expected: {"status": "ok", "database": "connected", "uptime_seconds": ...}
```

---

## 3. DNS Configuration (Operator Action)

Before obtaining SSL certificates, DNS must resolve:

```
GENERIC.DOMAIN  →  <server public IP>
```

Verify propagation:

```bash
dig +short GENERIC.DOMAIN
# Should return the server's public IP
```

---

## 4. nginx Configuration

### 4a. Create the site config

```bash
sudo nano /etc/nginx/sites-available/GENERIC.DOMAIN
```

Paste the following (**HTTP-only first** — SSL added after cert is obtained):

```nginx
# ─── GENERIC.DOMAIN ─── ISOLATED ───
# This block is self-contained. It does NOT affect other server blocks.

upstream GENERIC_backend {
    server 127.0.0.1:PORT#FE;
    # Fail-safe: if the backend is down, nginx returns 502 — no spillover
}

server {
    listen 80;
    server_name GENERIC.DOMAIN;

    # Temporary: serve HTTP for certbot challenge
    location /.well-known/acme-challenge/ {
        root /var/www/certbot;
        allow all;
    }

    # After cert is obtained, uncomment this and remove the location / block:
    # return 301 https://$host$request_uri;

    location / {
        proxy_pass http://GENERIC_backend;

        # Forward real client info (ProxyFix or equivalent handles these)
        proxy_set_header Host              $host;
        proxy_set_header X-Real-IP         $remote_addr;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # Timeouts — generous but bounded
        proxy_connect_timeout 10s;
        proxy_send_timeout    30s;
        proxy_read_timeout    30s;

        # Upload size limit
        client_max_body_size 16M;
    }

    # Health check for monitoring (optional)
    location = /nginx-health {
        access_log off;
        return 200 "ok\n";
        add_header Content-Type text/plain;
    }
}
```

### 4b. Enable the site

```bash
sudo ln -s /etc/nginx/sites-available/GENERIC.DOMAIN /etc/nginx/sites-enabled/

# TEST before reloading — this is the critical safety step
sudo nginx -t

# Only if syntax is OK:
sudo systemctl reload nginx
```

**IMPORTANT:** If `nginx -t` fails, fix the config. nginx keeps running with the old config — other sites are unaffected as long as you do not force a restart.

### 4c. Verify HTTP proxy works

```bash
curl -s -o /dev/null -w "%{http_code}" http://GENERIC.DOMAIN/api/health
# Expected: 200
```

---

## 5. SSL Certificate (Let's Encrypt)

### 5a. Create certbot webroot

```bash
sudo mkdir -p /var/www/certbot
```

### 5b. Obtain certificate (use --staging first!)

```bash
# Test with staging to avoid rate limits
sudo certbot certonly --staging --webroot -w /var/www/certbot -d GENERIC.DOMAIN

# If staging succeeds, get the real cert:
sudo certbot certonly --webroot -w /var/www/certbot -d GENERIC.DOMAIN
```

### 5c. Update nginx to HTTPS

Replace the site config with the full version:

```nginx
# ─── GENERIC.DOMAIN ─── ISOLATED ───

upstream GENERIC_backend {
    server 127.0.0.1:PORT#FE;
}

# HTTP → HTTPS redirect
server {
    listen 80;
    server_name GENERIC.DOMAIN;

    location /.well-known/acme-challenge/ {
        root /var/www/certbot;
        allow all;
    }

    location / {
        return 301 https://$host$request_uri;
    }
}

# HTTPS
server {
    listen 443 ssl http2;
    server_name GENERIC.DOMAIN;

    # ── SSL ──
    ssl_certificate     /etc/letsencrypt/live/GENERIC.DOMAIN/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/GENERIC.DOMAIN/privkey.pem;

    # Modern TLS only
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256:ECDHE-ECDSA-AES256-GCM-SHA384:ECDHE-RSA-AES256-GCM-SHA384;
    ssl_prefer_server_ciphers off;

    # OCSP stapling
    ssl_stapling on;
    ssl_stapling_verify on;

    # ── Proxy ──
    location / {
        proxy_pass http://GENERIC_backend;

        proxy_set_header Host              $host;
        proxy_set_header X-Real-IP         $remote_addr;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        proxy_connect_timeout 10s;
        proxy_send_timeout    30s;
        proxy_read_timeout    30s;

        client_max_body_size 16M;
    }

    # Health check
    location = /nginx-health {
        access_log off;
        return 200 "ok\n";
        add_header Content-Type text/plain;
    }
}
```

### 5d. Test and reload

```bash
sudo nginx -t && sudo systemctl reload nginx
```

### 5e. Verify HTTPS

```bash
curl -s https://GENERIC.DOMAIN/api/health | python3 -m json.tool
# Expected: {"status": "ok", "database": "connected", ...}
```

### 5f. Auto-renewal

```bash
sudo certbot renew --dry-run
```

---

## 6. Firewall Hardening

Ensure only nginx ports are public. Application ports stay on localhost:

```bash
# Allow SSH, HTTP, HTTPS only
sudo ufw allow 22/tcp
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw default deny incoming
sudo ufw enable

# Verify PORT#FE and PORT#DB are NOT accessible externally
# From another machine:
curl http://<server-ip>:PORT#FE    # Should timeout / connection refused
```

**Restrict Docker port bindings to localhost** — edit `docker-compose.yml`:

```yaml
ports:
  - "127.0.0.1:PORT#DB:5432"   # Was "PORT#DB:5432"
```

**Restrict gunicorn to localhost** — in start.sh or server_deploy.sh:

```
--bind 127.0.0.1:PORT#FE    # Not 0.0.0.0:PORT#FE
```

---

## 7. Post-Installation Verification

Run the endpoint test suite against the live URL:

```bash
./app/scripts/test_endpoints.sh https://GENERIC.DOMAIN
```

### Manual smoke test checklist:

| Step | Action | Expected |
|------|--------|----------|
| 1 | `curl https://GENERIC.DOMAIN/api/health` | `{"status":"ok","database":"connected"}` |
| 2 | Open `https://GENERIC.DOMAIN/login` in browser | Login form renders |
| 3 | Log in with bootstrap SU credentials | Redirect to dashboard |
| 4 | **Change the SU password immediately** | Success message |
| 5 | Visit admin panel | Admin panel loads |
| 6 | `curl http://GENERIC.DOMAIN/api/health` | 301 redirect to HTTPS |
| 7 | Test from external network | Same results as above |

---

## 8. Common Pitfalls and How to Avoid Them

### Pitfall 1: Docker socket path (Colima on macOS)

**Problem:** Docker commands fail with "Cannot connect to the Docker daemon."
**Fix:** Colima uses a non-standard socket. Export before running Docker:

```bash
export DOCKER_HOST=unix://$HOME/.colima/default/docker.sock
```

Add to `~/.zshrc` or `~/.bashrc` for persistence. Deployment scripts should detect this automatically.

### Pitfall 2: Port conflicts with other services

**Problem:** Another service already uses PORT#FE or PORT#DB.
**Fix:** Check before starting:

```bash
for p in PORT#FE PORT#DB; do lsof -ti :$p | xargs kill -9 2>/dev/null; done
```

Better: choose ports that are confirmed free on this server. Update `.env`, `docker-compose.yml`, and `start.sh` to match.

### Pitfall 3: Database password mismatch

**Problem:** `.env` DATABASE_URL password doesn't match `docker-compose.yml` POSTGRES_PASSWORD.
**Fix:** They must be identical. If you change one, change both, then recreate the volume:

```bash
docker compose down -v && docker compose up -d db
```

### Pitfall 4: ALLOWED_ORIGINS not updated for production domain

**Problem:** CORS errors in browser — API calls blocked.
**Fix:** Set `ALLOWED_ORIGINS=https://GENERIC.DOMAIN` in `.env`. Restart application.

### Pitfall 5: ALLOWED_CALLBACK_URLS not updated

**Problem:** Auth handshake fails with "callback URL not allowed."
**Fix:** Set `ALLOWED_CALLBACK_URLS=https://GENERIC.DOMAIN/callback` in `.env`.

### Pitfall 6: nginx config syntax error breaks ALL sites

**Problem:** A typo in the new config could prevent nginx from reloading.
**Fix:** **Always** run `sudo nginx -t` before `sudo systemctl reload nginx`. If it fails, fix the config — nginx keeps the old working config until a successful reload. Never use `nginx restart` during troubleshooting.

### Pitfall 7: Let's Encrypt rate limits

**Problem:** Too many failed cert requests → rate-limited for a week.
**Fix:** Always use `--staging` first to validate, then switch to production:

```bash
sudo certbot certonly --staging --webroot -w /var/www/certbot -d GENERIC.DOMAIN
# Once confirmed working, re-run without --staging
```

### Pitfall 8: Application server binds to 0.0.0.0

**Problem:** Application accessible directly on PORT#FE from the internet, bypassing nginx/SSL.
**Fix:** Bind to `127.0.0.1:PORT#FE` only. Verify in start.sh, server_deploy.sh, and gunicorn config.

### Pitfall 9: .env file permissions too open

**Problem:** Other users on the server can read secrets.
**Fix:**

```bash
chmod 600 DEPLOY_DIR/app/.env
```

### Pitfall 10: Database init script drops all tables

**Problem:** Running `init_db.py` (or equivalent) on a populated database **destroys all data**.
**Fix:** Only run during initial install. For updates, use the update mode of the deployment script, which skips DB init and creates a backup first.

### Pitfall 11: server_name collision with existing nginx sites

**Problem:** Two nginx server blocks with the same `server_name` cause unpredictable routing.
**Fix:** Before creating the config, verify no other config claims the same domain:

```bash
grep -r "server_name.*GENERIC.DOMAIN" /etc/nginx/sites-enabled/
```

### Pitfall 12: Forgetting to update FLASK_ENV

**Problem:** Running in development mode on production exposes debug info, disables HSTS, weakens cookies.
**Fix:** Set `FLASK_ENV=production` in `.env`. Verify with:

```bash
curl -sI https://GENERIC.DOMAIN/ | grep -i strict-transport
# Should show: Strict-Transport-Security: max-age=31536000; includeSubDomains
```

---

## 9. Rollback Plan

### If nginx config change broke something:

```bash
# Remove the new config
sudo rm -f /etc/nginx/sites-enabled/GENERIC.DOMAIN

# Restore backup if the sites-available file was overwritten
sudo cp /etc/nginx/sites-available.bak.<date>/GENERIC.DOMAIN /etc/nginx/sites-available/ 2>/dev/null

# Test and reload
sudo nginx -t && sudo systemctl reload nginx
```

### If the application itself is broken:

```bash
cd DEPLOY_DIR/app
./safe_restart.sh
```

### If the database is corrupted (and a backup exists):

```bash
# Backups stored by server_deploy.sh at DEPLOY_DIR/backups/
pg_restore -h localhost -p PORT#DB -U <db_user> -d <db_name> DEPLOY_DIR/backups/<latest>.dump
```

---

## 10. Quick-Reference: Complete Installation Sequence

```
 1.  Verify Docker running, ports free, nginx healthy
 2.  Backup existing nginx config (sites-available + sites-enabled)
 3.  Transfer deployment tarball to server
 4.  Run: ./server_deploy.sh <tarball> install
 5.  Edit .env (production values, strong passwords, correct domain)
 6.  Verify: curl http://127.0.0.1:PORT#FE/api/health → 200
 7.  Configure DNS: GENERIC.DOMAIN → server IP
 8.  Wait for DNS propagation (dig +short GENERIC.DOMAIN)
 9.  Create nginx site config (HTTP only, with certbot location)
10.  sudo nginx -t && sudo systemctl reload nginx
11.  sudo certbot certonly --staging -d GENERIC.DOMAIN (test first)
12.  sudo certbot certonly -d GENERIC.DOMAIN (production cert)
13.  Update nginx config to full HTTPS version
14.  sudo nginx -t && sudo systemctl reload nginx
15.  Verify: curl https://GENERIC.DOMAIN/api/health → 200
16.  Harden firewall: ufw allow 22, 80, 443 only
17.  Restrict Docker + gunicorn port bindings to 127.0.0.1
18.  Login as SU → change bootstrap password immediately
19.  Run endpoint test suite against https://GENERIC.DOMAIN
20.  Verify certbot auto-renewal: sudo certbot renew --dry-run
```

---

## 11. For Future Updates

```bash
# On dev machine:
./pack_deploy.sh

# Transfer to server, then:
./server_deploy.sh <new-tarball> update
# This backs up .env + DB, deploys new code, restores config, skips DB init

# Or for a quick restart after config changes:
cd DEPLOY_DIR/app
./safe_restart.sh
```

---

## 12. Checklist Before Leaving the Server

- [ ] `sudo nginx -t` returns OK
- [ ] All other sites still respond (`curl` each one)
- [ ] `https://GENERIC.DOMAIN/api/health` returns `{"status":"ok"}`
- [ ] `http://GENERIC.DOMAIN` redirects to HTTPS
- [ ] PORT#FE and PORT#DB are **not** reachable from external network
- [ ] `.env` has `chmod 600`
- [ ] `FLASK_ENV=production`
- [ ] Bootstrap SU password has been changed
- [ ] Certbot auto-renewal is active (`systemctl list-timers | grep certbot`)
- [ ] nginx backup from step 2 is still in place
