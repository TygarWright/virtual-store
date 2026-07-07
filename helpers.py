import os
import re
import time
import secrets
import smtplib
import threading
from email.mime.text import MIMEText
from functools import wraps

import requests
from flask import session, redirect, url_for, request, abort, jsonify
from werkzeug.utils import secure_filename
from PIL import Image, UnidentifiedImageError

import config


# ---------- Auth ----------

def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("admin_id"):
            return redirect(url_for("admin_login", next=request.path))
        return view(*args, **kwargs)
    return wrapped


# ---------- CSRF (lightweight, no extra dependency) ----------
# Two flavours: `check_csrf()` for normal HTML <form> POSTs (token comes in
# the form body), and `check_csrf_api()` for JSON fetch() calls, where the
# token travels in an `X-CSRF-Token` header instead (forms don't send custom
# headers, which is exactly why this split exists).

def get_csrf_token():
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_hex(16)
    return session["csrf_token"]


def check_csrf():
    token = request.form.get("csrf_token", "")
    if not token or token != session.get("csrf_token"):
        abort(400, description="Your session expired — please refresh and try again.")


def check_csrf_api():
    """For requests made via fetch() rather than a plain form submit — covers
    JSON bodies (token in body or header) and FormData bodies sent via fetch
    (token travels as a normal form field there, same as check_csrf())."""
    token = request.headers.get("X-CSRF-Token", "")
    if not token:
        data = request.get_json(silent=True) or {}
        token = data.get("csrf_token", "")
    if not token:
        token = request.form.get("csrf_token", "")
    if not token or token != session.get("csrf_token"):
        response = jsonify({"error": "Your session expired — please refresh the page and try again."})
        response.status_code = 400
        abort(response)


# ---------- Rate limiting (simple, in-process — no Redis needed) ----------
# Good enough for a single small instance. Resets on restart, and only tracks
# this one worker process — not a substitute for a real service at scale, but
# stops casual scripted abuse of login/checkout/coupon/newsletter endpoints.

_rate_buckets = {}
_rate_lock = threading.Lock()


def _client_ip():
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.remote_addr or "unknown"


def rate_limited(key_prefix, max_attempts, window_seconds):
    """Returns True if the current client has exceeded max_attempts within
    window_seconds for this key_prefix. Also records the current attempt."""
    key = f"{key_prefix}:{_client_ip()}"
    now = time.time()
    with _rate_lock:
        bucket = _rate_buckets.setdefault(key, [])
        # drop anything outside the window
        cutoff = now - window_seconds
        while bucket and bucket[0] < cutoff:
            bucket.pop(0)
        if len(bucket) >= max_attempts:
            return True
        bucket.append(now)
        # keep the whole structure from growing forever
        if len(_rate_buckets) > 5000:
            _rate_buckets.clear()
        return False


def rate_limit(key_prefix, max_attempts, window_seconds):
    """Decorator form of rate_limited(), for use directly on a route."""
    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if rate_limited(key_prefix, max_attempts, window_seconds):
                if request.is_json or request.path.startswith("/api/"):
                    return jsonify({"error": "Too many attempts — please wait a minute and try again."}), 429
                flash_msg = "Too many attempts — please wait a minute and try again."
                from flask import flash as _flash
                _flash(flash_msg, "error")
                return redirect(request.referrer or url_for("home"))
            return view(*args, **kwargs)
        return wrapped
    return decorator


# ---------- CAPTCHA (Cloudflare Turnstile — free, no account limits) ----------

def turnstile_enabled():
    return bool(config.TURNSTILE_SITE_KEY and config.TURNSTILE_SECRET_KEY)


def verify_turnstile(token):
    """Returns True if Turnstile is not configured (so the site works before
    setup) or if the token is valid. Returns False only on a real failure."""
    if not turnstile_enabled():
        return True
    if not token:
        return False
    try:
        resp = requests.post(
            "https://challenges.cloudflare.com/turnstile/v0/siteverify",
            data={
                "secret": config.TURNSTILE_SECRET_KEY,
                "response": token,
                "remoteip": _client_ip(),
            },
            timeout=8,
        )
        return bool(resp.json().get("success"))
    except Exception:
        return False


# ---------- Slugs ----------

def slugify(text):
    text = text.lower().strip()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    text = re.sub(r"-+", "-", text).strip("-")
    return text or secrets.token_hex(4)


# ---------- Images ----------

def allowed_image(filename):
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    return ext in config.ALLOWED_IMAGE_EXTENSIONS


def save_product_image(file_storage):
    """
    Saves an uploaded image, resized so the longest side is at most
    MAX_IMAGE_DIMENSION px, re-encoded efficiently. Keeps the storefront
    fast regardless of what the admin uploads from their phone/camera.
    Returns the stored filename.
    """
    if not file_storage or file_storage.filename == "":
        return None
    if not allowed_image(file_storage.filename):
        raise ValueError("Please upload a PNG, JPG or WEBP image.")

    # Verify this is actually a valid, safe-to-decode image before touching it —
    # Image.open() alone doesn't fully parse the file, so a corrupt or
    # disguised upload could otherwise slip through.
    try:
        file_storage.stream.seek(0)
        probe = Image.open(file_storage.stream)
        probe.verify()
    except (UnidentifiedImageError, OSError, ValueError):
        raise ValueError("That file doesn't look like a valid image. Please try a different file.")
    finally:
        file_storage.stream.seek(0)

    os.makedirs(config.UPLOAD_FOLDER, exist_ok=True)
    ext = file_storage.filename.rsplit(".", 1)[-1].lower()
    filename = secure_filename(f"{secrets.token_hex(8)}.{ext}")
    path = os.path.join(config.UPLOAD_FOLDER, filename)

    image = Image.open(file_storage)
    image.load()

    # JPEG has no alpha channel — flatten transparency onto white instead of
    # letting Pillow error out (or silently corrupt colours) on save.
    if ext in ("jpg", "jpeg"):
        if image.mode in ("RGBA", "LA") or (image.mode == "P" and "transparency" in image.info):
            background = Image.new("RGB", image.size, (255, 255, 255))
            rgba = image.convert("RGBA")
            background.paste(rgba, mask=rgba.split()[-1])
            image = background
        elif image.mode != "RGB":
            image = image.convert("RGB")
    elif image.mode not in ("RGB", "RGBA"):
        image = image.convert("RGBA" if "A" in image.getbands() else "RGB")

    w, h = image.size
    longest = max(w, h)
    if longest > config.MAX_IMAGE_DIMENSION:
        scale = config.MAX_IMAGE_DIMENSION / longest
        image = image.resize((int(w * scale), int(h * scale)), Image.LANCZOS)

    save_kwargs = {"quality": 85, "optimize": True} if ext in ("jpg", "jpeg") else {"optimize": True}
    image.save(path, **save_kwargs)
    return filename


def delete_file_quietly(filename):
    try:
        os.remove(os.path.join(config.UPLOAD_FOLDER, filename))
    except OSError:
        pass


# ---------- Email (optional) ----------

def email_enabled():
    return bool(config.SMTP_HOST and config.SMTP_USERNAME and config.SMTP_PASSWORD)


def send_email(to_address, subject, body):
    if not email_enabled():
        return False
    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = config.SMTP_FROM
    msg["To"] = to_address
    try:
        with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=15) as server:
            server.starttls()
            server.login(config.SMTP_USERNAME, config.SMTP_PASSWORD)
            server.sendmail(config.SMTP_FROM, [to_address], msg.as_string())
        return True
    except Exception:
        return False
