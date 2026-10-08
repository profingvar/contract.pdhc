"""contract.pdhc → sibling-service smoke (#708).

Runs INSIDE contract-api-1, because that is the only place the caller's real
configuration exists: `IPS_BASE_URL` is `http://host.docker.internal:9040`,
which resolves in the container and nowhere else. A smoke run from the host
would be testing a different client than the one in production.

## Why this exists

Unit tests cannot catch a cross-service contract mismatch, and in this repo
they demonstrably did not. All 24 of `signer_resolver`'s resolution tests
passed for months while the code called an ips route that does not exist with
a header ips does not read (#800) — because every one of them patched
`requests.get` and asserted on the status code it had chosen to return. The
URL and the headers are inputs a mock discards.

Worse, this repo held BOTH the bug and its fix at the same time:
`consent_emitter._ips_headers` sent `Authorization: ApiKey` and even cited the
request.pdhc precedent in a comment, while `signer_resolver._ips_headers`,
calling the same service, sent `X-API-Key`. No test in either file could see
the difference. Nothing short of a real call can.

## The central distinction

Every check below separates two answers that look alike in a log and mean
opposite things:

    401 / 403  -> our CREDENTIAL or HEADER NAME is wrong
    404        -> the credential WORKED; the resource just is not there

That is the whole trick. #800 presented as "Patient not found in IPS" — a
sentence about the patient — when the truth was a header the callee ignores.
Probing a deliberately absent guid turns every edge into a credential test
with no side effects, which is also how the write edge is checked read-only.

## Deliberately NOT done here

Per #708: no cross-service contract registry, and this is not wired into CI.
It is a pre-deploy gate an operator runs and reads.

Exit code 0 = all checked edges healthy, 1 = first failure (and it stops).
"""
import argparse
import json
import os
import sys
import uuid

# A guid that cannot exist. Used to probe an edge's AUTH without touching data:
# a well-formed UUID gets past input validation and reaches the auth gate, so
# the status code that comes back is about our credentials, not our syntax.
ABSENT = "00000000-0000-0000-0000-000000000000"

# ---------------------------------------------------------------------------
# Every path this smoke probes, and the client module that really builds it.
#
# This table exists because gateway.pdhc's smoke docstring advertises a
# `TestPathsMatchTheClients` guard that WAS NEVER WRITTEN -- and the docstring
# explains the guard was needed because an earlier smoke probed a path the
# author had invented (`fhir/Observation` for `/api/v1/fhir/Observation`),
# which would have reported a healthy boundary no code uses. A smoke that
# tests a path production never calls is worse than no smoke: it is a green
# light wired to nothing.
#
# `tests/test_smoke_siblings_paths.py` asserts each entry below against the
# named module's source, so the guard is real here.
#
# A None means "deliberately not in any client", and every such entry has to
# justify itself in the comment beside it.
# ---------------------------------------------------------------------------
P_SSO_ORGS = "/api/public/organisations"
P_SSO_ME = "/api/auth/me/service"
P_IPS_CLINICS = "/api/v1/patients/%s/clinics"
P_IPS_CONSENTS = "/api/v1/patients/%s/consents"
P_PLAN_CONCEPT = "/api/v1/concepts/%s"
P_REQ_SR_CONTEXT = "/api/v1/internal/service-request/%s/context"
P_REQ_PROVISION = "/api/v1/internal/auto-provision-pat"

CLIENT_SOURCE = {
    P_SSO_ORGS: "sso.py",
    P_SSO_ME: "sso.py",
    P_IPS_CLINICS: "signer_resolver.py",
    P_IPS_CONSENTS: "consent_emitter.py",
    P_PLAN_CONCEPT: "scope_validation.py",
    # No client calls this. It is a READ-ONLY STAND-IN for P_REQ_PROVISION:
    # same blueprint, same @requires_service_key decorator, same
    # INTERNAL_SERVICE_KEY, but a GET that writes nothing. It lets the smoke
    # prove the write edge's credential without minting anything. The test
    # enforces that the edge it stands in for IS in a client.
    P_REQ_SR_CONTEXT: None,
    P_REQ_PROVISION: "main.py",
}


GREEN, RED, YELLOW, DIM, OFF = (
    "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[0m")
if not sys.stdout.isatty():
    GREEN = RED = YELLOW = DIM = OFF = ""

results = []


def _probe(label, sibling, method, url, headers, *, expect, body=None,
           note=""):
    """Issue one real call and judge it. Returns True on pass."""
    import requests

    print("%s[%s]%s %s" % (DIM, sibling, OFF, label))
    print("    %s %s" % (method, url))
    shown = {k: ("%s <%d chars>" % (v.split(" ", 1)[0], len(v.split(" ", 1)[1]))
                 if k == "Authorization" and " " in v
                 else ("<%d chars>" % len(v) if k in
                       ("X-Service-Key", "X-API-Key", "X-SSO-Client-Secret")
                       else v))
             for k, v in headers.items()}
    print("    headers: %s" % json.dumps(shown, sort_keys=True))

    try:
        r = requests.request(method, url, headers=headers, json=body,
                             timeout=10)
    except Exception as e:  # noqa: BLE001 — any transport failure is a failure
        print("    %sUNREACHABLE%s %s: %s" % (RED, OFF, type(e).__name__, e))
        results.append((label, sibling, "UNREACHABLE"))
        return False

    snippet = (r.text or "")[:220].replace("\n", " ")
    ok = r.status_code in expect
    if ok:
        print("    %s%s%s  %s" % (GREEN, r.status_code, OFF, snippet))
        if note:
            print("    %s%s%s" % (DIM, note, OFF))
        results.append((label, sibling, "ok"))
        return True

    print("    %s%s%s  %s" % (RED, r.status_code, OFF, snippet))
    print("    expected one of %s" % (sorted(expect),))
    if r.status_code in (401, 403):
        print("    %s=> CREDENTIAL OR HEADER NAME IS WRONG. The callee is up "
              "and refused us.%s" % (RED, OFF))
        print("    %s   This is the #800 shape: a 4xx that reads like a "
              "statement about the resource.%s" % (RED, OFF))
    elif 500 <= r.status_code < 600:
        print("    %s=> the SIBLING errored. Our request may still be "
              "malformed; check its logs.%s" % (RED, OFF))
    elif r.status_code == 404:
        print("    %s=> route may not exist on the sibling (the other half of "
              "#800). Check its URL map, not just the guid.%s" % (RED, OFF))
    results.append((label, sibling, "FAIL %s" % r.status_code))
    return False


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--for-real", action="store_true",
                    help="also exercise the WRITE edge (mints a real PAT in "
                         "request.pdhc). Omitted = read-only, no state "
                         "changes anywhere.")
    ap.add_argument("--patient-guid", metavar="GUID",
                    help="a patient that really is assigned to a clinic in "
                         "ips. Adds POSITIVE checks: without it the ips edges "
                         "only prove auth works, not that a real lookup "
                         "succeeds.")
    ap.add_argument("--provider-org-guid", metavar="GUID",
                    help="required with --for-real: the provider org to mint "
                         "a PAT for.")
    ap.add_argument("--contract-guid", metavar="GUID",
                    help="required with --for-real: the contract to bind it "
                         "to.")
    args = ap.parse_args()

    sys.path.insert(0, "/srv")
    from app.main import app
    from app import signer_resolver, consent_emitter, scope_validation

    created = []

    with app.app_context():
        ips_base = signer_resolver._ips_base()
        # Resolve sso exactly as the real caller does, via its own config
        # helper, rather than re-reading the env here. A smoke that computes
        # its own idea of the base URL is testing itself.
        from app import sso as contract_sso
        sso_cfg = contract_sso._sso_cfg()
        sso_base = (sso_cfg.get("base_url") or "").rstrip("/")
        plan_base = (os.environ.get("PLAN_BASE_URL")
                     or app.config.get("PLAN_BASE_URL") or "").rstrip("/")
        req_base = (os.environ.get("REQUEST_BASE_URL")
                    or app.config.get("REQUEST_BASE_URL") or "").rstrip("/")
        svc_key = (os.environ.get("INTERNAL_SERVICE_KEY")
                   or app.config.get("INTERNAL_SERVICE_KEY") or "")

        print("=" * 72)
        print("contract.pdhc -> siblings  (#708)   mode: %s"
              % ("FOR REAL (will write)" if args.for_real else "read-only"))
        print("=" * 72)
        for name, val in (("ips", ips_base), ("sso", sso_base),
                          ("plan", plan_base), ("request", req_base)):
            print("  %-8s %s" % (name, val or "%s(UNSET)%s" % (YELLOW, OFF)))
        print("  strict signer validation: %s" % signer_resolver._strict_mode())
        print()

        # --- 1. sso: the organisation list that resolves Organization signers
        if sso_base:
            if not _probe(
                    "organisation signer resolution",
                    "sso", "GET", "%s%s" % (sso_base, P_SSO_ORGS),
                    {"Accept": "application/json"}, expect={200},
                    note="public route; a 200 here is also the only proof the "
                         "reverse proxy is not in front of this call"):
                return 1

            # The access-blob validator. A real user token cannot be minted
            # non-interactively, so this asserts the weaker but still useful
            # property: the endpoint is REACHABLE and REJECTS cleanly rather
            # than 500ing. If the SSO_CLIENT_ID/SECRET pair were wrong the
            # body differs from a plain bad-token refusal -- which is why the
            # body is printed rather than swallowed (see
            # project_pdhc_sso_client_creds: a wrong pair fails silently).
            # Send the REAL client pair. An earlier draft of this script
            # guarded with hasattr() and fell back to empty strings -- which
            # would have sent no credentials and still "passed" on the 401,
            # proving nothing. If the helper is gone, that is a failure.
            cid = str(sso_cfg.get("client_id") or "")
            csec = str(sso_cfg.get("client_secret") or "")
            if not cid or not csec:
                print("%sSSO_CLIENT_ID/SECRET not configured — /me/service "
                      "cannot succeed for any token, so login is broken. "
                      "See project_pdhc_sso_client_creds.%s" % (RED, OFF))
                return 1
            if not _probe(
                    "access-blob validation (deliberately invalid token)",
                    "sso", "GET", "%s%s" % (sso_base, P_SSO_ME),
                    {"Authorization": "Bearer not-a-real-token",
                     "X-SSO-Client-Id": cid,
                     "X-SSO-Client-Secret": csec,
                     "Accept": "application/json"},
                    expect={401, 403},
                    note="401/403 is the PASS here -- we sent a junk token on "
                         "purpose. A 5xx or a timeout is the failure. READ THE "
                         "BODY: 'invalid client' means our client pair is "
                         "wrong, which breaks login silently."):
                return 1
        else:
            print("%s[sso] SKIPPED — SSO_BASE_URL unset%s\n" % (YELLOW, OFF))

        # --- 2. ips: the two edges that disagreed with each other until #800
        if ips_base:
            if not _probe(
                    "patient signer existence (signer_resolver, #800)",
                    "ips", "GET",
                    "%s%s" % (ips_base, P_IPS_CLINICS % ABSENT),
                    signer_resolver._ips_headers(), expect={404},
                    note="404 = our ApiKey was ACCEPTED and this guid simply "
                         "is not there. A 401 would mean the #800 regression "
                         "is back; a 404 on the ROUTE looks identical here, "
                         "which is why the positive check below matters."):
                return 1

            if not _probe(
                    "consent emit/read (consent_emitter, cf. request #558)",
                    "ips", "GET",
                    "%s%s" % (ips_base, P_IPS_CONSENTS % ABSENT),
                    consent_emitter._ips_headers(), expect={200, 404},
                    note="this module always sent the right header; the check "
                         "exists so the two ips callers in this repo can "
                         "never silently diverge again."):
                return 1

            if args.patient_guid:
                # The positive case. Without this, every ips check above is
                # satisfied by a 404 -- including the 404 that #800 produced
                # for EVERY guid. A real patient is the only input that tells
                # "route is wrong" apart from "guid is absent".
                if not _probe(
                        "POSITIVE: resolve a patient that really exists",
                        "ips", "GET",
                        "%s%s" % (ips_base, P_IPS_CLINICS % args.patient_guid),
                        signer_resolver._ips_headers(), expect={200},
                        note="this is the check #800 would have failed. "
                             "Without --patient-guid the suite is blind to a "
                             "wrong-route bug."):
                    return 1

                reason = signer_resolver._resolve_patient(
                    args.patient_guid, strict=True)
                print("%s[ips]%s signer_resolver._resolve_patient() verdict"
                      % (DIM, OFF))
                if reason is None:
                    print("    %sRESOLVED%s via the real production code path"
                          % (GREEN, OFF))
                    results.append(("resolve_patient verdict", "ips", "ok"))
                else:
                    print("    %sREJECTED%s %s" % (RED, OFF, reason))
                    results.append(("resolve_patient verdict", "ips", "FAIL"))
                    return 1
                print()
            else:
                print("%s[ips] NOTE: no --patient-guid, so the ips edges "
                      "proved AUTH only. A wrong route also returns 404 and "
                      "would pass.%s\n" % (YELLOW, OFF))
        else:
            print("%s[ips] SKIPPED — IPS_BASE_URL unset. NOTE: _resolve_"
                  "patient() returns None (accept) when the base is unset, so "
                  "signer validation is silently OFF in this "
                  "configuration.%s\n" % (YELLOW, OFF))

        # --- 3. plan: concept scope validation (deliberately unauthenticated)
        if plan_base:
            if not _probe(
                    "scope concept validation (scope_validation)",
                    "plan", "GET",
                    "%s%s" % (plan_base, P_PLAN_CONCEPT % ABSENT),
                    {"Accept": "application/json"}, expect={404},
                    note="sent with NO credential on purpose: plan's "
                         "capability statement marks GET /concepts/<guid> as "
                         "auth=False. A 401/403 here means plan started "
                         "requiring auth and this edge broke."):
                return 1
        else:
            print("%s[plan] SKIPPED — PLAN_BASE_URL unset%s\n"
                  % (YELLOW, OFF))

        # --- 4. request: prove the WRITE edge's credential WITHOUT writing
        if req_base and svc_key:
            if not _probe(
                    "internal service-key credential (read-only proxy for the "
                    "PAT write edge)",
                    "request", "GET",
                    "%s%s" % (req_base, P_REQ_SR_CONTEXT % ABSENT),
                    {"X-Service-Key": svc_key, "Accept": "application/json"},
                    expect={404},
                    note="this route carries the SAME @requires_service_key "
                         "gate and the SAME INTERNAL_SERVICE_KEY as "
                         "auto-provision-pat, so a 404 proves our key matches "
                         "while writing nothing. A 401 means the two .env "
                         "values have drifted -- and note the caller LOGS the "
                         "status at info() without checking it, so a broken "
                         "PAT provisioning is silent in production."):
                return 1
        elif req_base:
            print("%s[request] SKIPPED — INTERNAL_SERVICE_KEY unset, so PAT "
                  "auto-provisioning cannot authenticate at all%s\n"
                  % (YELLOW, OFF))
        else:
            print("%s[request] SKIPPED — REQUEST_BASE_URL unset%s\n"
                  % (YELLOW, OFF))

        # --- 5. the actual write, only when asked
        if args.for_real:
            if not (args.provider_org_guid and args.contract_guid):
                print("%s--for-real needs --provider-org-guid and "
                      "--contract-guid%s" % (RED, OFF))
                return 2
            if not _probe(
                    "MINT A REAL PAT (write)",
                    "request", "POST",
                    "%s%s" % (req_base, P_REQ_PROVISION),
                    {"X-Service-Key": svc_key,
                     "Content-Type": "application/json"},
                    body={"provider_org_guid": args.provider_org_guid,
                          "contract_guid": args.contract_guid},
                    expect={200, 201},
                    note="idempotent per org+contract: it returns the "
                         "existing PAT rather than minting a second one."):
                return 1
            created.append(
                "PAT for org=%s contract=%s in request.pdhc"
                % (args.provider_org_guid, args.contract_guid))

    print("=" * 72)
    bad = [r for r in results if r[2] != "ok"]
    for label, sibling, verdict in results:
        mark = "%sPASS%s" % (GREEN, OFF) if verdict == "ok" \
            else "%s%s%s" % (RED, verdict, OFF)
        print("  %-6s %-9s %s" % (mark, sibling, label))
    print("=" * 72)
    if created:
        print("%sCREATED — the operator must clean these up:%s" % (YELLOW, OFF))
        for c in created:
            print("  * %s" % c)
    else:
        print("Created nothing. No state changed on any service.")
    print("%d edge(s) checked, %d failed." % (len(results), len(bad)))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
