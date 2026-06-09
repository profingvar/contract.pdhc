## Changed files

List all edited files (full path), newest first.

- `app/docker-compose.yml` — Ticket #72: pinned host interface `127.0.0.1:` on 9020/9021/9022 so DB/api/web ports are localhost-only (were binding to `0.0.0.0`, exposed on LAN via colima ssh-mux forwarder → CLAUDE.md §3 violation). Deployed + containers recreated on macmini 2026-04-16; `curl http://192.168.1.154:902x/` now refuses, `https://contract.pdhc.se/health` continues 200.
- `app/web/dist/index.html` — full SPA rebuild (dashboard, contract CRUD, user mgmt, routing)
- `app/backend/app/main.py` — added CORS, user update/deactivate/reset-password endpoints
- `app/backend/requirements.txt` — added flask-cors
- `app/docker-compose.yml` — added CORS_ORIGINS env var
- `/Users/martiningvar/T7_sidewinder/Contracts/app/docs/admin-manual.md`
- `/Users/martiningvar/T7_sidewinder/Contracts/app/web/dist/index.html`
- `/Users/martiningvar/T7_sidewinder/Contracts/app/docs/operator-manual.md`
- `/Users/martiningvar/T7_sidewinder/Contracts/start.sh`
- `/Users/martiningvar/T7_sidewinder/Contracts/app/docs/operator-manual.md`
- `/Users/martiningvar/T7_sidewinder/Contracts/start.sh`
- `/Users/martiningvar/T7_sidewinder/Contracts/app/docs/operator-manual.md`
- `/Users/martiningvar/T7_sidewinder/Contracts/app/docker-compose.yml`
- `/Users/martiningvar/T7_sidewinder/Contracts/app/docker-compose.yml`
- `/Users/martiningvar/T7_sidewinder/Contracts/app/backend/app/main.py`
- `/Users/martiningvar/T7_sidewinder/Contracts/readme.md`
- `/Users/martiningvar/T7_sidewinder/Contracts/progress.md`
- `/Users/martiningvar/T7_sidewinder/Contracts/app/docs/architecture.md`
- `/Users/martiningvar/T7_sidewinder/Contracts/app/docs/api.md`
- `/Users/martiningvar/T7_sidewinder/Contracts/app/docs/admin-manual.md`
- `/Users/martiningvar/T7_sidewinder/Contracts/app/docs/operator-manual.md`
- `/Users/martiningvar/T7_sidewinder/Contracts/app/backend/tests/conftest.py`
- `/Users/martiningvar/T7_sidewinder/Contracts/app/backend/app/__init__.py`
- `/Users/martiningvar/T7_sidewinder/Contracts/app/backend/tests/test_health_and_contracts.py`
- `/Users/martiningvar/T7_sidewinder/Contracts/app/backend/app/main.py`
- `/Users/martiningvar/T7_sidewinder/Contracts/app/backend/app/fhir.py`
- `/Users/martiningvar/T7_sidewinder/Contracts/app/backend/app/security.py`
- `/Users/martiningvar/T7_sidewinder/Contracts/app/backend/app/models.py`
- `/Users/martiningvar/T7_sidewinder/Contracts/app/backend/app/db.py`
- `/Users/martiningvar/T7_sidewinder/Contracts/app/backend/app/config.py`
- `/Users/martiningvar/T7_sidewinder/Contracts/app/backend/Dockerfile`
- `/Users/martiningvar/T7_sidewinder/Contracts/app/backend/requirements.txt`
- `/Users/martiningvar/T7_sidewinder/Contracts/app/docker-compose.yml`
- `/Users/martiningvar/T7_sidewinder/Contracts/app/scripts/test_endpoints.py`
- `/Users/martiningvar/T7_sidewinder/Contracts/app/web/dist/index.html`
- `/Users/martiningvar/T7_sidewinder/Contracts/start.sh`
- `/Users/martiningvar/T7_sidewinder/Contracts/readme.md`
- `/Users/martiningvar/T7_sidewinder/Contracts/progress.md`
- `/Users/martiningvar/T7_sidewinder/Contracts/readme.md`
- `/Users/martiningvar/T7_sidewinder/Contracts/progress.md`
- `/Users/martiningvar/T7_sidewinder/Contracts/readme.md`
- `/Users/martiningvar/T7_sidewinder/Contracts/progress.md`
- `/Users/martiningvar/T7_sidewinder/Contracts/progress.md`
- `/Users/martiningvar/T7_sidewinder/Contracts/changed_files.md`
- `/Users/martiningvar/T7_sidewinder/Contracts/progress.md`
- `/Users/martiningvar/T7_sidewinder/Contracts/readme.md`
- `app/backend/app/config.py` — 2026-04-05 — added REQUEST_BASE_URL for auto-provision
- `app/backend/app/main.py` — 2026-04-05 — added _auto_provision_pat hook on contract create/update

## 2026-04-15 — Health endpoint standardisation (ticket #40)

| File | Change |
|------|--------|
| `app/backend/app/main.py` | `/health` upgraded to CLAUDE.md §10 shape: returns `{status, database, service}` with HTTP 200/503 based on a live `SELECT 1` via `db_session()`. Adds `Access-Control-Allow-Origin: *` and `Cache-Control: no-store`. |
| `miserver:contract-api-1:/srv/app/main.py` | Replaced via `docker cp` + `docker restart contract-api-1`. Pre-change file backed up at `/tmp/contract_main_backup_<stamp>.py` on server. |
| `app/backend/app/main.py` | Ticket #55 — `sso_callback()` now refuses to mint a local JWT when the freshly validated blob has `must_change_password=True`; instead returns `redirect({SSO_BASE_URL}/change-password)`. Once SSO clears the flag, a second SSO login lands here with it off and minting proceeds. Deployed to `/usr/local/www/contract.pdhc/app/backend/app/main.py`; backup `.bak-2026-04-15T18-51-19Z` on server. Rebuilt via `docker-compose up -d --build api` in `/usr/local/www/contract.pdhc/app`; `https://contract.pdhc.se/health` returns 200. |
| `app/web/dist/index.html` | Ticket #55 — central `api()` wrapper's 401 handler previously cleared local auth and navigated to `#/login` (a sign-in button page). Now clears auth, shows a "Session expired — redirecting to sign-in" toast, and `window.location.assign(API + "/api/v1/auth/login")` immediately. So when SSO flushes a user's session (SSO #44), the next /api/ call in the SPA auto-bounces through the SSO handshake instead of stranding the user on a disabled page. Deployed via rebuild of `contract-web` container (nginx image COPIES `dist/` at build time). Verified over the wire: `curl -s https://contract.pdhc.se/ | grep redirecting` hits. |
| `app/backend/app/main.py` | Ticket #70 / CLAUDE.md §10: tightened CORS on `/health` from `Access-Control-Allow-Origin: *` to `https://www.pdhc.se`, added `Access-Control-Allow-Methods: GET` and `Vary: Origin`. Contract runs via docker-compose with a baked image; `start.sh` rebuilds via `docker compose up -d --build` so the change takes effect on restart. Verified: all three headers present, body `{"database":"connected","service":"contract.pdhc","status":"ok"}`, image's `/srv/app/main.py` contains the new code. Server backup at `/tmp/contract_main.py.bak.20260416T185418Z`. Side note, not fixed this session: host-side ports `*:9020/21/22` are LAN-exposed via colima ssh-mux (CLAUDE.md §3 concern — compose port map lacks `127.0.0.1:` prefix). |

## 2026-04-30 — Contract.status enum reduction + governance extensions

| File | Change |
|------|--------|
| `app/web/dist/index.html` | `STATUSES` array trimmed from 14 FHIR R5 codes to a 4-entry `STATUS_OPTIONS` map: `negotiable` (UI: "Under consideration"), `executed` (UI: "Active" — only state that gates request fulfilment), `terminated` ("Expired"), `revoked` ("Revoked"). Status badge map / detail-view tooltip / edit-form `<select>` all updated. Added a "Compliance & provider data" form block: 3 checkboxes (Legally OK / PUB exists / Legal Provider) + 1 radio (Provider data status: ok/deficient/unclear). Persisted as FHIR `Contract.extension[]` with canonical URLs `https://contract.pdhc.se/StructureDefinition/{legally-ok,pub-exists,legal-provider,provider-data-status}` so the JSON travels portably across FHIR servers. Deployed via `docker cp` to `contract-web-1:/usr/share/nginx/html/index.html`. |
| `app/docs/admin-manual.md` | New §3.1.1 Status (FHIR-code-to-UI-label mapping), §3.1.2 Compliance + provider-data extensions table. |
| `app/docs/api.md` | Example payloads use `executed` for active or `negotiable` for new contracts. Status table + extensions table added. Status gate paragraph updated: only `executed` accepts submissions. |
| `app/docs/architecture.md` | §4.2 expanded to document the four extension URLs + FHIR-portability rationale + the constrained 4-code status set. |
| miserver bind-mount via `colima ssh -- sudo cp` | All 4 manuals + 3 runbooks copied into `/usr/local/www/contract.pdhc/app/docs/` (VM-side path — host's `/usr/local/www` is not auto-mounted into Colima). All `https://contract.pdhc.se/docs/*.md` URLs now serve the updated content. |

## 2026-05-27T08:20:50Z — fix: contract list not refreshing after delete (web)
- app/web/dist/index.html (deleteContract: force render when hash already #/contracts)

## 2026-06-09T09-XX-XXZ — feat #231: contract emits PatientConsent on patient-signer (Lag 2022:913 §5)

- `app/backend/app/consent_emitter.py` (NEW): pure FHIR parsers
  (_extract_patient_signers / _extract_provider_org_guids /
  _extract_expires_at), idempotent IPS HTTP layer (_list_active_consents,
  _post_grant, _post_revoke), and the lifecycle helpers
  `emit_patient_consents(contract_resource)` and
  `revoke_patient_consents(contract_resource, reason=...)`.
- `app/backend/app/config.py`: new `IPS_BASE_URL` + `IPS_API_KEY` env
  knobs. Empty IPS_BASE_URL → emitter is a noop (local dev safety).
- `app/backend/app/main.py`:
    - Imports the emitter helpers.
    - New `_emit_consents_for_lifecycle(contract_resource)` dispatcher
      that emits grants on active statuses
      (executed/executable/offered/renewed) and revokes on end-of-life
      statuses (cancelled/terminated/revoked).
    - POST /fhir/Contract and PUT /fhir/Contract/<guid> call the
      dispatcher after the DB commit.
    - DELETE /fhir/Contract/<guid> snapshots the resource before
      deletion and calls `revoke_patient_consents` with
      `reason='contract_deleted:<guid>'`.
    - Best-effort: emitter exceptions are logged but never propagate
      to the contract write — a flaky IPS must not break contract CRUD.
- `app/backend/tests/test_consent_emitter.py` (NEW, 20 tests):
    - Parsing (7): patient signers single / list form / dedup /
      ignore-non-patients; provider orgs; expires_at present / missing.
    - Service (8): active contract posts grant + verifies body shape;
      multi-signer x multi-grantee cross-product; idempotent against
      existing consent; 409 from IPS treated as success; inactive
      status noop; no patient signer noop; no provider org noop;
      revoke matching contract_guid (and only that one); no match noop.
    - Route integration (4): POST executed → emits; PUT to cancelled
      → revokes; DELETE → revokes; re-PUT same contract is idempotent.
- ips.pdhc/gateway/app/models/patient_consent.py:
    - Added `'contract'` to `CONSENT_GRANTED_VIA` so the auto-emit
      channel is a distinct enum value (was: portal / in_person /
      paper / phone / other).

Tests: 70/70 contract.pdhc green (was 50/50). 229/229 ips.pdhc still
green after the enum addition.
