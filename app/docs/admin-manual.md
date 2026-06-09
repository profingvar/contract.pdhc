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

> **When to use a Contract vs PatientConsent vs PatientBlock**
>
> A Contract scopes traffic between two **organisations** (per concept,
> never per patient). It is the wrong tool for "patient P consents to
> caregiver G reading their data" — that is a `PatientConsent` on
> ips.pdhc. It is also the wrong tool for "patient P blocks clinic S"
> — that is a `PatientBlock` on ips.pdhc.
>
> When a contract is signed and a patient appears in `signer[]`, the
> system auto-emits a `PatientConsent` row on ips.pdhc as a side
> effect (`granted_via='contract'`, `contract_guid=<linkback>`). The
> two artefacts coexist by design.
>
> See [architecture §1.3](architecture.md#13-where-contracts-fit-in-the-pdl-consent--blocking-model) for the full framing.

### 3.1 Creating contracts

```json
POST /fhir/Contract
{
  "resourceType": "Contract",
  "status": "negotiable",
  "period": {
    "start": "2026-01-01T00:00:00Z",
    "end": "2026-12-31T23:59:59Z"
  },
  "subject": [
    {"reference": "Organization/abc-123"}
  ],
  "extension": [
    { "url": "https://contract.pdhc.se/StructureDefinition/legally-ok",            "valueBoolean": false },
    { "url": "https://contract.pdhc.se/StructureDefinition/pub-exists",            "valueBoolean": false },
    { "url": "https://contract.pdhc.se/StructureDefinition/legal-provider",        "valueBoolean": false },
    { "url": "https://contract.pdhc.se/StructureDefinition/provider-data-status",  "valueCode":    "unclear" }
  ]
}
```

- **`resourceType`** must be `"Contract"` (required)
- **`status`** must be one of the four supported FHIR R5 codes (required) — see §3.1.1
- **`period.start`** and **`period.end`** must be ISO-8601 datetime strings (optional)
- **`subject`** references must use `"ResourceType/id"` format (optional)
- **`extension[]`** carries the four platform-specific compliance fields — see §3.1.2
- A GUID is auto-generated if `id` is not provided

#### 3.1.1 Status — supported codes

The platform constrains `Contract.status` to four FHIR R5 codes. Other FHIR codes are still accepted at the API layer (the persisted JSON validates against any FHIR-aware tool), but the admin UI only emits these four:

| UI label              | FHIR code     | Meaning |
|-----------------------|---------------|---------|
| Under consideration   | `negotiable`  | Drafting / under negotiation. Contract is not yet a basis for fulfilling requests. |
| **Active**            | `executed`    | **Only this state qualifies a contract for fulfilling requests.** All other states cause request submission to be rejected. |
| Expired              | `terminated`  | Period elapsed; archived. |
| Revoked              | `revoked`     | Cancelled. Irreversible at the platform layer. |

The UI label "Active" wraps the FHIR code `executed` because the FHIR spelling is awkward to non-implementers; the underlying JSON remains FHIR-canonical.

#### 3.1.2 Compliance + provider-data extensions

Four FHIR `Contract.extension[]` entries capture platform-side governance state. They travel with the JSON across servers (no platform-specific column needed) and are FHIR-portable.

| Extension URL                                                                | Type           | Default | UI |
|------------------------------------------------------------------------------|----------------|---------|----|
| `https://contract.pdhc.se/StructureDefinition/legally-ok`                    | `valueBoolean` | `false` | Checkbox **Legally OK** |
| `https://contract.pdhc.se/StructureDefinition/pub-exists`                    | `valueBoolean` | `false` | Checkbox **PUB exists** (PUB = personuppgiftsbiträdesavtal / data-processor agreement) |
| `https://contract.pdhc.se/StructureDefinition/legal-provider`                | `valueBoolean` | `false` | Checkbox **Legal Provider** |
| `https://contract.pdhc.se/StructureDefinition/provider-data-status`          | `valueCode`    | `unclear` | Radio: `ok` / `deficient` / `unclear` |

All four are operator-managed metadata; none currently affect the request-fulfillment gate (which depends only on `status == executed`). They exist for governance, audit, and partner onboarding workflows.

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
