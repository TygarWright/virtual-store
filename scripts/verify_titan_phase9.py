"""Static Phase 9 gate. Does not pretend to replace runtime/staging tests."""
from pathlib import Path
import ast

ROOT = Path(__file__).resolve().parents[1]

def contains(path, needle):
    return needle in (ROOT / path).read_text(encoding="utf-8")

def py_ok(path):
    ast.parse((ROOT / path).read_text(encoding="utf-8"), filename=path)

required = {
    "intelligence_service.py": [
        "get_recommendations", "get_personalized_recommendations", "rank_products",
        "get_business_insights", "detect_anomalies", "inventory_forecast",
        "assistant_answer", "_optional_llm_answer",
    ],
    "blueprints/admin.py": ["admin_insights", "admin_intelligence_ask"],
    "blueprints/storefront.py": ["api_recommendations", "rank_products", "record_event"],
    "database.py": ["analytics_events", "idx_analytics_event_type_created"],
    "config.py": ["INTELLIGENCE_API_URL", "INTELLIGENCE_API_KEY", "INTELLIGENCE_MODEL"],
    "templates/admin/insights.html": ["Store Intelligence", "intelAsk", "Inventory forecast", "Detected anomalies"],
}
for file, needles in required.items():
    for needle in needles:
        assert contains(file, needle), f"missing {needle} in {file}"
for path in ["intelligence_service.py", "blueprints/admin.py", "blueprints/storefront.py", "models.py", "database.py", "config.py"]:
    py_ok(path)
# No unresolved implementation markers in Phase 9 core.
for path in ["intelligence_service.py", "blueprints/admin.py", "blueprints/storefront.py", "templates/admin/insights.html"]:
    text=(ROOT/path).read_text(encoding="utf-8").lower()
    assert "todo" not in text and "fixme" not in text, f"unfinished marker in {path}"
print("PHASE 9 STATIC GATE: PASS")
