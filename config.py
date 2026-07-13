"""
App configuration.

All secrets are read from environment variables so nothing sensitive
lives in the code. For local/manual hosting we also support a plain
`.env` file (KEY=VALUE per line) — no extra dependency needed for that,
it's parsed by the tiny loader below.
"""
import os
import secrets


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
# No hardcoded fallback here on purpose: a secret key baked into source code
# (visible to anyone who can read this repo) defeats the point of a secret.
# If you don't set SECRET_KEY yourself, a random one is generated at startup
# instead — sessions just won't survive a restart until you set a real one.
SECRET_KEY = os.environ.get("SECRET_KEY") or secrets.token_hex(32)
SECRET_KEY_WAS_GENERATED = "SECRET_KEY" not in os.environ

DEBUG = os.environ.get("DEBUG", "false").lower() == "true"

# --- Database ---
DB_PATH = os.environ.get("DB_PATH", os.path.join("instance", "store.db"))

# --- Razorpay ---
RAZORPAY_KEY_ID = os.environ.get("RAZORPAY_KEY_ID", "")
RAZORPAY_KEY_SECRET = os.environ.get("RAZORPAY_KEY_SECRET", "")

# --- First-run admin account (only used the very first time the DB is created) ---
# Same reasoning as SECRET_KEY: no predictable fallback password. If
# ADMIN_PASSWORD isn't set, database.py generates a random one on first run
# and writes it to instance/INITIAL_ADMIN_PASSWORD.txt so you can log in once
# and then change it immediately from the admin panel.
DEFAULT_ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "admin")
DEFAULT_ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")

# --- Uploads ---
UPLOAD_FOLDER = os.path.join("static", "uploads")
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
OTP_DEV_MODE = os.environ.get("OTP_DEV_MODE", "true").lower() == "true"
