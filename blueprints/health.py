"""
Health Check Blueprint
"""
from flask import Blueprint, jsonify, current_app
from database import get_db
from schema_contract import missing_columns

health_bp = Blueprint('health', __name__)

@health_bp.route('/healthz', methods=['GET'])
def healthz():
    """Liveness probe: check that the application is running."""
    return jsonify({"status": "ok"}), 200

@health_bp.route('/readyz', methods=['GET'])
def readyz():
    """Readiness probe: check that the application can serve traffic.
    We check the database connection.
    """
    conn = get_db()
    try:
        # Prove both connectivity and the critical schema contract.  A database
        # that answers SELECT 1 but is missing a column is not ready to serve
        # admin/payment traffic safely.
        conn.execute("SELECT 1")
        missing = missing_columns(conn)
        if missing:
            names = [f"{item.table}.{item.name}" for item in missing]
            current_app.logger.error("Readiness failed: schema contract incomplete: %s", names)
            return jsonify({"status": "error", "reason": "schema_incomplete", "missing": names}), 503
        return jsonify({"status": "ok"}), 200
    except Exception as e:
        current_app.logger.exception("Readiness check failed")
        return jsonify({"status": "error"}), 503
    finally:
        conn.close()

@health_bp.route('/healthz/backend', methods=['GET'])
def backend_health():
    """Safe backend diagnostics: no secrets, just capability/configuration state."""
    import os
    return jsonify({
        "status": "ok",
        "database": "configured" if (os.environ.get("TURSO_DB_URL") and os.environ.get("TURSO_DB_AUTH_TOKEN")) else "local-sqlite",
        "redis": bool(os.environ.get("REDIS_URL")),
        "razorpay": bool(os.environ.get("RAZORPAY_KEY_ID") and os.environ.get("RAZORPAY_KEY_SECRET")),
        "sentry": bool(os.environ.get("SENTRY_DSN")),
        "outbox": True,
    }), 200


@health_bp.route('/internal/reconciliation', methods=['POST'])
def internal_reconciliation_trigger():
    """Protected scheduler endpoint. The Render cron job calls the web service so it reaches the persistent web-service database."""
    import hmac, os
    configured = os.environ.get('CRON_SECRET', '').strip()
    supplied = str(__import__('flask').request.headers.get('X-TITAN-CRON-SECRET', '')).strip()
    if not configured or not hmac.compare_digest(configured, supplied):
        return jsonify({'error': 'forbidden'}), 403
    try:
        from reconcile_razorpay import reconcile
        result = reconcile(created_by=0, mode='scheduled', order_limit=5000)
        return jsonify(result), 200 if result.get('status') in {'completed', 'skipped'} else 500
    except Exception as exc:
        current_app.logger.exception('Scheduled reconciliation failed')
        return jsonify({'status': 'failed', 'error': str(exc)[:500]}), 500
