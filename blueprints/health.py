"""
Health Check Blueprint
"""
from flask import Blueprint, jsonify, current_app
from database import get_db

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
        # Try to execute a simple query to check the database.
        conn.execute("SELECT 1")
        return jsonify({"status": "ok"}), 200
    except Exception as e:
        current_app.logger.exception("Readiness check failed")
        return jsonify({"status": "error"}), 503
    finally:
        conn.close()