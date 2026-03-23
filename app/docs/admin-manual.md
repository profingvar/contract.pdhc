# Contract Service — Admin Manual

This manual covers administrative tasks: authentication, user lifecycle, contract management, and rate limiting behaviour.

---

## 1) Authentication

### 1.1 Login flow

Send a POST request to `/auth/login` with your credentials:

```json
POST /auth/login
{
  "username": "admin",
  "password": "your-password"
}
```

On success (200), the response contains:

```json
{
  "access_token": "eyJ...",
  "role": "admin"
}
```

Use the token on all admin endpoints:

```
Authorization: Bearer eyJ...
```

Tokens expire after **8 hours**. After expiry, log in again.

### 1.2 Logout

There is no server-side logout. Discard the token on the client side (the SPA clears it from memory on logout).

---

## 2) User lifecycle

All user management endpoints require `admin` role.

### 2.1 Create user

```json
POST /admin/users
{
  "username": "new.user",
  "password": "SecureP@ss1",
  "role": "reader"
}
```

- **`role`** must be `admin` or `reader`
- **`username`** must be unique
- Returns 201 with the new user object (guid, username, role, is_active)

### 2.2 Update role

```json
PUT /admin/users/{guid}
{
  "role": "admin"
}
```

### 2.3 Reset password

```json
POST /admin/users/{guid}/reset-password
{
  "password": "NewSecureP@ss2"
}
```

The user's existing sessions (JWT tokens) remain valid until they expire. For immediate revocation, rotate the `JWT_SECRET_KEY` (see runbook: `runbooks/credential-rotation.md`).

### 2.4 Deactivate user

```json
PUT /admin/users/{guid}
{
  "is_active": false
}
```

Deactivated users cannot log in. Existing tokens will still pass JWT validation but the login endpoint rejects inactive users.

---

## 3) Contract management

### 3.1 Creating contracts

```json
POST /fhir/Contract
{
  "resourceType": "Contract",
  "status": "executable",
  "period": {
    "start": "2026-01-01T00:00:00Z",
    "end": "2026-12-31T23:59:59Z"
  },
  "subject": [
    {"reference": "Organization/abc-123"}
  ]
}
```

- **`resourceType`** must be `"Contract"` (required)
- **`status`** must be a valid FHIR R5 contract status (required)
- **`period.start`** and **`period.end`** must be ISO-8601 datetime strings (optional)
- **`subject`** references must use `"ResourceType/id"` format (optional)
- A GUID is auto-generated if `id` is not provided

### 3.2 Editing contracts

```json
PUT /fhir/Contract/{guid}
```

Send the full updated Contract resource. The `id` field in the URL takes precedence.

### 3.3 Deleting contracts

```
DELETE /fhir/Contract/{guid}
```

Returns 204 on success. This is permanent — there is no soft-delete or undo.

### 3.4 Viewing contracts

Public (no auth required), rate-limited:

- **List**: `GET /fhir/Contract` — returns a FHIR Bundle (searchset)
- **Read**: `GET /fhir/Contract/{guid}` — returns a single Contract resource

---

## 4) Rate limiting

### 4.1 Read rate limit

Public read endpoints (`GET /fhir/Contract`, `GET /fhir/Contract/{guid}`, `GET /fhir/metadata`) are limited to **100 requests per hour per IP address**.

Configurable via the `READ_RATE_LIMIT` environment variable (e.g., `"200 per hour"`).

### 4.2 Handling 429 responses

When the limit is exceeded, the API returns:

```
HTTP 429 Too Many Requests
```

Wait for the rate window to reset (1 hour from the first request in the window).

---

## 5) Credential rotation

For procedures on rotating `JWT_SECRET_KEY`, database passwords, and bootstrap credentials, see `runbooks/credential-rotation.md`.
