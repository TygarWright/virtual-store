"""
Very small data layer on top of sqlite3 (built into Python — no ORM,
no extra dependency, easy to back up: it's a single .db file).
"""
import sqlite3
import os
import secrets
import string
from datetime import datetime
from werkzeug.security import generate_password_hash

import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS sections (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    title    TEXT NOT NULL,
    content  TEXT NOT NULL DEFAULT '',
    position INTEGER NOT NULL DEFAULT 0,
    visible  INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS products (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    name              TEXT NOT NULL,
    slug              TEXT UNIQUE NOT NULL,
    short_description TEXT NOT NULL DEFAULT '',
    description       TEXT NOT NULL DEFAULT '',
    price             INTEGER NOT NULL DEFAULT 0,
    category          TEXT NOT NULL DEFAULT '',
    active            INTEGER NOT NULL DEFAULT 1,
    position          INTEGER NOT NULL DEFAULT 0,
    created_at        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS product_images (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    filename   TEXT NOT NULL,
    position   INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS orders (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    order_ref          TEXT UNIQUE NOT NULL,
    product_id         INTEGER REFERENCES products(id),
    product_name       TEXT NOT NULL,
    customer_name      TEXT NOT NULL,
    customer_email     TEXT NOT NULL,
    customer_phone     TEXT NOT NULL DEFAULT '',
    amount             INTEGER NOT NULL,
    coupon_code        TEXT NOT NULL DEFAULT '',
    discount_amount    INTEGER NOT NULL DEFAULT 0,
    razorpay_order_id  TEXT,
    razorpay_payment_id TEXT,
    razorpay_signature TEXT,
    status             TEXT NOT NULL DEFAULT 'created',
    delivery_message   TEXT NOT NULL DEFAULT '',
    created_at         TEXT NOT NULL,
    paid_at            TEXT,
    delivered_at       TEXT
);

CREATE TABLE IF NOT EXISTS admin_users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS coupons (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    code           TEXT UNIQUE NOT NULL,
    discount_type  TEXT NOT NULL DEFAULT 'percent',   -- 'percent' or 'flat'
    discount_value INTEGER NOT NULL DEFAULT 0,
    active         INTEGER NOT NULL DEFAULT 1,
    usage_limit    INTEGER,                            -- NULL = unlimited
    used_count     INTEGER NOT NULL DEFAULT 0,
    created_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS testimonials (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_name TEXT NOT NULL,
    quote         TEXT NOT NULL,
    rating        INTEGER NOT NULL DEFAULT 5,
    position      INTEGER NOT NULL DEFAULT 0,
    visible       INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS faqs (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    question TEXT NOT NULL,
    answer   TEXT NOT NULL,
    position INTEGER NOT NULL DEFAULT 0,
    visible  INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS newsletter_subscribers (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    email      TEXT UNIQUE NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS order_items (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id     INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    product_id   INTEGER REFERENCES products(id),
    product_name TEXT NOT NULL,
    unit_price   INTEGER NOT NULL,
    quantity     INTEGER NOT NULL DEFAULT 1,
    line_total   INTEGER NOT NULL
);
"""

# Safe, additive migrations for databases created before these columns existed.
# Each entry is (table, column, "ALTER TABLE ... " statement). Errors from
# already-applied migrations (duplicate column) are ignored on purpose.
MIGRATIONS = [
    "ALTER TABLE products ADD COLUMN category TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE orders ADD COLUMN coupon_code TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE orders ADD COLUMN discount_amount INTEGER NOT NULL DEFAULT 0",
]

DEFAULT_SETTINGS = {
    "site_name": "Atelier",
    "site_tagline": "Curated digital goods, delivered with care.",
    "hero_title": "Exceptional digital products,\ncarefully made.",
    "hero_subtitle": "A small, considered catalogue — nothing mass produced.",
    "about_title": "About Us",
    "about_content": "We are a small studio creating premium digital products. "
                      "Every item in our catalogue is reviewed personally before "
                      "it reaches you.\n\nWrite your own story here from the Admin Panel.",
    "contact_email": "hello@example.com",
    "contact_phone": "+91 00000 00000",
    "footer_text": "Crafted with care.",
    "meta_description": "A curated catalogue of premium digital products.",
    "currency_symbol": "₹",
}


def get_db():
    os.makedirs(os.path.dirname(config.DB_PATH) or ".", exist_ok=True)
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    conn = get_db()
    conn.executescript(SCHEMA)

    for stmt in MIGRATIONS:
        try:
            conn.execute(stmt)
        except sqlite3.OperationalError:
            pass  # column already exists — already migrated

    # Seed default settings (only keys that don't already exist)
    for key, value in DEFAULT_SETTINGS.items():
        conn.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (key, value)
        )

    # Seed a default admin user, only if none exists yet
    existing = conn.execute("SELECT COUNT(*) AS c FROM admin_users").fetchone()["c"]
    if existing == 0:
        conn.execute(
            "INSERT INTO admin_users (username, password_hash) VALUES (?, ?)",
            (
                config.DEFAULT_ADMIN_USERNAME,
                generate_password_hash(config.DEFAULT_ADMIN_PASSWORD),
            ),
        )

    conn.commit()
    conn.close()


def new_order_ref():
    """Short, human-friendly, unambiguous order reference e.g. ORD-7F3K9Q."""
    alphabet = string.ascii_uppercase + string.digits
    alphabet = alphabet.replace("O", "").replace("0", "").replace("I", "").replace("1", "")
    suffix = "".join(secrets.choice(alphabet) for _ in range(6))
    return f"ORD-{suffix}"


def now():
    return datetime.utcnow().isoformat()
