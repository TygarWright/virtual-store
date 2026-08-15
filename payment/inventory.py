"""Transactional inventory reservation primitives.

The checkout path reserves stock before payment and commits that reservation
only after verified payment.  SQL statements themselves enforce availability
so concurrent buyers cannot both reserve the same last unit.
"""
from __future__ import annotations

from typing import Optional, Dict, Any, List, Iterable
import logging

import database as db

logger = logging.getLogger(__name__)


def reserve_stock(conn, product_id: int, quantity: int, reservation_id: Optional[str] = None) -> bool:
    if quantity <= 0:
        return True
    if not reservation_id:
        raise ValueError("reservation_id is required")

    now = db.now()
    # One INSERT...SELECT statement performs the availability check and write
    # atomically under SQLite's writer serialization. This avoids the classic
    # SELECT-then-INSERT race where two buyers reserve the last unit together.
    cursor = conn.execute(
        """INSERT INTO stock_reservations
           (product_id, quantity, reservation_id, status, created_at, updated_at)
           SELECT ?, ?, ?, 'active', ?, ?
           WHERE EXISTS (
             SELECT 1 FROM products p
             WHERE p.id = ?
               AND p.quantity >= ?
               AND p.quantity - COALESCE((
                    SELECT SUM(sr.quantity) FROM stock_reservations sr
                    WHERE sr.product_id = p.id AND sr.status = 'active'
               ), 0) >= ?
           )""",
        (product_id, quantity, reservation_id, now, now, product_id, quantity, quantity),
    )
    if cursor.rowcount == 1:
        logger.info("Reserved stock product=%s qty=%s reservation=%s", product_id, quantity, reservation_id)
        return True
    logger.warning("Insufficient stock product=%s qty=%s reservation=%s", product_id, quantity, reservation_id)
    return False


def reserve_stock_batch(conn, items: Iterable[tuple[int, int]], reservation_id: str) -> bool:
    """Reserve every item for one order or release all if any item is unavailable."""
    reserved: list[tuple[int, int]] = []
    for product_id, quantity in items:
        if not reserve_stock(conn, int(product_id), int(quantity), reservation_id):
            release_stock(conn, reservation_id)
            return False
        reserved.append((int(product_id), int(quantity)))
    return True


def release_stock(conn, reservation_id: str) -> int:
    updated = conn.execute(
        """UPDATE stock_reservations
           SET status = 'released', updated_at = ?
           WHERE reservation_id = ? AND status = 'active'""",
        (db.now(), reservation_id),
    )
    return int(updated.rowcount or 0)


def commit_stock(conn, reservation_id: str) -> bool:
    """Commit every active reservation for an order atomically.

    The product decrement is guarded by quantity >= requested quantity. If any
    reservation cannot be committed, nothing is marked committed and the caller
    can safely handle the exceptional payment/fulfilment case.
    """
    rows = conn.execute(
        "SELECT * FROM stock_reservations WHERE reservation_id = ? AND status = 'active' ORDER BY id",
        (reservation_id,),
    ).fetchall()
    if not rows:
        # Idempotent success when the order was already committed.
        prior = conn.execute(
            "SELECT COUNT(*) AS c FROM stock_reservations WHERE reservation_id = ? AND status = 'committed'",
            (reservation_id,),
        ).fetchone()
        return bool(prior and prior["c"] > 0)

    for reservation in rows:
        updated = conn.execute(
            "UPDATE products SET quantity = quantity - ? WHERE id = ? AND quantity >= ?",
            (reservation["quantity"], reservation["product_id"], reservation["quantity"]),
        )
        if updated.rowcount != 1:
            raise RuntimeError(
                f"Inventory commit failed for product {reservation['product_id']} "
                f"reservation {reservation_id}"
            )

    conn.execute(
        """UPDATE stock_reservations
           SET status = 'committed', updated_at = ?
           WHERE reservation_id = ? AND status = 'active'""",
        (db.now(), reservation_id),
    )
    logger.info("Committed inventory reservation=%s rows=%s", reservation_id, len(rows))
    return True


def get_product_stock_status(conn, product_id: int) -> Optional[Dict[str, Any]]:
    product = conn.execute(
        """SELECT id, name, quantity,
                  COALESCE((SELECT SUM(quantity) FROM stock_reservations
                           WHERE product_id = ? AND status = 'active'), 0) AS reserved
           FROM products WHERE id = ?""",
        (product_id, product_id),
    ).fetchone()
    if not product:
        return None
    available = product["quantity"] - product["reserved"]
    return {
        "product_id": product["id"],
        "product_name": product["name"],
        "total_quantity": product["quantity"],
        "reserved_quantity": product["reserved"],
        "available_quantity": available,
    }


def get_active_reservations(conn, product_id: Optional[int] = None) -> List[Dict[str, Any]]:
    if product_id is not None:
        rows = conn.execute(
            """SELECT sr.*, p.name AS product_name FROM stock_reservations sr
               JOIN products p ON sr.product_id = p.id
               WHERE sr.product_id = ? AND sr.status = 'active' ORDER BY sr.created_at""",
            (product_id,),
        ).fetchall()
    else:
        rows = conn.execute(
            """SELECT sr.*, p.name AS product_name FROM stock_reservations sr
               JOIN products p ON sr.product_id = p.id
               WHERE sr.status = 'active' ORDER BY sr.created_at""",
        ).fetchall()
    return [dict(r) for r in rows]


def cleanup_expired_reservations(conn, max_age_hours: int = 24) -> int:
    # Never expire a reservation belonging to a paid/fulfilled order. If an
    # order reference cannot be resolved, leave the reservation alone for
    # manual reconciliation rather than risking stock corruption.
    updated = conn.execute(
        """UPDATE stock_reservations
           SET status = 'expired', updated_at = ?
           WHERE status = 'active'
             AND datetime(created_at) < datetime('now', '-' || ? || ' hours')
             AND reservation_id IN (
                 SELECT order_ref FROM orders WHERE status IN ('created', 'failed', 'cancelled')
             )""",
        (db.now(), max_age_hours),
    )
    return int(updated.rowcount or 0)


__all__ = [
    "reserve_stock", "reserve_stock_batch", "release_stock", "commit_stock",
    "get_product_stock_status", "get_active_reservations", "cleanup_expired_reservations",
]
