# Contract Service — Architecture

Technical architecture of the PDHC Contract Manager, covering container topology, request/data flows, the consent side effects, the data model, and security posture.

---

## 1) System overview

### 1.1 Purpose

The Contract Manager is a FHIR R5 microservice for creating, reading, updating, and deleting healthcare **Contract** resources. It provides public, rate-limited read access and SSO-authenticated write access. Beyond CRUD, a contract write drives three integration side effects: **scope-concept validation** against plan.pdhc, **PatientConsent emission** to ips.pdhc, and **PAT auto-provisioning** to request.pdhc.

### 1.2 Position in the PDHC platform

The Contract Manager is one service in the PDHC family, alongside:

- **`sso.pdhc.se`** — single sign-on; validates tokens and returns the access blob.
- **`ips.pdhc.se`** — patient registry; holds `PatientConsent` and `PatientBlock` rows.
- **`plan.pdhc.se`** — PlanDefinition + concept/terminology authority.
- **`request.pdhc.se`** — orchestrating gateway; owns Provider Access Tokens (PATs).
- **`gateway.pdhc.se`** — ingest gateway; reads contract return scope.
- **`contract.pdhc.se`** — this service.

Each service runs independently on its own port block and Docker Compose project.

### 1.3 Where contracts fit in the PDL consent + blocking model

The platform has **three peer concepts** for governing who may see
whose data. They are not interchangeable. New code consistently
gets this wrong — the symptom is usually a contract being asked to
do something it cannot, and a Patient* row not being created where
one should have been. Pick by what is being expressed:

| You want to express… | Use… | Lives in | PDL/legal basis |
|---|---|---|---|
| "Organisation A may submit observations on concepts C[] under provider B's care plans" | `Contract` (`term[]` with `request_scope` / `return_scope`) | contract.pdhc | Civil agreement between two orgs — not a patient-data ruling |
| "Patient P consents that caregiver G may read their data (optionally only concepts C[])" | `PatientConsent` | ips.pdhc (`/api/v1/patients/<guid>/consents`) | Lag (2022:913) § 5 cohesive-care consent |
| "Patient P blocks caregiver/clinic S from reading their data" | `PatientBlock` | ips.pdhc (`/api/v1/patients/<guid>/blocks`) | PDL Ch 4 § 4 spärr |

The shape is the giveaway:

- **Contracts are concept-shaped.** They scope traffic between
  organisations — never between a patient and an organisation. A
  contract has a `signer[]` list and can include a patient signer,
  but that is the patient *attesting to a civil agreement they are
  the subject of*, not the contract acting as their consent record.
  Even when the patient signs, the contract itself does not "scope
  to" the patient: the next request from a different patient also
  uses the same contract.

- **PatientConsent is patient-shaped + caregiver-shaped + optionally
  concept-narrowed.** It always belongs to exactly one patient and
  names exactly one caregiver grantee. It exists so cohesive-care
  read paths can *enforce* the patient's affirmative yes.

- **PatientBlock is patient-shaped + source-shaped.** Same patient
  axis as PatientConsent, but on the *no* side: hides a clinic's (or
  caregiver's) data from readers outside that scope.

#### Auto-emit from contract to consent (#231)

When a contract is in a grant status and a `Patient/<guid>` reference
appears in `signer[]`, contract.pdhc emits a `PatientConsent` row on
ips.pdhc as a side effect (`granted_via='contract'`,
`contract_guid=<linkback>`). The signer reference and the auto-emitted
consent are two distinct artefacts in two distinct services, related by
`contract_guid`:

- **contract.pdhc** keeps the legal artefact: who agreed, with what
  scope, at what time. Cancelling the contract revokes the
  auto-emitted consent.
- **ips.pdhc** keeps the enforcement artefact: a row that downstream
  read paths can consult cheaply without going through contract.pdhc.

If you only need patient consent (no civil agreement, no concept
scope, no provider org party) — author the `PatientConsent` directly
on ips.pdhc. Inventing a contract just to get a consent row is the
wrong tool.

If you only need a block — go straight to `PatientBlock`. A contract
cannot revoke another organisation's read rights to a patient's data;
that is structurally a different decision (the patient's, not the
caregiver's).

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

- **Browser** loads the SPA from `web` on port 9022.
- **SPA** makes API calls directly to `api` on port 9021.
- **API** reads/writes data to `db` (`postgres:16`) on port 9020.

All three are one Docker Compose project (`name: contract`). The `pgdata` volume is declared `external` as `app_contracts_pgdata` so a rebuild never silently forks a second data volume.

### 2.2 Port map

| Service | Container Port | Host bind | Purpose |
|---------|---------------|-----------|---------|
| **db** | 5432 | `127.0.0.1:9020` | PostgreSQL 16 |
| **api** | 9021 | `127.0.0.1:9021` | Flask REST API |
| **web** | 80 | `127.0.0.1:9022` | nginx serving SPA + docs |

Every port is pinned to `127.0.0.1` (ticket #72) so only the reverse proxy — not the LAN — can reach the containers. Binding to `0.0.0.0` would expose the DB/API directly and bypass SSO (CLAUDE.md §3).

### 2.3 Service-to-service consumers and callees

**Inbound** (other services call the Contract Manager):

- **gateway.pdhc** — `GET /internal/contract/{guid}/scope` to fetch a contract's return scope for observation validation. Authenticated with the `X-Service-Key` header (shared secret `INTERNAL_SERVICE_KEY`, compared with `hmac.compare_digest`). This endpoint has no rate limit and additionally returns a `parties` block (requesting + provider org GUIDs).

**Outbound** (the Contract Manager calls, as side effects of a write):

- **sso.pdhc** — `GET /api/auth/me/service` to validate a login token.
- **plan.pdhc** — `GET /api/v1/concepts/<guid>` to verify scope concepts exist.
- **ips.pdhc** — `POST /api/v1/patients/<guid>/consents` (grant) and `.../consents/<guid>/revoke` (revoke) for auto-emitted consents.
- **request.pdhc** — `POST /api/v1/internal/auto-provision-pat` to provision a PAT for the provider org (the PAT itself is owned by request.pdhc).

This internal/outbound layer is separate from the public FHIR API and the SSO-authenticated write API.

---

## 3) Request and data flows

### 3.1 Public read flow

```
Browser → GET localhost:9022 → nginx serves index.html (SPA)
SPA JS → GET localhost:9021/fhir/Contract → Flask → PostgreSQL → JSON Bundle
```

Read endpoints (`/fhir/metadata`, `/fhir/Contract`, `/fhir/Contract/{guid}`, `/fhir/Contract/{guid}/scope`) are public and rate-limited per IP (flask-limiter, in-memory store, `READ_RATE_LIMIT`).

### 3.2 SSO auth flow

Production runs with `AUTH_DISABLED=false`. The SPA never handles a password.

1. `GET /api/v1/auth/login` — store a CSRF `state` in the session, redirect to `SSO_BASE_URL/login?next=<callback>&state=<state>`.
2. SSO authenticates the user and redirects back to `GET /api/v1/auth/callback?token=…&state=…`.
3. The callback validates `state`, then calls `sso.pdhc /api/auth/me/service` (headers `X-SSO-Client-Id` / `X-SSO-Client-Secret`) to validate the token and obtain the **access blob**.
4. If `blob.must_change_password` is set, redirect to `SSO_BASE_URL/change-password` and mint nothing.
5. Otherwise derive a role from the blob (§5.2), mint an 8-hour local JWT with the derived role plus reform/legacy identity claims (`organization_ids`/`care_unit_guids`, `effective_phases`/`session_phases`, `is_su_admin`, `user_type`, …), and redirect to `PUBLIC_WEB_URL/?sso_token=<jwt>`.

`GET /api/v1/auth/me` echoes the JWT claims; `GET /api/v1/auth/logout` clears the session.

The token is **not cached** — every request that needs identity re-reads the JWT the callback minted; an SSO-side change takes effect on the next login. The legacy local `POST /auth/login` is inert unless `AUTH_DISABLED=true` (dev-only, see §5.1).

### 3.3 Admin write flow (create / update)

`POST /fhir/Contract` and `PUT /fhir/Contract/{guid}` both require the `admin` role and run the same pipeline:

1. **Shape validation** — `ensure_contract_shape()` enforces the FHIR R5 shape (resourceType, status, party/topic/signer/term structure). Failure → `400 validation`.
2. **Scope-concept validation** — `_validate_scope_concepts()` extracts every concept GUID from `term[].asset[].typeReference[]` and verifies each exists in plan.pdhc. A missing concept → `422 scope_concept_missing`; plan.pdhc unreachable under `STRICT_SCOPE_CONCEPTS=true` → `503 scope_validation_unavailable`. With the flag `false`, validation is skipped.
3. **Signer resolution** — `_verify_signers()` resolves each `signer[]` reference against the relevant catalogue. Unresolved references → `400 signer_unresolved` (gated by `STRICT_SIGNER_VALIDATION`).
4. **Persist** — INSERT (create, `409` on duplicate id) or overwrite `fhir_contract` (update, `404` if absent).
5. **PAT auto-provision** — `_auto_provision_pat()`: if the status is `executed`/`executable`/`offered`/`renewed`, POST each provider org to request.pdhc's `/api/v1/internal/auto-provision-pat` (this is the Medituner-style onboarding trigger). Requires `REQUEST_BASE_URL` + `INTERNAL_SERVICE_KEY`.
6. **Consent lifecycle** — `_emit_consents_for_lifecycle()`: see §3.5.

Steps 5–6 are best-effort — failures are logged, never propagated to the write response.

### 3.4 Delete flow

`DELETE /fhir/Contract/{guid}` (admin) removes the row and returns `204`. As a best-effort side effect it calls `revoke_patient_consents()` for the deleted contract, revoking any consents on ips.pdhc that linked back to it.

### 3.5 Contract → consent emission (`_emit_consents_for_lifecycle`)

After a create/update commits, the status decides the verb:

- **Grant statuses** — `executed`, `executable`, `offered`, `renewed` → `emit_patient_consents()`. For each `Patient/<guid>` in `signer[]` crossed with each `provider` org in `party[]`, POST a `PatientConsent` to ips.pdhc (`granted_via='contract'`, `contract_guid=<id>`, `expires_at` from `period.end` when present). Idempotent: an existing active consent with the same `contract_guid` + grantee is skipped.
- **Revoke statuses** — `cancelled`, `terminated`, `revoked` → `revoke_patient_consents()`. For each patient signer, revoke every active consent whose `contract_guid` matches this contract.
- Other statuses (drafts, `negotiable`, …) have no consent implication.

The **reconciler** (`consent_reconciler.py`, CLI `flask reconcile-consents`) is the recovery path for emissions dropped while IPS was briefly down. It walks every contract in a lifecycle status and re-runs the idempotent emitter/revoker. It **lives on this service** and runs hourly on the macmini (#246, #243).

---

## 4) Data model

The schema is deliberately small — **two tables**, created via `Base.metadata.create_all` on boot.

### 4.1 Users table (`users`)

Only populated in `AUTH_DISABLED=true` (dev) installs; in production identity comes from SSO.

| Column | Type | Constraints |
|--------|------|-------------|
| **`guid`** | `VARCHAR(36)` | Primary key, UUID v4 |
| **`username`** | `VARCHAR(128)` | Unique, not null |
| **`password_hash`** | `VARCHAR(255)` | Not null (bcrypt) |
| **`role`** | `VARCHAR(16)` | Not null (`"admin"` or `"reader"`) |
| **`is_active`** | `BOOLEAN` | Not null, default `true` |
| **`created_at`** | `TIMESTAMPTZ` | Not null, auto-set to UTC now |

### 4.2 Contract records table (`contract_records`)

| Column | Type | Constraints |
|--------|------|-------------|
| **`guid`** | `VARCHAR(36)` | Primary key, UUID v4 |
| **`fhir_contract`** | `JSON` | Not null, stores the full FHIR R5 Contract resource |
| **`created_at`** | `TIMESTAMPTZ` | Not null, auto-set to UTC now |
| **`updated_at`** | `TIMESTAMPTZ` | Not null, auto-updated on modification |

The entire FHIR Contract lives in the `fhir_contract` JSON — including scope, parties, signers, and four PDHC-defined extensions kept inside `Contract.extension[]` (so the JSON stays portable across FHIR servers; they ride along without a platform-specific column):

| Extension URL | Type | Purpose |
|---|---|---|
| `https://contract.pdhc.se/StructureDefinition/legally-ok` | bool | Operator has signed off on legal terms |
| `https://contract.pdhc.se/StructureDefinition/pub-exists` | bool | A personuppgiftsbiträdesavtal (data-processor agreement) exists |
| `https://contract.pdhc.se/StructureDefinition/legal-provider` | bool | Provider is a legally registered entity |
| `https://contract.pdhc.se/StructureDefinition/provider-data-status` | code | `ok` / `deficient` / `unclear` — provider-data verification verdict |

The API accepts any of the 15 FHIR R5 contract-status codes, but the **UI constrains `Contract.status` to four**: `negotiable` (Under consideration), `executed` (Active — only this state qualifies the contract as a basis for fulfilling requests), `terminated` (Expired), and `revoked` (Revoked). Other codes are still accepted via the API for compatibility with externally authored Contracts.

### 4.3 Concept scope shape (`term[]`)

Scope is expressed as FHIR `Contract.term[]` entries, validated by `fhir._validate_terms` and read back by `get_contract_scope`:

- **`request_scope`** — assets of type `outbound_concept`; the concepts an org may submit.
- **`return_scope`** — assets of type `obligatory_return` and `optional_return`; the concepts that must / may be returned.

Each concept is a `typeReference[].reference` URL of the form `https://…/api/v1/concepts/<uuid>`; the GUID is parsed out for the scope endpoints. A contract with no `term[]` has undefined scope (backward-compatible = all permitted).

### 4.4 GUID rules

- All primary keys are UUID v4 strings (36 chars).
- GUIDs are generated server-side when the client omits `id`.
- Frontend, backend, and cross-service references coordinate via GUIDs, never numeric IDs (Rule 18).

---

## 5) Security posture

### 5.1 Authentication

- **Production**: SSO. The service validates the login token against sso.pdhc and mints a short-lived local JWT (§3.2). `AUTH_DISABLED` defaults to `false`; `config.py` **refuses to boot with `AUTH_DISABLED=true` unless `FLASK_ENV=development`**, so the local-login/bootstrap-admin path can never accidentally ship to the server.
- **Local JWT**: `flask-jwt-extended`, `JWT_SECRET_KEY`, 8-hour expiry.
- **Dev-only local login**: `POST /auth/login` verifies bcrypt (`werkzeug.security`) against the `users` table and is only reachable under `AUTH_DISABLED=true`.

### 5.2 Authorization

- Role is derived from the SSO access blob: `is_su_admin` **or** `user_type == "professional"` → **admin**; anyone else authenticated → **reader**.
- **`admin`**: full contract CRUD + user management.
- **`reader`**: read-only (functionally the same as anonymous, but identified).
- Enforced by the `require_role()` decorator, which reads the `role` claim from the JWT.
- Internal service calls use `require_service_key` (`X-Service-Key` vs `INTERNAL_SERVICE_KEY`, constant-time compare); when the key is unset the endpoint rejects everyone.

### 5.3 Rate limiting

- **Library**: `flask-limiter`, in-memory store (default; `LIMITER_STORAGE_URI` to override).
- **Limit**: `READ_RATE_LIMIT` (default `100 per hour`) per IP.
- **Scope**: the public read endpoints `GET /fhir/metadata`, `GET /fhir/Contract`, `GET /fhir/Contract/{guid}`, `GET /fhir/Contract/{guid}/scope`. The internal scope endpoint is **not** rate-limited.

### 5.4 CORS

- **Library**: `flask-cors`, configured from `CORS_ORIGINS` (default `*` for local dev; restrict to `https://contract.pdhc.se` in production).
- `GET /health` additionally sets an explicit `Access-Control-Allow-Origin: https://www.pdhc.se` so `www.pdhc.se/services.html` can read the JSON body and drive real status/DB dots (ticket #70 / CLAUDE.md §10).

### 5.5 Database security

- **Local dev**: `POSTGRES_HOST_AUTH_METHOD: trust` on the container network (harden to SCRAM-SHA-256 + TLS for server deployment).
- **`POSTGRES_PASSWORD` is mandatory** — Compose uses `${POSTGRES_PASSWORD:?…}` for both the `db` container and the `api` container's `DATABASE_URL`, so the stack refuses to start without it (Rollup #350 §1.2). Note the trust rule means local password tests can lie (CLAUDE.md §9).
- **Credentials** live in environment variables only, never hardcoded.

### 5.6 Health

`GET /health` probes the DB and returns the canonical PDHC shape — `{status, database, service, version}` — with HTTP `200` when the DB is reachable and `503` (`degraded` / `unavailable`) when it is not.

## Port Allocation

All ports bind to `127.0.0.1` (loopback only); external traffic arrives
via the reverse proxy. (Detailed in §2.2 above.)

| Port | Service |
|------|---------|
| 9021 | Flask REST API (Gunicorn) |
| 9020 | PostgreSQL database |
| 9022 | nginx serving SPA + docs |
