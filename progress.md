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
