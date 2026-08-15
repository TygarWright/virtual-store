"""Round 30 gates for Observability and Design System mastery."""
from pathlib import Path
import ast, re
ROOT=Path(__file__).resolve().parents[1]
ob=(ROOT/"observability_service.py").read_text()
ad=(ROOT/"blueprints/admin.py").read_text()
tpl=(ROOT/"templates/admin/observability.html").read_text()
css=(ROOT/"static/css/titan-ui.css").read_text()
assert "def emit_alert" in ob and "observability_alert" in ob
assert "operation=operation or None" in ad
assert "summary.slowest" in tpl
for token in ["--titan-space-1","--titan-control-md","--titan-focus-ring",".titan-stack",".titan-cluster",".titan-surface",".titan-divider",".titan-control",".titan-visually-hidden",".titan-virtual-list"]:
    assert token in css, token
# UI must not introduce emoji as navigation/status chrome in admin templates.
for p in (ROOT/"templates/admin").glob("*.html"):
    text=p.read_text()
    assert not re.search(r"[\U0001F300-\U0001FAFF]", text), p
for p in ROOT.rglob("*.py"):
    ast.parse(p.read_text())
print("ROUND30_GATE: PASS")
