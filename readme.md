## Deployment plan (local-first)

### 1.a. Local repository structure (keep root clean)
Create the following structure:

- `./app/` (all application code, config, venv, and local runtime artifacts)
- `./results/` (all test and run outputs, timestamped per Rule 11)
- Root files: `readme.md`, `progress.md`, `newtask.txt`, `changed_files.md`, `# Top Rules.md`

### 1.a.1 Create required tracking files
Ensure these files exist in repo root:

- `readme.md`
- `progress.md`
- `newtask.txt`
- `changed_files.md`

### 1.a.2 Create application folder
Create `./app/` and keep everything else out of root.

### 1.a.3 Choose ports (9020–9030 only)
Reserve localhost ports:

- **9020**: PostgreSQL (container published to host)
- **9021**: Backend API (Flask)
- **9022**: Frontend (separate `web` container)

Do not use ports outside 9020–9030.

### 1.a.4 Containerize the stack (self-standing)
Use Docker Compose inside `./app/` to run:

- `db`: PostgreSQL (published on `localhost:9020`)
- `api`: Flask API (published on `localhost:9021`)
- `web`: frontend build (published on `localhost:9022`) as a separate container

Constraints:

- Must not interfere with other services on a future server.
- No assumptions about reverse proxy/SSO; isolate by ports and compose project name.

### 1.a.5 Python environment (where applicable)
Maintain a Python virtual environment under `./app/.venv/` for local tooling (lint/tests/dev utilities), even though runtime is containerized.

### 1.a.6 Database baseline (PostgreSQL + FHIR alignment)
Local DB is PostgreSQL and the domain model must be **FHIR R5-aligned**.

- The system’s “capability statement” baseline is the current self-standing **FHIR HAPI** behavior (use it as the reference for what endpoints/resources should look like).
- Model contracts using best-practice FHIR R5 resources and references (GUIDs) and enforce validation at API boundaries.

### 1.a.7 Identity and roles (best practice)
Implement **two roles**:

- **reader**: can read allowed resources
- **admin**: full CRUD + user management

Authentication:

- Use a simple, standard approach for local-first (e.g., session or JWT) but keep secrets in environment variables.
- Seed an initial admin user **only via env-driven bootstrap**. Do not hardcode credentials.
- Provide procedure to rotate/revoke credentials.

### 1.a.8 Rate limiting (anonymous/open read)
Open read access is allowed with a strict rate limit:

- **100 requests per hour** for read operations (scope: per IP by default; document any alternative)

### 1.a.9 API design & validation
Implement:

- FHIR R5 compliant API schema for the supported resources
- Validation layer that rejects non-conforming payloads
- Internal linking via **GUIDs** (not numeric IDs) for all cross-entity references and frontend/backend coordination

### 1.a.9.1 Frontend design (admin + reader UX)
Frontend goal: a sleek, low-friction admin UI for contract CRUD and user management, plus a simple read-only browsing experience.

Information architecture:

- **Public (reader)**:
  - Contract list (search/filter/sort)
  - Contract details (read-only)
  - Rate-limit messaging (“100/hour” with friendly error state)
- **Admin**:
  - Login
  - Dashboard (key counts + recent changes)
  - Contracts: list → create/edit/delete
  - Parties: payer/provider pickers (FHIR `Organization` search)
  - Plan definitions: search/select reference (FHIR `PlanDefinition`)
  - Users: list → create user → role assignment (admin/reader) → reset password → deactivate
  - Audit/revisions view (who changed what, when)

Core screens (minimum viable set):

- **Contracts list**: dense table with quick filters (payer, provider, status, date range), debounced search, server-side pagination.
- **Contract editor**: two-column form, autosave draft, explicit “Publish/Save” CTA, clear validation errors, and a “Delete” danger zone.
- **User management**: table + create/reset/deactivate flows.

UI principles (best practice):

- **Accessibility**: keyboard navigable, visible focus, semantic headings, sufficient contrast.
- **Errors**: inline validation + global toast for API failures; never lose user input.
- **Performance**: list virtualization not required initially; prefer server paging + caching; avoid refetch loops.
- **Security**: never expose secrets; session/JWT stored safely; protect admin routes; CSRF if cookie-based auth.

Implementation approach (container-friendly):

- Serve a static SPA from a `web` container on **9022**, talking to API on **9021**.

### 1.a.10 Capability statement & endpoint tests
Create:

- A generated `CapabilityStatement` endpoint (or a static one if that’s the chosen approach) aligned with the HAPI baseline for the supported subset.
- A script that tests all API endpoints according to the capability statement, suitable for CI/local runs.

### 1.a.10.1 Technical documentation and manuals (planned deliverables)
Keep documentation inside `./app/docs/` so the repo root stays clean.

Documentation set (best practice):

- **Operator manual** (`./app/docs/operator-manual.md`)
  - How to start/stop locally (`./start.sh`)
  - How to run tests and where to find `./results/<timestamp>_results/`
  - Backup/restore (local Postgres volume)
  - Common failures and safe recovery steps
- **Admin manual** (`./app/docs/admin-manual.md`)
  - Login/logout
  - User lifecycle (create, role assignment, reset password, deactivate)
  - Contract CRUD workflow and validation expectations
  - Rate limiting behavior and troubleshooting
- **API documentation** (`./app/docs/api.md`)
  - Supported FHIR R5 resources and search parameters
  - Auth rules (public read vs admin)
  - Error model + examples
  - Link to `CapabilityStatement` output and how it’s generated
- **Architecture overview** (`./app/docs/architecture.md`)
  - Container topology, ports 9020–9030, and data flows
  - GUID usage rules (frontend/backend + DB)
  - Security posture (secrets, rotation, audit logging)
- **Runbooks** (`./app/docs/runbooks/*.md`)
  - Credential rotation + revocation
  - Incident response checklist (rate limit spikes, DB corruption, failed migrations)
  - Upgrade procedure (dependency updates, DB migrations)

How docs stay current:

- Treat docs as part of the deployment plan: update docs whenever an endpoint, auth rule, or UI flow changes.
- Generate API examples from tests where possible (endpoint test script output saved under `./results/...`).

### 1.a.11 Test discipline (pytest + results folder)
All tests use **pytest**. Store results under:

- `./results/<ISO-8601-UTC>_results/`

Include:

- test logs
- coverage (if used)
- endpoint test script output

### 1.a.12 Start/stop script (single entry point)
Create `./start.sh` at repo root that:

- Kills processes using ports **9020–9030** (and also 9040–9043 per rules)
- Ensures Docker is running
- Activates `./app/.venv/` for tooling
- Starts the database and app containers
- On Ctrl+C, gracefully shuts down containers and deactivates the venv

### 1.a.13 API key handling (storage/rotation/expiry/revocation)
Document and implement best practice:

- Store API keys/secrets only in environment variables (and secret managers later)
- Rotation procedure (regular + emergency)
- Expiry policy
- Revocation procedure

### 1.a.14 “Web level” handling (later phase)
When a web instance exists:

- Target URL: `contract.pdhc.se`
- First download/archive remote state to a temporary archive
- Compare with local
- Present diffs and propose next steps without applying changes remotely
- Operator performs web edits/restarts (no ssh/scp instructions in plan)

