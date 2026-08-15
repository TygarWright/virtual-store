import sqlite3
from analytics_mastery import funnel_report, cohort_report, experiment_report, analytics_overview
from mastery_services import create_or_update_experiment, assign_experiment, index_memory, search_memory, memory_source_types, record_decision_outcome


def analytics_conn():
    c=sqlite3.connect(':memory:'); c.row_factory=sqlite3.Row
    c.executescript('''
    CREATE TABLE analytics_events(id INTEGER PRIMARY KEY,event_type TEXT,product_id INTEGER,customer_id INTEGER,session_id TEXT,query TEXT,metadata_json TEXT DEFAULT '{}',ip_address TEXT DEFAULT '',user_agent TEXT DEFAULT '',created_at TEXT);
    CREATE TABLE orders(id INTEGER PRIMARY KEY,customer_id INTEGER,total INTEGER,status TEXT,created_at TEXT);
    CREATE TABLE refunds(id INTEGER PRIMARY KEY,amount INTEGER,created_at TEXT);
    CREATE TABLE experiments(id INTEGER PRIMARY KEY AUTOINCREMENT,key TEXT UNIQUE,name TEXT,status TEXT,variants_json TEXT,allocation_json TEXT,primary_metric TEXT,started_at TEXT,ended_at TEXT,created_by INTEGER,created_at TEXT,updated_at TEXT);
    CREATE TABLE experiment_assignments(id INTEGER PRIMARY KEY AUTOINCREMENT,experiment_id INTEGER,subject_key TEXT,variant TEXT,assigned_at TEXT,UNIQUE(experiment_id,subject_key));
    CREATE TABLE institutional_memory_index(id INTEGER PRIMARY KEY AUTOINCREMENT,source_type TEXT,source_id INTEGER,title TEXT,body TEXT,keywords TEXT,created_at TEXT,updated_at TEXT,UNIQUE(source_type,source_id));
    CREATE TABLE decision_journal(id INTEGER PRIMARY KEY,title TEXT,decision TEXT,reason TEXT,expected_result TEXT,outcome TEXT DEFAULT '',lesson TEXT DEFAULT '',reviewed_by INTEGER,created_at TEXT,reviewed_at TEXT);
    ''')
    return c


def test_institutional_memory_filter_and_review():
    c=analytics_conn()
    index_memory(c,source_type='decision',source_id=1,title='Coupon strategy',body='margin fell',keywords='pricing')
    index_memory(c,source_type='sop',source_id=2,title='Refund SOP',body='verify provider first',keywords='refund')
    assert memory_source_types(c)==['decision','sop']
    assert search_memory(c,'coupon',source_type='decision')[0]['source_id']==1
    c.execute("INSERT INTO decision_journal VALUES(1,'Coupon','stop it','margin','recover','', '',NULL,'2026-01-01',NULL)")
    record_decision_outcome(c,decision_id=1,outcome='margin recovered',lesson='cap discount',future_recommendation='require approval',reviewed_by=7)
    row=c.execute('SELECT outcome,lesson,future_recommendation FROM decision_journal WHERE id=1').fetchone()
    assert tuple(row)==('margin recovered','cap discount','require approval')
    assert search_memory(c,'require approval')[0]['source_type']=='decision'


def test_experiment_report_attributes_by_subject():
    c=analytics_conn()
    create_or_update_experiment(c,key='checkout_copy',name='Checkout Copy',variants=['control','new'],allocation={'control':50,'new':50},primary_metric='purchase',status='running')
    a=assign_experiment(c,experiment_key='checkout_copy',subject_key='1')
    b=assign_experiment(c,experiment_key='checkout_copy',subject_key='2')
    assert {a,b}=={'control','new'} or len({a,b})==1
    c.execute("INSERT INTO analytics_events(event_type,customer_id,session_id,created_at) VALUES('purchase',1,'sess1','2026-01-02T00:00:00+00:00')")
    report=experiment_report(c,1,days=3650)
    assert sum(v['assigned'] for v in report['variants'].values())==2
    assert sum(v['conversions'] for v in report['variants'].values())==1


def test_funnel_cohort_overview():
    c=analytics_conn()
    c.executemany("INSERT INTO analytics_events(event_type,session_id,created_at) VALUES(?,?,?)",[
        ('view','s1','2026-01-01T00:00:00+00:00'),('cart','s1','2026-01-01T00:01:00+00:00'),('view','s2','2026-01-01T00:00:00+00:00')])
    c.executemany("INSERT INTO orders(customer_id,total,status,created_at) VALUES(?,?,?,?)",[(1,100,'paid','2026-01-01T00:00:00+00:00'),(1,100,'paid','2026-02-01T00:00:00+00:00'),(2,50,'paid','2026-01-02T00:00:00+00:00')])
    f=funnel_report(c,steps=['view','cart'],days=3650); assert f['steps'][0]['sessions']==2 and f['steps'][1]['sessions']==1
    co=cohort_report(c,days=3650); assert sum(v['customers'] for v in co['cohorts'].values())==2
    o=analytics_overview(c,days=3650,funnel_steps=['view','cart']); assert o['summary']['orders']==3 and o['funnel']['steps'][1]['sessions']==1
