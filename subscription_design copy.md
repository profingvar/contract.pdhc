# provider.pdhc — Subscription Design Document

> **Implementation Note (2026-03-20):** The `request.pdhc` side of this design (the upstream API that provider portals poll) is now fully implemented. Sections 6, 7, and 13 have been annotated with confirmed implementation details. The `provider.pdhc` consumer side (Sections 4, 5, 8–12) remains to be built.

## 1) Problem Statement

The provider portal must subscribe to data from `request.pdhc.se` (the central request registry). When the request service registers new requests in the form of JSON careplans, any request carrying this provider's name/GUID must be automatically downloaded and made available in the portal.

Authentication is via SSO-issued keys from `sso.pdhc.se`.

The same codebase must support multiple independent instances — each configured for a different provider identity (name + GUID).

---

## 2) Architecture Overview

```
┌─────────────────┐         ┌──────────────┐         ┌────────────────────────────┐
│ request.pdhc.se │         │ sso.pdhc.se  │         │ provider.pdhc              │
│                 │         │              │         │                            │
│ Request         │◄──auth──│ SSO Key      │──auth──▶│ Instance: "Lab Karolinska" │
│ Registry        │         │ Store        │         │ GUID: abc-123              │
│                 │         └──────────────┘         │ Port: 9070                 │
│ Stores JSON     │                                  ├────────────────────────────┤
│ careplans       │────pull──────────────────────────▶│ Instance: "Radiology SU"   │
│                 │                                  │ GUID: def-456              │
│                 │                                  │ Port: 9074                 │
└─────────────────┘                                  └────────────────────────────┘
```

**Pull model**: each provider portal instance periodically polls `request.pdhc.se` for new/updated requests matching its provider GUID. No inbound connections required — the portal can run behind firewalls.

---

## 3) Instance Identity Configuration

Each instance is configured via `.env`. The codebase is identical; only the environment differs.

### New .env variables

```env
# --- Instance identity (one provider per instance) ---
PROVIDER_GUID=<guid-assigned-to-this-provider>
PROVIDER_NAME=<human-readable-provider-name>

# --- Upstream request service ---
REQUEST_SERVICE_URL=https://request.pdhc.se/api/v1
SSO_API_KEY=<api-key-issued-by-sso.pdhc.se-for-this-provider>

# --- Sync settings ---
SYNC_INTERVAL_SECONDS=60
SYNC_ENABLED=true
```

### Multi-instance deployment

Each instance runs as a separate Docker stack with its own database, ports, and `.env`:

| Instance            | PROVIDER_GUID | Ports       | Database                  |
|---------------------|---------------|-------------|---------------------------|
| Lab Karolinska      | abc-123...    | 9070, 9071  | provider_portal_lab_db    |
| Radiology SU        | def-456...    | 9074, 9075  | provider_portal_rad_db    |
| Cardiology Centrum  | ghi-789...    | 9078, 9079  | provider_portal_card_db   |

This gives full isolation — no cross-provider data leaks, independent scaling, independent maintenance windows.

---

## 4) New Database Model: InboundRequest

The raw data from `request.pdhc.se` is stored separately from the working `ProviderTask`. This preserves the original request as received and allows re-processing.

### Table: `inbound_requests`

| Column            | Type          | Notes                                          |
|-------------------|---------------|-------------------------------------------------|
| id                | Integer PK    | Internal sequence                               |
| guid              | String(36)    | Local GUID, unique                              |
| request_guid      | String(36)    | GUID from request.pdhc.se (immutable, unique)   |
| provider_guid     | String(36)    | Must match instance PROVIDER_GUID               |
| receipt_token     | String(255)   | Token from request service, links to ProviderTask|
| careplan_json     | JSON          | Full JSON careplan as received                  |
| status            | String(50)    | new → synced → acknowledged → completed         |
| source_url        | String(512)   | The endpoint this was fetched from              |
| checksum          | String(64)    | SHA-256 of careplan_json for change detection   |
| received_at       | DateTime(tz)  | When first downloaded                           |
| last_synced_at    | DateTime(tz)  | When last checked/updated from upstream         |
| created_at        | DateTime(tz)  | Row creation                                    |

### Table: `sync_state`

Tracks the sync cursor so we only fetch new/changed data.

| Column            | Type          | Notes                                          |
|-------------------|---------------|-------------------------------------------------|
| id                | Integer PK    |                                                 |
| provider_guid     | String(36)    | This instance's provider                        |
| last_sync_at      | DateTime(tz)  | Timestamp of last successful sync               |
| last_sync_cursor  | String(255)   | Opaque cursor if the upstream API supports it   |
| requests_synced   | Integer       | Running count of synced requests                |
| last_error        | Text          | Last sync error message (null if OK)            |
| updated_at        | DateTime(tz)  |                                                 |

### Relationship to existing models

```
InboundRequest (raw from request.pdhc.se)
    │
    ├──creates──▶ ProviderTask (working copy for acknowledge/report flow)
    │
    └──stores───▶ CarePlanCache (careplan details for guided response)
```

The `InboundRequest.receipt_token` links to `ProviderTask.receipt_token`.

---

## 5) New Service: RequestSubscriptionService

### Responsibilities

1. Authenticate to `request.pdhc.se` using SSO-issued key
2. Poll for requests matching this instance's `PROVIDER_GUID`
3. Detect new/changed requests (via checksum comparison)
4. Store raw data in `inbound_requests`
5. Create/update `ProviderTask` records from inbound data
6. Cache careplan JSON in `careplan_cache`
7. Log all sync activity in `task_audit_log`
8. Track sync state (cursor, errors, counts)

### Sync flow (detail)

```
1. Read SYNC_ENABLED from config. If false, skip.

2. Read last_sync_at from sync_state table.

3. Call upstream:
   GET {REQUEST_SERVICE_URL}/requests
   Headers:
     X-API-Key: {SSO_API_KEY}
   Query params:
     provider_guid={PROVIDER_GUID}
     since={last_sync_at}          (ISO-8601, if supported by upstream)
     status=active                 (if supported)

4. For each request in the response:

   a. Compute checksum = SHA-256(careplan_json)

   b. Look up InboundRequest by request_guid:
      - If not found → INSERT (status='new')
      - If found and checksum unchanged → skip (already synced)
      - If found and checksum changed → UPDATE careplan_json, last_synced_at

   c. Upsert ProviderTask:
      - receipt_token = from upstream response
      - provider_guid = PROVIDER_GUID
      - patient_name, careplan_title, etc. = extracted from careplan_json
      - status = 'dispatched' (if new) or preserve current status (if updated)

   d. Upsert CarePlanCache:
      - receipt_token = same
      - careplan_json = full careplan from upstream

   e. Insert TaskAuditLog entry:
      - action = 'sync'
      - payload_snapshot = { request_guid, checksum, is_new: bool }

5. Update sync_state:
   - last_sync_at = now
   - requests_synced += new_count
   - last_error = null

6. On error:
   - Log error
   - Update sync_state.last_error
   - Do NOT clear previous data (local cache is not source of truth, but
     must not lose what it has)
   - Retry on next interval
```

### Sync scheduling

Two options (both implemented, operator chooses):

**Option A — Background thread** (default for development):
- A daemon thread inside the Flask app runs the sync loop
- Controlled by `SYNC_INTERVAL_SECONDS` and `SYNC_ENABLED`
- Simple, no extra infrastructure

**Option B — External cron/systemd timer** (recommended for production):
- A CLI command: `flask sync run`
- Called by cron or systemd timer
- Better observability, no thread management
- Example cron: `* * * * * cd /path/to/provider_portal && venv/bin/flask sync run >> logs/sync.log 2>&1`

Both options use the same `RequestSubscriptionService` — only the trigger differs.

---

## 6) Assumed Upstream API Contract

Since the exact `request.pdhc.se` API is not yet confirmed, this is the assumed response format. The sync client will be built with a mapping layer so adjustments are isolated.

### ✅ IMPLEMENTED: GET {REQUEST_SERVICE_URL}/requests

> **Implementation status:** Fully implemented in `request.pdhc` — see `gateway/app/api/requests.py` and `gateway/app/services/request_feed_service.py`.

**Request:**
```
GET /api/v1/requests?provider_guid={guid}&since={iso-datetime}&cursor={opaque}&status={status}&_count={limit}
X-API-Key: {sso-key}
```

Additional query parameters beyond the original assumption:
- `cursor` — opaque cursor (internal dispatch_request.id) for pagination
- `status` — filter by dispatch status
- `_count` — max results per page (default 100, max 500)

**Confirmed response format** (matches assumed format):
```json
{
  "requests": [
    {
      "request_guid": "uuid",
      "receipt_token": "string",
      "provider_guid": "uuid",
      "provider_name": "string",
      "status": "submitted|active|...",
      "provider_status": "acknowledged|in_progress|completed|rejected|null",
      "created_at": "ISO-8601",
      "updated_at": "ISO-8601",
      "careplan": {
        "careplan_guid": "uuid",
        "title": "string",
        "patient": {
          "patient_guid": "uuid",
          "name": "string"
        },
        "activities": [
          {
            "activity_guid": "uuid",
            "title": "string",
            "transactions": [
              {
                "transaction_guid": "uuid",
                "concept_guid": "uuid",
                "concept_name": "string",
                "response_type": "text",
                "valueset_values": [],
                "unit": "string|null",
                "required": true
              }
            ]
          }
        ],
        "dispatch_metadata": {
          "dispatched_at": "ISO-8601",
          "due_at": "ISO-8601|null",
          "priority": "routine|urgent",
          "notes": "string|null"
        }
      }
    }
  ],
  "cursor": "string|null",
  "has_more": false
}
```

**Differences from original assumption:**
- Added `provider_status` field (tracks provider-side status: acknowledged/in_progress/completed/rejected)
- Added `dispatch_metadata.notes` field (dispatch notes from the original request)
- `status` reflects the dispatch status, not a generic "active" value
- Careplan data is enriched live from `plan.pdhc.se` via `careplan_service.get_careplan()`

### ✅ IMPLEMENTED: GET /api/v1/requests/{request_guid}

> Single-request endpoint not in original assumption — added for convenience.

```
GET /api/v1/requests/{request_guid}
X-API-Key: {sso-key}
```

Returns the same entry structure as the list endpoint, or 404.

### ✅ IMPLEMENTED: PUT /api/v1/requests/{request_guid}/status

> Provider status callback — allows providers to report status back to request.pdhc.

```
PUT /api/v1/requests/{request_guid}/status
X-API-Key: {sso-key}
Content-Type: application/json

{
  "provider_guid": "uuid",
  "status": "acknowledged|in_progress|completed|rejected"
}
```

Response (200):
```json
{
  "request_guid": "uuid",
  "provider_guid": "uuid",
  "provider_status": "acknowledged",
  "provider_status_updated_at": "ISO-8601",
  "message": "Status updated to acknowledged"
}
```

Error codes: 400 (invalid status/missing fields), 403 (provider_guid mismatch), 404 (request not found).

### Mapping layer

The assumed `RequestMapper` class is for the **provider.pdhc consumer side** (not yet built). The upstream response from `request.pdhc` now matches the confirmed format above, so the mapper implementation can target this exact structure.

```python
class RequestMapper:
    @staticmethod
    def to_inbound_request(upstream_data) -> dict:
        # Maps upstream JSON to InboundRequest fields

    @staticmethod
    def to_provider_task(upstream_data) -> dict:
        # Maps upstream JSON to ProviderTask fields

    @staticmethod
    def to_careplan_cache(upstream_data) -> dict:
        # Maps upstream careplan to CarePlanCache fields
```

---

## 7) Authentication Flow with SSO

> **Implementation status (request.pdhc side):** X-API-Key authentication is fully implemented. See `gateway/app/middleware/auth_middleware.py`.

```
1. Instance starts with SSO_API_KEY in .env
   (key issued by operator via sso.pdhc.se for this provider)

2. On each sync request to request.pdhc.se:
   - Send X-API-Key: {SSO_API_KEY}
   - request.pdhc.se validates against sso.pdhc.se

3. If key is rejected (401/403):
   - Log error with clear message
   - Set sync_state.last_error
   - Do not retry until next interval (avoid flooding)

4. Future: if sso.pdhc.se moves to OAuth/Bearer tokens:
   - Add token exchange step before sync
   - SSO_API_KEY becomes client_secret
   - Sync sends Bearer token instead
   - Change isolated to auth layer only
```

**How it works on the request.pdhc side:**
- The `requires_auth` middleware checks for `X-API-Key` header first, before session auth
- In production: the key is validated against `sso.pdhc.se` via `GET /api/auth/me` with Bearer token
- In dev (`AUTH_DISABLED=true`): any X-API-Key is accepted with a mock access blob
- Service accounts (X-API-Key) are given `read_write` access level
- All three request-feed endpoints (`GET /requests`, `GET /requests/<guid>`, `PUT /requests/<guid>/status`) are behind `@requires_auth`

---

## 8) Impact on Existing Code

> **Note:** This section describes changes needed in **provider.pdhc** (the consumer). The changes below to **request.pdhc** (the upstream) have already been completed:
>
> **Files modified in request.pdhc:**
> | File | Change |
> |------|--------|
> | `gateway/app/models/dispatch_models.py` | Added `provider_status`, `provider_status_updated_at` fields; added index on `provider_guid` |
> | `gateway/app/middleware/auth_middleware.py` | Added X-API-Key authentication support |
> | `gateway/app/services/auth_service.py` | Added `validate_api_key()` for SSO key validation |
> | `gateway/app/api/capability.py` | Added request-feed and request-status-update to CapabilityStatement |
>
> **New files in request.pdhc:**
> | File | Purpose |
> |------|---------|
> | `gateway/app/api/requests.py` | Three endpoints: list feed, single request, status callback |
> | `gateway/app/services/request_feed_service.py` | Core service: query, enrich, paginate, status update |
> | `gateway/tests/test_request_feed.py` | 18 tests covering all endpoints and edge cases |
>
> **Migration:** `837810485062_add_provider_status_fields_and_provider_.py` — adds `provider_status`, `provider_status_updated_at` columns and `ix_dispatch_requests_provider_guid` index.

### Config changes (provider.pdhc — not yet implemented)
- Add new env vars to `config.py`: `PROVIDER_GUID`, `PROVIDER_NAME`, `REQUEST_SERVICE_URL`, `SSO_API_KEY`, `SYNC_INTERVAL_SECONDS`, `SYNC_ENABLED`

### New files
| File | Purpose |
|------|---------|
| `app/models/inbound_request.py` | InboundRequest model |
| `app/models/sync_state.py` | SyncState model |
| `app/services/subscription.py` | RequestSubscriptionService |
| `app/services/request_mapper.py` | Upstream-to-local data mapper |
| `app/services/sync_scheduler.py` | Background thread / CLI trigger |
| `app/cli.py` | Flask CLI commands (`flask sync run`, `flask sync status`) |
| `tests/test_subscription.py` | Subscription service tests |
| `tests/test_mapper.py` | Mapper tests |

### Modified files
| File | Change |
|------|--------|
| `app/models/__init__.py` | Add InboundRequest, SyncState imports |
| `app/__init__.py` | Register CLI commands, optionally start sync thread |
| `config.py` | Add subscription config vars |
| `.env` | Add instance identity and upstream vars |
| `templates/dashboard.html` | Show sync status (last sync, error, count) |

### No changes to
- Existing API endpoints (they continue to work on ProviderTask)
- Existing services (acknowledge, report, receipt — unchanged)
- Existing tests (all 59 remain valid)

---

## 9) Dashboard Sync Status

The web dashboard will show sync status:

```
┌─────────────────────────────────┐
│ Sync Status                     │
│ Provider: Lab Karolinska        │
│ GUID: abc-123-...               │
│ Last sync: 2026-03-20 14:30 UTC │
│ Requests synced: 47             │
│ Status: ● OK                    │
│ [Sync Now]                      │
└─────────────────────────────────┘
```

If sync has errors:
```
│ Status: ● Error                 │
│ Last error: 401 Unauthorized    │
│ Check SSO key configuration     │
```

---

## 10) CLI Commands

```bash
# Manual sync (one-shot)
flask sync run

# Check sync status
flask sync status

# Reset sync cursor (re-sync all)
flask sync reset
```

---

## 11) Security Considerations

1. **SSO key storage**: stored in `.env`, never committed to git. Same bcrypt-at-rest rules as local API keys do not apply here — this is an outbound credential, stored as plaintext in `.env` (standard for service-to-service keys).

2. **Provider isolation**: each instance only requests data for its own `PROVIDER_GUID`. The upstream service enforces this server-side. The local portal double-checks `provider_guid` on every inbound record.

3. **Data integrity**: checksums prevent processing stale/duplicate data. Raw `careplan_json` is preserved in `inbound_requests` for audit. The working copy in `ProviderTask` can diverge (acknowledge/complete) without affecting the raw record.

4. **Network failure**: sync failures are logged but do not corrupt local state. The portal continues to operate on cached data. Queue reconciliation resumes on next successful sync.

---

## 12) Testing Strategy

| Test | What it verifies |
|------|------------------|
| test_sync_new_requests | New requests create InboundRequest + ProviderTask + CarePlanCache |
| test_sync_unchanged_skipped | Same checksum → no update |
| test_sync_updated_request | Changed checksum → careplan_json updated, ProviderTask status preserved |
| test_sync_auth_failure | 401 from upstream → error logged, no data lost |
| test_sync_network_error | Connection error → error logged, retry on next interval |
| test_sync_duplicate_guid | Same request_guid twice → upsert, not duplicate |
| test_mapper_upstream_format | Mapper correctly extracts fields from upstream JSON |
| test_mapper_missing_fields | Mapper handles missing optional fields gracefully |
| test_sync_state_tracking | Cursor/timestamp updated after successful sync |
| test_sync_audit_trail | Each sync creates audit log entries |
| test_provider_guid_mismatch | Request with wrong provider_guid → rejected |
| test_cli_sync_run | `flask sync run` executes one sync cycle |
| test_cli_sync_status | `flask sync status` shows current state |

---

## 13) Open Questions for Review — Answers

1. **Upstream API contract**: ✅ **Confirmed.** The assumed response format in Section 6 is implemented as-is, with minor additions (`provider_status`, `dispatch_metadata.notes`). Endpoint path: `GET /api/v1/requests`. See Section 6 for full confirmed format.

2. **Auth mechanism**: ✅ **Confirmed.** `request.pdhc.se` accepts `X-API-Key` directly in the request header. The middleware validates the key against `sso.pdhc.se` (in production) or accepts any key (in dev with `AUTH_DISABLED=true`). No token exchange required — direct key validation.

3. **Sync direction**: Currently **pull only** (portal polls). No webhook/push infrastructure is implemented in `request.pdhc`. This can be added later if needed — the `dispatch_service` could trigger an outbound webhook on new dispatches.

4. **Cursor/pagination**: ✅ **Both supported.** The upstream API supports:
   - `since` — ISO-8601 timestamp filtering (filters on `updated_at`)
   - `cursor` — opaque cursor-based pagination (internal dispatch_request.id)
   - `_count` — page size (default 100, max 500)
   - `has_more` + `cursor` in response for next-page fetching

5. **Request lifecycle**: ✅ **Implemented.** `PUT /api/v1/requests/{request_guid}/status` allows providers to push status back. Valid statuses: `acknowledged`, `in_progress`, `completed`, `rejected`. The endpoint validates `provider_guid` ownership (403 on mismatch). Status changes are audit-logged.

6. **Port allocation for multiple instances**: Still open — this is a deployment decision for `provider.pdhc`, not `request.pdhc`.

---

## 14) SSO Requirements — For sso.pdhc.se Team

> **Action required from the SSO team.** The provider portal subscription system is now implemented on both the `provider.pdhc` (consumer) and `request.pdhc` (upstream) sides. For it to work in production, `sso.pdhc.se` must support the following.

### What the provider portal does

Each provider portal instance is configured with an `SSO_API_KEY` in its `.env`. On every sync request to `request.pdhc.se`, the portal sends:

```
GET https://request.pdhc.se/api/v1/requests?provider_guid={PROVIDER_GUID}
X-API-Key: {SSO_API_KEY}
```

It also pushes status updates:

```
PUT https://request.pdhc.se/api/v1/requests/{request_guid}/status
X-API-Key: {SSO_API_KEY}
Content-Type: application/json

{"provider_guid": "uuid", "status": "acknowledged|completed"}
```

### What request.pdhc.se does with the key

The `request.pdhc` auth middleware (`gateway/app/middleware/auth_middleware.py`) validates the `X-API-Key` by calling:

```
GET https://sso.pdhc.se/api/auth/me
Authorization: Bearer {the-api-key-value}
```

It expects `sso.pdhc.se` to return an identity response. In the current dev setup (`AUTH_DISABLED=true`), any key is accepted with a mock identity.

### What SSO needs to provide

1. **Service-account API keys** — one per provider portal instance. Each key must be long-lived (or renewable) since it runs unattended in a background sync loop.

2. **Key-to-provider binding** — the SSO identity response for a service-account key should include the `provider_guid` so that `request.pdhc.se` can verify that the caller is authorized to access requests for that specific provider. Suggested response format:

   ```json
   {
     "authenticated": true,
     "account_type": "service",
     "provider_guid": "abc-123-...",
     "provider_name": "Lab Karolinska",
     "access_level": "read_write",
     "scopes": ["request:read", "request:status_write"]
   }
   ```

3. **Validation endpoint** — `GET /api/auth/me` (or equivalent) must accept an API key as `Authorization: Bearer {key}` and return the identity above. This is the endpoint `request.pdhc.se` calls on every inbound request.

4. **Error responses** — if the key is invalid, expired, or revoked, return `401` with a clear error. The provider portal logs these and shows them on the dashboard so operators can diagnose key issues.

### Provisioning flow (proposed)

```
1. Operator requests a new provider portal instance
2. SSO admin creates a service-account key in sso.pdhc.se
   → binds it to PROVIDER_GUID + PROVIDER_NAME
   → sets access_level = read_write
3. SSO admin gives the key to the operator
4. Operator sets SSO_API_KEY={key} in the instance's .env
5. Provider portal syncs, request.pdhc validates key via SSO, data flows
```

### Multi-instance summary

| Instance           | PROVIDER_GUID | SSO Key (unique per instance) |
|--------------------|---------------|-------------------------------|
| Lab Karolinska     | abc-123...    | key-lab-karo-...              |
| Radiology SU       | def-456...    | key-rad-su-...                |
| Cardiology Centrum | ghi-789...    | key-card-cen-...              |

Each key must only grant access to requests matching its bound `provider_guid`.

---

## 15) Implementation Sequence

If approved:

1. Add new models (`InboundRequest`, `SyncState`) and migration
2. Build `RequestMapper` with assumed upstream format
3. Build `RequestSubscriptionService` with sync logic
4. Add CLI commands (`flask sync run/status/reset`)
5. Add background sync thread option
6. Update dashboard with sync status
7. Write all tests from Section 12
8. Update `.env`, `config.py`, `requirements.txt` (add `requests` library)
9. Update `progress.md`, `changed_files.md`
