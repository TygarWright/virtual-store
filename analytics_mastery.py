"""Privacy-conscious analytics, funnel, cohort and experiment reporting."""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
import json

def _now(): return datetime.now(timezone.utc)
def _iso_days_ago(days:int)->str: return (_now()-timedelta(days=int(days))).isoformat()

def funnel_report(conn, *, steps, days:int=30):
    steps=[str(s).strip() for s in steps if str(s).strip()]
    if not steps: raise ValueError("at least one funnel step is required")
    days=max(1,min(int(days),3650)); cutoff=_iso_days_ago(days)
    rows=conn.execute("SELECT session_id,event_type,created_at FROM analytics_events WHERE created_at>=? AND session_id!='' ORDER BY session_id,created_at",(cutoff,)).fetchall()
    sessions={}
    for r in rows: sessions.setdefault(str(r["session_id"]),[]).append((str(r["event_type"]),str(r["created_at"])))
    counts=[]
    for step in steps: counts.append(sum(1 for events in sessions.values() if any(evt==step for evt,_ in events)))
    baseline=counts[0] if counts else 0
    return {"days":days,"steps":[{"event":s,"sessions":c,"conversion_from_first":round(c*100/baseline,2) if baseline else 0.0} for s,c in zip(steps,counts)],"total_sessions_seen":len(sessions)}

def cohort_report(conn, *, days:int=180):
    days=max(30,min(int(days),3650)); cutoff=_iso_days_ago(days)
    rows=conn.execute("SELECT customer_id,created_at FROM orders WHERE customer_id IS NOT NULL AND created_at>=? AND status NOT IN ('cancelled','payment_failed') ORDER BY customer_id,created_at",(cutoff,)).fetchall()
    by={};
    for r in rows:
        try: dt=datetime.fromisoformat(str(r["created_at"]).replace('Z','+00:00'))
        except ValueError: continue
        by.setdefault(int(r["customer_id"]),[]).append(dt)
    cohorts={}
    for dates in by.values():
        if not dates: continue
        key=min(dates).strftime('%Y-%m'); b=cohorts.setdefault(key,{"customers":0,"repeat_customers":0}); b["customers"]+=1
        if len(dates)>=2: b["repeat_customers"]+=1
    for b in cohorts.values(): b["repeat_rate"]=round(b["repeat_customers"]*100/b["customers"],2) if b["customers"] else 0.0
    return {"days":days,"cohorts":dict(sorted(cohorts.items()))}

def _subject_metric_exists(conn, subject_key:str, metric:str, cutoff:str)->bool:
    row=conn.execute("SELECT 1 FROM analytics_events WHERE created_at>=? AND event_type=? AND (session_id=? OR CAST(customer_id AS TEXT)=?) LIMIT 1",(cutoff,metric,subject_key,subject_key)).fetchone()
    return bool(row)


def set_experiment_guardrail(conn, *, experiment_id: int, metric: str, comparator: str, threshold: float, active: bool = True):
    metric = (metric or '').strip()
    comparator = (comparator or 'max_percent').strip().lower()
    if not metric: raise ValueError('guardrail metric is required')
    if comparator not in {'max_percent','min_percent'}: raise ValueError('unsupported guardrail comparator')
    threshold = float(threshold)
    if threshold < 0: raise ValueError('guardrail threshold must be non-negative')
    if not conn.execute('SELECT id FROM experiments WHERE id=?',(int(experiment_id),)).fetchone(): raise ValueError('experiment not found')
    conn.execute("INSERT INTO experiment_guardrails(experiment_id,metric,comparator,threshold,active,created_at) VALUES(?,?,?,?,?,?) ON CONFLICT(experiment_id,metric) DO UPDATE SET comparator=excluded.comparator, threshold=excluded.threshold, active=excluded.active", (int(experiment_id),metric,comparator,threshold,1 if active else 0,_now().isoformat()))
    conn.commit()


def evaluate_experiment_guardrails(conn, experiment_id: int, *, days: int = 90, persist_history: bool = False) -> list[dict]:
    days=max(1,min(int(days),3650)); cutoff=_iso_days_ago(days)
    rows=conn.execute('SELECT metric, comparator, threshold FROM experiment_guardrails WHERE experiment_id=? AND active=1 ORDER BY metric',(int(experiment_id),)).fetchall()
    assignments=conn.execute('SELECT subject_key FROM experiment_assignments WHERE experiment_id=? AND assigned_at>=?',(int(experiment_id),cutoff)).fetchall()
    subjects=[str(r['subject_key']) for r in assignments]
    out=[]
    for r in rows:
        metric=str(r['metric']); subject_count=len(set(subjects))
        converted=0
        if subjects:
            placeholders=','.join('?' for _ in subjects)
            params=tuple(subjects)+(tuple(subjects))+(cutoff,metric)
            got=conn.execute(f'SELECT COUNT(DISTINCT COALESCE(session_id, CAST(customer_id AS TEXT))) AS n FROM analytics_events WHERE created_at>=? AND event_type=? AND (session_id IN ({placeholders}) OR CAST(customer_id AS TEXT) IN ({placeholders}))', (cutoff,metric,*subjects,*subjects)).fetchone()
            converted=int(got['n'] or 0)
        observed=(converted*100/subject_count) if subject_count else 0.0
        threshold=float(r['threshold']); comparator=str(r['comparator'])
        passed=observed<=threshold if comparator=='max_percent' else observed>=threshold
        out.append({'metric':metric,'comparator':comparator,'threshold':threshold,'observed_percent':round(observed,2),'passed':bool(passed)})
    if persist_history:
        try:
            from mastery_services import record_guardrail_evaluation
            record_guardrail_evaluation(conn, experiment_id=int(experiment_id), results=out)
        except Exception:
            pass
    return out


def experiment_report(conn, experiment_id:int, *, days:int=90)->dict:
    days=max(1,min(int(days),3650)); cutoff=_iso_days_ago(days)
    exp=conn.execute("SELECT * FROM experiments WHERE id=?",(int(experiment_id),)).fetchone()
    if not exp: raise ValueError("experiment not found")
    assignments=conn.execute("SELECT variant,subject_key FROM experiment_assignments WHERE experiment_id=? AND assigned_at>=?",(int(experiment_id),cutoff)).fetchall()
    variants={}
    for r in assignments:
        v=str(r['variant']); item=variants.setdefault(v,{'assigned':0,'conversions':0}); item['assigned']+=1
        metric=str(exp['primary_metric'] or '').strip()
        if metric and _subject_metric_exists(conn,str(r['subject_key']),metric,cutoff): item['conversions']+=1
    control_rate=0.0
    for v,d in variants.items(): d['conversion_rate']=round(d['conversions']*100/d['assigned'],2) if d['assigned'] else 0.0
    if 'control' in variants: control_rate=variants['control']['conversion_rate']
    for v,d in variants.items(): d['lift_vs_control_pct_points']=round(d['conversion_rate']-control_rate,2) if control_rate else 0.0
    guardrails=evaluate_experiment_guardrails(conn,int(experiment_id),days=days)
    history=[]
    try:
        from mastery_services import guardrail_history
        history=guardrail_history(conn,int(experiment_id),days=days)
    except Exception:
        history=[]
    return {'experiment':{'id':int(exp['id']),'key':exp['key'],'name':exp['name'],'status':exp['status'],'primary_metric':exp['primary_metric']},'days':days,'variants':variants,'guardrails':guardrails,'guardrail_history':history,'guardrails_passed':all(g['passed'] for g in guardrails) if guardrails else True}

def analytics_overview(conn, *, days:int=30, funnel_steps=None, experiment_id=None)->dict:
    days=max(1,min(int(days),3650)); cutoff=_iso_days_ago(days)
    r=conn.execute("SELECT COALESCE(SUM(total),0) revenue,COUNT(*) orders FROM orders WHERE created_at>=? AND status NOT IN ('cancelled','payment_failed')",(cutoff,)).fetchone()
    try: rr=conn.execute("SELECT COALESCE(SUM(amount),0) refunds,COUNT(*) count FROM refunds WHERE created_at>=?",(cutoff,)).fetchone()
    except Exception: rr={"refunds":0,"count":0}
    ev=conn.execute("SELECT COUNT(*) n FROM analytics_events WHERE created_at>=?",(cutoff,)).fetchone()
    exps=[]
    for e in conn.execute("SELECT id,key,name,status,primary_metric,started_at,ended_at FROM experiments ORDER BY updated_at DESC LIMIT 50").fetchall():
        d=dict(e); d["report"]=experiment_report(conn,int(e["id"]),days=days); exps.append(d)
    result={"period_days":days,"summary":{"revenue":int(r["revenue"] or 0),"orders":int(r["orders"] or 0),"refunds":int(rr["refunds"] or 0),"refund_count":int(rr["count"] or 0),"events":int(ev["n"] or 0)},"funnel":funnel_report(conn,steps=funnel_steps,days=days) if funnel_steps else None,"cohorts":cohort_report(conn,days=max(days,30)),"experiments":exps}
    if experiment_id is not None: result["selected_experiment"]=experiment_report(conn,int(experiment_id),days=days)
    return result


def conclude_experiment(conn, *, experiment_id: int, concluded_by: int, conclusion: str, days: int = 90, require_guardrails: bool = True) -> dict:
    """Conclude an experiment only after evaluating its safety guardrails.

    The conclusion is stored as an explicit business decision. When guardrails
    fail, conclusion is rejected unless the caller explicitly disables the
    guardrail requirement (an override that should be governed by the admin
    policy layer).
    """
    conclusion=(conclusion or '').strip()
    if not conclusion:
        raise ValueError('experiment conclusion is required')
    exp=conn.execute('SELECT * FROM experiments WHERE id=?',(int(experiment_id),)).fetchone()
    if not exp:
        raise ValueError('experiment not found')
    report=experiment_report(conn,int(experiment_id),days=days)
    passed=bool(report.get('guardrails_passed', True))
    if require_guardrails and not passed:
        raise ValueError('cannot conclude experiment while active guardrails are failing')
    status='concluded'
    now_iso=_now().isoformat()
    conn.execute('UPDATE experiments SET status=?, ended_at=?, conclusion=?, conclusion_by=?, guardrails_passed=?, updated_at=? WHERE id=?', (status,now_iso,conclusion,int(concluded_by),1 if passed else 0,now_iso,int(experiment_id)))
    conn.commit()
    return {'experiment_id':int(experiment_id),'status':status,'conclusion':conclusion,'guardrails_passed':passed,'report':report}
