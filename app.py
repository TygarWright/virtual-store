import os
from datetime import datetime, timedelta, timezone

from flask import (
    Flask, render_template, request, redirect, url_for, session,
    flash, jsonify, abort
)
from werkzeug.security import check_password_hash, generate_password_hash

import config
import database as db
from helpers import (
    login_required, get_csrf_token, check_csrf, check_csrf_api, slugify,
    save_product_image, delete_file_quietly, send_email, email_enabled,
    rate_limited, turnstile_enabled, verify_turnstile,
    firebase_auth_enabled, verify_firebase_id_token,
    generate_otp_code, store_otp, verify_otp_code,
)
import razorpay_client as rzp

app = Flask(__name__)
app.config["SECRET_KEY"] = config.SECRET_KEY
app.config["MAX_CONTENT_LENGTH"] = config.MAX_IMAGE_SIZE_MB * 1024 * 1024 * 6  # a few images per request

# Session cookie hardening — not readable by JS, not sent cross-site, and
# only sent over HTTPS once deployed behind TLS (off under DEBUG so local
# http:// testing still works).
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = not config.DEBUG
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(hours=12)

db.init_db()


@app.before_request
def capture_url_coupon():
    """Check for ?coupon=CODE in the URL on any GET request and store it in
    the session so it can be auto-applied at checkout. This enables
    URL-driven coupons from marketing campaigns, social media links, etc."""
    if request.method == "GET" and request.args.get("coupon"):
        code = request.args.get("coupon", "").strip().upper()
        if code:
            session["url_coupon_code"] = code
            session.modified = True


def is_safe_redirect_target(target):
    """Only allow redirecting to a same-site relative path — blocks
    open-redirect attacks via a crafted ?next= value."""
    if not target or not target.startswith("/") or target.startswith("//"):
        return False
    return True


@app.after_request
def set_security_headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers.setdefault(
        "Permissions-Policy", "camera=(), microphone=(), geolocation=()"
    )
    response.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com; "
        "img-src 'self' data:; "
        "script-src 'self' 'unsafe-inline' https://challenges.cloudflare.com https://checkout.razorpay.com "
        "https://www.gstatic.com https://www.google.com https://apis.google.com; "
        "frame-src https://challenges.cloudflare.com https://api.razorpay.com "
        "https://www.google.com https://*.firebaseapp.com; "
        "connect-src 'self' https://api.razorpay.com https://lumberjack.razorpay.com "
        "https://identitytoolkit.googleapis.com https://securetoken.googleapis.com "
        "https://www.googleapis.com; "
        "base-uri 'self'; "
        "object-src 'none'",
    )

    # HSTS — tell browsers to always use HTTPS for this site
    if request.is_secure:
        response.headers.setdefault(
            "Strict-Transport-Security",
            "max-age=31536000; includeSubDomains",
        )

    # Long-cache static files — Flask defaults to no-cache which forces
    # re-downloading CSS/JS/fonts/images on every page load. Static files
    # served from /static/ are content-addressed by the browser via ETag,
    # so a 1-year cache with immutable is safe and massively cuts repeat
    # page-load time.
    if request.path.startswith("/static/"):
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"

    return response


@app.context_processor
def inject_globals():
    cart = session.get("cart", {})
    cart_count = sum(cart.values()) if cart else 0
    pending_count = 0
    try:
        conn = db.get_db()
        pending_count = conn.execute("SELECT COUNT(*) c FROM orders WHERE status = 'paid'").fetchone()["c"]
        conn.close()
    except Exception:
        pass
    return {
        "csrf_token": get_csrf_token,
        "cart_count": cart_count,
        "pending_count": pending_count,
        "turnstile_enabled": turnstile_enabled(),
        "turnstile_site_key": config.TURNSTILE_SITE_KEY,
        "firebase_auth_enabled": firebase_auth_enabled(),
        "firebase_config": {
            "apiKey": config.FIREBASE_API_KEY,
            "authDomain": config.FIREBASE_AUTH_DOMAIN,
            "projectId": config.FIREBASE_PROJECT_ID,
            "appId": config.FIREBASE_APP_ID,
            "messagingSenderId": config.FIREBASE_MESSAGING_SENDER_ID,
            "storageBucket": config.FIREBASE_STORAGE_BUCKET,
        },
        "current_customer_name": session.get("customer_name", ""),
        "current_customer_phone": session.get("customer_phone", ""),
        "current_customer_email": session.get("customer_email", ""),
        "customer_logged_in": bool(session.get("customer_id")),
        "otp_dev_mode": config.OTP_DEV_MODE,
        "settings": get_settings(),
    }


def _is_coupon_active(coupon, now_str=None):
    """Check if a coupon is active, not expired, not started yet, and not
    exhausted by usage limits. Returns True if the coupon is usable right now."""
    if not coupon:
        return False
    if not coupon["active"]:
        return False
    if now_str is None:
        now_str = datetime.now(timezone.utc).isoformat()
    if coupon["starts_at"] and coupon["starts_at"] > now_str:
        return False
    if coupon["expires_at"] and coupon["expires_at"] < now_str:
        return False
    if coupon["usage_limit"] is not None and coupon["used_count"] >= coupon["usage_limit"]:
        return False
    return True


def _coupon_discount(coupon, base_price):
    """Calculate the discount amount for a coupon against a base price.
    Returns a non-negative int that never exceeds base_price - 1 (so the
    customer always pays at least ₹1)."""
    if coupon["discount_type"] == "percent":
        discount = int(round(base_price * coupon["discount_value"] / 100))
    else:
        discount = coupon["discount_value"]
    discount = max(0, min(discount, base_price - 1 if base_price > 0 else 0))
    return discount


def get_auto_coupons(conn, items, subtotal, product_id=None):
    """Return a list of auto-applicable coupons for the current cart/visitor.
    Checks trigger conditions (cart threshold, product-specific, customer
    segment, URL-driven) and skips expired/inactive coupons. If product_id
    is given (single-product page), checks against that product instead of
    the cart.

    Returns a list of coupon Row objects, sorted by discount (best first).
    """
    now_str = datetime.now(timezone.utc).isoformat()
    coupons = conn.execute("SELECT * FROM coupons WHERE active = 1").fetchall()
    customer_id = session.get("customer_id")
    is_logged_in = bool(customer_id)
    is_new_user = False
    if is_logged_in:
        order_count = conn.execute(
            "SELECT COUNT(*) c FROM orders WHERE customer_id = ? AND status IN ('paid','delivered')",
            (customer_id,)
        ).fetchone()["c"]
        is_new_user = order_count == 0

    # URL-driven coupon stored in session
    url_coupon_code = session.get("url_coupon_code", "")
    url_coupon = None
    if url_coupon_code:
        url_coupon = conn.execute(
            "SELECT * FROM coupons WHERE code = ? AND active = 1", (url_coupon_code.upper(),)
        ).fetchone()

    results = []
    cart_product_ids = set()
    if items:
        cart_product_ids = {it["product"]["id"] for it in items}

    for c in coupons:
        if not _is_coupon_active(c, now_str):
            continue

        trigger = c["trigger_type"]

        if trigger == "manual":
            # Manual coupons only apply if the user types the code — skip auto
            # unless it's the URL-driven one matching this code
            if url_coupon and url_coupon["id"] == c["id"]:
                results.append(c)
            continue

        if trigger == "url_driven":
            # URL-driven coupons only apply when the code was passed via URL
            # and stored in session
            if url_coupon and url_coupon["id"] == c["id"]:
                results.append(c)
            continue

        if trigger == "cart_threshold":
            if not items:
                continue
            min_val = c["min_cart_value"] or 0
            if subtotal >= min_val:
                results.append(c)
            continue

        if trigger == "product_specific":
            target_pid = c["target_product_id"]
            if not target_pid:
                continue
            if product_id is not None:
                if product_id == target_pid:
                    results.append(c)
            elif target_pid in cart_product_ids:
                results.append(c)
            continue

        if trigger == "customer_segment":
            segment = c["customer_segment"] or "all"
            if segment == "all":
                results.append(c)
            elif segment == "new_user" and is_new_user:
                results.append(c)
            elif segment == "logged_in" and is_logged_in:
                results.append(c)
            continue

    # Sort by best discount (descending). For cart, use subtotal; for single
    # product, use product price.
    def discount_amount(c):
        if items:
            base = subtotal
        elif product_id:
            p = conn.execute("SELECT price FROM products WHERE id = ?", (product_id,)).fetchone()
            base = p["price"] if p else 0
        else:
            base = 0
        return _coupon_discount(c, base)

    results.sort(key=discount_amount, reverse=True)
    return results


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
    sort = request.args.get("sort", "")
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

    # Apply sort if requested (default: by position, then newest first)
    if sort == "price_low":
        products = sorted(products, key=lambda p: p["price"])
    elif sort == "price_high":
        products = sorted(products, key=lambda p: p["price"], reverse=True)
    elif sort == "newest":
        products = sorted(products, key=lambda p: p["id"], reverse=True)
    elif sort == "name":
        products = sorted(products, key=lambda p: p["name"].lower())

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

    cutoff = (datetime.now(timezone.utc) - timedelta(days=14)).isoformat()
    new_product_ids = {p["id"] for p in products if p["created_at"] and p["created_at"] >= cutoff}

    return render_template(
        "index.html", settings=settings, sections=sections,
        products=products, product_images=product_images,
        categories=categories, active_category=category,
        testimonials=testimonials, faqs=faqs, search_query=query,
        new_product_ids=new_product_ids, sort=sort,
    )


@app.route("/api/search")
def api_search():
    """Instant search for the nav search dropdown. Returns JSON."""
    q = (request.args.get("q") or "").strip()
    if len(q) < 2:
        return jsonify({"results": []})
    conn = db.get_db()
    like = f"%{q}%"
    products = conn.execute(
        """SELECT id, name, slug, category, price FROM products
           WHERE active = 1 AND (name LIKE ? OR short_description LIKE ? OR category LIKE ?)
           ORDER BY position ASC, id DESC LIMIT 8""",
        (like, like, like),
    ).fetchall()
    results = []
    for p in products:
        img = conn.execute(
            "SELECT filename FROM product_images WHERE product_id = ? ORDER BY position ASC LIMIT 1",
            (p["id"],),
        ).fetchone()
        results.append({
            "name": p["name"],
            "slug": p["slug"],
            "category": p["category"],
            "price": p["price"],
            "image": img["filename"] if img else None,
        })
    conn.close()
    return jsonify({"results": results})


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

    # Track recently viewed products in session (keep last 8, exclude current)
    viewed = session.get("recently_viewed", [])
    pid_str = str(product["id"])
    if pid_str in viewed:
        viewed.remove(pid_str)
    viewed.insert(0, pid_str)
    session["recently_viewed"] = viewed[:8]
    session.modified = True

    return render_template(
        "product.html", settings=settings, product=product,
        images=[i["filename"] for i in images],
        razorpay_key=config.RAZORPAY_KEY_ID,
        related=related, related_images=related_images,
    )


@app.route("/api/create-order", methods=["POST"])
def api_create_order():
    check_csrf_api()
    if rate_limited("create-order", max_attempts=8, window_seconds=60):
        return jsonify({"error": "Too many attempts — please wait a minute and try again."}), 429

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
            customer_phone, amount, coupon_code, discount_amount, razorpay_order_id,
            status, created_at, customer_id)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'created', ?, ?)""",
        (order_ref, product["id"], product["name"], name, email, phone,
         final_amount, applied_code, discount_amount, rzp_order["id"], db.now(),
         session.get("customer_id")),
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


@app.route("/api/cart/preview")
def api_cart_preview():
    """Return cart items for the nav hover preview dropdown."""
    conn = db.get_db()
    items, subtotal = get_cart_items(conn)
    result = []
    for it in items:
        img = conn.execute(
            "SELECT filename FROM product_images WHERE product_id = ? ORDER BY position ASC LIMIT 1",
            (it["product"]["id"],),
        ).fetchone()
        result.append({
            "name": it["product"]["name"],
            "slug": it["product"]["slug"],
            "quantity": it["quantity"],
            "price": it["product"]["price"],
            "line_total": it["line_total"],
            "image": img["filename"] if img else None,
        })
    conn.close()
    return jsonify({"items": result, "subtotal": subtotal, "count": sum(it["quantity"] for it in items)})


@app.route("/api/auto-coupons")
def api_auto_coupons():
    """Return auto-applicable coupons for the current cart or a specific product.
    Query params: product_id (optional) — if given, checks against that product
    instead of the cart. Returns the best matching coupon(s) with discount info."""
    product_id = request.args.get("product_id", type=int)
    conn = db.get_db()
    if product_id:
        items = None
        subtotal = 0
    else:
        items, subtotal = get_cart_items(conn)

    auto_coupons = get_auto_coupons(conn, items, subtotal, product_id=product_id)

    results = []
    for c in auto_coupons:
        base = subtotal if items else 0
        if product_id and not items:
            p = conn.execute("SELECT price FROM products WHERE id = ?", (product_id,)).fetchone()
            base = p["price"] if p else 0
        discount = _coupon_discount(c, base)
        final = base - discount if base > 0 else 0
        results.append({
            "id": c["id"],
            "code": c["code"],
            "discount_type": c["discount_type"],
            "discount_value": c["discount_value"],
            "trigger_type": c["trigger_type"],
            "discount_amount": discount,
            "final_price": final,
            "auto_apply": bool(c["auto_apply"]),
            "description": _coupon_description(c),
        })
    conn.close()
    return jsonify({"coupons": results, "best": results[0] if results else None})


def _coupon_description(c):
    """Human-readable description of what a coupon does and how it triggers."""
    parts = []
    if c["discount_type"] == "percent":
        parts.append(f"{c['discount_value']}% off")
    else:
        parts.append(f"₹{c['discount_value']} off")

    trigger = c["trigger_type"]
    if trigger == "cart_threshold":
        parts.append(f"on orders over ₹{c['min_cart_value'] or 0}")
    elif trigger == "product_specific":
        parts.append("on this product")
    elif trigger == "customer_segment":
        seg = c["customer_segment"]
        if seg == "new_user":
            parts.append("for new customers")
        elif seg == "logged_in":
            parts.append("for signed-in customers")
    elif trigger == "url_driven":
        parts.append("from your referral link")

    return " ".join(parts)


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
    check_csrf_api()
    if rate_limited("cart-add", max_attempts=40, window_seconds=60):
        return jsonify({"error": "Too many requests — please slow down."}), 429
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
    check_csrf()
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
    check_csrf()
    cart = session.get("cart", {})
    cart.pop(str(product_id), None)
    session["cart"] = cart
    session.modified = True
    flash("Item removed from cart.", "success")
    return redirect(url_for("view_cart"))


@app.route("/cart/clear", methods=["POST"])
def cart_clear():
    check_csrf()
    session["cart"] = {}
    session.modified = True
    return redirect(url_for("view_cart"))


@app.route("/api/cart/apply-coupon", methods=["POST"])
def api_cart_apply_coupon():
    check_csrf_api()
    if rate_limited("apply-coupon", max_attempts=15, window_seconds=60):
        return jsonify({"error": "Too many attempts — please wait a minute and try again."}), 429
    data = request.get_json(force=True, silent=True) or {}
    code = (data.get("code") or "").strip().upper()
    conn = db.get_db()
    items, subtotal = get_cart_items(conn)
    coupon = conn.execute("SELECT * FROM coupons WHERE code = ? AND active = 1", (code,)).fetchone()
    conn.close()
    if not items:
        return jsonify({"error": "Your cart is empty."}), 400
    if not coupon or not _is_coupon_active(coupon):
        return jsonify({"error": "That coupon code isn't valid or has expired."}), 400
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
    check_csrf_api()
    if rate_limited("create-order", max_attempts=8, window_seconds=60):
        return jsonify({"error": "Too many attempts — please wait a minute and try again."}), 429
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
            customer_phone, amount, coupon_code, discount_amount, razorpay_order_id,
            status, created_at, customer_id)
           VALUES (?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, 'created', ?, ?)""",
        (order_ref, summary_name, name, email, phone,
         final_amount, applied_code, discount_amount, rzp_order["id"], db.now(),
         session.get("customer_id")),
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
    check_csrf_api()
    if rate_limited("apply-coupon", max_attempts=15, window_seconds=60):
        return jsonify({"error": "Too many attempts — please wait a minute and try again."}), 429
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
    if not coupon or not _is_coupon_active(coupon):
        return jsonify({"error": "That coupon code isn't valid or has expired."}), 400
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
    check_csrf_api()
    data = request.get_json(force=True, silent=True) or {}
    rzp_order_id = data.get("razorpay_order_id")
    rzp_payment_id = data.get("razorpay_payment_id")
    rzp_signature = data.get("razorpay_signature")

    if not all([rzp_order_id, rzp_payment_id, rzp_signature]):
        return jsonify({"error": "Missing payment details."}), 400

    conn = db.get_db()
    order = conn.execute(
        "SELECT * FROM orders WHERE razorpay_order_id = ?", (rzp_order_id,)
    ).fetchone()

    if not order:
        conn.close()
        return jsonify({"error": "Order not found."}), 404

    # Idempotency guard: if this order was already confirmed paid (or moved
    # further, e.g. delivered), don't re-run any of the side effects below —
    # a retried/replayed call just gets the same success response again,
    # without double-crediting coupon usage or re-sending the confirmation email.
    if order["status"] in ("paid", "delivered"):
        conn.close()
        return jsonify({"success": True, "order_ref": order["order_ref"]})

    valid = rzp.verify_payment_signature(rzp_order_id, rzp_payment_id, rzp_signature)

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

    # Per-product automatic delivery: if every item on this order is set to
    # "automatic" in the admin panel, the order is delivered instantly —
    # no human review step. Mixed carts (some auto, some manual) are left
    # for manual review so nothing gets half-delivered.
    auto_message = _maybe_auto_deliver(conn, order, order_items)
    conn.commit()
    conn.close()

    if order_items:
        session["cart"] = {}
        session.modified = True

    if order_items:
        lines = "\n".join(
            f"  - {it['product_name']} x{it['quantity']} — "
            f"{'{:,}'.format(it['line_total'])}"
            for it in order_items
        )
        item_block = f"Items:\n{lines}\n\n"
        item_line = ", ".join(f"{it['product_name']} x{it['quantity']}" for it in order_items)
    else:
        item_block = f"Item: {order['product_name']}\n\n"
        item_line = order["product_name"]

    if email_enabled():
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
        if auto_message is not None:
            send_email(
                order["customer_email"],
                f"Your order {order['order_ref']} has been delivered",
                f"Hi {order['customer_name']},\n\n"
                f"Great news — your order for \"{item_line}\" is ready, delivered instantly!\n\n"
                f"{auto_message}\n\n"
                f"Order reference: {order['order_ref']}\n\nThank you for shopping with us.",
            )

    return jsonify({"success": True, "order_ref": order["order_ref"], "auto_delivered": auto_message is not None})


def _maybe_auto_deliver(conn, order, order_items):
    """If every product in this order has delivery_mode='automatic', marks
    the order delivered right away and returns the combined delivery
    message. Otherwise leaves the order at 'paid' for manual review and
    returns None. Must be called before conn.commit()/conn.close()."""
    if order_items:
        product_ids = [it["product_id"] for it in order_items if it["product_id"]]
        if len(product_ids) != len(order_items):
            return None  # a purchased product was later deleted — play it safe
        placeholders = ",".join("?" * len(product_ids))
        rows = conn.execute(
            f"SELECT id, name, delivery_mode, auto_delivery_content FROM products "
            f"WHERE id IN ({placeholders})",
            product_ids,
        ).fetchall()
        by_id = {r["id"]: r for r in rows}
        if len(by_id) != len(set(product_ids)):
            return None
        if not all(by_id[pid]["delivery_mode"] == "automatic" for pid in product_ids):
            return None
        parts = [
            f"{by_id[it['product_id']]['name']}:\n{(by_id[it['product_id']]['auto_delivery_content'] or '').strip()}"
            for it in order_items
            if (by_id[it["product_id"]]["auto_delivery_content"] or "").strip()
        ]
        message = "\n\n".join(parts).strip()
    else:
        if not order["product_id"]:
            return None
        product = conn.execute(
            "SELECT delivery_mode, auto_delivery_content FROM products WHERE id = ?",
            (order["product_id"],),
        ).fetchone()
        if not product or product["delivery_mode"] != "automatic":
            return None
        message = (product["auto_delivery_content"] or "").strip()

    conn.execute(
        "UPDATE orders SET status = 'delivered', delivery_message = ?, "
        "delivered_at = ?, auto_delivered = 1 WHERE id = ?",
        (message, db.now(), order["id"]),
    )
    return message


@app.route("/track", methods=["GET", "POST"])
def track_order():
    order = None
    searched = False
    prefill_ref = request.args.get("order_ref", "")
    prefill_email = request.args.get("email", "")

    if request.method == "POST":
        check_csrf()
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


# ============================================================= CUSTOMER AUTH (Firebase phone/OTP)

# ============================================================= CUSTOMER AUTH (self-contained OTP)

@app.route("/auth/send-otp", methods=["POST"])
def auth_send_otp():
    """Generate a 6-digit OTP, store it in the database with an expiry, and
    return it to the client in dev mode (so the flow works without an SMS
    provider). In production with a real SMS gateway, the code would be sent
    via SMS and not returned in the response."""
    check_csrf_api()
    if rate_limited("send-otp", max_attempts=5, window_seconds=60):
        return jsonify({"error": "Too many attempts. Please wait a minute and try again."}), 429

    data = request.get_json(force=True, silent=True) or {}
    phone = (data.get("phone") or "").strip()

    if not phone or not phone.startswith("+"):
        return jsonify({"error": "Please enter your phone number with the country code, e.g. +919876543210."}), 400
    if len(phone) < 8 or len(phone) > 16:
        return jsonify({"error": "That phone number doesn't look right. Please check and try again."}), 400

    code = generate_otp_code()
    conn = db.get_db()
    store_otp(conn, phone, code)
    conn.commit()
    conn.close()

    response = {"success": True, "message": "Code sent!"}
    if config.OTP_DEV_MODE:
        response["dev_code"] = code
    return jsonify(response)


@app.route("/auth/verify-otp", methods=["POST"])
def auth_verify_otp():
    """Verify the OTP code. If valid, create or update the customer account
    and log them into a Flask session. If name/email are provided, they're
    saved with the account (new users must provide a name at least)."""
    check_csrf_api()
    if rate_limited("verify-otp", max_attempts=10, window_seconds=60):
        return jsonify({"error": "Too many attempts. Please wait a minute and try again."}), 429

    data = request.get_json(force=True, silent=True) or {}
    phone = (data.get("phone") or "").strip()
    code = (data.get("code") or "").strip()
    name = (data.get("name") or "").strip()
    email = (data.get("email") or "").strip()

    if not phone or not code:
        return jsonify({"error": "Please enter the code we sent you."}), 400

    conn = db.get_db()
    valid, stored_name, stored_email = verify_otp_code(conn, phone, code)
    if not valid:
        conn.close()
        return jsonify({"error": "That code is wrong or expired. Please try again."}), 400

    # Use provided name/email, or fall back to what was stored with the OTP
    final_name = name or stored_name or ""
    final_email = email or stored_email or ""

    # Look up or create the customer by phone number
    customer = conn.execute("SELECT * FROM customers WHERE phone = ?", (phone,)).fetchone()
    if customer:
        new_name = final_name or customer["name"]
        new_email = final_email or customer["email"]
        conn.execute(
            "UPDATE customers SET name = ?, email = ?, last_login_at = ? WHERE id = ?",
            (new_name, new_email, db.now(), customer["id"]),
        )
        customer_id = customer["id"]
    else:
        cur = conn.execute(
            """INSERT INTO customers (phone, name, email, created_at, last_login_at)
               VALUES (?, ?, ?, ?, ?)""",
            (phone, final_name, final_email, db.now(), db.now()),
        )
        customer_id = cur.lastrowid
        new_name, new_email = final_name, final_email

    conn.commit()
    conn.close()

    session["customer_id"] = customer_id
    session["customer_name"] = new_name
    session["customer_phone"] = phone
    session["customer_email"] = new_email
    return jsonify({"success": True, "name": new_name, "phone": phone, "email": new_email})


@app.route("/auth/phone/verify", methods=["POST"])
def auth_phone_verify():
    """Called from the browser right after Firebase confirms the SMS code.
    We re-verify the ID token server-side (never trust the client's word for
    who they are), then create or look up the matching customer account and
    log them into a normal Flask session — no password involved."""
    check_csrf_api()
    if rate_limited("phone-verify", max_attempts=10, window_seconds=60):
        return jsonify({"error": "Too many attempts — please wait a minute and try again."}), 429
    if not firebase_auth_enabled():
        return jsonify({"error": "Phone sign-in isn't set up on this site yet."}), 503

    data = request.get_json(force=True, silent=True) or {}
    id_token = data.get("id_token", "")
    name = (data.get("name") or "").strip()
    email = (data.get("email") or "").strip()

    decoded = verify_firebase_id_token(id_token)
    if not decoded:
        return jsonify({"error": "We couldn't verify that code. Please try again."}), 400

    uid = decoded.get("uid") or decoded.get("sub")
    phone = decoded.get("phone_number", "")
    if not uid:
        return jsonify({"error": "We couldn't verify that code. Please try again."}), 400

    conn = db.get_db()
    customer = conn.execute("SELECT * FROM customers WHERE firebase_uid = ?", (uid,)).fetchone()
    if customer:
        new_name = name or customer["name"]
        new_email = email or customer["email"]
        conn.execute(
            "UPDATE customers SET name = ?, email = ?, phone = ?, last_login_at = ? WHERE id = ?",
            (new_name, new_email, phone, db.now(), customer["id"]),
        )
        customer_id = customer["id"]
    else:
        cur = conn.execute(
            """INSERT INTO customers (firebase_uid, phone, name, email, created_at, last_login_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (uid, phone, name, email, db.now(), db.now()),
        )
        customer_id = cur.lastrowid
        new_name, new_email = name, email
    conn.commit()
    conn.close()

    session["customer_id"] = customer_id
    session["customer_name"] = new_name
    session["customer_phone"] = phone
    session["customer_email"] = new_email
    return jsonify({"success": True, "name": new_name, "phone": phone, "email": new_email})


@app.route("/auth/update-profile", methods=["POST"])
def auth_update_profile():
    """Update the logged-in customer's name and email. Used after OTP
    verification when a new user needs to provide their name."""
    check_csrf_api()
    if not session.get("customer_id"):
        return jsonify({"error": "Please sign in first."}), 401
    data = request.get_json(force=True, silent=True) or {}
    name = (data.get("name") or "").strip()
    email = (data.get("email") or "").strip()
    if not name:
        return jsonify({"error": "Please enter your name."}), 400
    conn = db.get_db()
    conn.execute(
        "UPDATE customers SET name = ?, email = ? WHERE id = ?",
        (name, email, session["customer_id"]),
    )
    conn.commit()
    conn.close()
    session["customer_name"] = name
    session["customer_email"] = email
    return jsonify({"success": True, "name": name, "phone": session.get("customer_phone", ""), "email": email})


@app.route("/auth/logout", methods=["POST"])
def auth_logout():
    check_csrf()
    for key in ("customer_id", "customer_name", "customer_phone", "customer_email"):
        session.pop(key, None)
    flash("Signed out.", "success")
    return redirect(request.referrer or url_for("home"))


@app.route("/newsletter/subscribe", methods=["POST"])
def newsletter_subscribe():
    check_csrf()
    if rate_limited("newsletter", max_attempts=5, window_seconds=60):
        flash("Too many attempts — please wait a minute and try again.", "error")
        return redirect(url_for("home") + "#newsletter")
    if turnstile_enabled() and not verify_turnstile(request.form.get("cf-turnstile-response", "")):
        flash("Please complete the verification and try again.", "error")
        return redirect(url_for("home") + "#newsletter")
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


@app.route("/favicon.ico")
def favicon():
    path = os.path.join(app.static_folder, "favicon.ico")
    if os.path.exists(path):
        return app.send_static_file("favicon.ico")
    return "", 204


@app.route("/sitemap.xml")
def sitemap():
    conn = db.get_db()
    products = conn.execute("SELECT slug, created_at FROM products WHERE active = 1").fetchall()
    conn.close()
    urls = [url_for("home", _external=True), url_for("track_order", _external=True)]
    xml = ['<?xml version="1.0" encoding="UTF-8"?>', '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for u in urls:
        xml.append(f"<url><loc>{u}</loc><lastmod>{datetime.now(timezone.utc).date()}</lastmod></url>")
    for p in products:
        loc = url_for("product_detail", slug=p["slug"], _external=True)
        lastmod = (p["created_at"][:10] if p["created_at"] else datetime.now(timezone.utc).date())
        xml.append(f"<url><loc>{loc}</loc><lastmod>{lastmod}</lastmod></url>")
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
        if rate_limited("admin-login", max_attempts=8, window_seconds=300):
            flash("Too many login attempts — please wait a few minutes and try again.", "error")
            return render_template("admin/login.html")
        if turnstile_enabled() and not verify_turnstile(request.form.get("cf-turnstile-response", "")):
            flash("Please complete the verification and try again.", "error")
            return render_template("admin/login.html")
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
            next_url = request.args.get("next", "")
            return redirect(next_url if is_safe_redirect_target(next_url) else url_for("admin_dashboard"))
        flash("Incorrect username or password.", "error")
    return render_template("admin/login.html")


@app.route("/admin/logout", methods=["POST"])
@login_required
def admin_logout():
    check_csrf()
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
    today = datetime.now(timezone.utc).date()
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
        checkbox_keys = {"auto_deliver_enabled", "auto_email_enabled", "low_stock_alerts"}
        for key in db.DEFAULT_SETTINGS.keys():
            if key in checkbox_keys:
                value = "true" if request.form.get(key) else "false"
            else:
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
    q = (request.args.get("q") or "").strip()
    cat = (request.args.get("category") or "").strip()
    conn = db.get_db()
    clauses = []
    params = []
    if q:
        clauses.append("(name LIKE ? OR short_description LIKE ? OR category LIKE ?)")
        like = f"%{q}%"
        params += [like, like, like]
    if cat:
        clauses.append("category = ?")
        params.append(cat)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    products = conn.execute(
        f"SELECT * FROM products {where} ORDER BY position ASC, id DESC", params
    ).fetchall()
    thumbs = {}
    for p in products:
        img = conn.execute(
            "SELECT filename FROM product_images WHERE product_id = ? ORDER BY position ASC LIMIT 1",
            (p["id"],),
        ).fetchone()
        thumbs[p["id"]] = img["filename"] if img else None
    categories = [
        r["category"] for r in conn.execute(
            "SELECT DISTINCT category FROM products WHERE category != '' ORDER BY category ASC"
        ).fetchall()
    ]
    conn.close()
    return render_template("admin/products.html", products=products, thumbs=thumbs, q=q, cat=cat, categories=categories)


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
    delivery_mode = request.form.get("delivery_mode", "manual").strip()
    if delivery_mode not in ("manual", "automatic"):
        delivery_mode = "manual"
    auto_delivery_content = request.form.get("auto_delivery_content", "").strip()
    ribbon = request.form.get("ribbon", "").strip()
    compare_price_raw = request.form.get("compare_price", "").strip()

    # Validate compare_price
    compare_price = None
    if compare_price_raw:
        try:
            compare_price = int(float(compare_price_raw))
            if compare_price < 0:
                raise ValueError
        except ValueError:
            flash("Please enter a valid compare-at price.", "error")
            return redirect(request.referrer or url_for("admin_products"))

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
               price=?, category=?, active=?, delivery_mode=?, auto_delivery_content=?,
               ribbon=?, compare_price=? WHERE id=?""",
            (name, short_description, description, price, category, active,
             delivery_mode, auto_delivery_content, ribbon, compare_price, product_id),
        )
        # Update slug if the name changed (keep it in sync)
        new_slug_base = slugify(name)
        current_slug = conn.execute(
            "SELECT slug FROM products WHERE id = ?", (product_id,)
        ).fetchone()["slug"]
        if current_slug != new_slug_base and not current_slug.startswith(new_slug_base + "-"):
            new_slug = new_slug_base
            i = 2
            while conn.execute(
                "SELECT 1 FROM products WHERE slug = ? AND id != ?", (new_slug, product_id)
            ).fetchone():
                new_slug = f"{new_slug_base}-{i}"
                i += 1
            conn.execute("UPDATE products SET slug = ? WHERE id = ?", (new_slug, product_id))
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
               category, active, position, created_at, delivery_mode, auto_delivery_content,
               ribbon, compare_price)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (name, slug, short_description, description, price, category, active, max_pos + 1, db.now(),
             delivery_mode, auto_delivery_content, ribbon, compare_price),
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


@app.route("/admin/products/move/<int:product_id>/<direction>", methods=["POST"])
@login_required
def admin_product_move(product_id, direction):
    check_csrf()
    conn = db.get_db()
    products = conn.execute("SELECT * FROM products ORDER BY position ASC, id ASC").fetchall()
    ids = [p["id"] for p in products]
    idx = ids.index(product_id)
    swap_idx = idx - 1 if direction == "up" else idx + 1
    if 0 <= swap_idx < len(ids):
        a, b = products[idx], products[swap_idx]
        conn.execute("UPDATE products SET position = ? WHERE id = ?", (b["position"], a["id"]))
        conn.execute("UPDATE products SET position = ? WHERE id = ?", (a["position"], b["id"]))
        conn.commit()
    conn.close()
    return redirect(url_for("admin_products", q=request.args.get("q", ""), category=request.args.get("category", "")))


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
    products = conn.execute("SELECT id, name FROM products ORDER BY name ASC").fetchall()
    conn.close()
    now_iso = datetime.now(timezone.utc).isoformat()
    return render_template("admin/coupons.html", coupons=coupons, products=products, now_iso=now_iso)


@app.route("/admin/coupons/save", methods=["POST"])
@login_required
def admin_coupons_save():
    check_csrf()
    code = request.form.get("code", "").strip().upper()
    discount_type = request.form.get("discount_type", "percent")
    discount_value_raw = request.form.get("discount_value", "0").strip()
    usage_limit_raw = request.form.get("usage_limit", "").strip()
    active = 1 if request.form.get("active") else 0

    # Automatic coupon fields
    auto_apply = 1 if request.form.get("auto_apply") else 0
    trigger_type = request.form.get("trigger_type", "manual")
    if trigger_type not in ("manual", "cart_threshold", "product_specific", "customer_segment", "url_driven"):
        trigger_type = "manual"
    min_cart_value_raw = request.form.get("min_cart_value", "").strip()
    target_product_id_raw = request.form.get("target_product_id", "").strip()
    customer_segment = request.form.get("customer_segment", "all")
    if customer_segment not in ("all", "new_user", "logged_in"):
        customer_segment = "all"
    starts_at = request.form.get("starts_at", "").strip() or None
    expires_at = request.form.get("expires_at", "").strip() or None
    # Convert datetime-local inputs to ISO format for storage
    if starts_at:
        try:
            dt = datetime.fromisoformat(starts_at)
            starts_at = dt.isoformat()
        except ValueError:
            starts_at = None
    if expires_at:
        try:
            dt = datetime.fromisoformat(expires_at)
            expires_at = dt.isoformat()
        except ValueError:
            expires_at = None

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
    min_cart_value = int(min_cart_value_raw) if min_cart_value_raw.isdigit() else None
    target_product_id = int(target_product_id_raw) if target_product_id_raw.isdigit() else None

    # If auto_apply is checked, set trigger_type appropriately
    if auto_apply and trigger_type == "manual":
        trigger_type = "cart_threshold"  # sensible default

    conn = db.get_db()
    try:
        conn.execute(
            """INSERT INTO coupons
               (code, discount_type, discount_value, active, usage_limit, created_at,
                auto_apply, trigger_type, min_cart_value, target_product_id,
                customer_segment, starts_at, expires_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (code, discount_type, discount_value, active, usage_limit, db.now(),
             auto_apply, trigger_type, min_cart_value, target_product_id,
             customer_segment, starts_at, expires_at),
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
