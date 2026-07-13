"""
Tiny Razorpay wrapper. We deliberately avoid the official `razorpay`
python SDK to keep the dependency list short — the Orders API is a
simple authenticated REST call, and signature verification is just
one line of HMAC-SHA256.
"""
import hmac
import hashlib
import requests

import config

API_BASE = "https://api.razorpay.com/v1"


def _auth():
    return (config.RAZORPAY_KEY_ID, config.RAZORPAY_KEY_SECRET)


def is_configured():
    return bool(config.RAZORPAY_KEY_ID and config.RAZORPAY_KEY_SECRET)


def create_order(amount_rupees, receipt, notes=None):
    """
    Creates a Razorpay order. Amount must be sent to Razorpay in paise.
    Returns the parsed JSON response (contains 'id' = razorpay_order_id).
    """
    payload = {
        "amount": int(round(amount_rupees * 100)),
        "currency": "INR",
        "receipt": receipt,
        "notes": notes or {},
    }
    resp = requests.post(f"{API_BASE}/orders", json=payload, auth=_auth(), timeout=15)
    resp.raise_for_status()
    return resp.json()


def verify_payment_signature(razorpay_order_id, razorpay_payment_id, razorpay_signature):
    """
    Recomputes the HMAC-SHA256 signature Razorpay sends back after a
    successful checkout and compares it to what the browser gave us.
    This is what proves the payment wasn't tampered with client-side.
    """
    body = f"{razorpay_order_id}|{razorpay_payment_id}"
    expected = hmac.new(
        config.RAZORPAY_KEY_SECRET.encode("utf-8"),
        body.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, razorpay_signature)
