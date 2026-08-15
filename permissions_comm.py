"""Lightweight internal team communications and notice helpers."""
from __future__ import annotations
from typing import Optional
import re
import database as db

def visible_conversations(conn, admin_id: int):
    return conn.execute(
        """SELECT c.*, a.username AS target_username,
                  (SELECT COUNT(*) FROM team_messages m WHERE m.conversation_id=c.id AND m.id > COALESCE((SELECT last_message_id FROM team_reads r WHERE r.conversation_id=c.id AND r.admin_id=?),0)) AS unread_count
           FROM team_conversations c
           LEFT JOIN admin_users a ON a.id=c.target_admin_id
           WHERE c.kind='global'
              OR (c.kind='role' AND (c.target_role='' OR EXISTS(SELECT 1 FROM admin_users me WHERE me.id=? AND me.role=c.target_role)))
              OR (c.kind='direct' AND (c.created_by=? OR c.target_admin_id=?))
              OR (c.kind='context')
           ORDER BY c.kind='global' DESC, c.updated_at DESC""",
        (admin_id, admin_id, admin_id, admin_id),
    ).fetchall()

def get_or_create_global(conn, created_by: int) -> int:
    row = conn.execute("SELECT id FROM team_conversations WHERE kind='global' LIMIT 1").fetchone()
    if row:
        return int(row[0])
    cur = conn.execute("INSERT INTO team_conversations (kind,title,created_by,created_at,updated_at) VALUES ('global','Global Team',?,?,?)", (created_by, db.now(), db.now()))
    conn.commit(); return int(cur.lastrowid)

def get_or_create_direct(conn, admin_a: int, admin_b: int) -> int:
    row = conn.execute(
        """SELECT id FROM team_conversations WHERE kind='direct' AND ((created_by=? AND target_admin_id=?) OR (created_by=? AND target_admin_id=?)) LIMIT 1""",
        (admin_a,admin_b,admin_b,admin_a),
    ).fetchone()
    if row: return int(row[0])
    cur = conn.execute("INSERT INTO team_conversations (kind,title,created_by,target_admin_id,created_at,updated_at) VALUES ('direct',?,?,?, ?,?)", ('direct', f'Direct', admin_a, admin_b, db.now(), db.now()))
    conn.commit(); return int(cur.lastrowid)

def add_message(conn, conversation_id: int, sender_admin_id: int, body: str, parent_message_id: int | None = None) -> int:
    body = (body or '').strip()
    if not body: raise ValueError('Message cannot be empty.')
    cols = {str(r[1]) for r in conn.execute("PRAGMA table_info(team_messages)").fetchall()}
    if 'parent_message_id' in cols:
        cur = conn.execute("INSERT INTO team_messages (conversation_id,sender_admin_id,body,parent_message_id,created_at) VALUES (?,?,?,?,?)", (conversation_id,sender_admin_id,body,parent_message_id,db.now()))
    else:
        cur = conn.execute("INSERT INTO team_messages (conversation_id,sender_admin_id,body,created_at) VALUES (?,?,?,?)", (conversation_id,sender_admin_id,body,db.now()))
    conn.execute("UPDATE team_conversations SET updated_at=? WHERE id=?", (db.now(),conversation_id))
    message_id = int(cur.lastrowid)
    try:
        from backend_kernel import publish_event
        publish_event(conn, topic="team.message.created", aggregate="team_message", aggregate_id=message_id, payload={"message_id": message_id, "conversation_id": int(conversation_id), "sender_admin_id": int(sender_admin_id), "parent_message_id": int(parent_message_id) if parent_message_id else None})
    except Exception:
        # Collaboration must remain available even if the optional event spine is unavailable.
        pass
    # Mention notifications: @username, case-insensitive, de-duplicated.
    usernames = {m.group(1).lower() for m in re.finditer(r'@([A-Za-z0-9_.-]{2,64})', body)}
    if usernames:
        placeholders = ','.join('?' for _ in usernames)
        rows = conn.execute(f"SELECT id, username FROM admin_users WHERE is_active=1 AND lower(username) IN ({placeholders})", tuple(usernames)).fetchall()
        for row in rows:
            if int(row['id']) == int(sender_admin_id):
                continue
            conn.execute("INSERT INTO team_notifications(admin_id,conversation_id,message_id,kind,title,body,created_at) VALUES(?,?,?,?,?,?,?)",
                         (int(row['id']), conversation_id, message_id, 'mention', 'You were mentioned in Team Hub', body[:500], db.now()))
    conn.commit(); return message_id

def messages(conn, conversation_id: int, admin_id: int, limit: int = 200):
    ok = conn.execute(
        """SELECT 1 FROM team_conversations c WHERE c.id=? AND (
            c.kind='global' OR c.kind='context' OR (c.kind='role' AND EXISTS(SELECT 1 FROM admin_users a WHERE a.id=? AND a.role=c.target_role))
            OR (c.kind='direct' AND (c.created_by=? OR c.target_admin_id=?)))""",
        (conversation_id,admin_id,admin_id,admin_id),
    ).fetchone()
    if not ok: return []
    return conn.execute("""SELECT m.*, a.username AS sender_name, p.username AS parent_sender_name, parent.body AS parent_body
        FROM team_messages m JOIN admin_users a ON a.id=m.sender_admin_id
        LEFT JOIN team_messages parent ON parent.id=m.parent_message_id
        LEFT JOIN admin_users p ON p.id=parent.sender_admin_id
        WHERE m.conversation_id=? ORDER BY m.id ASC LIMIT ?""", (conversation_id,limit)).fetchall()

def mark_read(conn, conversation_id: int, admin_id: int, last_message_id: int):
    conn.execute("INSERT INTO team_reads (conversation_id,admin_id,last_message_id) VALUES (?,?,?) ON CONFLICT(conversation_id,admin_id) DO UPDATE SET last_message_id=excluded.last_message_id", (conversation_id,admin_id,last_message_id))
    conn.commit()

def active_notices(conn):
    now = db.now()
    return conn.execute(
        """SELECT * FROM site_notices WHERE enabled=1 AND (starts_at IS NULL OR starts_at='' OR starts_at <= ?) AND (ends_at IS NULL OR ends_at='' OR ends_at >= ?) ORDER BY priority DESC, created_at DESC""",
        (now, now),
    ).fetchall()


def search_messages(conn, conversation_id: int, admin_id: int, query: str, limit: int = 100):
    allowed = conn.execute("SELECT 1 FROM team_conversations WHERE id=?", (conversation_id,)).fetchone()
    if not allowed:
        return []
    q = f"%{(query or '').strip()}%"
    return conn.execute("""SELECT m.*, a.username AS sender_name
        FROM team_messages m JOIN admin_users a ON a.id=m.sender_admin_id
        WHERE m.conversation_id=? AND m.body LIKE ?
        ORDER BY m.id DESC LIMIT ?""", (conversation_id, q, max(1, min(int(limit), 200)))).fetchall()

def pin_message(conn, message_id: int, admin_id: int) -> bool:
    exists = conn.execute("SELECT id FROM team_messages WHERE id=?", (message_id,)).fetchone()
    if not exists:
        return False
    row = conn.execute("SELECT 1 FROM team_message_pins WHERE message_id=?", (message_id,)).fetchone()
    if row:
        conn.execute("DELETE FROM team_message_pins WHERE message_id=?", (message_id,))
    else:
        conn.execute("INSERT INTO team_message_pins(message_id,pinned_by,pinned_at) VALUES(?,?,?)", (message_id,admin_id,db.now()))
    conn.commit(); return True

def list_notifications(conn, admin_id: int, unread_only: bool = False, limit: int = 100):
    sql = "SELECT * FROM team_notifications WHERE admin_id=?"
    params=[admin_id]
    if unread_only:
        sql += " AND read_at IS NULL"
    sql += " ORDER BY created_at DESC LIMIT ?"
    params.append(max(1,min(int(limit),200)))
    return conn.execute(sql, tuple(params)).fetchall()

def mark_notifications_read(conn, admin_id: int, notification_ids=None):
    ids = list(notification_ids or [])
    if ids:
        placeholders=','.join('?' for _ in ids)
        conn.execute(f"UPDATE team_notifications SET read_at=? WHERE admin_id=? AND id IN ({placeholders})", (db.now(),admin_id,*[int(x) for x in ids]))
    else:
        conn.execute("UPDATE team_notifications SET read_at=? WHERE admin_id=? AND read_at IS NULL", (db.now(),admin_id))
    conn.commit()

def set_presence(conn, admin_id: int, state: str = 'online'):
    state = state if state in {'online','away','offline'} else 'online'
    conn.execute("INSERT INTO admin_presence(admin_id,state,last_seen_at) VALUES(?,?,?) ON CONFLICT(admin_id) DO UPDATE SET state=excluded.state,last_seen_at=excluded.last_seen_at", (admin_id,state,db.now()))
    conn.commit()

def list_presence(conn, admin_ids=None):
    if admin_ids:
        placeholders=','.join('?' for _ in admin_ids)
        return conn.execute(f"SELECT p.*, a.username, a.role FROM admin_presence p JOIN admin_users a ON a.id=p.admin_id WHERE p.admin_id IN ({placeholders})", tuple(int(x) for x in admin_ids)).fetchall()
    return conn.execute("SELECT p.*, a.username, a.role FROM admin_presence p JOIN admin_users a ON a.id=p.admin_id ORDER BY a.username").fetchall()


def get_or_create_context(conn, *, context_type: str, context_id: int, created_by: int, title: str = "") -> int:
    context_type = (context_type or "").strip().lower()
    if context_type not in {"order", "customer", "ticket", "exception"}:
        raise ValueError("Unsupported conversation context")
    row = conn.execute(
        "SELECT id FROM team_conversations WHERE context_type=? AND context_id=? LIMIT 1",
        (context_type, int(context_id)),
    ).fetchone()
    if row:
        return int(row[0])
    display = (title or f"{context_type.title()} #{context_id}").strip()[:160]
    now = db.now()
    cur = conn.execute(
        "INSERT INTO team_conversations(kind,title,created_by,context_type,context_id,created_at,updated_at) VALUES('context',?,?,?,?,?,?)",
        (display, int(created_by), context_type, int(context_id), now, now),
    )
    conn.commit()
    return int(cur.lastrowid)

# Round 42: richer team conversations.
def reply_to_message(conn, conversation_id: int, sender_admin_id: int, parent_message_id: int, body: str) -> int:
    parent = conn.execute("SELECT id FROM team_messages WHERE id=? AND conversation_id=?", (int(parent_message_id), int(conversation_id))).fetchone()
    if not parent:
        raise ValueError('Parent message not found in this conversation.')
    message_id = add_message(conn, conversation_id, sender_admin_id, body, parent_message_id=int(parent_message_id))
    return message_id


def toggle_reaction(conn, message_id: int, admin_id: int, emoji: str) -> dict:
    emoji = (emoji or '').strip()[:32]
    if not emoji:
        raise ValueError('Reaction is required.')
    row = conn.execute("SELECT id FROM team_message_reactions WHERE message_id=? AND admin_id=? AND reaction=?", (int(message_id), int(admin_id), emoji)).fetchone()
    if row:
        conn.execute("DELETE FROM team_message_reactions WHERE id=?", (int(row['id']),))
        state = False
    else:
        conn.execute("INSERT INTO team_message_reactions(message_id,admin_id,reaction,created_at) VALUES(?,?,?,?)", (int(message_id), int(admin_id), emoji, db.now()))
        state = True
    conn.commit()
    return {'reacted': state, 'reaction': emoji}


def list_message_reactions(conn, message_ids):
    ids = [int(x) for x in (message_ids or [])]
    if not ids:
        return {}
    placeholders = ','.join('?' for _ in ids)
    rows = conn.execute(f"SELECT r.message_id, r.reaction, COUNT(*) AS count FROM team_message_reactions r WHERE r.message_id IN ({placeholders}) GROUP BY r.message_id, r.reaction ORDER BY r.message_id, r.reaction", tuple(ids)).fetchall()
    out = {}
    for row in rows:
        out.setdefault(int(row['message_id']), []).append({'reaction': row['reaction'], 'count': int(row['count'])})
    return out
