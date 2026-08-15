import sqlite3
import tempfile
from pathlib import Path
import unittest

import database as db
from payment.inventory import reserve_stock, reserve_stock_batch, release_stock, commit_stock
from titan_db_tools import backup, restore, verify


class Phase3And6FoundationTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
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
        for stmt in db.INDEXES:
            try:
                self.conn.execute(stmt)
            except Exception:
                pass
        self.conn.execute(
            "INSERT INTO products (name, slug, quantity, created_at) VALUES (?, ?, ?, ?)",
            ("Test", "test", 5, "now"),
        )
        self.product_id = self.conn.execute("SELECT id FROM products").fetchone()[0]
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def test_last_unit_reservation_is_not_double_reserved(self):
        self.assertTrue(reserve_stock(self.conn, self.product_id, 5, "ORD-A"))
        self.assertFalse(reserve_stock(self.conn, self.product_id, 1, "ORD-B"))
        self.conn.commit()
        row = self.conn.execute("SELECT SUM(quantity) AS q FROM stock_reservations WHERE status='active'").fetchone()
        self.assertEqual(row[0], 5)

    def test_reservation_commit_decrements_and_is_idempotent(self):
        self.assertTrue(reserve_stock(self.conn, self.product_id, 2, "ORD-A"))
        self.assertTrue(commit_stock(self.conn, "ORD-A"))
        self.assertTrue(commit_stock(self.conn, "ORD-A"))
        self.conn.commit()
        qty = self.conn.execute("SELECT quantity FROM products WHERE id=?", (self.product_id,)).fetchone()[0]
        self.assertEqual(qty, 3)
        status = self.conn.execute("SELECT status FROM stock_reservations WHERE reservation_id='ORD-A'").fetchone()[0]
        self.assertEqual(status, "committed")

    def test_batch_reservation_rolls_back_partial_reservation(self):
        self.assertFalse(reserve_stock_batch(self.conn, [(self.product_id, 4), (self.product_id, 2)], "ORD-B"))
        active = self.conn.execute("SELECT COUNT(*) FROM stock_reservations WHERE reservation_id='ORD-B' AND status='active'").fetchone()[0]
        self.assertEqual(active, 0)

    def test_backup_and_restore_are_verified(self):
        with tempfile.TemporaryDirectory() as td:
            source = Path(td) / "store.db"
            backup_path = Path(td) / "backup.db"
            restored = Path(td) / "restored.db"
            self.conn.commit()
            disk = sqlite3.connect(source)
            disk.executescript(db.SCHEMA)
            disk.execute("INSERT INTO products (name, slug, quantity, created_at) VALUES (?, ?, ?, ?)", ("Disk", "disk", 2, "now"))
            disk.commit()
            disk.close()
            self.assertEqual(verify(str(source)), (True, []))
            backup(str(source), str(backup_path))
            restore(str(backup_path), str(restored))
            self.assertEqual(verify(str(restored)), (True, []))
            check = sqlite3.connect(restored)
            self.assertEqual(check.execute("SELECT COUNT(*) FROM products").fetchone()[0], 1)
            check.close()


if __name__ == "__main__":
    unittest.main()
