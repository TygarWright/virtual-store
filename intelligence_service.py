"""Virtual Store Phase 9 intelligence engine.

Deterministic, privacy-conscious intelligence that works without an external AI
provider. It provides recommendations, better search ranking, business
insights, anomaly detection, inventory forecasting, and a read-only natural-
language admin assistant. Any optional future LLM can sit on top of these
trusted facts; it must never become the source of commerce truth.
"""
from __future__ import annotations

import math
import re
import json
import urllib.request
import urllib.error
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Tuple

import database as db


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso_days_ago(days: int) -> str:
    return (_now() - timedelta(days=days)).isoformat()


def record_event(event_type: str, *, product_id: int | None = None,
                 customer_id: int | None = None, session_id: str = "",
                 query: str = "", metadata: Dict[str, Any] | None = None,
                 request=None) -> None:
    """Record non-sensitive product/search behavior with bounded metadata."""
    meta = metadata or {}
    safe = {str(k): str(v)[:200] for k, v in meta.items() if k not in {"email", "phone", "password", "token"}}
    ip = ""
    ua = ""
    if request is not None:
        ip = str(getattr(request, "remote_addr", "") or "")[:64]
        ua = str(request.headers.get("User-Agent", ""))[:256]
    conn = db.get_db()
    conn.execute(
        """INSERT INTO analytics_events
           (event_type, product_id, customer_id, session_id, query, metadata_json, ip_address, user_agent, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (event_type, product_id, customer_id, session_id[:128], query[:200], json.dumps(safe, separators=(",", ":"), sort_keys=True), ip, ua, db.now()),
    )
    conn.commit()
    conn.close()


def _tokenize(text: str) -> List[str]:
    return [t for t in re.findall(r"[\w-]+", (text or "").casefold()) if len(t) >= 2]


def rank_products(products: List[Dict[str, Any]], query: str) -> List[Dict[str, Any]]:
    """Rank products using field relevance + popularity + stock signals."""
    tokens = _tokenize(query)
    if not tokens:
        return sorted(products, key=lambda p: (-int(p.get("views") or 0), str(p.get("name") or "").casefold()))
    ranked = []
    for p in products:
        name = str(p.get("name") or "").casefold()
        desc = str(p.get("short_description") or "").casefold()
        cat = str(p.get("category") or "").casefold()
        score = 0.0
        matches = 0
        for token in tokens:
            if name == token:
                score += 60
                matches += 1
            elif name.startswith(token):
                score += 30
                matches += 1
            elif token in name:
                score += 20
                matches += 1
            elif token in cat:
                score += 10
                matches += 1
            elif token in desc:
                score += 5
                matches += 1
        if matches:
            views = min(int(p.get("views") or 0), 10000)
            score += math.log1p(views) * 1.5
            qty = p.get("quantity")
            if qty is None or int(qty) > 0:
                score += 1.5
            ranked.append((score, str(p.get("name") or "").casefold(), p))
    ranked.sort(key=lambda x: (-x[0], x[1]))
    return [p for _, _, p in ranked]


def get_recommendations(product_id: int, limit: int = 8) -> List[Dict[str, Any]]:
    """Co-purchase recommendations, then category/popularity fallback."""
    limit = max(1, min(int(limit), 24))
    conn = db.get_db()
    rows = conn.execute(
        """SELECT oi2.product_id, COUNT(*) AS pair_count
           FROM order_items oi1
           JOIN orders o1 ON o1.id = oi1.order_id
           JOIN order_items oi2 ON oi2.order_id = oi1.order_id
           WHERE oi1.product_id = ? AND oi2.product_id != ?
             AND o1.status IN ('paid','delivered','completed')
           GROUP BY oi2.product_id
           ORDER BY pair_count DESC
           LIMIT ?""", (product_id, product_id, limit * 2)
    ).fetchall()
    ids = [int(r["product_id"]) for r in rows]
    products = []
    if ids:
        placeholders = ",".join("?" for _ in ids)
        products = conn.execute(
            f"SELECT id, name, slug, category, price, compare_price, quantity, views FROM products WHERE active=1 AND id IN ({placeholders})",
            ids,
        ).fetchall()
    result_by_id = {int(r["id"]): dict(r) for r in products}
    result = [result_by_id[i] for i in ids if i in result_by_id]
    if len(result) < limit:
        base = conn.execute(
            "SELECT id, name, slug, category, price, compare_price, quantity, views FROM products WHERE active=1 AND id != ? ORDER BY views DESC, position ASC LIMIT ?",
            (product_id, limit * 2),
        ).fetchall()
        seen = {int(r["id"]) for r in result}
        current = conn.execute("SELECT category FROM products WHERE id=?", (product_id,)).fetchone()
        category = current["category"] if current else ""
        category_rows = [dict(r) for r in base if int(r["id"]) not in seen and (not category or r["category"] == category)]
        other_rows = [dict(r) for r in base if int(r["id"]) not in seen and dict(r) not in category_rows]
        result.extend(category_rows[: max(0, limit-len(result))])
        if len(result) < limit:
            result.extend(other_rows[: max(0, limit-len(result))])
    conn.close()
    return result[:limit]


def search_catalog(query: str, *, limit: int = 20) -> List[Dict[str, Any]]:
    conn = db.get_db()
    rows = conn.execute(
        "SELECT id, name, slug, short_description, category, price, quantity, views FROM products WHERE active=1"
    ).fetchall()
    conn.close()
    ranked = rank_products([dict(r) for r in rows], query)
    return ranked[: max(1, min(int(limit), 50))]


def _sales_series(days: int = 30) -> List[Dict[str, Any]]:
    conn = db.get_db()
    rows = conn.execute(
        """SELECT substr(created_at,1,10) AS day,
                  COUNT(*) AS orders,
                  COALESCE(SUM(amount),0) AS revenue
           FROM orders
           WHERE created_at >= ?
             AND status IN ('paid','delivered','completed')
             AND COALESCE(payment_mode,'gateway') != 'test'
           GROUP BY day ORDER BY day""", (_iso_days_ago(days),)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_business_insights(days: int = 30) -> Dict[str, Any]:
    days = max(7, min(int(days), 365))
    conn = db.get_db()
    row = conn.execute(
        """SELECT COUNT(*) AS orders,
                  COALESCE(SUM(amount),0) AS revenue,
                  COALESCE(AVG(amount),0) AS avg_order
           FROM orders
           WHERE created_at >= ?
             AND status IN ('paid','delivered','completed')
             AND COALESCE(payment_mode,'gateway') != 'test'""", (_iso_days_ago(days),)
    ).fetchone()
    failed = conn.execute(
        """SELECT COUNT(*) AS c FROM orders WHERE created_at >= ? AND status IN ('payment_failed','delivery_failed','refund_failed')""",
        (_iso_days_ago(days),),
    ).fetchone()["c"]
    top = conn.execute(
        """SELECT oi.product_name, SUM(oi.quantity) AS units, COALESCE(SUM(oi.line_total),0) AS revenue
           FROM order_items oi JOIN orders o ON o.id=oi.order_id
           WHERE o.created_at >= ? AND o.status IN ('paid','delivered','completed')
             AND COALESCE(o.payment_mode,'gateway') != 'test'
           GROUP BY oi.product_id, oi.product_name ORDER BY revenue DESC LIMIT 10""", (_iso_days_ago(days),)
    ).fetchall()
    conn.close()
    series = _sales_series(days)
    avg_daily = (sum(float(x["revenue"]) for x in series) / max(1, days))
    return {
        "period_days": days,
        "orders": int(row["orders"]),
        "revenue": int(row["revenue"]),
        "avg_order": round(float(row["avg_order"] or 0), 2),
        "failed_operations": int(failed),
        "failure_rate": round(float(failed) / max(1, int(row["orders"]) + int(failed)), 4),
        "avg_daily_revenue": round(avg_daily, 2),
        "top_products": [dict(r) for r in top],
        "daily": series,
    }


def detect_anomalies(days: int = 30) -> List[Dict[str, Any]]:
    series = _sales_series(days)
    revenues = [float(x["revenue"]) for x in series]
    alerts: List[Dict[str, Any]] = []
    if len(revenues) >= 7:
        baseline = revenues[:-1]
        mean = sum(baseline) / len(baseline)
        variance = sum((x - mean) ** 2 for x in baseline) / max(1, len(baseline))
        std = math.sqrt(variance)
        latest = revenues[-1]
        if std > 0 and abs(latest - mean) >= max(2 * std, mean * 0.6):
            alerts.append({"type": "revenue_anomaly", "severity": "high", "message": f"Latest daily revenue ({latest:.0f}) is unusually {'high' if latest > mean else 'low'} versus the recent baseline ({mean:.0f})."})
    conn = db.get_db()
    failed = conn.execute(
        "SELECT COUNT(*) AS c FROM orders WHERE created_at >= ? AND status IN ('payment_failed','delivery_failed','refund_failed')",
        (_iso_days_ago(days),),
    ).fetchone()["c"]
    paid = conn.execute(
        "SELECT COUNT(*) AS c FROM orders WHERE created_at >= ? AND status IN ('paid','delivered','completed') AND COALESCE(payment_mode,'gateway')!='test'",
        (_iso_days_ago(days),),
    ).fetchone()["c"]
    conn.close()
    rate = failed / max(1, failed + paid)
    if failed >= 3 and rate >= 0.08:
        alerts.append({"type": "failure_anomaly", "severity": "high", "message": f"Failure rate is {rate:.1%} over the last {days} days; review payment/delivery failures."})
    return alerts


def inventory_forecast(days: int = 14) -> List[Dict[str, Any]]:
    days = max(7, min(int(days), 90))
    conn = db.get_db()
    rows = conn.execute(
        """SELECT p.id, p.name, p.quantity,
                  COALESCE(SUM(CASE WHEN o.status IN ('paid','delivered','completed') THEN oi.quantity ELSE 0 END),0) AS units_sold
           FROM products p LEFT JOIN order_items oi ON oi.product_id=p.id
           LEFT JOIN orders o ON o.id=oi.order_id AND o.created_at >= ?
           WHERE p.active=1 GROUP BY p.id ORDER BY units_sold DESC, p.name COLLATE NOCASE""", (_iso_days_ago(days),)
    ).fetchall()
    conn.close()
    result=[]
    for r in rows:
        sold = int(r["units_sold"] or 0)
        daily = sold / days
        qty = r["quantity"]
        days_left = None if qty is None or daily <= 0 else round(float(qty) / daily, 1)
        risk = "healthy"
        if qty is not None and qty <= 0:
            risk = "out_of_stock"
        elif days_left is not None and days_left <= 3:
            risk = "critical"
        elif days_left is not None and days_left <= 7:
            risk = "warning"
        result.append({"id": r["id"], "name": r["name"], "quantity": qty, "units_sold": sold, "daily_velocity": round(daily, 2), "estimated_days_left": days_left, "risk": risk})
    return result



def get_personalized_recommendations(customer_id: int | None, *, limit: int = 8) -> List[Dict[str, Any]]:
    """Personalized recommendations from a customer's recent orders and wishlist.

    No sensitive attributes are used: only product IDs and commerce behavior.
    """
    limit = max(1, min(int(limit), 24))
    conn = db.get_db()
    if not customer_id:
        rows = conn.execute("SELECT id,name,slug,category,price,compare_price,quantity,views FROM products WHERE active=1 ORDER BY views DESC, position ASC LIMIT ?", (limit,)).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    seed = conn.execute(
        """SELECT product_id FROM (
             SELECT oi.product_id AS product_id, MAX(o.created_at) AS ts
             FROM order_items oi JOIN orders o ON o.id=oi.order_id
             WHERE o.customer_id=? AND o.status IN ('paid','delivered','completed')
             GROUP BY oi.product_id
             UNION ALL
             SELECT product_id, MAX(created_at) FROM wishlist_items WHERE customer_id=? GROUP BY product_id
           ) ORDER BY ts DESC LIMIT 12""", (customer_id, customer_id)).fetchall()
    seed_ids = [int(r["product_id"]) for r in seed]
    if not seed_ids:
        rows = conn.execute("SELECT id,name,slug,category,price,compare_price,quantity,views FROM products WHERE active=1 ORDER BY views DESC, position ASC LIMIT ?", (limit,)).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    ph=",".join("?" for _ in seed_ids)
    cats = conn.execute(f"SELECT DISTINCT category FROM products WHERE id IN ({ph}) AND category != ''", seed_ids).fetchall()
    categories=[r["category"] for r in cats]
    out=[]
    if categories:
        cph=",".join("?" for _ in categories)
        out=conn.execute(f"SELECT id,name,slug,category,price,compare_price,quantity,views FROM products WHERE active=1 AND id NOT IN ({ph}) AND category IN ({cph}) ORDER BY views DESC, position ASC LIMIT ?", seed_ids+categories+[limit]).fetchall()
    seen={int(r["id"]) for r in out}
    if len(out)<limit:
        more=conn.execute(f"SELECT id,name,slug,category,price,compare_price,quantity,views FROM products WHERE active=1 AND id NOT IN ({ph}) ORDER BY views DESC, position ASC LIMIT ?", seed_ids+[limit]).fetchall()
        out=list(out)+[r for r in more if int(r["id"]) not in seen][:max(0,limit-len(out))]
    conn.close()
    return [dict(r) for r in out[:limit]]


def _optional_llm_answer(question: str, trusted_facts: Dict[str, Any]) -> Dict[str, Any] | None:
    """Use an optional OpenAI-compatible chat endpoint as a presentation layer only.

    The model receives trusted facts and is explicitly forbidden from inventing or
    mutating commerce data. If configuration or the provider is unavailable, the
    deterministic assistant remains the fallback.
    """
    try:
        import config
        url = getattr(config, "INTELLIGENCE_API_URL", "")
        key = getattr(config, "INTELLIGENCE_API_KEY", "")
        model = getattr(config, "INTELLIGENCE_MODEL", "")
        if not (url and key and model):
            return None
        payload = {"model": model, "temperature": 0.2, "messages": [
            {"role": "system", "content": "You are Virtual Store's read-only business intelligence assistant. Use only the supplied trusted facts. Never invent numbers. Never claim to have changed an order, payment, refund, inventory, or customer record. Be concise."},
            {"role": "user", "content": json.dumps({"question": question[:500], "trusted_facts": trusted_facts}, default=str)},
        ]}
        req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"}, method="POST")
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        text = (((data.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()
        if not text:
            return None
        return {"answer": text[:4000], "facts": trusted_facts, "provider": "optional-llm"}
    except Exception:
        return None


def assistant_answer(question: str) -> Dict[str, Any]:
    """Read-only natural-language analytics assistant with safe deterministic fallback."""
    q = (question or "").strip().casefold()
    if not q:
        return {"answer": "Ask about sales, revenue, orders, products, stock, failures, or anomalies.", "facts": []}
    insights = get_business_insights(30)
    facts: Dict[str, Any] = {"period_days": 30}
    if any(k in q for k in ("revenue", "sales", "earning", "income")):
        facts["insights"] = {k: insights[k] for k in ("orders", "revenue", "avg_order", "failure_rate")}
    elif any(k in q for k in ("top product", "best seller", "best-selling", "selling")):
        facts["top_products"] = insights["top_products"][:5]
    elif any(k in q for k in ("stock", "inventory", "running out", "restock")):
        facts["inventory_risks"] = [x for x in inventory_forecast(14) if x["risk"] != "healthy"][:8]
    elif any(k in q for k in ("anomal", "problem", "issue", "warning", "failure")):
        facts["anomalies"] = detect_anomalies(30)
    else:
        facts["insights"] = {k: insights[k] for k in ("orders", "revenue", "avg_order", "failure_rate")}
        facts["top_products"] = insights["top_products"][:5]
        facts["inventory_risks"] = [x for x in inventory_forecast(14) if x["risk"] != "healthy"][:8]
        facts["anomalies"] = detect_anomalies(30)
    llm = _optional_llm_answer(question, facts)
    if llm:
        return llm
    if "insights" in facts:
        f = facts["insights"]
        return {"answer": f"Over the last 30 days, the store recorded {f['orders']} completed orders and {f['revenue']:,} in revenue, with an average order value of {f['avg_order']:,}.", "facts": facts}
    if "top_products" in facts:
        top = facts["top_products"]
        text = "Top products: " + ("; ".join(f"{x['product_name']} ({x['units']} units)" for x in top) if top else "No completed sales yet.")
        return {"answer": text, "facts": facts}
    if "inventory_risks" in facts:
        risks = facts["inventory_risks"]
        text = "No active stock risks were detected." if not risks else "Stock risks: " + "; ".join(f"{x['name']} — {x['risk']}" for x in risks)
        return {"answer": text, "facts": facts}
    alerts = facts.get("anomalies", [])
    return {"answer": "No significant anomalies were detected." if not alerts else " ".join(a["message"] for a in alerts), "facts": facts}

