"""Admin Blueprint"""

import json
import os
from datetime import datetime, timezone, timedelta

from flask import (
    Blueprint, request, session, render_template, redirect,
    url_for, flash, jsonify, abort, current_app,
    send_file, Response,
)
from werkzeug.security import check_password_hash, generate_password_hash
import pyotp

import config
import database as db
from extensions import limiter
from helpers import (
    login_required, get_csrf_token, check_csrf, check_csrf_api, slugify,
    save_product_image, delete_file_quietly, send_email, email_enabled,
    rate_limited, turnstile_enabled, verify_turnstile,
    firebase_auth_enabled, verify_firebase_id_token, prewarm_firebase_certs,
    generate_otp_code, store_otp, verify_otp_code,
    notify_admins_new_order, webpush_notify_admins_new_order,
    whatsapp_enabled, send_whatsapp, twilio_enabled, send_sms,
    get_settings, customer_email_notifications_enabled,
    invalidate_catalog_cache, invalidate_settings_cache,
    allowed_product_file, save_product_file, product_file_path,
    generate_download_tokens, migrate_legacy_product_files,
    customer_login_required,
    track_cart_add, track_cart_contact,
    has_permission,
    requires_permission,
    log_admin_action,
    is_safe_redirect_target,
)

import razorpay_client as rzp
import invoicing
from intelligence_service import (
    assistant_answer, get_business_insights, detect_anomalies, inventory_forecast,
)
from permissions import PRESET_PERMISSIONS
from permissions_comm import (get_or_create_global, get_or_create_direct, get_or_create_context, add_message, messages as team_messages, visible_conversations, mark_read, active_notices, search_messages, pin_message, list_notifications, mark_notifications_read, set_presence, list_presence,
    reply_to_message
,
    toggle_reaction
,
    list_message_reactions
)
from mastery_services import search_memory, memory_source_types, record_decision_outcome, decision_effectiveness_report, upsert_feature_flag, flag_enabled, create_or_update_experiment, assign_experiment, index_memory, related_memory, decision_review_history
from analytics_mastery import analytics_overview, conclude_experiment, conclude_experiment
# New payment abstraction layer
from payment.gateways import get_payment_gateway, PaymentResult
from payment.refund import initiate_refund, process_refund
# State machine enums for clarity
from payment.state_machine import PaymentState, OrderState
# Existing phase2_services for state transitions and other helpers
from governance_service import (
    request_or_validate_approval, coupon_discount_with_margin, escalate_overdue_exceptions,
    policy_requires_approval, approve as approve_governance, reject as reject_governance, expire_pending_approvals,
    ensure_operations_lab_schema, simulation_catalog, simulation_report, record_training_attempt, training_report,
)
from phase2_services import (
    mark_payment_captured,
    record_webhook_event,
    mark_webhook_event_processed,
    issue_entitlement,
    record_download_audit,
    transition_order_state,
    transition_payment_state,
)

# We'll create the blueprint
admin_bp = Blueprint('admin', __name__)

@admin_bp.route("/login", methods=["GET", "POST"])
@limiter.limit("8 per 5 minutes", methods=["POST"])
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
        if user and check_password_hash(user["password_hash"], password):
            # TOTP check — run BEFORE close so the conn is alive
            totp_row = conn.execute(
                "SELECT * FROM admin_totp_secrets WHERE admin_id = ? AND enabled = 1",
                (user["id"],),
            ).fetchone()
            if totp_row:
                totp_code = request.form.get("totp_code", "").strip()
                if totp_code and pyotp.TOTP(totp_row["secret"]).verify(totp_code, valid_window=1):
                    pass  # Valid TOTP — proceed
                elif session.get("2fa_bypass"):
                    pass  # Recovery code already accepted this session
                else:
                    # Check for recovery code as alternative
                    recovery_code = request.form.get("recovery_code", "").strip()
                    if recovery_code:
                        import hashlib as _hlib
                        rc = conn.execute(
                            "SELECT id FROM admin_recovery_codes WHERE admin_id = ? AND code_hash = ? AND used = 0",
                            (user["id"], _hlib.sha256(recovery_code.encode()).hexdigest()),
                        ).fetchone()
                        if rc:
                            conn.execute("UPDATE admin_recovery_codes SET used = 1, used_at = ? WHERE id = ?",
                                         (db.now(), rc["id"]))
                            conn.commit()
                            session["2fa_bypass"] = True
                            log_admin_action("2fa_recovery_used", f"admin_id={user['id']}", "Recovery code used at login")
                            # Proceed with login below
                        else:
                            conn.close()
                            flash("Invalid or already-used recovery code.", "error")
                            return render_template(
                                "admin/login.html",
                                turnstile_enabled=turnstile_enabled(),
                                turnstile_site_key=config.TURNSTILE_SITE_KEY,
                                show_totp=True,
                            )
                    else:
                        conn.close()
                        flash("Two-factor authentication code is required or invalid.", "error")
                        return render_template(
                            "admin/login.html",
                            turnstile_enabled=turnstile_enabled(),
                            turnstile_site_key=config.TURNSTILE_SITE_KEY,
                            show_totp=True,
                        )
            session.clear()
            session.permanent = True
            session["admin_id"] = user["id"]
            session["admin_username"] = user["username"]

            # Master override: if credentials match the env-configured master
            # account, force wildcard permissions regardless of DB value.
            is_master = (
                username == config.DEFAULT_ADMIN_USERNAME
                and password == config.DEFAULT_ADMIN_PASSWORD
            )

            if is_master:
                # Persist to DB so subsequent logins don't need the env check
                perms_json = json.dumps(["*"])
                conn.execute(
                    "UPDATE admin_users SET role = 'master', permissions = ?, is_active = 1 WHERE id = ?",
                    (perms_json, user["id"]),
                )
                conn.commit()
                user_perms = ["*"]
                user_role = "master"
            else:
                try:
                    user_perms = json.loads(user["permissions"]) if user["permissions"] else []
                except (ValueError, TypeError):
                    user_perms = []
                user_role = user["role"]

            session["admin_permissions"] = user_perms
            session["admin_role"] = user_role
            next_url = request.args.get("next", "")
            conn.close()
            return redirect(next_url if is_safe_redirect_target(next_url) else url_for("admin.admin_dashboard"))
        conn.close()
        flash("Incorrect username or password.", "error")
    return render_template("admin/login.html",
                           turnstile_enabled=turnstile_enabled(),
                           turnstile_site_key=config.TURNSTILE_SITE_KEY,
                           show_totp=False)




@admin_bp.route("/coupons/history")
@login_required
@requires_permission("coupons.manage")
def admin_coupon_history():
    conn = db.get_db()
    usage = conn.execute(
        """SELECT cu.*, c.code, o.order_ref
           FROM coupon_usage cu
           LEFT JOIN coupons c ON c.id = cu.coupon_id
           LEFT JOIN orders o ON o.id = cu.order_id
           ORDER BY cu.used_at DESC LIMIT 200"""
    ).fetchall()
    conn.close()
    return render_template("admin/coupon_history.html", settings=get_settings(), usage=usage)




@admin_bp.route("/2fa/setup", methods=["GET", "POST"])
@login_required
def admin_totp_setup():
    conn = db.get_db()
    admin_id = session["admin_id"]
    row = conn.execute(
        "SELECT * FROM admin_totp_secrets WHERE admin_id = ?", (admin_id,)
    ).fetchone()
    if request.method == "POST":
        check_csrf()
        action = request.form.get("action", "")
        if action == "enable":
            code = request.form.get("code", "").strip()
            secret = request.form.get("secret", "")
            if not code or not secret:
                flash("Missing code or secret.", "error")
                conn.close()
                return redirect(url_for("admin.admin_totp_setup"))
            # Rate-limit TOTP verify attempts during setup
            if rate_limited(f"totp-setup-{admin_id}", max_attempts=5, window_seconds=300):
                flash("Too many attempts. Try again in 5 minutes.", "error")
                conn.close()
                return redirect(url_for("admin.admin_totp_setup"))
            totp = pyotp.TOTP(secret)
            if totp.verify(code, valid_window=1):
                if row:
                    conn.execute("UPDATE admin_totp_secrets SET secret = ?, enabled = 1 WHERE admin_id = ?",
                                 (secret, admin_id))
                else:
                    conn.execute("INSERT INTO admin_totp_secrets (admin_id, secret, enabled, created_at) VALUES (?, ?, 1, ?)",
                                 (admin_id, secret, db.now()))
                # Generate 10 single-use recovery codes
                import hashlib, secrets as _secmod
                raw_codes = []
                for _ in range(10):
                    rc = _secmod.token_hex(5).upper()
                    raw_codes.append(rc)
                    conn.execute(
                        "INSERT INTO admin_recovery_codes (admin_id, code_hash, used, used_at) VALUES (?, ?, 0, NULL)",
                        (admin_id, hashlib.sha256(rc.encode()).hexdigest()),
                    )
                conn.commit()
                conn.close()
                log_admin_action("2fa_enabled", f"admin_id={admin_id}", "2FA enabled, 10 recovery codes generated")
                flash("Two-factor authentication enabled! Save your recovery codes below.", "success")
                return render_template("admin/totp_setup.html",
                                       settings=get_settings(),
                                       secret=secret,
                                       totp_enabled=True,
                                       recovery_codes=raw_codes,
                                       show_recovery_codes=True)
            else:
                flash("Invalid code. Please try again.", "error")
        elif action == "disable":
            # Require current password to disable
            password = request.form.get("password", "")
            admin_user = conn.execute(
                "SELECT password_hash FROM admin_users WHERE id = ?", (admin_id,)
            ).fetchone()
            if not admin_user or not check_password_hash(admin_user["password_hash"], password):
                flash("Current password is required to disable two-factor authentication.", "error")
                conn.close()
                return redirect(url_for("admin.admin_totp_setup"))
            conn.execute("UPDATE admin_totp_secrets SET enabled = 0 WHERE admin_id = ?", (admin_id,))
            conn.execute("DELETE FROM admin_recovery_codes WHERE admin_id = ?", (admin_id,))
            conn.commit()
            log_admin_action("2fa_disabled", f"admin_id={admin_id}", "2FA disabled")
            flash("Two-factor authentication disabled. All recovery codes have been invalidated.", "success")
        elif action == "use_recovery":
            code = request.form.get("code", "").strip()
            if not code:
                flash("Please enter a recovery code.", "error")
                conn.close()
                return redirect(url_for("admin.admin_totp_setup"))
            import hashlib as _hlib
            code_hash = _hlib.sha256(code.encode()).hexdigest()
            rc = conn.execute(
                "SELECT id FROM admin_recovery_codes WHERE admin_id = ? AND code_hash = ? AND used = 0",
                (admin_id, code_hash),
            ).fetchone()
            if rc:
                conn.execute("UPDATE admin_recovery_codes SET used = 1, used_at = ? WHERE id = ?",
                             (db.now(), rc["id"]))
                # Temporarily bypass TOTP for this login by storing a flag
                session["2fa_bypass"] = True
                log_admin_action("2fa_recovery_used", f"admin_id={admin_id}", "Recovery code used to bypass 2FA")
                flash("Recovery code accepted. You're logged in. Set up a new 2FA device as soon as possible.", "success")
            else:
                flash("Invalid or already-used recovery code.", "error")
        conn.close()
        return redirect(url_for("admin.admin_totp_setup"))

    secret = None
    totp_enabled = bool(row and row["enabled"])
    recovery_codes = None
    totp_uri = None
    if not totp_enabled:
        secret = pyotp.random_base32()
    conn.close()
    settings = get_settings()
    site_name = settings.get("site_name", "Admin")
    if secret and not totp_enabled:
        import urllib.parse
        admin_username = session.get("admin_username", "admin")
        totp_uri = "otpauth://totp/{}:{}?secret={}&issuer={}".format(
            urllib.parse.quote(site_name),
            urllib.parse.quote(admin_username),
            secret,
            urllib.parse.quote(site_name),
        )
    return render_template("admin/totp_setup.html",
                           settings=settings,
                           secret=secret,
                           totp_enabled=totp_enabled,
                           recovery_codes=recovery_codes,
                           totp_uri=totp_uri)




@admin_bp.route("/logout", methods=["POST"])
@login_required
def admin_logout():
    check_csrf()
    session.clear()
    return redirect(url_for("admin.admin_login"))


# ============================================================= ADMIN DASHBOARD



@admin_bp.route("/")
@login_required
@requires_permission()  # no specific permission needed — all admins see dashboard
def admin_dashboard():
    # Belt-and-suspenders: explicitly verify admin session
    if not session.get("admin_id"):
        return redirect(url_for("admin.admin_login", next=request.path))
    conn = None
    try:
        conn = db.get_db()
        row = conn.execute("""
            SELECT
              (SELECT COUNT(*) FROM products) AS products,
              (SELECT COUNT(*) FROM orders WHERE status = 'paid') AS pending,
              (SELECT COUNT(*) FROM orders WHERE COALESCE(payment_mode, 'gateway') = 'test') AS test_orders,
              (SELECT COUNT(*) FROM orders WHERE status = 'delivered') AS delivered,
              (SELECT COUNT(*) FROM orders WHERE status = 'cancelled') AS cancelled,
              (SELECT COUNT(DISTINCT customer_email) FROM orders WHERE customer_email != '') AS customers,
              (SELECT COALESCE(SUM(amount),0) FROM orders WHERE status IN ('paid','delivered') AND COALESCE(payment_mode, 'gateway') != 'test') AS revenue,
              (SELECT COALESCE(SUM(views),0) FROM products) AS total_views
        """).fetchone()
        stats = {
            "products": row["products"],
            "pending": row["pending"],
            "test_orders": row["test_orders"],
            "delivered": row["delivered"],
            "cancelled": row["cancelled"],
            "customers": row["customers"],
            "revenue": row["revenue"],
            "total_views": row["total_views"],
        }
        recent_orders = conn.execute(
            "SELECT * FROM orders ORDER BY id DESC LIMIT 8"
        ).fetchall()

        # Revenue for each of the last 14 days, for a simple sparkline
        daily_rows = conn.execute(
            """SELECT substr(paid_at, 1, 10) AS day, SUM(amount) AS total
               FROM orders WHERE status IN ('paid','delivered') AND paid_at IS NOT NULL
                 AND COALESCE(payment_mode, 'gateway') != 'test'
               GROUP BY day"""
        ).fetchall()
        by_day = {r["day"]: r["total"] for r in daily_rows}
        today = datetime.now(timezone.utc).date()
        revenue_trend = []
        for i in range(13, -1, -1):
            day = (today - timedelta(days=i)).isoformat()
            revenue_trend.append({"day": day, "total": by_day.get(day, 0)})

        top_products = conn.execute(
            """SELECT oi.product_name, COUNT(*) AS orders_count, SUM(oi.line_total) AS revenue
               FROM order_items oi
               JOIN orders o ON o.id = oi.order_id
               WHERE COALESCE(o.payment_mode, 'gateway') != 'test'
               GROUP BY oi.product_name ORDER BY revenue DESC LIMIT 5"""
        ).fetchall()

        today_iso = today.isoformat()
        today_orders_row = conn.execute(
            "SELECT COUNT(*) AS cnt, COALESCE(SUM(amount),0) AS rev FROM orders WHERE substr(created_at,1,10) = ? AND COALESCE(payment_mode, 'gateway') != 'test'",
            (today_iso,),
        ).fetchone()
        today_stats = {"orders": today_orders_row["cnt"], "revenue": today_orders_row["rev"], "pending": stats["pending"]}

        # Operational alerts: keep the dashboard actionable rather than purely analytical.
        low_stock = conn.execute(
            """SELECT id, name, quantity FROM products
               WHERE active = 1 AND quantity IS NOT NULL AND quantity <= 5
               ORDER BY quantity ASC, name COLLATE NOCASE ASC LIMIT 8"""
        ).fetchall()
        failed_orders = conn.execute(
            """SELECT COUNT(*) AS cnt FROM orders
               WHERE status IN ('payment_failed', 'delivery_failed', 'refund_failed')"""
        ).fetchone()["cnt"]
        operational_alerts = {
            "low_stock": low_stock,
            "failed_orders": failed_orders,
        }

        # Coupon performance — top 10 by total discount given
        coupon_performance = conn.execute(
            """SELECT c.code,
                      COUNT(cu.id) AS use_count,
                      COALESCE(SUM(cu.discount_amount), 0) AS total_discount,
                      COALESCE(SUM(o.amount), 0) AS net_revenue
               FROM coupon_usage cu
               JOIN coupons c ON c.id = cu.coupon_id
               LEFT JOIN orders o ON o.id = cu.order_id AND o.status IN ('paid', 'delivered')
               GROUP BY cu.coupon_id
               ORDER BY total_discount DESC
               LIMIT 10"""
        ).fetchall()

        return render_template(
            "admin/dashboard.html", stats=stats, recent_orders=recent_orders,
            razorpay_configured=rzp.is_configured(),
            revenue_trend=revenue_trend, top_products=top_products,
            today_stats=today_stats, settings=get_settings(),
            coupon_performance=coupon_performance,
            operational_alerts=operational_alerts,
        )
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


# ============================================================= ADMIN: SITE SETTINGS



@admin_bp.route("/settings", methods=["GET", "POST"])
@login_required
@requires_permission("settings.manage")
def admin_settings():
    if request.method == "POST":
        check_csrf()
        conn = None
        try:
            conn = db.get_db()
            checkbox_keys = {"test_checkout_mode", "auto_deliver_enabled", "auto_email_enabled", "low_stock_alerts", "calendarific_enabled", "disable_payments"}
            # Accept any key sent by the form, but never allow the safe checkout
            # simulator to be enabled unless the deployment explicitly permits it.
            for key in request.form:
                if key == "test_checkout_mode":
                    requested = bool(request.form.get(key))
                    if requested and not config.ALLOW_STORE_TEST_MODE:
                        flash("Testing mode is locked by the deployment. Enable ALLOW_STORE_TEST_MODE only on a staging/test environment.", "warning")
                        value = "false"
                    else:
                        value = "true" if requested else "false"
                elif key in checkbox_keys:
                    value = "true" if request.form.get(key) else "false"
                else:
                    value = request.form.get(key, "")
                conn.execute(
                    "INSERT INTO settings (key, value) VALUES (?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    (key, value),
                )

            # Save seasonal greetings — skip blank/empty rows entirely
            dates = request.form.getlist("greeting_date[]")
            labels = request.form.getlist("greeting_label[]")
            msgs = request.form.getlist("greeting_msg[]")
            # Clear old seasonal greetings first
            conn.execute("DELETE FROM settings WHERE key LIKE 'greeting_%'")
            for dt, lb, msg in zip(dates, labels, msgs):
                dt = dt.strip()
                lb = lb.strip()
                msg = msg.strip()
                # Skip completely blank greeting rows — don't validate empty fields
                if not dt and not lb and not msg:
                    continue
                if dt and lb and dt.isdigit() and len(dt) == 4:
                    conn.execute(
                        "INSERT INTO settings (key, value) VALUES (?, ?)",
                        (f"greeting_{dt}", lb),
                    )
                    if msg:
                        conn.execute(
                            "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                            (f"greeting_msg_{dt}", msg),
                        )
                    else:
                        conn.execute("DELETE FROM settings WHERE key = ?", (f"greeting_msg_{dt}",))
            # Re-fetch Calendarific holidays if enabled
            if config.CALENDARIFIC_API_KEY and request.form.get("calendarific_enabled"):
                fetch_calendarific_holidays(force=True)
            conn.commit()
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass
        invalidate_settings_cache()
        log_admin_action("settings_save", details="Settings updated")
        flash("Your changes have been saved.", "success")
        return redirect(url_for("admin.admin_settings"))

    settings = get_settings()
    # Gather existing seasonal greetings
    conn = None
    try:
        conn = db.get_db()
        greeting_rows = conn.execute(
            "SELECT key, value FROM settings WHERE key LIKE 'greeting_%' ORDER BY key"
        ).fetchall()
        cal_holiday_count = conn.execute(
            "SELECT COUNT(*) c FROM settings WHERE key LIKE 'holiday_%'"
        ).fetchone()["c"]
        cal_last_fetch = conn.execute(
            "SELECT value FROM settings WHERE key = 'calendarific_last_fetch'"
        ).fetchone()
        # Load custom messages for each greeting
        seasonal_greetings = []
        for r in greeting_rows:
            dt = r["key"].replace("greeting_", "")
            msg_row = conn.execute(
                "SELECT value FROM settings WHERE key = ?", (f"greeting_msg_{dt}",)
            ).fetchone()
            seasonal_greetings.append({
                "date": dt,
                "label": r["value"],
                "msg": msg_row["value"] if msg_row else "",
            })
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
    return render_template(
        "admin/settings.html",
        settings=settings,
        seasonal_greetings=seasonal_greetings,
        cal_holiday_count=cal_holiday_count,
        cal_last_fetch_year=cal_last_fetch["value"] if cal_last_fetch else None,
        cal_api_key_set=bool(config.CALENDARIFIC_API_KEY),
        allow_store_test_mode=config.ALLOW_STORE_TEST_MODE,
        test_mode_send_emails=config.TEST_MODE_SEND_EMAILS,
        razorpay_environment=("Test/Sandbox" if config.RAZORPAY_KEY_ID.startswith("rzp_test_") else "Live" if config.RAZORPAY_KEY_ID.startswith("rzp_live_") else "Not configured"),
    )


# ============================================================= ADMIN: SECTIONS



@admin_bp.route("/sections")
@login_required
@requires_permission("content.manage")
def admin_sections():
    conn = db.get_db()
    sections = conn.execute("SELECT * FROM sections ORDER BY position ASC").fetchall()
    conn.close()
    return render_template("admin/sections.html", sections=sections)




@admin_bp.route("/sections/save", methods=["POST"])
@login_required
@requires_permission("content.manage")
def admin_sections_save():
    check_csrf()
    section_id = request.form.get("id")
    title = request.form.get("title", "").strip()
    content = request.form.get("content", "").strip()
    visible = 1 if request.form.get("visible") else 0

    if not title:
        flash("Please give the section a title.", "error")
        return redirect(url_for("admin.admin_sections"))

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
    return redirect(url_for("admin.admin_sections"))




@admin_bp.route("/sections/delete/<int:section_id>", methods=["POST"])
@login_required
@requires_permission("content.manage")
def admin_sections_delete(section_id):
    check_csrf()
    conn = db.get_db()
    conn.execute("DELETE FROM sections WHERE id = ?", (section_id,))
    conn.commit()
    conn.close()
    flash("Section removed.", "success")
    return redirect(url_for("admin.admin_sections"))




@admin_bp.route("/sections/move/<int:section_id>/<direction>", methods=["POST"])
@login_required
@requires_permission("content.manage")
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
    return redirect(url_for("admin.admin_sections"))


# ============================================================= ADMIN: PRODUCTS



@admin_bp.route("/products")
@login_required
@requires_permission("products.edit")
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




@admin_bp.route("/products/new", methods=["GET", "POST"])
@login_required
@requires_permission("products.edit")
def admin_product_new():
    if request.method == "POST":
        check_csrf()
        return _save_product(None)
    categories = _existing_categories()
    return render_template("admin/product_form.html", product=None, images=[], categories=categories)




@admin_bp.route("/products/clone/<int:product_id>", methods=["POST"])
@login_required
@requires_permission("products.edit")
def admin_product_clone(product_id):
    """Duplicate a product with all its images."""
    check_csrf()
    conn = db.get_db()
    orig = conn.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()
    if not orig:
        conn.close()
        abort(404)
    slug_base = slugify(f"{orig['name']}-copy")
    slug = slug_base
    i = 2
    while conn.execute("SELECT 1 FROM products WHERE slug = ?", (slug,)).fetchone():
        slug = f"{slug_base}-{i}"
        i += 1
    max_pos = conn.execute("SELECT COALESCE(MAX(position), -1) m FROM products").fetchone()["m"]
    cur = conn.execute(
        """INSERT INTO products (name, slug, short_description, description, price, category, active,
           position, created_at, delivery_mode, auto_delivery_content, delivery_content_type,
           ribbon, compare_price, quantity, cost_price, min_margin_percent)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (f"{orig['name']} (Copy)", slug, orig["short_description"], orig["description"],
         orig["price"], orig["category"], 0, max_pos + 1, db.now(),
         orig["delivery_mode"], orig["auto_delivery_content"], orig["delivery_content_type"],
         orig["ribbon"], orig["compare_price"], orig["quantity"], orig["cost_price"], orig["min_margin_percent"]),
    )
    new_id = cur.lastrowid
    # Clone images
    images = conn.execute(
        "SELECT filename FROM product_images WHERE product_id = ? ORDER BY position ASC",
        (product_id,),
    ).fetchall()
    for img in images:
        conn.execute(
            "INSERT INTO product_images (product_id, filename, position) VALUES (?, ?, "
            "(SELECT COALESCE(MAX(position), -1) + 1 FROM product_images WHERE product_id = ?))",
            (new_id, img["filename"], new_id),
        )
    conn.commit()
    conn.close()
    flash(f"Product cloned as '{orig['name']} (Copy)'. Edit it below.", "success")
    return redirect(url_for("admin.admin_product_edit", product_id=new_id))




@admin_bp.route("/api/admin/products/<int:product_id>/stock", methods=["PATCH"])
@login_required
@requires_permission("products.edit")
def admin_product_stock_update(product_id):
    """Quick stock update from the admin product list (inline editing)."""
    check_csrf_api()
    data = request.get_json(force=True, silent=True) or {}
    qty_raw = data.get("quantity")
    if qty_raw is None:
        return jsonify({"error": "Missing quantity."}), 400
    try:
        qty = int(float(qty_raw))
        if qty < 0:
            raise ValueError
    except (TypeError, ValueError):
        return jsonify({"error": "Quantity must be a non-negative integer."}), 400
    conn = db.get_db()
    conn.execute("UPDATE products SET quantity = ? WHERE id = ?", (qty, product_id))
    conn.commit()
    # Check if any stock_requests should be notified
    if qty > 0:
        pending = conn.execute(
            "SELECT * FROM stock_requests WHERE product_id = ? AND notified = 0",
            (product_id,),
        ).fetchall()
        for sr in pending:
            product = conn.execute("SELECT name, slug FROM products WHERE id = ?", (product_id,)).fetchone()
            if product and customer_email_notifications_enabled():
                try:
                    site = get_settings().get("site_name", "Virtual Store")
                    product_url = url_for("storefront.product_detail", slug=product["slug"], _external=True)
                    send_email(
                        sr["customer_email"],
                        f"Back in stock: {product['name']}",
                        f"Hi {sr['customer_name'] or 'there'},\n\nGood news — '{product['name']}' is back in stock at {site}!\n\nCheck it out: {product_url}",
                    )
                except Exception:
                    pass
            conn.execute(
                "UPDATE stock_requests SET notified = 1, notified_at = ? WHERE id = ?",
                (db.now(), sr["id"]),
            )
        conn.commit()
    conn.close()
    invalidate_catalog_cache()
    return jsonify({"success": True, "quantity": qty, "notified_count": len(pending) if qty > 0 else 0})




@admin_bp.route("/products/edit/<int:product_id>", methods=["GET", "POST"])
@login_required
@requires_permission("products.edit")
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
    product_files = conn.execute(
        "SELECT * FROM product_files WHERE product_id = ? ORDER BY id ASC", (product_id,)
    ).fetchall()
    conn.close()
    categories = _existing_categories()
    return render_template("admin/product_form.html", product=product, images=images, product_files=product_files, categories=categories)


def _existing_categories():
    conn = db.get_db()
    rows = conn.execute(
        "SELECT DISTINCT category FROM products WHERE category != '' ORDER BY category ASC"
    ).fetchall()
    conn.close()
    return [r["category"] for r in rows]


def _detect_delivery_content_type(delivery_mode, auto_delivery_content, product_files_count):
    """Auto-detect the best delivery_content_type for a product.

    Priority:
      1. Has product_files rows → 'file'
      2. auto_delivery_content starts with http(s):// → 'access_link'
      3. auto_delivery_content is short single-line alphanumeric-with-dashes → 'license_key'
      4. Otherwise → 'instructions'
    """
    if product_files_count > 0:
        return "file"
    if not auto_delivery_content:
        return "instructions"
    content = auto_delivery_content.strip()
    if content.startswith("http://") or content.startswith("https://"):
        return "access_link"
    # Single line, short (≤ 80 chars), mostly alphanumeric with dashes/underscores/dots
    if "\n" not in content and len(content) <= 80:
        alpha_chars = sum(1 for c in content if c.isalnum() or c in "-_.")
        if alpha_chars / max(len(content), 1) > 0.7:
            return "license_key"
    return "instructions"


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
    delivery_content_type = request.form.get("delivery_content_type", "").strip()
    if delivery_content_type not in ("file", "license_key", "access_link", "instructions", ""):
        delivery_content_type = "instructions"
    ribbon = request.form.get("ribbon", "").strip()
    compare_price_raw = request.form.get("compare_price", "").strip()
    quantity_raw = request.form.get("quantity", "0").strip()
    cost_price_raw = request.form.get("cost_price", "0").strip()
    min_margin_raw = request.form.get("min_margin_percent", "15").strip()

    try:
        quantity = int(float(quantity_raw))
        if quantity < 0:
            raise ValueError
    except ValueError:
        flash("Please enter a valid quantity.", "error")
        return redirect(request.referrer or url_for("admin.admin_products"))

    try:
        cost_price = max(0, int(float(cost_price_raw or 0)))
        min_margin_percent = min(100, max(0, int(float(min_margin_raw or 15))))
    except ValueError:
        flash("Please enter valid cost/margin values.", "error")
        return redirect(request.referrer or url_for("admin.admin_products"))

    # Validate compare_price
    compare_price = None
    if compare_price_raw:
        try:
            compare_price = int(float(compare_price_raw))
            if compare_price < 0:
                raise ValueError
        except ValueError:
            flash("Please enter a valid compare-at price.", "error")
            return redirect(request.referrer or url_for("admin.admin_products"))

    if not name:
        flash("Please give the product a name.", "error")
        return redirect(request.referrer or url_for("admin.admin_products"))
    try:
        price = int(float(price_raw))
        if price < 0:
            raise ValueError
    except ValueError:
        flash("Please enter a valid price in rupees.", "error")
        return redirect(request.referrer or url_for("admin.admin_products"))

    conn = db.get_db()
    existing_qty = 0
    if product_id:
        old_product = conn.execute("SELECT quantity FROM products WHERE id=?", (product_id,)).fetchone()
        existing_qty = int(old_product["quantity"] or 0) if old_product else 0
        qty_delta = abs(quantity - existing_qty)
        approval = request_or_validate_approval(conn, action="inventory.adjust", requested_by=int(session["admin_id"]), entity="product", entity_id=int(product_id), amount=qty_delta, reason=f"Inventory change for {name}", metadata={"old_quantity": existing_qty, "new_quantity": quantity, "product_id": int(product_id)}, approval_id=int(request.form.get("approval_id", "0")) if request.form.get("approval_id", "0").isdigit() else None) if qty_delta else {"allowed": True}
        if not approval["allowed"]:
            conn.close()
            flash(f"This inventory adjustment requires second-person approval. Approval #{approval['approval_id']} was created.", "warning")
            return redirect(url_for("admin.admin_product_edit", product_id=product_id, approval_id=approval["approval_id"]))
    # Count current product files for auto-detect
    existing_file_count = conn.execute(
        "SELECT COUNT(*) c FROM product_files WHERE product_id = ?", (product_id,)
    ).fetchone()["c"] if product_id else 0

    if product_id:
        # Auto-detect if no explicit type was sent
        if not delivery_content_type:
            delivery_content_type = _detect_delivery_content_type(
                delivery_mode, auto_delivery_content, existing_file_count
            )
        conn.execute(
            """UPDATE products SET name=?, short_description=?, description=?,
               price=?, category=?, active=?, delivery_mode=?, auto_delivery_content=?,
               delivery_content_type=?, ribbon=?, compare_price=?, quantity=?, cost_price=?, min_margin_percent=? WHERE id=?""",
            (name, short_description, description, price, category, active,
             delivery_mode, auto_delivery_content, delivery_content_type,
             ribbon, compare_price, quantity, cost_price, min_margin_percent, product_id),
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
        if not delivery_content_type:
            delivery_content_type = _detect_delivery_content_type(
                delivery_mode, auto_delivery_content, 0
            )
        cur = conn.execute(
            """INSERT INTO products (name, slug, short_description, description, price,
               category, active, position, created_at, delivery_mode, auto_delivery_content,
               delivery_content_type, ribbon, compare_price, quantity, cost_price, min_margin_percent)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (name, slug, short_description, description, price, category, active, max_pos + 1, db.now(),
             delivery_mode, auto_delivery_content, delivery_content_type, ribbon, compare_price, quantity, cost_price, min_margin_percent),
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

    # Handle PNG thumbnail upload
    png_file = request.files.get("png_thumbnail")
    if png_file and png_file.filename and png_file.filename.lower().endswith(".png"):
        try:
            png_filename = save_product_image(png_file)
            if png_filename:
                conn.execute("UPDATE products SET png_thumbnail = ? WHERE id = ?", (png_filename, product_id))
        except ValueError as e:
            flash(str(e), "error")
    if request.form.get("png_thumbnail_remove"):
        old_png = conn.execute("SELECT png_thumbnail FROM products WHERE id = ?", (product_id,)).fetchone()
        if old_png and old_png[0]:
            delete_file_quietly(old_png[0])
            conn.execute("UPDATE products SET png_thumbnail = '' WHERE id = ?", (product_id,))

    # Handle product file uploads
    product_files = request.files.getlist("product_files")
    for f in product_files:
        if f and f.filename:
            if allowed_product_file(f.filename):
                fsize = 0
                f.seek(0, 2)  # seek to end
                fsize = f.tell()
                f.seek(0)
                if fsize > config.MAX_PRODUCT_FILE_MB * 1024 * 1024:
                    flash(f"Skipped {f.filename}: exceeds {config.MAX_PRODUCT_FILE_MB}MB limit.", "error")
                    continue
                rel_path = save_product_file(f)
                # Check if this original_name already exists — version bump
                existing = conn.execute(
                    "SELECT id, version FROM product_files WHERE product_id = ? AND original_name = ? ORDER BY id DESC LIMIT 1",
                    (product_id, f.filename),
                ).fetchone()
                if existing:
                    new_version = existing["version"] + 1
                    # Update existing row: new file, bumped version
                    conn.execute(
                        """UPDATE product_files SET filename=?, file_size=?, mime_type=?, version=?, created_at=?
                           WHERE id=?""",
                        (rel_path, fsize, f.content_type or "", new_version, db.now(), existing["id"]),
                    )
                else:
                    conn.execute(
                        """INSERT INTO product_files (product_id, filename, original_name, file_size, mime_type, created_at, version)
                           VALUES (?, ?, ?, ?, ?, ?, 1)""",
                        (product_id, rel_path, f.filename, fsize, f.content_type or "", db.now()),
                    )
    # If files were uploaded and the type was never explicitly chosen
    # (still 'instructions'), re-detect: files → 'file'
    if product_files and all(f.filename for f in product_files):
        saved_count = conn.execute(
            "SELECT COUNT(*) c FROM product_files WHERE product_id = ?", (product_id,)
        ).fetchone()["c"]
        if saved_count > 0 and delivery_content_type == "instructions":
            conn.execute(
                "UPDATE products SET delivery_content_type = 'file' WHERE id = ?",
                (product_id,),
            )
    conn.commit()
    conn.close()
    flash("Product saved.", "success")
    return redirect(url_for("admin.admin_product_edit", product_id=product_id))




@admin_bp.route("/products/<int:file_id>/download")
@login_required
@requires_permission("products.edit")
def admin_product_file_download(file_id):
    """Serve a product file for admin download. The admin can use this
    URL to get a direct download link to share with customers."""
    conn = db.get_db()
    f = conn.execute("SELECT * FROM product_files WHERE id = ?", (file_id,)).fetchone()
    conn.close()
    if not f:
        abort(404)
    file_path = product_file_path(f["filename"])
    if not file_path or not os.path.isfile(file_path):
        abort(404)
    return send_file(file_path, as_attachment=True, download_name=f["original_name"])




@admin_bp.route("/products/<int:file_id>/delete", methods=["POST"])
@login_required
@requires_permission("products.edit")
def admin_product_file_delete(file_id):
    check_csrf()
    conn = db.get_db()
    row = conn.execute("SELECT * FROM product_files WHERE id = ?", (file_id,)).fetchone()
    if row:
        delete_file_quietly(row["filename"])
        conn.execute("DELETE FROM product_files WHERE id = ?", (file_id,))
        conn.commit()
    conn.close()
    return redirect(request.referrer or url_for("admin.admin_products"))




@admin_bp.route("/products/move/<int:product_id>/<direction>", methods=["POST"])
@login_required
@requires_permission("products.edit")
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
    return redirect(url_for("admin.admin_products", q=request.args.get("q", ""), category=request.args.get("category", "")))




@admin_bp.route("/products/bulk", methods=["POST"])
@login_required
@requires_permission("products.edit")
def admin_products_bulk():
    check_csrf()
    action = request.form.get("action", "")
    ids = request.form.getlist("product_ids")
    if not ids:
        flash("Select at least one product first.", "error")
        return redirect(url_for("admin.admin_products"))

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
    return redirect(url_for("admin.admin_products"))




@admin_bp.route("/products/delete/<int:product_id>", methods=["POST"])
@login_required
@requires_permission("products.edit")
def admin_product_delete(product_id):
    check_csrf()
    conn = db.get_db()
    images = conn.execute(
        "SELECT filename FROM product_images WHERE product_id = ?", (product_id,)
    ).fetchall()
    for img in images:
        delete_file_quietly(img["filename"])
    product_name = conn.execute("SELECT name FROM products WHERE id = ?", (product_id,)).fetchone()
    product_name = product_name["name"] if product_name else str(product_id)
    conn.execute("DELETE FROM products WHERE id = ?", (product_id,))
    conn.commit()
    conn.close()
    log_admin_action("product_delete", product_name)
    flash("Product deleted.", "success")
    return redirect(url_for("admin.admin_products"))




@admin_bp.route("/products/image/delete/<int:image_id>", methods=["POST"])
@login_required
@requires_permission("products.edit")
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
    return redirect(url_for("admin.admin_product_edit", product_id=product_id))


# ============================================================= ADMIN: ORDERS



@admin_bp.route("/orders")
@login_required
@requires_permission("orders.view")
def admin_orders():
    status_filter = request.args.get("status", "")
    mode_filter = request.args.get("mode", "")
    q = (request.args.get("q") or "").strip()
    conn = db.get_db()
    clauses = []
    params = []
    if status_filter:
        clauses.append("status = ?")
        params.append(status_filter)
    if mode_filter == "test":
        clauses.append("COALESCE(payment_mode, 'gateway') = 'test'")
    elif mode_filter == "gateway":
        clauses.append("COALESCE(payment_mode, 'gateway') != 'test'")
    if q:
        clauses.append("(order_ref LIKE ? OR customer_name LIKE ? OR customer_email LIKE ? OR customer_phone LIKE ?)")
        like = f"%{q}%"
        params += [like, like, like, like]
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    orders = conn.execute(f"SELECT * FROM orders {where} ORDER BY id DESC", params).fetchall()
    conn.close()
    now_iso = datetime.now(timezone.utc).isoformat()
    return render_template("admin/orders.html", orders=orders, status_filter=status_filter, mode_filter=mode_filter, q=q, now_iso=now_iso)




@admin_bp.route("/orders/<int:order_id>")
@login_required
@requires_permission("orders.view")
def admin_order_detail(order_id):
    conn = db.get_db()
    order = conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
    order_items = conn.execute(
        "SELECT * FROM order_items WHERE order_id = ?", (order_id,)
    ).fetchall() if order else []
    # Grab product delivery_content_type for preview rendering
    product = None
    if order and order_items:
        pid = order_items[0]["product_id"]
        product = conn.execute("SELECT id, name, delivery_content_type, delivery_mode FROM products WHERE id = ?", (pid,)).fetchone()
    elif order:
        product = conn.execute("SELECT id, name, delivery_content_type, delivery_mode FROM products WHERE id = ?", (order["product_id"],)).fetchone()
    product_files = []
    if product:
        product_files = conn.execute(
            """SELECT pf.id, pf.original_name, pf.filename,
                      dt.token AS download_token
               FROM product_files pf
               LEFT JOIN download_tokens dt
                 ON dt.order_id = ? AND dt.product_id = pf.product_id
                AND dt.file_path = pf.filename
              WHERE pf.product_id = ?
              ORDER BY pf.id ASC""",
            (order["id"], product["id"]),
        ).fetchall()
    conn.close()
    if not order:
        abort(404)
    return render_template("admin/order_detail.html", order=order, order_items=order_items,
                            email_enabled=email_enabled(), product=product, product_files=product_files)




@admin_bp.route("/orders/<int:order_id>/deliver", methods=["POST"])
@login_required
@requires_permission("orders.edit")
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
    from commerce_workflows import deliver_order_durable
    item_line = order["product_name"] if not order_items else ", ".join(
        f"{it['product_name']} x{it['quantity']}" for it in order_items
    )

    def send_delivery_email():
        if customer_email_notifications_enabled():
            send_email(
                order["customer_email"],
                f"Your order {order['order_ref']} has been delivered",
                f"Hi {order['customer_name']},\n\n"
                f"Great news — your order for \"{item_line}\" is ready!\n\n"
                f"{message}\n\n"
                f"Order reference: {order['order_ref']}\n\nThank you for shopping with us.",
            )

    def send_delivery_sms():
        if order["customer_phone"] and twilio_enabled():
            send_sms(order["customer_phone"], f"Your order {order['order_ref']} is ready! Check your email for details.")

    try:
        result = deliver_order_durable(
            conn, order=order, delivery_message=message,
            notify_email_callable=send_delivery_email if customer_email_notifications_enabled() else None,
            notify_sms_callable=send_delivery_sms if (order["customer_phone"] and twilio_enabled()) else None,
        )
    except Exception as exc:
        conn.close()
        flash(f"Delivery could not be completed safely: {exc}", "error")
        return redirect(url_for("admin.admin_order_detail", order_id=order_id))
    conn.close()
    notified = bool((result.get("context") or {}).get("email_sent") or (result.get("context") or {}).get("sms_sent"))
    flash("Order delivered. Customer has been notified." if notified else
          "Order marked as delivered. Share the details with the customer directly (notifications aren't set up).", "success")
    return redirect(url_for("admin.admin_order_detail", order_id=order_id))




@admin_bp.route("/orders/<int:order_id>/invoice")
@login_required
@requires_permission("orders.view")
def admin_order_invoice(order_id):
    """Generate and download a PDF invoice for a paid or delivered order."""
    conn = db.get_db()
    order = conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
    if not order or order["status"] in ("created", "cancelled"):
        conn.close()
        if not order:
            abort(404)
        flash("Invoice is only available for paid/delivered orders.", "info")
        return redirect(url_for("admin.admin_order_detail", order_id=order_id))
    order_items = conn.execute(
        "SELECT * FROM order_items WHERE order_id = ?", (order_id,),
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




@admin_bp.route("/orders/<int:order_id>/cancel", methods=["POST"])
@login_required
@requires_permission("orders.refund")
def admin_order_cancel(order_id):
    """Cancel an order, optionally refunding through the configured provider."""
    check_csrf()
    refund_amount_raw = request.form.get("refund_amount", "").strip()
    try:
        refund_amt = int(refund_amount_raw) if refund_amount_raw else 0
    except (ValueError, TypeError):
        refund_amt = 0
    refund_amt = max(0, refund_amt)

    conn = db.get_db()
    order = conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
    if not order:
        conn.close()
        abort(404)
    if refund_amt > int(order["amount"]):
        flash("Refund amount cannot exceed the order amount.", "error")
        conn.close()
        return redirect(url_for("admin.admin_order_detail", order_id=order_id))

    approval_id_raw = request.form.get("approval_id", "").strip()
    approval = request_or_validate_approval(
        conn, action="order.refund", requested_by=int(session["admin_id"]), entity="order",
        entity_id=order_id, amount=refund_amt, reason="Admin refund/cancellation",
        metadata={"refund_amount": refund_amt, "order_ref": order["order_ref"]},
        approval_id=int(approval_id_raw) if approval_id_raw.isdigit() else None,
    ) if refund_amt > 0 else {"allowed": True}
    if not approval["allowed"]:
        conn.close()
        flash(f"Refund approval required before this action can proceed. Approval #{approval['approval_id']} was created.", "warning")
        return redirect(url_for("admin.admin_order_detail", order_id=order_id, approval_id=approval["approval_id"]))

    if refund_amt > 0:
        refund_result = initiate_refund(conn, order_id, amount=refund_amt, reason="Admin cancellation")
        if not refund_result.get("success"):
            flash(refund_result.get("error", "Unable to create refund."), "error")
            conn.close()
            return redirect(url_for("admin.admin_order_detail", order_id=order_id))
        processed = process_refund(conn, int(refund_result["refund_id"]))
        if not processed.get("success"):
            flash("Refund could not be completed. The order was not cancelled.", "error")
            conn.close()
            return redirect(url_for("admin.admin_order_detail", order_id=order_id))
        if processed.get("status") != "processed":
            flash("Refund accepted and is awaiting provider confirmation. The order remains open until confirmed.", "info")
            conn.close()
            return redirect(url_for("admin.admin_order_detail", order_id=order_id))

    conn.execute("UPDATE orders SET status = 'cancelled' WHERE id = ?", (order_id,))
    conn.commit()
    conn.close()
    log_admin_action(
        "order_refund" if refund_amt else "order_cancel",
        order["order_ref"],
        f"Refunded ₹{refund_amt:,} through payment provider" if refund_amt else "Order cancelled without refund",
    )
    flash(
        f"Order cancelled and ₹{refund_amt:,} refund submitted successfully." if refund_amt else "Order cancelled.",
        "success",
    )
    return redirect(url_for("admin.admin_order_detail", order_id=order_id))


@admin_bp.route("/orders/bulk-deliver", methods=["POST"])
@login_required
@requires_permission("orders.edit")
def admin_orders_bulk_deliver():
    check_csrf()
    conn = db.get_db()
    orders = conn.execute(
        "SELECT * FROM orders WHERE status = 'paid' ORDER BY id ASC"
    ).fetchall()
    delivered = 0
    for order in orders:
        order_items = conn.execute(
            "SELECT * FROM order_items WHERE order_id = ?", (order["id"],)
        ).fetchall()
        message = _maybe_auto_deliver(conn, order, order_items)
        if message is not None:
            delivered += 1
    conn.commit()
    conn.close()
    flash(f"Delivered {delivered} order{'s' if delivered != 1 else ''} automatically.", "success")
    if delivered < len(orders):
        flash(f"{len(orders) - delivered} order{'s' if (len(orders) - delivered) != 1 else ''} need{'s' if (len(orders) - delivered) == 1 else ''} manual delivery (not digital products).", "warning")
    return redirect(url_for("admin.admin_orders", status="paid"))
  
  
# ============================================================= ADMIN: COUPONS
  


@admin_bp.route("/coupons")
@login_required
@requires_permission("coupons.manage")
def admin_coupons():
    conn = db.get_db()
    coupons = conn.execute("SELECT * FROM coupons ORDER BY id DESC").fetchall()
    products = conn.execute("SELECT id, name FROM products ORDER BY name ASC").fetchall()
    conn.close()
    now_iso = datetime.now(timezone.utc).isoformat()
    return render_template("admin/coupons.html", coupons=coupons, products=products, now_iso=now_iso)




@admin_bp.route("/coupons/save", methods=["POST"])
@login_required
@requires_permission("coupons.manage")
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
    code = (request.form.get("code") or "").strip().upper()
    if not code:
        flash("Please enter a coupon code.", "error")
        return redirect(url_for("admin.admin_coupons"))
    if len(code) > 50:
        flash("Coupon code is too long (max 50 characters).", "error")
        return redirect(url_for("admin.admin_coupons"))
    # Restrict coupon codes to safe characters only (no spaces/symbols that
    # could break the ?coupon=CODE URL-driven flow or be awkward to type/share).
    import re as _re
    if not _re.match(r'^[A-Z0-9_-]+$', code):
        flash("Coupon code may only contain letters, numbers, hyphens, and underscores.", "error")
        return redirect(url_for("admin.admin_coupons"))
    try:
        discount_value = int(discount_value_raw)
        if discount_value <= 0:
            raise ValueError
        if discount_type == "percent" and discount_value > 100:
            raise ValueError
    except ValueError:
        flash("Please enter a valid discount amount.", "error")
        return redirect(url_for("admin.admin_coupons"))

    usage_limit = int(usage_limit_raw) if usage_limit_raw.isdigit() else None
    min_cart_value = int(min_cart_value_raw) if min_cart_value_raw.isdigit() else None
    target_product_id = int(target_product_id_raw) if target_product_id_raw.isdigit() else None

    # If auto_apply is checked, set trigger_type appropriately
    if auto_apply and trigger_type == "manual":
        trigger_type = "cart_threshold"  # sensible default

    conn = db.get_db()
    if target_product_id:
        product_for_margin = conn.execute("SELECT price, cost_price, min_margin_percent FROM products WHERE id=?", (target_product_id,)).fetchone()
        if product_for_margin:
            safe = coupon_discount_with_margin(price=int(product_for_margin["price"] or 0), cost_price=int(product_for_margin["cost_price"] or 0), discount_type=discount_type, discount_value=discount_value, min_margin_percent=int(product_for_margin["min_margin_percent"] or 15))
            if safe["discount"] < (discount_value if discount_type == "flat" else int(round(product_for_margin["price"] * discount_value / 100))):
                flash(f"This promotion would breach the product's minimum margin. Maximum safe discount is ₹{safe['discount']:,}.", "error")
                conn.close()
                return redirect(url_for("admin.admin_coupons"))
    action = "promotion.create.percent" if discount_type == "percent" else "promotion.create.flat"
    approval = request_or_validate_approval(conn, action=action, requested_by=int(session["admin_id"]), entity="coupon", entity_id=0, amount=discount_value, reason=f"Create coupon {code}", metadata={"code": code, "discount_type": discount_type, "discount_value": discount_value, "target_product_id": target_product_id}, approval_id=int(request.form.get("approval_id", "0")) if request.form.get("approval_id", "0").isdigit() else None)
    if not approval["allowed"]:
        conn.close()
        flash(f"This promotion requires a second-person approval. Approval #{approval['approval_id']} was created.", "warning")
        return redirect(url_for("admin.admin_coupons", approval_id=approval["approval_id"]))
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
    return redirect(url_for("admin.admin_coupons"))




@admin_bp.route("/coupons/toggle/<int:coupon_id>", methods=["POST"])
@login_required
@requires_permission("coupons.manage")
def admin_coupons_toggle(coupon_id):
    check_csrf()
    conn = db.get_db()
    conn.execute("UPDATE coupons SET active = 1 - active WHERE id = ?", (coupon_id,))
    conn.commit()
    conn.close()
    return redirect(url_for("admin.admin_coupons"))




@admin_bp.route("/coupons/delete/<int:coupon_id>", methods=["POST"])
@login_required
@requires_permission("coupons.manage")
def admin_coupons_delete(coupon_id):
    check_csrf()
    conn = db.get_db()
    coupon = conn.execute("SELECT id, code, active, usage_limit FROM coupons WHERE id=?", (coupon_id,)).fetchone()
    if not coupon:
        conn.close(); abort(404)
    approval_raw = request.form.get("approval_id", "").strip()
    approval = request_or_validate_approval(
        conn, action="promotion.delete", requested_by=int(session["admin_id"]),
        entity="coupon", entity_id=coupon_id, amount=0,
        reason=f"Delete promotion {coupon['code']}",
        metadata={"code": coupon["code"]},
        approval_id=int(approval_raw) if approval_raw.isdigit() else None,
    )
    if not approval["allowed"]:
        conn.close()
        flash(f"Deleting a promotion requires second-person approval. Approval #{approval['approval_id']} was created.", "warning")
        return redirect(url_for("admin.admin_coupons", approval_id=approval["approval_id"]))
    conn.execute("DELETE FROM coupons WHERE id = ?", (coupon_id,))
    conn.commit(); conn.close()
    log_admin_action("coupon_delete", f"coupon_id={coupon_id}")
    flash("Coupon deleted.", "success")
    return redirect(url_for("admin.admin_coupons"))


# ============================================================= ADMIN: STOCK REQUESTS (waitlist)



@admin_bp.route("/stock-requests")
@login_required
@requires_permission("inventory.manage")
def admin_stock_requests():
    conn = db.get_db()
    requests = conn.execute(
        """SELECT sr.*, p.name AS product_name, p.quantity AS product_qty
           FROM stock_requests sr
           JOIN products p ON sr.product_id = p.id
           ORDER BY sr.notified ASC, sr.created_at DESC"""
    ).fetchall()
    conn.close()
    return render_template("admin/stock_requests.html", requests=requests)




@admin_bp.route("/stock-requests/notify/<int:request_id>", methods=["POST"])
@login_required
@requires_permission("inventory.manage")
def admin_stock_request_notify(request_id):
    check_csrf()
    conn = db.get_db()
    req = conn.execute(
        "SELECT sr.*, p.name AS pname FROM stock_requests sr JOIN products p ON sr.product_id = p.id WHERE sr.id = ?",
        (request_id,),
    ).fetchone()
    if not req:
        conn.close()
        flash("Request not found.", "error")
        return redirect(url_for("admin.admin_stock_requests"))
    if req["notified"]:
        conn.close()
        flash("Already notified.", "info")
        return redirect(url_for("admin.admin_stock_requests"))
    subject = f"{req['pname']} is back in stock!"
    body = f"Hi {req['customer_name'] or 'there'},\n\nGood news — '{req['pname']}' is back in stock at {get_settings().get('site_name', 'our store')}!\n\nCheck it out: {request.url_root}product/{req['product_id']}"
    if customer_email_notifications_enabled():
        send_email(req["customer_email"], subject, body)
    conn.execute("UPDATE stock_requests SET notified = 1, notified_at = ? WHERE id = ?", (db.now(), request_id))
    conn.commit()
    conn.close()
    log_admin_action("stock_notify", f"request_id={request_id}", f"Emailed {req['customer_email']} about {req['pname']}")
    flash(f"Notified {req['customer_email']} that {req['pname']} is back.", "success")
    return redirect(url_for("admin.admin_stock_requests"))




@admin_bp.route("/stock-requests/notify-all/<int:product_id>", methods=["POST"])
@login_required
@requires_permission("inventory.manage")
def admin_stock_request_notify_all(product_id):
    check_csrf()
    conn = db.get_db()
    product = conn.execute("SELECT id, name FROM products WHERE id = ?", (product_id,)).fetchone()
    if not product:
        conn.close()
        flash("Product not found.", "error")
        return redirect(url_for("admin.admin_stock_requests"))
    unotified = conn.execute(
        "SELECT * FROM stock_requests WHERE product_id = ? AND notified = 0", (product_id,)
    ).fetchall()
    site_name = get_settings().get("site_name", "Virtual Store")
    product_url = request.url_root + f"product/{product_id}"
    count = 0
    for req in unotified:
        subject = f"{product['name']} is back in stock!"
        body = f"Hi {req['customer_name'] or 'there'},\n\nGood news — '{product['name']}' is back in stock at {site_name or 'our store'}!\n\nCheck it out: {product_url}"
        if customer_email_notifications_enabled():
            send_email(req["customer_email"], subject, body)
        conn.execute("UPDATE stock_requests SET notified = 1, notified_at = ? WHERE id = ?", (db.now(), req["id"]))
        count += 1
    conn.commit()
    conn.close()
    log_admin_action("stock_notify_all", f"product_id={product_id}", f"Emailed {count} customers about {product['name']}")
    flash(f"Notified {count} customer{'s' if count != 1 else ''} that {product['name']} is back.", "success")
    return redirect(url_for("admin.admin_stock_requests"))


# ============================================================= ADMIN: TESTIMONIALS



@admin_bp.route("/testimonials")
@login_required
@requires_permission("content.manage")
def admin_testimonials():
    conn = db.get_db()
    testimonials = conn.execute("SELECT * FROM testimonials ORDER BY position ASC").fetchall()
    conn.close()
    return render_template("admin/testimonials.html", testimonials=testimonials)




@admin_bp.route("/testimonials/save", methods=["POST"])
@login_required
@requires_permission("content.manage")
def admin_testimonials_save():
    check_csrf()
    testimonial_id = request.form.get("id")
    customer_name = request.form.get("customer_name", "").strip()
    quote = request.form.get("quote", "").strip()
    rating = request.form.get("rating", "5")
    visible = 1 if request.form.get("visible") else 0

    if not customer_name or not quote:
        flash("Please fill in both a name and a quote.", "error")
        return redirect(url_for("admin.admin_testimonials"))

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
    return redirect(url_for("admin.admin_testimonials"))




@admin_bp.route("/testimonials/delete/<int:testimonial_id>", methods=["POST"])
@login_required
@requires_permission("content.manage")
def admin_testimonials_delete(testimonial_id):
    check_csrf()
    conn = db.get_db()
    t_row = conn.execute("SELECT customer_name FROM testimonials WHERE id = ?", (testimonial_id,)).fetchone()
    t_name = t_row["customer_name"] if t_row else str(testimonial_id)
    conn.execute("DELETE FROM testimonials WHERE id = ?", (testimonial_id,))
    conn.commit()
    conn.close()
    log_admin_action("testimonial_delete", t_name)
    flash("Testimonial removed.", "success")
    return redirect(url_for("admin.admin_testimonials"))


# ============================================================= ADMIN: FAQS



@admin_bp.route("/faqs")
@login_required
@requires_permission("content.manage")
def admin_faqs():
    conn = db.get_db()
    faqs = conn.execute("SELECT * FROM faqs ORDER BY position ASC").fetchall()
    conn.close()
    return render_template("admin/faqs.html", faqs=faqs)




@admin_bp.route("/faqs/save", methods=["POST"])
@login_required
@requires_permission("content.manage")
def admin_faqs_save():
    check_csrf()
    faq_id = request.form.get("id")
    question = request.form.get("question", "").strip()
    answer = request.form.get("answer", "").strip()
    visible = 1 if request.form.get("visible") else 0

    if not question or not answer:
        flash("Please fill in both the question and the answer.", "error")
        return redirect(url_for("admin.admin_faqs"))

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
    return redirect(url_for("admin.admin_faqs"))




@admin_bp.route("/faqs/delete/<int:faq_id>", methods=["POST"])
@login_required
@requires_permission("content.manage")
def admin_faqs_delete(faq_id):
    check_csrf()
    conn = db.get_db()
    f_row = conn.execute("SELECT question FROM faqs WHERE id = ?", (faq_id,)).fetchone()
    f_q = f_row["question"] if f_row else str(faq_id)
    conn.execute("DELETE FROM faqs WHERE id = ?", (faq_id,))
    conn.commit()
    conn.close()
    log_admin_action("faq_delete", f_q[:80])
    flash("FAQ removed.", "success")
    return redirect(url_for("admin.admin_faqs"))


# ============================================================= ADMIN: NEWSLETTER



@admin_bp.route("/newsletter")
@login_required
@requires_permission("newsletter.view", "marketing.manage")
def admin_newsletter():
    conn = db.get_db()
    subscribers = conn.execute(
        "SELECT * FROM newsletter_subscribers ORDER BY id DESC"
    ).fetchall()
    conn.close()
    return render_template("admin/newsletter.html", subscribers=subscribers)


def _csv_safe(value):
    """Sanitize a cell value against CSV/formula injection. If the value
    starts with =, +, -, @, tab, or CR, prefix it with a single quote so
    Excel/Sheets treats it as text rather than a formula (CWE-1236)."""
    if value is None:
        return ""
    s = str(value)
    if s and s[0] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + s
    return s




@admin_bp.route("/newsletter/export.csv")
@login_required
@requires_permission("newsletter.view", "marketing.manage")
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
        writer.writerow([_csv_safe(s["email"]), _csv_safe(s["created_at"])])
    return buf.getvalue(), 200, {
        "Content-Type": "text/csv",
        "Content-Disposition": "attachment; filename=newsletter_subscribers.csv",
    }


# ============================================================= ADMIN: ORDERS EXPORT



@admin_bp.route("/orders/export.csv")
@login_required
@requires_permission("orders.export")
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
            _csv_safe(o["order_ref"]), _csv_safe(item_summary), _csv_safe(o["customer_name"]), _csv_safe(o["customer_email"]),
            _csv_safe(o["customer_phone"]), o["amount"], _csv_safe(o["coupon_code"]), o["discount_amount"],
            o["status"], _csv_safe(o["created_at"]), _csv_safe(o["paid_at"]), _csv_safe(o["delivered_at"]),
        ])
    return buf.getvalue(), 200, {
        "Content-Type": "text/csv",
        "Content-Disposition": "attachment; filename=orders.csv",
    }


# ============================================================= ADMIN: INTELLIGENCE


@admin_bp.route("/insights")
@login_required
@requires_permission("orders.view")
def admin_insights():
    """Read-only intelligence console. No commerce mutation is exposed here."""
    days = request.args.get("days", 30, type=int) or 30
    days = max(7, min(days, 365))
    insights = get_business_insights(days)
    anomalies = detect_anomalies(days)
    forecast = inventory_forecast(14)
    at_risk = [x for x in forecast if x["risk"] != "healthy"]
    return render_template(
        "admin/insights.html",
        insights=insights, anomalies=anomalies, forecast=forecast,
        at_risk=at_risk, days=days, settings=get_settings(),
    )


@admin_bp.route("/api/intelligence/ask", methods=["POST"])
@login_required
@requires_permission("orders.view")
def admin_intelligence_ask():
    check_csrf_api()
    data = request.get_json(silent=True) or {}
    question = str(data.get("question") or "").strip()[:500]
    if not question:
        return jsonify({"error": "Ask a question about sales, orders, products, stock, failures, or anomalies."}), 400
    return jsonify(assistant_answer(question))


# ============================================================= ADMIN: ACCOUNT



@admin_bp.route("/account", methods=["GET", "POST"])
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
            # Rotate the session after password change so any other active
            # admin sessions (e.g. from a compromised credential) are forced
            # to re-authenticate.
            session.clear()
            session["admin_id"] = user["id"]
            session["admin_username"] = user["username"]
            flash("Password updated. For security, your session has been refreshed.", "success")
        conn.close()
        return redirect(url_for("admin.admin_account"))
    return render_template("admin/account.html")


# ============================================================= ADMIN: CUSTOMERS


@admin_bp.route("/customers")
@login_required
@requires_permission("orders.view")
def admin_customers():
    """Customer operations view derived from the canonical customers table and orders.

    This is intentionally read-only: support/order staff can inspect customer history
    without getting write access to customer authentication data.
    """
    conn = db.get_db()
    q = (request.args.get("q") or "").strip()
    page = max(1, request.args.get("page", 1, type=int) or 1)
    per_page = 25
    where = []
    params = []
    if q:
        like = f"%{q}%"
        where.append("(c.name LIKE ? OR c.email LIKE ? OR c.phone LIKE ?)")
        params.extend([like, like, like])
    where_sql = (" WHERE " + " AND ".join(where)) if where else ""
    total = conn.execute(f"SELECT COUNT(*) AS c FROM customers c{where_sql}", params).fetchone()["c"]
    total_pages = max(1, (total + per_page - 1) // per_page)
    page = min(page, total_pages)
    offset = (page - 1) * per_page

    rows = conn.execute(
        f"""
        SELECT c.id, c.name, c.email, c.phone, c.created_at,
               COUNT(DISTINCT o.id) AS order_count,
               COALESCE(SUM(CASE WHEN o.status IN ('paid','delivered') AND COALESCE(o.payment_mode, 'gateway') != 'test' THEN o.amount ELSE 0 END), 0) AS lifetime_value,
               MAX(o.created_at) AS last_order_at
        FROM customers c
        LEFT JOIN orders o ON o.customer_id = c.id
        {where_sql}
        GROUP BY c.id
        ORDER BY COALESCE(MAX(o.created_at), c.created_at) DESC, c.id DESC
        LIMIT ? OFFSET ?
        """, params + [per_page, offset]
    ).fetchall()
    conn.close()
    return render_template(
        "admin/customers.html",
        customers=rows, q=q, page=page, total_pages=total_pages, total=total,
    )


# ============================================================= ADMIN: AUDIT LOG



@admin_bp.route("/audit-log")
@login_required
@requires_permission("audit.view")
def admin_audit_log():
    conn = db.get_db()
    page = request.args.get("page", 1, type=int)
    per_page = 50
    action_filter = (request.args.get("action") or "").strip()
    from_date = (request.args.get("from") or "").strip()
    to_date = (request.args.get("to") or "").strip()

    where_clauses = []
    params = []

    if action_filter:
        where_clauses.append("aal.action = ?")
        params.append(action_filter)
    if from_date:
        where_clauses.append("aal.created_at >= ?")
        params.append(from_date)
    if to_date:
        where_clauses.append("aal.created_at <= ?")
        params.append(to_date)

    where_sql = ""
    if where_clauses:
        where_sql = "WHERE " + " AND ".join(where_clauses)

    offset = (page - 1) * per_page

    # Count
    total_row = conn.execute(
        f"SELECT COUNT(*) AS c FROM admin_audit_log aal {where_sql}", params
    ).fetchone()
    total = total_row["c"] if total_row else 0

    # Fetch entries
    fetch_params = params + [per_page, offset]
    entries = conn.execute(
        f"""SELECT aal.*, au.username
            FROM admin_audit_log aal
            LEFT JOIN admin_users au ON aal.admin_id = au.id
            {where_sql}
            ORDER BY aal.created_at DESC LIMIT ? OFFSET ?""",
        fetch_params,
    ).fetchall()

    # Distinct actions for filter dropdown
    actions = conn.execute(
        "SELECT DISTINCT action FROM admin_audit_log ORDER BY action ASC"
    ).fetchall()

    conn.close()

    total_pages = max(1, (total + per_page - 1) // per_page)

    return render_template(
        "admin/audit_log.html",
        entries=entries,
        page=page,
        total_pages=total_pages,
        total=total,
        action_filter=action_filter,
        from_date=from_date,
        to_date=to_date,
        actions=[r["action"] for r in actions],
    )




@admin_bp.route("/audit-log/export")
@login_required
@requires_permission("audit.export")
def admin_audit_log_export():
    import csv
    import io

    conn = db.get_db()
    action_filter = (request.args.get("action") or "").strip()
    from_date = (request.args.get("from") or "").strip()
    to_date = (request.args.get("to") or "").strip()

    where_clauses = []
    params = []

    if action_filter:
        where_clauses.append("aal.action = ?")
        params.append(action_filter)
    if from_date:
        where_clauses.append("aal.created_at >= ?")
        params.append(from_date)
    if to_date:
        where_clauses.append("aal.created_at <= ?")
        params.append(to_date)

    where_sql = ""
    if where_clauses:
        where_sql = "WHERE " + " AND ".join(where_clauses)

    entries = conn.execute(
        f"""SELECT aal.*, au.username
            FROM admin_audit_log aal
            LEFT JOIN admin_users au ON aal.admin_id = au.id
            {where_sql}
            ORDER BY aal.created_at DESC""",
        params,
    ).fetchall()
    conn.close()

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["ID", "Admin", "Action", "Target", "Details", "Timestamp"])
    for e in entries:
        writer.writerow([
            e["id"],
            _csv_safe(e["username"] or "unknown"),
            _csv_safe(e["action"]),
            _csv_safe(e["target"]),
            _csv_safe(e["details"]),
            _csv_safe(e["created_at"]),
        ])

    return buf.getvalue(), 200, {
        "Content-Type": "text/csv",
        "Content-Disposition": "attachment; filename=audit-log.csv",
    }


# ============================================================= ADMIN: TICKETS



@admin_bp.route("/tickets")
@login_required
@requires_permission("tickets.create")
def admin_tickets():
    """List admin tickets. Master sees all; sub-admins see only their own."""
    conn = db.get_db()
    admin_id = session.get("admin_id")
    admin_perms = session.get("admin_permissions", [])
    is_master = "*" in admin_perms

    current_role = session.get("admin_role", "custom")
    if is_master:
        tickets = conn.execute(
            """SELECT t.*, au.username AS creator_name, target.username AS target_username
               FROM admin_tickets t
               LEFT JOIN admin_users au ON t.admin_id = au.id
               LEFT JOIN admin_users target ON t.target_admin_id = target.id
               ORDER BY t.created_at DESC"""
        ).fetchall()
    else:
        tickets = conn.execute(
            """SELECT t.*, au.username AS creator_name, target.username AS target_username
               FROM admin_tickets t
               LEFT JOIN admin_users au ON t.admin_id = au.id
               LEFT JOIN admin_users target ON t.target_admin_id = target.id
               WHERE t.scope_type='global'
                  OR t.admin_id = ?
                  OR t.target_admin_id = ?
                  OR (t.scope_type='role' AND t.target_role = ?)
               ORDER BY t.created_at DESC""",
            (admin_id, admin_id, current_role),
        ).fetchall()

    # Load replies for all tickets
    ticket_ids = [t["id"] for t in tickets]
    replies_map = {}
    if ticket_ids:
        placeholders = ",".join("?" * len(ticket_ids))
        replies = conn.execute(
            f"""SELECT r.*, au.username AS replier_name
                FROM admin_ticket_replies r
                LEFT JOIN admin_users au ON r.admin_id = au.id
                WHERE r.ticket_id IN ({placeholders})
                ORDER BY r.created_at ASC""",
            ticket_ids,
        ).fetchall()
        for r in replies:
            replies_map.setdefault(r["ticket_id"], []).append(dict(r))

    team_admins = conn.execute("SELECT id, username, role FROM admin_users WHERE is_active=1 ORDER BY username").fetchall()
    conn.close()
    return render_template("admin/tickets.html", tickets=tickets, is_master=is_master, replies_map=replies_map, team_admins=team_admins)




@admin_bp.route("/tickets/new", methods=["POST"])
@login_required
@requires_permission("tickets.create")
def admin_tickets_new():
    """Create a global, role-focused or employee-focused internal ticket."""
    check_csrf()
    admin_id = session.get("admin_id")
    category = (request.form.get("category") or "other").strip()
    title = (request.form.get("title") or "").strip()
    description = (request.form.get("description") or "").strip()
    scope_type = (request.form.get("scope_type") or "private").strip()
    target_role = (request.form.get("target_role") or "").strip()
    try:
        target_admin_id = int(request.form.get("target_admin_id")) if request.form.get("target_admin_id") else None
    except ValueError:
        target_admin_id = None
    if scope_type not in {"private", "global", "role", "employee"}: scope_type = "private"
    if scope_type == "employee" and not target_admin_id:
        flash("Choose an employee for an employee-focused ticket.", "error"); return redirect(url_for("admin.admin_tickets"))
    if scope_type == "role" and not target_role:
        flash("Choose a staff role for a staff-focused ticket.", "error"); return redirect(url_for("admin.admin_tickets"))

    valid_categories = {"feature_request", "bug_report", "content_update", "permission_request", "general", "other"}
    if category not in valid_categories:
        category = "other"

    if not title or not description:
        flash("Please fill in both title and description.", "error")
        return redirect(url_for("admin.admin_tickets"))

    conn = db.get_db()
    conn.execute(
        "INSERT INTO admin_tickets (admin_id, category, title, description, status, created_at, scope_type, target_role, target_admin_id) VALUES (?, ?, ?, ?, 'open', ?, ?, ?, ?)",
        (admin_id, category, title, description, db.now(), scope_type, target_role, target_admin_id),
    )
    conn.commit()
    conn.close()
    log_admin_action("ticket_created", f"category={category}", f"Title: {title[:80]}")
    flash("Ticket submitted successfully. Master admin has been notified (if connected).", "success")
    return redirect(url_for("admin.admin_tickets"))




@admin_bp.route("/tickets/<int:ticket_id>/status", methods=["POST"])
@login_required
@requires_permission("tickets.create")
def admin_tickets_status(ticket_id):
    """Update ticket status and add admin note (master only)."""
    check_csrf()
    admin_perms = session.get("admin_permissions", [])
    if "*" not in admin_perms:
        flash("Only the master admin can update ticket status.", "error")
        return redirect(url_for("admin.admin_tickets"))

    conn = db.get_db()
    ticket = conn.execute("SELECT * FROM admin_tickets WHERE id = ?", (ticket_id,)).fetchone()
    if not ticket:
        conn.close()
        abort(404)

    status = (request.form.get("status") or "").strip()
    admin_note = (request.form.get("admin_note") or "").strip()

    valid_statuses = {"open", "in_progress", "resolved", "closed"}
    if status not in valid_statuses:
        conn.close()
        flash("Invalid status.", "error")
        return redirect(url_for("admin.admin_tickets"))

    resolved_at = None
    if status in ("resolved", "closed") and ticket["status"] not in ("resolved", "closed"):
        resolved_at = db.now()

    conn.execute(
        "UPDATE admin_tickets SET status = ?, admin_note = ?, resolved_at = COALESCE(?, resolved_at) WHERE id = ?",
        (status, admin_note, resolved_at, ticket_id),
    )
    conn.commit()
    conn.close()
    log_admin_action("ticket_status", f"ticket_id={ticket_id}", f"Status: {status}, note: {admin_note[:100]}")
    flash(f"Ticket #{ticket_id} updated to '{status}'.", "success")
    return redirect(url_for("admin.admin_tickets"))




@admin_bp.route("/tickets/<int:ticket_id>/reply", methods=["POST"])
@login_required
@requires_permission("tickets.create")
def admin_tickets_reply(ticket_id):
    """Add a reply to a ticket. Both master and sub-admins can reply."""
    check_csrf()
    admin_id = session.get("admin_id")
    reply_text = (request.form.get("reply_text") or "").strip()
    if not reply_text:
        flash("Reply cannot be empty.", "error")
        return redirect(url_for("admin.admin_tickets"))
    
    conn = db.get_db()
    ticket = conn.execute("SELECT * FROM admin_tickets WHERE id = ?", (ticket_id,)).fetchone()
    if not ticket:
        conn.close()
        abort(404)
    
    # Handle file attachment
    attachment_name = ""
    attachment_path = ""
    attachment_file = request.files.get("attachment")
    if attachment_file and attachment_file.filename:
        try:
            filename = save_product_image(attachment_file)
            if filename:
                attachment_name = attachment_file.filename
                attachment_path = filename
        except ValueError as e:
            flash(str(e), "error")
    
    # If ticket is resolved/closed, reopen
    if ticket["status"] in ("resolved", "closed"):
        conn.execute("UPDATE admin_tickets SET status = 'in_progress' WHERE id = ?", (ticket_id,))
    
    conn.execute(
        "INSERT INTO admin_ticket_replies (ticket_id, admin_id, reply_text, attachment_name, attachment_path, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (ticket_id, admin_id, reply_text, attachment_name, attachment_path, db.now()),
    )
    conn.commit()
    conn.close()
    log_admin_action("ticket_reply", f"ticket_id={ticket_id}", f"Reply: {reply_text[:100]}")
    flash("Reply added.", "success")
    return redirect(url_for("admin.admin_tickets"))



# ============================================================= ADMIN: TEAM HUB / NOTICES

@admin_bp.route("/team-hub", methods=["GET", "POST"])
@login_required
@requires_permission("team.chat")
def admin_team_hub():
    admin_id = int(session["admin_id"])
    conn = db.get_db()
    try:
        db.ensure_round29_schema(conn)
    except Exception:
        pass
    if request.method == "POST":
        check_csrf()
        kind = (request.form.get("kind") or "global").strip()
        body = (request.form.get("body") or "").strip()
        selected_conversation = request.form.get("conversation_id", type=int)
        try:
            if selected_conversation:
                cid = selected_conversation
                if not team_messages(conn, cid, admin_id) and not conn.execute("SELECT id FROM team_conversations WHERE id=?", (cid,)).fetchone():
                    raise ValueError("Conversation not found.")
                add_message(conn, cid, admin_id, body)
            elif kind == "global":
                cid = get_or_create_global(conn, admin_id); add_message(conn, cid, admin_id, body)
            elif kind == "direct":
                target = int(request.form.get("target_admin_id"))
                if target == admin_id: raise ValueError("You cannot start a direct chat with yourself.")
                cid = get_or_create_direct(conn, admin_id, target); add_message(conn, cid, admin_id, body)
            else:
                flash("Unsupported conversation type.", "error")
                conn.close(); return redirect(url_for("admin.admin_team_hub"))
            flash("Message sent to the team.", "success")
        except Exception as exc:
            conn.rollback(); flash(str(exc), "error")
        conn.close(); return redirect(url_for("admin.admin_team_hub", conversation=cid if 'cid' in locals() else None))
    conversations = visible_conversations(conn, admin_id)
    if not conversations:
        get_or_create_global(conn, admin_id); conversations = visible_conversations(conn, admin_id)
    selected_id = request.args.get("conversation", type=int) or int(conversations[0]["id"])
    selected = next((c for c in conversations if int(c["id"]) == selected_id), conversations[0])
    msgs = team_messages(conn, int(selected["id"]), admin_id)
    if msgs: mark_read(conn, int(selected["id"]), admin_id, int(msgs[-1]["id"]))
    admins = conn.execute("SELECT id, username, role FROM admin_users WHERE is_active=1 AND id<>? ORDER BY username", (admin_id,)).fetchall()
    presence_rows = list_presence(conn, [int(a["id"]) for a in admins]) if admins else []
    unread_notifications = list_notifications(conn, admin_id, unread_only=True, limit=50)
    conn.close()
    presence = {int(r["admin_id"]): dict(r) for r in presence_rows}
    return render_template(
        "admin/team_hub.html", conversations=conversations, selected=selected, messages=msgs,
        admins=admins, presence=presence, unread_notifications=unread_notifications,
    )

@admin_bp.route("/team-hub/context")
@login_required
def admin_team_hub_context():
    context_type = (request.args.get("type") or "").strip().lower()
    context_id = request.args.get("id", type=int)
    if not context_id or context_type not in {"order", "customer", "ticket", "exception"}:
        abort(400)
    required = {"order": "orders.view", "customer": "customers.view", "ticket": "tickets.view", "exception": "audit.view"}[context_type]
    if not has_permission(required):
        abort(403)
    conn = db.get_db()
    try: db.ensure_round29_schema(conn)
    except Exception: pass
    title = request.args.get("title") or f"{context_type.title()} #{context_id}"
    cid = get_or_create_context(conn, context_type=context_type, context_id=context_id, created_by=int(session["admin_id"]), title=title)
    conn.close()
    return redirect(url_for("admin.admin_team_hub", conversation=cid))

@admin_bp.route("/team-hub/poll/<int:conversation_id>")
@login_required
@requires_permission("team.chat")
def admin_team_hub_poll(conversation_id):
    conn = db.get_db(); msgs = team_messages(conn, conversation_id, int(session["admin_id"])); conn.close()
    return jsonify([dict(m) for m in msgs[-100:]])

@admin_bp.route("/team-hub/search")
@login_required
@requires_permission("team.chat")
def admin_team_hub_search():
    conn = db.get_db(); q=(request.args.get("q") or "").strip(); cid=request.args.get("conversation", type=int)
    rows = search_messages(conn, cid, int(session["admin_id"]), q) if cid and q else []
    conn.close(); return jsonify([dict(r) for r in rows])

@admin_bp.route("/team-hub/pin", methods=["POST"])
@login_required
@requires_permission("team.chat")
def admin_team_hub_pin():
    check_csrf(); conn=db.get_db(); ok=pin_message(conn, int(request.form.get("message_id", 0)), int(session["admin_id"])); conn.close()
    return jsonify({"ok": ok})

@admin_bp.route("/team-hub/notifications")
@login_required
@requires_permission("team.chat")
def admin_team_hub_notifications():
    conn=db.get_db(); rows=list_notifications(conn, int(session["admin_id"]), unread_only=True); conn.close()
    return jsonify([dict(r) for r in rows])

@admin_bp.route("/team-hub/notifications/read", methods=["POST"])
@login_required
@requires_permission("team.chat")
def admin_team_hub_notifications_read():
    check_csrf(); conn=db.get_db(); mark_notifications_read(conn, int(session["admin_id"]), request.json.get("ids") if request.is_json else None); conn.close(); return jsonify({"ok": True})

@admin_bp.route("/team-hub/presence", methods=["POST"])
@login_required
@requires_permission("team.chat")
def admin_team_hub_presence():
    state=(request.json or {}).get("state","online") if request.is_json else request.form.get("state","online")
    conn=db.get_db(); set_presence(conn, int(session["admin_id"]), state); conn.close(); return jsonify({"ok": True, "state": state})

@admin_bp.route("/team-hub/presence", methods=["GET"])
@login_required
@requires_permission("team.chat")
def admin_team_hub_presence_get():
    conn=db.get_db(); rows=list_presence(conn); conn.close()
    return jsonify([dict(r) for r in rows])



@admin_bp.route("/team-hub/reply", methods=["POST"])
@login_required
@requires_permission("team.chat")
def admin_team_hub_reply():
    check_csrf()
    payload = request.get_json(silent=True) or request.form
    cid = int(payload.get("conversation_id", 0)); parent_id = int(payload.get("parent_message_id", 0)); body = str(payload.get("body", ""))
    conn = db.get_db()
    try:
        mid = reply_to_message(conn, cid, int(session["admin_id"]), parent_id, body)
        return jsonify({"ok": True, "message_id": mid})
    except Exception as exc:
        conn.rollback(); return jsonify({"ok": False, "error": str(exc)}), 400
    finally:
        conn.close()


@admin_bp.route("/team-hub/reaction", methods=["POST"])
@login_required
@requires_permission("team.chat")
def admin_team_hub_reaction():
    check_csrf()
    payload = request.get_json(silent=True) or request.form
    conn = db.get_db()
    try:
        result = toggle_reaction(conn, int(payload.get("message_id", 0)), int(session["admin_id"]), str(payload.get("reaction", "")))
        return jsonify({"ok": True, **result})
    except Exception as exc:
        conn.rollback(); return jsonify({"ok": False, "error": str(exc)}), 400
    finally:
        conn.close()


@admin_bp.route("/team-hub/reactions", methods=["GET"])
@login_required
@requires_permission("team.chat")
def admin_team_hub_reactions():
    conn = db.get_db()
    ids = [int(x) for x in request.args.getlist("message_id") if str(x).isdigit()]
    rows = list_message_reactions(conn, ids)
    conn.close()
    return jsonify(rows)

@admin_bp.route("/support")
@login_required
@requires_permission("customers.view")
def admin_support_cockpit():
    """Unified support cockpit: customer, order, ticket and team context in one view."""
    q = (request.args.get("q") or "").strip()
    customer_id = request.args.get("customer_id", type=int)
    order_id = request.args.get("order_id", type=int)
    conn = db.get_db()
    selected_customer = None
    customer_orders = []
    customer_tickets = []
    customer_context_conversations = []
    search_results = []
    try:
        if q:
            like = f"%{q}%"
            search_results = conn.execute(
                "SELECT id, name, email, phone, created_at FROM customers "
                "WHERE name LIKE ? OR email LIKE ? OR phone LIKE ? "
                "ORDER BY created_at DESC LIMIT 25",
                (like, like, like),
            ).fetchall()
        if customer_id:
            selected_customer = conn.execute(
                "SELECT id, name, email, phone, created_at FROM customers WHERE id=?",
                (customer_id,),
            ).fetchone()
            if selected_customer:
                customer_orders = conn.execute(
                    "SELECT id, order_ref, product_name, amount, status, payment_state, order_state, created_at "
                    "FROM orders WHERE customer_email=? OR customer_phone=? ORDER BY created_at DESC LIMIT 50",
                    (selected_customer["email"], selected_customer["phone"]),
                ).fetchall()
                customer_tickets = conn.execute(
                    "SELECT id, title, status, created_at, updated_at FROM admin_tickets "
                    "WHERE title LIKE ? OR description LIKE ? ORDER BY created_at DESC LIMIT 50",
                    (f"%{selected_customer['email']}%", f"%{selected_customer['email']}%"),
                ).fetchall()
                customer_context_conversations = conn.execute(
                    "SELECT c.id, c.title, c.updated_at, "
                    "(SELECT body FROM team_messages m WHERE m.conversation_id=c.id ORDER BY m.id DESC LIMIT 1) AS last_message "
                    "FROM team_conversations c WHERE c.kind='context' AND c.context_type='customer' AND c.context_id=? "
                    "ORDER BY c.updated_at DESC LIMIT 20",
                    (int(customer_id),),
                ).fetchall()
        selected_order = None
        if order_id:
            selected_order = conn.execute(
                "SELECT id, order_ref, product_name, amount, status, payment_state, order_state, "
                "customer_name, customer_email, customer_phone, created_at, paid_at, delivered_at, refunded_amount "
                "FROM orders WHERE id=?", (order_id,)
            ).fetchone()
            if selected_order and not selected_customer:
                selected_customer = conn.execute(
                    "SELECT id, name, email, phone, created_at FROM customers WHERE email=? ORDER BY id DESC LIMIT 1",
                    (selected_order["customer_email"],),
                ).fetchone()
                if selected_customer:
                    customer_orders = conn.execute(
                        "SELECT id, order_ref, product_name, amount, status, payment_state, order_state, created_at "
                        "FROM orders WHERE customer_email=? OR customer_phone=? ORDER BY created_at DESC LIMIT 50",
                        (selected_customer["email"], selected_customer["phone"]),
                    ).fetchall()
        else:
            selected_order = None
    finally:
        conn.close()
    return render_template(
        "admin/support_cockpit.html",
        q=q, search_results=search_results, selected_customer=selected_customer,
        customer_orders=customer_orders, customer_tickets=customer_tickets, customer_context_conversations=customer_context_conversations, selected_order=selected_order,
    )

@admin_bp.route("/approvals", methods=["GET"])
@login_required
@requires_permission("audit.view")
def admin_approvals():
    conn=db.get_db(); expire_pending_approvals(conn)
    rows=conn.execute("SELECT a.*, au.username AS requester FROM admin_approval_requests a LEFT JOIN admin_users au ON au.id=a.requested_by ORDER BY CASE a.status WHEN 'pending' THEN 0 ELSE 1 END, a.created_at DESC LIMIT 200").fetchall()
    enriched=[]
    for row in rows:
        item=dict(row); item["steps"]= [dict(r) for r in conn.execute("SELECT step_index,status,approved_by,note,approved_at FROM approval_steps WHERE approval_id=? ORDER BY step_index", (int(row["id"]),)).fetchall()]
        enriched.append(item)
    policies=conn.execute("SELECT action,threshold_amount,require_two_person,required_approvals,approval_expiry_minutes,enabled,version,updated_at FROM high_risk_action_policies ORDER BY action").fetchall()
    conn.close(); return render_template("admin/approvals.html", approvals=enriched, policies=policies)

@admin_bp.route("/approvals/policy", methods=["POST"])
@login_required
@requires_permission("governance.approve")
def admin_approval_policy_update():
    check_csrf()
    action=(request.form.get("action") or "").strip()
    if not action:
        abort(400)
    threshold=max(0, request.form.get("threshold_amount", type=int) or 0)
    required=max(1, min(5, request.form.get("required_approvals", type=int) or 1))
    expiry=max(1, min(10080, request.form.get("approval_expiry_minutes", type=int) or 1440))
    enabled=1 if request.form.get("enabled") == "1" else 0
    now=db.now(); conn=db.get_db()
    existing=conn.execute("SELECT version FROM high_risk_action_policies WHERE action=?", (action,)).fetchone()
    version=max(1, int(existing[0] if existing else 0)+1)
    conn.execute("""INSERT INTO high_risk_action_policies(action,threshold_amount,require_two_person,required_approvals,approval_expiry_minutes,enabled,version,updated_at)
                   VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(action) DO UPDATE SET threshold_amount=excluded.threshold_amount,require_two_person=excluded.require_two_person,required_approvals=excluded.required_approvals,approval_expiry_minutes=excluded.approval_expiry_minutes,enabled=excluded.enabled,version=excluded.version,updated_at=excluded.updated_at""", (action,threshold,1 if required>0 else 0,required,expiry,enabled,version,now))
    try:
        from backend_kernel import publish_event
        publish_event(conn, topic="governance.approval_policy.updated", aggregate="approval_policy", aggregate_id=action, payload={"action":action,"threshold_amount":threshold,"required_approvals":required,"approval_expiry_minutes":expiry,"enabled":bool(enabled),"version":version,"updated_by":int(session["admin_id"])})
    except Exception:
        pass
    conn.commit(); conn.close(); flash("Approval policy updated.", "success")
    return redirect(url_for("admin.admin_approvals"))

@admin_bp.route("/approvals/<int:approval_id>/approve", methods=["POST"])
@login_required
@requires_permission("governance.approve")
def admin_approval_approve(approval_id):
    check_csrf(); conn=db.get_db()
    success=approve_governance(conn, approval_id, approved_by=int(session["admin_id"]), note=(request.form.get("note") or "").strip())
    conn.close(); flash("Approval recorded." if success else "Approval rejected by policy, expiry or state.", "success" if success else "error")
    return redirect(url_for("admin.admin_approvals"))

@admin_bp.route("/approvals/<int:approval_id>/reject", methods=["POST"])
@login_required
@requires_permission("governance.approve")
def admin_approval_reject(approval_id):
    check_csrf(); conn=db.get_db()
    success=reject_governance(conn, approval_id, rejected_by=int(session["admin_id"]), note=(request.form.get("note") or "").strip())
    conn.close(); flash("Approval rejected." if success else "Approval could not be rejected.", "success" if success else "error")
    return redirect(url_for("admin.admin_approvals"))

@admin_bp.route("/institutional-memory", methods=["GET", "POST"])
@login_required
@requires_permission("analytics.view")
def admin_institutional_memory():
    admin_id=int(session["admin_id"]); conn=db.get_db()
    if request.method == "POST":
        check_csrf(); decision_id=request.form.get("decision_id", type=int)
        if decision_id:
            try:
                record_decision_outcome(conn, decision_id=decision_id, outcome=request.form.get("outcome", ""), lesson=request.form.get("lesson", ""), future_recommendation=request.form.get("future_recommendation", ""), reviewed_by=admin_id, effectiveness=request.form.get("effectiveness", "inconclusive"), effectiveness_score=request.form.get("effectiveness_score", type=int))
                flash("Decision outcome recorded.", "success")
            except ValueError as exc:
                flash(str(exc), "error")
            conn.close(); return redirect(url_for("admin.admin_institutional_memory"))
    q=(request.args.get("q") or "").strip(); source_type=(request.args.get("source_type") or "").strip()
    rows=search_memory(conn, q, 100, source_type=source_type)
    decisions=conn.execute("SELECT * FROM decision_journal ORDER BY CASE WHEN outcome='' THEN 0 ELSE 1 END, created_at DESC LIMIT 100").fetchall()
    selected_id=request.args.get("decision_id", type=int)
    selected_history=decision_review_history(conn, selected_id) if selected_id else []
    selected_related=related_memory(conn, "decision", selected_id, limit=12) if selected_id else []
    source_types=memory_source_types(conn)
    effectiveness=decision_effectiveness_report(conn)
    conn.close(); return render_template("admin/institutional_memory.html", rows=rows, decisions=decisions, q=q, source_type=source_type, source_types=source_types, effectiveness=effectiveness, selected_id=selected_id, selected_history=selected_history, selected_related=selected_related)

@admin_bp.route("/analytics")
@login_required
@requires_permission("analytics.view")
def admin_analytics():
    conn=db.get_db()
    days=request.args.get("days",30,type=int)
    steps_raw=(request.args.get("funnel") or "view_product,add_to_cart,checkout_started,payment_succeeded").split(",")
    steps=[x.strip() for x in steps_raw if x.strip()]
    experiment_id=request.args.get("experiment_id", type=int)
    result=analytics_overview(conn, days=days, funnel_steps=steps, experiment_id=experiment_id)
    conn.close()
    return render_template("admin/analytics.html", result=result, days=days, funnel_steps=steps, experiment_id=experiment_id)

@admin_bp.route("/feature-flags", methods=["GET", "POST"])
@login_required
@requires_permission("settings.manage")
def admin_feature_flags():
    conn=db.get_db()
    if request.method == "POST":
        check_csrf(); upsert_feature_flag(conn, key=request.form.get("key",""), description=request.form.get("description",""), enabled=request.form.get("enabled") == "1", rollout_percent=request.form.get("rollout_percent",0), updated_by=int(session["admin_id"]))
        flash("Feature flag saved.", "success"); conn.close(); return redirect(url_for("admin.admin_feature_flags"))
    rows=conn.execute("SELECT * FROM feature_flags ORDER BY key").fetchall(); conn.close(); return render_template("admin/feature_flags.html", flags=rows)

@admin_bp.route("/experiments", methods=["GET", "POST"])
@login_required
@requires_permission("analytics.view")
def admin_experiments():
    conn=db.get_db()
    if request.method == "POST":
        check_csrf(); action=request.form.get("action","save")
        try:
            if action == "conclude":
                conclude_experiment(conn, experiment_id=request.form.get("experiment_id", type=int), concluded_by=int(session["admin_id"]), conclusion=request.form.get("conclusion",""), require_guardrails=True)
                flash("Experiment concluded safely.", "success")
            else:
                variants=[v.strip() for v in (request.form.get("variants") or "").split(",") if v.strip()]
                allocation={v:int(request.form.get("alloc_"+v, 0) or 0) for v in variants}
                create_or_update_experiment(conn, key=request.form.get("key",""), name=request.form.get("name",""), variants=variants, allocation=allocation, primary_metric=request.form.get("primary_metric",""), status=request.form.get("status","draft"), created_by=int(session["admin_id"]))
                flash("Experiment saved.", "success")
        except ValueError as exc:
            flash(str(exc), "error")
        conn.close(); return redirect(url_for("admin.admin_experiments"))
    rows=conn.execute("SELECT * FROM experiments ORDER BY updated_at DESC").fetchall(); conn.close(); return render_template("admin/experiments.html", experiments=rows)

@admin_bp.route("/notices", methods=["GET", "POST"])
@login_required
@requires_permission("content.manage")
def admin_notices():
    conn = db.get_db()
    if request.method == "POST":
        check_csrf()
        action = request.form.get("action", "create")
        if action == "toggle":
            nid = int(request.form.get("id")); conn.execute("UPDATE site_notices SET enabled=1-enabled, updated_at=? WHERE id=?", (db.now(), nid)); conn.commit(); flash("Notice visibility updated.", "success")
        elif action == "delete":
            nid = int(request.form.get("id")); conn.execute("DELETE FROM site_notices WHERE id=?", (nid,)); conn.commit(); flash("Notice removed.", "success")
        else:
            title=(request.form.get("title") or "").strip(); body=(request.form.get("body") or "").strip(); kind=(request.form.get("kind") or "info").strip()
            try: priority=int(request.form.get("priority",0))
            except ValueError: priority=0
            if not title or not body: flash("Title and message are required.", "error")
            else:
                conn.execute("INSERT INTO site_notices (title,body,kind,enabled,priority,starts_at,ends_at,created_by,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)", (title,body,kind,1,priority,request.form.get("starts_at") or None,request.form.get("ends_at") or None,int(session["admin_id"]),db.now(),db.now())); conn.commit(); flash("Notice published to the storefront.", "success")
        conn.close(); return redirect(url_for("admin.admin_notices"))
    notices=conn.execute("SELECT n.*, a.username AS creator_name FROM site_notices n LEFT JOIN admin_users a ON a.id=n.created_by ORDER BY n.priority DESC,n.created_at DESC").fetchall(); conn.close()
    return render_template("admin/notices.html", notices=notices)

# ============================================================= ADMIN: TEAM MANAGEMENT (master only)



@admin_bp.route("/team", methods=["GET"])
@login_required
@requires_permission("admin.manage")
def admin_team():
    """List all admin users as cards. Only master (admin.manage) can access."""
    conn = db.get_db()
    admins = conn.execute(
        "SELECT id, username, role, permissions, is_active, created_at FROM admin_users ORDER BY id ASC"
    ).fetchall()
    conn.close()
    new_creds = request.args.get("_new_creds")
    if new_creds:
        try:
            new_creds = json.loads(bytes.fromhex(new_creds).decode())
        except Exception:
            new_creds = None
    return render_template("admin/team.html", admins=admins, new_creds=new_creds)




@admin_bp.route("/team/add", methods=["POST"])
@login_required
@requires_permission("admin.manage")
def admin_team_add():
    """Create a new admin user with a preset or custom role."""
    check_csrf()
    username = (request.form.get("username") or "").strip()
    password = request.form.get("password", "") or request.form.get("manual_password", "")
    role_preset = request.form.get("role_preset", "custom")

    if not username or len(password) < 8:
        flash("Username is required and password must be at least 8 characters.", "error")
        return redirect(url_for("admin.admin_team"))

    if role_preset in PRESET_PERMISSIONS:
        permissions = json.dumps(PRESET_PERMISSIONS[role_preset])
        role_name = role_preset
    elif role_preset == "custom":
        try:
            raw = request.form.get("custom_permissions", "[]")
            perms_list = json.loads(raw)
            if not isinstance(perms_list, list):
                raise ValueError
            permissions = json.dumps(perms_list)
        except (ValueError, TypeError):
            flash("Invalid permissions JSON. Enter a valid list like [\"orders.view\", \"products.edit\"].", "error")
            return redirect(url_for("admin.admin_team"))
        role_name = "custom"
    else:
        flash("Unknown role preset.", "error")
        return redirect(url_for("admin.admin_team"))

    conn = db.get_db()
    try:
        conn.execute(
            "INSERT INTO admin_users (username, password_hash, role, permissions, is_active, created_at) VALUES (?, ?, ?, ?, 1, ?)",
            (username, generate_password_hash(password), role_name, permissions, db.now()),
        )
        conn.commit()
        log_admin_action("admin_add", username, f"Role: {role_name}")
        flash(f"Admin '{username}' created with role '{role_name}'.", "success")
        # Pass generated password to template via query param
        encoded = json.dumps({"username": username, "password": password}).encode().hex()
        return redirect(url_for("admin.admin_team", _new_creds=encoded))
    except Exception:
        flash(f"Username '{username}' already exists.", "error")
    finally:
        conn.close()
    return redirect(url_for("admin.admin_team"))




@admin_bp.route("/team/edit/<int:admin_id>", methods=["POST"])
@login_required
@requires_permission("admin.manage")
def admin_team_edit(admin_id):
    """Change role/permissions/active status of a sub-admin. Cannot edit master."""
    check_csrf()
    conn = db.get_db()
    target = conn.execute("SELECT * FROM admin_users WHERE id = ?", (admin_id,)).fetchone()
    if not target:
        conn.close()
        flash("Admin not found.", "error")
        return redirect(url_for("admin.admin_team"))
    if target["role"] == "master":
        conn.close()
        flash("Cannot edit the master admin.", "error")
        return redirect(url_for("admin.admin_team"))

    role_preset = request.form.get("role_preset", "custom")
    is_active = 1 if request.form.get("is_active") else 0

    if role_preset in PRESET_PERMISSIONS:
        permissions = json.dumps(PRESET_PERMISSIONS[role_preset])
        role_name = role_preset
    elif role_preset == "custom":
        try:
            raw = request.form.get("custom_permissions", "[]")
            perms_list = json.loads(raw)
            if not isinstance(perms_list, list):
                raise ValueError
            permissions = json.dumps(perms_list)
        except (ValueError, TypeError):
            conn.close()
            flash("Invalid permissions JSON.", "error")
            return redirect(url_for("admin.admin_team"))
        role_name = "custom"
    else:
        role_name = target["role"]
        permissions = target["permissions"]

    conn.execute(
        "UPDATE admin_users SET role = ?, permissions = ?, is_active = ? WHERE id = ?",
        (role_name, permissions, is_active, admin_id),
    )
    conn.commit()
    conn.close()
    log_admin_action("admin_edit", target["username"], f"Role: {role_name}, active: {is_active}")
    flash(f"Admin '{target['username']}' updated.", "success")
    return redirect(url_for("admin.admin_team"))




@admin_bp.route("/team/toggle/<int:admin_id>", methods=["POST"])
@login_required
@requires_permission("admin.manage")
def admin_team_toggle(admin_id):
    """Suspend/unsuspend a sub-admin. Cannot toggle master."""
    check_csrf()
    conn = db.get_db()
    target = conn.execute("SELECT * FROM admin_users WHERE id = ?", (admin_id,)).fetchone()
    if not target:
        conn.close()
        flash("Admin not found.", "error")
        return redirect(url_for("admin.admin_team"))
    if target["role"] == "master":
        conn.close()
        flash("Cannot suspend the master admin.", "error")
        return redirect(url_for("admin.admin_team"))

    new_status = 1 - target["is_active"]
    conn.execute("UPDATE admin_users SET is_active = ? WHERE id = ?", (new_status, admin_id))
    conn.commit()
    conn.close()
    label = "activated" if new_status else "suspended"
    log_admin_action("admin_toggle", target["username"], f"New status: {label}")
    flash(f"Admin '{target['username']}' {label}.", "success")
    return redirect(url_for("admin.admin_team"))


# ============================================================= WEBHOOKS / REPORTING




# ============================================================= ADMIN: TITAN OPERATIONS CONSOLE

@admin_bp.route("/guardian")
@login_required
@requires_permission("audit.view")
def admin_guardian():
    from governance_service import run_guardian_scan
    conn = db.get_db()

    # Never trust migration bookkeeping for a control-plane page. Repair the
    # live schema first, then verify it before executing any Guardian query.
    db.ensure_business_exception_columns(conn)
    if not db.guardian_schema_ready(conn):
        # A remote libSQL/Turso connection can briefly retain stale schema
        # metadata after additive DDL. Re-open the worker connection once and
        # perform the repair again instead of exposing a 500 to the operator.
        try:
            reset = getattr(db, "reset_turso_connection", None)
            if reset:
                reset()
            conn = db.get_db()
            db.ensure_business_exception_columns(conn)
        except Exception:
            current_app.logger.exception("Guardian schema repair retry failed")

    if not db.guardian_schema_ready(conn):
        conn.close()
        flash("Guardian is temporarily unavailable because its database schema could not be repaired. Check the deployment logs.", "error")
        return redirect(url_for("admin.admin_dashboard"))

    result = run_guardian_scan(conn)
    from governance_service import guardian_cross_signal_summary, guardian_health
    cross_signal = guardian_cross_signal_summary(conn)
    health = guardian_health(conn)
    exceptions = conn.execute(
        """SELECT e.*, a.username AS assignee
           FROM business_exceptions e LEFT JOIN admin_users a ON a.id=e.assigned_to
           WHERE e.status IN ('open','acknowledged')
           ORDER BY CASE e.severity WHEN 'critical' THEN 0 WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END,
                    COALESCE(e.due_at, e.created_at) ASC LIMIT 200"""
    ).fetchall()
    assignees = conn.execute("SELECT id, username, role FROM admin_users WHERE is_active=1 ORDER BY username").fetchall()
    from governance_service import ensure_guardian_mastery_schema
    ensure_guardian_mastery_schema(conn)
    sla_policies = conn.execute("SELECT * FROM guardian_sla_policies ORDER BY CASE severity WHEN 'critical' THEN 0 WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END").fetchall()
    conn.close()
    return render_template("admin/guardian.html", result=result, health=health, cross_signal=cross_signal, exceptions=exceptions, assignees=assignees, sla_policies=sla_policies)


@admin_bp.route("/guardian/exception/<int:exception_id>/assign", methods=["POST"])
@login_required
@requires_permission("audit.view")
def admin_guardian_assign(exception_id):
    check_csrf()
    conn=db.get_db()
    assignee=(request.form.get("assigned_to") or "").strip()
    due_at=(request.form.get("due_at") or "").strip() or None
    if assignee:
        try:
            admin_id=int(assignee)
        except ValueError:
            admin_id=0
    else:
        admin_id=0
    from governance_service import assign_exception, notify_exception_assignee
    success=assign_exception(conn, exception_id, assigned_to=admin_id, due_at=due_at)
    if success and admin_id:
        notify_exception_assignee(conn, exception_id, kind="guardian_assigned")
    conn.close()
    flash("Exception assignment updated." if success else "Exception assignment failed.", "success" if success else "error")
    return redirect(url_for("admin.admin_guardian"))


@admin_bp.route("/guardian/exception/<int:exception_id>/resolve", methods=["POST"])
@login_required
@requires_permission("audit.view")
def admin_guardian_resolve(exception_id):
    check_csrf()
    from governance_service import resolve_exception
    resolution = (request.form.get("resolution") or "Resolved by operations").strip()
    conn = db.get_db()
    success = resolve_exception(conn, exception_id, resolved_by=int(session["admin_id"]), resolution=resolution)
    if success:
        try:
            from governance_service import notify_exception_assignee
            notify_exception_assignee(conn, exception_id, kind="guardian_resolved")
        except Exception:
            pass
    conn.close()
    flash("Exception resolved." if success else "Exception could not be resolved.", "success" if success else "error")
    return redirect(url_for("admin.admin_guardian"))

@admin_bp.route("/guardian/exception/<int:exception_id>/acknowledge", methods=["POST"])
@login_required
@requires_permission("audit.view")
def admin_guardian_acknowledge(exception_id):
    check_csrf()
    from governance_service import acknowledge_exception, notify_exception_assignee
    conn = db.get_db(); admin_id = int(session["admin_id"])
    success = acknowledge_exception(conn, exception_id, admin_id=admin_id)
    if success:
        notify_exception_assignee(conn, exception_id, kind="guardian_acknowledged")
    conn.close()
    flash("Exception acknowledged." if success else "Exception could not be acknowledged.", "success" if success else "error")
    return redirect(url_for("admin.admin_guardian"))


@admin_bp.route("/guardian/exception/<int:exception_id>/reopen", methods=["POST"])
@login_required
@requires_permission("audit.view")
def admin_guardian_reopen(exception_id):
    check_csrf()
    from governance_service import reopen_exception, notify_exception_assignee
    conn = db.get_db(); admin_id = int(session["admin_id"])
    success = reopen_exception(conn, exception_id, admin_id=admin_id, reason=(request.form.get("reason") or "").strip())
    if success:
        notify_exception_assignee(conn, exception_id, kind="guardian_reopened")
    conn.close()
    flash("Exception reopened." if success else "Exception could not be reopened.", "success" if success else "error")
    return redirect(url_for("admin.admin_guardian"))


@admin_bp.route("/guardian/sla-policy", methods=["POST"])
@login_required
@requires_permission("audit.manage")
def admin_guardian_sla_policy():
    check_csrf()
    severity=(request.form.get("severity") or "").strip().lower()
    if severity not in {"critical","high","medium","low"}:
        abort(400)
    due=max(1, request.form.get("due_minutes", type=int) or 1)
    grace=max(0, request.form.get("escalation_grace_minutes", type=int) or 0)
    enabled=1 if request.form.get("enabled") == "1" else 0
    notify_assignee=1 if request.form.get("notify_assignee") == "1" else 0
    notify_admins=1 if request.form.get("notify_admins") == "1" else 0
    conn=db.get_db()
    from governance_service import ensure_guardian_mastery_schema
    ensure_guardian_mastery_schema(conn)
    conn.execute("UPDATE guardian_sla_policies SET due_minutes=?, escalation_grace_minutes=?, notify_assignee=?, notify_admins=?, enabled=?, updated_at=? WHERE severity=?", (due,grace,notify_assignee,notify_admins,enabled,db.now(),severity))
    conn.commit(); conn.close()
    flash("Guardian SLA policy updated.", "success")
    return redirect(url_for("admin.admin_guardian"))

@admin_bp.route("/guardian/scan", methods=["POST"])
@login_required
@requires_permission("audit.view")
def admin_guardian_scan():
    check_csrf()
    from governance_service import run_guardian_scan
    conn = db.get_db(); result = run_guardian_scan(conn); conn.close()
    flash(f"Guardian scan complete — {result.get('open_exceptions', 0)} open exceptions.", "success")
    return redirect(url_for("admin.admin_guardian"))


@admin_bp.route("/guardian/health")
@login_required
@requires_permission("audit.view")
def admin_guardian_health():
    from governance_service import guardian_health
    conn = db.get_db()
    try:
        report = guardian_health(conn)
    finally:
        conn.close()
    return jsonify(report), (200 if report.get("ok") else 503)


@admin_bp.route("/guardian/detectors")
@login_required
@requires_permission("audit.view")
def admin_guardian_detectors():
    from governance_service import guardian_detectors
    conn=db.get_db(); rows=guardian_detectors(conn); conn.close()
    return render_template("admin/guardian_detectors.html", detectors=rows)

@admin_bp.route("/guardian/exception/<int:exception_id>/timeline")
@login_required
@requires_permission("audit.view")
def admin_guardian_exception_timeline(exception_id):
    from governance_service import exception_timeline
    conn=db.get_db(); rows=exception_timeline(conn, exception_id); conn.close()
    return jsonify({"exception_id": exception_id, "events": [dict(r) for r in rows]})

@admin_bp.route("/events")
@login_required
@requires_permission("audit.view")
def admin_events():
    from backend_kernel import list_domain_events, retryable_event_deliveries, dead_letter_event_deliveries
    conn=db.get_db()
    topic=(request.args.get("topic") or "").strip()
    aggregate=(request.args.get("aggregate") or "").strip()
    events=list_domain_events(conn, topic=topic, aggregate=aggregate, limit=200)
    retryable=retryable_event_deliveries(conn, limit=100)
    dead_letters=dead_letter_event_deliveries(conn, limit=100)
    conn.close()
    return render_template("admin/events.html", events=events, retryable=retryable, dead_letters=dead_letters, topic=topic, aggregate=aggregate)


@admin_bp.route("/observability/policies", methods=["GET", "POST"])
@login_required
@requires_permission("settings.manage")
def admin_observability_policies():
    import observability_service
    conn=db.get_db()
    if request.method == "POST":
        check_csrf()
        observability_service.set_alert_policy(conn, alert_type=request.form.get("alert_type", ""), enabled=request.form.get("enabled") == "1", severity=request.form.get("severity", "medium"), threshold_ms=request.form.get("threshold_ms", type=int), cooldown_minutes=request.form.get("cooldown_minutes", 10, type=int), notify_admins=request.form.get("notify_admins") == "1")
        flash("Alert policy updated.", "success"); conn.close(); return redirect(url_for("admin.admin_observability_policies"))
    rows=observability_service.alert_policies(conn); conn.close()
    return render_template("admin/observability_policies.html", policies=rows)

@admin_bp.route("/observability/slo", methods=["GET", "POST"])
@login_required
@requires_permission("audit.view")
def admin_observability_slo():
    import observability_service
    conn = db.get_db()
    if request.method == "POST":
        check_csrf()
        observability_service.upsert_slo_policy(
            conn,
            key=(request.form.get("key") or "").strip(),
            name=(request.form.get("name") or "").strip(),
            operation_pattern=(request.form.get("operation_pattern") or "").strip(),
            target_percent=float(request.form.get("target_percent") or 99),
            window_hours=int(request.form.get("window_hours") or 24),
            max_latency_ms=(float(request.form.get("max_latency_ms")) if request.form.get("max_latency_ms") else None),
            enabled=request.form.get("enabled") == "1",
        )
        flash("SLO policy saved.", "success")
        conn.close()
        return redirect(url_for("admin.admin_observability_slo"))
    policies = observability_service.slo_policies(conn)
    reports = [observability_service.slo_report(conn, key=p["key"]) for p in policies]
    conn.close()
    return render_template("admin/observability_slo.html", policies=policies, reports=[r for r in reports if r])

@admin_bp.route("/observability")
@login_required
@requires_permission("audit.view")
def admin_observability():
    """Correlated request/workflow observability without a heavyweight tracing stack."""
    import observability_service
    conn = db.get_db(); db.ensure_round25_schema(conn)
    trace_id = (request.args.get("trace") or "").strip()
    operation = (request.args.get("operation") or "").strip()
    spans = observability_service.recent_spans(conn, trace_id=trace_id or None, operation=operation or None, limit=250)
    summary = observability_service.trace_summary(conn, trace_id) if trace_id else None
    alerts = observability_service.recent_alerts(conn, status=(request.args.get("alert_status") or "open"), limit=100)
    conn.close()
    return render_template("admin/observability.html", spans=spans, trace_id=trace_id, summary=summary, alerts=alerts)


@admin_bp.route("/observability/alert/<int:alert_id>/resolve", methods=["POST"])
@login_required
@requires_permission("audit.view")
def admin_observability_resolve_alert(alert_id):
    check_csrf()
    import observability_service
    conn=db.get_db(); success=observability_service.resolve_alert(conn, alert_id, admin_id=int(session["admin_id"])); conn.close()
    flash("Alert resolved." if success else "Alert could not be resolved.", "success" if success else "error")
    return redirect(url_for("admin.admin_observability"))




@admin_bp.route("/workflows")
@login_required
@requires_permission("orders.view")
def admin_workflows():
    conn = db.get_db()
    rows = conn.execute(
        "SELECT workflow_id, workflow_type, aggregate_type, aggregate_id, status, current_step, attempt_count, compensation_status, error, created_at, updated_at, completed_at FROM workflow_runs ORDER BY updated_at DESC LIMIT 200"
    ).fetchall()
    steps_by={}
    for row in rows:
        steps_by[row["workflow_id"]]=conn.execute(
            "SELECT step_index, step_name, status, error, started_at, completed_at FROM workflow_steps WHERE workflow_id=? ORDER BY step_index",
            (row["workflow_id"],)
        ).fetchall()
    conn.close()
    return render_template("admin/workflows.html", workflows=rows, steps_by=steps_by)


@admin_bp.route("/workflows/<workflow_id>/recover", methods=["POST"])
@login_required
@requires_permission("orders.edit")
def admin_workflow_recover(workflow_id):
    check_csrf()
    from workflow_recovery import recover_known_workflow
    from .storefront import _confirm_order_payment
    conn=db.get_db()
    try:
        result=recover_known_workflow(conn, workflow_id, confirm_callable=_confirm_order_payment)
        conn.close()
        log_admin_action("workflow.recover", "workflow", workflow_id, {"result_status": result.get("status") if isinstance(result,dict) else "completed"})
        flash("Workflow recovery completed or resumed safely.", "success")
    except Exception as exc:
        conn.close()
        flash(f"Workflow recovery refused: {exc}", "error")
    return redirect(url_for("admin.admin_workflows"))


@admin_bp.route("/reconciliation", methods=["GET", "POST"])
@login_required
@requires_permission("audit.view")
def admin_reconciliation():
    from reconcile_razorpay import reconcile
    conn = db.get_db(); result = None
    if request.method == "POST":
        check_csrf()
        try:
            result = reconcile(created_by=int(session["admin_id"]), mode="admin")
            flash("Provider reconciliation completed.", "success")
        except Exception as exc:
            flash(f"Reconciliation failed: {exc}", "error")
    from reconcile_razorpay import get_open_items
    runs = conn.execute("SELECT id, provider, mode, status, started_at, completed_at, scanned_orders, repaired_orders, mismatches, summary_json FROM reconciliation_runs ORDER BY started_at DESC LIMIT 50").fetchall()
    open_items = get_open_items(limit=100)
    conn.close()
    return render_template("admin/reconciliation.html", runs=runs, result=result, open_items=open_items)


@admin_bp.route("/reconciliation/items/<int:item_id>/resolve", methods=["POST"])
@login_required
@requires_permission("audit.view")
def admin_reconciliation_resolve(item_id):
    check_csrf()
    from reconcile_razorpay import resolve_item
    resolution = (request.form.get("resolution") or "Resolved after manual review").strip()
    resolution_code = (request.form.get("resolution_code") or "manual_review").strip()
    if not resolution:
        flash("Resolution note is required.", "error")
        return redirect(url_for("admin.admin_reconciliation"))
    if resolve_item(item_id, resolved_by=int(session["admin_id"]), resolution=resolution, resolution_code=resolution_code):
        log_admin_action("reconciliation.item.resolve", "reconciliation_item", item_id, {"resolution": resolution[:500]})
        flash("Reconciliation discrepancy marked resolved.", "success")
    else:
        flash("Reconciliation discrepancy was already resolved or no longer exists.", "error")
    return redirect(url_for("admin.admin_reconciliation"))


@admin_bp.route("/simulation-lab", methods=["GET", "POST"])
@login_required
@requires_permission("analytics.view")
def admin_simulation_lab():
    conn=db.get_db(); ensure_operations_lab_schema(conn)
    result=None; report=None
    if request.method=="POST":
        check_csrf(); scenario=(request.form.get("scenario") or "payment_outage").strip()
        try:
            from governance_service import simulate_scenario
            result=simulate_scenario(conn,admin_id=int(session["admin_id"]),scenario=scenario,scale=int(request.form.get("scale","100")))
            report=simulation_report(conn,int(result["id"]))
            flash("Simulation completed. No real orders, payments, or inventory were changed.","success")
        except (ValueError,TypeError) as exc: flash(str(exc),"error")
    runs=conn.execute("SELECT id,label,parameters_json,results_json,status,created_at FROM simulation_runs ORDER BY created_at DESC LIMIT 30").fetchall()
    catalog=simulation_catalog(conn); conn.close()
    return render_template("admin/simulation_lab.html",result=result,report=report,runs=runs,catalog=catalog,scenarios=[c["key"] for c in catalog])

@admin_bp.route("/simulation-lab/runs/<int:run_id>", methods=["GET"])
@login_required
@requires_permission("analytics.view")
def admin_simulation_report(run_id):
    conn=db.get_db(); report=simulation_report(conn,run_id); conn.close()
    if not report: abort(404)
    return jsonify(report)

@admin_bp.route("/training", methods=["GET", "POST"])
@login_required
@requires_permission("tickets.create")
def admin_training():
    conn=db.get_db(); ensure_operations_lab_schema(conn)
    scenarios=[
      {"id":"double_charge","title":"Customer reports a double charge","prompt":"A customer says their card was charged twice but only one order appears in the store.","best":["check provider payment IDs","reconcile","do not refund blindly"]},
      {"id":"late_delivery","title":"Delivery is late","prompt":"An order has passed its expected delivery window. What is the first operational step?","best":["check fulfillment","check latest delivery event","give customer concrete status"]},
      {"id":"negative_stock","title":"Inventory goes negative","prompt":"The dashboard reports negative inventory for a product. What should happen next?","best":["stop uncontrolled selling","investigate reservation/commit history","record exception"]},
      {"id":"refund_request","title":"Large refund request","prompt":"A customer requests a very large refund. What should happen before money moves?","best":["verify order and provider state","follow approval policy","do not duplicate refund"]},
    ]
    score=None; attempt=None
    if request.method=="POST":
        check_csrf(); sid=request.form.get("scenario") or "double_charge"; answer=(request.form.get("answer") or "").strip(); rubric=next((x for x in scenarios if x["id"]==sid),None)
        if rubric:
            score=round(sum(1 for k in rubric["best"] if k in answer.lower())/len(rubric["best"])*100)
            mapping={"double_charge":"payment_outage","late_delivery":"stockout","negative_stock":"stockout","refund_request":"refund_surge"}
            attempt=record_training_attempt(conn,admin_id=int(session["admin_id"]),scenario_key=mapping.get(sid,"payment_outage"),answer=answer)
            flash(f"Training exercise scored {score}/100. Attempt #{attempt['id']} recorded.","success")
    recent=conn.execute("SELECT t.*,a.username admin_username FROM training_attempts t LEFT JOIN admin_users a ON a.id=t.admin_id ORDER BY t.created_at DESC LIMIT 50").fetchall()
    summary=training_report(conn,admin_id=int(session["admin_id"])); conn.close()
    return render_template("admin/training.html",scenarios=scenarios,score=score,attempt=attempt,recent=recent,summary=summary)

@admin_bp.route("/training/report", methods=["GET"])
@login_required
@requires_permission("tickets.create")
def admin_training_report():
    conn=db.get_db(); report=training_report(conn); conn.close(); return jsonify(report)

@admin_bp.route("/customers/<int:customer_id>/timeline", methods=["GET", "POST"])
@login_required
@requires_permission("orders.view")
def admin_customer_timeline(customer_id):
    from governance_service import customer_timeline, recommend_recovery_playbooks, recovery_action_history, record_recovery_action
    conn = db.get_db()
    timeline = customer_timeline(conn, customer_id)
    if not timeline:
        conn.close(); abort(404)
    if request.method == "POST":
        check_csrf()
        action = (request.form.get("action") or "interaction").strip()
        admin_id=int(session["admin_id"])
        from governance_service import log_support_interaction
        if action == 'recovery_step':
            playbook_key=(request.form.get('playbook_key') or '').strip()
            step_index=int(request.form.get('step_index') or 0)
            step_action=(request.form.get('step_action') or '').strip()
            outcome=(request.form.get('outcome') or '').strip()
            if not playbook_key or not step_action:
                conn.close(); flash('Recovery step is incomplete.', 'error'); return redirect(url_for('admin.admin_customer_timeline', customer_id=customer_id))
            record_recovery_action(conn, customer_id=customer_id, admin_id=admin_id, playbook_key=playbook_key, step_index=step_index, action=step_action, outcome=outcome)
            log_support_interaction(conn, customer_id=customer_id, admin_id=admin_id, channel='recovery', subject=f'Recovery playbook: {playbook_key}', summary=step_action, outcome=outcome)
            conn.close(); flash('Recovery step recorded in the customer history.', 'success'); return redirect(url_for('admin.admin_customer_timeline', customer_id=customer_id))
        log_support_interaction(conn, customer_id=customer_id, admin_id=admin_id,
                                channel=(request.form.get("channel") or "internal").strip(),
                                subject=(request.form.get("subject") or "Support interaction").strip(),
                                summary=(request.form.get("summary") or "").strip(),
                                outcome=(request.form.get("outcome") or "").strip())
        conn.close()
        flash("Customer interaction saved to the permanent customer timeline.", "success")
        return redirect(url_for("admin.admin_customer_timeline", customer_id=customer_id))
    playbooks = recommend_recovery_playbooks(timeline)
    recovery_history = recovery_action_history(conn, customer_id)
    conn.close()
    return render_template("admin/customer_timeline.html", timeline=timeline, playbooks=playbooks, recovery_history=recovery_history)
