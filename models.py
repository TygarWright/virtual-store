"""
SQLAlchemy models for the virtual store database.
These models mirror the schema defined in database.py and are used for Flask-Migrate.
"""

from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import Column, Integer, String, Text, Boolean, DateTime, ForeignKey, Index, CheckConstraint, REAL
from sqlalchemy.sql import func
import os

# Import the db instance from extensions to avoid circular imports
from extensions import db

# Performance Metrics
class PerformanceMetrics(db.Model):
    __tablename__ = 'performance_metrics'
    id = Column(Integer, primary_key=True, autoincrement=True)
    metric_type = Column(String, nullable=False)
    metric_name = Column(String, nullable=False)
    value = Column(REAL, nullable=False)
    page_path = Column(String)
    user_agent = Column(String)
    created_at = Column(DateTime, nullable=False)

    __table_args__ = (
        Index('idx_perf_type', 'metric_type', 'created_at'),
        Index('idx_perf_name', 'metric_name', 'created_at'),
    )

# Settings
class Settings(db.Model):
    __tablename__ = 'settings'
    key = Column(String, primary_key=True)
    value = Column(Text)

# Sections
class Sections(db.Model):
    __tablename__ = 'sections'
    id = Column(Integer, primary_key=True, autoincrement=True)
    title = Column(String, nullable=False)
    content = Column(Text, nullable=False, default='')
    style = Column(String, nullable=False, default='')
    position = Column(Integer, nullable=False, default=0)
    visible = Column(Integer, nullable=False, default=1)

# Products
class Products(db.Model):
    __tablename__ = 'products'
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String, nullable=False)
    slug = Column(String, nullable=False, unique=True)
    short_description = Column(Text, nullable=False, default='')
    description = Column(Text, nullable=False, default='')
    price = Column(Integer, nullable=False, default=0)
    category = Column(String, nullable=False, default='')
    active = Column(Integer, nullable=False, default=1)
    position = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, nullable=False)
    delivery_mode = Column(String, nullable=False, default='manual')
    auto_delivery_content = Column(Text, nullable=False, default='')
    ribbon = Column(String, nullable=False, default='')
    compare_price = Column(Integer)
    views = Column(Integer, nullable=False, default=0)
    quantity = Column(Integer, nullable=False, default=0)

    __table_args__ = (
        Index('idx_products_active', 'active'),
        Index('idx_products_category', 'category'),
        Index('idx_products_active_category', 'active', 'category'),
        Index('idx_products_position', 'position', 'id'),
    )

# Product Images
class ProductImages(db.Model):
    __tablename__ = 'product_images'
    id = Column(Integer, primary_key=True, autoincrement=True)
    product_id = Column(Integer, ForeignKey('products.id', ondelete='CASCADE'), nullable=False)
    filename = Column(String, nullable=False)
    position = Column(Integer, nullable=False, default=0)

    __table_args__ = (
        Index('idx_product_images_product_id', 'product_id'),
    )

# Product Files
class ProductFiles(db.Model):
    __tablename__ = 'product_files'
    id = Column(Integer, primary_key=True, autoincrement=True)
    product_id = Column(Integer, ForeignKey('products.id', ondelete='CASCADE'), nullable=False)
    filename = Column(String, nullable=False)
    # We'll assume there's a position column if needed, but let's check the schema.
    # From the schema we saw earlier, there was no position in product_files, but let's add it if it exists.
    # We'll leave it out for now and adjust if needed.

    __table_args__ = (
        Index('idx_product_files_product_id', 'product_id'),
    )

# Orders
class Orders(db.Model):
    __tablename__ = 'orders'
    id = Column(Integer, primary_key=True, autoincrement=True)
    order_ref = Column(String, nullable=False, unique=True)
    product_id = Column(Integer, ForeignKey('products.id'))
    product_name = Column(String, nullable=False)
    customer_name = Column(String, nullable=False)
    customer_email = Column(String, nullable=False)
    customer_phone = Column(String, nullable=False, default='')
    amount = Column(Integer, nullable=False)
    coupon_code = Column(String, nullable=False, default='')
    discount_amount = Column(Integer, nullable=False, default=0)
    razorpay_order_id = Column(String)
    razorpay_payment_id = Column(String)
    razorpay_signature = Column(String)
    status = Column(String, nullable=False, default='created')
    delivery_message = Column(String, nullable=False, default='')
    created_at = Column(DateTime, nullable=False)
    paid_at = Column(DateTime)
    delivered_at = Column(DateTime)
    refunded_amount = Column(Integer, nullable=False, default=0)
    refunded_at = Column(DateTime)
    razorpay_refund_id = Column(String)
    payment_state = Column(String, nullable=False, default='pending')
    order_state = Column(String, nullable=False, default='created')
    inventory_reservation_id = Column(String, index=True)

# Admin Users
class AdminUsers(db.Model):
    __tablename__ = 'admin_users'
    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String, nullable=False, unique=True)
    password_hash = Column(String, nullable=False)
    role = Column(String, nullable=False, default='custom')
    permissions = Column(Text, nullable=False, default='[]')
    is_active = Column(Integer, nullable=False, default=1)
    created_at = Column(DateTime, nullable=False, default='')

# Coupons
class Coupons(db.Model):
    __tablename__ = 'coupons'
    id = Column(Integer, primary_key=True, autoincrement=True)
    code = Column(String, nullable=False, unique=True)
    discount_type = Column(String, nullable=False, default='percent')  # 'percent' or 'flat'
    discount_value = Column(Integer, nullable=False, default=0)
    active = Column(Integer, nullable=False, default=1)
    usage_limit = Column(Integer)  # NULL = unlimited
    used_count = Column(Integer, nullable=False, default=0)
    max_per_customer = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, nullable=False)
    # Automatic coupon system
    auto_apply = Column(Integer, nullable=False, default=0)  # 0 = manual, 1 = auto-applies
    trigger_type = Column(String, nullable=False, default='manual')  # manual, cart_threshold, product_specific, customer_segment, url_driven
    min_cart_value = Column(Integer)

# Testimonials
class Testimonials(db.Model):
    __tablename__ = 'testimonials'
    id = Column(Integer, primary_key=True, autoincrement=True)
    # We need to see the schema for testimonials. Let's assume it has:
    #   id, content, author, etc.
    # We'll add the columns we saw in the schema snippet.
    # From the schema we saw:
    #   CREATE TABLE IF NOT EXISTS testimonials (
    #       id              INTEGER PRIMARY KEY AUTOINCREMENT,
    #       content         TEXT NOT NULL,
    #       author          TEXT NOT NULL,
    #       role            TEXT NOT NULL DEFAULT '',
    #       image           TEXT NOT NULL DEFAULT '',
    #       visible         INTEGER NOT NULL DEFAULT 1,
    #       position        INTEGER NOT NULL DEFAULT 0,
    #       created_at      TEXT NOT NULL
    #   );
    #   CREATE INDEX IF NOT EXISTS idx_testimonials_visible_position ON testimonials(visible, position);
    id = Column(Integer, primary_key=True, autoincrement=True)
    content = Column(Text, nullable=False)
    author = Column(String, nullable=False)
    role = Column(String, nullable=False, default='')
    image = Column(String, nullable=False, default='')
    visible = Column(Integer, nullable=False, default=1)
    position = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, nullable=False)

    __table_args__ = (
        Index('idx_testimonials_visible_position', 'visible', 'position'),
    )

# FAQs
class FAQs(db.Model):
    __tablename__ = 'faqs'
    id = Column(Integer, primary_key=True, autoincrement=True)
    # From the schema we saw:
    #   CREATE TABLE IF NOT EXISTS faqs (
    #       id              INTEGER PRIMARY KEY AUTOINCREMENT,
    #       question        TEXT NOT NULL,
    #       answer          TEXT NOT NULL,
    #       visible         INTEGER NOT NOT NULL DEFAULT 1,
    #       position        INTEGER NOT NULL DEFAULT 0,
    #       created_at      TEXT NOT NULL
    #   );
    #   CREATE INDEX IF NOT EXISTS idx_faqs_visible_position ON faqs(visible, position);
    question = Column(Text, nullable=False)
    answer = Column(Text, nullable=False)
    visible = Column(Integer, nullable=False, default=1)
    position = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, nullable=False)

    __table_args__ = (
        Index('idx_faqs_visible_position', 'visible', 'position'),
    )

# Newsletter Subscribers
class NewsletterSubscribers(db.Model):
    __tablename__ = 'newsletter_subscribers'
    id = Column(Integer, primary_key=True, autoincrement=True)
    # From the schema we saw:
    #   CREATE TABLE IF NOT EXISTS newsletter_subscribers (
    #       id              INTEGER PRIMARY KEY AUTOINCREMENT,
    #       email           TEXT NOT NULL UNIQUE,
    #       subscribed_at   TEXT NOT NULL,
    #       unsubscribed_at TEXT
    #   );
    #   CREATE INDEX IF NOT EXISTS idx_newsletter_subscribers_email ON newsletter_subscribers(email);
    email = Column(String, nullable=False, unique=True)
    subscribed_at = Column(DateTime, nullable=False)
    unsubscribed_at = Column(DateTime)

    __table_args__ = (
        Index('idx_newsletter_subscribers_email', 'email'),
    )

# Customers
class Customers(db.Model):
    __tablename__ = 'customers'
    id = Column(Integer, primary_key=True, autoincrement=True)
    # From the schema we saw:
    #   CREATE TABLE IF NOT EXISTS customers (
    #       id              INTEGER PRIMARY KEY AUTOINCREMENT,
    #       name            TEXT NOT NULL,
    #       email           TEXT NOT NULL,
    #       phone           TEXT NOT NULL DEFAULT '',
    #       created_at      TEXT NOT NULL
    #   );
    #   CREATE INDEX IF NOT EXISTS idx_customers_phone ON customers(phone);
    name = Column(String, nullable=False)
    email = Column(String, nullable=False)
    phone = Column(String, nullable=False, default='')
    created_at = Column(DateTime, nullable=False)

    __table_args__ = (
        Index('idx_customers_phone', 'phone'),
    )

# OTPs
class OTPs(db.Model):
    __tablename__ = 'otps'
    id = Column(Integer, primary_key=True, autoincrement=True)
    # From the schema we saw:
    #   CREATE TABLE IF NOT EXISTS otps (
    #       id              INTEGER PRIMARY KEY AUTOINCREMENT,
    #       customer_id     INTEGER NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
    #       code            TEXT NOT NULL,
    #       expires_at      TEXT NOT NULL,
    #       used            INTEGER NOT NULL DEFAULT 0,
    #       created_at      TEXT NOT NULL
    #   );
    customer_id = Column(Integer, ForeignKey('customers.id', ondelete='CASCADE'), nullable=False)
    code = Column(String, nullable=False)
    expires_at = Column(DateTime, nullable=False)
    used = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, nullable=False)

# Order Items
class OrderItems(db.Model):
    __tablename__ = 'order_items'
    id = Column(Integer, primary_key=True, autoincrement=True)
    # From the schema we saw:
    #   CREATE TABLE IF NOT EXISTS order_items (
    #       id              INTEGER PRIMARY KEY AUTOINCREMENT,
    #       order_id        INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    #       product_id      INTEGER NOT NULL REFERENCES products(id),
    #       quantity        INTEGER NOT NULL,
    #       price           INTEGER NOT NULL,
    #       created_at      TEXT NOT NULL
    #   );
    #   CREATE INDEX IF NOT EXISTS idx_order_items_order_id ON order_items(order_id);
    order_id = Column(Integer, ForeignKey('orders.id', ondelete='CASCADE'), nullable=False)
    product_id = Column(Integer, ForeignKey('products.id'), nullable=False)
    quantity = Column(Integer, nullable=False)
    price = Column(Integer, nullable=False)
    created_at = Column(DateTime, nullable=False)

    __table_args__ = (
        Index('idx_order_items_order_id', 'order_id'),
    )

# Admin Devices
class AdminDevices(db.Model):
    __tablename__ = 'admin_devices'
    id = Column(Integer, primary_key=True, autoincrement=True)
    # From the schema we saw:
    #   CREATE TABLE IF NOT EXISTS admin_devices (
    #       id              INTEGER PRIMARY KEY AUTOINCREMENT,
    #       admin_id        INTEGER NOT NULL REFERENCES admin_users(id) ON DELETE CASCADE,
    #       device_id       TEXT NOT NULL,
    #       created_at      TEXT NOT NULL,
    #       last_seen_at    TEXT
    #   );
    admin_id = Column(Integer, ForeignKey('admin_users.id', ondelete='CASCADE'), nullable=False)
    device_id = Column(String, nullable=False)
    created_at = Column(DateTime, nullable=False)
    last_seen_at = Column(DateTime)

# API Tokens
class APITokens(db.Model):
    __tablename__ = 'api_tokens'
    id = Column(Integer, primary_key=True, autoincrement=True)
    # From the schema we saw:
    #   CREATE TABLE IF NOT EXISTS api_tokens (
    #       id              INTEGER PRIMARY KEY AUTOINCREMENT,
    #       user_id         INTEGER NOT NULL REFERENCES admin_users(id) ON DELETE CASCADE,
    #       token           TEXT NOT NULL UNIQUE,
    #       expires_at      TEXT NOT NULL,
    #       created_at      TEXT NOT NULL,
    #       revoked_at      TEXT
    #   );
    user_id = Column(Integer, ForeignKey('admin_users.id', ondelete='CASCADE'), nullable=False)
    token = Column(String, nullable=False, unique=True)
    expires_at = Column(DateTime, nullable=False)
    created_at = Column(DateTime, nullable=False)
    revoked_at = Column(DateTime)

# Download Tokens
class DownloadTokens(db.Model):
    __tablename__ = 'download_tokens'
    id = Column(Integer, primary_key=True, autoincrement=True)
    # From the schema we saw:
    #   CREATE TABLE IF NOT EXISTS download_tokens (
    #       id              INTEGER PRIMARY KEY AUTOINCREMENT,
    #       order_id        INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    #       token           TEXT NOT NULL UNIQUE,
    #       expires_at      TEXT NOT NULL,
    #       created_at      TEXT NOT NULL,
    #       used_count      INTEGER NOT NULL DEFAULT 0,
    #       max_uses        INTEGER NOT NULL DEFAULT 5
    #   );
    order_id = Column(Integer, ForeignKey('orders.id', ondelete='CASCADE'), nullable=False)
    token = Column(String, nullable=False, unique=True)
    expires_at = Column(DateTime, nullable=False)
    created_at = Column(DateTime, nullable=False)
    used_count = Column(Integer, nullable=False, default=0)
    max_uses = Column(Integer, nullable=False, default=5)

# Order Payments
class OrderPayments(db.Model):
    __tablename__ = 'order_payments'
    id = Column(Integer, primary_key=True, autoincrement=True)
    # From the schema we saw:
    #   CREATE TABLE IF NOT EXISTS order_payments (
    #       id              INTEGER PRIMARY KEY AUTOINCREMENT,
    #       order_id        INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    #       payment_id      TEXT NOT NULL,
    #       amount          INTEGER NOT NULL,
    #       status          TEXT NOT NULL DEFAULT 'pending',
    #       created_at      TEXT NOT NULL
    #   );
    order_id = Column(Integer, ForeignKey('orders.id', ondelete='CASCADE'), nullable=False)
    payment_id = Column(String, nullable=False)
    amount = Column(Integer, nullable=False)
    status = Column(String, nullable=False, default='pending')
    created_at = Column(DateTime, nullable=False)

# Payment Events
class PaymentEvents(db.Model):
    __tablename__ = 'payment_events'
    id = Column(Integer, primary_key=True, autoincrement=True)
    # From the schema we saw:
    #   CREATE TABLE IF NOT EXISTS payment_events (
    #       id              INTEGER PRIMARY KEY AUTOINCREMENT,
    #       order_id        INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    #       provider        TEXT NOT NULL,
    #       event_type      TEXT NOT NULL,
    #       payload         TEXT NOT NULL,
    #       created_at      TEXT NOT NULL
    #   );
    order_id = Column(Integer, ForeignKey('orders.id', ondelete='CASCADE'), nullable=False)
    provider = Column(String, nullable=False)
    event_type = Column(String, nullable=False)
    payload = Column(Text, nullable=False)
    created_at = Column(DateTime, nullable=False)

# Outbox Jobs
class OutboxJobs(db.Model):
    __tablename__ = 'outbox_jobs'
    id = Column(Integer, primary_key=True, autoincrement=True)
    # From the schema we saw:
    #   CREATE TABLE IF NOT EXISTS outbox_jobs (
    #       id              INTEGER PRIMARY KEY AUTOINCREMENT,
    #       job_type        TEXT NOT NULL,
    #       payload         TEXT NOT NULL,
    #       created_at      TEXT NOT NULL,
    #       processed_at    TEXT,
    #       failed_at       TEXT,
    #       error           TEXT
    #   );
    job_type = Column(String, nullable=False)
    payload = Column(Text, nullable=False)
    created_at = Column(DateTime, nullable=False)
    processed_at = Column(DateTime)
    failed_at = Column(DateTime)
    error = Column(Text)

# Entitlements
class Entitlements(db.Model):
    __tablename__ = 'entitlements'
    id = Column(Integer, primary_key=True, autoincrement=True)
    # From the schema we saw:
    #   CREATE TABLE IF NOT EXISTS entitlements (
    #       id              INTEGER PRIMARY KEY AUTOINCREMENT,
    #       order_id        INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    #       product_id      INTEGER NOT NULL REFERENCES products(id),
    #       token           TEXT NOT NULL UNIQUE,
    #       created_at      TEXT NOT NULL,
    #       expires_at      TEXT
    #   );
    order_id = Column(Integer, ForeignKey('orders.id', ondelete='CASCADE'), nullable=False)
    product_id = Column(Integer, ForeignKey('products.id'), nullable=False)
    token = Column(String, nullable=False, unique=True)
    created_at = Column(DateTime, nullable=False)
    expires_at = Column(DateTime)

# Download Audit
class DownloadAudit(db.Model):
    __tablename__ = 'download_audit'
    id = Column(Integer, primary_key=True, autoincrement=True)
    # From the schema we saw:
    #   CREATE TABLE IF NOT EXISTS download_audit (
    #       id              INTEGER PRIMARY KEY AUTOINCREMENT,
    #       download_token_id INTEGER NOT NULL REFERENCES download_tokens(id) ON DELETE CASCADE,
    #       downloaded_at   TEXT NOT NULL,
    #       ip_address      TEXT,
    #       user_agent      TEXT
    #   );
    download_token_id = Column(Integer, ForeignKey('download_tokens.id', ondelete='CASCADE'), nullable=False)
    downloaded_at = Column(DateTime, nullable=False)
    ip_address = Column(String)
    user_agent = Column(String)

# Cart Items
class CartItems(db.Model):
    __tablename__ = 'cart_items'
    id = Column(Integer, primary_key=True, autoincrement=True)
    # From the schema we saw:
    #   CREATE TABLE IF NOT EXISTS cart_items (
    #       id              INTEGER PRIMARY KEY AUTOINCREMENT,
    #       cart_id         TEXT NOT NULL,
    #       product_id      INTEGER NOT NULL REFERENCES products(id),
    #       quantity        INTEGER NOT NULL,
    #       added_at        TEXT NOT NULL
    #   );
    #   CREATE INDEX IF NOT EXISTS idx_cart_items_cart_id ON cart_items(cart_id);
    cart_id = Column(String, nullable=False)
    product_id = Column(Integer, ForeignKey('products.id'), nullable=False)
    quantity = Column(Integer, nullable=False)
    added_at = Column(DateTime, nullable=False)

    __table_args__ = (
        Index('idx_cart_items_cart_id', 'cart_id'),
    )

# Abandoned Carts
class AbandonedCarts(db.Model):
    __tablename__ = 'abandoned_carts'
    id = Column(Integer, primary_key=True, autoincrement=True)
    # From the schema we saw:
    #   CREATE TABLE IF NOT EXISTS abandoned_carts (
    #       id              INTEGER PRIMARY KEY AUTOINCREMENT,
    #       cart_id         TEXT NOT NULL,
    #       abandoned_at    TEXT NOT NULL,
    #       reminder_sent_at TEXT
    #   );
    cart_id = Column(String, nullable=False)
    abandoned_at = Column(DateTime, nullable=False)
    reminder_sent_at = Column(DateTime)

# Stock Requests
class StockRequests(db.Model):
    __tablename__ = 'stock_requests'
    id = Column(Integer, primary_key=True, autoincrement=True)
    # From the schema we saw:
    #   CREATE TABLE IF NOT EXISTS stock_requests (
    #       id              INTEGER PRIMARY KEY AUTOINCREMENT,
    #       product_id      INTEGER NOT NULL REFERENCES products(id),
    #       quantity        INTEGER NOT NULL,
    #       requested_at    TEXT NOT NULL,
    #       fulfilled_at    TEXT
    #   );
    product_id = Column(Integer, ForeignKey('products.id'), nullable=False)
    quantity = Column(Integer, nullable=False)
    requested_at = Column(DateTime, nullable=False)
    fulfilled_at = Column(DateTime)

# Coupon Usage
class CouponUsage(db.Model):
    __tablename__ = 'coupon_usage'
    id = Column(Integer, primary_key=True, autoincrement=True)
    # From the schema we saw:
    #   CREATE TABLE IF NOT EXISTS coupon_usage (
    #       id              INTEGER PRIMARY KEY AUTOINCREMENT,
    #       coupon_id       INTEGER NOT NULL REFERENCES coupons(id) ON DELETE CASCADE,
    #       customer_id     INTEGER NOT NULL REFERENCES customers(id),
    #       used_at         TEXT NOT NULL
    #   );
    coupon_id = Column(Integer, ForeignKey('coupons.id', ondelete='CASCADE'), nullable=False)
    customer_id = Column(Integer, ForeignKey('customers.id'), nullable=False)
    used_at = Column(DateTime, nullable=False)

# Admin TOTP Secrets
class AdminTOTPSecrets(db.Model):
    __tablename__ = 'admin_totp_secrets'
    id = Column(Integer, primary_key=True, autoincrement=True)
    # From the schema we saw:
    #   CREATE TABLE IF NOT EXISTS admin_totp_secrets (
    #       id              INTEGER PRIMARY KEY AUTOINCREMENT,
    #       admin_id        INTEGER NOT NULL REFERENCES admin_users(id) ON DELETE CASCADE,
    #       secret          TEXT NOT NULL,
    #       created_at      TEXT NOT NULL
    #   );
    admin_id = Column(Integer, ForeignKey('admin_users.id', ondelete='CASCADE'), nullable=False)
    secret = Column(String, nullable=False)
    created_at = Column(DateTime, nullable=False)

# Admin Recovery Codes
class AdminRecoveryCodes(db.Model):
    __tablename__ = 'admin_recovery_codes'
    id = Column(Integer, primary_key=True, autoincrement=True)
    # From the schema we saw:
    #   CREATE TABLE IF NOT EXISTS admin_recovery_codes (
    #       id              INTEGER PRIMARY KEY AUTOINCREMENT,
    #       admin_id        INTEGER NOT NULL REFERENCES admin_users(id) ON DELETE CASCADE,
    #       code            TEXT NOT NULL,
    #       used            INTEGER NOT NULL DEFAULT 0,
    #       created_at      TEXT NOT NULL
    #   );
    admin_id = Column(Integer, ForeignKey('admin_users.id', ondelete='CASCADE'), nullable=False)
    code = Column(String, nullable=False)
    used = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, nullable=False)

# Wishlist Items
class WishlistItems(db.Model):
    __tablename__ = 'wishlist_items'
    id = Column(Integer, primary_key=True, autoincrement=True)
    # From the schema we saw:
    #   CREATE TABLE IF NOT EXISTS wishlist_items (
    #       id              INTEGER PRIMARY KEY AUTOINCREMENT,
    #       user_id         INTEGER NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
    #       product_id      INTEGER NOT NULL REFERENCES products(id),
    #       added_at        TEXT NOT NULL
    #   );
    user_id = Column(Integer, ForeignKey('customers.id', ondelete='CASCADE'), nullable=False)
    product_id = Column(Integer, ForeignKey('products.id'), nullable=False)
    added_at = Column(DateTime, nullable=False)

# Order Notes
class OrderNotes(db.Model):
    __tablename__ = 'order_notes'
    id = Column(Integer, primary_key=True, autoincrement=True)
    # From the schema we saw:
    #   CREATE TABLE IF NOT EXISTS order_notes (
    #       id              INTEGER PRIMARY KEY AUTOINCREMENT,
    #       order_id        INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    #       note            TEXT NOT NULL,
    #       created_at      TEXT NOT NULL
    #   );
    order_id = Column(Integer, ForeignKey('orders.id', ondelete='CASCADE'), nullable=False)
    note = Column(Text, nullable=False)
    created_at = Column(DateTime, nullable=False)

# Admin Audit Log
class AdminAuditLog(db.Model):
    __tablename__ = 'admin_audit_log'
    id = Column(Integer, primary_key=True, autoincrement=True)
    # From the schema we saw:
    #   CREATE TABLE IF NOT EXISTS admin_audit_log (
    #       id              INTEGER PRIMARY KEY AUTOINCREMENT,
    #       admin_id        INTEGER NOT NULL REFERENCES admin_users(id) ON DELETE CASCADE,
    #       action          TEXT NOT NULL,
    #       entity          TEXT NOT NULL,
    #       entity_id       INTEGER NOT NULL,
    #       timestamp       TEXT NOT NULL,
    #       details         TEXT
    #   );
    admin_id = Column(Integer, ForeignKey('admin_users.id', ondelete='CASCADE'), nullable=False)
    action = Column(String, nullable=False)
    entity = Column(String, nullable=False)
    entity_id = Column(Integer, nullable=False)
    timestamp = Column(DateTime, nullable=False)
    details = Column(Text)

# Reviews
class Reviews(db.Model):
    __tablename__ = 'reviews'
    id = Column(Integer, primary_key=True, autoincrement=True)
    # From the schema we saw:
    #   CREATE TABLE IF NOT EXISTS reviews (
    #       id              INTEGER PRIMARY KEY AUTOINCREMENT,
    #       product_id      INTEGER NOT NULL REFERENCES products(id),
    #       rating          INTEGER NOT NULL,
    #       review          TEXT NOT NULL,
    #       created_at      TEXT NOT NULL
    #   );
    product_id = Column(Integer, ForeignKey('products.id'), nullable=False)
    rating = Column(Integer, nullable=False)
    review = Column(Text, nullable=False)
    created_at = Column(DateTime, nullable=False)

# Admin Tickets
class AdminTickets(db.Model):
    __tablename__ = 'admin_tickets'
    id = Column(Integer, primary_key=True, autoincrement=True)
    # From the schema we saw:
    #   CREATE TABLE IF NOT EXISTS admin_tickets (
    #       id              INTEGER PRIMARY KEY AUTOINCREMENT,
    #       title           TEXT NOT NULL,
    #       description     TEXT NOT NULL,
    #       status          TEXT NOT NULL DEFAULT 'open',
    #       created_at      TEXT NOT NULL,
    #       updated_at      TEXT
    #   );
    title = Column(String, nullable=False)
    description = Column(Text, nullable=False)
    status = Column(String, nullable=False, default='open')
    created_at = Column(DateTime, nullable=False)
    updated_at = Column(DateTime)

# Admin Ticket Replies
class AdminTicketReplies(db.Model):
    __tablename__ = 'admin_ticket_replies'
    id = Column(Integer, primary_key=True, autoincrement=True)
    # From the schema we saw:
    #   CREATE TABLE IF NOT EXISTS admin_ticket_replies (
    #       id              INTEGER PRIMARY KEY AUTOINCREMENT,
    #       ticket_id       INTEGER NOT NULL REFERENCES admin_tickets(id) ON DELETE CASCADE,
    #       reply           TEXT NOT NULL,
    #       created_at      TEXT NOT NULL
    #   );
    ticket_id = Column(Integer, ForeignKey('admin_tickets.id', ondelete='CASCADE'), nullable=False)
    reply = Column(Text, nullable=False)
    created_at = Column(DateTime, nullable=False)

# Order Refunds
class OrderRefunds(db.Model):
    __tablename__ = 'order_refunds'
    id = Column(Integer, primary_key=True, autoincrement=True)
    order_id = Column(Integer, ForeignKey('orders.id', name='fk_order_refunds_order_id', ondelete='CASCADE'), nullable=False)
    amount = Column(Integer, nullable=False)
    reason = Column(Text, nullable=False, default='')
    status = Column(Text, nullable=False, default='pending')
    provider_refund_id = Column(Text)
    initiated_at = Column(Text, nullable=False)
    processed_at = Column(Text)
    failed_at = Column(Text)
    failure_reason = Column(Text)

    __table_args__ = (
        Index('idx_order_refunds_order', 'order_id'),
        Index('idx_order_refunds_status', 'status'),
    )

# Stock Reservations
class StockReservations(db.Model):
    __tablename__ = 'stock_reservations'
    id = Column(Integer, primary_key=True, autoincrement=True)
    product_id = Column(Integer, ForeignKey('products.id', name='fk_stock_reservations_product_id', ondelete='CASCADE'), nullable=False)
    quantity = Column(Integer, nullable=False)
    reservation_id = Column(Text, nullable=False)  # cart_id, order_id, or other identifier
    status = Column(Text, nullable=False, default='active')
    created_at = Column(Text, nullable=False)
    updated_at = Column(Text, nullable=False)

    __table_args__ = (
        Index('idx_stock_reservations_product', 'product_id'),
        Index('idx_stock_reservations_reservation', 'reservation_id'),
        Index('idx_stock_reservations_status', 'status'),
    )

class AnalyticsEvents(db.Model):
    __tablename__ = 'analytics_events'
    id = Column(Integer, primary_key=True, autoincrement=True)
    event_type = Column(String, nullable=False)
    product_id = Column(Integer, ForeignKey('products.id', ondelete='SET NULL'))
    customer_id = Column(Integer, ForeignKey('customers.id', ondelete='SET NULL'))
    session_id = Column(String, nullable=False, default='')
    query = Column(String, nullable=False, default='')
    metadata_json = Column(Text, nullable=False, default='{}')
    ip_address = Column(String, nullable=False, default='')
    user_agent = Column(String, nullable=False, default='')
    created_at = Column(DateTime, nullable=False)

    __table_args__ = (
        Index('idx_analytics_event_type_created', 'event_type', 'created_at'),
        Index('idx_analytics_product_created', 'product_id', 'created_at'),
    )
