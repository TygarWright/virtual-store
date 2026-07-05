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


@app.context_processor
def inject_globals():
    return {"csrf_token": get_csrf_token}


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
    conn.close()
    return render_template(
        "index.html", settings=settings, sections=sections,
        products=products, product_images=product_images,
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
    conn.close()
    return render_template(
        "product.html", settings=settings, product=product,
        images=[i["filename"] for i in images],
        razorpay_key=config.RAZORPAY_KEY_ID,
    )


@app.route("/api/create-order", methods=["POST"])
def api_create_order():
    data = request.get_json(force=True, silent=True) or {}
    product_id = data.get("product_id")
    name = (data.get("name") or "").strip()
    email = (data.get("email") or "").strip()
    phone = (data.get("phone") or "").strip()

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

    order_ref = db.new_order_ref()
    try:
        rzp_order = rzp.create_order(product["price"], receipt=order_ref)
    except Exception:
        conn.close()
        return jsonify({"error": "Could not start payment. Please try again."}), 502

    conn.execute(
        """INSERT INTO orders
           (order_ref, product_id, product_name, customer_name, customer_email,
            customer_phone, amount, razorpay_order_id, status, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'created', ?)""",
        (order_ref, product["id"], product["name"], name, email, phone,
         product["price"], rzp_order["id"], db.now()),
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
    conn.commit()
    conn.close()

    if email_enabled():
        send_email(
            order["customer_email"],
            f"We've received your order {order['order_ref']}",
            f"Hi {order['customer_name']},\n\n"
            f"Thank you for your purchase of \"{order['product_name']}\".\n"
            f"Your payment has been received and your order is now being prepared.\n\n"
            f"Order reference: {order['order_ref']}\n"
            f"You can check its status any time at our order tracking page.\n\n"
            f"We'll be in touch shortly.",
        )

    return jsonify({"success": True, "order_ref": order["order_ref"]})


@app.route("/track", methods=["GET", "POST"])
def track_order():
    order = None
    searched = False
    if request.method == "POST":
        searched = True
        order_ref = (request.form.get("order_ref") or "").strip().upper()
        email = (request.form.get("email") or "").strip().lower()
        conn = db.get_db()
        order = conn.execute(
            "SELECT * FROM orders WHERE order_ref = ? AND lower(customer_email) = ?",
            (order_ref, email),
        ).fetchone()
        conn.close()
    settings = get_settings()
    return render_template("order_status.html", settings=settings, order=order, searched=searched)


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
    conn.close()
    return render_template("admin/dashboard.html", stats=stats, recent_orders=recent_orders,
                            razorpay_configured=rzp.is_configured())


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
    return render_template("admin/product_form.html", product=None, images=[])


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
    return render_template("admin/product_form.html", product=product, images=images)


def _save_product(product_id):
    name = request.form.get("name", "").strip()
    short_description = request.form.get("short_description", "").strip()
    description = request.form.get("description", "").strip()
    price_raw = request.form.get("price", "0").strip()
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
               price=?, active=? WHERE id=?""",
            (name, short_description, description, price, active, product_id),
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
               active, position, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (name, slug, short_description, description, price, active, max_pos + 1, db.now()),
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
    conn = db.get_db()
    if status_filter:
        orders = conn.execute(
            "SELECT * FROM orders WHERE status = ? ORDER BY id DESC", (status_filter,)
        ).fetchall()
    else:
        orders = conn.execute("SELECT * FROM orders ORDER BY id DESC").fetchall()
    conn.close()
    return render_template("admin/orders.html", orders=orders, status_filter=status_filter)


@app.route("/admin/orders/<int:order_id>")
@login_required
def admin_order_detail(order_id):
    conn = db.get_db()
    order = conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
    conn.close()
    if not order:
        abort(404)
    return render_template("admin/order_detail.html", order=order, email_enabled=email_enabled())


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
    conn.execute(
        "UPDATE orders SET status = 'delivered', delivery_message = ?, delivered_at = ? WHERE id = ?",
        (message, db.now(), order_id),
    )
    conn.commit()
    conn.close()

    if email_enabled():
        send_email(
            order["customer_email"],
            f"Your order {order['order_ref']} has been delivered",
            f"Hi {order['customer_name']},\n\n"
            f"Great news — your order for \"{order['product_name']}\" is ready!\n\n"
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
