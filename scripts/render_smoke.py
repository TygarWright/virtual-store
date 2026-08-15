"""Render-parity smoke test.

Boots the real Flask application against a temporary SQLite database and exercises
critical public + admin GET routes using the same dependency/runtime configuration
expected in deployment. This is intentionally side-effect-light and never contacts
Razorpay or sends external notifications.
"""
from __future__ import annotations

import os
import re
import shutil
import tempfile
from pathlib import Path

# Configure the environment before importing app/config.
ROOT = Path(__file__).resolve().parents[1]
TMP = Path(tempfile.mkdtemp(prefix="titan-render-smoke-"))
os.environ.update({
    "SECRET_KEY": "ci-render-smoke-secret-please-replace",
    "DEBUG": "false",
    "DB_PATH": str(TMP / "store.db"),
    "ADMIN_USERNAME": "admin",
    "ADMIN_PASSWORD": "ci-render-smoke-admin-password",
    "RAZORPAY_KEY_ID": "rzp_test_ci_dummy",
    "RAZORPAY_KEY_SECRET": "ci_test_razorpay_secret",
    "RAZORPAY_WEBHOOK_SECRET": "ci_test_webhook_secret",
    "OTP_DEV_MODE": "false",
    "ALLOW_STORE_TEST_MODE": "false",
    "ALLOW_TEST_GATEWAY": "false",
    "REDIS_URL": "",
    "SENTRY_DSN": "",
    "TURNSTILE_SITE_KEY": "",
    "TURNSTILE_SECRET_KEY": "",
})


def assert_status(client, path: str, expected=(200, 302), label: str | None = None):
    response = client.get(path, follow_redirects=False)
    if response.status_code not in expected:
        body = response.get_data(as_text=True)[:500]
        raise AssertionError(f"{label or path}: HTTP {response.status_code}; body={body!r}")
    return response


def main() -> int:
    try:
        from app import app  # noqa: E402

        app.testing = True
        client = app.test_client()

        public_routes = [
            "/",
            "/privacy",
            "/terms",
            "/refund-policy",
            "/track",
            "/favicon.ico",
            "/healthz",
        ]
        for path in public_routes:
            assert_status(client, path)

        login = client.get("/admin/login")
        if login.status_code != 200:
            raise AssertionError(f"/admin/login: HTTP {login.status_code}")
        html = login.get_data(as_text=True)
        match = re.search(r'name="csrf_token"\s+value="([^"]+)"', html)
        if not match:
            raise AssertionError("Admin login did not render a CSRF token")

        response = client.post(
            "/admin/login",
            data={
                "csrf_token": match.group(1),
                "username": "admin",
                "password": "ci-render-smoke-admin-password",
            },
            follow_redirects=False,
        )
        if response.status_code not in (302, 303):
            body = response.get_data(as_text=True)[:500]
            raise AssertionError(f"Admin login failed: HTTP {response.status_code}; body={body!r}")

        admin_routes = [
            "/admin/",
            "/admin/orders",
            "/admin/products",
            "/admin/guardian",
            "/admin/team",
            "/admin/team-hub",
            "/admin/tickets",
            "/admin/stock-requests",
            "/admin/newsletter",
            "/admin/audit-log",
            "/admin/notices",
            "/admin/training",
            "/admin/simulation-lab",
            "/admin/account",
            "/admin/insights",
            "/admin/customers",
        ]
        for path in admin_routes:
            assert_status(client, path, expected=(200, 302), label=f"admin {path}")

        print(f"PASS Render smoke: {len(public_routes)} public + {len(admin_routes)} admin routes")
        return 0
    finally:
        shutil.rmtree(TMP, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
