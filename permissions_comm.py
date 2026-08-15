"""Lightweight internal team communications and notice helpers."""
from __future__ import annotations
from typing import Optional
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

def add_message(conn, conversation_id: int, sender_admin_id: int, body: str) -> int:
    body = (body or '').strip()
    if not body: raise ValueError('Message cannot be empty.')
    cur = conn.execute("INSERT INTO team_messages (conversation_id,sender_admin_id,body,created_at) VALUES (?,?,?,?)", (conversation_id,sender_admin_id,body,db.now()))
    conn.execute("UPDATE team_conversations SET updated_at=? WHERE id=?", (db.now(),conversation_id))
    conn.commit(); return int(cur.lastrowid)

def messages(conn, conversation_id: int, admin_id: int, limit: int = 200):
    ok = conn.execute(
        """SELECT 1 FROM team_conversations c WHERE c.id=? AND (
            c.kind='global' OR (c.kind='role' AND EXISTS(SELECT 1 FROM admin_users a WHERE a.id=? AND a.role=c.target_role))
            OR (c.kind='direct' AND (c.created_by=? OR c.target_admin_id=?)))""",
        (conversation_id,admin_id,admin_id,admin_id),
    ).fetchone()
    if not ok: return []
    return conn.execute("""SELECT m.*, a.username AS sender_name FROM team_messages m JOIN admin_users a ON a.id=m.sender_admin_id WHERE m.conversation_id=? ORDER BY m.id ASC LIMIT ?""", (conversation_id,limit)).fetchall()

def mark_read(conn, conversation_id: int, admin_id: int, last_message_id: int):
    conn.execute("INSERT INTO team_reads (conversation_id,admin_id,last_message_id) VALUES (?,?,?) ON CONFLICT(conversation_id,admin_id) DO UPDATE SET last_message_id=excluded.last_message_id", (conversation_id,admin_id,last_message_id))
    conn.commit()

def active_notices(conn):
    now = db.now()
    return conn.execute(
        """SELECT * FROM site_notices WHERE enabled=1 AND (starts_at IS NULL OR starts_at='' OR starts_at <= ?) AND (ends_at IS NULL OR ends_at='' OR ends_at >= ?) ORDER BY priority DESC, created_at DESC""",
        (now, now),
    ).fetchall()
