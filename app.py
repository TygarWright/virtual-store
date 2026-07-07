from datetime import datetime, timedelta

from flask import (
    Flask, render_template, request, redirect, url_for, session,
    flash, jsonify, abort
)
from werkzeug.security import check_password_hash, generate_password_hash

import config
import database as db
from helpers import (
    login_required, get_csrf_token, check_csrf, slugify,
    save_product_image, delete_file_quietly, send_email, email_enabled,
)
import razorpay_client as rzp

app = Flask(__name__)
app.config["SECRET_KEY"] = config.SECRET_KEY
app.config["MAX_CONTENT_LENGTH"] = config.MAX_IMAGE_SIZE_MB * 1024 * 1024 * 6  # a few images per request

db.init_db()


@app.after_request
def set_security_headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers.setdefault(
        "Permissions-Policy", "camera=(), microphone=(), geolocation=()"
    )
    return response


@app.context_processor
def inject_globals():
    cart = session.get("cart", {})
    cart_count = sum(cart.values()) if cart else 0
    return {"csrf_token": get_csrf_token, "cart_count": cart_count}


def get_cart_items(conn):
    """Read the session cart, look up live product data, and return a list of
    {product, quantity, line_total} plus the subtotal. Prices always come from
    the database, never the client, so a tampered cart can't change what's charged."""
    cart = session.get("cart", {})
    items = []
    subtotal = 0
    changed = False
    for pid_str, qty in list(cart.items()):
        try:
            pid = int(pid_str)
            qty = max(1, int(qty))
        except (TypeError, ValueError):
            del cart[pid_str]
            changed = True
            continue
        product = conn.execute(
            "SELECT * FROM products WHERE id = ? AND active = 1", (pid,)
        ).fetchone()
        if not product:
            del cart[pid_str]
            changed = True
            continue
        line_total = product["price"] * qty
        subtotal += line_total
        items.append({"product": product, "quantity": qty, "line_total": line_total})
    if changed:
        session["cart"] = cart
        session.modified = True
    return items, subtotal


def get_settings():
    conn = db.get_db()
    rows = conn.execute("SELECT key, value FROM settings").fetchall()
    conn.close()
    return {r["key"]: r["value"] for r in rows}


# ============================================================= STOREFRONT

@app.route("/")
def home():
    conn = db.get_db()
    settings = get_settings()
    sections = conn.execute(
        "SELECT * FROM sections WHERE visible = 1 ORDER BY position ASC"
    ).fetchall()

    category = (request.args.get("category") or "").strip()
    query = (request.args.get("q") or "").strip()
    categories = [
        r["category"] for r in conn.execute(
            "SELECT DISTINCT category FROM products WHERE active = 1 AND category != '' ORDER BY category ASC"
        ).fetchall()
    ]
    if query:
        like = f"%{query}%"
        products = conn.execute(
            """SELECT * FROM products WHERE active = 1
               AND (name LIKE ? OR short_description LIKE ? OR category LIKE ?)
               ORDER BY position ASC, id DESC""",
            (like, like, like),
        ).fetchall()
    elif category:
        products = conn.execute(
            "SELECT * FROM products WHERE active = 1 AND category = ? ORDER BY position ASC, id DESC",
            (category,),
        ).fetchall()
    else:
        products = conn.execute(
            "SELECT * FROM products WHERE active = 1 ORDER BY position ASC, id DESC"
        ).fetchall()

    product_images = {}
    for p in products:
        img = conn.execute(
            "SELECT filename FROM product_images WHERE product_id = ? ORDER BY position ASC LIMIT 1",
            (p["id"],),
        ).fetchone()
        product_images[p["id"]] = img["filename"] if img else None

    testimonials = conn.execute(
        "SELECT * FROM testimonials WHERE visible = 1 ORDER BY position ASC"
    ).fetchall()
    faqs = conn.execute(
        "SELECT * FROM faqs WHERE visible = 1 ORDER BY position ASC"
    ).fetchall()
    conn.close()

    cutoff = (datetime.utcnow() - timedelta(days=14)).isoformat()
    new_product_ids = {p["id"] for p in products if p["created_at"] and p["created_at"] >= cutoff}

    return render_template(
        "index.html", settings=settings, sections=sections,
        products=products, product_images=product_images,
        categories=categories, active_category=category,
        testimonials=testimonials, faqs=faqs, search_query=query,
        new_product_ids=new_product_ids,
    )


@app.route("/product/<slug>")
def product_detail(slug):
    conn = db.get_db()
    settings = get_settings()
    product = conn.execute(
        "SELECT * FROM products WHERE slug = ? AND active = 1", (slug,)
    ).fetchone()
    if not product:
        conn.close()
        abort(404)
    images = conn.execute(
        "SELECT filename FROM product_images WHERE product_id = ? ORDER BY position ASC",
        (product["id"],),
    ).fetchall()

    related = []
    if product["category"]:
        related = conn.execute(
            """SELECT * FROM products WHERE active = 1 AND category = ? AND id != ?
               ORDER BY position ASC, id DESC LIMIT 4""",
            (product["category"], product["id"]),
        ).fetchall()
    if len(related) < 4:
        existing_ids = [r["id"] for r in related] + [product["id"]]
        placeholders = ",".join("?" * len(existing_ids))
        related += conn.execute(
            f"""SELECT * FROM products WHERE active = 1 AND id NOT IN ({placeholders})
                ORDER BY id DESC LIMIT ?""",
            (*existing_ids, 4 - len(related)),
        ).fetchall()

    related_images = {}
    for r in related:
        img = conn.execute(
            "SELECT filename FROM product_images WHERE product_id = ? ORDER BY position ASC LIMIT 1",
            (r["id"],),
        ).fetchone()
        related_images[r["id"]] = img["filename"] if img else None

    conn.close()
    return render_template(
        "product.html", settings=settings, product=product,
        images=[i["filename"] for i in images],
        razorpay_key=config.RAZORPAY_KEY_ID,
        related=related, related_images=related_images,
    )


@app.route("/api/create-order", methods=["POST"])
def api_create_order():
    data = request.get_json(force=True, silent=True) or {}
    product_id = data.get("product_id")
    name = (data.get("name") or "").strip()
    email = (data.get("email") or "").strip()
    phone = (data.get("phone") or "").strip()
    coupon_code = (data.get("coupon_code") or "").strip().upper()

    if not all([product_id, name, email]):
        return jsonify({"error": "Please fill in your name and email."}), 400

    conn = db.get_db()
    product = conn.execute(
        "SELECT * FROM products WHERE id = ? AND active = 1", (product_id,)
    ).fetchone()
    if not product:
        conn.close()
        return jsonify({"error": "This product is not available."}), 404

    if not rzp.is_configured():
        conn.close()
        return jsonify({"error": "Payments are not configured yet. Please contact the site owner."}), 503

    final_amount = product["price"]
    discount_amount = 0
    applied_code = ""

    if coupon_code:
        coupon = conn.execute(
            "SELECT * FROM coupons WHERE code = ? AND active = 1", (coupon_code,)
        ).fetchone()
        if not coupon:
            conn.close()
            return jsonify({"error": "That coupon code isn't valid."}), 400
        if coupon["usage_limit"] is not None and coupon["used_count"] >= coupon["usage_limit"]:
            conn.close()
            return jsonify({"error": "That coupon has already been fully redeemed."}), 400

        if coupon["discount_type"] == "percent":
            discount_amount = int(round(product["price"] * coupon["discount_value"] / 100))
        else:
            discount_amount = coupon["discount_value"]
        discount_amount = min(discount_amount, product["price"] - 1) if product["price"] > 0 else 0
        discount_amount = max(discount_amount, 0)
        final_amount = product["price"] - discount_amount
        applied_code = coupon["code"]

    order_ref = db.new_order_ref()
    try:
        rzp_order = rzp.create_order(final_amount, receipt=order_ref)
    except Exception:
        conn.close()
        return jsonify({"error": "Could not start payment. Please try again."}), 502

    conn.execute(
        """INSERT INTO orders
           (order_ref, product_id, product_name, customer_name, customer_email,
            customer_phone, amount, coupon_code, discount_amount, razorpay_order_id, status, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'created', ?)""",
        (order_ref, product["id"], product["name"], name, email, phone,
         final_amount, applied_code, discount_amount, rzp_order["id"], db.now()),
    )
    conn.commit()
    conn.close()

    return jsonify({
        "razorpay_order_id": rzp_order["id"],
        "razorpay_key": config.RAZORPAY_KEY_ID,
        "amount": rzp_order["amount"],
        "currency": rzp_order["currency"],
        "order_ref": order_ref,
        "product_name": product["name"],
        "customer_name": name,
        "customer_email": email,
        "customer_phone": phone,
    })


@app.route("/cart")
def view_cart():
    conn = db.get_db()
    settings = get_settings()
    items, subtotal = get_cart_items(conn)
    product_images = {}
    for it in items:
        img = conn.execute(
            "SELECT filename FROM product_images WHERE product_id = ? ORDER BY position ASC LIMIT 1",
            (it["product"]["id"],),
        ).fetchone()
        product_images[it["product"]["id"]] = img["filename"] if img else None
    conn.close()
    return render_template(
        "cart.html", settings=settings, items=items, subtotal=subtotal,
        product_images=product_images, razorpay_key=config.RAZORPAY_KEY_ID,
    )


@app.route("/cart/add", methods=["POST"])
def cart_add():
    product_id = request.form.get("product_id") or (request.get_json(silent=True) or {}).get("product_id")
    try:
        qty = max(1, int(request.form.get("quantity", 1)))
    except (TypeError, ValueError):
        qty = 1
    if not product_id:
        return jsonify({"error": "Missing product."}), 400

    conn = db.get_db()
    product = conn.execute("SELECT * FROM products WHERE id = ? AND active = 1", (product_id,)).fetchone()
    conn.close()
    if not product:
        return jsonify({"error": "This product is not available."}), 404

    cart = session.get("cart", {})
    key = str(product["id"])
    cart[key] = cart.get(key, 0) + qty
    session["cart"] = cart
    session.modified = True
    cart_count = sum(cart.values())

    if request.headers.get("X-Requested-With") == "fetch":
        return jsonify({"success": True, "cart_count": cart_count, "product_name": product["name"]})
    flash(f'Added "{product["name"]}" to your cart.', "success")
    return redirect(request.referrer or url_for("home"))


@app.route("/cart/update", methods=["POST"])
def cart_update():
    product_id = str(request.form.get("product_id", ""))
    try:
        qty = int(request.form.get("quantity", 1))
    except (TypeError, ValueError):
        qty = 1
    cart = session.get("cart", {})
    if product_id in cart:
        if qty <= 0:
            del cart[product_id]
        else:
            cart[product_id] = min(qty, 99)
        session["cart"] = cart
        session.modified = True
    return redirect(url_for("view_cart"))


@app.route("/cart/remove/<int:product_id>", methods=["POST"])
def cart_remove(product_id):
    cart = session.get("cart", {})
    cart.pop(str(product_id), None)
    session["cart"] = cart
    session.modified = True
    flash("Item removed from cart.", "success")
    return redirect(url_for("view_cart"))


@app.route("/cart/clear", methods=["POST"])
def cart_clear():
    session["cart"] = {}
    session.modified = True
    return redirect(url_for("view_cart"))


@app.route("/api/cart/apply-coupon", methods=["POST"])
def api_cart_apply_coupon():
    data = request.get_json(force=True, silent=True) or {}
    code = (data.get("code") or "").strip().upper()
    conn = db.get_db()
    items, subtotal = get_cart_items(conn)
    coupon = conn.execute("SELECT * FROM coupons WHERE code = ? AND active = 1", (code,)).fetchone()
    conn.close()
    if not items:
        return jsonify({"error": "Your cart is empty."}), 400
    if not coupon:
        return jsonify({"error": "That coupon code isn't valid."}), 400
    if coupon["usage_limit"] is not None and coupon["used_count"] >= coupon["usage_limit"]:
        return jsonify({"error": "That coupon has already been fully redeemed."}), 400

    if coupon["discount_type"] == "percent":
        discount = int(round(subtotal * coupon["discount_value"] / 100))
    else:
        discount = coupon["discount_value"]
    discount = max(0, min(discount, subtotal - 1 if subtotal > 0 else 0))
    final_total = subtotal - discount
    return jsonify({"success": True, "discount_amount": discount, "final_price": final_total, "code": coupon["code"]})


@app.route("/api/cart/create-order", methods=["POST"])
def api_cart_create_order():
    data = request.get_json(force=True, silent=True) or {}
    name = (data.get("name") or "").strip()
    email = (data.get("email") or "").strip()
    phone = (data.get("phone") or "").strip()
    coupon_code = (data.get("coupon_code") or "").strip().upper()

    if not all([name, email]):
        return jsonify({"error": "Please fill in your name and email."}), 400

    conn = db.get_db()
    items, subtotal = get_cart_items(conn)
    if not items:
        conn.close()
        return jsonify({"error": "Your cart is empty."}), 400

    if not rzp.is_configured():
        conn.close()
        return jsonify({"error": "Payments are not configured yet. Please contact the site owner."}), 503

    final_amount = subtotal
    discount_amount = 0
    applied_code = ""
    if coupon_code:
        coupon = conn.execute(
            "SELECT * FROM coupons WHERE code = ? AND active = 1", (coupon_code,)
        ).fetchone()
        if not coupon:
            conn.close()
            return jsonify({"error": "That coupon code isn't valid."}), 400
        if coupon["usage_limit"] is not None and coupon["used_count"] >= coupon["usage_limit"]:
            conn.close()
            return jsonify({"error": "That coupon has already been fully redeemed."}), 400
        if coupon["discount_type"] == "percent":
            discount_amount = int(round(subtotal * coupon["discount_value"] / 100))
        else:
            discount_amount = coupon["discount_value"]
        discount_amount = max(0, min(discount_amount, subtotal - 1 if subtotal > 0 else 0))
        final_amount = subtotal - discount_amount
        applied_code = coupon["code"]

    order_ref = db.new_order_ref()
    try:
        rzp_order = rzp.create_order(final_amount, receipt=order_ref)
    except Exception:
        conn.close()
        return jsonify({"error": "Could not start payment. Please try again."}), 502

    item_count = sum(it["quantity"] for it in items)
    summary_name = items[0]["product"]["name"] if len(items) == 1 else f"{item_count} items ({len(items)} products)"

    cur = conn.execute(
        """INSERT INTO orders
           (order_ref, product_id, product_name, customer_name, customer_email,
            customer_phone, amount, coupon_code, discount_amount, razorpay_order_id, status, created_at)
           VALUES (?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, 'created', ?)""",
        (order_ref, summary_name, name, email, phone,
         final_amount, applied_code, discount_amount, rzp_order["id"], db.now()),
    )
    order_id = cur.lastrowid
    for it in items:
        conn.execute(
            """INSERT INTO order_items (order_id, product_id, product_name, unit_price, quantity, line_total)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (order_id, it["product"]["id"], it["product"]["name"], it["product"]["price"],
             it["quantity"], it["line_total"]),
        )
    conn.commit()
    conn.close()

    return jsonify({
        "razorpay_order_id": rzp_order["id"],
        "razorpay_key": config.RAZORPAY_KEY_ID,
        "amount": rzp_order["amount"],
        "currency": rzp_order["currency"],
        "order_ref": order_ref,
        "product_name": summary_name,
        "customer_name": name,
        "customer_email": email,
        "customer_phone": phone,
    })


@app.route("/api/apply-coupon", methods=["POST"])
def api_apply_coupon():
    data = request.get_json(force=True, silent=True) or {}
    code = (data.get("code") or "").strip().upper()
    product_id = data.get("product_id")
    conn = db.get_db()
    product = conn.execute("SELECT * FROM products WHERE id = ? AND active = 1", (product_id,)).fetchone()
    if not product:
        conn.close()
        return jsonify({"error": "Product not found."}), 404
    coupon = conn.execute("SELECT * FROM coupons WHERE code = ? AND active = 1", (code,)).fetchone()
    conn.close()
    if not coupon:
        return jsonify({"error": "That coupon code isn't valid."}), 400
    if coupon["usage_limit"] is not None and coupon["used_count"] >= coupon["usage_limit"]:
        return jsonify({"error": "That coupon has already been fully redeemed."}), 400

    if coupon["discount_type"] == "percent":
        discount = int(round(product["price"] * coupon["discount_value"] / 100))
    else:
        discount = coupon["discount_value"]
    discount = max(0, min(discount, product["price"] - 1 if product["price"] > 0 else 0))
    final_price = product["price"] - discount
    return jsonify({"success": True, "discount_amount": discount, "final_price": final_price, "code": coupon["code"]})


@app.route("/api/verify-payment", methods=["POST"])
def api_verify_payment():
    data = request.get_json(force=True, silent=True) or {}
    rzp_order_id = data.get("razorpay_order_id")
    rzp_payment_id = data.get("razorpay_payment_id")
    rzp_signature = data.get("razorpay_signature")

    if not all([rzp_order_id, rzp_payment_id, rzp_signature]):
        return jsonify({"error": "Missing payment details."}), 400

    valid = rzp.verify_payment_signature(rzp_order_id, rzp_payment_id, rzp_signature)

    conn = db.get_db()
    order = conn.execute(
        "SELECT * FROM orders WHERE razorpay_order_id = ?", (rzp_order_id,)
    ).fetchone()

    if not order:
        conn.close()
        return jsonify({"error": "Order not found."}), 404

    if not valid:
        conn.execute(
            "UPDATE orders SET status = 'failed' WHERE id = ?", (order["id"],)
        )
        conn.commit()
        conn.close()
        return jsonify({"error": "Payment verification failed."}), 400

    conn.execute(
        """UPDATE orders SET status = 'paid', razorpay_payment_id = ?,
           razorpay_signature = ?, paid_at = ? WHERE id = ?""",
        (rzp_payment_id, rzp_signature, db.now(), order["id"]),
    )
    if order["coupon_code"]:
        conn.execute(
            "UPDATE coupons SET used_count = used_count + 1 WHERE code = ?",
            (order["coupon_code"],),
        )
    order_items = conn.execute(
        "SELECT * FROM order_items WHERE order_id = ?", (order["id"],)
    ).fetchall()
    conn.commit()
    conn.close()

    if order_items:
        session["cart"] = {}
        session.modified = True

    if email_enabled():
        if order_items:
            lines = "\n".join(
                f"  - {it['product_name']} x{it['quantity']} — "
                f"{'{:,}'.format(it['line_total'])}"
                for it in order_items
            )
            item_block = f"Items:\n{lines}\n\n"
        else:
            item_block = f"Item: {order['product_name']}\n\n"
        send_email(
            order["customer_email"],
            f"We've received your order {order['order_ref']}",
            f"Hi {order['customer_name']},\n\n"
            f"Thank you for your order.\n"
            f"Your payment has been received and your order is now being prepared.\n\n"
            f"{item_block}"
            f"Order reference: {order['order_ref']}\n"
            f"You can check its status any time at our order tracking page.\n\n"
            f"We'll be in touch shortly.",
        )

    return jsonify({"success": True, "order_ref": order["order_ref"]})


@app.route("/track", methods=["GET", "POST"])
def track_order():
    order = None
    searched = False
    prefill_ref = request.args.get("order_ref", "")
    prefill_email = request.args.get("email", "")

    if request.method == "POST":
        order_ref = (request.form.get("order_ref") or "").strip().upper()
        email = (request.form.get("email") or "").strip().lower()
        searched = True
    elif prefill_ref and prefill_email:
        order_ref = prefill_ref.strip().upper()
        email = prefill_email.strip().lower()
        searched = True
    else:
        order_ref = email = None

    if searched:
        conn = db.get_db()
        order = conn.execute(
            "SELECT * FROM orders WHERE order_ref = ? AND lower(customer_email) = ?",
            (order_ref, email),
        ).fetchone()
        order_items = []
        if order:
            order_items = conn.execute(
                "SELECT * FROM order_items WHERE order_id = ?", (order["id"],)
            ).fetchall()
        conn.close()
    else:
        order_items = []

    settings = get_settings()
    return render_template(
        "order_status.html", settings=settings, order=order, searched=searched,
        prefill_ref=prefill_ref, prefill_email=prefill_email, order_items=order_items,
    )


@app.route("/newsletter/subscribe", methods=["POST"])
def newsletter_subscribe():
    email = (request.form.get("email") or "").strip().lower()
    if not email or "@" not in email:
        flash("Please enter a valid email address.", "error")
        return redirect(url_for("home") + "#newsletter")
    conn = db.get_db()
    try:
        conn.execute(
            "INSERT INTO newsletter_subscribers (email, created_at) VALUES (?, ?)",
            (email, db.now()),
        )
        conn.commit()
        flash("You're subscribed! We'll keep you posted.", "success")
    except Exception:
        flash("You're already on the list — thank you!", "success")
    conn.close()
    return redirect(url_for("home") + "#newsletter")


@app.route("/robots.txt")
def robots():
    lines = [
        "User-agent: *",
        "Allow: /",
        "Disallow: /admin/",
        f"Sitemap: {url_for('sitemap', _external=True)}",
    ]
    return "\n".join(lines), 200, {"Content-Type": "text/plain"}


@app.route("/sitemap.xml")
def sitemap():
    conn = db.get_db()
    products = conn.execute("SELECT slug FROM products WHERE active = 1").fetchall()
    conn.close()
    urls = [url_for("home", _external=True), url_for("track_order", _external=True)]
    urls += [url_for("product_detail", slug=p["slug"], _external=True) for p in products]
    xml = ['<?xml version="1.0" encoding="UTF-8"?>', '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for u in urls:
        xml.append(f"<url><loc>{u}</loc></url>")
    xml.append("</urlset>")
    return "\n".join(xml), 200, {"Content-Type": "application/xml"}


@app.errorhandler(404)
def not_found(e):
    settings = get_settings()
    return render_template("404.html", settings=settings), 404


# ============================================================= ADMIN AUTH

@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        check_csrf()
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        conn = db.get_db()
        user = conn.execute(
            "SELECT * FROM admin_users WHERE username = ?", (username,)
        ).fetchone()
        conn.close()
        if user and check_password_hash(user["password_hash"], password):
            session.clear()
            session["admin_id"] = user["id"]
            session["admin_username"] = user["username"]
            return redirect(request.args.get("next") or url_for("admin_dashboard"))
        flash("Incorrect username or password.", "error")
    return render_template("admin/login.html")


@app.route("/admin/logout")
def admin_logout():
    session.clear()
    return redirect(url_for("admin_login"))


# ============================================================= ADMIN DASHBOARD

@app.route("/admin/")
@login_required
def admin_dashboard():
    conn = db.get_db()
    stats = {
        "products": conn.execute("SELECT COUNT(*) c FROM products").fetchone()["c"],
        "pending": conn.execute("SELECT COUNT(*) c FROM orders WHERE status = 'paid'").fetchone()["c"],
        "delivered": conn.execute("SELECT COUNT(*) c FROM orders WHERE status = 'delivered'").fetchone()["c"],
        "revenue": conn.execute(
            "SELECT COALESCE(SUM(amount),0) s FROM orders WHERE status IN ('paid','delivered')"
        ).fetchone()["s"],
    }
    recent_orders = conn.execute(
        "SELECT * FROM orders ORDER BY id DESC LIMIT 8"
    ).fetchall()

    # Revenue for each of the last 14 days, for a simple sparkline
    daily_rows = conn.execute(
        """SELECT substr(paid_at, 1, 10) AS day, SUM(amount) AS total
           FROM orders WHERE status IN ('paid','delivered') AND paid_at IS NOT NULL
           GROUP BY day"""
    ).fetchall()
    by_day = {r["day"]: r["total"] for r in daily_rows}
    today = datetime.utcnow().date()
    revenue_trend = []
    for i in range(13, -1, -1):
        day = (today - timedelta(days=i)).isoformat()
        revenue_trend.append({"day": day, "total": by_day.get(day, 0)})

    top_products = conn.execute(
        """SELECT product_name, COUNT(*) AS orders_count, SUM(line_total) AS revenue
           FROM order_items GROUP BY product_name ORDER BY revenue DESC LIMIT 5"""
    ).fetchall()

    conn.close()
    return render_template(
        "admin/dashboard.html", stats=stats, recent_orders=recent_orders,
        razorpay_configured=rzp.is_configured(),
        revenue_trend=revenue_trend, top_products=top_products,
    )


# ============================================================= ADMIN: SITE SETTINGS

@app.route("/admin/settings", methods=["GET", "POST"])
@login_required
def admin_settings():
    if request.method == "POST":
        check_csrf()
        conn = db.get_db()
        for key in db.DEFAULT_SETTINGS.keys():
            value = request.form.get(key, "")
            conn.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )
        conn.commit()
        conn.close()
        flash("Your changes have been saved.", "success")
        return redirect(url_for("admin_settings"))

    settings = get_settings()
    return render_template("admin/settings.html", settings=settings)


# ============================================================= ADMIN: SECTIONS

@app.route("/admin/sections")
@login_required
def admin_sections():
    conn = db.get_db()
    sections = conn.execute("SELECT * FROM sections ORDER BY position ASC").fetchall()
    conn.close()
    return render_template("admin/sections.html", sections=sections)


@app.route("/admin/sections/save", methods=["POST"])
@login_required
def admin_sections_save():
    check_csrf()
    section_id = request.form.get("id")
    title = request.form.get("title", "").strip()
    content = request.form.get("content", "").strip()
    visible = 1 if request.form.get("visible") else 0

    if not title:
        flash("Please give the section a title.", "error")
        return redirect(url_for("admin_sections"))

    conn = db.get_db()
    if section_id:
        conn.execute(
            "UPDATE sections SET title = ?, content = ?, visible = ? WHERE id = ?",
            (title, content, visible, section_id),
        )
        flash("Section updated.", "success")
    else:
        max_pos = conn.execute("SELECT COALESCE(MAX(position), -1) m FROM sections").fetchone()["m"]
        conn.execute(
            "INSERT INTO sections (title, content, position, visible) VALUES (?, ?, ?, ?)",
            (title, content, max_pos + 1, visible),
        )
        flash("New section added.", "success")
    conn.commit()
    conn.close()
    return redirect(url_for("admin_sections"))


@app.route("/admin/sections/delete/<int:section_id>", methods=["POST"])
@login_required
def admin_sections_delete(section_id):
    check_csrf()
    conn = db.get_db()
    conn.execute("DELETE FROM sections WHERE id = ?", (section_id,))
    conn.commit()
    conn.close()
    flash("Section removed.", "success")
    return redirect(url_for("admin_sections"))


@app.route("/admin/sections/move/<int:section_id>/<direction>", methods=["POST"])
@login_required
def admin_sections_move(section_id, direction):
    check_csrf()
    conn = db.get_db()
    sections = conn.execute("SELECT * FROM sections ORDER BY position ASC").fetchall()
    ids = [s["id"] for s in sections]
    idx = ids.index(section_id)
    swap_idx = idx - 1 if direction == "up" else idx + 1
    if 0 <= swap_idx < len(ids):
        a, b = sections[idx], sections[swap_idx]
        conn.execute("UPDATE sections SET position = ? WHERE id = ?", (b["position"], a["id"]))
        conn.execute("UPDATE sections SET position = ? WHERE id = ?", (a["position"], b["id"]))
        conn.commit()
    conn.close()
    return redirect(url_for("admin_sections"))


# ============================================================= ADMIN: PRODUCTS

@app.route("/admin/products")
@login_required
def admin_products():
    conn = db.get_db()
    products = conn.execute("SELECT * FROM products ORDER BY position ASC, id DESC").fetchall()
    thumbs = {}
    for p in products:
        img = conn.execute(
            "SELECT filename FROM product_images WHERE product_id = ? ORDER BY position ASC LIMIT 1",
            (p["id"],),
        ).fetchone()
        thumbs[p["id"]] = img["filename"] if img else None
    conn.close()
    return render_template("admin/products.html", products=products, thumbs=thumbs)


@app.route("/admin/products/new", methods=["GET", "POST"])
@login_required
def admin_product_new():
    if request.method == "POST":
        check_csrf()
        return _save_product(None)
    categories = _existing_categories()
    return render_template("admin/product_form.html", product=None, images=[], categories=categories)


@app.route("/admin/products/edit/<int:product_id>", methods=["GET", "POST"])
@login_required
def admin_product_edit(product_id):
    conn = db.get_db()
    product = conn.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()
    if not product:
        conn.close()
        abort(404)
    if request.method == "POST":
        check_csrf()
        conn.close()
        return _save_product(product_id)
    images = conn.execute(
        "SELECT * FROM product_images WHERE product_id = ? ORDER BY position ASC", (product_id,)
    ).fetchall()
    conn.close()
    categories = _existing_categories()
    return render_template("admin/product_form.html", product=product, images=images, categories=categories)


def _existing_categories():
    conn = db.get_db()
    rows = conn.execute(
        "SELECT DISTINCT category FROM products WHERE category != '' ORDER BY category ASC"
    ).fetchall()
    conn.close()
    return [r["category"] for r in rows]


def _save_product(product_id):
    name = request.form.get("name", "").strip()
    short_description = request.form.get("short_description", "").strip()
    description = request.form.get("description", "").strip()
    price_raw = request.form.get("price", "0").strip()
    category = request.form.get("category", "").strip()
    active = 1 if request.form.get("active") else 0

    if not name:
        flash("Please give the product a name.", "error")
        return redirect(request.referrer or url_for("admin_products"))
    try:
        price = int(float(price_raw))
        if price < 0:
            raise ValueError
    except ValueError:
        flash("Please enter a valid price in rupees.", "error")
        return redirect(request.referrer or url_for("admin_products"))

    conn = db.get_db()
    if product_id:
        conn.execute(
            """UPDATE products SET name=?, short_description=?, description=?,
               price=?, category=?, active=? WHERE id=?""",
            (name, short_description, description, price, category, active, product_id),
        )
    else:
        slug_base = slugify(name)
        slug = slug_base
        i = 2
        while conn.execute("SELECT 1 FROM products WHERE slug = ?", (slug,)).fetchone():
            slug = f"{slug_base}-{i}"
            i += 1
        max_pos = conn.execute("SELECT COALESCE(MAX(position), -1) m FROM products").fetchone()["m"]
        cur = conn.execute(
            """INSERT INTO products (name, slug, short_description, description, price,
               category, active, position, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (name, slug, short_description, description, price, category, active, max_pos + 1, db.now()),
        )
        product_id = cur.lastrowid

    files = request.files.getlist("images")
    max_pos = conn.execute(
        "SELECT COALESCE(MAX(position), -1) m FROM product_images WHERE product_id = ?", (product_id,)
    ).fetchone()["m"]
    for f in files:
        if f and f.filename:
            try:
                filename = save_product_image(f)
            except ValueError as e:
                flash(str(e), "error")
                continue
            if filename:
                max_pos += 1
                conn.execute(
                    "INSERT INTO product_images (product_id, filename, position) VALUES (?, ?, ?)",
                    (product_id, filename, max_pos),
                )

    conn.commit()
    conn.close()
    flash("Product saved.", "success")
    return redirect(url_for("admin_product_edit", product_id=product_id))


@app.route("/admin/products/bulk", methods=["POST"])
@login_required
def admin_products_bulk():
    check_csrf()
    action = request.form.get("action", "")
    ids = request.form.getlist("product_ids")
    if not ids:
        flash("Select at least one product first.", "error")
        return redirect(url_for("admin_products"))

    conn = db.get_db()
    placeholders = ",".join("?" * len(ids))
    if action == "activate":
        conn.execute(f"UPDATE products SET active = 1 WHERE id IN ({placeholders})", ids)
        flash(f"Activated {len(ids)} product(s).", "success")
    elif action == "deactivate":
        conn.execute(f"UPDATE products SET active = 0 WHERE id IN ({placeholders})", ids)
        flash(f"Deactivated {len(ids)} product(s).", "success")
    elif action == "delete":
        rows = conn.execute(
            f"SELECT filename FROM product_images WHERE product_id IN ({placeholders})", ids
        ).fetchall()
        for r in rows:
            delete_file_quietly(r["filename"])
        conn.execute(f"DELETE FROM products WHERE id IN ({placeholders})", ids)
        flash(f"Deleted {len(ids)} product(s).", "success")
    else:
        flash("Unknown bulk action.", "error")
    conn.commit()
    conn.close()
    return redirect(url_for("admin_products"))


@app.route("/admin/products/delete/<int:product_id>", methods=["POST"])
@login_required
def admin_product_delete(product_id):
    check_csrf()
    conn = db.get_db()
    images = conn.execute(
        "SELECT filename FROM product_images WHERE product_id = ?", (product_id,)
    ).fetchall()
    for img in images:
        delete_file_quietly(img["filename"])
    conn.execute("DELETE FROM products WHERE id = ?", (product_id,))
    conn.commit()
    conn.close()
    flash("Product deleted.", "success")
    return redirect(url_for("admin_products"))


@app.route("/admin/products/image/delete/<int:image_id>", methods=["POST"])
@login_required
def admin_product_image_delete(image_id):
    check_csrf()
    conn = db.get_db()
    img = conn.execute("SELECT * FROM product_images WHERE id = ?", (image_id,)).fetchone()
    if img:
        delete_file_quietly(img["filename"])
        conn.execute("DELETE FROM product_images WHERE id = ?", (image_id,))
        conn.commit()
        product_id = img["product_id"]
    else:
        product_id = request.form.get("product_id")
    conn.close()
    return redirect(url_for("admin_product_edit", product_id=product_id))


# ============================================================= ADMIN: ORDERS

@app.route("/admin/orders")
@login_required
def admin_orders():
    status_filter = request.args.get("status", "")
    q = (request.args.get("q") or "").strip()
    conn = db.get_db()
    clauses = []
    params = []
    if status_filter:
        clauses.append("status = ?")
        params.append(status_filter)
    if q:
        clauses.append("(order_ref LIKE ? OR customer_name LIKE ? OR customer_email LIKE ?)")
        like = f"%{q}%"
        params += [like, like, like]
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    orders = conn.execute(f"SELECT * FROM orders {where} ORDER BY id DESC", params).fetchall()
    conn.close()
    return render_template("admin/orders.html", orders=orders, status_filter=status_filter, q=q)


@app.route("/admin/orders/<int:order_id>")
@login_required
def admin_order_detail(order_id):
    conn = db.get_db()
    order = conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
    order_items = conn.execute(
        "SELECT * FROM order_items WHERE order_id = ?", (order_id,)
    ).fetchall() if order else []
    conn.close()
    if not order:
        abort(404)
    return render_template("admin/order_detail.html", order=order, order_items=order_items,
                            email_enabled=email_enabled())


@app.route("/admin/orders/<int:order_id>/deliver", methods=["POST"])
@login_required
def admin_order_deliver(order_id):
    check_csrf()
    message = request.form.get("delivery_message", "").strip()
    conn = db.get_db()
    order = conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
    if not order:
        conn.close()
        abort(404)
    order_items = conn.execute(
        "SELECT * FROM order_items WHERE order_id = ?", (order_id,)
    ).fetchall()
    conn.execute(
        "UPDATE orders SET status = 'delivered', delivery_message = ?, delivered_at = ? WHERE id = ?",
        (message, db.now(), order_id),
    )
    conn.commit()
    conn.close()

    if email_enabled():
        item_line = order["product_name"] if not order_items else ", ".join(
            f"{it['product_name']} x{it['quantity']}" for it in order_items
        )
        send_email(
            order["customer_email"],
            f"Your order {order['order_ref']} has been delivered",
            f"Hi {order['customer_name']},\n\n"
            f"Great news — your order for \"{item_line}\" is ready!\n\n"
            f"{message}\n\n"
            f"Order reference: {order['order_ref']}\n\nThank you for shopping with us.",
        )
    flash("Order marked as delivered and the customer has been notified." if email_enabled()
          else "Order marked as delivered. Please share the details with the customer directly "
               "(email sending isn't set up yet).", "success")
    return redirect(url_for("admin_order_detail", order_id=order_id))


@app.route("/admin/orders/<int:order_id>/cancel", methods=["POST"])
@login_required
def admin_order_cancel(order_id):
    check_csrf()
    conn = db.get_db()
    conn.execute("UPDATE orders SET status = 'cancelled' WHERE id = ?", (order_id,))
    conn.commit()
    conn.close()
    flash("Order cancelled.", "success")
    return redirect(url_for("admin_order_detail", order_id=order_id))


# ============================================================= ADMIN: COUPONS

@app.route("/admin/coupons")
@login_required
def admin_coupons():
    conn = db.get_db()
    coupons = conn.execute("SELECT * FROM coupons ORDER BY id DESC").fetchall()
    conn.close()
    return render_template("admin/coupons.html", coupons=coupons)


@app.route("/admin/coupons/save", methods=["POST"])
@login_required
def admin_coupons_save():
    check_csrf()
    code = request.form.get("code", "").strip().upper()
    discount_type = request.form.get("discount_type", "percent")
    discount_value_raw = request.form.get("discount_value", "0").strip()
    usage_limit_raw = request.form.get("usage_limit", "").strip()
    active = 1 if request.form.get("active") else 0

    if not code:
        flash("Please enter a coupon code.", "error")
        return redirect(url_for("admin_coupons"))
    try:
        discount_value = int(discount_value_raw)
        if discount_value <= 0:
            raise ValueError
        if discount_type == "percent" and discount_value > 100:
            raise ValueError
    except ValueError:
        flash("Please enter a valid discount amount.", "error")
        return redirect(url_for("admin_coupons"))

    usage_limit = int(usage_limit_raw) if usage_limit_raw.isdigit() else None

    conn = db.get_db()
    try:
        conn.execute(
            """INSERT INTO coupons (code, discount_type, discount_value, active, usage_limit, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (code, discount_type, discount_value, active, usage_limit, db.now()),
        )
        conn.commit()
        flash(f"Coupon {code} created.", "success")
    except Exception:
        flash("A coupon with that code already exists.", "error")
    conn.close()
    return redirect(url_for("admin_coupons"))


@app.route("/admin/coupons/toggle/<int:coupon_id>", methods=["POST"])
@login_required
def admin_coupons_toggle(coupon_id):
    check_csrf()
    conn = db.get_db()
    conn.execute("UPDATE coupons SET active = 1 - active WHERE id = ?", (coupon_id,))
    conn.commit()
    conn.close()
    return redirect(url_for("admin_coupons"))


@app.route("/admin/coupons/delete/<int:coupon_id>", methods=["POST"])
@login_required
def admin_coupons_delete(coupon_id):
    check_csrf()
    conn = db.get_db()
    conn.execute("DELETE FROM coupons WHERE id = ?", (coupon_id,))
    conn.commit()
    conn.close()
    flash("Coupon deleted.", "success")
    return redirect(url_for("admin_coupons"))


# ============================================================= ADMIN: TESTIMONIALS

@app.route("/admin/testimonials")
@login_required
def admin_testimonials():
    conn = db.get_db()
    testimonials = conn.execute("SELECT * FROM testimonials ORDER BY position ASC").fetchall()
    conn.close()
    return render_template("admin/testimonials.html", testimonials=testimonials)


@app.route("/admin/testimonials/save", methods=["POST"])
@login_required
def admin_testimonials_save():
    check_csrf()
    testimonial_id = request.form.get("id")
    customer_name = request.form.get("customer_name", "").strip()
    quote = request.form.get("quote", "").strip()
    rating = request.form.get("rating", "5")
    visible = 1 if request.form.get("visible") else 0

    if not customer_name or not quote:
        flash("Please fill in both a name and a quote.", "error")
        return redirect(url_for("admin_testimonials"))

    conn = db.get_db()
    if testimonial_id:
        conn.execute(
            "UPDATE testimonials SET customer_name=?, quote=?, rating=?, visible=? WHERE id=?",
            (customer_name, quote, rating, visible, testimonial_id),
        )
    else:
        max_pos = conn.execute("SELECT COALESCE(MAX(position), -1) m FROM testimonials").fetchone()["m"]
        conn.execute(
            "INSERT INTO testimonials (customer_name, quote, rating, position, visible) VALUES (?, ?, ?, ?, ?)",
            (customer_name, quote, rating, max_pos + 1, visible),
        )
    conn.commit()
    conn.close()
    flash("Testimonial saved.", "success")
    return redirect(url_for("admin_testimonials"))


@app.route("/admin/testimonials/delete/<int:testimonial_id>", methods=["POST"])
@login_required
def admin_testimonials_delete(testimonial_id):
    check_csrf()
    conn = db.get_db()
    conn.execute("DELETE FROM testimonials WHERE id = ?", (testimonial_id,))
    conn.commit()
    conn.close()
    flash("Testimonial removed.", "success")
    return redirect(url_for("admin_testimonials"))


# ============================================================= ADMIN: FAQS

@app.route("/admin/faqs")
@login_required
def admin_faqs():
    conn = db.get_db()
    faqs = conn.execute("SELECT * FROM faqs ORDER BY position ASC").fetchall()
    conn.close()
    return render_template("admin/faqs.html", faqs=faqs)


@app.route("/admin/faqs/save", methods=["POST"])
@login_required
def admin_faqs_save():
    check_csrf()
    faq_id = request.form.get("id")
    question = request.form.get("question", "").strip()
    answer = request.form.get("answer", "").strip()
    visible = 1 if request.form.get("visible") else 0

    if not question or not answer:
        flash("Please fill in both the question and the answer.", "error")
        return redirect(url_for("admin_faqs"))

    conn = db.get_db()
    if faq_id:
        conn.execute(
            "UPDATE faqs SET question=?, answer=?, visible=? WHERE id=?",
            (question, answer, visible, faq_id),
        )
    else:
        max_pos = conn.execute("SELECT COALESCE(MAX(position), -1) m FROM faqs").fetchone()["m"]
        conn.execute(
            "INSERT INTO faqs (question, answer, position, visible) VALUES (?, ?, ?, ?)",
            (question, answer, max_pos + 1, visible),
        )
    conn.commit()
    conn.close()
    flash("FAQ saved.", "success")
    return redirect(url_for("admin_faqs"))


@app.route("/admin/faqs/delete/<int:faq_id>", methods=["POST"])
@login_required
def admin_faqs_delete(faq_id):
    check_csrf()
    conn = db.get_db()
    conn.execute("DELETE FROM faqs WHERE id = ?", (faq_id,))
    conn.commit()
    conn.close()
    flash("FAQ removed.", "success")
    return redirect(url_for("admin_faqs"))


# ============================================================= ADMIN: NEWSLETTER

@app.route("/admin/newsletter")
@login_required
def admin_newsletter():
    conn = db.get_db()
    subscribers = conn.execute(
        "SELECT * FROM newsletter_subscribers ORDER BY id DESC"
    ).fetchall()
    conn.close()
    return render_template("admin/newsletter.html", subscribers=subscribers)


@app.route("/admin/newsletter/export.csv")
@login_required
def admin_newsletter_export():
    import csv
    import io
    conn = db.get_db()
    subscribers = conn.execute(
        "SELECT email, created_at FROM newsletter_subscribers ORDER BY id DESC"
    ).fetchall()
    conn.close()
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["Email", "Subscribed At"])
    for s in subscribers:
        writer.writerow([s["email"], s["created_at"]])
    return buf.getvalue(), 200, {
        "Content-Type": "text/csv",
        "Content-Disposition": "attachment; filename=newsletter_subscribers.csv",
    }


# ============================================================= ADMIN: ORDERS EXPORT

@app.route("/admin/orders/export.csv")
@login_required
def admin_orders_export():
    import csv
    import io
    conn = db.get_db()
    orders = conn.execute("SELECT * FROM orders ORDER BY id DESC").fetchall()
    all_items = conn.execute("SELECT * FROM order_items ORDER BY order_id ASC").fetchall()
    conn.close()
    items_by_order = {}
    for it in all_items:
        items_by_order.setdefault(it["order_id"], []).append(it)

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([
        "Order Ref", "Items", "Customer Name", "Email", "Phone", "Amount",
        "Coupon", "Discount", "Status", "Created", "Paid", "Delivered",
    ])
    for o in orders:
        items = items_by_order.get(o["id"])
        item_summary = o["product_name"] if not items else "; ".join(
            f"{it['product_name']} x{it['quantity']}" for it in items
        )
        writer.writerow([
            o["order_ref"], item_summary, o["customer_name"], o["customer_email"],
            o["customer_phone"], o["amount"], o["coupon_code"], o["discount_amount"],
            o["status"], o["created_at"], o["paid_at"], o["delivered_at"],
        ])
    return buf.getvalue(), 200, {
        "Content-Type": "text/csv",
        "Content-Disposition": "attachment; filename=orders.csv",
    }


# ============================================================= ADMIN: ACCOUNT

@app.route("/admin/account", methods=["GET", "POST"])
@login_required
def admin_account():
    if request.method == "POST":
        check_csrf()
        current = request.form.get("current_password", "")
        new = request.form.get("new_password", "")
        confirm = request.form.get("confirm_password", "")

        conn = db.get_db()
        user = conn.execute("SELECT * FROM admin_users WHERE id = ?", (session["admin_id"],)).fetchone()

        if not check_password_hash(user["password_hash"], current):
            flash("Current password is incorrect.", "error")
        elif len(new) < 8:
            flash("New password must be at least 8 characters.", "error")
        elif new != confirm:
            flash("New passwords do not match.", "error")
        else:
            conn.execute(
                "UPDATE admin_users SET password_hash = ? WHERE id = ?",
                (generate_password_hash(new), user["id"]),
            )
            conn.commit()
            flash("Password updated.", "success")
        conn.close()
        return redirect(url_for("admin_account"))
    return render_template("admin/account.html")


if __name__ == "__main__":
    import os
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=config.DEBUG)
