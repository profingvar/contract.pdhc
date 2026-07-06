"""M0 #414 — contract adopts reform org identity (care_unit_guids from affiliations[]).

contract carries org identity in its local JWT (it does not enforce an org
filter). The reform derives the Zone-1 care units from affiliations[], carried
alongside the dual-emitted legacy organization_ids. The helper lives in a
config-free module so this needs no app env bootstrap.
"""
from app.reform_identity import care_unit_guids_from_blob


def test_derives_care_units_from_affiliations():
    blob = {
        "affiliations": [
            {"care_unit_guid": "unit-a", "role": "doctor"},
            {"care_unit_guid": "unit-b", "role": "nurse"},
        ],
        "organization_ids": ["unit-a", "unit-b"],
    }
    assert care_unit_guids_from_blob(blob) == ["unit-a", "unit-b"]


def test_empty_for_pre_reform_blob():
    # A pre-reform token has no affiliations[] — helper yields [].
    assert care_unit_guids_from_blob({"organization_ids": ["x"]}) == []


def test_skips_entries_without_care_unit_guid():
    blob = {"affiliations": [{"role": "doctor"}, {"care_unit_guid": "u"}]}
    assert care_unit_guids_from_blob(blob) == ["u"]
