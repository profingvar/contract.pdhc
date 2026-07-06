"""Access-model reform identity helpers (M0 #414).

Kept in its own module (no config / app imports) so it is trivially unit-
testable without the Flask app's required-env-var bootstrap.
"""


def care_unit_guids_from_blob(blob: dict) -> list:
    """The caller's Zone-1 care units, derived from ``affiliations[].care_unit_guid``.

    Carried into contract's local JWT alongside the dual-emitted legacy
    ``organization_ids`` so the reform identity is available without bloating
    the token with the full affiliation objects. Empty for a pre-reform blob
    (no ``affiliations[]``).
    """
    return [a.get("care_unit_guid")
            for a in (blob.get("affiliations") or [])
            if a.get("care_unit_guid")]
