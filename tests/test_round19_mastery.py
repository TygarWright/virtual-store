import sqlite3
from mastery_services import upsert_feature_flag, flag_enabled, create_or_update_experiment, assign_experiment, index_memory, search_memory, record_decision_outcome
from permissions_comm import add_message, search_messages, pin_message, list_notifications, set_presence, list_presence


def team_conn():
    c=sqlite3.connect(':memory:'); c.row_factory=sqlite3.Row
    c.executescript('''
    CREATE TABLE team_conversations(id INTEGER PRIMARY KEY,kind TEXT,title TEXT,target_role TEXT,target_admin_id INTEGER,created_by INTEGER,created_at TEXT,updated_at TEXT);
    CREATE TABLE admin_users(id INTEGER PRIMARY KEY,username TEXT,role TEXT,is_active INTEGER);
    CREATE TABLE team_messages(id INTEGER PRIMARY KEY AUTOINCREMENT,conversation_id INTEGER,sender_admin_id INTEGER,body TEXT,created_at TEXT);
    CREATE TABLE team_notifications(id INTEGER PRIMARY KEY AUTOINCREMENT,admin_id INTEGER,conversation_id INTEGER,message_id INTEGER,kind TEXT,title TEXT,body TEXT,read_at TEXT,created_at TEXT);
    CREATE TABLE team_message_pins(message_id INTEGER PRIMARY KEY,pinned_by INTEGER,pinned_at TEXT);
    CREATE TABLE team_reads(conversation_id INTEGER,admin_id INTEGER,last_message_id INTEGER,PRIMARY KEY(conversation_id,admin_id));
    CREATE TABLE admin_presence(admin_id INTEGER PRIMARY KEY,state TEXT,last_seen_at TEXT);
    ''')
    c.execute("INSERT INTO admin_users VALUES(1,'alice','admin',1)"); c.execute("INSERT INTO admin_users VALUES(2,'bob','staff',1)")
    c.execute("INSERT INTO team_conversations VALUES(1,'global','Global Team','',NULL,1,'2026','2026')")
    return c


def test_feature_flag_deterministic():
    c=sqlite3.connect(':memory:'); c.row_factory=sqlite3.Row; c.execute('CREATE TABLE feature_flags(id INTEGER PRIMARY KEY AUTOINCREMENT,key TEXT UNIQUE,description TEXT,enabled INTEGER,rollout_percent INTEGER,updated_by INTEGER,updated_at TEXT)')
    upsert_feature_flag(c,key='new_search',enabled=True,rollout_percent=100)
    assert flag_enabled(c,'new_search',subject_key='alice')


def test_experiment_assignment_stable():
    c=sqlite3.connect(':memory:'); c.row_factory=sqlite3.Row; c.executescript('CREATE TABLE experiments(id INTEGER PRIMARY KEY AUTOINCREMENT,key TEXT UNIQUE,name TEXT,status TEXT,variants_json TEXT,allocation_json TEXT,primary_metric TEXT,started_at TEXT,ended_at TEXT,created_by INTEGER,created_at TEXT,updated_at TEXT); CREATE TABLE experiment_assignments(id INTEGER PRIMARY KEY AUTOINCREMENT,experiment_id INTEGER,subject_key TEXT,variant TEXT,assigned_at TEXT,UNIQUE(experiment_id,subject_key));')
    create_or_update_experiment(c,key='checkout',name='Checkout',variants=['control','variant'],allocation={'control':50,'variant':50},status='running')
    a=assign_experiment(c,experiment_key='checkout',subject_key='alice'); b=assign_experiment(c,experiment_key='checkout',subject_key='alice'); assert a==b


def test_memory_search():
    c=sqlite3.connect(':memory:'); c.row_factory=sqlite3.Row; c.execute('CREATE TABLE institutional_memory_index(id INTEGER PRIMARY KEY AUTOINCREMENT,source_type TEXT,source_id INTEGER,title TEXT,body TEXT,keywords TEXT,created_at TEXT,updated_at TEXT,UNIQUE(source_type,source_id))')
    index_memory(c,source_type='decision',source_id=1,title='Coupon change',body='Stopped 50 percent coupons because margin fell',keywords='pricing coupon')
    assert search_memory(c,'margin')[0]['source_id']==1


def test_team_mentions_pin_presence():
    c=team_conn(); mid=add_message(c,1,1,'@bob please review this.'); assert len(list_notifications(c,2,unread_only=True))==1
    assert pin_message(c,mid,2); set_presence(c,2,'online'); assert list_presence(c,[2])[0]['state']=='online'; assert search_messages(c,1,2,'review')[0]['id']==mid
