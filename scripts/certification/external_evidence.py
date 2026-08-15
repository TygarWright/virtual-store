#!/usr/bin/env python3
"""Register and verify external Phase 10 evidence files.

The registry deliberately requires a real evidence artifact plus a SHA-256 hash,
execution environment, timestamp, and reviewer. It never upgrades evidence to
PASS merely because a row exists.
"""
from __future__ import annotations

import argparse, hashlib, json, os, sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REG = ROOT / "reports" / "PHASE10_EXTERNAL_EVIDENCE.json"
ITEMS = [
    "razorpay_checkout_refund_webhook",
    "browser_mobile_accessibility",
    "concurrency_load_failure_injection",
    "staging_migration_restore",
    "deployment_rollback",
    "adversarial_security",
    "production_operations",
    "performance_load",
]


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def load() -> dict:
    if not REG.exists():
        return {"schema_version": 1, "items": {}}
    return json.loads(REG.read_text(encoding="utf-8"))


def save(data: dict) -> None:
    REG.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def cmd_register(args: argparse.Namespace) -> int:
    artifact = Path(args.artifact).resolve()
    if not artifact.is_file():
        print(f"evidence artifact not found: {artifact}", file=sys.stderr)
        return 2
    if args.status != "VERIFIED":
        print("external evidence may only be registered as VERIFIED through this command", file=sys.stderr)
        return 2
    data = load()
    digest = sha256(artifact)
    item = {
        "status": "VERIFIED",
        "artifact": os.path.relpath(artifact, ROOT),
        "sha256": digest,
        "captured_at": args.captured_at or datetime.now(timezone.utc).isoformat(),
        "environment": args.environment,
        "reviewer": args.reviewer,
    }
    data.setdefault("items", {})[args.item] = item
    data["schema_version"] = 1
    save(data)
    print(json.dumps(item, indent=2, sort_keys=True))
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    data = load()
    errors = []
    items = data.get("items", {})
    for item in ITEMS:
        rec = items.get(item)
        if not rec or rec.get("status") != "VERIFIED":
            errors.append(f"{item}: missing VERIFIED evidence")
            continue
        artifact = ROOT / rec.get("artifact", "")
        if not artifact.is_file():
            errors.append(f"{item}: artifact missing")
            continue
        actual = sha256(artifact)
        if actual != rec.get("sha256"):
            errors.append(f"{item}: SHA-256 mismatch")
        for field in ("captured_at", "environment", "reviewer"):
            if not rec.get(field):
                errors.append(f"{item}: missing {field}")
    if errors:
        print("EXTERNAL_EVIDENCE_VERIFY: FAIL")
        for e in errors: print(f"- {e}")
        return 1
    print("EXTERNAL_EVIDENCE_VERIFY: PASS")
    return 0


def main() -> int:
    p = argparse.ArgumentParser()
    s = p.add_subparsers(dest="cmd", required=True)
    r = s.add_parser("register")
    r.add_argument("item", choices=ITEMS)
    r.add_argument("artifact")
    r.add_argument("--environment", required=True)
    r.add_argument("--reviewer", required=True)
    r.add_argument("--captured-at")
    r.add_argument("--status", default="VERIFIED")
    r.set_defaults(fn=cmd_register)
    v = s.add_parser("verify")
    v.set_defaults(fn=cmd_verify)
    a = s.add_parser("init")
    a.set_defaults(fn=lambda args: (save({"schema_version": 1, "items": {}}) or print(REG) or 0))
    args = p.parse_args()
    return int(args.fn(args))

if __name__ == "__main__":
    raise SystemExit(main())
