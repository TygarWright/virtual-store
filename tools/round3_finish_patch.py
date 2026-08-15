from pathlib import Path
root=Path('/mnt/data/round3_work')

# product form fields
p=root/'templates/admin/product_form.html'; s=p.read_text()
anchor='''    <div class="field">\n      <label>Compare-at Price (optional)</label>\n'''
addition='''    <div class="field">\n      <label>Cost Price (internal)</label>\n      <input type="number" name="cost_price" min="0" step="1" value="{{ product.cost_price|default(0, true) if product else 0 }}">\n      <small>What this item costs the business. Customers never see this. Used to protect minimum margins.</small>\n    </div>\n    <div class="field">\n      <label>Minimum Margin (%)</label>\n      <input type="number" name="min_margin_percent" min="0" max="100" step="1" value="{{ product.min_margin_percent|default(15, true) if product else 15 }}">\n      <small>Promotions cannot reduce the known margin below this level.</small>\n    </div>\n'''
if 'name="cost_price"' not in s:
    s=s.replace(anchor, addition+anchor)
p.write_text(s)

# admin _save_product persists cost/margin
p=root/'blueprints/admin.py'; s=p.read_text()
s=s.replace('    quantity_raw = request.form.get("quantity", "0").strip()\n', '    quantity_raw = request.form.get("quantity", "0").strip()\n    cost_price_raw = request.form.get("cost_price", "0").strip()\n    min_margin_raw = request.form.get("min_margin_percent", "15").strip()\n')
s=s.replace('''    # Validate compare_price\n    compare_price = None\n''','''    try:\n        cost_price = max(0, int(float(cost_price_raw or 0)))\n        min_margin_percent = min(100, max(0, int(float(min_margin_raw or 15))))\n    except ValueError:\n        flash("Please enter valid cost/margin values.", "error")\n        return redirect(request.referrer or url_for("admin.admin_products"))\n\n    # Validate compare_price\n    compare_price = None\n''',1)
s=s.replace('''               delivery_content_type=?, ribbon=?, compare_price=?, quantity=? WHERE id=?""",\n            (name, short_description, description, price, category, active,\n             delivery_mode, auto_delivery_content, delivery_content_type,\n             ribbon, compare_price, quantity, product_id),''','''               delivery_content_type=?, ribbon=?, compare_price=?, quantity=?, cost_price=?, min_margin_percent=? WHERE id=?""",\n            (name, short_description, description, price, category, active,\n             delivery_mode, auto_delivery_content, delivery_content_type,\n             ribbon, compare_price, quantity, cost_price, min_margin_percent, product_id),''')
# For insert route, locate INSERT fields
s=s.replace('''               (name, short_description, description, price, category, active, position, created_at,\n                delivery_mode, auto_delivery_content, delivery_content_type, ribbon, compare_price, quantity)\n               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''','''               (name, short_description, description, price, category, active, position, created_at,\n                delivery_mode, auto_delivery_content, delivery_content_type, ribbon, compare_price, quantity, cost_price, min_margin_percent)\n               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''')
# if params tuple in insert exact old form, patch
s=s.replace('''                delivery_mode, auto_delivery_content, delivery_content_type,\n                ribbon, compare_price, quantity))''','''                delivery_mode, auto_delivery_content, delivery_content_type,\n                ribbon, compare_price, quantity, cost_price, min_margin_percent))''')
p.write_text(s)

# API coupon margin + approval
p=root/'admin_api.py'; s=p.read_text()
if 'request_or_validate_approval' not in s:
    s=s.replace('from helpers import (', 'from governance_service import request_or_validate_approval, coupon_discount_with_margin\n\nfrom helpers import (',1)
needle='''    conn = db.get_db()\n    try:\n        cur = conn.execute(\n            """INSERT INTO coupons'''
repl='''    conn = db.get_db()\n    if target_product_id:\n        prod = conn.execute("SELECT price, cost_price, min_margin_percent FROM products WHERE id=?", (target_product_id,)).fetchone()\n        if prod:\n            safe = coupon_discount_with_margin(price=int(prod["price"] or 0), cost_price=int(prod["cost_price"] or 0), discount_type=discount_type, discount_value=discount_value, min_margin_percent=int(prod["min_margin_percent"] or 15))\n            requested = int(round(prod["price"] * discount_value / 100)) if discount_type == "percent" else discount_value\n            if int(prod["cost_price"] or 0) and safe["discount"] < requested:\n                conn.close(); return err(f"Promotion breaches the product minimum margin. Maximum safe discount is ₹{safe['discount']:,}.", 400)\n    action = "promotion.create.percent" if discount_type == "percent" else "promotion.create.flat"\n    approval = request_or_validate_approval(conn, action=action, requested_by=int(g.admin_id), entity="coupon", entity_id=0, amount=discount_value, reason=f"Create coupon {code}", metadata={"code": code, "discount_type": discount_type, "discount_value": discount_value, "target_product_id": target_product_id}, approval_id=int(data.get("approval_id")) if str(data.get("approval_id", "")).isdigit() else None)\n    if not approval["allowed"]:\n        conn.close(); return err(f"Second-person approval required. Approval #{approval['approval_id']} was created.", 409)\n    try:\n        cur = conn.execute(\n            """INSERT INTO coupons'''
if needle in s and 'action = "promotion.create.percent"' not in s:
    s=s.replace(needle,repl,1)
p.write_text(s)

# Reconciliation function in script + admin endpoint
p=root/'reconcile_razorpay.py'; s=p.read_text()
s=s.replace('def main():\n', 'def reconcile():\n')
if 'def main()' not in s:
    s += '\n\ndef main():\n    reconcile()\n'
p.write_text(s)

# Add admin API endpoints for escalation and provider reconciliation
p=root/'admin_api.py'; s=p.read_text()
marker='@admin_api.route("/governance/simulate", methods=["POST"])\n'
block='''@admin_api.route("/governance/exceptions/<int:exception_id>/assign", methods=["POST"])\n@api_requires_permission("audit.view")\ndef governance_assign_exception(exception_id):\n    payload = request.get_json(silent=True) or {}\n    assignee = payload.get("assigned_to")\n    due_at = str(payload.get("due_at", "")).strip() or None\n    if assignee in (None, ""):\n        return err("assigned_to is required.", 400)\n    conn = db.get_db()\n    row = conn.execute("SELECT id FROM admin_users WHERE id=? AND is_active=1", (int(assignee),)).fetchone()\n    if not row:\n        conn.close(); return err("Active admin not found.", 404)\n    conn.execute("UPDATE business_exceptions SET assigned_to=?, due_at=?, updated_at=? WHERE id=?", (int(assignee), due_at, db.now(), exception_id))\n    conn.commit(); conn.close()\n    return ok({"assigned_to": int(assignee), "due_at": due_at})\n\n\n@admin_api.route("/governance/exceptions/escalate", methods=["POST"])\n@api_requires_permission("audit.view")\ndef governance_escalate_exceptions():\n    from governance_service import escalate_overdue_exceptions\n    conn = db.get_db(); count = escalate_overdue_exceptions(conn); conn.close()\n    return ok({"escalated": count})\n\n\n@admin_api.route("/governance/razorpay-reconcile", methods=["POST"])\n@api_requires_permission("audit.view")\ndef governance_razorpay_reconcile():\n    from reconcile_razorpay import reconcile\n    try:\n        result = reconcile()\n    except Exception as exc:\n        return err(f"Provider reconciliation failed: {exc}", 502)\n    return ok(result or {"status": "completed"})\n\n\n'''
if '/governance/razorpay-reconcile' not in s:
    s=s.replace(marker, block+marker)
p.write_text(s)

# Enhance reconcile return value
p=root/'reconcile_razorpay.py'; s=p.read_text()
s=s.replace('''    logger.info("Reconciliation complete: repaired=%s mismatches=%s scanned=%s", fixed, mismatches, len(orders))\n''','''    result = {"repaired": fixed, "mismatches": mismatches, "scanned": len(orders)}\n    logger.info("Reconciliation complete: %s", result)\n    return result\n''')
p.write_text(s)

# Guardian scan should escalate overdue issues
p=root/'governance_service.py'; s=p.read_text()
s=s.replace('''def run_guardian_scan(conn) -> Dict[str, Any]:\n    created = scan_exceptions(conn)\n''','''def run_guardian_scan(conn) -> Dict[str, Any]:\n    created = scan_exceptions(conn)\n    escalated = escalate_overdue_exceptions(conn)\n''')
s=s.replace('''    return {"created": created, "open_exceptions": sum(int(r[1]) for r in open_rows),\n            "risk_score": score, "severity_counts": {str(r[0]): int(r[1]) for r in open_rows}}\n''','''    return {"created": created, "escalated": escalated, "open_exceptions": sum(int(r[1]) for r in open_rows),\n            "risk_score": score, "severity_counts": {str(r[0]): int(r[1]) for r in open_rows}}\n''')
p.write_text(s)
print('finished patch')
