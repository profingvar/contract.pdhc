# Contract scope (request_scope / return_scope)

Each Contract between a requesting org and a provider org may carry a
per-concept scope, encoded as FHIR `term[]`. Two scope lists are
supported, both optional:

- **`request_scope`** — the concepts the **requester may ask of this
  provider**. Enforced at SR creation time by `request.pdhc`.
- **`return_scope`** — the concepts the **provider may submit
  observations for**, with each concept marked `obligatory_return` or
  `optional_return`. Enforced at report ingestion time by
  `gateway.pdhc`.

A contract with no `term[]` is backward-compatible: every concept is
permitted on both sides. `term[]`-less contracts pre-date the scope
work and should be progressively upgraded.

## Authoring scope via FHIR Contract

Scope is part of the FHIR Contract resource you POST/PUT to
`/fhir/Contract`. The exact shape (validated by `_validate_terms`):

```jsonc
{
  "resourceType": "Contract",
  "status": "executed",
  "term": [
    {
      "type": {"text": "request_scope"},
      "offer": {"text": "Concepts permitted in outbound ServiceRequests"},
      "asset": [
        {
          "type": [{"text": "outbound_concept"}],
          "typeReference": [
            {"reference": "https://plan.pdhc.se/api/v1/concepts/<concept-guid-A>"},
            {"reference": "https://plan.pdhc.se/api/v1/concepts/<concept-guid-B>"}
          ]
        }
      ]
    },
    {
      "type": {"text": "return_scope"},
      "offer": {"text": "Concepts the provider may submit"},
      "asset": [
        {
          "type": [{"text": "obligatory_return"}],
          "typeReference": [
            {"reference": "https://plan.pdhc.se/api/v1/concepts/<concept-guid-A>"}
          ]
        },
        {
          "type": [{"text": "optional_return"}],
          "typeReference": [
            {"reference": "https://plan.pdhc.se/api/v1/concepts/<concept-guid-B>"}
          ]
        }
      ]
    }
  ]
}
```

Constraints (all rejected with HTTP 400 from `ensure_contract_shape`):

- `term[].type.text` must be one of `request_scope`, `return_scope`.
- Each term type may appear at most once per contract.
- `asset[].type[0].text` must match: `outbound_concept` for
  `request_scope`; `obligatory_return` or `optional_return` for
  `return_scope`.
- `asset[].typeReference[].reference` must be a valid concept URL
  shaped `https?://.+/api/v1/concepts/<uuid>`.

## Concept-existence validation (ticket #135)

When `STRICT_SCOPE_CONCEPTS=true` (default in production), every
concept GUID referenced in `term[]` is checked against plan.pdhc via
`GET /api/v1/concepts/<guid>` at contract create/update time:

| Plan response | Contract verdict | HTTP |
|---|---|---|
| 200 for every GUID | accepted | 201 / 200 |
| 404 for any GUID | rejected, `missing_concept_guids` returned | 422 |
| Network error or non-2xx | rejected as `scope_validation_unavailable` | 503 |

For local development without plan.pdhc running, set
`STRICT_SCOPE_CONCEPTS=false` and the validator skips the check
entirely. The intended state on `miserver` is `true`.

Audit: every validation attempt logs operator, contract id, GUID
count, and verdict via `app.logger.info`.

## Read API

`/fhir/Contract/<guid>/scope` (public, rate-limited) and
`/internal/contract/<guid>/scope` (X-Service-Key) both return:

```json
{
  "contract_guid": "...",
  "status": "executed",
  "scope_defined": true,
  "request_scope": [
    {"concept_guid": "...", "concept_url": "..."},
    ...
  ],
  "return_scope": {
    "obligatory_return": [...],
    "optional_return": [...]
  }
}
```

`scope_defined=false` means the contract has no `term[]` and every
concept is permitted (backward compatible). When the contract is in a
dead status (`revoked`, `terminated`, `cancelled`), both scope lists
are returned empty — downstream services treat that as
`CONTRACT_INACTIVE`.

The internal endpoint also returns a `parties` block with
`requesting_org_guid` and `provider_org_guids[]`, used by gateway.pdhc
to enrich observation provenance.

## Downstream enforcement

### request.pdhc — request_scope (ticket #135)

`app/services/scope_service.py` is called from
`create_service_request`. When `contract_guid` is set on a new SR,
the snapshot is walked for every concept GUID
(`activities[].concept_guid`, `activities[].transactions[].concept_guid`,
`activities[].transactions[].goal_concept_guid`, `goals[].concept_guid`)
and compared to `request_scope`.

Outcomes:

| Verdict | Response |
|---|---|
| Contract has no `term[]` (`scope_defined=false`) | 201 — SR created |
| `request_scope` empty (only `return_scope` defined) | 201 |
| Every concept in `request_scope` | 201 |
| Contract `revoked`/`terminated`/`cancelled` | 403 `CONTRACT_INACTIVE` |
| Any concept outside `request_scope` | 403 `SCOPE_VIOLATION` with `out_of_scope_concept_guids[]` |
| Contract service unreachable | **201** (fail-open; logged) |

Fail-open on contract-service downtime is deliberate: blocking SR
authoring on transient contract.pdhc unavailability would be a worse
failure than letting the report-ingestion check on the gateway side
catch any out-of-scope content.

### gateway.pdhc — return_scope + obligatory check

Already wired (`gateway_app/app/services/contract_scope.py`):

- Every observation's `concept_guid` must be in
  `return_scope.obligatory_return + return_scope.optional_return`
  → otherwise 403 `SCOPE_VIOLATION`.
- On `status=completed`, every concept in
  `return_scope.obligatory_return` must be present in the submitted
  observations → otherwise 422 `VALIDATION_ERROR`. The "aggregate
  across prior in-progress submissions" extension is tracked in
  ticket #147.

## Audit

Both rejection paths emit audit records:

- request.pdhc: `service_request.create.rejected` with `reason`
  (`CONTRACT_INACTIVE` or `SCOPE_VIOLATION`),
  `out_of_scope_concept_guids` when applicable.
- contract.pdhc: `app.logger.info(...)` with contract id, GUID count,
  and verdict from `verify_concepts_exist`.

## Configuration

| Setting | Where | Default | Notes |
|---|---|---|---|
| `STRICT_SCOPE_CONCEPTS` | contract.pdhc env | `true` | When false, plan.pdhc existence check is skipped. |
| `PLAN_BASE_URL` | contract.pdhc env | `https://plan.pdhc.se` | Source of truth for concepts. |
| `CONTRACT_BASE_URL` | request.pdhc env | `https://contract.pdhc.se` | Where `scope_service.fetch_scope` calls. |
| `INTERNAL_SERVICE_KEY` | both services | required | Service-to-service auth for `/internal/contract/<guid>/scope`. |
