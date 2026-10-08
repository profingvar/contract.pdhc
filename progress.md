## Progress tracking

This file tracks progress against `readme.md` step numbering (e.g. 1.a.1, 1.a.2, ...).

### 1.a.1
- Status: completed
- Result: `readme.md`, `progress.md`, `newtask.txt`, and `changed_files.md` created in repo root.

### 1.a.2
- Status: completed
- Result: created `./app/` folder.

### 1.a.3
- Status: completed
- Result: port plan captured in `readme.md` (9020 Postgres, 9021 API, 9022 frontend).

### 1.a.3 (verification)
- Status: completed
- Result: verified all project-wide port references are consistent (db 9020, api 9021, web 9022; only 9020–9030 used; start script kills 9020–9030 + 9040–9043).

### 1.a.4 (stability)
- Status: completed
- Result: added Postgres healthcheck + API DB-wait retry to prevent API crash during DB startup.

### 1.a.6 (local DB auth)
- Status: completed
- Result: adjusted local Postgres container auth to allow non-SSL container-network connections (dev-only), resolving pg_hba “no encryption” startup failure.

### 1.a.9.1
- Status: completed
- Result: added frontend UX/UI design section to `readme.md` (public reader + admin flows).

### 1.a.10.1
- Status: completed
- Result: added documentation/manuals plan to `readme.md` (operator, admin, API, architecture, runbooks).

### 1.a.4 (update)
- Status: completed
- Result: `web` container is mandatory on 9022 (separate from API).

### 1.a.14 (update)
- Status: completed
- Result: web-instance target set to `contract.pdhc.se`.

### 1.a.10.1 (docs skeleton)
- Status: completed
- Result: created initial manuals under `app/docs/` (operator, admin, API, architecture).

### 1.a.7 (user management endpoints)
- Status: completed
- Result: added PUT /admin/users/<guid> (role change, activate/deactivate) and POST /admin/users/<guid>/reset-password.

### 1.a.9.1 (frontend SPA)
- Status: completed
- Result: replaced placeholder index.html with full SPA — hash-based routing, sidebar nav, dashboard with stats, contract list with search, contract create/edit/delete, user management (create, role toggle, activate/deactivate, password reset), public read-only browsing, login/logout, toast notifications, modals.

### CORS support
- Status: completed
- Result: added flask-cors to backend + CORS_ORIGINS env var in docker-compose for SPA→API communication.

### repo_css.md compliance (PDHC Design System)
- Status: completed
- Result: rewrote entire frontend to follow repo_css.md. Changes: light theme (--pdhc-bg/#f8fafc, white surfaces), navy/teal/slate colour tokens, Inter + JetBrains Mono fonts via Google Fonts, Lucide icons via CDN, fixed top navbar (navy bg, teal active underline), proper card/button/form/table/badge component patterns, WCAG focus indicators, aria-live toasts, aria-modal dialogs, accessible labels on all inputs, mobile hamburger menu, prefers-reduced-motion support, status badges with text+colour (not colour alone).

### 1.a.10 (CapabilityStatement — full R5)
- Status: completed
- Result: replaced 4-field stub at `/fhir/metadata` with full FHIR R5 CapabilityStatement. Includes: id, url, version, name, title, status(active), date, publisher, description, kind(instance), software, implementation, fhirVersion(5.0.0), format, rest[0] with server mode, security (CORS + JWT Bearer), and Contract resource with 5 interactions (read, search-type, create, update, delete). Content-Type set to application/fhir+json.

### 1.a.10 (endpoint test coverage)
- Status: completed
- Result: expanded `app/scripts/test_endpoints.py` from 3 to ~18 checks covering all 11 endpoints: health, CapabilityStatement structure validation, auth success/failure, unauthorized access, full Contract CRUD cycle (create→read→update→list→delete→404), and user management cycle (create→list→update role→reset password→deactivate). Expanded pytest suite to 15 test functions covering auth, CRUD, user mgmt, validation edge cases.

### 1.a.10.1 (documentation — full content)
- Status: completed
- Result: fleshed out all 4 manuals to production quality following PDHC markdown layout standard (numbered H2/H3, lead paragraphs, bold labels, backtick identifiers). operator-manual.md (~120 lines): prerequisites, start/stop, backup/restore, common failures, test running. admin-manual.md (~100 lines): auth flow, user lifecycle, contract CRUD, rate limiting, credential rotation reference. api.md (~180 lines): full endpoint reference with request/response JSON examples, error model, status codes, FHIR conformance. architecture.md (~120 lines): container topology diagram, port map, data flows, DB schema, security posture.

### 1.a.10.1 (runbooks)
- Status: completed
- Result: created `app/docs/runbooks/` with 3 runbooks: credential-rotation.md (JWT key, admin password, DB credentials, emergency rotation), incident-response.md (triage checklist, common incidents, recovery verification), upgrade-procedure.md (pre-upgrade, upgrade steps, post-upgrade verification, rollback, dependency updates).

### 1.a.9.1 (frontend — Docs + API pages)
- Status: completed
- Result: added #/docs and #/api routes to SPA. Docs page: card grid with 4 manuals + 3 runbooks, each with description and download link. API page: endpoint reference table (method, path, auth, rate limit, description), authentication example, error model with status codes, FHIR conformance summary, link to CapabilityStatement. Nav links (book-open + code icons) visible to all users. Created nginx.conf serving /docs/*.md as downloadable files. Updated docker-compose.yml with nginx config + docs volume mounts.

### 1.a.9 (FHIR validation hardening)
- Status: completed
- Result: expanded `ensure_contract_shape()` in fhir.py: status field now required and validated against FHIR R5 contract-status code set (15 valid codes). Subject references validated as array of objects with reference field matching "ResourceType/id" format. Added 7 validation edge-case tests (requires status, rejects invalid status, accepts valid statuses, subject must be list, bad reference format, valid reference, bad period date).

### 1.a.12 (start.sh — PDHC family aligned)
- Status: completed
- Result: rewrote start.sh to match PDHC family patterns (request.pdhc, plan.pdhc, sso.pdhc). Changes: Colima-first Docker detection with Docker Desktop fallback (30s wait), DB backup before restart with gzip and rotation (keep 10), docker-compose binary detection (hyphenated for Mac Mini server, plugin for dev), health check wait loop (30 attempts), log tailing with graceful Ctrl+C shutdown, OBJC_DISABLE_INITIALIZE_FORK_SAFETY for macOS, detached mode (-d) with log follow, status banner with all URLs.


---

## 2026-09-23 — #599: contract.pdhc enablers for onboard.pdhc OB-7

All four items delivered. 139 tests pass, up from a clean 116 baseline.

**1. Server-side `?party=` and `?status=` on `GET /fhir/Contract`.** Every
consumer previously pulled the whole searchset and filtered `party[]`
client-side. `party` accepts either `Organization/<guid>` or a bare guid and
matches any role. Note `Contract.party.reference` is 0..* — each party entry
holds a LIST of references, which is why matching walks two levels.
Implementation note: `party[]` lives inside the unindexed `fhir_contract` JSON
blob, so filtering is done in Python — but BEFORE paging, or `total` and the
page window would both be computed over the unfiltered set. There is a
regression test for exactly that. The unfiltered path keeps its cheap SQL
count+page and is untouched.

**2. `onboarding_terms` term[].** Was hard-rejected: `ALLOWED_TERM_TYPES` was
`frozenset({"request_scope","return_scope"})`. Three traps had to be handled,
not just the allow-list:
  - `ALLOWED_ASSET_TYPES[term_type]` is a bare dict lookup — adding the type to
    the allow-list alone would have produced a KeyError, not a clean 400.
  - `asset[]` was required non-empty for EVERY term.
  - every `typeReference` had to match the concept-URL regex — but onboarding
    assets reference webhooks and people, not concepts.
So scope terms and onboarding terms now take separate validation paths:
`SCOPE_TERM_TYPES` stays strict (it is a security boundary — what a provider
may be asked for and must return), while `_validate_onboarding_term` checks
structural shape only and leaves asset types open, so a newly agreed field
does not need a contract.pdhc release. Stored verbatim.
Both scope readers now skip it: `get_contract_scope` filters on
`SCOPE_TERM_TYPES`, and `scope_validation.extract_scope_concept_guids` skips
non-scope terms — otherwise plan.pdhc would have been asked to verify a
webhook URL as a concept.

**3. `X-Skip-Auto-Provision: 1` on `POST /fhir/Contract`** (OB-13 decision 1c).
Skips the request.pdhc auto-provision call so onboard.pdhc, which mints the PAT
itself, does not leave a second dangling token whose raw value nobody ever saw.
Only reachable on a route already gated by `@require_role("admin")`.

**4. Accepted signer party types documented** in `signer_resolver`'s module
docstring, with the caveat stated plainly: `Organization/<guid>` is accepted on
SHAPE ALONE and is not verified at all, internal or external. OB-7 can sign as
`Organization/<payer>` and `Organization/<provider>` today with no change — but
a typo in either guid is stored silently and surfaces later as a dangling
reference. Making these resolve via sso `/api/public/organisations` is real
hardening, not a blocker, and is not done here.

**Also:** `party` and `status` are now advertised as `searchParam` in the
CapabilityStatement. The code comment there said "Add entries here when search
parameters are actually supported" — they now are, and Rule 20 requires the CS
to be bidirectionally truthful.

**NOT DEPLOYED.** Local only; contract.pdhc on miserver still runs the previous
code. Colima is down on this laptop (macOS 27), so the containers were not
exercised — tests run on sqlite.

---

## 2026-09-23 — #691: deployed #599 (onboard.pdhc OB-7 enablers)

Deployed and verified live. `contract-api-1` rebuilt, healthy, `/health`
200, no errors. Markers confirmed **inside the container**:
`onboarding_terms` ×3 in `fhir.py`, `_skip_auto_provision` defined and
guarding the provision call in `main.py`.

This unblocks onboard.pdhc's `contract_writer`, which posts the negotiated
FHIR Contract here with `X-Skip-Auto-Provision: 1`.

### Pre-deploy check

Prod was exactly one commit behind (`1f67a8e`) with a **clean** working
tree. All four changed files were verified byte-identical to
`local@1f67a8e` before overwriting — the 15 lines that looked "only on
prod" in a first pass were simply the pre-#599 versions of modified lines,
not server-only edits. The only prod-only file is a stray
`main.py.before_version_edit` backup, left in place.

No alembic/migrations in this service, so nothing to upgrade.

Compose project is pinned `contract`; `docker-compose` (v1 CLI, compose
5.1.4) — `docker compose` v2 is not available on the mini.

Predeploy tar: `~/backups/predeploy/contract.pdhc/app_20260923T173327Z.tar.gz`
Rollback image: `sha256:1286bd5a195dd`

---

## 2026-09-29 — #706: a sibling service can now write a Contract

144 tests pass (+5). Found by onboard.pdhc's first end-to-end call, which
reached `agree` and then failed at the contract write with
`401 Missing Authorization Header`.

### The gap

`POST /fhir/Contract` is `@require_role("admin")`, i.e. `@jwt_required()`,
and the only issuer of a contract.pdhc JWT is `/api/v1/auth/callback` — a
**browser** redirect flow with CSRF state in a session. **There was no
server-to-server path to it at all.**

Meanwhile `require_service_key` and `INTERNAL_SERVICE_KEY` both already
existed here, and the two services already held the same key value —
onboard's `CONTRACT_SERVICE_KEY` and this service's `INTERNAL_SERVICE_KEY`
match. So onboard was sending exactly the right credential at a door that
did not accept it for writes.

> **Correction (2026-09-29).** An earlier version of this entry, and the
> commit message on the change, said `require_service_key` was "applied to
> nothing". That is wrong: it guards
> `GET /internal/contract/<guid>/scope` (main.py:613) and always has. The
> error came from grepping for `_require_service_key` with a leading
> underscore, finding nothing, and concluding the decorator was dead.
>
> The gap was real but narrower than described: there was a service-key path
> for **reads** and none for **writes**.

### The change

`require_role(*roles, allow_service_key=False)`. When a route opts in, a
valid `X-Service-Key` is accepted **as an alternative** to the JWT. Only
`POST /fhir/Contract` opts in.

Deliberately narrow:

- A JWT caller is completely unaffected — the JWT path is unchanged and
  still runs when no key is offered.
- A caller with neither is still refused.
- Routes that did not opt in still refuse the key. Tested.
- An **empty** `INTERNAL_SERVICE_KEY` refuses rather than admitting an empty
  header — the classic `"" == ""` hole. Tested.
- Constant-time comparison.

### Note for whoever reads this next

This is the third time in a week that a service layer or a credential
existed while the surface that would use it did not: onboard's request.pdhc
client sent a header request.pdhc never accepted (#702), onboard's plan
picker had no route (#703), and here a decorator guarded nothing. The tests
pass in every case because they exercise the layer directly.

## #800 — Patient signer resolution was rejecting every existing patient (2026-10-08)

**Symptom in production:** any contract naming a `Patient/<guid>` signer was
refused with `signer_unresolved` → `"Patient/<guid> not found in IPS"`, for
patients that exist in ips.

**Cause — two faults on one call, either sufficient alone:**

1. `_resolve_patient` GET `{IPS_BASE_URL}/api/v1/patients/<guid>`. ips has no
   such route, so Flask answered **404 for every guid** — indistinguishable,
   to this function, from "no such patient".
2. `_ips_headers` sent the key as `X-API-Key`. ips's `require_auth` reads
   **only** `Authorization` and answers `401 "Missing Authorization header"`.

`_strict_mode()` defaults **True** and `STRICT_SIGNER_VALIDATION` is unset in
production, so both became a hard rejection rather than a logged warning.

**Fix:** probe `GET /api/v1/patients/<guid>/clinics` — it answers exactly the
question asked (200 + list when the patient exists, 404 when not) and carries
`@require_auth` only, with no `_can_act_on_patient`, which is why a service key
may use it (unlike `/blocks`, see #801). Key sent as `Authorization: ApiKey`.

**No credential was minted.** contract's existing 64-char `IPS_API_KEY` was
valid throughout; it had simply been sent under a name ips does not read.

**Tests.** All 24 pre-existing resolution tests pass *with the bug in place*:
each patches `http_requests.get` and asserts on the status code it chose to
return, so the URL and the headers are inputs the mock throws away. Added
`TestIpsWireShape`, which asserts the wire shape — exact URL, exact header name
and scheme. Two of its three tests fail against the old code (verified by
reverting, not assumed). 147 passed.
`tests/test_provider_ref_guard.py` errors at collection on a missing
`JWT_SECRET_KEY`/`DATABASE_URL`; pre-existing, confirmed at HEAD, unrelated.

**Live verification** from inside `contract-api-1` after the rebuild — the
ticket explicitly asked for this rather than a mocked 200:

```
ips base   : http://host.docker.internal:9040
strict mode: True
auth header: ApiKey, key length 64; X-API-Key present: False
real guid 8f83e561-5ff8-495a-8d5c-13368007426b -> RESOLVED
guid 00000000-0000-0000-0000-000000000000      -> still REJECTED
```

The second guid matters as much as the first: the gate still denies. A fix that
made every patient resolve would have been worse than the defect.

**Deploy.** Commit `2b56f07`. The deployed file was byte-identical to the
pre-change baseline (`c4124577`), so nothing server-side-only was overwritten —
prod `git status` showed ` M`, but only against prod HEAD `1f67a8e`, which is
older than local. Predeploy backup:
`~/backups/predeploy/contract.pdhc/signer_resolver.py.800.bak`. Rebuilt with
`docker-compose up -d --build` (no bind mount — source is baked into the image,
so a restart alone would have run the old code). In-image hash
`6c21202087b8099c` = local HEAD. `/health`: `status ok`, `database connected`.

## #708 — sibling smoke for contract.pdhc (2026-10-08)

`deploy/smoke_siblings.sh` + `deploy/smoke_siblings.py`. Read-only by default;
everything that writes is behind `--for-real`, and the script names what it
created.

**Why it runs inside the container.** `IPS_BASE_URL` is
`http://host.docker.internal:9040` and `REQUEST_BASE_URL` is
`host.docker.internal:9060` — meaningful in `contract-api-1` and nowhere else.
A smoke run from a laptop would exercise a different client than the one
serving production. The wrapper `docker exec`s it; `deploy/` is not copied into
contract's image (the Dockerfile ships only `app/backend` to `/srv`), so the
script is piped over stdin.

**Why it builds requests with contract's own helpers.** `_ips_headers()`,
`_sso_cfg()`, `_ips_base()`. #800 *was* a bug in `_ips_headers`; a smoke that
composed its own headers would have been green against it.

**The central distinction.** Every check separates two answers that look alike
in a log and mean opposite things:

| answer | meaning |
|---|---|
| 401 / 403 | our credential or header name is wrong |
| 404 | the credential WORKED; the resource is simply absent |

Probing a deliberately absent-but-well-formed guid turns each edge into a
credential test with no side effects — including the write edge, checked via
`GET /internal/service-request/<absent>/context`, which carries the same
blueprint, the same `@requires_service_key` and the same `INTERNAL_SERVICE_KEY`
as `auto-provision-pat` but writes nothing.

**Six edges, four siblings:**

| sibling | route | caller | verdict |
|---|---|---|---|
| sso | `GET /api/public/organisations` | `sso.py` | 200 |
| sso | `GET /api/auth/me/service` | `sso.py` | 401 on a junk token (correct) |
| ips | `GET /api/v1/patients/<g>/clinics` | `signer_resolver.py` | 404 absent / 200 real |
| ips | `GET /api/v1/patients/<g>/consents` | `consent_emitter.py` | 404 |
| plan | `GET /api/v1/concepts/<g>` | `scope_validation.py` | 404, unauthenticated by design |
| request | `POST /internal/auto-provision-pat` | `main.py` | **401 — see #802** |

**It found a live defect on its first run.** contract's and request's
`INTERNAL_SERVICE_KEY` differ (both set, both 64 chars, sha256 `773a030c` vs
`2d172e9a`), so PAT auto-provisioning has been failing — and silently, because
`main.py` logs `resp.status_code` at `info()` without checking it. A 401 reads
as an ordinary log line and the loop continues. Only `RequestException` is
warned about, so a transport failure is louder than an auth failure. Operator
`.env` fix, filed as **#802**.

**A note on the two ips callers.** This repo held the bug and its fix at the
same time: `consent_emitter._ips_headers` sent `Authorization: ApiKey` and even
cited the request.pdhc precedent, while `signer_resolver._ips_headers` sent
`X-API-Key` to the same service. No unit test in either file could see the
difference. The smoke now checks both so they cannot silently diverge again.

### The path guard, and why the first version of it was worthless

`app/backend/tests/test_smoke_siblings_paths.py` asserts every path the smoke
probes appears in the client module that builds it — the guard gateway's smoke
docstring advertises as `TestPathsMatchTheClients`, which does not exist by that
name (the real one is an inline `@check`; see #804).

The first version used `frag in src` and was useless: it passes
`/patients/%s/clinics` against a client calling
`/api/v1/patients/{guid}/clinics`, because the truncation is a substring of the
original. That is the precise bug gateway's docstring cites
(`fhir/Observation` vs `/api/v1/fhir/Observation`). The first fragment is now
anchored to a `}` or quote boundary, and the truncated path fails — verified by
introducing the truncation and watching it fail, not by reasoning.

156 passed. `tests/test_provider_ref_guard.py` still errors at collection on
missing env; pre-existing, unrelated.

### Status of #708 overall — still OPEN

Three of ~20 services have a sibling smoke: `analyse.pdhc` (8 checks),
`gateway.pdhc` (9 checks), `contract.pdhc` (6 edges). `onboard.pdhc` has the
richer `deploy/full_call.sh` that the ticket cites as the precedent. Both
pre-existing smokes were run against production today and pass. The ticket is
explicitly incremental ("one service at a time, starting with whichever is next
to be deployed"), so it stays open.
