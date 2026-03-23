# Credential Rotation Runbook

Procedures for rotating secrets used by the Contract Manager. Perform these on a regular schedule or immediately after a suspected compromise.

---

## 1) JWT signing key

### 1.1 When to rotate

- Regular schedule: every 90 days
- Immediately if the key is exposed in logs, commits, or shared environments

### 1.2 Procedure

1. Generate a new secret: `python3 -c "import secrets; print(secrets.token_urlsafe(48))"`
2. Update the `JWT_SECRET_KEY` value in `./app/docker-compose.yml` (or your `.env` file)
3. Restart the API container: `cd app && docker compose restart api`
4. **Impact**: all existing JWT tokens become invalid — all users must log in again

### 1.3 Verification

```bash
curl -X POST http://localhost:9021/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"your-password"}'
```

Confirm a new token is issued successfully.

---

## 2) Bootstrap admin password

### 2.1 When to rotate

- After first deployment (change from default `change-me`)
- If the password is shared or exposed

### 2.2 Procedure

1. Update `BOOTSTRAP_ADMIN_PASSWORD` in `docker-compose.yml` or `.env`
2. The bootstrap only creates the user if it does not exist — changing the env var alone does NOT update an existing user's password
3. To update the existing admin password, use the API:

```bash
# Get the admin's GUID
curl -s http://localhost:9021/admin/users \
  -H "Authorization: Bearer <token>" | python3 -m json.tool

# Reset the password
curl -X POST http://localhost:9021/admin/users/<admin-guid>/reset-password \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"password":"new-secure-password"}'
```

### 2.3 Verification

Log in with the new password and confirm a token is returned.

---

## 3) Database credentials

### 3.1 When to rotate

- Moving from development to production
- If the database password is exposed

### 3.2 Procedure

1. Update `POSTGRES_PASSWORD` in the `db` service environment
2. Update the `DATABASE_URL` in the `api` service environment to match
3. Recreate the database volume (local dev): `cd app && docker compose down -v && docker compose up --build`
4. **Production**: use `ALTER ROLE contracts WITH PASSWORD 'new-password';` inside psql, then update `DATABASE_URL` and restart the API

### 3.3 Verification

Check that the API container starts without database connection errors:

```bash
docker compose -f app/docker-compose.yml logs api | tail -20
curl http://localhost:9021/health
```

---

## 4) Emergency rotation (all secrets)

If you suspect a broad compromise:

1. Rotate JWT_SECRET_KEY (invalidates all sessions)
2. Rotate database password
3. Reset all user passwords via the API or by recreating the database
4. Review `docker compose logs api` for unauthorized access patterns
5. Run the endpoint test script to verify service health
