import os
import sqlite3
import unittest

os.environ.setdefault("SECRET_KEY", "unit-test-secret")
os.environ.setdefault("ALLOW_TEST_GATEWAY", "true")

import database as db
from payment.refund import initiate_refund, process_refund


class RefundWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(db.SCHEMA)
        for stmt in db.MIGRATIONS:
            try:
                self.conn.execute(stmt)
            except Exception:
                pass
        for stmt in db.SCHEMA_EXTRA.split(";"):
            stmt = stmt.strip()
            if stmt:
                try:
                    self.conn.execute(stmt)
                except Exception:
                    pass
        self.conn.execute(
            "INSERT INTO products (name, slug, created_at) VALUES (?, ?, ?)",
            ("Test", "test", "now"),
        )
        self.conn.execute(
            "INSERT INTO orders (order_ref, product_name, customer_name, customer_email, amount, status, payment_state, order_state, razorpay_payment_id, created_at) VALUES (?, ?, ?, ?, ?, 'paid', 'captured', 'paid', ?, ?)",
            ("ORD-REFUND", "Test", "Buyer", "buyer@example.com", 100, "test_payment_ORD-REFUND" , "now"),
        )
        order_id = self.conn.execute("SELECT id FROM orders").fetchone()[0]
        self.conn.execute(
            "INSERT INTO order_payments (order_id, provider, provider_payment_id, status, amount, currency, created_at, updated_at) VALUES (?, 'test', ?, 'captured', ?, 'INR', 'now', 'now')",
            (order_id, "test_payment_ORD-REFUND", 10000),
        )
        self.conn.commit()
        self.order_id = order_id

    def tearDown(self):
        self.conn.close()

    def test_full_refund_is_provider_backed_and_idempotent(self):
        intent = initiate_refund(self.conn, self.order_id, amount=100, reason="test")
        self.assertTrue(intent["success"])
        first = process_refund(self.conn, intent["refund_id"])
        second = process_refund(self.conn, intent["refund_id"])
        self.assertEqual(first["status"], "processed")
        self.assertEqual(second["status"], "processed")
        order = self.conn.execute("SELECT status, refunded_amount, payment_state FROM orders WHERE id=?", (self.order_id,)).fetchone()
        self.assertEqual((order["status"], order["refunded_amount"], order["payment_state"]), ("refunded", 100, "refunded"))

    def test_partial_refund_does_not_mark_full_refund(self):
        intent = initiate_refund(self.conn, self.order_id, amount=30)
        result = process_refund(self.conn, intent["refund_id"])
        self.assertEqual(result["status"], "processed")
        order = self.conn.execute("SELECT status, refunded_amount FROM orders WHERE id=?", (self.order_id,)).fetchone()
        self.assertEqual((order["status"], order["refunded_amount"]), ("paid", 30))

    def test_excess_refund_is_rejected(self):
        intent = initiate_refund(self.conn, self.order_id, amount=101)
        self.assertFalse(intent["success"])


if __name__ == "__main__":
    unittest.main()
