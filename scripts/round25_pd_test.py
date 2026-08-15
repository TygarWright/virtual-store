from __future__ import annotations
import os, sqlite3, tempfile, sys, types, importlib.util
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

# Load governance_service without importing the full production database module.
stub = types.ModuleType('database'); sys.modules['database'] = stub
spec=importlib.util.spec_from_file_location('governance_service','governance_service.py')
gov=importlib.util.module_from_spec(spec); spec.loader.exec_module(gov)
import observability_service as obs

def main():
    fd, path = tempfile.mkstemp(suffix='.db'); os.close(fd)
    try:
        conn=sqlite3.connect(path); conn.row_factory=sqlite3.Row
        conn.execute("CREATE TABLE admin_users (id INTEGER PRIMARY KEY, username TEXT, role TEXT, is_active INTEGER DEFAULT 1)")
        conn.execute("INSERT INTO admin_users VALUES(1,'owner','master',1),(2,'ops','admin',1)")
        conn.execute("""CREATE TABLE business_exceptions(
          id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT, severity TEXT, title TEXT, description TEXT,
          entity TEXT, entity_id INTEGER, metadata_json TEXT, status TEXT, resolved_by INTEGER,
          resolution TEXT, created_at TEXT, resolved_at TEXT, assigned_to INTEGER, due_at TEXT,
          escalated_at TEXT, escalation_reason TEXT, updated_at TEXT)""")
        conn.execute("CREATE TABLE team_notifications(id INTEGER PRIMARY KEY AUTOINCREMENT,admin_id INTEGER,kind TEXT,title TEXT,body TEXT,created_at TEXT)")
        conn.commit()
        eid=gov.add_exception(conn,code='x',severity='medium',title='Test',description='Evidence',entity='order',entity_id=7)
        assert gov.assign_exception(conn,eid,assigned_to=2,due_at='2000-01-01T00:00:00+00:00')
        assert gov.escalate_overdue_exceptions(conn,now_iso='2030-01-01T00:00:00+00:00')==1
        assert conn.execute('SELECT admin_id FROM team_notifications').fetchone()['admin_id']==2
        obs.ensure_schema(conn)
        conn.execute("CREATE TABLE observability_alerts(id INTEGER PRIMARY KEY AUTOINCREMENT,trace_id TEXT,alert_type TEXT,severity TEXT,title TEXT,details TEXT,status TEXT,created_at TEXT,resolved_at TEXT,resolved_by INTEGER)")
        conn.commit()
        a,t=obs.start_span(conn,trace_id='t1',kind='http',name='GET /')
        obs.finish_span(conn,a,t)
        b,t2=obs.start_span(conn,trace_id='t1',kind='workflow',name='checkout:payment')
        obs.finish_span(conn,b,t2,status='error',error='boom')
        summary=obs.trace_summary(conn,'t1')
        assert summary['errors']==1 and len(summary['spans'])==2
        print('ROUND25_PD_TEST: PASS')
    finally:
        try: os.unlink(path)
        except OSError: pass
main()
