"""Rollup #350 §4.1 — emit FHIR R5 corpus for HL7 validator gating.

Scope narrowed per Rule 15 A (mirrors termbank #363, request.pdhc
#377, ips.pdhc #390): emit ONLY the CapabilityStatement for now.
Contract-resource shape polish is a separate follow-up if the
validator ever flags real R4→R5 drift there.

Boots the self-contained Flask app on an in-memory SQLite. No
external HTTP calls.

Run:  python tests/conformance_corpus_emit.py [out_dir]
"""
from __future__ import annotations

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.dirname(HERE)
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)


def _bootstrap_env() -> None:
    os.environ.setdefault("FLASK_ENV", "development")
    os.environ.setdefault("AUTH_DISABLED", "true")
    os.environ.setdefault("JWT_SECRET_KEY", "corpus-emit-not-secret")
    os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")
    os.environ.setdefault("BOOTSTRAP_ADMIN_USERNAME", "admin")
    os.environ.setdefault("BOOTSTRAP_ADMIN_PASSWORD", "corpus-emit-placeholder")
    os.environ.setdefault("INTERNAL_SERVICE_KEY", "corpus-emit-internal")


def _write(out_dir: str, name: str, body: dict) -> None:
    path = os.path.join(out_dir, f"{name}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(body, f, indent=2, ensure_ascii=False, sort_keys=True)
    print(f"  wrote {path}")


def emit_corpus(out_dir: str) -> None:
    os.makedirs(out_dir, exist_ok=True)
    _bootstrap_env()

    from app.main import create_app
    app = create_app()
    client = app.test_client()

    print(f"Emitting FHIR R5 corpus → {out_dir}")

    cs = client.get("/fhir/metadata").get_json()
    _write(out_dir, "capability_statement", cs)

    n = len([f for f in os.listdir(out_dir) if f.endswith(".json")])
    print(f"Done — {n} JSON files.")


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "fhir_corpus")
    emit_corpus(out)
