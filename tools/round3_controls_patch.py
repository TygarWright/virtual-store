from pathlib import Path
root=Path('/mnt/data/round3_work')

# revise policy seed
p=root/'database.py'; s=p.read_text()
s=s.replace("        ('promotion.create', 20, 1, 1440, 1),", "        ('promotion.create.percent', 20, 1, 1440, 1),\n        ('promotion.create.flat', 500, 1, 1440, 1),")
p.write_text(s)

# admin web imports
p=root/'blueprints/admin.py'; s=p.read_text()
old='from phase2_services import (\n'
if 'from governance_service import (' not in s:
    s=s.replace(old, 'from governance_service import (\n    request_or_validate_approval, coupon_discount_with_margin, escalate_overdue_exceptions,\n    policy_requires_approval,\n)\n'+old)
# refund route inject approval after amount validation
needle='    if refund_amt > int(order["amount"]):\n        flash("Refund amount cannot exceed the order amount.", "error")\n        conn.close()\n        return redirect(url_for("admin.admin_order_detail", order_id=order_id))\n\n'
repl=needle+'''    approval_id_raw = request.form.get("approval_id", "").strip()\n    approval = request_or_validate_approval(\n        conn, action="order.refund", requested_by=int(session["admin_id"]), entity="order",\n        entity_id=order_id, amount=refund_amt, reason="Admin refund/cancellation",\n        metadata={"refund_amount": refund_amt, "order_ref": order["order_ref"]},\n        approval_id=int(approval_id_raw) if approval_id_raw.isdigit() else None,\n    ) if refund_amt > 0 else {"allowed": True}\n    if not approval["allowed"]:\n        conn.close()\n        flash(f"Refund approval required before this action can proceed. Approval #{approval['approval_id']} was created.", "warning")\n        return redirect(url_for("admin.admin_order_detail", order_id=order_id))\n\n'''
if needle in s and 'approval = request_or_validate_approval' not in s:
    s=s.replace(needle,repl)
# coupon save: after usage/min calculations, do margin check and approval
needle2='    # If auto_apply is checked, set trigger_type appropriately\n    if auto_apply and trigger_type == "manual":\n        trigger_type = "cart_threshold"  # sensible default\n\n    conn = db.get_db()\n'
repl2='''    # If auto_apply is checked, set trigger_type appropriately\n    if auto_apply and trigger_type == "manual":\n        trigger_type = "cart_threshold"  # sensible default\n\n    conn = db.get_db()\n    if target_product_id:\n        product_for_margin = conn.execute("SELECT price, cost_price, min_margin_percent FROM products WHERE id=?", (target_product_id,)).fetchone()\n        if product_for_margin:\n            safe = coupon_discount_with_margin(price=int(product_for_margin["price"] or 0), cost_price=int(product_for_margin["cost_price"] or 0), discount_type=discount_type, discount_value=discount_value, min_margin_percent=int(product_for_margin["min_margin_percent"] or 15))\n            if safe["discount"] < (discount_value if discount_type == "flat" else int(round(product_for_margin["price"] * discount_value / 100))):\n                flash(f"This promotion would breach the product's minimum margin. Maximum safe discount is ₹{safe['discount']:,}.", "error")\n                conn.close()\n                return redirect(url_for("admin.admin_coupons"))\n    action = "promotion.create.percent" if discount_type == "percent" else "promotion.create.flat"\n    approval = request_or_validate_approval(conn, action=action, requested_by=int(session["admin_id"]), entity="coupon", entity_id=0, amount=discount_value, reason=f"Create coupon {code}", metadata={"code": code, "discount_type": discount_type, "discount_value": discount_value, "target_product_id": target_product_id}, approval_id=int(request.form.get("approval_id", "0")) if request.form.get("approval_id", "0").isdigit() else None)\n    if not approval["allowed"]:\n        conn.close()\n        flash(f"This promotion requires a second-person approval. Approval #{approval['approval_id']} was created.", "warning")\n        return redirect(url_for("admin.admin_coupons"))\n'''
if needle2 in s and 'action = "promotion.create.percent"' not in s:
    s=s.replace(needle2,repl2)
# Inventory change in _save_product: identify old qty after conn creation, then approval
needle3='    conn = db.get_db()\n    # Count current product files for auto-detect\n'
repl3='''    conn = db.get_db()\n    existing_qty = 0\n    if product_id:\n        old_product = conn.execute("SELECT quantity FROM products WHERE id=?", (product_id,)).fetchone()\n        existing_qty = int(old_product["quantity"] or 0) if old_product else 0\n        qty_delta = abs(quantity - existing_qty)\n        approval = request_or_validate_approval(conn, action="inventory.adjust", requested_by=int(session["admin_id"]), entity="product", entity_id=int(product_id), amount=qty_delta, reason=f"Inventory change for {name}", metadata={"old_quantity": existing_qty, "new_quantity": quantity, "product_id": int(product_id)}, approval_id=int(request.form.get("approval_id", "0")) if request.form.get("approval_id", "0").isdigit() else None) if qty_delta else {"allowed": True}\n        if not approval["allowed"]:\n            conn.close()\n            flash(f"This inventory adjustment requires second-person approval. Approval #{approval['approval_id']} was created.", "warning")\n            return redirect(request.referrer or url_for("admin.admin_products"))\n    # Count current product files for auto-detect\n'''
if needle3 in s and 'qty_delta = abs(quantity - existing_qty)' not in s:
    s=s.replace(needle3,repl3,1)
p.write_text(s)

# storefront margin enforcement in both coupon endpoints
p=root/'blueprints/storefront.py'; s=p.read_text()
if 'from governance_service import coupon_discount_with_margin' not in s:
    # insert after imports block using a known import
    s=s.replace('from payment.inventory import', 'from governance_service import coupon_discount_with_margin\nfrom payment.inventory import') if 'from payment.inventory import' in s else s
# replace main checkout discount block
old='''        if coupon["discount_type"] == "percent":\n            discount_amount = int(round(product["price"] * coupon["discount_value"] / 100))\n        else:\n            discount_amount = coupon["discount_value"]\n        discount_amount = min(discount_amount, product["price"] - 1) if product["price"] > 0 else 0\n        discount_amount = max(discount_amount, 0)\n        final_amount = product["price"] - discount_amount\n'''
new='''        margin_result = coupon_discount_with_margin(\n            price=int(product["price"] or 0), cost_price=int(product["cost_price"] or 0),\n            discount_type=coupon["discount_type"], discount_value=int(coupon["discount_value"] or 0),\n            min_margin_percent=int(product["min_margin_percent"] or 15),\n        )\n        requested_discount = int(round(product["price"] * coupon["discount_value"] / 100)) if coupon["discount_type"] == "percent" else int(coupon["discount_value"] or 0)\n        if int(product["cost_price"] or 0) > 0 and margin_result["discount"] < requested_discount:\n            conn.close()\n            return jsonify({"error": "That coupon exceeds the product's protected minimum margin."}), 400\n        discount_amount = margin_result["discount"]\n        final_amount = margin_result["final_price"]\n'''
if old in s:
    s=s.replace(old,new,1)
old2='''    if coupon["discount_type"] == "percent":\n        discount = int(round(product["price"] * coupon["discount_value"] / 100))\n    else:\n        discount = coupon["discount_value"]\n    discount = max(0, min(discount, product["price"] - 1 if product["price"] > 0 else 0))\n    final_price = product["price"] - discount\n'''
new2='''    margin_result = coupon_discount_with_margin(\n        price=int(product["price"] or 0), cost_price=int(product["cost_price"] or 0),\n        discount_type=coupon["discount_type"], discount_value=int(coupon["discount_value"] or 0),\n        min_margin_percent=int(product["min_margin_percent"] or 15),\n    )\n    requested_discount = int(round(product["price"] * coupon["discount_value"] / 100)) if coupon["discount_type"] == "percent" else int(coupon["discount_value"] or 0)\n    if int(product["cost_price"] or 0) > 0 and margin_result["discount"] < requested_discount:\n        return jsonify({"error": "That coupon exceeds the product's protected minimum margin."}), 400\n    discount = margin_result["discount"]\n    final_price = margin_result["final_price"]\n'''
if old2 in s:
    s=s.replace(old2,new2,1)
p.write_text(s)

print('patched controls')
