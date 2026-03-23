# Contract Service — API Reference

Complete endpoint reference for the PDHC Contract Manager API. Base URL: `http://localhost:9021` (local) or `https://contract.pdhc.se` (production).

---

## 1) Overview

### 1.1 Authentication

Admin endpoints require a JWT Bearer token obtained via `POST /auth/login`. Include it as:

```
Authorization: Bearer <token>
```

Tokens expire after 8 hours. Public endpoints require no authentication.

### 1.2 Content type

All request and response bodies are JSON:

```
Content-Type: application/json
```

FHIR endpoints return `application/fhir+json`.

### 1.3 Error response shape

All errors follow this structure:

```json
{
  "error": "error_code",
  "message": "Human-readable description"
}
```

### 1.4 HTTP status codes

| Code | Meaning |
|------|---------|
| **200** | Success |
| **201** | Created |
| **204** | Deleted (no body) |
| **400** | Validation error |
| **401** | Missing or invalid authentication |
| **403** | Insufficient role |
| **404** | Resource not found |
| **409** | Conflict (duplicate username or contract id) |
| **429** | Rate limit exceeded |

---

## 2) Health

### 2.1 GET /health

Returns service health status. No authentication required.

**Response** (200):

```json
{
  "status": "ok"
}
```

---

## 3) Authentication

### 3.1 POST /auth/login

Authenticate and receive a JWT token.

**Request body:**

```json
{
  "username": "admin",
  "password": "your-password"
}
```

**Response** (200):

```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIs...",
  "role": "admin"
}
```

**Error** (401):

```json
{
  "error": "invalid_credentials"
}
```

---

## 4) FHIR endpoints

### 4.1 GET /fhir/metadata

Returns the FHIR R5 CapabilityStatement for this server. Public, rate-limited.

**Response** (200): A full `CapabilityStatement` resource declaring supported resources (Contract), interactions (read, search-type, create, update, delete), and security model.

### 4.2 GET /fhir/Contract

List all contracts. Public, rate-limited (100/hour per IP).

**Response** (200):

```json
{
  "resourceType": "Bundle",
  "type": "searchset",
  "entry": [
    {
      "resource": {
        "resourceType": "Contract",
        "id": "a1b2c3d4-...",
        "status": "executable",
        "period": {"start": "2026-01-01T00:00:00Z"}
      }
    }
  ]
}
```

### 4.3 GET /fhir/Contract/{guid}

Read a single contract. Public, rate-limited.

**Response** (200): The Contract resource.

**Error** (404):

```json
{
  "error": "not_found"
}
```

### 4.4 POST /fhir/Contract

Create a new contract. **Admin required.**

**Request body:**

```json
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

- **`resourceType`**: must be `"Contract"` (required)
- **`status`**: valid FHIR R5 contract status code (required)
- **`period`**: start/end as ISO-8601 datetime (optional)
- **`subject`**: array of references in `"ResourceType/id"` format (optional)
- **`id`**: auto-generated UUID if omitted

**Response** (201): The created Contract resource with `id` populated.

**Errors**: 400 (validation), 409 (duplicate id)

### 4.5 PUT /fhir/Contract/{guid}

Update an existing contract. **Admin required.**

**Request body:** Full Contract resource (same shape as create).

**Response** (200): The updated Contract resource.

**Errors**: 400 (validation), 404 (not found)

### 4.6 DELETE /fhir/Contract/{guid}

Delete a contract. **Admin required.**

**Response**: 204 (no body).

**Error**: 404 (not found)

---

## 5) User management

All user endpoints require `admin` role.

### 5.1 GET /admin/users

List all users.

**Response** (200):

```json
[
  {
    "guid": "d4e5f6...",
    "username": "admin",
    "role": "admin",
    "is_active": true,
    "created_at": "2026-03-20T10:00:00+00:00"
  }
]
```

### 5.2 POST /admin/users

Create a new user.

**Request body:**

```json
{
  "username": "new.user",
  "password": "SecurePass1!",
  "role": "reader"
}
```

- **`role`** must be `"admin"` or `"reader"`

**Response** (201):

```json
{
  "guid": "...",
  "username": "new.user",
  "role": "reader",
  "is_active": true
}
```

**Errors**: 400 (validation), 409 (username exists)

### 5.3 PUT /admin/users/{guid}

Update user role or active status.

**Request body** (any combination):

```json
{
  "role": "admin",
  "is_active": false
}
```

**Response** (200): Updated user object.

**Error**: 404 (not found)

### 5.4 POST /admin/users/{guid}/reset-password

Reset a user's password.

**Request body:**

```json
{
  "password": "NewSecurePass2!"
}
```

**Response** (200):

```json
{
  "ok": true,
  "guid": "...",
  "username": "the.user"
}
```

**Error**: 404 (not found)

---

## 6) Rate limiting

Public read endpoints are rate-limited to **100 requests per hour per IP** (configurable via `READ_RATE_LIMIT` environment variable).

When exceeded, the server returns `429 Too Many Requests`.

---

## 7) FHIR conformance

This server implements a subset of FHIR R5:

- **Resource**: `Contract` only
- **Bundle**: `searchset` type for list responses
- **CapabilityStatement**: available at `GET /fhir/metadata`
- **IDs**: UUID v4 strings (GUIDs)
- **No versioning**: resources are not version-tracked
- **No history**: `_history` endpoint is not supported
