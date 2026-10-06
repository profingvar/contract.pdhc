"""#755 — a non-GUID provider reference must not mint a PAT.

Three live contracts carry `Organization/None` — a Python None formatted into a
FHIR reference by whatever built them. `ref_str.split("/", 1)[1]` yields the
literal string `"None"`, and it was POSTed to request.pdhc as the provider org.

No such PAT exists today, so the risk was latent. The path is live and all three
contracts are `executed`, which is in the trigger list.
"""
from __future__ import annotations

import re

from app.main import _ORG_GUID_RE

GOOD = "7a69ab02-dfce-43ae-ade7-fabf6b858d84"


def test_a_real_guid_is_accepted():
    assert _ORG_GUID_RE.match(GOOD)


def test_the_literal_string_None_is_refused():
    """The exact value on the three affected contracts."""
    assert not _ORG_GUID_RE.match("None")


def test_other_stringified_nothings_are_refused():
    for bad in ("", "null", "nil", "undefined", "NULL", "none", "0"):
        assert not _ORG_GUID_RE.match(bad), bad


def test_a_truncated_or_padded_guid_is_refused():
    assert not _ORG_GUID_RE.match(GOOD[:-1])
    assert not _ORG_GUID_RE.match(GOOD + "x")
    assert not _ORG_GUID_RE.match(" " + GOOD)


def test_the_nil_uuid_is_ACCEPTED_by_shape():
    """Stated rather than silently assumed: the nil UUID is well-formed, so this
    guard passes it. It is a different defect — a placeholder, not a formatting
    accident — and belongs to #768, which is about what the organisation MEANS.
    Conflating the two here would make this guard reject data it has no business
    judging."""
    assert _ORG_GUID_RE.match("00000000-0000-0000-0000-000000000000")
