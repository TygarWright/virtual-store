"""
Storefront Blueprint
"""
from flask import send_file, send_from_directory, Response, g, Blueprint, render_template, request, redirect, url_for, flash, session, jsonify, abort, current_app
from datetime import datetime, timezone, timedelta
import os

import config
import database as db
from helpers import (
    login_required, get_csrf_token, check_csrf, check_csrf_api, slugify,
    save_product_image, delete_file_quietly, send_email, email_enabled,
    rate_limited, turnstile_enabled, verify_turnstile,
    firebase_auth_enabled, verify_firebase_id_token, prewarm_firebase_certs,
    generate_otp_code, store_otp, verify_otp_code,
    notify_admins_new_order, webpush_notify_admins_new_order,
    whatsapp_enabled, send_whatsapp, twilio_enabled, send_sms,
    allowed_product_file, save_product_file, product_file_path,
    generate_download_tokens, migrate_legacy_product_files,
    customer_login_required,
    track_cart_add, track_cart_contact,
    has_permission,
    _coupon_description, _coupon_discount, _is_coupon_active,
    _delivery_speed_stat, get_primary_image_map,
    _get_webp_path, _track_product_view,
    get_auto_coupons, get_cart_items,
    get_settings,
    get_catalog,
    checkout_enabled,
    is_testing_checkout,
    customer_email_notifications_enabled,
    log_admin_action,
)

import razorpay_client as rzp
import invoicing
# New payment abstraction layer
from payment.gateways import get_payment_gateway, PaymentResult
# State machine enums for clarity
from payment.state_machine import PaymentState, OrderState
# Existing phase2_services for state transitions and other helpers
from phase2_services import (
    mark_payment_captured,
    record_webhook_event,
    mark_webhook_event_processed,
    issue_entitlement,
    record_download_audit,
    transition_order_state,
    transition_payment_state,    enqueue_email_or_send,
)

# We'll create the blueprint
from extensions import limiter, csrf
from governance_service import coupon_discount_with_margin, cart_coupon_margin
from payment.inventory import reserve_stock_batch, release_stock, commit_stock
from intelligence_service import rank_products, get_recommendations, get_personalized_recommendations, record_event

storefront_bp = Blueprint('storefront', __name__)

@storefront_bp.route("/", methods=["GET", "HEAD"])
def home():
    settings = get_settings()
    catalog = get_catalog()

    category = (request.args.get("category") or "").strip()
    query = (request.args.get("q") or "").strip()
    sort = request.args.get("sort", "")
    price_min = request.args.get("price_min", "")
    price_max = request.args.get("price_max", "")
    delivery_type = request.args.get("delivery_type", "")
    rating = request.args.get("rating", "")

    # Build active_filters dict for the template
    active_filters = {}
    if category:
        active_filters["category"] = category
    if query:
        active_filters["q"] = query
    if sort:
        active_filters["sort"] = sort
    if price_min:
        active_filters["price_min"] = price_min
    if price_max:
        active_filters["price_max"] = price_max
    if delivery_type:
        active_filters["delivery_type"] = delivery_type
    if rating:
        active_filters["rating"] = rating

    # ── Filter and sort in Python on cached data (zero Turso queries) ──
    products = list(catalog["products"])

    # ── Intelligent search ranking ──
    if query:
        products = rank_products(products, query)
        try:
            record_event("search", query=query, customer_id=session.get("customer_id"), session_id=session.get("cart_id", ""), request=request)
        except Exception:
            current_app.logger.debug("Search analytics unavailable", exc_info=True)

    # ── Category filter ──
    if category:
        products = [p for p in products if p["category"] == category]

    # ── Price range filter ──
    if price_min:
        try:
            min_val = int(price_min)
            products = [p for p in products if p["price"] >= min_val]
        except (ValueError, TypeError):
            pass
    if price_max:
        try:
            max_val = int(price_max)
            products = [p for p in products if p["price"] <= max_val]
        except (ValueError, TypeError):
            pass

    # ── Delivery type filter ──
    if delivery_type:
        dt_values = [d.strip() for d in delivery_type.split(",") if d.strip()]
        if dt_values:
            products = [p for p in products if p.get("delivery_content_type", "") in dt_values]

    # ── Rating filter (fetch average ratings for visible products) ──
    if rating:
        try:
            min_rating = int(rating)
            # Build a map of product_id -> avg_rating for products in the current set
            if products:
                conn = db.get_db()
                try:
                    pids = [p["id"] for p in products]
                    placeholders = ",".join("?" for _ in pids)
                    rows = conn.execute(
                        f"""SELECT product_id, ROUND(AVG(CAST(rating AS REAL)), 1) AS avg_rating
                             FROM reviews WHERE visible = 1 AND product_id IN ({placeholders})
                             GROUP BY product_id""",
                        pids,
                    ).fetchall()
                    avg_ratings = {r["product_id"]: r["avg_rating"] for r in rows}
                    products = [p for p in products if avg_ratings.get(p["id"], 0) >= min_rating]
                except Exception:
                    pass
                finally:
                    conn.close()
        except (ValueError, TypeError):
            pass

    # ── Compute min/max prices from all catalog products (for range inputs) ──
    all_products = catalog["products"]
    filter_price_min = min(p["price"] for p in all_products) if all_products else 0
    filter_price_max = max(p["price"] for p in all_products) if all_products else 0

    # ── Personalize for returning customers ──
    owned_ids = set()
    if not sort and not query and not category and not price_min and not price_max and not delivery_type and not rating and session.get("customer_id"):
        customer_id = session["customer_id"]
        conn = db.get_db()
        try:
            rows = conn.execute(
                "SELECT DISTINCT oi.product_id FROM orders o JOIN order_items oi ON oi.order_id = o.id WHERE o.customer_id = ? AND o.status = 'delivered'",
                (customer_id,),
            ).fetchall()
            owned_ids = {r["product_id"] for r in rows}
        except Exception:
            pass
        finally:
            conn.close()
    # For returning customers on the default view, promote unpurchased products first
    if owned_ids:
        unpurchased = [p for p in products if p["id"] not in owned_ids]
        purchased = [p for p in products if p["id"] in owned_ids]
        products = unpurchased + purchased

    # ── Sorting ──
    if sort == "price_low":
        products = sorted(products, key=lambda p: p["price"])
    elif sort == "price_high":
        products = sorted(products, key=lambda p: p["price"], reverse=True)
    elif sort == "newest":
        products = sorted(products, key=lambda p: p["id"], reverse=True)
    elif sort == "name":
        products = sorted(products, key=lambda p: p["name"].lower())
    elif sort == "popular":
        products = sorted(products, key=lambda p: p["views"] or 0, reverse=True)

    # Paginate the final filtered/sorted set so very large catalogs do not
    # render hundreds of cards at once. Keep all filtering/sorting decisions
    # server-side and paginate only after personalization has been applied.
    per_page = 24
    try:
        page = max(1, int(request.args.get("page", 1)))
    except (TypeError, ValueError):
        page = 1
    total_products = len(products)
    total_pages = max(1, (total_products + per_page - 1) // per_page)
    page = min(page, total_pages)
    start = (page - 1) * per_page
    page_products = products[start:start + per_page]

    pagination_params = request.args.to_dict(flat=True)
    pagination_params.pop("page", None)

    # Use the cached product_images map
    product_images = catalog["product_images"]

    cutoff = (datetime.now(timezone.utc) - timedelta(days=14)).isoformat()
    new_product_ids = {p["id"] for p in page_products if p["created_at"] and p["created_at"] >= cutoff}

    return render_template(
        "index.html", settings=settings, sections=catalog["sections"],
        products=page_products, product_images=product_images,
        total_products=total_products, page=page, total_pages=total_pages,
        pagination_params=pagination_params,
        categories=catalog["categories"], active_category=category,
        testimonials=catalog["testimonials"], faqs=catalog["faqs"],
        search_query=query, new_product_ids=new_product_ids, sort=sort,
        sold_counts=catalog.get("sold_counts", {}),
        owned_ids=owned_ids,
        active_filters=active_filters,
        filter_price_min=filter_price_min,
        filter_price_max=filter_price_max,
    )




@storefront_bp.route("/api/search")
def api_search():
    """Instant search for the nav search dropdown. Returns JSON."""
    q = (request.args.get("q") or "").strip()
    if len(q) < 2:
        return jsonify({"results": []})
    catalog = get_catalog()
    ranked = rank_products(list(catalog["products"]), q)
    try:
        record_event("search", query=q, customer_id=session.get("customer_id"), session_id=session.get("cart_id", ""), request=request)
    except Exception:
        current_app.logger.debug("Search analytics unavailable", exc_info=True)
    results = []
    for p in ranked[:8]:
        results.append({
            "name": p["name"],
            "slug": p["slug"],
            "category": p["category"],
            "price": p["price"],
            "image": catalog["product_images"].get(p["id"]),
        })
    return jsonify({"results": results})




@storefront_bp.route("/api/recommendations")
def api_recommendations():
    """Return safe personalized/popular recommendations for the storefront."""
    try:
        limit = max(1, min(request.args.get("limit", 8, type=int) or 8, 24))
        items = get_personalized_recommendations(session.get("customer_id"), limit=limit)
        return jsonify({"results": items})
    except Exception:
        current_app.logger.exception("Recommendation request failed")
        return jsonify({"results": []})


@storefront_bp.route("/api/product/<int:product_id>/quick-view")
def api_product_quick_view(product_id):
    """JSON data for the quick view modal."""
    catalog = get_catalog()
    p = catalog["products_by_id"].get(product_id)
    if not p:
        return jsonify({"error": "Not found"}), 404
    return jsonify({
        "id": p["id"],
        "name": p["name"],
        "slug": p["slug"],
        "price": p["price"],
        "compare_price": p["compare_price"],
        "short_description": p["short_description"],
        "category": p["category"],
        "image": catalog["product_images"].get(product_id),
    })




@storefront_bp.route("/api/product/<int:product_id>/reviews")
def api_product_reviews(product_id):
    """JSON list of visible reviews for a product."""
    conn = db.get_db()
    rows = conn.execute(
        """SELECT id, customer_name, rating, title, body, verified, created_at
           FROM reviews WHERE product_id = ? AND visible = 1
           ORDER BY created_at DESC LIMIT 50""",
        (product_id,),
    ).fetchall()
    conn.close()
    reviews = []
    for r in rows:
        reviews.append({
            "id": r["id"],
            "customer_name": r["customer_name"],
            "rating": r["rating"],
            "title": r["title"],
            "body": r["body"],
            "verified": bool(r["verified"]),
            "created_at": r["created_at"],
        })
    return jsonify({"reviews": reviews})




@storefront_bp.route("/api/product/<int:product_id>/reviews/create", methods=["POST"])
def api_product_reviews_create(product_id):
    """Submit a review for a product (must be from a customer who bought it)."""
    check_csrf_api()
    data = request.get_json(force=True, silent=True) or {}
    try:
        rating_val = int(data.get("rating", 5))
    except (ValueError, TypeError):
        return jsonify({"error": "Rating must be a number between 1 and 5."}), 400
    rating = max(1, min(5, rating_val))
    title = (data.get("title") or "").strip()
    body = (data.get("body") or "").strip()
    customer_id = session.get("customer_id")
    customer_name = (session.get("customer_name") or "").strip()
    customer_email = (session.get("customer_email") or "").strip()

    if not customer_id and not customer_name:
        return jsonify({"error": "Please sign in to leave a review."}), 403

    conn = db.get_db()
    # Check if user already reviewed this product
    existing = conn.execute(
        "SELECT id FROM reviews WHERE product_id = ? AND (customer_id = ? OR customer_name = ?) AND customer_name != ''",
        (product_id, customer_id, customer_name),
    ).fetchone()
    if existing:
        conn.close()
        return jsonify({"error": "You have already reviewed this product."}), 409

    # Verify purchase — check both direct product_id and order_items (for cart orders)
    order = conn.execute(
        """SELECT o.id FROM orders o
           LEFT JOIN order_items oi ON oi.order_id = o.id
           WHERE (o.product_id = ? OR oi.product_id = ?)
             AND o.status = 'paid'
             AND (o.customer_id = ? OR o.customer_email = ?)
           ORDER BY o.id DESC LIMIT 1""",
        (product_id, product_id, customer_id, customer_email),
    ).fetchone()
    verified = 1 if order else 0
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()

    conn.execute(
        """INSERT INTO reviews (product_id, order_id, customer_id, customer_name, rating, title, body, verified, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (product_id, order["id"] if order else None, customer_id, customer_name, rating, title, body, verified, now),
    )
    conn.commit()
    conn.close()
    return jsonify({"success": True, "verified": bool(verified)})




@storefront_bp.route("/api/wishlist/add", methods=["POST"])
@customer_login_required
def api_wishlist_add():
    check_csrf_api()
    data = request.get_json(force=True, silent=True) or {}
    product_id = data.get("product_id")
    if not product_id:
        return jsonify({"error": "Missing product."}), 400
    conn = db.get_db()
    existing = conn.execute(
        "SELECT 1 FROM wishlist_items WHERE customer_id = ? AND product_id = ?",
        (session["customer_id"], product_id),
    ).fetchone()
    if existing:
        conn.close()
        return jsonify({"error": "Already in your wishlist."}), 409
    try:
        conn.execute(
            "INSERT INTO wishlist_items (customer_id, product_id, created_at) VALUES (?, ?, ?)",
            (session["customer_id"], product_id, db.now()),
        )
        conn.commit()
        return jsonify({"success": True, "message": "Added to wishlist!"})
    except Exception as exc:
        conn.close()
        current_app.logger.warning("Wishlist insert failed: %s", exc)
        return jsonify({"error": "Could not add to wishlist. Please try again."}), 500
    finally:
        conn.close()




@storefront_bp.route("/api/wishlist/remove", methods=["POST"])
@customer_login_required
def api_wishlist_remove():
    check_csrf_api()
    data = request.get_json(force=True, silent=True) or {}
    product_id = data.get("product_id")
    if not product_id:
        return jsonify({"error": "Missing product."}), 400
    conn = db.get_db()
    conn.execute(
        "DELETE FROM wishlist_items WHERE customer_id = ? AND product_id = ?",
        (session["customer_id"], product_id),
    )
    conn.commit()
    conn.close()
    return jsonify({"success": True})




@storefront_bp.route("/api/wishlist/list")
@customer_login_required
def api_wishlist_list():
    conn = db.get_db()
    rows = conn.execute(
        """SELECT w.product_id, w.created_at, p.name, p.slug, p.price, p.compare_price
           FROM wishlist_items w JOIN products p ON p.id = w.product_id
           WHERE w.customer_id = ? AND p.active = 1
           ORDER BY w.created_at DESC""",
        (session["customer_id"],),
    ).fetchall()
    conn.close()
    catalog = get_catalog()
    results = []
    for r in rows:
        results.append({
            "product_id": r["product_id"],
            "name": r["name"],
            "slug": r["slug"],
            "price": r["price"],
            "compare_price": r["compare_price"],
            "image": catalog["product_images"].get(r["product_id"]),
        })
    return jsonify({"items": results})




@storefront_bp.route("/api/product/<int:product_id>/view", methods=["POST"])
def api_product_view(product_id):
    """Record a product view after the page is already visible.

    This keeps the page render path fast and pushes the database write into a
    lightweight follow-up request instead of blocking the main HTML response.
    Rate-limited per IP: one view per product per 60s window.
    """
    if rate_limited(f"product-view-{product_id}", max_attempts=1, window_seconds=60):
        return ("", 204)
    conn = None
    try:
        conn = db.get_db()
        conn.execute("UPDATE products SET views = COALESCE(views, 0) + 1 WHERE id = ?", (product_id,))
        conn.commit()
    except Exception as exc:
        current_app.logger.warning("Product view tracking failed for %s: %s", product_id, exc)
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
    return ("", 204)



@storefront_bp.route("/product/<slug>")
def product_detail(slug):
    settings = get_settings()
    catalog = get_catalog()

    product = catalog["products_by_slug"].get(slug)
    if not product:
        abort(404)

    conn = db.get_db()
    detail = conn.execute(
        """SELECT id, name, slug, category, price, compare_price,
                  short_description, description, quantity,
                  delivery_mode, active, created_at, views, ribbon, position
           FROM products WHERE slug = ? AND active = 1 LIMIT 1""",
        (slug,),
    ).fetchone()
    if not detail:
        conn.close()
        abort(404)

    images = conn.execute(
        "SELECT filename FROM product_images WHERE product_id = ? ORDER BY position ASC",
        (detail["id"],),
    ).fetchall()

    related = []
    if detail["category"]:
        related = [
            p for p in catalog["products"]
            if p["category"] == detail["category"] and p["id"] != detail["id"]
        ][:4]
    if len(related) < 4:
        related_ids = {r["id"] for r in related}
        related += [
            p for p in catalog["products"]
            if p["id"] != detail["id"] and p["id"] not in related_ids
        ][:4 - len(related)]

    related_images = {r["id"]: catalog["product_images"].get(r["id"]) for r in related}
    try:
        personalized = get_personalized_recommendations(session.get("customer_id"), limit=8)
        recommendations = personalized if personalized else get_recommendations(detail["id"], 8)
    except Exception:
        recommendations = []

    delivery_speed = None
    if detail["delivery_mode"] == "manual":
        delivery_speed = _delivery_speed_stat(conn, detail["id"])

    conn.close()

    _track_product_view(detail["id"])
    try:
        record_event("product_view", product_id=detail["id"], customer_id=session.get("customer_id"), session_id=session.get("cart_id", ""), request=request)
    except Exception:
        current_app.logger.debug("Product analytics unavailable", exc_info=True)

    return render_template(
        "product.html", settings=settings, product=detail,
        images=[i["filename"] for i in images],
        razorpay_key=config.RAZORPAY_KEY_ID,
        related=related, related_images=related_images,
        sold_counts=catalog.get("sold_counts", {}),
        delivery_speed=delivery_speed,
        recommendations=recommendations,
    )




@storefront_bp.route("/api/product/<int:product_id>/stock-request", methods=["POST"])
def api_stock_request(product_id):
    """Customer requests notification when a sold-out product is back in stock."""
    if rate_limited("stock-request", max_attempts=5, window_seconds=300):
        return jsonify({"error": "Too many attempts. Please wait a few minutes."}), 429
    data = request.get_json(force=True, silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    name = (data.get("name") or "").strip()
    phone = (data.get("phone") or "").strip()

    if not email or "@" not in email:
        return jsonify({"error": "Please enter a valid email address."}), 400

    conn = db.get_db()
    product = conn.execute("SELECT id, quantity, name FROM products WHERE id = ? AND active = 1", (product_id,)).fetchone()
    if not product:
        conn.close()
        return jsonify({"error": "Product not found."}), 404
    if product["quantity"] is None or product["quantity"] > 0:
        conn.close()
        return jsonify({"error": "This product is in stock!"}), 400

    # Check for duplicate request from this email
    existing = conn.execute(
        "SELECT id FROM stock_requests WHERE product_id = ? AND customer_email = ? AND notified = 0",
        (product_id, email),
    ).fetchone()
    if existing:
        conn.close()
        return jsonify({"error": "You're already on the waitlist for this product!"}), 409

    conn.execute(
        "INSERT INTO stock_requests (product_id, customer_name, customer_email, customer_phone, created_at) VALUES (?, ?, ?, ?, ?)",
        (product_id, name, email, phone, db.now()),
    )
    conn.commit()
    conn.close()
    return jsonify({"success": True, "message": f"We'll email {email} when '{product['name']}' is back in stock!"})




@storefront_bp.route("/api/recent-purchases/<int:product_id>")
def api_recent_purchases(product_id):
    """Return recent confirmed/delivered order customer names for social proof."""
    conn = None
    try:
        conn = db.get_db()
        rows = conn.execute(
            """SELECT customer_name FROM orders
               WHERE product_id = ? AND status IN ('confirmed','delivered')
                 AND paid_at IS NOT NULL AND customer_name != ''
               ORDER BY paid_at DESC LIMIT 30""",
            (product_id,),
        ).fetchall()
        names = [r["customer_name"] for r in rows]
        return jsonify(names)
    except Exception:
        return jsonify([])
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass




@storefront_bp.route("/api/products/recent")
def api_products_recent():
    """Return recently viewed product data (max 10)."""
    viewed_ids = session.get("recently_viewed", [])
    if not viewed_ids:
        return jsonify([])
    ids = []
    for vid in viewed_ids:
        try:
            ids.append(int(vid))
        except (ValueError, TypeError):
            continue
    if not ids:
        return jsonify([])
    placeholders = ",".join("?" for _ in ids)
    conn = db.get_db()
    rows = conn.execute(
        f"""SELECT id, name, slug, price, compare_price, quantity
            FROM products WHERE id IN ({placeholders}) AND active = 1""",
        tuple(ids),
    ).fetchall()
    product_map = {r["id"]: r for r in rows}
    image_map = get_primary_image_map(conn, ids)
    conn.close()
    catalog = get_catalog()
    cat_images = catalog.get("product_images", {})
    results = []
    for vid in ids:
        p = product_map.get(vid)
        if not p:
            continue
        raw_image = image_map.get(vid) or cat_images.get(vid)
        results.append({
            "id": p["id"],
            "name": p["name"],
            "slug": p["slug"],
            "price": p["price"],
            "compare_price": p["compare_price"],
            "image": _get_webp_path(raw_image) if raw_image else None,
        })
    return jsonify(results)




@storefront_bp.route("/api/create-order", methods=["POST"])
def api_create_order():
    check_csrf_api()
    if not checkout_enabled():
        return jsonify({"error": "Checkout is temporarily disabled."}), 503
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
    if product["quantity"] is not None and product["quantity"] <= 0:
        conn.close()
        return jsonify({"error": f"Sorry, \"{product['name']}\" is sold out."}), 410

    testing_mode = is_testing_checkout()
    if not testing_mode and not rzp.is_configured():
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

        # Check usage limit against actual completed uses
        actual_uses = conn.execute(
            "SELECT COUNT(*) AS cnt FROM coupon_usage WHERE coupon_id = ?", (coupon["id"],)
        ).fetchone()["cnt"]
        if coupon["usage_limit"] is not None and actual_uses >= coupon["usage_limit"]:
            conn.close()
            return jsonify({"error": "That coupon has already been fully redeemed."}), 400

        # Per-customer reuse check
        if coupon["max_per_customer"]:
            used = conn.execute(
                "SELECT COUNT(*) AS cnt FROM coupon_usage WHERE coupon_id = ? AND customer_email = ?",
                (coupon["id"], email),
            ).fetchone()
            if used and used["cnt"] >= coupon["max_per_customer"]:
                conn.close()
                return jsonify({"error": "You've already used this coupon."}), 400

        margin_result = coupon_discount_with_margin(
            price=int(product["price"] or 0), cost_price=int(product["cost_price"] or 0),
            discount_type=coupon["discount_type"], discount_value=int(coupon["discount_value"] or 0),
            min_margin_percent=int(product["min_margin_percent"] or 15),
        )
        requested_discount = int(round(product["price"] * coupon["discount_value"] / 100)) if coupon["discount_type"] == "percent" else int(coupon["discount_value"] or 0)
        if int(product["cost_price"] or 0) > 0 and margin_result["discount"] < requested_discount:
            conn.close()
            return jsonify({"error": "That coupon exceeds the product's protected minimum margin."}), 400
        discount_amount = margin_result["discount"]
        final_amount = margin_result["final_price"]
        applied_code = coupon["code"]

    order_ref = db.new_order_ref()
    payment_mode = "test" if testing_mode else "gateway"
    # Reserve stock before payment is finalized. The reservation is keyed by
    # the immutable order reference so payment retries can safely commit it
    # exactly once.
    rzp_order = None
    if testing_mode:
        razorpay_order_id = None
    else:
        try:
            rzp_order = rzp.create_order(final_amount, receipt=order_ref)
        except Exception:
            conn.close()
            return jsonify({"error": "Could not start payment. Please try again."}), 502
        razorpay_order_id = rzp_order["id"]

    if not reserve_stock_batch(conn, [(product["id"], 1)], order_ref):
        conn.close()
        return jsonify({"error": f"Sorry, {product['name']!r} is no longer available in that quantity."}), 409

    cur = conn.execute(
        """INSERT INTO orders
           (order_ref, product_id, product_name, customer_name, customer_email,
            customer_phone, amount, coupon_code, discount_amount, razorpay_order_id,
            status, created_at, customer_id, payment_mode, paid_at, inventory_reservation_id)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (order_ref, product["id"], product["name"], name, email, phone,
         final_amount, applied_code, discount_amount, razorpay_order_id,
         'created', db.now(), session.get("customer_id"), payment_mode, None, order_ref),
    )
    # Keep the single-product path aligned with cart orders.  Payment
    # confirmation and downstream order/invoice code operate on order_items.
    conn.execute(
        """INSERT INTO order_items
           (order_id, product_id, product_name, unit_price, quantity, line_total)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (cur.lastrowid, product["id"], product["name"], product["price"], 1, product["price"]),
    )
    order = conn.execute("SELECT * FROM orders WHERE order_ref = ?", (order_ref,)).fetchone()
    order_items = conn.execute(
        "SELECT * FROM order_items WHERE order_id = ?", (order["id"],)
    ).fetchall()
    # Track abandoned cart contact info
    if "session_key" in session:
        track_cart_contact(session["session_key"], name, email, phone)
    conn.commit()

    if testing_mode:
        confirm_order_payment_durable(conn, order=order, order_items=order_items, payment_mode="test", confirm_callable=_confirm_order_payment)
        return jsonify({
            "test_mode": True,
            "payment_mode": "test",
            "order_ref": order_ref,
            "product_name": product["name"],
            "customer_name": name,
            "customer_email": email,
            "customer_phone": phone,
            "redirect_url": url_for("storefront.track_order", order_ref=order_ref, email=email),
        })

    conn.close()

    return jsonify({
        "test_mode": False,
        "payment_mode": "gateway",
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




@storefront_bp.route("/api/order/<order_id>/cancel-unpaid", methods=["POST"])
def api_cancel_unpaid_order(order_id):
    """Let a customer cancel their own unpaid order before payment."""
    check_csrf_api()
    data = request.get_json(force=True, silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    conn = db.get_db()
    order = conn.execute(
        "SELECT * FROM orders WHERE order_ref = ? AND lower(customer_email) = ? AND status = 'created'",
        (order_id.upper(), email),
    ).fetchone()
    if not order:
        conn.close()
        return jsonify({"error": "Unpaid order not found."}), 404
    reservation_id = order["inventory_reservation_id"] or order["order_ref"] if "inventory_reservation_id" in order.keys() else order["order_ref"]
    release_stock(conn, reservation_id)
    conn.execute("UPDATE orders SET status = 'cancelled', order_state = 'cancelled' WHERE id = ?", (order["id"],))
    conn.commit()
    conn.close()
    return jsonify({"success": True, "message": "Your unpaid order has been cancelled."})




@storefront_bp.route("/api/order/<order_id>/notes")
@login_required
def api_order_notes(order_id):
    """Return internal notes for an order (used by admin)."""
    conn = db.get_db()
    order = conn.execute("SELECT id FROM orders WHERE order_ref = ?", (order_id.upper(),)).fetchone()
    if not order:
        conn.close()
        return jsonify([])
    notes = conn.execute(
        "SELECT id, note, created_at FROM order_notes WHERE order_id = ? ORDER BY created_at DESC",
        (order["id"],),
    ).fetchall()
    conn.close()
    return jsonify([dict(n) for n in notes])




@storefront_bp.route("/api/order/<order_id>/notes/add", methods=["POST"])
@login_required
def api_order_notes_add(order_id):
    check_csrf_api()
    data = request.get_json(force=True, silent=True) or {}
    note = (data.get("note") or "").strip()
    if not note:
        return jsonify({"error": "Note cannot be empty."}), 400
    conn = db.get_db()
    order = conn.execute("SELECT id FROM orders WHERE order_ref = ?", (order_id.upper(),)).fetchone()
    if not order:
        conn.close()
        return jsonify({"error": "Order not found."}), 404
    conn.execute(
        "INSERT INTO order_notes (order_id, admin_id, note, created_at) VALUES (?, ?, ?, ?)",
        (order["id"], session.get("admin_id"), note, db.now()),
    )
    conn.commit()
    conn.close()
    return jsonify({"success": True})




@storefront_bp.route("/api/cart/preview")
def api_cart_preview():
    """Return cart items for the nav hover preview dropdown."""
    conn = db.get_db()
    items, subtotal = get_cart_items(conn)
    conn.close()
    catalog = get_catalog()
    image_map = catalog["product_images"]
    result = []
    for it in items:
        raw_image = image_map.get(it["product"]["id"])
        result.append({
            "name": it["product"]["name"],
            "slug": it["product"]["slug"],
            "quantity": it["quantity"],
            "price": it["product"]["price"],
            "line_total": it["line_total"],
            "image": _get_webp_path(raw_image) if raw_image else None,
        })
    return jsonify({"items": result, "subtotal": subtotal, "count": sum(it["quantity"] for it in items)})


def _merge_guest_cart(conn, customer_id):
    """Merge the guest session cart into the user's DB-backed cart on login.
    Guest items that don't exist in the DB cart are inserted; guest items that
    already exist have their quantities added together."""
    cart = session.pop("cart", None)
    if not cart:
        return
    now_ts = db.now()
    for pid_str, qty in list(cart.items()):
        try:
            pid = int(pid_str)
            qty = max(1, int(qty))
        except (TypeError, ValueError):
            continue
        existing = conn.execute(
            "SELECT quantity FROM cart_items WHERE customer_id = ? AND product_id = ?",
            (customer_id, pid),
        ).fetchone()
        if existing:
            conn.execute(
                "UPDATE cart_items SET quantity = MIN(quantity + ?, 99), updated_at = ? WHERE customer_id = ? AND product_id = ?",
                (qty, now_ts, customer_id, pid),
            )
        else:
            conn.execute(
                "INSERT INTO cart_items (customer_id, product_id, quantity, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                (customer_id, pid, qty, now_ts, now_ts),
            )
    session.modified = True




@storefront_bp.route("/api/auto-coupons")
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


# _coupon_description is imported from helpers




@storefront_bp.route("/cart")
def view_cart():
    conn = None
    try:
        conn = db.get_db()
        settings = get_settings()
        items, subtotal = get_cart_items(conn)
        product_images = get_primary_image_map(conn, [it["product"]["id"] for it in items])
        return render_template(
            "cart.html", settings=settings, items=items, subtotal=subtotal,
            product_images=product_images, razorpay_key=config.RAZORPAY_KEY_ID,
        )
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass




@storefront_bp.route("/cart/add", methods=["POST"])
def cart_add():
    check_csrf_api()
    if rate_limited("cart-add", max_attempts=40, window_seconds=60):
        return jsonify({"error": "Too many requests — please slow down."}), 429
    product_id = request.form.get("product_id") or (request.get_json(silent=True) or {}).get("product_id")
    try:
        qty = max(1, min(int(request.form.get("quantity", 1)), 99))
    except (TypeError, ValueError):
        qty = 1
    if not product_id:
        return jsonify({"error": "Missing product."}), 400

    conn = db.get_db()
    product = conn.execute("SELECT * FROM products WHERE id = ? AND active = 1", (product_id,)).fetchone()
    if not product:
        conn.close()
        return jsonify({"error": "This product is not available."}), 404
    if product["quantity"] is not None and product["quantity"] <= 0:
        conn.close()
        return jsonify({"error": f"Sorry, \"{product['name']}\" is sold out."}), 410

    customer_id = session.get("customer_id")
    if customer_id:
        # DB-backed cart for logged-in users
        existing = conn.execute(
            "SELECT quantity FROM cart_items WHERE customer_id = ? AND product_id = ?",
            (customer_id, product["id"]),
        ).fetchone()
        current_qty = existing["quantity"] if existing else 0

        if product["quantity"] is not None and current_qty + qty > product["quantity"]:
            qty = max(1, product["quantity"] - current_qty)
            if qty <= 0:
                conn.close()
                return jsonify({"error": f"Sorry, only {product['quantity']} of \"{product['name']}\" available."}), 410

        now_ts = db.now()
        if existing:
            conn.execute(
                "UPDATE cart_items SET quantity = quantity + ?, updated_at = ? WHERE customer_id = ? AND product_id = ?",
                (qty, now_ts, customer_id, product["id"]),
            )
        else:
            conn.execute(
                "INSERT INTO cart_items (customer_id, product_id, quantity, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                (customer_id, product["id"], qty, now_ts, now_ts),
            )
        conn.commit()
        cart_count = conn.execute(
            "SELECT COALESCE(SUM(quantity), 0) AS cnt FROM cart_items WHERE customer_id = ?",
            (customer_id,),
        ).fetchone()["cnt"]
        conn.close()
    else:
        # Session cart for guests
        conn.close()
        cart = session.get("cart", {})
        if product["quantity"] is not None:
            in_cart = cart.get(str(product["id"]), 0)
            if qty > product["quantity"]:
                qty = product["quantity"]
            if in_cart >= product["quantity"]:
                return jsonify({"error": f"Sorry, only {product['quantity']} of \"{product['name']}\" available."}), 410
        if len(cart) >= 50 and str(product["id"]) not in cart:
            return jsonify({"error": "Cart is full — please checkout or clear items before adding more."}), 400
        total_qty = sum(cart.values())
        if total_qty + qty > 500:
            return jsonify({"error": "Cart limit reached — please checkout before adding more."}), 400
        key = str(product["id"])
        cart[key] = cart.get(key, 0) + qty
        session["cart"] = cart
        session.modified = True
        cart_count = sum(cart.values())

    # Track abandoned cart
    if "session_key" not in session:
        session["session_key"] = os.urandom(16).hex()
    track_cart_add(session["session_key"], product["id"], product["name"], product["price"], qty)

    if request.headers.get("X-Requested-With") == "fetch":
        return jsonify({"success": True, "cart_count": cart_count, "product_name": product["name"]})
    flash(f'Added "{product["name"]}" to your cart.', "success")
    return redirect(request.referrer or url_for("storefront.home"))




@storefront_bp.route("/cart/update", methods=["POST"])
def cart_update():
    check_csrf()
    product_id = request.form.get("product_id", "")
    try:
        qty = int(request.form.get("quantity", 1))
    except (TypeError, ValueError):
        qty = 1
    customer_id = session.get("customer_id")
    if customer_id:
        # DB-backed cart for logged-in users
        if qty <= 0:
            conn = db.get_db()
            conn.execute("DELETE FROM cart_items WHERE customer_id = ? AND product_id = ?",
                         (customer_id, int(product_id)))
            conn.commit()
            conn.close()
        else:
            conn = db.get_db()
            p = conn.execute("SELECT quantity FROM products WHERE id = ? AND active = 1", (int(product_id),)).fetchone()
            if p and p["quantity"] is not None:
                qty = min(qty, p["quantity"])
            qty = min(qty, 99)
            now_ts = db.now()
            existing = conn.execute(
                "SELECT quantity FROM cart_items WHERE customer_id = ? AND product_id = ?",
                (customer_id, int(product_id)),
            ).fetchone()
            if existing:
                if qty <= 0:
                    conn.execute("DELETE FROM cart_items WHERE customer_id = ? AND product_id = ?",
                                 (customer_id, int(product_id)))
                else:
                    conn.execute("UPDATE cart_items SET quantity = ?, updated_at = ? WHERE customer_id = ? AND product_id = ?",
                                 (qty, now_ts, customer_id, int(product_id)))
            conn.commit()
            conn.close()
    else:
        # Session cart for guests
        product_id = str(product_id)
        cart = session.get("cart", {})
        if product_id in cart:
            if qty <= 0:
                del cart[product_id]
            else:
                # Cap at available stock
                conn = db.get_db()
                p = conn.execute("SELECT quantity FROM products WHERE id = ? AND active = 1", (int(product_id),)).fetchone()
                conn.close()
                if p and p["quantity"] is not None:
                    qty = min(qty, p["quantity"])
                cart[product_id] = min(qty, 99)
            session["cart"] = cart
            session.modified = True
    return redirect(url_for("storefront.view_cart"))




@storefront_bp.route("/cart/remove/<int:product_id>", methods=["POST"])
def cart_remove(product_id):
    check_csrf()
    customer_id = session.get("customer_id")
    if customer_id:
        conn = db.get_db()
        conn.execute("DELETE FROM cart_items WHERE customer_id = ? AND product_id = ?",
                     (customer_id, product_id))
        conn.commit()
        conn.close()
    else:
        cart = session.get("cart", {})
        cart.pop(str(product_id), None)
        session["cart"] = cart
        session.modified = True
    flash("Item removed from cart.", "success")
    return redirect(url_for("storefront.view_cart"))




@storefront_bp.route("/cart/clear", methods=["POST"])
def cart_clear():
    check_csrf()
    session["cart"] = {}
    session.modified = True
    return redirect(url_for("storefront.view_cart"))




@storefront_bp.route("/api/cart/apply-coupon", methods=["POST"])
@limiter.limit("30 per minute")
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

    margin = cart_coupon_margin(items=items, subtotal=subtotal, discount_type=coupon["discount_type"], discount_value=int(coupon["discount_value"] or 0))
    requested = margin["requested_discount"]
    if requested > margin["safe_discount"] and any(int((it["product"].get("cost_price") or 0)) > 0 for it in items):
        return jsonify({"error": "That coupon exceeds the cart's protected minimum margin."}), 400
    discount = margin["safe_discount"]
    final_total = margin["final_price"]
    return jsonify({"success": True, "discount_amount": discount, "final_price": final_total, "code": coupon["code"]})




@storefront_bp.route("/api/cart/create-order", methods=["POST"])
def api_cart_create_order():
    check_csrf_api()
    if not checkout_enabled():
        return jsonify({"error": "Checkout is temporarily disabled."}), 503
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

    # Verify stock for all cart items before creating order
    for it in items:
        p = it["product"]
        qty_needed = it["quantity"]
        if p["quantity"] is not None and p["quantity"] < qty_needed:
            conn.close()
            return jsonify({"error": f"Sorry, \"{p['name']}\" has only {p['quantity']} left in stock."}), 410

    testing_mode = is_testing_checkout()
    if not testing_mode and not rzp.is_configured():
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

        # Check usage limit against actual completed uses
        actual_uses = conn.execute(
            "SELECT COUNT(*) AS cnt FROM coupon_usage WHERE coupon_id = ?", (coupon["id"],)
        ).fetchone()["cnt"]
        if coupon["usage_limit"] is not None and actual_uses >= coupon["usage_limit"]:
            conn.close()
            return jsonify({"error": "That coupon has already been fully redeemed."}), 400

        # Per-customer reuse check
        if coupon["max_per_customer"]:
            used = conn.execute(
                "SELECT COUNT(*) AS cnt FROM coupon_usage WHERE coupon_id = ? AND customer_email = ?",
                (coupon["id"], email),
            ).fetchone()
            if used and used["cnt"] >= coupon["max_per_customer"]:
                conn.close()
                return jsonify({"error": "You've already used this coupon."}), 400

        margin = cart_coupon_margin(items=items, subtotal=subtotal, discount_type=coupon["discount_type"], discount_value=int(coupon["discount_value"] or 0))
        requested_discount = margin["requested_discount"]
        if requested_discount > margin["safe_discount"] and any(int((it["product"].get("cost_price") or 0)) > 0 for it in items):
            conn.close()
            return jsonify({"error": "That coupon exceeds the cart's protected minimum margin."}), 400
        discount_amount = margin["safe_discount"]
        final_amount = margin["final_price"]
        applied_code = coupon["code"]

    order_ref = db.new_order_ref()
    rzp_order = None
    payment_mode = "test" if testing_mode else "gateway"
    if not testing_mode:
        try:
            rzp_order = rzp.create_order(final_amount, receipt=order_ref)
        except Exception:
            conn.close()
            return jsonify({"error": "Could not start payment. Please try again."}), 502

    item_count = sum(it["quantity"] for it in items)
    summary_name = items[0]["product"]["name"] if len(items) == 1 else f"{item_count} items ({len(items)} products)"

    if not reserve_stock_batch(conn, [(it["product"]["id"], it["quantity"]) for it in items], order_ref):
        conn.close()
        return jsonify({"error": "One or more items became unavailable while you were checking out. Please review your cart and try again."}), 409

    cur = conn.execute(
        """INSERT INTO orders
           (order_ref, product_id, product_name, customer_name, customer_email,
            customer_phone, amount, coupon_code, discount_amount, razorpay_order_id,
            status, created_at, customer_id, payment_mode, paid_at, inventory_reservation_id)
           VALUES (?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (order_ref, summary_name, name, email, phone,
         final_amount, applied_code, discount_amount, (rzp_order["id"] if rzp_order else None),
         'created', db.now(), session.get("customer_id"), payment_mode, None, order_ref),
    )
    order_id = cur.lastrowid
    for it in items:
        conn.execute(
            """INSERT INTO order_items (order_id, product_id, product_name, unit_price, quantity, line_total)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (order_id, it["product"]["id"], it["product"]["name"], it["product"]["price"],
             it["quantity"], it["line_total"]),
        )
    order = conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
    order_items = conn.execute("SELECT * FROM order_items WHERE order_id = ?", (order_id,)).fetchall()
    # Track abandoned cart contact info
    if "session_key" in session:
        track_cart_contact(session["session_key"], name, email, phone)
    conn.commit()

    if testing_mode:
        confirm_order_payment_durable(conn, order=order, order_items=order_items, payment_mode="test", confirm_callable=_confirm_order_payment)
        return jsonify({
            "test_mode": True,
            "payment_mode": "test",
            "order_ref": order_ref,
            "product_name": summary_name,
            "customer_name": name,
            "customer_email": email,
            "customer_phone": phone,
            "redirect_url": url_for("storefront.track_order", order_ref=order_ref, email=email),
        })

    conn.close()

    return jsonify({
        "test_mode": False,
        "payment_mode": "gateway",
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




@storefront_bp.route("/api/apply-coupon", methods=["POST"])
@limiter.limit("30 per minute")
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

    margin_result = coupon_discount_with_margin(
        price=int(product["price"] or 0), cost_price=int(product["cost_price"] or 0),
        discount_type=coupon["discount_type"], discount_value=int(coupon["discount_value"] or 0),
        min_margin_percent=int(product["min_margin_percent"] or 15),
    )
    requested_discount = int(round(product["price"] * coupon["discount_value"] / 100)) if coupon["discount_type"] == "percent" else int(coupon["discount_value"] or 0)
    if int(product["cost_price"] or 0) > 0 and margin_result["discount"] < requested_discount:
        return jsonify({"error": "That coupon exceeds the product's protected minimum margin."}), 400
    discount = margin_result["discount"]
    final_price = margin_result["final_price"]
    return jsonify({"success": True, "discount_amount": discount, "final_price": final_price, "code": coupon["code"]})




@storefront_bp.route("/api/verify-payment", methods=["POST"])
@limiter.limit("12 per minute")
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
        reservation_id = order["inventory_reservation_id"] or order["order_ref"] if "inventory_reservation_id" in order.keys() else order["order_ref"]
        release_stock(conn, reservation_id)
        conn.execute(
            "UPDATE orders SET status = 'failed', payment_state = 'failed' WHERE id = ? AND payment_state = 'pending'",
            (order["id"],),
        )
        conn.commit()
        conn.close()
        return jsonify({"error": "Payment verification failed."}), 400

    # Signature proves authenticity of the checkout response, but the provider
    # state must also prove that the payment was actually captured for the exact
    # amount/currency expected by the order. Never trust browser-side totals.
    gateway = get_payment_gateway("test" if config.ALLOW_TEST_GATEWAY else "razorpay", {
        "RAZORPAY_KEY_ID": config.RAZORPAY_KEY_ID,
        "RAZORPAY_KEY_SECRET": config.RAZORPAY_KEY_SECRET,
        "RAZORPAY_WEBHOOK_SECRET": config.RAZORPAY_WEBHOOK_SECRET,
    })
    provider_payment = gateway.capture_payment(rzp_payment_id, amount=int(order["amount"]) * 100)
    if not provider_payment.success:
        reservation_id = order["inventory_reservation_id"] or order["order_ref"] if "inventory_reservation_id" in order.keys() else order["order_ref"]
        release_stock(conn, reservation_id)
        conn.execute(
            "UPDATE orders SET status = 'failed', payment_state = 'failed' WHERE id = ? AND payment_state = 'pending'",
            (order["id"],),
        )
        conn.commit()
        conn.close()
        return jsonify({"error": "Payment could not be confirmed with the payment provider."}), 400
    if int(provider_payment.amount or 0) != int(order["amount"]) * 100 or str(provider_payment.currency or "").upper() != "INR":
        conn.rollback()
        conn.close()
        current_app.logger.error("Payment amount/currency mismatch for order %s", order["order_ref"])
        return jsonify({"error": "Payment amount does not match the order."}), 400

    order_items = conn.execute(
        "SELECT * FROM order_items WHERE order_id = ?", (order["id"],)
    ).fetchall()
    try:
        auto_message = _confirm_order_payment(
            conn,
            order,
            order_items,
            payment_mode="gateway",
            razorpay_payment_id=rzp_payment_id,
            razorpay_signature=rzp_signature,
        )
    except Exception:
        current_app.logger.exception("Payment confirmed by provider but local order finalization failed: %s", order["order_ref"])
        conn.rollback()
        refund = gateway.refund_payment(
            rzp_payment_id,
            amount=int(order["amount"]) * 100,
            idempotency_key=f"inventory-failure:{order["order_ref"]}",
        )
        if refund.success:
            conn.execute(
                "UPDATE orders SET status='refunded', payment_state='refunded', order_state='refunded', refunded_amount=? WHERE id=?",
                (order["amount"], order["id"]),
            )
            conn.commit()
            return jsonify({"error": "Payment was reversed because the order could not be finalized safely."}), 409
        conn.execute(
            "UPDATE orders SET status='payment_issue', payment_state='captured', order_state='processing' WHERE id=?",
            (order["id"],),
        )
        conn.commit()
        return jsonify({"error": "Your payment was received, but we could not finish the order automatically. Support has been alerted."}), 503

    return jsonify({"success": True, "order_ref": order["order_ref"], "auto_delivered": auto_message is not None})



def _confirm_order_payment(conn, order, order_items, *, payment_mode="gateway", razorpay_payment_id=None, razorpay_signature=None):
    """Mark an order as paid, issue file tokens, optionally auto-deliver, and
    trigger notifications. Returns the auto-delivery message when one is
    generated, otherwise None."""
    if not order:
        return None

    current_status = (order["status"] or "").lower()
    if current_status in {"paid", "delivered"}:
        return order["delivery_message"] if order["delivery_message"] else None

    # Commit the inventory reservation before recording the payment in local
    # state. If inventory has somehow disappeared after checkout, the caller
    # can treat this as a fulfilment exception instead of pretending the order
    # is successfully fulfilled.
    reservation_id = order["inventory_reservation_id"] if "inventory_reservation_id" in order.keys() else None
    if reservation_id:
        commit_stock(conn, reservation_id)
    else:
        for item in (order_items or []):
            if item["product_id"]:
                updated = conn.execute(
                    "UPDATE products SET quantity = quantity - ? WHERE id = ? AND quantity >= ?",
                    (item["quantity"], item["product_id"], item["quantity"]),
                )
                if updated.rowcount != 1:
                    raise RuntimeError(f"Legacy inventory commit failed for order {order['order_ref']}")

    # Now capture the payment and move the order state exactly once before any
    # coupon, entitlement, token, or notification side effects.
    if not mark_payment_captured(conn, order["id"]):
        return None

    coupon_code = (order["coupon_code"] or "").strip().upper()
    if coupon_code:
        try:
            # Atomically increment used_count only on confirmed payment
            conn.execute(
                "UPDATE coupons SET used_count = used_count + 1 WHERE code = ? AND (usage_limit IS NULL OR used_count < usage_limit)",
                (coupon_code,),
            )
            coupon = conn.execute("SELECT id FROM coupons WHERE code = ?", (coupon_code,)).fetchone()
            if coupon:
                conn.execute(
                    "INSERT INTO coupon_usage (coupon_id, order_id, customer_email, discount_amount, used_at) VALUES (?, ?, ?, ?, ?)",
                    (coupon["id"], order["id"], order["customer_email"], order["discount_amount"], db.now()),
                )
        except Exception:
            current_app.logger.exception("Coupon usage recording failed for order %s", order["order_ref"])

    # Issue protected links as part of the confirmed-payment transaction. The
    # helper is idempotent, so webhook/client retries cannot create duplicate
    # tokens. Keep manual-delivery orders paid; admins can still paste these
    # protected links into the delivery message when they deliver the order.
    product_ids = [it["product_id"] for it in (order_items or []) if it["product_id"]]
    if not product_ids and order["product_id"]:
        product_ids = [order["product_id"]]
    for product_id in sorted(set(product_ids)):
        issue_entitlement(
            conn,
            order["id"],
            product_id,
            customer_id=order["customer_id"] if "customer_id" in order.keys() else None,
        )
    file_tokens = generate_download_tokens(conn, order["id"], product_ids)
    token_lines = [
        f"📎 {item['filename']}: {url_for('storefront.download_product', token=item['token'], _external=True)}"
        for item in file_tokens
    ]

    auto_message = None
    auto_deliver_enabled = str(get_settings().get("auto_deliver_enabled", "true")).lower() != "false"
    if auto_deliver_enabled:
        auto_message = _maybe_auto_deliver(conn, order, order_items)
        # Only automatic-delivery products are marked delivered here. Manual
        # orders still receive generated tokens, exposed to the admin detail
        # page for later inclusion when the admin completes delivery.
        if auto_message is not None and token_lines:
            auto_message = (auto_message + "\n\n" if auto_message else "") + "\n".join(token_lines)
            conn.execute(
                "UPDATE orders SET delivery_message = ? WHERE id = ?",
                (auto_message, order["id"]),
            )

    paid_at = db.now()
    if auto_message is None:
        conn.execute(
            "UPDATE orders SET status = 'paid', paid_at = ?, payment_mode = ?, razorpay_payment_id = COALESCE(?, razorpay_payment_id), razorpay_signature = COALESCE(?, razorpay_signature) WHERE id = ?",
            (paid_at, payment_mode, razorpay_payment_id, razorpay_signature, order["id"]),
        )
    else:
        conn.execute(
            "UPDATE orders SET status = 'delivered', paid_at = COALESCE(paid_at, ?), payment_mode = ?, razorpay_payment_id = COALESCE(?, razorpay_payment_id), razorpay_signature = COALESCE(?, razorpay_signature) WHERE id = ?",
            (paid_at, payment_mode, razorpay_payment_id, razorpay_signature, order["id"]),
        )
        transition_order_state(conn, order["id"], "delivered", expected_states={"paid"})

    conn.commit()

    try:
        notify_admins_new_order(order["id"])
        webpush_notify_admins_new_order(order["id"])
    except Exception:
        pass

    if customer_email_notifications_enabled():
        try:
            item_line = order["product_name"] if not order_items else ", ".join(
                f"{it['product_name']} x{it['quantity']}" for it in order_items
            )
            subject = f"Your order {order['order_ref']} is confirmed"
            body = (
                f"Hi {order['customer_name']},\n\n"
                f"We have received your order for \"{item_line}\".\n\n"
            )
            if auto_message:
                subject = f"Your order {order['order_ref']} has been delivered"
                body += f"Your download/details are ready below:\n\n{auto_message}\n\n"
            body += f"Order reference: {order['order_ref']}\n\nThank you for shopping with us."
            enqueue_email_or_send(
                conn,
                to=order["customer_email"],
                subject=subject,
                body=body,
                idempotency_key=f"order-email:{order["id"]}:{"delivered" if auto_message else "confirmed"}",
                logger=current_app.logger if "current_app" in globals() else None,
            )
        except Exception:
            pass

    return auto_message


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




@storefront_bp.route("/track", methods=["GET", "POST"])
def track_order():
    order = None
    searched = False
    prefill_ref = request.args.get("order_ref", "")
    prefill_email = request.args.get("email", "")

    if request.method == "POST":
        check_csrf()
        if rate_limited("track-order", max_attempts=10, window_seconds=60):
            settings = get_settings()
            return render_template(
                "track_order.html", settings=settings, order=None, searched=False,
                prefill_ref="", prefill_email="", order_items=[],
                product_map={}, delivery_content_type="instructions",
                error="Too many attempts — please wait a minute and try again.",
            )
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
        product_map = {}
        if order:
            order_items = conn.execute(
                "SELECT * FROM order_items WHERE order_id = ?", (order["id"],)
            ).fetchall()
            # Load delivery_content_type for each product in the order
            product_ids = list(set(it["product_id"] for it in order_items))
            if product_ids:
                placeholders = ",".join("?" for _ in product_ids)
                products_data = conn.execute(
                    f"SELECT id, name, delivery_content_type FROM products WHERE id IN ({placeholders})",
                    product_ids,
                ).fetchall()
                for p in products_data:
                    product_map[p["id"]] = {
                        "name": p["name"],
                        "delivery_content_type": p["delivery_content_type"],
                    }
        conn.close()
    else:
        order_items = []
        product_map = {}

    # Determine the primary delivery content type for this order
    delivery_content_type = "instructions"
    if order and order_items:
        types = set()
        for it in order_items:
            pinfo = product_map.get(it["product_id"], {})
            t = pinfo.get("delivery_content_type", "")
            if t:
                types.add(t)
        if len(types) == 1:
            delivery_content_type = types.pop()
        elif types:
            delivery_content_type = "|".join(sorted(types))

    settings = get_settings()
    return render_template(
        "track_order.html", settings=settings, order=order, searched=searched,
        prefill_ref=prefill_ref, prefill_email=prefill_email, order_items=order_items,
        product_map=product_map, delivery_content_type=delivery_content_type,
    )




@storefront_bp.route("/track/<order_ref>/resend", methods=["POST"])
def track_order_resend(order_ref):
    """Self-serve route for customers to resend the delivery email."""
    check_csrf()
    if rate_limited("resend-delivery", max_attempts=3, window_seconds=300):
        flash("Too many attempts. Please try again in 5 minutes.", "error")
        return redirect(url_for("storefront.track_order"))

    email = (request.form.get("email") or "").strip().lower()
    if not email:
        flash("Email is required.", "error")
        return redirect(url_for("storefront.track_order"))

    conn = db.get_db()
    order = conn.execute(
        "SELECT * FROM orders WHERE order_ref = ? AND lower(customer_email) = ?",
        (order_ref.strip().upper(), email),
    ).fetchone()
    conn.close()

    if not order:
        flash("Order not found. Check the reference and email.", "error")
        return redirect(url_for("storefront.track_order"))

    if order["status"] != "delivered":
        flash("Delivery email can only be resent for delivered orders.", "error")
        return redirect(url_for("storefront.track_order"))

    if not order["delivery_message"]:
        flash("No delivery content available to resend for this order.", "error")
        return redirect(url_for("storefront.track_order"))

    if not customer_email_notifications_enabled():
        flash("Automatic customer emails are disabled in site settings.", "info")
        return redirect(url_for("storefront.track_order", order_ref=order["order_ref"], email=order["customer_email"]))

    try:
        settings = get_settings()
        site_name = settings.get("site_name", "Virtual Store")
        subject = f"Your order {order['order_ref']} delivery details — {site_name}"
        body = (
            f"Hi {order['customer_name']},\n\n"
            f"Here are the delivery details for your order {order['order_ref']}:\n\n"
            f"{order['delivery_message']}\n\n"
            f"If you have any questions, feel free to contact us.\n\n"
            f"Thank you for shopping with us!"
        )
        send_email(email, subject, body)
        flash("Delivery email has been resent. Check your inbox.", "success")
    except Exception:
        flash("Failed to resend delivery email. Please try again later.", "error")

    return redirect(url_for("storefront.track_order", order_ref=order["order_ref"], email=order["customer_email"]))




@storefront_bp.route("/orders/<order_ref>/invoice")
def public_order_invoice(order_ref):
    """Generate and download a PDF invoice for a paid/delivered order
    — requires order_ref + email match (same model as track_order)."""
    email = (request.args.get("email") or "").strip().lower()
    if not email:
        abort(404)
    conn = db.get_db()
    order = conn.execute(
        "SELECT * FROM orders WHERE order_ref = ? AND lower(customer_email) = ?",
        (order_ref.strip().upper(), email),
    ).fetchone()
    if not order or order["status"] in ("created", "cancelled"):
        conn.close()
        abort(404)
    order_items = conn.execute(
        "SELECT * FROM order_items WHERE order_id = ?", (order["id"],),
    ).fetchall()
    product_ids = {item["product_id"] for item in order_items}
    product_map = {}
    if product_ids:
        rows = conn.execute(
            f"SELECT id, name FROM products WHERE id IN ({','.join('?' for _ in product_ids)})",
            list(product_ids),
        ).fetchall()
        product_map = {r["id"]: dict(r) for r in rows}
    conn.close()
    pdf_bytes, filename = invoicing.generate_and_save_invoice(
        order, order_items, product_map, get_settings(),
    )
    return Response(
        pdf_bytes,
        mimetype="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ============================================================= LEGAL PAGES



@storefront_bp.route("/terms")
def terms_of_service():
    """Terms of Service — rendered from a styled template, not a static file."""
    return render_template("terms_of_service.html", settings=get_settings())




@storefront_bp.route("/privacy")
def privacy_policy():
    """Privacy Policy — rendered from a styled template, not a static file."""
    return render_template("privacy_policy.html", settings=get_settings())




@storefront_bp.route("/refund-policy")
def refund_policy():
    """Refund Policy — rendered directly from the admin-configurable setting."""
    return render_template("refund_policy.html", settings=get_settings())


# ============================================================= CUSTOMER AUTH (self-contained OTP)

# ============================================================= CUSTOMER AUTH (self-contained OTP)



@storefront_bp.route("/auth/send-otp", methods=["POST"])
@limiter.limit("3 per 10 minutes")
def auth_send_otp():
    """Generate a 6-digit OTP, store it in the database with an expiry,
    and send it via Twilio SMS. Falls back to dev mode (code shown in UI)
    if Twilio credentials are not set."""
    check_csrf_api()
    if rate_limited("send-otp", max_attempts=5, window_seconds=60):
        return jsonify({"error": "Too many attempts. Please wait a minute and try again."}), 429

    data = request.get_json(force=True, silent=True) or {}
    phone = (data.get("phone") or "").strip()

    if not phone or not phone.startswith("+"):
        return jsonify({"error": "Please enter your phone number with the country code, e.g. +919876543210."}), 400
    if len(phone) < 8 or len(phone) > 16:
        return jsonify({"error": "That phone number doesn't look right. Please check and try again."}), 400

    twilio_enabled = bool(
        config.TWILIO_ACCOUNT_SID and
        config.TWILIO_AUTH_TOKEN and
        config.TWILIO_FROM_NUMBER
    )
    if not twilio_enabled and not config.OTP_DEV_MODE:
        return jsonify({"error": "Phone sign-in isn't configured on this site yet."}), 503

    code = generate_otp_code()
    conn = db.get_db()
    store_otp(conn, phone, code)
    conn.commit()
    conn.close()

    # --- Try Twilio first, or use explicit local dev mode ---
    if twilio_enabled:
        try:
            from twilio.rest import Client as TwilioClient
            twilio = TwilioClient(config.TWILIO_ACCOUNT_SID, config.TWILIO_AUTH_TOKEN)
            twilio.messages.create(
                body=f"Your verification code is {code}. It expires in {config.OTP_EXPIRY_MINUTES} minutes.",
                from_=config.TWILIO_FROM_NUMBER,
                to=phone,
            )
            return jsonify({"success": True, "message": "Code sent!"})
        except Exception as e:
            # Log but don't expose Twilio errors to the client
            current_app.logger.error("Twilio SMS error: %s", e)
            return jsonify({"error": "Failed to send SMS. Please try again."}), 500

    # Dev mode fallback — never expose codes in production without Twilio
    response = {"success": True, "message": "Code sent!"}
    if config.OTP_DEV_MODE:
        response["dev_code"] = code
    return jsonify(response)




@storefront_bp.route("/auth/verify-otp", methods=["POST"])
@limiter.limit("10 per 10 minutes")
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

    session.permanent = True
    session["customer_id"] = customer_id
    session["customer_name"] = new_name
    session["customer_phone"] = phone
    session["customer_email"] = new_email
    # Store the current session token version for "Sign out everywhere" check
    try:
        ver_conn = db.get_db()
        ver_row = ver_conn.execute(
            "SELECT session_token_version FROM customers WHERE id = ?", (customer_id,)
        ).fetchone()
        if ver_row:
            session["session_token_version"] = ver_row["session_token_version"]
        ver_conn.close()
    except Exception:
        pass
    # Merge guest cart into persistent cart
    try:
        merge_conn = db.get_db()
        _merge_guest_cart(merge_conn, customer_id)
        merge_conn.commit()
        merge_conn.close()
    except Exception:
        pass
    return jsonify({"success": True, "name": new_name, "phone": phone, "email": new_email})




@storefront_bp.route("/auth/phone/verify", methods=["POST"])
@limiter.limit("10 per 10 minutes")
def auth_phone_verify():
    """Called from the browser right after Firebase confirms the SMS code
    OR after a successful Google Sign-In redirect/popup. The Firebase ID
    token is already server-verified proof of identity, so CSRF is not
    needed — an attacker who has a valid ID token already owns the session."""
    data = request.get_json(force=True, silent=True) or {}
    id_token = data.get("id_token", "")

    current_app.logger.info(
        "POST /auth/phone/verify: has_id_token=%s has_name=%s has_email=%s",
        bool(id_token),
        bool(data.get("name")),
        bool(data.get("email")),
    )

    # Skip CSRF when a Firebase ID token is present — it's the auth proof.
    # This avoids the "session expired" error after Google redirect flows
    # where the page reload may generate a new csrf_token.
    if id_token:
        pass  # Firebase ID token IS the auth — no CSRF needed
    else:
        check_csrf_api()

    if rate_limited("phone-verify", max_attempts=10, window_seconds=60):
        return jsonify({"error": "Too many attempts — please wait a minute and try again."}), 429
    if not firebase_auth_enabled():
        return jsonify({"error": "Phone sign-in isn't set up on this site yet."}), 503

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
    # First try to find the customer by Firebase UID.
    customer = conn.execute(
        "SELECT * FROM customers WHERE firebase_uid = ?",
        (uid,),
    ).fetchone()

    # If not found, try matching by phone number.
    if not customer and phone:
        customer = conn.execute(
            "SELECT * FROM customers WHERE phone = ?",
            (phone,),
        ).fetchone()

    if customer:
        new_name = name or customer["name"]
        new_email = email or customer["email"]

        conn.execute(
            """
            UPDATE customers
            SET firebase_uid = ?, name = ?, email = ?, phone = ?, last_login_at = ?
            WHERE id = ?
            """,
            (
                uid,
                new_name,
                new_email,
                phone,
                db.now(),
                customer["id"],
            ),
        )

        customer_id = customer["id"]

    else:
        try:
            cur = conn.execute(
                """
                INSERT INTO customers
                (firebase_uid, phone, name, email, created_at, last_login_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    uid,
                    phone,
                    name,
                    email,
                    db.now(),
                    db.now(),
                ),
            )
            customer_id = cur.lastrowid
        except Exception as e:
            if "UNIQUE constraint failed: customers.phone" in str(e):
                # Phone already exists (race condition) — fetch and update that record.
                customer = conn.execute(
                    "SELECT * FROM customers WHERE phone = ?",
                    (phone,),
                ).fetchone()
                new_name = name or customer["name"]
                new_email = email or customer["email"]
                conn.execute(
                    """
                    UPDATE customers
                    SET firebase_uid = ?, name = ?, email = ?, last_login_at = ?
                    WHERE id = ?
                    """,
                    (uid, new_name, new_email, db.now(), customer["id"]),
                )
                customer_id = customer["id"]
            else:
                raise
        new_name = name
        new_email = email

    conn.commit()
    conn.close()

    session.permanent = True
    session["customer_id"] = customer_id
    session["customer_name"] = new_name
    session["customer_phone"] = phone
    session["customer_email"] = new_email
    # Store the current session token version for "Sign out everywhere" check
    try:
        ver_conn = db.get_db()
        ver_row = ver_conn.execute(
            "SELECT session_token_version FROM customers WHERE id = ?", (customer_id,)
        ).fetchone()
        if ver_row:
            session["session_token_version"] = ver_row["session_token_version"]
        ver_conn.close()
    except Exception:
        pass
    # Merge guest cart into persistent cart
    try:
        merge_conn = db.get_db()
        _merge_guest_cart(merge_conn, customer_id)
        merge_conn.commit()
        merge_conn.close()
    except Exception:
        pass
    return jsonify({"success": True, "name": new_name, "phone": phone, "email": new_email})




@storefront_bp.route("/auth/update-profile", methods=["POST"])
def auth_update_profile():
    """Update the logged-in customer's name and email. Supports both
    JSON (from auth flow) and form POST (from account hub)."""
    check_csrf_api()
    if not session.get("customer_id"):
        flash("Please sign in first.", "error")
        return redirect(url_for("storefront.home"))
    data = request.get_json(force=True, silent=True) or {}
    name = (data.get("name") or request.form.get("name") or "").strip()
    email = (data.get("email") or request.form.get("email") or "").strip()
    if not name:
        if request.is_json:
            return jsonify({"error": "Please enter your name."}), 400
        flash("Please enter your name.", "error")
        return redirect(url_for("storefront.account_hub"))
    conn = db.get_db()
    conn.execute(
        "UPDATE customers SET name = ?, email = ? WHERE id = ?",
        (name, email, session["customer_id"]),
    )
    conn.commit()
    conn.close()
    session["customer_name"] = name
    session["customer_email"] = email
    if request.is_json:
        return jsonify({"success": True, "name": name, "phone": session.get("customer_phone", ""), "email": email})
    flash("Profile updated.", "success")
    return redirect(url_for("storefront.account_hub"))


# ─── Direct Google OAuth (via GIS, no Firebase) ──────────────────────────



@storefront_bp.route("/auth/google", methods=["POST"])
def auth_google():
    """Verify a Google ID token from the GIS one-tap / button flow and
    create or update a customer session. No Firebase SDK involved — the
    token is verified directly against Google's OAuth2 certs."""
    if rate_limited("auth-google", max_attempts=10, window_seconds=60):
        return jsonify({"error": "Too many attempts — please wait a minute and try again."}), 429
    if not config.GOOGLE_CLIENT_ID:
        return jsonify({"error": "Google sign-in is not set up on this site."}), 503
    check_csrf_api()

    data = request.get_json(force=True, silent=True) or {}
    credential = data.get("credential", "")
    if not credential:
        return jsonify({"error": "Missing credential."}), 400

    # Verify the JWT directly using google-auth library
    try:
        from google.auth.transport import requests as google_requests
        from google.oauth2 import id_token as google_id_token
        req = google_requests.Request()
        decoded = google_id_token.verify_oauth2_token(
            credential, req, audience=config.GOOGLE_CLIENT_ID
        )
    except Exception as exc:
        current_app.logger.warning("Google OAuth token verification failed: %s", exc)
        return jsonify({"error": "We couldn't verify your Google sign-in. Please try again."}), 400

    google_uid = decoded.get("sub", "")
    name = decoded.get("name", "")
    email = decoded.get("email", "") if decoded.get("email_verified") else ""
    if not google_uid:
        return jsonify({"error": "We couldn't verify your Google sign-in. Please try again."}), 400

    conn = db.get_db()
    # First: look up by google_uid (fast match for returning users)
    customer = conn.execute(
        "SELECT * FROM customers WHERE google_uid = ?", (google_uid,)
    ).fetchone()

    # Second: try matching by email if not found by google_uid
    if not customer and email:
        customer = conn.execute(
            "SELECT * FROM customers WHERE email = ? AND email != ''",
            (email,),
        ).fetchone()

    if customer:
        # Update existing customer record with Google info
        new_name = name or customer["name"]
        new_email = email or customer["email"]
        conn.execute(
            """UPDATE customers
               SET google_uid = ?, name = ?, email = ?, last_login_at = ?
               WHERE id = ?""",
            (google_uid, new_name, new_email, db.now(), customer["id"]),
        )
        customer_id = customer["id"]
    else:
        # Create new customer from Google identity
        # Use a unique placeholder phone to avoid UNIQUE constraint collisions
        # with other Google-only users who don't have a real phone yet.
        unique_phone = f"g_{google_uid[:18]}"
        try:
            cur = conn.execute(
                """INSERT INTO customers
                   (google_uid, phone, name, email, created_at, last_login_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (google_uid, unique_phone, name, email, db.now(), db.now()),
            )
            customer_id = cur.lastrowid
        except Exception as e:
            if "UNIQUE constraint failed: customers.phone" in str(e):
                # Race — soft retry lookup + update
                customer = conn.execute(
                    "SELECT * FROM customers WHERE phone = ?", (unique_phone,)
                ).fetchone()
            else:
                raise
            if customer:
                new_name = name or customer["name"]
                new_email = email or customer["email"]
                conn.execute(
                    """UPDATE customers
                       SET google_uid = ?, name = ?, email = ?, last_login_at = ?
                       WHERE id = ?""",
                    (google_uid, new_name, new_email, db.now(), customer["id"]),
                )
                customer_id = customer["id"]
            else:
                conn.close()
                raise
        new_name = name
        new_email = email

    conn.commit()
    conn.close()

    session.permanent = True
    session["customer_id"] = customer_id
    session["customer_name"] = new_name or ""
    session["customer_phone"] = ""
    session["customer_email"] = new_email or ""
    # Store the current session token version for "Sign out everywhere" check
    try:
        ver_conn = db.get_db()
        ver_row = ver_conn.execute(
            "SELECT session_token_version FROM customers WHERE id = ?", (customer_id,)
        ).fetchone()
        if ver_row:
            session["session_token_version"] = ver_row["session_token_version"]
        ver_conn.close()
    except Exception:
        pass
    # Merge guest cart into persistent cart
    try:
        merge_conn = db.get_db()
        _merge_guest_cart(merge_conn, customer_id)
        merge_conn.commit()
        merge_conn.close()
    except Exception:
        pass
    return jsonify({"success": True, "name": new_name or "", "email": new_email or ""})




@storefront_bp.route("/auth/logout", methods=["POST"])
def auth_logout():
    check_csrf()
    for key in ("customer_id", "customer_name", "customer_phone", "customer_email"):
        session.pop(key, None)
    flash("Signed out.", "success")
    return redirect(request.referrer or url_for("storefront.home"))




@storefront_bp.route("/account/signout-everywhere", methods=["POST"])
@customer_login_required
def account_signout_everywhere():
    """Invalidate all active sessions for the current customer by incrementing
    the session_token_version column. This revokes every session cookie that
    carries an older version number — on any browser, any device."""
    check_csrf()
    customer_id = session.get("customer_id")
    conn = db.get_db()
    try:
        conn.execute(
            "UPDATE customers SET session_token_version = session_token_version + 1 WHERE id = ?",
            (customer_id,),
        )
        conn.commit()
    except Exception:
        pass
    finally:
        conn.close()
    # Clear the current session so this browser is logged out too
    for key in ("customer_id", "customer_name", "customer_phone", "customer_email", "session_token_version"):
        session.pop(key, None)
    flash("You've been signed out of all devices.", "success")
    return redirect(url_for("storefront.home"))




@storefront_bp.route("/auth/delete-account", methods=["POST"])
@customer_login_required
def auth_delete_account():
    """Permanently delete the customer account and anonymise orders."""
    check_csrf()
    customer_id = session.get("customer_id")
    conn = db.get_db()
    # Anonymise orders
    conn.execute(
        "UPDATE orders SET customer_id = NULL, customer_email = 'deleted@anon', customer_name = 'Deleted Account' WHERE customer_id = ?",
        (customer_id,),
    )
    # Delete customer
    conn.execute("DELETE FROM customers WHERE id = ?", (customer_id,))
    conn.commit()
    conn.close()
    for key in ("customer_id", "customer_name", "customer_phone", "customer_email"):
        session.pop(key, None)
    flash("Your account has been deleted.", "success")
    return redirect(url_for("storefront.home"))




@storefront_bp.route("/account/wishlist")
@customer_login_required
def account_wishlist():
    """Page showing all wishlisted items."""
    conn = db.get_db()
    rows = conn.execute(
        """SELECT w.product_id, w.created_at, p.name, p.slug, p.price, p.compare_price, p.quantity
           FROM wishlist_items w JOIN products p ON p.id = w.product_id
           WHERE w.customer_id = ? AND p.active = 1
           ORDER BY w.created_at DESC""",
        (session["customer_id"],),
    ).fetchall()
    conn.close()
    catalog = get_catalog()
    product_images = catalog["product_images"]
    return render_template(
        "account_wishlist.html",
        settings=get_settings(),
        items=rows,
        product_images=product_images,
    )




@storefront_bp.route("/account")
@customer_login_required
def account_hub():
    """Account hub: orders, library, change username, logout."""
    conn = db.get_db()
    customer_id = session.get("customer_id")
    customer = conn.execute(
        "SELECT name, phone FROM customers WHERE id = ?", (customer_id,)
    ).fetchone()
    return render_template("account_hub.html", customer=customer)




@storefront_bp.route("/account/library")
@customer_login_required
def account_library():
    """Show every digital item a customer has ever purchased, grouped by product.
    Includes both delivered and paid items — a purchase is yours the moment
    payment clears, not just after the admin clicks "deliver"."""
    conn = db.get_db()
    customer_id = session.get("customer_id")
    orders = conn.execute(
        """SELECT o.id, o.order_ref, o.delivery_message, o.status, o.created_at, o.delivered_at,
                  oi.product_id, oi.product_name
           FROM orders o
           JOIN order_items oi ON oi.order_id = o.id
           WHERE o.customer_id = ? AND o.status IN ('paid', 'delivered')
           ORDER BY COALESCE(o.delivered_at, o.created_at) DESC""",
        (customer_id,),
    ).fetchall()
    # Load product info + files for each unique product
    product_ids = list(set(o["product_id"] for o in orders))
    products = {}
    product_files = {}
    if product_ids:
        placeholders = ",".join("?" for _ in product_ids)
        products_data = conn.execute(
            f"SELECT id, name, slug, delivery_content_type, delivery_mode FROM products WHERE id IN ({placeholders})",
            product_ids,
        ).fetchall()
        for p in products_data:
            products[p["id"]] = dict(p)
            files = conn.execute(
                "SELECT id, original_name, file_size, version, created_at FROM product_files WHERE product_id = ? ORDER BY created_at DESC",
                (p["id"],),
            ).fetchall()
            product_files[p["id"]] = [dict(f) for f in files]
    conn.close()
    return render_template(
        "account_library.html",
        settings=get_settings(),
        orders=orders,
        products=products,
        product_files=product_files,
    )




@storefront_bp.route("/account/orders")
@customer_login_required
def account_orders():
    conn = db.get_db()
    try:
        orders = conn.execute(
            "SELECT * FROM orders WHERE customer_id = ? ORDER BY id DESC",
            (session.get("customer_id"),),
        ).fetchall()
    except Exception as exc:
        _dblog = getattr(db, "_db_logger", None)
        if _dblog:
            _dblog.warning(
                "customer_id query failed (migration missing?): %s",
                exc,
            )
        orders = conn.execute(
            "SELECT * FROM orders ORDER BY id DESC"
        ).fetchall()
    finally:
        conn.close()

    return render_template(
        "account_orders.html",
        settings=get_settings(),
        orders=orders,
    )




@storefront_bp.route("/download/<token>")
def download_product(token):
    """Secure download link — validates the token, serves the file, marks
    it used. Tokens expire after 72 hours."""
    conn = db.get_db()
    row = conn.execute(
        "SELECT * FROM download_tokens WHERE token = ?", (token,)
    ).fetchone()
    if not row:
        conn.close()
        return abort(404)
    from datetime import datetime, timezone
    expires = datetime.fromisoformat(row["expires_at"])
    if expires < datetime.now(timezone.utc):
        try:
            record_download_audit(conn, download_token=token, success=False, failure_reason="expired", ip_address=request.remote_addr or "", user_agent=request.headers.get("User-Agent", ""))
            conn.commit()
        finally:
            conn.close()
        return abort(410)
    # Multi-download: decrement remaining count instead of one-shot used flag.
    # Existing tokens (migrated with downloads_remaining=1) get one download,
    # new tokens get MAX_DOWNLOADS (5) re-downloads within the expiry window.
    remaining = row["downloads_remaining"]
    if remaining <= 0:
        try:
            record_download_audit(conn, order_id=row["order_id"], download_token=row["token"], product_id=row["product_id"], success=False, failure_reason="exhausted", ip_address=request.remote_addr or "", user_agent=request.headers.get("User-Agent", ""))
            conn.commit()
        finally:
            conn.close()
        return abort(410)
    file_path = product_file_path(row["file_path"])
    # Validate the private path and file before consuming a download. A stale
    # token must not lose one of the customer's remaining downloads.
    if not file_path or not os.path.isfile(file_path):
        try:
            record_download_audit(conn, order_id=row["order_id"], download_token=row["token"], product_id=row["product_id"], success=False, failure_reason="missing_file", ip_address=request.remote_addr or "", user_agent=request.headers.get("User-Agent", ""))
            conn.commit()
        finally:
            conn.close()
        return abort(404)
    updated = conn.execute(
        """UPDATE download_tokens
           SET downloads_remaining = downloads_remaining - 1
           WHERE id = ? AND downloads_remaining > 0""",
        (row["id"],),
    )
    if updated.rowcount != 1:
        try:
            record_download_audit(conn, order_id=row["order_id"], download_token=row["token"], product_id=row["product_id"], success=False, failure_reason="race_or_exhausted", ip_address=request.remote_addr or "", user_agent=request.headers.get("User-Agent", ""))
            conn.commit()
        finally:
            conn.close()
        return abort(410)
    try:
        record_download_audit(
            conn,
            order_id=row["order_id"],
            download_token=row["token"],
            product_id=row["product_id"],
            success=True,
            ip_address=request.remote_addr or "",
            user_agent=request.headers.get("User-Agent", ""),
        )
        conn.commit()
    finally:
        conn.close()
    return send_file(file_path, as_attachment=True, download_name=row["filename"])




@storefront_bp.route("/api/newsletter/subscribe", methods=["POST"])
@limiter.limit("5 per hour")
def api_newsletter_subscribe():
    """JSON newsletter subscribe endpoint for AJAX."""
    if rate_limited("newsletter", max_attempts=5, window_seconds=60):
        return jsonify({"error": "Too many attempts."}), 429
    data = request.get_json(force=True, silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    if not email or "@" not in email:
        return jsonify({"error": "Please enter a valid email."}), 400
    conn = db.get_db()
    try:
        conn.execute(
            "INSERT INTO newsletter_subscribers (email, created_at) VALUES (?, ?)",
            (email, db.now()),
        )
        conn.commit()
        # Send welcome email with unsubscribe link
        if customer_email_notifications_enabled():
            try:
                site_name = get_settings().get("site_name", "Virtual Store")
                unsub_url = url_for("storefront.newsletter_unsubscribe", email=email, _external=True)
                send_email(
                    email,
                    f"You're subscribed to {site_name}",
                    f"Thanks for subscribing to {site_name}!\n\nYou'll be the first to know about new products and offers.\n\nTo unsubscribe at any time: {unsub_url}",
                )
            except Exception:
                pass
        conn.close()
        return jsonify({"success": True})
    except Exception:
        conn.close()
        return jsonify({"error": "Already subscribed."}), 409




@storefront_bp.route("/newsletter/unsubscribe", methods=["GET"])
def newsletter_unsubscribe():
    """One-click unsubscribe via email query param."""
    email = (request.args.get("email") or "").strip().lower()
    if email and "@" in email:
        conn = db.get_db()
        conn.execute("DELETE FROM newsletter_subscribers WHERE email = ?", (email,))
        conn.commit()
        conn.close()
    return render_template("unsubscribed.html", settings=get_settings())




@storefront_bp.route("/newsletter/subscribe", methods=["POST"])
@limiter.limit("5 per hour")
def newsletter_subscribe():
    check_csrf()
    if rate_limited("newsletter", max_attempts=5, window_seconds=60):
        flash("Too many attempts — please wait a minute and try again.", "error")
        return redirect(url_for("storefront.home") + "#newsletter")
    if turnstile_enabled() and not verify_turnstile(request.form.get("cf-turnstile-response", "")):
        flash("Please complete the verification and try again.", "error")
        return redirect(url_for("storefront.home") + "#newsletter")
    email = (request.form.get("email") or "").strip().lower()
    if not email or "@" not in email:
        flash("Please enter a valid email address.", "error")
        return redirect(url_for("storefront.home") + "#newsletter")
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
    return redirect(url_for("storefront.home") + "#newsletter")




@storefront_bp.route("/robots.txt")
def robots():
    lines = [
        "User-agent: *",
        "Allow: /",
        "Disallow: /admin/",
        # /instance/ isn't Flask-served (no real exposure), but disallowing
        # it explicitly is defense-in-depth since INITIAL_ADMIN_PASSWORD.txt
        # briefly lives there on first boot.
        "Disallow: /instance/",
        f"Sitemap: {url_for('storefront.sitemap', _external=True)}",
    ]
    return "\n".join(lines), 200, {"Content-Type": "text/plain"}




@storefront_bp.route("/favicon.ico")
def favicon():
    # Only favicon.svg exists in the repo — serve it for .ico requests too
    # so older browsers/crawlers get a real icon instead of an empty 204.
    return current_app.send_static_file("favicon.svg")




@storefront_bp.route("/uploads/")
def uploads_root():
    """Return the canonical uploads base URL used by templates and JS."""
    return "", 204




@storefront_bp.route("/uploads/<path:filename>")


@storefront_bp.route("/static/uploads/<path:filename>")
def uploaded_file(filename):
    """Serve uploaded images/files safely, falling back to a tiny placeholder
    SVG when a legacy reference points at a file that no longer exists.
    This avoids noisy 404s for old browser or DB references."""
    filename = (filename or "").strip()
    if not filename:
        return _missing_upload_placeholder()
    safe_path = os.path.join(config.UPLOAD_FOLDER, filename)
    if os.path.exists(safe_path):
        return send_from_directory(config.UPLOAD_FOLDER, filename)
    return _missing_upload_placeholder()


def _missing_upload_placeholder():
    svg = """<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 400 500' role='img' aria-label='Image unavailable'>
  <rect width='400' height='500' fill='#111827'/>
  <rect x='28' y='28' width='344' height='444' rx='28' fill='#1f2937' stroke='#374151'/>
  <circle cx='200' cy='190' r='46' fill='#374151'/>
  <path d='M110 370l64-78 46 50 26-30 54 58H110z' fill='#374151'/>
  <text x='200' y='420' text-anchor='middle' fill='#9ca3af' font-family='Arial, sans-serif' font-size='22'>Image unavailable</text>
</svg>"""
    return Response(svg, mimetype="image/svg+xml")




@storefront_bp.route("/sitemap.xml", endpoint='sitemap')
def sitemap():
    catalog = get_catalog()
    urls = [url_for("storefront.home", _external=True), url_for("storefront.track_order", _external=True),
            url_for("storefront.terms_of_service", _external=True), url_for("storefront.privacy_policy", _external=True)]
    xml = ['<?xml version="1.0" encoding="UTF-8"?>', '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for u in urls:
        xml.append(f"<url><loc>{u}</loc><lastmod>{datetime.now(timezone.utc).date()}</lastmod><changefreq>weekly</changefreq><priority>0.8</priority></url>")
    for p in catalog["products"]:
        loc = url_for("storefront.product_detail", slug=p["slug"], _external=True)
        lastmod = (p["created_at"][:10] if p["created_at"] else datetime.now(timezone.utc).date())
        xml.append(f"<url><loc>{loc}</loc><lastmod>{lastmod}</lastmod><changefreq>weekly</changefreq><priority>0.6</priority></url>")
    xml.append("</urlset>")
    return "\n".join(xml), 200, {"Content-Type": "application/xml"}


@storefront_bp.errorhandler(404)
def not_found(e):
    # Never touch the database here — missing assets can trigger 404s and we
    # do not want a broken image path to cascade into a Turso lookup.
    return render_template("404.html", settings=db.DEFAULT_SETTINGS), 404


@storefront_bp.errorhandler(500)
def internal_error(e):
    rid = getattr(g, "request_id", "")
    current_app.logger.exception("Unhandled error rid=%s %s %s", rid, request.method, request.path)
    return render_template("500.html", settings=db.DEFAULT_SETTINGS, request_id=rid), 500


# ============================================================= ADMIN AUTH



@storefront_bp.route("/api/performance", methods=["POST"])
def api_performance():
    """Ingest Web Vitals / performance metrics from the browser.
    Rate-limited since it's unauthenticated."""
    if rate_limited("api-perf", max_attempts=120, window_seconds=60):
        return ("", 204)
    data = request.get_json(silent=True) or {}
    metrics = data.get("metrics") if isinstance(data, dict) else None
    if not metrics:
        return ("", 204)
    conn = None
    try:
        conn = db.get_db()
        now_str = db.now()
        for m in metrics:
            name = (m.get("name") or "").strip()
            value = m.get("value")
            page_path = (m.get("path") or request.referrer or "").strip()
            if name and value is not None:
                conn.execute(
                    "INSERT INTO performance_metrics (metric_type, metric_name, value, page_path, created_at) VALUES (?, ?, ?, ?, ?)",
                    ("cwv", name, float(value), page_path[:500], now_str),
                )
        conn.commit()
    except Exception:
        pass
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
    return ("", 204)




@storefront_bp.route("/webhook/razorpay", methods=["POST"])
@limiter.limit("120 per minute")
@csrf.exempt
def razorpay_webhook():
    """Razorpay webhook handler — catches payment.captured events server-side
    so payment confirmation isn't solely dependent on the fragile client-side
    verify call. If the customer's tab crashes between payment success and the
    /api/verify-payment call, this endpoint still confirms the order.

    Configure in Razorpay Dashboard -> Settings -> Webhooks with the webhook
    URL and set RAZORPAY_WEBHOOK_SECRET in your environment to the webhook
    secret shown in the dashboard."""
    webhook_body = request.get_data()
    webhook_signature = request.headers.get("X-Razorpay-Signature", "")

    if not rzp.verify_webhook_signature(webhook_body, webhook_signature):
        return jsonify({"error": "Invalid webhook signature"}), 400

    import json as _json
    try:
        payload = _json.loads(webhook_body)
    except (ValueError, TypeError):
        return jsonify({"error": "Invalid JSON"}), 400

    event = payload.get("event", "")
    event_id = request.headers.get("X-Razorpay-Event-ID", "").strip()
    if not event_id:
        import hashlib as _hashlib
        event_id = "sha256:" + _hashlib.sha256(webhook_body).hexdigest()
    conn = db.get_db()
    if event_id:
        try:
            is_new = record_webhook_event(conn, event_id, event, payload)
            conn.commit()
        except Exception:
            conn.rollback()
            conn.close()
            return jsonify({"error": "Webhook persistence failed"}), 503
        if not is_new:
            conn.close()
            return jsonify({"status": "duplicate"}), 200
    # Refund events are the authoritative final-state notifications for refunds.
    if event in ("refund.created", "refund.processed", "refund.failed"):
        entity = payload.get("payload", {}).get("refund", {}).get("entity", {})
        provider_refund_id = (entity.get("id") or "").strip()
        provider_payment_id = (entity.get("payment_id") or "").strip()
        refund_row = None
        if provider_refund_id:
            refund_row = conn.execute(
                "SELECT * FROM order_refunds WHERE provider_refund_id=? ORDER BY id DESC LIMIT 1",
                (provider_refund_id,),
            ).fetchone()
        if not refund_row and provider_payment_id:
            refund_row = conn.execute(
                "SELECT r.* FROM order_refunds r JOIN orders o ON o.id=r.order_id "
                "WHERE o.razorpay_payment_id=? AND r.status IN ('pending','processing') "
                "ORDER BY r.id DESC LIMIT 1",
                (provider_payment_id,),
            ).fetchone()
        if refund_row:
            if event == "refund.processed":
                provider_id = provider_refund_id or refund_row["provider_refund_id"]
                conn.execute(
                    "UPDATE order_refunds SET status='processed', provider_refund_id=?, processed_at=? WHERE id=?",
                    (provider_id, db.now(), refund_row["id"]),
                )
                total = conn.execute(
                    "SELECT COALESCE(SUM(amount),0) AS total FROM order_refunds WHERE order_id=? AND status='processed'",
                    (refund_row["order_id"],),
                ).fetchone()["total"]
                order_for_refund = conn.execute("SELECT * FROM orders WHERE id=?", (refund_row["order_id"],)).fetchone()
                if order_for_refund:
                    if int(total or 0) >= int(order_for_refund["amount"]):
                        conn.execute(
                            "UPDATE orders SET status='refunded', payment_state='refunded', refunded_amount=?, refunded_at=?, razorpay_refund_id=? WHERE id=?",
                            (int(total), db.now(), provider_id, refund_row["order_id"]),
                        )
                    else:
                        conn.execute(
                            "UPDATE orders SET refunded_amount=?, razorpay_refund_id=? WHERE id=?",
                            (int(total), provider_id, refund_row["order_id"]),
                        )
            elif event == "refund.failed":
                conn.execute(
                    "UPDATE order_refunds SET status='failed', provider_refund_id=?, failed_at=?, failure_reason=? WHERE id=?",
                    (provider_refund_id or refund_row["provider_refund_id"], db.now(), "Provider reported refund failure", refund_row["id"]),
                )
            else:
                conn.execute(
                    "UPDATE order_refunds SET provider_refund_id=? WHERE id=?",
                    (provider_refund_id or refund_row["provider_refund_id"], refund_row["id"]),
                )
        if event_id:
            mark_webhook_event_processed(conn, event_id, status="processed")
        conn.commit()
        conn.close()
        return jsonify({"status": "processed"}), 200

    # Only handle payment.captured — other events are ignored (but acknowledged)
    if event != "payment.captured":
        if event_id:
            mark_webhook_event_processed(conn, event_id, status="ignored")
            conn.commit()
        conn.close()
        return jsonify({"status": "ignored"}), 200

    payment = payload.get("payload", {}).get("payment", {}).get("entity", {})
    razorpay_order_id = payment.get("order_id", "")
    razorpay_payment_id = payment.get("id", "")

    if not razorpay_order_id:
        if event_id:
            mark_webhook_event_processed(conn, event_id, status="ignored", error="missing order_id")
            conn.commit()
        conn.close()
        return jsonify({"status": "no order_id"}), 200

    order = conn.execute(
        "SELECT * FROM orders WHERE razorpay_order_id = ?", (razorpay_order_id,)
    ).fetchone()

    if not order:
        if event_id:
            mark_webhook_event_processed(conn, event_id, status="ignored", error="order not found")
            conn.commit()
        conn.close()
        return jsonify({"status": "order not found"}), 200

    # Idempotency: if already paid/delivered, don't re-run side effects
    if order["status"] in ("paid", "delivered"):
        if event_id:
            mark_webhook_event_processed(conn, event_id, status="processed")
            conn.commit()
        conn.close()
        return jsonify({"status": "already confirmed"}), 200

    # Mark as paid (same transition as /api/verify-payment, but without the
    # client-side signature — the webhook signature itself is the proof). The
    # provider payload must still match our expected amount/currency/capture state.
    expected_amount_minor = int(order["amount"]) * 100
    provider_amount = int(payment.get("amount") or 0)
    provider_currency = str(payment.get("currency") or "").upper()
    if provider_amount != expected_amount_minor or provider_currency != "INR" or payment.get("captured") is False:
        if event_id:
            mark_webhook_event_processed(conn, event_id, status="failed", error="payment amount/currency/capture mismatch")
            conn.commit()
        conn.close()
        return jsonify({"error": "Payment data does not match the order."}), 400

    order_items = conn.execute(
        "SELECT * FROM order_items WHERE order_id = ?", (order["id"],)
    ).fetchall()
    try:
        confirm_order_payment_durable(conn, order=order, order_items=order_items, payment_mode="gateway", razorpay_payment_id=razorpay_payment_id, razorpay_signature="", confirm_callable=_confirm_order_payment)
        if event_id:
            mark_webhook_event_processed(conn, event_id, status="processed")
        conn.commit()
    except Exception as exc:
        conn.rollback()
        if event_id:
            try:
                mark_webhook_event_processed(conn, event_id, status="failed", error=str(exc)[:500])
                conn.commit()
            except Exception:
                conn.rollback()
        conn.close()
        return jsonify({"error": "Webhook processing failed"}), 500
    conn.close()
    return jsonify({"status": "confirmed"}), 200




@storefront_bp.route("/csp-report", methods=["POST"])
@limiter.limit("60 per minute")
@csrf.exempt
def csp_report():
    """Receive Content-Security-Policy violation reports (from the report-uri
    directive in the CSP header). Logs them for monitoring — no response body
    needed, the browser just needs a 204."""
    import logging as _logging
    try:
        report = request.get_json(silent=True) or {}
        _logging.getLogger("virtual_store").warning(
            "CSP violation: %s", report.get("csp-report", {})
        )
    except Exception:
        pass
    return "", 204




@storefront_bp.route("/health", methods=["GET", "HEAD"])
def health():
    """Alias for Render monitoring and quick checks."""
    return "ok", 200




@storefront_bp.route("/healthz", methods=["GET", "HEAD"])
def healthz():
    """Readiness check: verify the configured database can execute a query."""
    conn = None
    try:
        db.init_db_if_needed()
        conn = db.get_db()
        conn.execute("SELECT 1").fetchone()
        return "ok", 200
    except Exception:
        current_app.logger.exception("Health check database probe failed")
        return "unhealthy", 503
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass




@storefront_bp.route("/set-timezone", methods=["POST"])
def set_timezone():
    """Receives client-side timezone offset (seconds from UTC) and stores in session."""
    check_csrf_api()
    data = request.get_json(silent=True)
    if data and "offset" in data:
        session["timezone_offset"] = int(data["offset"])
        session.modified = True
    return "ok"




# Google Search Console site ownership verification — must be served at /google*.html


@storefront_bp.route("/googlead21c3b32e52177a.html")
def google_verification():
    import os
    return send_file(os.path.join(current_app.static_folder, "googlead21c3b32e52177a.html"))


# Service worker for admin offline + push support — served from app root for scope


@storefront_bp.route("/sw-admin.js")
def service_worker_admin():
    resp = send_from_directory("static", "sw-admin.js")
    resp.headers["Service-Worker-Allowed"] = "/"
    resp.headers["Cache-Control"] = "no-cache"
    return resp


if __name__ == "__main__":
    import os
    from app import create_app
    port = int(os.environ.get("PORT", 5000))
    create_app().run(host="0.0.0.0", port=port, debug=config.DEBUG)


