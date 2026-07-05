import os
import re
import secrets
import smtplib
from email.mime.text import MIMEText
from functools import wraps

from flask import session, redirect, url_for, request, abort
from werkzeug.utils import secure_filename
from PIL import Image

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

def get_csrf_token():
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_hex(16)
    return session["csrf_token"]


def check_csrf():
    token = request.form.get("csrf_token", "")
    if not token or token != session.get("csrf_token"):
        abort(400, description="Your session expired — please try again.")


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

    os.makedirs(config.UPLOAD_FOLDER, exist_ok=True)
    ext = file_storage.filename.rsplit(".", 1)[-1].lower()
    filename = secure_filename(f"{secrets.token_hex(8)}.{ext}")
    path = os.path.join(config.UPLOAD_FOLDER, filename)

    image = Image.open(file_storage)
    image = image.convert("RGB") if image.mode not in ("RGB", "RGBA") else image

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
