"""TITAN mastery services: institutional memory, feature flags and experiments."""
from __future__ import annotations
import hashlib, json
from datetime import datetime, timezone
from typing import Any


def now():
    return datetime.now(timezone.utc).isoformat()


def _stable_bucket(subject_key: str, flag_key: str) -> int:
    digest = hashlib.sha256(f"{flag_key}:{subject_key}".encode()).hexdigest()
    return int(digest[:8], 16) % 100


def upsert_feature_flag(conn, *, key: str, description: str = "", enabled: bool = False, rollout_percent: int = 0, updated_by: int | None = None):
    rollout = max(0, min(100, int(rollout_percent)))
    conn.execute("""INSERT INTO feature_flags(key,description,enabled,rollout_percent,updated_by,updated_at)\n                   VALUES(?,?,?,?,?,?)\n                   ON CONFLICT(key) DO UPDATE SET description=excluded.description, enabled=excluded.enabled, rollout_percent=excluded.rollout_percent, updated_by=excluded.updated_by, updated_at=excluded.updated_at""",
                 (key.strip(), description.strip(), 1 if enabled else 0, rollout, updated_by, now()))
    conn.commit()


def flag_enabled(conn, key: str, *, subject_key: str = "") -> bool:
    row = conn.execute("SELECT enabled, rollout_percent FROM feature_flags WHERE key=?", (key,)).fetchone()
    if not row or not int(row["enabled"]):
        return False
    rollout = int(row["rollout_percent"] or 0)
    if rollout >= 100:
        return True
    if rollout <= 0:
        return False
    return _stable_bucket(subject_key or "anonymous", key) < rollout


def create_or_update_experiment(conn, *, key: str, name: str, variants: list[str], allocation: dict[str, int] | None = None,
                                 primary_metric: str = "", status: str = "draft", created_by: int | None = None):
    key = (key or "").strip()
    name = (name or "").strip()
    variants = list(dict.fromkeys(str(v).strip() for v in (variants or []) if str(v).strip()))
    if not key or not name or not variants:
        raise ValueError("experiment key, name and at least one variant are required")
    allocation = allocation or {v: 100 // len(variants) for v in variants}
    allocation = {v: int(allocation.get(v, 0)) for v in variants}
    remainder = 100 - sum(allocation.values())
    if remainder:
        allocation[variants[-1]] += remainder
    if any(v < 0 for v in allocation.values()) or sum(allocation.values()) != 100:
        raise ValueError("experiment allocation must total 100")
    status = (status or "draft").strip().lower()
    allowed_status = {"draft", "running", "paused", "concluded", "archived"}
    if status not in allowed_status:
        raise ValueError("invalid experiment status")
    now_iso = now()
    existing = conn.execute("SELECT started_at, ended_at FROM experiments WHERE key=?", (key,)).fetchone()
    started_at = existing["started_at"] if existing else None
    ended_at = existing["ended_at"] if existing else None
    if status == "running" and not started_at:
        started_at = now_iso
    if status in {"concluded", "archived"} and not ended_at:
        ended_at = now_iso
    conn.execute(
        """INSERT INTO experiments(key,name,status,variants_json,allocation_json,primary_metric,started_at,ended_at,created_by,created_at,updated_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(key) DO UPDATE SET name=excluded.name,status=excluded.status,variants_json=excluded.variants_json,
             allocation_json=excluded.allocation_json,primary_metric=excluded.primary_metric,started_at=excluded.started_at,
             ended_at=excluded.ended_at,updated_at=excluded.updated_at""",
        (key, name, status, json.dumps(variants), json.dumps(allocation, sort_keys=True), primary_metric.strip(), started_at, ended_at, created_by, now_iso, now_iso),
    )
    conn.commit()

def ensure_experiment_mastery_schema(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS experiment_exposure_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT, experiment_id INTEGER NOT NULL, subject_key TEXT NOT NULL,
        variant TEXT NOT NULL, exposed_at TEXT NOT NULL, UNIQUE(experiment_id, subject_key)
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS experiment_guardrail_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT, experiment_id INTEGER NOT NULL, metric TEXT NOT NULL,
        observed_percent REAL NOT NULL, threshold REAL NOT NULL, comparator TEXT NOT NULL,
        passed INTEGER NOT NULL, evaluated_at TEXT NOT NULL
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_exp_guard_hist ON experiment_guardrail_history(experiment_id, evaluated_at DESC)")
    conn.commit()


def assign_experiment(conn, *, experiment_key: str, subject_key: str) -> str | None:
    ensure_experiment_mastery_schema(conn)
    exp = conn.execute("SELECT * FROM experiments WHERE key=? AND status='running'", (experiment_key,)).fetchone()
    if not exp:
        return None
    existing = conn.execute("SELECT variant FROM experiment_assignments WHERE experiment_id=? AND subject_key=?", (exp["id"], subject_key)).fetchone()
    if existing:
        return str(existing[0])
    variants = json.loads(exp["variants_json"] or "[]")
    allocation = json.loads(exp["allocation_json"] or "{}")
    bucket = _stable_bucket(subject_key, experiment_key)
    cursor = 0
    chosen = variants[-1] if variants else None
    for variant in variants:
        cursor += int(allocation.get(variant, 0))
        if bucket < cursor:
            chosen = variant
            break
    if chosen is None:
        return None
    now_iso=now()
    conn.execute("INSERT INTO experiment_assignments(experiment_id,subject_key,variant,assigned_at) VALUES(?,?,?,?)", (exp["id"], subject_key, chosen, now_iso))
    conn.execute("INSERT INTO experiment_exposure_events(experiment_id,subject_key,variant,exposed_at) VALUES(?,?,?,?)", (exp["id"], subject_key, chosen, now_iso))
    conn.commit()
    return chosen


def record_guardrail_evaluation(conn, *, experiment_id:int, results:list[dict], evaluated_at:str|None=None):
    ensure_experiment_mastery_schema(conn)
    stamp=evaluated_at or now()
    for r in results:
        conn.execute("INSERT INTO experiment_guardrail_history(experiment_id,metric,observed_percent,threshold,comparator,passed,evaluated_at) VALUES(?,?,?,?,?,?,?)", (int(experiment_id),str(r['metric']),float(r['observed_percent']),float(r['threshold']),str(r['comparator']),1 if r['passed'] else 0,stamp))
    conn.commit()


def guardrail_history(conn, experiment_id:int, *, days:int=90)->list[dict]:
    ensure_experiment_mastery_schema(conn)
    cutoff=(datetime.now(timezone.utc)-__import__('datetime').timedelta(days=max(1,int(days)))).isoformat()
    rows=conn.execute("SELECT metric,observed_percent,threshold,comparator,passed,evaluated_at FROM experiment_guardrail_history WHERE experiment_id=? AND evaluated_at>=? ORDER BY evaluated_at DESC, metric", (int(experiment_id),cutoff)).fetchall()
    return [dict(r) for r in rows]



def index_memory(conn, *, source_type: str, source_id: int, title: str, body: str, keywords: str = ""):
    now_iso = now()
    conn.execute("""INSERT INTO institutional_memory_index(source_type,source_id,title,body,keywords,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?)
                   ON CONFLICT(source_type,source_id) DO UPDATE SET title=excluded.title,body=excluded.body,keywords=excluded.keywords,updated_at=excluded.updated_at""",
                 (source_type, int(source_id), title.strip(), body.strip(), keywords.strip(), now_iso, now_iso))
    _ensure_memory_link_schema(conn)
    _refresh_memory_links(conn, source_type=source_type, source_id=int(source_id))
    conn.commit()


def _ensure_memory_link_schema(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS institutional_memory_links (
        id INTEGER PRIMARY KEY AUTOINCREMENT, source_type TEXT NOT NULL, source_id INTEGER NOT NULL,
        related_type TEXT NOT NULL, related_id INTEGER NOT NULL, relation TEXT NOT NULL DEFAULT 'related',
        score INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL,
        UNIQUE(source_type, source_id, related_type, related_id, relation)
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_memory_links_source ON institutional_memory_links(source_type, source_id, score DESC)")


def _refresh_memory_links(conn, *, source_type: str, source_id: int):
    row = conn.execute("SELECT title, keywords FROM institutional_memory_index WHERE source_type=? AND source_id=?", (source_type, int(source_id))).fetchone()
    if not row:
        return
    tokens={w.lower() for w in (str(row['title'])+' '+str(row['keywords'])).replace(',',' ').split() if len(w)>=4}
    if not tokens:
        return
    rows=conn.execute("SELECT source_type,source_id,title,keywords FROM institutional_memory_index WHERE NOT (source_type=? AND source_id=?) ORDER BY updated_at DESC LIMIT 500", (source_type,int(source_id))).fetchall()
    now_iso=now()
    for other in rows:
        other_tokens={w.lower() for w in (str(other['title'])+' '+str(other['keywords'])).replace(',',' ').split() if len(w)>=4}
        score=len(tokens & other_tokens)
        if score <= 0:
            continue
        conn.execute("""INSERT INTO institutional_memory_links(source_type,source_id,related_type,related_id,relation,score,created_at)
                       VALUES(?,?,?,?,?,?,?) ON CONFLICT(source_type,source_id,related_type,related_id,relation)
                       DO UPDATE SET score=excluded.score,created_at=excluded.created_at""",
                     (source_type,int(source_id),str(other['source_type']),int(other['source_id']),'keyword_overlap',int(score),now_iso))


def memory_links(conn, source_type: str, source_id: int, *, limit: int = 20):
    _ensure_memory_link_schema(conn)
    rows=conn.execute("SELECT l.*,m.title,m.body,m.keywords FROM institutional_memory_links l JOIN institutional_memory_index m ON m.source_type=l.related_type AND m.source_id=l.related_id WHERE l.source_type=? AND l.source_id=? ORDER BY l.score DESC,l.created_at DESC LIMIT ?", (source_type,int(source_id),max(1,min(int(limit),100)))).fetchall()
    return [dict(r) for r in rows]


def memory_source_types(conn):
    rows = conn.execute(
        "SELECT DISTINCT source_type FROM institutional_memory_index WHERE source_type != '' ORDER BY source_type"
    ).fetchall()
    return [str(r[0]) for r in rows]


def search_memory(conn, query: str, limit: int = 50, source_type: str = ""):
    limit = max(1, min(int(limit), 200))
    query = (query or "").strip()
    source_type = (source_type or "").strip()
    clauses=[]; params=[]
    if source_type:
        clauses.append("source_type = ?"); params.append(source_type)
    if query:
        like=f"%{query}%"
        clauses.append("(title LIKE ? OR body LIKE ? OR keywords LIKE ?)")
        params.extend([like,like,like])
        order="ORDER BY CASE WHEN title = ? THEN 0 WHEN title LIKE ? THEN 1 WHEN keywords LIKE ? THEN 2 ELSE 3 END, updated_at DESC"
        params.extend([query,like,like])
    else:
        order="ORDER BY updated_at DESC"
    where=f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params.append(limit)
    return conn.execute(f"SELECT * FROM institutional_memory_index {where} {order} LIMIT ?", tuple(params)).fetchall()

def record_decision_outcome(conn, *, decision_id: int, outcome: str, lesson: str, reviewed_by: int | None = None, future_recommendation: str = "", effectiveness: str = "effective", effectiveness_score: int | None = None):
    cols = {r[1] for r in conn.execute("PRAGMA table_info(decision_journal)").fetchall()}
    if "lesson" not in cols:
        conn.execute("ALTER TABLE decision_journal ADD COLUMN lesson TEXT NOT NULL DEFAULT ''")
    if "reviewed_by" not in cols:
        conn.execute("ALTER TABLE decision_journal ADD COLUMN reviewed_by INTEGER")
    if "future_recommendation" not in cols:
        conn.execute("ALTER TABLE decision_journal ADD COLUMN future_recommendation TEXT NOT NULL DEFAULT ''")
    allowed={"effective","mixed","ineffective","inconclusive"}
    effectiveness=(effectiveness or "inconclusive").strip().lower()
    if effectiveness not in allowed:
        raise ValueError("invalid effectiveness value")
    if effectiveness_score is not None:
        effectiveness_score=max(0,min(100,int(effectiveness_score)))
    reviewed_at = now()
    if not conn.execute("SELECT id FROM decision_journal WHERE id=?", (int(decision_id),)).fetchone():
        raise ValueError("decision not found")
    conn.execute("""INSERT INTO decision_review_history
        (decision_id, reviewed_by, outcome, lesson, future_recommendation, effectiveness, effectiveness_score, created_at)
        VALUES(?,?,?,?,?,?,?,?)""",
        (int(decision_id), reviewed_by, outcome.strip(), lesson.strip(), future_recommendation.strip(), effectiveness, effectiveness_score, reviewed_at))
    conn.execute("UPDATE decision_journal SET outcome=?, lesson=?, reviewed_by=?, reviewed_at=?, future_recommendation=?, effectiveness=?, effectiveness_score=? WHERE id=?",
                 (outcome.strip(), lesson.strip(), reviewed_by, reviewed_at, future_recommendation.strip(), effectiveness, effectiveness_score, int(decision_id)))
    conn.commit()
    row = conn.execute("SELECT * FROM decision_journal WHERE id=?", (int(decision_id),)).fetchone()
    if row:
        index_memory(conn, source_type="decision", source_id=int(decision_id), title=row["title"], body=" ".join([row["decision"], row["reason"], row["expected_result"], row["outcome"], row["lesson"], row["future_recommendation"]]), keywords="decision institutional memory lesson recommendation")


def memory_health(conn, *, stale_days: int = 180) -> dict:
    stale_days=max(1,min(int(stale_days),3650)); cutoff=datetime.now(timezone.utc).timestamp()-stale_days*86400
    rows=conn.execute("SELECT source_type, source_id, title, updated_at FROM institutional_memory_index ORDER BY updated_at ASC").fetchall()
    stale=[]; fresh=0
    for r in rows:
        try: ts=datetime.fromisoformat(str(r['updated_at']).replace('Z','+00:00')).timestamp()
        except Exception: ts=0
        if ts < cutoff: stale.append(dict(r))
        else: fresh += 1
    return {'total':len(rows),'fresh':fresh,'stale':len(stale),'stale_days':stale_days,'stale_items':stale[:200]}


def related_memory(conn, source_type: str, source_id: int, *, limit: int = 12):
    linked = memory_links(conn, source_type, source_id, limit=limit)
    if linked:
        return linked
    current=conn.execute("SELECT title, body, keywords FROM institutional_memory_index WHERE source_type=? AND source_id=?",(source_type,int(source_id))).fetchone()
    if not current: return []
    tokens={w.lower() for w in (str(current['title'])+' '+str(current['keywords'])).replace(',',' ').split() if len(w)>=4}
    rows=conn.execute("SELECT * FROM institutional_memory_index WHERE NOT (source_type=? AND source_id=?) ORDER BY updated_at DESC LIMIT 300",(source_type,int(source_id))).fetchall()
    scored=[]
    for r in rows:
        other={w.lower() for w in (str(r['title'])+' '+str(r['keywords'])).replace(',',' ').split() if len(w)>=4}
        score=len(tokens & other)
        if score: scored.append((score,str(r['updated_at']),dict(r)))
    scored.sort(key=lambda x:(-x[0],x[1]))
    return [item|{'relevance':score} for score,_,item in scored[:max(1,min(int(limit),50))]]


def decision_review_history(conn, decision_id: int, *, limit: int = 50):
    rows=conn.execute("SELECT * FROM decision_review_history WHERE decision_id=? ORDER BY created_at DESC LIMIT ?", (int(decision_id), max(1,min(int(limit),100)))).fetchall()
    return [dict(r) for r in rows]


def decision_effectiveness_report(conn, *, stale_days: int = 180) -> dict:
    rows=conn.execute("SELECT id,title,effectiveness,effectiveness_score,reviewed_at,review_due_at FROM decision_journal ORDER BY created_at DESC LIMIT 500").fetchall()
    counts={"effective":0,"mixed":0,"ineffective":0,"inconclusive":0,"unreviewed":0}
    reviewed=0; due=0
    cutoff=(datetime.now(timezone.utc).timestamp()-max(1,int(stale_days))*86400)
    for r in rows:
        eff=str(r['effectiveness'] or 'unreviewed'); counts[eff]=counts.get(eff,0)+1
        if r['reviewed_at']:
            reviewed+=1
            try:
                ts=datetime.fromisoformat(str(r['reviewed_at']).replace('Z','+00:00')).timestamp()
                if ts < cutoff: due+=1
            except Exception: pass
    return {"total":len(rows),"reviewed":reviewed,"review_due_or_stale":due,"effectiveness":counts}
