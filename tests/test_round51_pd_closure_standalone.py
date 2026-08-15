import sqlite3, sys, types
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parents[1]))
# stub database module so governance_service can be imported without the full Flask/Werkzeug stack
stub=types.ModuleType('database'); stub.now=lambda: '2026-01-01T00:00:00+00:00'; sys.modules['database']=stub
from mastery_services import ensure_experiment_mastery_schema, create_or_update_experiment, assign_experiment, guardrail_history
from analytics_mastery import set_experiment_guardrail, evaluate_experiment_guardrails, experiment_report
from governance_service import ensure_governance_maturity_schema, create_approval_delegation, approver_is_delegated, set_segregation_rule

def db():
 c=sqlite3.connect(':memory:'); c.row_factory=sqlite3.Row
 c.executescript('''CREATE TABLE experiments(id INTEGER PRIMARY KEY AUTOINCREMENT,key TEXT UNIQUE,name TEXT,status TEXT,variants_json TEXT,allocation_json TEXT,primary_metric TEXT,started_at TEXT,ended_at TEXT,created_by INTEGER,created_at TEXT,updated_at TEXT);
 CREATE TABLE experiment_assignments(id INTEGER PRIMARY KEY AUTOINCREMENT,experiment_id INTEGER,subject_key TEXT,variant TEXT,assigned_at TEXT,UNIQUE(experiment_id,subject_key));
 CREATE TABLE analytics_events(id INTEGER PRIMARY KEY,session_id TEXT,event_type TEXT,customer_id INTEGER,created_at TEXT);
 CREATE TABLE experiment_guardrails(id INTEGER PRIMARY KEY AUTOINCREMENT,experiment_id INTEGER,metric TEXT,comparator TEXT,threshold REAL,active INTEGER,created_at TEXT, UNIQUE(experiment_id,metric));''')
 return c

def main():
 c=db(); ensure_experiment_mastery_schema(c)
 create_or_update_experiment(c,key='x',name='X',variants=['control','new'],allocation={'control':50,'new':50},primary_metric='purchase',status='running')
 assert assign_experiment(c,experiment_key='x',subject_key='u1') in {'control','new'}
 set_experiment_guardrail(c,experiment_id=1,metric='refund',comparator='max_percent',threshold=10)
 evaluate_experiment_guardrails(c,1,days=3650,persist_history=True)
 assert c.execute('SELECT COUNT(*) FROM experiment_exposure_events').fetchone()[0]==1
 assert len(guardrail_history(c,1,days=3650))==1
 assert 'guardrail_history' in experiment_report(c,1,days=3650)
 g=sqlite3.connect(':memory:'); g.row_factory=sqlite3.Row; ensure_governance_maturity_schema(g)
 did=create_approval_delegation(g,delegator_id=1,delegate_id=2,action='refund.execute',starts_at='2026-01-01T00:00:00+00:00',expires_at='2099-01-01T00:00:00+00:00',reason='leave')
 assert did and approver_is_delegated(g,delegate_id=2,action='refund.execute',now_iso='2026-06-01T00:00:00+00:00')
 set_segregation_rule(g,action='refund.execute',requester_role='staff',approver_role='manager')
 assert g.execute("SELECT approver_role FROM segregation_rules WHERE action='refund.execute'").fetchone()[0]=='manager'
 print('ROUND51_PD_CLOSURE_PASS')
if __name__=='__main__': main()
