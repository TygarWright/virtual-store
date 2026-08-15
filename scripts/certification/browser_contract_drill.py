"""Repository-level browser/admin contract drill.

Does not pretend to be a browser. It statically verifies that the HTML/template
contract required by the Phase 10 browser certification exists and that common
UI regressions are absent before running a real-device/browser pass.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TEMPLATES = ROOT / "templates"


def all_template_text() -> str:
    return "\n".join(p.read_text(encoding="utf-8", errors="ignore") for p in TEMPLATES.rglob("*.html"))


def main() -> int:
    text = all_template_text()
    checks: dict[str, str] = {}

    # Admin login must expose a CSRF token field.
    login_candidates = list((TEMPLATES / "admin").glob("*login*.html"))
    login_text = "\n".join(p.read_text(encoding="utf-8", errors="ignore") for p in login_candidates)
    checks["admin_login_csrf"] = "PASS" if ("csrf_token" in login_text or "csrf" in login_text.lower()) else "FAIL"

    # Professional UI contract: no blocking prompt() usage in templates.
    checks["no_blocking_prompt"] = "PASS" if not re.search(r"\bprompt\s*\(", text) else "FAIL"

    # No emoji pictograms in admin templates.
    admin_text = "\n".join(p.read_text(encoding="utf-8", errors="ignore") for p in (TEMPLATES / "admin").rglob("*.html"))
    checks["no_admin_emoji_ui"] = "PASS" if not re.search(r"[\U0001F300-\U0001FAFF]", admin_text) else "FAIL"

    # Required mature operational surfaces remain present.
    required_fragments = {
        "guardian": ["guardian"],
        "team_hub": ["team"],
        "support_cockpit": ["support"],
        "observability": ["observability"],
        "simulation_lab": ["simulation"],
        "training": ["training"],
    }
    for name, needles in required_fragments.items():
        checks[f"surface_{name}"] = "PASS" if all(n.lower() in text.lower() for n in needles) else "FAIL"

    # Navigation should have active-state primitives rather than relying solely on color.
    checks["active_nav_primitive"] = "PASS" if ("active" in admin_text.lower() and ("aria-current" in admin_text or "nav" in admin_text.lower())) else "FAIL"

    failed = [k for k, v in checks.items() if v != "PASS"]
    if failed:
        raise SystemExit(f"BROWSER_CONTRACT_DRILL: FAIL {failed}")

    print("BROWSER_CONTRACT_DRILL: PASS")
    print(json.dumps(checks, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
