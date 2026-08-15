import os
import secrets
import uuid
import hashlib
import json
from datetime import datetime, timedelta, timezone

import pyotp
from markupsafe import Markup

from flask import (
    Flask, g, render_template, request, redirect, url_for, session,
    flash, jsonify, abort, send_file, Response, send_from_directory,
    current_app,
)
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.middleware.proxy_fix import ProxyFix
from functools import wraps

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
    requires_permission,
    log_admin_action,
    invalidate_settings_cache,
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
    transition_payment_state,
)
from admin_api import admin_api
from storefront_service import get_primary_image_map

# Security extensions
from flask_talisman import Talisman
from extensions import db as sqlalchemy_db, migrate as sqlalchemy_migrate, limiter, csrf

# Observability extensions
import sentry_sdk
from sentry_sdk.integrations.flask import FlaskIntegration
from sentry_sdk.integrations.sqlalchemy import SqlalchemyIntegration
import logging
import sys
from pythonjsonlogger import jsonlogger
from redis import Redis
import rq
from prometheus_flask_exporter import PrometheusMetrics

# ---------------------------------------------------------------------------
# Calendarific holiday helper — fetches & caches authentic holidays for the
# configured country so the smart greeting shows real festival names.
# ---------------------------------------------------------------------------
import urllib.request as _ur
import json as _j

_CALENDARIFIC_CACHE_KEY = "calendarific_last_fetch"


def _get_webp_path(path):
    """If a WebP version of the given image path exists, return the WebP path.
    Otherwise return the original path unchanged.  The WebP file must be in
    the same directory with `.webp` appended (e.g. `photo.jpg.webp`)."""
    if not path:
        return path
    webp_path = path + ".webp"
    full = os.path.join(config.UPLOAD_FOLDER, webp_path)
    if os.path.exists(full):
        return webp_path
    return path


def _track_product_view(product_id):
    """Track a recently viewed product.  Stores up to 10 product IDs in the
    session, most recent first.  Uses strings for JSON-serializable storage."""
    pid_str = str(product_id)
    viewed = session.get("recently_viewed", [])
    if pid_str in viewed:
        viewed.remove(pid_str)
    viewed.insert(0, pid_str)
    session["recently_viewed"] = viewed[:10]
    session.modified = True


def fetch_calendarific_holidays(year=None, force=False):
    """Fetch holidays from Calendarific for the configured country+year and
    cache them in the settings table as holiday_<mmdd> keys.  Returns a
    dict of {mmdd: festival_name} (the cached set even on a re-fetch)."""
    if not config.CALENDARIFIC_API_KEY:
        return {}
    if year is None:
        year = datetime.now(timezone.utc).year
    conn = db.get_db()
    # Check if we already fetched this year (unless forced)
    if not force:
        cached = conn.execute(
            "SELECT value FROM settings WHERE key = ?", (_CALENDARIFIC_CACHE_KEY,)
        ).fetchone()
        if cached and cached["value"] == str(year):
            rows = conn.execute(
                "SELECT key, value FROM settings WHERE key LIKE 'holiday_%'"
            ).fetchall()
            conn.close()
            return {r["key"].replace("holiday_", ""): r["value"] for r in rows}
    # Fetch from Calendarific
    url = (
        f"https://calendarific.com/api/v2/holidays?"
        f"api_key={config.CALENDARIFIC_API_KEY}&"
        f"country={config.CALENDARIFIC_COUNTRY}&"
        f"year={year}"
    )
    try:
        req = _ur.Request(url, headers={"User-Agent": "virtual-store/1.0"})
        resp = _ur.urlopen(req, timeout=10)
        data = _j.loads(resp.read())
    except Exception as exc:
        _startup_logger.warning("Calendarific fetch failed: %s", exc)
        conn.close()
        return {}
    holidays = data.get("response", {}).get("holidays", [])
    # Clear old holiday cache
    conn.execute("DELETE FROM settings WHERE key LIKE 'holiday_%'")
    conn.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (_CALENDARIFIC_CACHE_KEY, str(year)),
    )
    result = {}
    for h in holidays:
        date_str = h.get("date", {}).get("iso")
        if not date_str:
            continue
        mmdd = date_str[5:7] + date_str[8:10]
        name = h.get("name", "").strip()
        if mmdd and name:
            # Only take the first holiday for each date (highest-level one)
            if mmdd not in result:
                # Skip "working day" / "observance" type holidays
                holiday_type = h.get("type", [])
                if not any(t in str(holiday_type) for t in ("National", "Common", "Observance")):
                    continue
                # Skip generic observances like "Day off for ..."
                if "Day off for" in name:
                    continue
                result[mmdd] = name
                conn.execute(
                    "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    (f"holiday_{mmdd}", name),
                )
    conn.commit()
    conn.close()
    return result


def create_app():
      # Render persists the instance directory across deploys. Ensure the image
      # upload directory exists before routes or upload handlers use it.
      os.makedirs(config.UPLOAD_FOLDER, exist_ok=True)
      app = Flask(__name__)
      from flask_compress import Compress
      Compress(app)
      # Trust Render's proxy headers for correct scheme (HSTS), remote_addr (rate
      # limiting), and host detection. x_for=1 trusts the leftmost X-Forwarded-For,
      # x_proto=1 trusts the leftmost X-Forwarded-Proto (https flag from Render's LB).
      app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
      app.config["SECRET_KEY"] = config.SECRET_KEY
      # Validate production settings once, then expose immutable runtime settings
      # and provider implementations through a tiny dependency-injection container.
      runtime_config = config.get_runtime_config()
      app.extensions["titan.config"] = runtime_config
      from service_container import build_service_container
      app.extensions["titan.services"] = build_service_container(config)
      # Configure SQLAlchemy for Flask-Migrate using the shared extension instance.
      # The same URI is used by the legacy sqlite layer, while the SQLAlchemy
      # extension provides migrations/repository access without a second DB object.
      app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///" + config.DB_PATH
      app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
      app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
          "pool_pre_ping": True,
          "pool_recycle": 1800,
      }
      sqlalchemy_db.init_app(app)
      sqlalchemy_migrate.init_app(app, sqlalchemy_db)

      # Import models after the shared SQLAlchemy instance is initialized so
      # Alembic can see their metadata for autogeneration.
      import models  # noqa: F401

      # =============================================================================
      # Observability Setup
      # =============================================================================

      # 1. Sentry Error Tracking
      if hasattr(config, 'SENTRY_DSN') and config.SENTRY_DSN:
          sentry_sdk.init(
              dsn=config.SENTRY_DSN,
              integrations=[
                  FlaskIntegration(),
                  SqlalchemyIntegration(),
              ],
              traces_sample_rate=0.1,
              profiles_sample_rate=0.1,
          )

      # 2. Structured Logging with JSON
      # Remove default handlers
      for handler in app.logger.handlers[:]:
          app.logger.removeHandler(handler)
      # Create a JSON formatter
      formatter = jsonlogger.JsonFormatter(
          '%(asctime)s %(levelname)s %(name)s %(message)s %(request_id)s %(method)s %(path)s %(status_code)s'
      )
      # Create a stream handler for stdout
      handler = logging.StreamHandler(sys.stdout)
      handler.setFormatter(formatter)
      # Set the logger level
      app.logger.setLevel(logging.INFO)
      app.logger.addHandler(handler)
      # Also set the root logger
      logging.getLogger().setLevel(logging.INFO)
      logging.getLogger().addHandler(handler)
      # Prevent duplicate logs
      app.logger.propagate = False

      # 3. Request ID Middleware
      @app.before_request
      def before_request():
          """Generate or retrieve a request ID for correlation."""
          request_id = request.headers.get('X-Request-ID', str(uuid.uuid4()))
          g.request_id = request_id
          # Also set it in the Sentry scope for error events
          sentry_sdk.set_tag("request_id", request_id)

      # 4. Block direct access to product files in static/ (for security)
      @app.before_request
      def block_product_files_access():
          if request.path.startswith('/static/product_files/'):
              abort(404)

      # 4. Background Job Queue (RQ)
      # Redis is optional. Do not fall back to Redis() when REDIS_URL is
      # missing, because that silently targets localhost and can break
      # deployments that do not provision a Redis service.
      redis_url = getattr(config, "REDIS_URL", "")
      if redis_url:
          app.redis = Redis.from_url(redis_url)
          app.task_queue = rq.Queue("default", connection=app.redis)
      else:
          app.redis = None
          app.task_queue = None
          app.logger.warning(
              "REDIS_URL is not configured; background job queue is disabled."
          )

      # 5. Prometheus Metrics
      # Expose /metrics endpoint by default
      PrometheusMetrics(app, group_by='endpoint')

      # 6. Audit Logging Helper
      def audit_log(action, user_id=None, details=None):
          """Log an audit event in structured format."""
          audit_entry = {
              "action": action,
              "user_id": user_id,
              "details": details or {},
              "timestamp": datetime.now(timezone.utc).isoformat(),
              "request_id": getattr(g, 'request_id', None),
          }
          app.logger.info("AUDIT", extra=audit_entry)

      # Make the audit_log function available in the app context
      app.audit_log = audit_log

      # =============================================================================
      # Security Setup
      # =============================================================================

      # Initialize Talisman for security headers
      csp = {
          'default-src': [
              '\'self\'',
              'https:'
          ],
          'script-src': [
              '\'self\'',
              '\'unsafe-inline\'',
              'https:'
          ],
          'style-src': [
              '\'self\'',
              '\'unsafe-inline\'',
              'https:'
          ],
          'img-src': [
              '\'self\'',
              'data:',
              'https:'
          ],
          'font-src': [
              '\'self\'',
              'https:'
          ],
      }
      # Keep local HTTP test/development clients usable, while enforcing HTTPS
      # and secure session cookies whenever the configured site is not local.
      site_url = str(getattr(config, "SITE_URL", "")).lower()
      local_site = site_url.startswith((
          "http://localhost",
          "http://127.0.0.1",
          "http://testserver",
      ))
      production_security = not getattr(config, "DEBUG", False) and not local_site

      Talisman(
          app,
          content_security_policy=csp,
          force_https=production_security,
          session_cookie_secure=production_security,
          session_cookie_http_only=True,
          session_cookie_samesite='Lax',
      )

      # Initialize shared rate limiting with conservative global defaults.
      # Sensitive endpoints below receive explicit route-level limits.
      limiter.init_app(app)
      app.limiter = limiter

      # Initialize shared CSRF protection if enabled. Bearer-token API routes
      # are exempted after registration; browser-session endpoints still call
      # check_csrf/check_csrf_api explicitly where JSON requests are involved.
      if config.CSRF_ENABLED:
          csrf.init_app(app)
      app.extensions["csrf"] = csrf

      # =============================================================================
      # Context Processors
      # =============================================================================

      @app.context_processor
      def inject_greeting_data():
          from datetime import datetime, timezone
          # Determine time of day
          hour = datetime.now(timezone.utc).hour
          if 5 <= hour < 12:
              tod = "morning"
          elif 12 <= hour < 17:
              tod = "afternoon"
          elif 17 <= hour < 21:
              tod = "evening"
          else:
              tod = "night"

          # Check if this is the user's first visit
          is_first_visit = not session.get("visitor_id")

          # Get order count for the customer
          order_count = 0
          if session.get("customer_id"):
              conn = db.get_db()
              try:
                  order_count = conn.execute(
                      "SELECT COUNT(*) FROM orders WHERE customer_id = ?",
                      (session["customer_id"],),
                  ).fetchone()[0]
              except Exception:
                  pass
              finally:
                  conn.close()

          # Determine festival
          today_str = datetime.now(timezone.utc).strftime("%m%d")
          festival = None
          # Built-in fallback festival list
          festive = {
              "0101": "New Year", "0126": "Republic Day", "0214": "Valentine's",
              "0310": "Holi", "0329": "Easter", "0414": "Baisakhi", "0501": "Labour Day",
              "0618": "Eid al-Adha", "0703": "Guru Purnima", "0815": "Independence Day",
              "0826": "Raksha Bandhan", "0831": "Janmashtami", "1002": "Gandhi Jayanti",
              "1007": "Dussehra", "1020": "Karwa Chauth", "1027": "Diwali", "1101": "Diwali",
              "1115": "Guru Nanak Jayanti", "1204": "Christmas", "1225": "Christmas",
          }
          festival = festive.get(today_str)

          # Determine greeting message
          greeting_msg = ""
          if festival:
              greeting_msg = f"Happy {festival}!"

          greeting_data = {
              "timeOfDay": tod,
              "isNewUser": is_first_visit,
              "orderCount": order_count,
              "festival": festival,
              "msg": greeting_msg,
          }

          return dict(greeting_data=greeting_data)

      @app.context_processor
      def inject_csrf_token():
          """Provide a CSRF token function to Jinja templates."""
          return dict(csrf_token=lambda: get_csrf_token())

      @app.context_processor
      def inject_cart_count():
          """Provide cart count to all templates."""
          cart = session.get("cart", {})
          cart_count = sum(cart.values()) if cart else 0
          return dict(cart_count=cart_count)

      @app.context_processor
      def inject_admin_permissions():
          """Expose the current admin permission check to Jinja templates."""
          perms = session.get("admin_permissions", []) or []
          role = session.get("admin_role", "")
          return dict(admin_can=lambda *required: bool(
              role in ("master", "admin") or has_permission(perms, *required)
          ))

      @app.context_processor
      def inject_customer_auth_config():
          """Expose only authentication methods actually configured."""
          firebase_enabled = bool(firebase_auth_enabled())
          google_enabled = bool(getattr(config, "GOOGLE_CLIENT_ID", ""))
          return dict(
              firebase_auth_enabled=firebase_enabled,
              gis_enabled=google_enabled,
              customer_auth_enabled=(firebase_enabled or google_enabled),
          )

      # =============================================================================
      # Blueprint Registration
      # =============================================================================

      # Register blueprints
      from blueprints.storefront import storefront_bp
      from blueprints.admin import admin_bp
      from blueprints.health import health_bp

      app.register_blueprint(storefront_bp)
      app.register_blueprint(admin_bp, url_prefix='/admin')
      app.register_blueprint(health_bp)
      # Bearer-token admin API endpoints are not cookie-authenticated and therefore
      # do not need browser CSRF protection. Browser push endpoints in that
      # blueprint still perform explicit check_csrf() calls.
      csrf.exempt(admin_api)

      # Re-export internal helpers for test compatibility
      from blueprints.storefront import (
          _confirm_order_payment, download_product as _download_product,
      )

      # Custom static route to prevent serving files from the product_files subdirectory
      @app.route('/static/<path:filename>')
      def custom_static(filename):
          if filename.startswith('product_files/'):
              abort(404)
          response = send_from_directory(app.static_folder, filename)
          lower = filename.lower()
          if lower.endswith(('.css', '.js', '.svg', '.png', '.jpg', '.jpeg', '.webp', '.gif', '.woff2', '.woff', '.ico')):
              response.headers.setdefault('Cache-Control', 'public, max-age=604800, stale-while-revalidate=86400')
          else:
              response.headers.setdefault('Cache-Control', 'no-cache')
          return response

      return app


app = create_app()

# Re-export for test compatibility (tests import app and call app._confirm_order_payment)
from blueprints.storefront import (
    _confirm_order_payment, download_product,
)