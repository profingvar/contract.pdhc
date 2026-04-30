# Contract Service — Architecture

Technical architecture of the PDHC Contract Manager, covering container topology, data flows, data model, and security posture.

---

## 1) System overview

### 1.1 Purpose

The Contract Manager is a FHIR R5 microservice for creating, reading, updating, and deleting healthcare contract resources. It provides public read access with rate limiting and admin-authenticated write access via JWT tokens.

### 1.2 Position in the PDHC platform

The Contract Manager is one service in the PDHC family, alongside:

- **`ips.pdhc.se`** — Patient/IPS data
- **`plan.pdhc.se`** — PlanDefinition builder
- **`sso.pdhc.se`** — Single sign-on
- **`request.pdhc.se`** — Orchestrating gateway
- **`contract.pdhc.se`** — This service

Each service runs independently on its own port range and Docker Compose project.

---

## 2) Container topology

### 2.1 Architecture diagram

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│   browser    │────▶│  web:9022   │     │  db:9020    │
│              │     │  (nginx)    │     │ (PostgreSQL) │
└─────────────┘     └─────────────┘     └──────┬──────┘
                           │                    │
                           │ API calls          │ SQL
                           ▼                    │
                    ┌─────────────┐             │
                    │  api:9021   │─────────────┘
                    │  (Flask)    │
                    └─────────────┘
```

- **Browser** loads the SPA from `web` on port 9022
- **SPA** makes API calls directly to `api` on port 9021
- **API** reads/writes data to `db` on port 9020

### 2.2 Port map

| Service | Container Port | Host Port | Purpose |
|---------|---------------|-----------|---------|
| **db** | 5432 | 9020 | PostgreSQL database |
| **api** | 9021 | 9021 | Flask REST API |
| **web** | 80 | 9022 | nginx serving SPA + docs |

All ports are within the 9020–9030 range as required by project rules.

### 2.3 Service-to-service consumers

The Contract Manager exposes an **internal API** consumed by other PDHC services:

- **gateway.pdhc** — calls `GET /internal/contract/{guid}/scope` to fetch contract return scope for observation validation. Authenticated via `X-Service-Key` header (shared secret in `INTERNAL_SERVICE_KEY` env var, validated with `hmac.compare_digest`).

This internal layer is separate from the public FHIR API and admin JWT endpoints.

---

## 3) Data flows

### 3.1 Public read flow

```
Browser → GET localhost:9022 → nginx serves index.html (SPA)
SPA JS → GET localhost:9021/fhir/Contract → Flask → PostgreSQL → JSON response
```

Rate limiting is applied per IP at the Flask layer (flask-limiter, in-memory store).

### 3.2 Admin write flow

```
SPA JS → POST localhost:9021/auth/login → JWT token returned
SPA JS → POST localhost:9021/fhir/Contract (Bearer token) → Flask validates JWT + role → PostgreSQL INSERT → 201
```

### 3.3 Auth flow

1. Client sends `POST /auth/login` with username + password
2. Flask verifies credentials against the `users` table (bcrypt hash)
3. On success, Flask issues a JWT with claims: `identity` (user guid), `role`, `username`
4. Token expires after 8 hours
5. Client includes `Authorization: Bearer <token>` on admin requests
6. Flask-JWT-Extended validates the token and extracts claims

---

## 4) Data model

### 4.1 Users table

| Column | Type | Constraints |
|--------|------|-------------|
| **`guid`** | `VARCHAR(36)` | Primary key, UUID v4 |
| **`username`** | `VARCHAR(128)` | Unique, not null |
| **`password_hash`** | `VARCHAR(255)` | Not null (bcrypt) |
| **`role`** | `VARCHAR(16)` | Not null (`"admin"` or `"reader"`) |
| **`is_active`** | `BOOLEAN` | Not null, default `true` |
| **`created_at`** | `TIMESTAMPTZ` | Not null, auto-set to UTC now |

### 4.2 Contract records table

| Column | Type | Constraints |
|--------|------|-------------|
| **`guid`** | `VARCHAR(36)` | Primary key, UUID v4 |
| **`fhir_contract`** | `JSON` | Not null, stores the full FHIR R5 Contract resource |
| **`created_at`** | `TIMESTAMPTZ` | Not null, auto-set to UTC now |
| **`updated_at`** | `TIMESTAMPTZ` | Not null, auto-updated on modification |

The `fhir_contract` JSON includes the full FHIR R5 Contract shape plus four PDHC-defined extensions (kept inside `Contract.extension[]` so the JSON stays portable across FHIR servers — they ride along with the contract record without needing a platform-specific column):

| Extension URL                                                                | Type        | Purpose |
|------------------------------------------------------------------------------|-------------|---------|
| `https://contract.pdhc.se/StructureDefinition/legally-ok`                    | bool        | Operator has signed off on legal terms |
| `https://contract.pdhc.se/StructureDefinition/pub-exists`                    | bool        | A personuppgiftsbiträdesavtal (data-processor agreement) exists |
| `https://contract.pdhc.se/StructureDefinition/legal-provider`                | bool        | Provider is a legally registered entity |
| `https://contract.pdhc.se/StructureDefinition/provider-data-status`          | code        | `ok` / `deficient` / `unclear` — provider-data verification verdict |

The status field of `Contract.status` is constrained at the UI layer to four FHIR R5 codes — `negotiable` (Under consideration), `executed` (Active — only this state qualifies the contract as a basis for fulfilling requests), `terminated` (Expired), `revoked` (Revoked). Other FHIR codes are accepted via direct API for compatibility with externally authored Contracts.

### 4.3 GUID rules

- All primary keys are UUID v4 strings (36 characters, e.g., `"a1b2c3d4-e5f6-7890-abcd-ef1234567890"`)
- GUIDs are generated server-side when not provided by the client
- Frontend and backend always coordinate via GUIDs, never numeric IDs

---

## 5) Security posture

### 5.1 Authentication

- **Mechanism**: JWT Bearer tokens via `flask-jwt-extended`
- **Password hashing**: bcrypt via `werkzeug.security`
- **Token expiry**: 8 hours
- **Signing key**: `JWT_SECRET_KEY` environment variable

### 5.2 Authorization

- **`admin`**: full CRUD on contracts + user management
- **`reader`**: read-only access (same as unauthenticated, but identified)
- Role is stored in JWT claims and checked via `require_role()` decorator

### 5.3 Rate limiting

- **Library**: `flask-limiter`
- **Storage**: in-memory (default), configurable via `LIMITER_STORAGE_URI`
- **Limit**: 100 requests/hour per IP on public read endpoints
- **Scope**: `GET /fhir/Contract`, `GET /fhir/Contract/{guid}`, `GET /fhir/metadata`

### 5.4 CORS

- **Library**: `flask-cors`
- **Configuration**: `CORS_ORIGINS` environment variable (default: `*` for local dev)
- **Production**: restrict to `https://contract.pdhc.se`

### 5.5 Database security

- **Local dev**: `POSTGRES_HOST_AUTH_METHOD: trust` (no password on container network)
- **Production**: use SCRAM-SHA-256 + TLS between API and database containers
- **Credentials**: stored in environment variables only, never hardcoded
