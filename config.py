"""
App configuration.

All secrets are read from environment variables so nothing sensitive
lives in the code. For local/manual hosting we also support a plain
`.env` file (KEY=VALUE per line) — no extra dependency needed for that,
it's parsed by the tiny loader below.
"""
import os


def _load_dotenv(path=".env"):
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            os.environ.setdefault(key, value)


_load_dotenv()

# --- Core ---
# Refuse values that are commonly copied from .env.example files or quick-start
# guides. They are not safe session secrets, even if they technically work.
_SECRET_KEY_PLACEHOLDERS = {
    "please-change-this-to-a-long-random-string",
    "change-me",
    "change_this",
    "changeme",
    "your-secret-key",
    "your-secret-key-here",
    "secret",
    "secret-key",
    "default-secret-key",
}
_secret_key_env = os.environ.get("SECRET_KEY", "").strip()
if _secret_key_env.lower() in _SECRET_KEY_PLACEHOLDERS:
    raise RuntimeError(
        "SECRET_KEY contains a known placeholder value. Set a unique random "
        "SECRET_KEY before starting the application."
    )

# Default settings for the application
DEFAULT_SETTINGS = {
    "site_name": "Virtual Store",
    "site_tagline": "Curated finds, considered craft",
    "currency_symbol": "₹",
    "currency_code": "INR",
    "test_checkout_mode": "false",
}

# A stable, explicitly configured key is required so signed sessions survive
# application restarts. Never generate a replacement key at startup: doing so
# silently invalidates every existing session and can hide a deployment error.
if not _secret_key_env:
    raise RuntimeError(
        "SECRET_KEY is required. Set SECRET_KEY in the environment or .env "
        "file before starting the application. Generate one with "
        "`python3 -c \"import secrets; print(secrets.token_urlsafe(32))\"`."
    )

SECRET_KEY = _secret_key_env

DEBUG = os.environ.get("DEBUG", "false").lower() == "true"
ALLOW_TEST_GATEWAY = os.environ.get("ALLOW_TEST_GATEWAY", "false").lower() in ("true", "1", "yes", "on")
# Safe store-level checkout simulator. Keep this disabled by default in production;
# enabling it never talks to Razorpay. It only exercises local order workflows.
ALLOW_STORE_TEST_MODE = os.environ.get("ALLOW_STORE_TEST_MODE", "true" if DEBUG else "false").lower() in ("true", "1", "yes", "on")
TEST_MODE_SEND_EMAILS = os.environ.get("TEST_MODE_SEND_EMAILS", "false").lower() in ("true", "1", "yes", "on")

# --- Database ---
DB_PATH = os.environ.get("DB_PATH", os.path.join("instance", "store.db"))

# --- Razorpay ---
RAZORPAY_KEY_ID = os.environ.get("RAZORPAY_KEY_ID", "")
RAZORPAY_KEY_SECRET = os.environ.get("RAZORPAY_KEY_SECRET", "")
RAZORPAY_WEBHOOK_SECRET = os.environ.get("RAZORPAY_WEBHOOK_SECRET", "")

# --- Observability / background jobs ---
SENTRY_DSN = os.environ.get("SENTRY_DSN", "").strip()
INTELLIGENCE_API_URL = os.environ.get("INTELLIGENCE_API_URL", "").strip()
INTELLIGENCE_API_KEY = os.environ.get("INTELLIGENCE_API_KEY", "").strip()
INTELLIGENCE_MODEL = os.environ.get("INTELLIGENCE_MODEL", "").strip()
REDIS_URL = os.environ.get("REDIS_URL", "").strip()

# --- First-run admin account ---
# Production requires an explicit ADMIN_PASSWORD. A random one may only be
# generated for local/debug use.
DEFAULT_ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "admin")
DEFAULT_ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")

# --- Product file uploads (for auto-delivery download links) ---
# Product files must not live below static/, because Flask serves that tree
# directly without download-token expiry or download-count checks. Keep this
# on the persistent disk in production (Render mounts instance/).
PRODUCT_UPLOAD_FOLDER = os.environ.get(
    "PRODUCT_UPLOAD_FOLDER", os.path.join("instance", "product_files")
)
ALLOWED_PRODUCT_EXTENSIONS = {"pdf", "zip", "txt", "csv", "json", "xml", "doc", "docx", "xlsx", "jpg", "jpeg", "png", "gif", "mp3", "mp4", "epub", "mobi"}
MAX_PRODUCT_FILE_MB = int(os.environ.get("MAX_PRODUCT_FILE_MB", "100"))

# --- Uploads ---
# Keep product images on Render's persistent instance disk so they survive
# redeploys. The application factory creates this directory at startup.
UPLOAD_FOLDER = os.path.join("instance", "uploads")
ALLOWED_IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "webp"}
MAX_IMAGE_SIZE_MB = 5
MAX_IMAGE_DIMENSION = 1600  # longest side, in pixels — keeps the site fast

# --- Optional: email notifications to customers (SMTP). Leave blank to disable. ---
SMTP_HOST = os.environ.get("SMTP_HOST", "")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587") or 587)
SMTP_USERNAME = os.environ.get("SMTP_USERNAME", "")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
SMTP_FROM = os.environ.get("SMTP_FROM", SMTP_USERNAME)

# --- Optional: Cloudflare Turnstile CAPTCHA (free, no request limits) ---
# Leave both blank to disable — the site works fine without it, just with
# less bot protection on login/checkout/newsletter forms. Get free keys at
# https://dash.cloudflare.com/ -> Turnstile.
TURNSTILE_SITE_KEY = os.environ.get("TURNSTILE_SITE_KEY", "")
TURNSTILE_SECRET_KEY = os.environ.get("TURNSTILE_SECRET_KEY", "")

# --- Optional: Firebase Phone Authentication (OTP sign-in / account creation) ---
# Lets customers verify their phone number with a one-time SMS code, right
# from the homepage or at checkout, instead of (or alongside) guest checkout.
# All six values below come from Firebase Console -> Project settings ->
# General -> "Your apps" -> Web app (</> icon) -> SDK setup and config.
# They are meant to be public (shipped to the browser) — Firebase's security
# model relies on backend ID-token verification, not on hiding these.
# Leave FIREBASE_API_KEY blank to disable phone sign-in entirely; the site
# works fine as guest-checkout-only without it.
FIREBASE_API_KEY = os.environ.get("FIREBASE_API_KEY", "")
FIREBASE_AUTH_DOMAIN = os.environ.get("FIREBASE_AUTH_DOMAIN", "")
FIREBASE_PROJECT_ID = os.environ.get("FIREBASE_PROJECT_ID", "")
FIREBASE_APP_ID = os.environ.get("FIREBASE_APP_ID", "")
FIREBASE_MESSAGING_SENDER_ID = os.environ.get("FIREBASE_MESSAGING_SENDER_ID", "")
FIREBASE_STORAGE_BUCKET = os.environ.get("FIREBASE_STORAGE_BUCKET", "")

# --- Optional: Firebase Cloud Messaging (push notifications to the Android
# admin app). This is a *different* Firebase credential from the six
# FIREBASE_* values above: those are public web-SDK keys used to verify
# customer phone sign-in, this one is a private, server-only service-account
# key used to *send* pushes via the firebase-admin SDK. Never expose it to a
# browser or commit it to source control.
#
# Get it from: Firebase Console -> Project settings -> Service accounts ->
# "Generate new private key" (downloads a JSON file).
#
# Two ways to supply it, either works:
#   1. FIREBASE_SERVICE_ACCOUNT_JSON = the full JSON contents, pasted as one
#      env var value (handiest on hosts like Render/Railway that don't let
#      you upload a file).
#   2. FIREBASE_SERVICE_ACCOUNT_FILE = a filesystem path to the downloaded
#      JSON file (handiest for local development).
# Leave both blank to disable push notifications entirely — the rest of the
# site and the /api/admin/* endpoints work fine without it, admins just won't
# get a phone alert when an order comes in.
FIREBASE_SERVICE_ACCOUNT_JSON = os.environ.get("FIREBASE_SERVICE_ACCOUNT_JSON", "")
FIREBASE_SERVICE_ACCOUNT_FILE = os.environ.get("FIREBASE_SERVICE_ACCOUNT_FILE", "")

# Web Push (VAPID) — used for browser push notifications on the admin panel.
# Generate a key pair with: openssl ecparam -genkey -name prime256v1 -noout -out vapid_private.pem
# Then: openssl ec -in vapid_private.pem -pubout -out vapid_public.pem
# Store the base64-encoded (URL-safe) raw public key and the PEM private key.
VAPID_PUBLIC_KEY = os.environ.get("VAPID_PUBLIC_KEY", "")
VAPID_PRIVATE_KEY = os.environ.get("VAPID_PRIVATE_KEY", "")
VAPID_CLAIM_EMAIL = os.environ.get("VAPID_CLAIM_EMAIL", "admin@virtualstore.local")

# --- Android admin app: bearer-token API auth ---
# How long a /api/admin/login token stays valid before the app needs to log
# in again. Independent of the web admin's session-cookie lifetime above.
API_TOKEN_EXPIRY_DAYS = int(os.environ.get("API_TOKEN_EXPIRY_DAYS", "30") or 30)

# --- Optional: transactional email via SendGrid or Resend (HTTP APIs) ---
# Preferred over SMTP when set — no app-password hassle, better deliverability,
# generous free tiers. If both are set, Resend is tried first, then SendGrid,
# falling back to SMTP (above) if neither is configured. Leave all blank and
# the site simply skips emailing (admin still sees everything in the panel).
RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
SENDGRID_API_KEY = os.environ.get("SENDGRID_API_KEY", "")
EMAIL_FROM = os.environ.get("EMAIL_FROM", SMTP_FROM)
EMAIL_FROM_NAME = os.environ.get("EMAIL_FROM_NAME", "")

# --- Optional: SMS gateway for OTP delivery ---
# When SMS_GATEWAY is "dev", OTP codes are returned to the frontend (shown in
# the UI) so you can test the full flow without an SMS provider. Set to "twilio"
# or another provider when ready for real SMS. Leave blank to default to "dev".
OTP_EXPIRY_MINUTES = int(os.environ.get("OTP_EXPIRY_MINUTES", "5"))
# Security: default to FALSE in production. OTP codes are only returned to the
# client when this is true, so the default must never expose verification codes
# on a live deployment. Set OTP_DEV_MODE=true locally for testing without SMS.
OTP_DEV_MODE = os.environ.get("OTP_DEV_MODE", "false").lower() == "true"
# --- Twilio SMS (for OTP delivery) ---
TWILIO_ACCOUNT_SID = os.environ.get("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN  = os.environ.get("TWILIO_AUTH_TOKEN", "")
TWILIO_FROM_NUMBER = os.environ.get("TWILIO_FROM_NUMBER", "")
TWILIO_WHATSAPP_FROM = os.environ.get("TWILIO_WHATSAPP_FROM", "whatsapp:+14155238886")
TWILIO_PHONE = os.environ.get("TWILIO_PHONE", "") or TWILIO_FROM_NUMBER

# --- Optional: Calendarific holiday API (provides accurate festival/holiday data) ---
# Get a free API key at https://calendarific.com/
# When set, the site auto-fetches holidays for the configured country on each
# admin settings save and caches them for greeting display. Leave blank to
# rely on the built-in static list only.
CALENDARIFIC_API_KEY = os.environ.get("CALENDARIFIC_API_KEY", "")
# ISO 3166-1 alpha-2 country code for holiday lookups (default: IN = India)
CALENDARIFIC_COUNTRY = os.environ.get("CALENDARIFIC_COUNTRY", "IN")


# --- Security ---
# Enable CSRF protection via Flask-WTF. Set to False to disable in dev/testing.
CSRF_ENABLED = os.environ.get("CSRF_ENABLED", "true").lower() in ("true", "1", "yes")

# --- Optional: Google OAuth (direct, no Firebase) ---
# Google Client ID for direct OAuth sign-in via Google Identity Services (GIS).
# Set this in your environment to enable fast Google sign-in without the
# Firebase SDK overhead. When empty, the Google sign-in button is hidden.
# Get a client ID from: https://console.cloud.google.com/apis/credentials
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")

# --- Downloads ---
MAX_DOWNLOADS = 5  # max times a customer can re-download before the token expires

# --- Runtime configuration object / validation ---
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class RuntimeConfig:
    """Validated, immutable application configuration exposed to services."""
    secret_key: str
    debug: bool
    db_path: str
    site_url: str
    razorpay_configured: bool
    razorpay_environment: str
    webhook_configured: bool
    admin_configured: bool
    rate_limit_storage_uri: str
    sentry_enabled: bool
    redis_enabled: bool


def validate_production_configuration() -> None:
    """Fail fast on unsafe production configuration combinations."""
    if DEBUG:
        return
    if DEFAULT_ADMIN_PASSWORD.strip() == "":
        raise RuntimeError("ADMIN_PASSWORD is required when DEBUG=false.")
    if OTP_DEV_MODE:
        raise RuntimeError("OTP_DEV_MODE must be false when DEBUG=false.")
    if str(os.environ.get("ALLOW_TEST_GATEWAY", "")).strip().lower() in {"1", "true", "yes", "on"}:
        raise RuntimeError("ALLOW_TEST_GATEWAY must not be enabled when DEBUG=false.")


def get_runtime_config() -> RuntimeConfig:
    return RuntimeConfig(
        secret_key=SECRET_KEY,
        debug=DEBUG,
        db_path=DB_PATH,
        site_url=os.environ.get("SITE_URL", ""),
        razorpay_configured=bool(RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET),
        razorpay_environment=("test" if RAZORPAY_KEY_ID.startswith("rzp_test_") else "live" if RAZORPAY_KEY_ID.startswith("rzp_live_") else "unknown"),
        webhook_configured=bool(RAZORPAY_WEBHOOK_SECRET),
        admin_configured=bool(DEFAULT_ADMIN_PASSWORD),
        rate_limit_storage_uri=os.environ.get("RATELIMIT_STORAGE_URI", ""),
        sentry_enabled=bool(SENTRY_DSN),
        redis_enabled=bool(REDIS_URL),
    )


if not DEBUG:
    validate_production_configuration()
