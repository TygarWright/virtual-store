#!/usr/bin/env python3
"""Dependency-free structural gate for TITAN phases 0-8.
Run in CI before Phase 9 work begins."""
from __future__ import annotations

from pathlib import Path
import ast
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
ERRORS: list[str] = []


def require(path: str, needle: str, label: str) -> None:
    text = (ROOT / path).read_text(encoding="utf-8")
    if needle not in text:
        ERRORS.append(f"{label}: missing {needle!r} in {path}")


def compile_all() -> None:
    for path in ROOT.rglob("*.py"):
        if any(part in {".git", "__pycache__", ".venv"} for part in path.parts):
            continue
        try:
            ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError as exc:
            ERRORS.append(f"syntax: {path}: {exc}")


def main() -> int:
    compile_all()
    required = [
        ("config.py", "SECRET_KEY", "production secret guard"),
        ("extensions.py", "csrf =", "CSRF extension"),
        ("phase2_services.py", "enqueue_outbox_job", "durable outbox"),
        ("workers/outbox_worker.py", "claim_outbox_job", "outbox worker"),
        ("phase2_services.py", "status = 'processing' AND locked_at IS NOT NULL", "expired outbox lease reclaim"),
        ("tests/test_phase2_foundation.py", "test_expired_outbox_lease_is_reclaimable", "outbox crash recovery test"),
        ("blueprints/admin.py", "admin_products_bulk", "admin bulk operations"),
        ("blueprints/admin.py", "admin_orders_bulk_deliver", "admin bulk delivery"),
        ("blueprints/admin.py", "admin_customers", "customer operations"),
        ("blueprints/storefront.py", "per_page = 24", "catalog pagination"),
        ("templates/index.html", 'aria-label="Catalogue pages"', "catalog accessibility"),
        ("templates/product.html", 'schema.org/InStock', "product availability schema"),
        ("render-outbox-worker.yaml", "virtual-store-outbox", "production outbox worker"),
        (".github/workflows/ci.yml", "pip_audit", "dependency security audit"),
        ("app.py", "inject_customer_auth_config", "configured customer-auth context"),
        ("templates/_auth_modal.html", "authStepGoogleOnly", "Google-only auth fallback"),
        ("static/js/auth.js", "authStepGoogleOnly", "Google-only auth navigation"),
        ("app.py", "stale-while-revalidate=86400", "static asset cache policy"),
    ]
    for item in required:
        require(*item)
    admin_source = (ROOT / "blueprints/admin.py").read_text(encoding="utf-8")
    if '@admin_bp.route("//' in admin_source:
        ERRORS.append("admin routes: double-slash route decorators remain")

    forbidden_patterns = [
        r"INITIAL_ADMIN_PASSWORD",
        r"store\.db\.backup",
        r"\.bak\d*",
    ]
    for path in ROOT.rglob("*"):
        if not path.is_file() or any(p in {".git", ".venv", "__pycache__"} for p in path.parts):
            continue
        if path.suffix.lower() in {".db", ".pyc"}:
            ERRORS.append(f"forbidden artifact: {path.relative_to(ROOT)}")
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        for pattern in forbidden_patterns:
            if re.search(pattern, str(path)):
                ERRORS.append(f"forbidden artifact name: {path.relative_to(ROOT)}")
                break
    if ERRORS:
        print("PRE-9 TITAN GATE: FAIL")
        print("\n".join(f"- {e}" for e in ERRORS))
        return 1
    print("PRE-9 TITAN GATE: PASS — phases 0-8 structural safeguards present")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
