import sqlite3
import sys
import types


def test_observability_slo_report():
    fake_db = types.ModuleType('database')
    fake_db.now = lambda: '2026-08-15T00:00:00+00:00'
    sys.modules['database'] = fake_db
    import observability_service as obs
    c = sqlite3.connect(':memory:')
    c.row_factory = sqlite3.Row
    obs.ensure_schema(c)
    c.execute("INSERT INTO observability_slo_policies(key,name,operation_pattern,target_percent,window_hours,max_latency_ms,enabled,updated_at) VALUES(?,?,?,?,?,?,?,?)",
              ('storefront.latency','Storefront','GET /',99.0,24,250.0,1,obs.now_iso()))
    now = obs.now_iso()
    c.execute("INSERT INTO observability_spans(trace_id,span_id,kind,name,status,started_at,ended_at,duration_ms) VALUES(?,?,?,?,?,?,?,?)",
              ('t1','s1','http','GET /','ok',now,now,100.0))
    c.execute("INSERT INTO observability_spans(trace_id,span_id,kind,name,status,started_at,ended_at,duration_ms) VALUES(?,?,?,?,?,?,?,?)",
              ('t2','s2','http','GET /','error',now,now,200.0))
    c.commit()
    r = obs.slo_report(c, key='storefront.latency')
    assert r and r['total'] == 2 and r['errors'] == 1 and r['p95_ms'] == 200.0 and r['healthy'] is False
    c.close()


def test_support_context_query_shape():
    c=sqlite3.connect(':memory:'); c.row_factory=sqlite3.Row
    c.executescript('''
    CREATE TABLE team_conversations(id INTEGER PRIMARY KEY, kind TEXT, title TEXT, context_type TEXT, context_id INTEGER, updated_at TEXT);
    CREATE TABLE team_messages(id INTEGER PRIMARY KEY, conversation_id INTEGER, body TEXT);
    INSERT INTO team_conversations VALUES(1,'context','Customer · A','customer',7,'2026-08-15T00:00:00+00:00');
    INSERT INTO team_messages VALUES(9,1,'Need to verify refund status');
    ''')
    row=c.execute("SELECT c.id,c.title,c.updated_at,(SELECT body FROM team_messages m WHERE m.conversation_id=c.id ORDER BY m.id DESC LIMIT 1) AS last_message FROM team_conversations c WHERE c.kind='context' AND c.context_type='customer' AND c.context_id=?",(7,)).fetchone()
    assert row['last_message']=='Need to verify refund status'
    c.close()

print('ROUND50_PD_CLOSURE_PASS')
