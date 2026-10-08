"""Every path deploy/smoke_siblings.py probes must be one a client really calls.

#708. This is the guard gateway.pdhc's smoke *advertises* in its docstring as
`TestPathsMatchTheClients` and never implemented. Its own docstring explains
why it is needed: an earlier smoke probed `fhir/Observation` instead of
`/api/v1/fhir/Observation` and would have reported a healthy boundary that no
code uses.

That failure mode is worse than having no smoke. A smoke exists to be trusted
when it is green, and a green light wired to a path production never calls is
a false assurance about the exact thing it claims to check — the same shape as
#800, where 24 tests were green against a route that does not exist.

So: for each entry in `CLIENT_SOURCE`, assert the path's literal fragments
appear in the named client module's source. Textual, deliberately — the point
is to catch a path I typed from memory, and importing the module would prove
only that it imports.
"""
import importlib.util
import pathlib
import re

import pytest

_HERE = pathlib.Path(__file__).resolve()
_BACKEND = _HERE.parent.parent            # app/backend
_APP = _BACKEND / "app"
_SMOKE = _BACKEND.parent.parent / "deploy" / "smoke_siblings.py"

# A path in a client always starts right after the base-URL placeholder or an
# opening quote. Matching that boundary is what makes the check reject a
# truncated prefix rather than quietly accepting it.
_BOUNDARY = "[}\"']"


def _load_smoke():
    """Import the smoke by path — it lives outside the package."""
    spec = importlib.util.spec_from_file_location("_smoke", _SMOKE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def smoke():
    if not _SMOKE.exists():
        pytest.fail("deploy/smoke_siblings.py is missing — #708's gate for "
                    "this service is gone, so nothing checks its sibling "
                    "edges before a deploy.")
    return _load_smoke()


def _fragments(template):
    """Literal pieces of a path template, ignoring the %s placeholders.

    `/api/v1/patients/%s/clinics` -> ['/api/v1/patients/', '/clinics']
    Each piece must appear verbatim in the client, which catches both a wrong
    prefix and a wrong suffix — the two halves of the #800 URL bug.
    """
    return [p for p in template.split("%s") if p]


class TestPathsMatchTheClients:
    def test_table_covers_every_probe(self, smoke):
        """Each P_* constant must appear in CLIENT_SOURCE.

        Otherwise a new probe could be added with an unchecked path, which is
        precisely the hole this file closes.
        """
        declared = {v for k, v in vars(smoke).items()
                    if k.startswith("P_") and isinstance(v, str)}
        missing = declared - set(smoke.CLIENT_SOURCE)
        assert not missing, (
            "these probed paths are not in CLIENT_SOURCE, so nothing checks "
            "that a client calls them: %s" % sorted(missing))

    @pytest.mark.parametrize("which", [
        "P_SSO_ORGS", "P_SSO_ME", "P_IPS_CLINICS", "P_IPS_CONSENTS",
        "P_PLAN_CONCEPT", "P_REQ_PROVISION",
    ])
    def test_path_appears_in_its_client(self, smoke, which):
        template = getattr(smoke, which)
        module_name = smoke.CLIENT_SOURCE[template]
        assert module_name is not None, (
            "%s is mapped to None but is listed as client-backed" % which)

        src_path = _APP / module_name
        assert src_path.exists(), "client module %s not found" % src_path
        src = src_path.read_text(encoding="utf-8")

        frags = _fragments(template)

        # ANCHOR the first fragment to a path boundary. A plain substring
        # check is not enough, and the first version of this test proved it:
        # with `in`, the truncated path `/patients/%s/clinics` passes against
        # a client calling `/api/v1/patients/{guid}/clinics`, because the
        # short form is a substring of the long one. Prefix truncation is
        # exactly the bug gateway's docstring describes (`fhir/Observation`
        # for `/api/v1/fhir/Observation`), so a guard that misses it is
        # decoration.
        #
        # In every client here the path begins immediately after the base-URL
        # placeholder or an opening quote, so requiring one of } " ' before it
        # rejects a path that merely ends the same way.
        head = re.search(_BOUNDARY + re.escape(frags[0]), src)
        assert head, (
            "smoke probes %r (%s) but %s never starts a URL with %r. Either "
            "the smoke tests a path production does not call, or the client "
            "changed under it. Both are a FALSE GREEN -- the one failure a "
            "smoke must not have."
            % (template, which, module_name, frags[0]))

        for frag in frags[1:]:
            assert frag in src, (
                "smoke probes %r (%s) but %s does not contain the trailing "
                "fragment %r." % (template, which, module_name, frag))

    def test_standins_are_justified(self, smoke):
        """A None entry is allowed only as a read-only proxy for a real edge.

        Today there is exactly one: the internal service-request context GET,
        which stands in for the auto-provision-pat POST so the write edge's
        credential can be checked without writing. Its justification is that
        the edge it proxies IS client-backed — assert that, so the stand-in
        cannot outlive the thing it stands for.
        """
        standins = [k for k, v in smoke.CLIENT_SOURCE.items() if v is None]
        assert standins == [smoke.P_REQ_SR_CONTEXT], (
            "unexpected un-backed probe path(s): %s. A path no client calls "
            "needs a stated reason here." % standins)

        # The edge it exists to vouch for must still be real.
        assert smoke.CLIENT_SOURCE[smoke.P_REQ_PROVISION] == "main.py"
        src = (_APP / "main.py").read_text(encoding="utf-8")
        assert smoke.P_REQ_PROVISION in src

        # Both must sit under the same internal prefix, or the stand-in is not
        # proving anything about the same gate.
        prefix = "/api/v1/internal/"
        assert smoke.P_REQ_SR_CONTEXT.startswith(prefix)
        assert smoke.P_REQ_PROVISION.startswith(prefix)


class TestSmokeIsReadOnlyByDefault:
    def test_write_edge_is_behind_for_real(self, smoke):
        """The PAT mint must not run unless --for-real is passed.

        #708 requires a stop-before-writing mode. Asserted against the source
        because running main() would issue the calls.
        """
        src = _SMOKE.read_text(encoding="utf-8")
        mint_at = src.index("MINT A REAL PAT")
        guard_at = src.index("if args.for_real:")
        assert guard_at < mint_at, (
            "the write probe is not inside the --for-real branch")
