"""Provider reconciliation with durable reports and discrepancy history.

Safe to run repeatedly. It never silently mutates mismatches; recoverable
missing payment state is repaired only when provider truth is unambiguous.
"""
import os, sys, logging, json
from datetime import datetime, timezone, timedelta
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import database as db
import razorpay_client as rzp
from governance_service import open_exception
import observability_service

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger=logging.getLogger("reconcile_razorpay")


def _now():
    return datetime.now(timezone.utc).isoformat()




def acquire_reconciliation_lock(conn, *, provider: str, acquired_by: int = 0, lease_minutes: int = 30) -> bool:
    """Prevent overlapping provider reconciliation runs."""
    now = datetime.now(timezone.utc)
    until = now + timedelta(minutes=max(1, min(int(lease_minutes), 240)))
    provider = str(provider or "").strip().lower()
    if not provider:
        raise ValueError("provider is required")
    row = conn.execute("SELECT acquired_until FROM reconciliation_locks WHERE provider=?", (provider,)).fetchone()
    if row and row["acquired_until"] and str(row["acquired_until"]) > now.isoformat():
        return False
    conn.execute(
        "INSERT INTO reconciliation_locks(provider,acquired_until,acquired_at,acquired_by) VALUES(?,?,?,?) "
        "ON CONFLICT(provider) DO UPDATE SET acquired_until=excluded.acquired_until, acquired_at=excluded.acquired_at, acquired_by=excluded.acquired_by",
        (provider, until.isoformat(), now.isoformat(), int(acquired_by or 0) or None),
    )
    conn.commit()
    return True


def release_reconciliation_lock(conn, *, provider: str) -> None:
    conn.execute("DELETE FROM reconciliation_locks WHERE provider=?", (str(provider).strip().lower(),))
    conn.commit()

def reconcile(*, created_by: int = 0, mode: str = "manual", order_limit: int = 500):
    """Run a provider reconciliation and persist a durable report."""
    conn = db.get_db()
    db.ensure_round18_schema(conn)
    db.ensure_round48_schema(conn)
    if not acquire_reconciliation_lock(conn, provider="razorpay", acquired_by=created_by):
        conn.close()
        return {"status": "skipped", "reason": "reconciliation already running", "provider": "razorpay"}
    db.ensure_round24_schema(conn)
    db.ensure_round45_schema(conn)
    started = _now()
    run_id = conn.execute(
        "INSERT INTO reconciliation_runs(provider,mode,status,started_at,created_by) VALUES(?,?,?,?,?)",
        ("razorpay", mode, "running", started, int(created_by or 0) or None),
    ).lastrowid
    repaired = mismatches = scanned = 0
    trace_id = f"recon-{run_id}-{os.urandom(6).hex()}"
    span_started = None
    span_id = None
    try:
        span_id, span_started = observability_service.start_span(conn, trace_id=trace_id, kind="reconciliation", name=f"razorpay.reconcile:{mode}", request_id=None, attributes={"run_id": int(run_id), "mode": mode})
    except Exception:
        span_id = span_started = None
    try:
        if not rzp.is_configured():
            result = {"status": "skipped", "reason": "Razorpay not configured", "run_id": int(run_id)}
            conn.execute("UPDATE reconciliation_runs SET status='skipped',completed_at=?,summary_json=? WHERE id=?",
                         (_now(), json.dumps(result, sort_keys=True), run_id))
            conn.commit(); release_reconciliation_lock(conn, provider="razorpay"); conn.close(); return result

        cutoff=(datetime.now(timezone.utc)-timedelta(minutes=30)).isoformat()
        orders=conn.execute(
            "SELECT * FROM orders WHERE created_at <= ? AND razorpay_order_id IS NOT NULL AND razorpay_order_id != '' ORDER BY created_at ASC LIMIT ?",
            (cutoff, max(1, min(int(order_limit), 5000))),
        ).fetchall()
        for order in orders:
            scanned += 1
            oid = int(order["id"])
            try:
                payments=rzp.fetch_order_payments(order["razorpay_order_id"])
            except Exception as exc:
                open_exception(conn, code="provider_reconciliation_error", severity="high", title="Razorpay reconciliation failed", description=str(exc), entity="order", entity_id=oid)
                conn.execute("INSERT INTO reconciliation_items(run_id,severity,code,entity,entity_id,title,details,created_at) VALUES(?,?,?,?,?,?,?,?)",
                             (run_id,"high","provider_reconciliation_error","order",oid,"Razorpay reconciliation failed",str(exc)[:4000],_now()))
                continue
            captured=[p for p in payments if p.get("status")=="captured" and p.get("captured") is True]
            total_captured=sum(int(p.get("amount") or 0) for p in captured)
            expected_paise=int(order["amount"] or 0)*100
            if captured and total_captured != expected_paise:
                mismatches += 1
                detail=f"Local ₹{order['amount']} vs provider paise {total_captured}"
                open_exception(conn, code="payment_amount_mismatch", severity="critical", title="Razorpay payment amount mismatch", description=detail, entity="order", entity_id=oid, metadata={"expected_paise":expected_paise,"provider_paise":total_captured})
                conn.execute("INSERT INTO reconciliation_items(run_id,severity,code,entity,entity_id,title,details,created_at) VALUES(?,?,?,?,?,?,?,?)",
                             (run_id,"critical","payment_amount_mismatch","order",oid,"Payment amount mismatch",detail,_now()))
                continue
            if captured and not order["razorpay_payment_id"]:
                p=captured[0]
                conn.execute("UPDATE orders SET status='paid', payment_state='paid', order_state='paid', razorpay_payment_id=?, paid_at=? WHERE id=? AND status NOT IN ('cancelled','refunded')", (p.get("id"), db.now(), oid))
                repaired += 1
                conn.execute("INSERT INTO reconciliation_items(run_id,severity,code,entity,entity_id,title,details,created_at) VALUES(?,?,?,?,?,?,?,?)",
                             (run_id,"info","payment_state_recovered","order",oid,"Payment state recovered","Captured payment existed at provider but local payment ID was missing.",_now()))
            payment_id=order["razorpay_payment_id"] or (captured[0].get("id") if captured else None)
            if captured and payment_id:
                capture_ref = payment_id
                _ledger_entry(conn, entry_key=f"sale:{oid}:{capture_ref}", entry_type="sale", order_id=oid, provider_reference=capture_ref, amount=int(order["amount"] or 0), occurred_at=order["paid_at"] or order["created_at"], metadata={"source":"razorpay_reconciliation"})
            if payment_id:
                try:
                    provider_refunds=rzp.fetch_payment_refunds(payment_id)
                    provider_ids={r.get("id") for r in provider_refunds if r.get("id")}
                    for provider_ref in provider_refunds:
                        pref = provider_ref.get("id")
                        if pref and str(provider_ref.get("status") or "").lower() in {"processed","refunded"}:
                            _ledger_entry(conn, entry_key=f"refund:{oid}:{pref}", entry_type="refund", order_id=oid, provider_reference=pref, amount=int(provider_ref.get("amount") or 0)//100, occurred_at=provider_ref.get("created_at") and datetime.fromtimestamp(int(provider_ref.get("created_at")), tz=timezone.utc).isoformat() or _now(), metadata={"source":"razorpay_reconciliation"})
                    local=conn.execute("SELECT id, provider_refund_id, status FROM order_refunds WHERE order_id=?", (oid,)).fetchall()
                    local_ids={r["provider_refund_id"] for r in local if r["provider_refund_id"]}
                    missing=provider_ids-local_ids
                    if missing:
                        mismatches += len(missing)
                        detail=f"Provider refunds missing locally: {sorted(missing)}"
                        open_exception(conn, code="refund_reconciliation_mismatch", severity="high", title="Provider refund not recorded locally", description=detail, entity="order", entity_id=oid, metadata={"provider_refund_ids":sorted(missing)})
                        conn.execute("INSERT INTO reconciliation_items(run_id,severity,code,entity,entity_id,title,details,created_at) VALUES(?,?,?,?,?,?,?,?)",
                                     (run_id,"high","refund_reconciliation_mismatch","order",oid,"Refund reconciliation mismatch",detail,_now()))
                except Exception as exc:
                    open_exception(conn, code="refund_reconciliation_error", severity="medium", title="Refund reconciliation failed", description=str(exc), entity="order", entity_id=oid)
                    conn.execute("INSERT INTO reconciliation_items(run_id,severity,code,entity,entity_id,title,details,created_at) VALUES(?,?,?,?,?,?,?,?)",
                                 (run_id,"medium","refund_reconciliation_error","order",oid,"Refund reconciliation failed",str(exc)[:4000],_now()))

        mismatches += reconcile_inventory_consistency(conn, run_id=int(run_id))
        mismatches += reconcile_ledger_consistency(conn, run_id=int(run_id))
        period_start = (datetime.now(timezone.utc)-timedelta(days=1)).date().isoformat() + "T00:00:00+00:00"
        period_end = datetime.now(timezone.utc).date().isoformat() + "T00:00:00+00:00"
        conn.execute("""UPDATE reconciliation_items SET due_at=CASE severity WHEN 'critical' THEN datetime(created_at, '+2 hours') WHEN 'high' THEN datetime(created_at, '+12 hours') WHEN 'medium' THEN datetime(created_at, '+2 days') ELSE datetime(created_at, '+7 days') END WHERE run_id=? AND due_at IS NULL""", (run_id,))
        snapshot = refresh_ledger_snapshot(conn, period_start=period_start, period_end=period_end)
        result = {"status":"completed","run_id":int(run_id),"repaired":repaired,"mismatches":mismatches,"scanned":scanned,"ledger":snapshot}
        conn.execute("UPDATE reconciliation_runs SET status='completed',completed_at=?,scanned=?,repaired=?,mismatches=?,summary_json=? WHERE id=?",
                     (_now(), scanned, repaired, mismatches, json.dumps(result, sort_keys=True), run_id))
        conn.commit()
        if span_id:
            try: observability_service.finish_span(conn, span_id, span_started, status="ok")
            except Exception: pass
        release_reconciliation_lock(conn, provider="razorpay")
        conn.close()
        logger.info("Reconciliation complete: %s", result)
        return result
    except Exception as exc:
        conn.execute("UPDATE reconciliation_runs SET status='failed',completed_at=?,scanned=?,repaired=?,mismatches=?,summary_json=? WHERE id=?",
                     (_now(), scanned, repaired, mismatches, json.dumps({"status":"failed","error":str(exc)[:2000]}, sort_keys=True), run_id))
        release_reconciliation_lock(conn, provider="razorpay")
        conn.commit(); conn.close()
        raise



def reconcile_inventory_consistency(conn, *, run_id: int) -> int:
    """Detect inventory states that cannot be reconciled from current data.

    This is observational: it never changes stock automatically.
    """
    mismatches=0
    rows=conn.execute(
        """SELECT p.id, p.name, p.quantity,
                  COALESCE(SUM(CASE WHEN sr.status='active' THEN sr.quantity ELSE 0 END),0) AS active_reserved
           FROM products p LEFT JOIN stock_reservations sr ON sr.product_id=p.id
           GROUP BY p.id,p.name,p.quantity"""
    ).fetchall()
    for row in rows:
        quantity=int(row['quantity'] or 0)
        reserved=int(row['active_reserved'] or 0)
        if quantity < 0:
            mismatches += 1
            detail=f"Product {row['name']} has negative quantity {quantity}."
            conn.execute("INSERT INTO reconciliation_items(run_id,severity,code,entity,entity_id,title,details,created_at) VALUES(?,?,?,?,?,?,?,?)",
                         (run_id,'critical','negative_inventory','product',int(row['id']),'Negative inventory',detail,_now()))
        if reserved < 0 or reserved > quantity:
            mismatches += 1
            detail=f"Product {row['name']}: quantity={quantity}, active_reserved={reserved}."
            conn.execute("INSERT INTO reconciliation_items(run_id,severity,code,entity,entity_id,title,details,created_at) VALUES(?,?,?,?,?,?,?,?)",
                         (run_id,'critical','inventory_reservation_mismatch','product',int(row['id']),'Inventory reservation mismatch',detail,_now()))
    # Paid/processing orders should not retain active reservations indefinitely.
    stale=conn.execute(
        """SELECT o.id,o.order_ref,o.status,o.inventory_reservation_id
           FROM orders o JOIN stock_reservations sr ON sr.reservation_id=o.inventory_reservation_id
           WHERE sr.status='active' AND o.status IN ('paid','delivered','refunded')"""
    ).fetchall()
    for row in stale:
        mismatches += 1
        detail=f"Order {row['order_ref']} is {row['status']} but retains an active stock reservation."
        conn.execute("INSERT INTO reconciliation_items(run_id,severity,code,entity,entity_id,title,details,created_at) VALUES(?,?,?,?,?,?,?,?)",
                     (run_id,'high','stale_active_reservation','order',int(row['id']),'Paid order has active inventory reservation',detail,_now()))
    return mismatches

def recent_runs(limit: int = 20):
    conn = db.get_db(); db.ensure_round18_schema(conn)
    rows = conn.execute("SELECT * FROM reconciliation_runs ORDER BY id DESC LIMIT ?", (max(1,min(int(limit),100)),)).fetchall()
    result=[]
    for row in rows:
        item=dict(row); item["summary"]=json.loads(item.pop("summary_json") or "{}")
        result.append(item)
    conn.close(); return result


def run_items(run_id: int):
    conn = db.get_db(); db.ensure_round18_schema(conn)
    rows=conn.execute("SELECT * FROM reconciliation_items WHERE run_id=? ORDER BY CASE severity WHEN 'critical' THEN 0 WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END, id DESC", (int(run_id),)).fetchall()
    result=[dict(r) for r in rows]; conn.close(); return result


def main():
    reconcile(mode="cli")


if __name__ == "__main__":
    main()


def get_open_items(*, run_id=None, limit=200):
    conn = db.get_db(); db.ensure_round24_schema(conn)
    db.ensure_round45_schema(conn)
    if run_id is None:
        rows = conn.execute("SELECT * FROM reconciliation_items WHERE resolved=0 ORDER BY CASE severity WHEN 'critical' THEN 0 WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END, id DESC LIMIT ?", (max(1, min(int(limit), 500)),)).fetchall()
    else:
        rows = conn.execute("SELECT * FROM reconciliation_items WHERE run_id=? AND resolved=0 ORDER BY id DESC", (int(run_id),)).fetchall()
    data=[]
    now = datetime.now(timezone.utc)
    for r in rows:
        item=dict(r)
        try:
            created=datetime.fromisoformat(str(item.get("created_at")).replace("Z","+00:00"))
            age_days=max(0,(now-created).days)
        except Exception:
            age_days=0
        item["age_days"]=age_days
        item["aging_bucket"]="0-1d" if age_days < 1 else "1-3d" if age_days < 3 else "3-7d" if age_days < 7 else "7d+"
        data.append(item)
    conn.close(); return data


def aging_summary(*, limit: int = 1000):
    conn=db.get_db(); db.ensure_round24_schema(conn); db.ensure_round45_schema(conn)
    rows=conn.execute("SELECT severity, created_at, due_at FROM reconciliation_items WHERE resolved=0 ORDER BY created_at ASC LIMIT ?", (max(1,min(int(limit),5000)),)).fetchall()
    now=datetime.now(timezone.utc); buckets={"0-1d":0,"1-3d":0,"3-7d":0,"7d+":0,"overdue":0}
    by_severity={}
    for r in rows:
        try: created=datetime.fromisoformat(str(r["created_at"]).replace("Z","+00:00")); age=(now-created).total_seconds()/86400
        except Exception: age=0
        bucket="0-1d" if age < 1 else "1-3d" if age < 3 else "3-7d" if age < 7 else "7d+"
        buckets[bucket]+=1
        if r["due_at"]:
            try:
                due=datetime.fromisoformat(str(r["due_at"]).replace("Z","+00:00"))
                if due < now: buckets["overdue"]+=1
            except Exception: pass
        by_severity[str(r["severity"])] = by_severity.get(str(r["severity"]),0)+1
    conn.close(); return {"open_total":len(rows),"aging":buckets,"by_severity":by_severity}


def resolve_item(item_id: int, *, resolved_by: int, resolution: str, resolution_code: str = "manual_review") -> bool:
    conn = db.get_db(); db.ensure_round24_schema(conn)
    db.ensure_round45_schema(conn)
    row=conn.execute("SELECT id, resolved FROM reconciliation_items WHERE id=?", (int(item_id),)).fetchone()
    if not row or int(row["resolved"] or 0):
        conn.close(); return False
    code=str(resolution_code or "manual_review").strip().lower()[:64]
    allowed={"manual_review","provider_confirmed","local_record_corrected","duplicate","false_positive","accepted_difference","escalated"}
    if code not in allowed:
        code="manual_review"
    conn.execute("UPDATE reconciliation_items SET resolved=1,resolution=?,resolution_code=?,resolved_by=?,resolved_at=?,signed_off_by=?,signed_off_at=? WHERE id=?", (str(resolution or "Resolved after manual review")[:4000], code, int(resolved_by), _now(), int(resolved_by), _now(), int(item_id)))
    conn.commit(); conn.close(); return True



def reconcile_ledger_consistency(conn, run_id: int) -> int:
    """Compare local financial state with the durable ledger and record drift.

    Detection only: this function never mutates orders, refunds, or ledger rows.
    """
    mismatches = 0
    rows = conn.execute("""
        SELECT o.id, o.amount, o.status,
               COALESCE((SELECT SUM(fl.amount) FROM financial_ledger fl WHERE fl.order_id=o.id AND fl.entry_type='sale'),0) AS ledger_sales,
               COALESCE((SELECT SUM(fl.amount) FROM financial_ledger fl WHERE fl.order_id=o.id AND fl.entry_type='refund'),0) AS ledger_refunds,
               COALESCE((SELECT SUM(r.amount) FROM order_refunds r WHERE r.order_id=o.id AND r.status IN ('processed','refunded')),0) AS local_refunds
        FROM orders o
        WHERE o.status NOT IN ('cancelled','created')
        ORDER BY o.id DESC LIMIT 2000
    """).fetchall()
    for row in rows:
        order_id = int(row['id'])
        expected_sale = int(row['amount'] or 0)
        ledger_sales = int(row['ledger_sales'] or 0)
        ledger_refunds = int(row['ledger_refunds'] or 0)
        local_refunds = int(row['local_refunds'] or 0)
        if row['status'] in ('paid','delivered','refunded') and ledger_sales != expected_sale:
            mismatches += 1
            detail = f"Order {order_id}: expected sale ledger ₹{expected_sale}, found ₹{ledger_sales}."
            open_exception(conn, code='ledger_sale_mismatch', severity='high', title='Ledger sale mismatch', description=detail, entity='order', entity_id=order_id, metadata={'expected': expected_sale, 'ledger': ledger_sales})
            conn.execute("INSERT INTO reconciliation_items(run_id,severity,code,entity,entity_id,title,details,created_at) VALUES(?,?,?,?,?,?,?,?)", (run_id,'high','ledger_sale_mismatch','order',order_id,'Ledger sale mismatch',detail,_now()))
        if local_refunds != ledger_refunds:
            mismatches += 1
            detail = f"Order {order_id}: local processed refunds ₹{local_refunds}, ledger refunds ₹{ledger_refunds}."
            open_exception(conn, code='ledger_refund_mismatch', severity='high', title='Ledger refund mismatch', description=detail, entity='order', entity_id=order_id, metadata={'local_refunds': local_refunds, 'ledger_refunds': ledger_refunds})
            conn.execute("INSERT INTO reconciliation_items(run_id,severity,code,entity,entity_id,title,details,created_at) VALUES(?,?,?,?,?,?,?,?)", (run_id,'high','ledger_refund_mismatch','order',order_id,'Ledger refund mismatch',detail,_now()))
    return mismatches

def _ledger_entry(conn, *, entry_key, entry_type, order_id=None, refund_id=None, provider_reference="", amount=0, occurred_at=None, metadata=None):
    conn.execute(
        """INSERT OR IGNORE INTO financial_ledger
           (entry_key,entry_type,order_id,refund_id,provider,provider_reference,amount,currency,occurred_at,created_at,metadata_json)
           VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
        (str(entry_key), str(entry_type), order_id, refund_id, "razorpay", str(provider_reference or ""), int(amount or 0), "INR", occurred_at or _now(), _now(), json.dumps(metadata or {}, sort_keys=True, default=str)),
    )


def refresh_ledger_snapshot(conn, *, period_start, period_end):
    sales = int(conn.execute("SELECT COALESCE(SUM(amount),0) AS v FROM financial_ledger WHERE entry_type='sale' AND occurred_at>=? AND occurred_at<?", (period_start, period_end)).fetchone()["v"] or 0)
    refunds = int(conn.execute("SELECT COALESCE(SUM(amount),0) AS v FROM financial_ledger WHERE entry_type='refund' AND occurred_at>=? AND occurred_at<?", (period_start, period_end)).fetchone()["v"] or 0)
    entries = int(conn.execute("SELECT COUNT(*) AS v FROM financial_ledger WHERE occurred_at>=? AND occurred_at<?", (period_start, period_end)).fetchone()["v"] or 0)
    conn.execute("INSERT INTO financial_ledger_snapshots(period_start,period_end,gross_sales,refunds,net_sales,ledger_entries,created_at) VALUES(?,?,?,?,?,?,?) ON CONFLICT(period_start,period_end) DO UPDATE SET gross_sales=excluded.gross_sales,refunds=excluded.refunds,net_sales=excluded.net_sales,ledger_entries=excluded.ledger_entries,created_at=excluded.created_at", (period_start,period_end,sales,refunds,sales-refunds,entries,_now()))
    return {"gross_sales": sales, "refunds": refunds, "net_sales": sales-refunds, "ledger_entries": entries}


def ledger_snapshot(period_start: str, period_end: str):
    conn=db.get_db(); row=conn.execute("SELECT * FROM financial_ledger_snapshots WHERE period_start=? AND period_end=?", (period_start,period_end)).fetchone(); conn.close()
    return dict(row) if row else None
