#!/usr/bin/env python3
"""Production-safe SQLite backup/restore/verification helpers for Virtual Store.

Designed for the project's existing sqlite3-based data layer.  It deliberately
uses SQLite's online backup API rather than raw file copying so backups are
consistent even while the application is open.
"""
from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
import sys
import tempfile
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


def _connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def verify(path: str) -> tuple[bool, list[str]]:
    problems: list[str] = []
    p = Path(path)
    if not p.exists():
        return False, [f"Database does not exist: {p}"]
    try:
        with _connect(str(p)) as conn:
            integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
            if integrity != "ok":
                problems.append(f"integrity_check: {integrity}")
            foreign_keys = conn.execute("PRAGMA foreign_key_check").fetchall()
            if foreign_keys:
                problems.append(f"foreign_key_check returned {len(foreign_keys)} violations")
            user_tables = conn.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            ).fetchone()[0]
            if user_tables <= 0:
                problems.append("database contains no application tables")
    except sqlite3.DatabaseError as exc:
        problems.append(str(exc))
    return not problems, problems



def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _db_fingerprint(path: str) -> str:
    with _connect(path) as conn:
        rows = conn.execute(
            "SELECT name, type, sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name"
        ).fetchall()
        table_counts = []
        for row in rows:
            if row[1] == "table":
                table_counts.append((row[0], int(conn.execute(f"SELECT COUNT(*) FROM [{row[0].replace(chr(93), chr(93)+chr(93))}]" ).fetchone()[0])))
        payload = {"schema": [(r[0], r[1], r[2]) for r in rows], "counts": table_counts}
        return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


def _manifest_path(db_path: Path) -> Path:
    return db_path.with_suffix(db_path.suffix + ".manifest.json")


def _write_manifest(db_path: Path, *, source: str) -> Path:
    manifest = {
        "format": 1,
        "artifact": db_path.name,
        "source": source,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "size_bytes": db_path.stat().st_size,
        "sha256": _sha256(db_path),
        "database_fingerprint": _db_fingerprint(str(db_path)),
    }
    out = _manifest_path(db_path)
    out.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return out


def verify_manifest(db_path: str) -> tuple[bool, list[str]]:
    db = Path(db_path)
    manifest_path = _manifest_path(db)
    if not manifest_path.exists():
        return True, []
    problems=[]
    try:
        data=json.loads(manifest_path.read_text())
        if int(data.get("format",0)) != 1:
            problems.append("unsupported backup manifest format")
        if data.get("sha256") != _sha256(db):
            problems.append("backup SHA-256 does not match manifest")
        if data.get("size_bytes") != db.stat().st_size:
            problems.append("backup size does not match manifest")
        ok, integrity_problems = verify(str(db))
        if not ok:
            problems.extend(integrity_problems)
        if data.get("database_fingerprint") != _db_fingerprint(str(db)):
            problems.append("database fingerprint does not match manifest")
    except Exception as exc:
        problems.append(f"invalid backup manifest: {exc}")
    return not problems, problems

def backup(source: str, destination: str) -> Path:
    src = Path(source)
    dst = Path(destination)
    dst.parent.mkdir(parents=True, exist_ok=True)
    if not src.exists():
        raise FileNotFoundError(src)

    ok, problems = verify(str(src))
    if not ok:
        raise RuntimeError("Refusing to back up an invalid database: " + "; ".join(problems))

    # Online backup API creates a consistent snapshot without copying a live
    # file byte-for-byte while pages may be changing.
    with _connect(str(src)) as src_conn, _connect(str(dst)) as dst_conn:
        src_conn.backup(dst_conn)
        dst_conn.execute("PRAGMA journal_mode")

    ok, problems = verify(str(dst))
    if not ok:
        dst.unlink(missing_ok=True)
        raise RuntimeError("Backup verification failed: " + "; ".join(problems))
    _write_manifest(dst, source=str(src))
    ok, problems = verify_manifest(str(dst))
    if not ok:
        dst.unlink(missing_ok=True)
        _manifest_path(dst).unlink(missing_ok=True)
        raise RuntimeError("Backup manifest verification failed: " + "; ".join(problems))
    return dst


def restore(backup_path: str, destination: str, *, force: bool = False) -> Path:
    src = Path(backup_path)
    dst = Path(destination)
    if not src.exists():
        raise FileNotFoundError(src)
    ok, problems = verify(str(src))
    if not ok:
        raise RuntimeError("Refusing to restore an invalid backup: " + "; ".join(problems))
    ok, problems = verify_manifest(str(src))
    if not ok:
        raise RuntimeError("Refusing to restore a backup with an invalid manifest: " + "; ".join(problems))
    if dst.exists() and not force:
        raise FileExistsError(f"Destination exists: {dst}. Use --force to replace it.")

    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkstemp(prefix="restore-", suffix=".db", dir=str(dst.parent))[1])
    try:
        with _connect(str(src)) as src_conn, _connect(str(tmp)) as dst_conn:
            src_conn.backup(dst_conn)
        ok, problems = verify(str(tmp))
        if not ok:
            raise RuntimeError("Restored copy failed verification: " + "; ".join(problems))
        os.replace(tmp, dst)
        manifest = _manifest_path(src)
        if manifest.exists():
            _manifest_path(dst).write_text(manifest.read_text())
            ok, problems = verify_manifest(str(dst))
            if not ok:
                raise RuntimeError("Restored database failed manifest verification: " + "; ".join(problems))
        else:
            _write_manifest(dst, source=str(src))
        return dst
    finally:
        tmp.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Virtual Store database maintenance")
    sub = parser.add_subparsers(dest="command", required=True)

    p_verify = sub.add_parser("verify")
    p_verify.add_argument("database")

    p_backup = sub.add_parser("backup")
    p_backup.add_argument("database")
    p_backup.add_argument("destination")

    p_manifest = sub.add_parser("verify-manifest")
    p_manifest.add_argument("database")

    p_restore = sub.add_parser("restore")
    p_restore.add_argument("backup")
    p_restore.add_argument("destination")
    p_restore.add_argument("--force", action="store_true")

    args = parser.parse_args(argv)
    try:
        if args.command == "verify":
            ok, problems = verify(args.database)
            if ok:
                print(f"OK: {args.database}")
                return 0
            for problem in problems:
                print(f"ERROR: {problem}", file=sys.stderr)
            return 1
        if args.command == "backup":
            out = backup(args.database, args.destination)
            print(f"Backup verified: {out}")
            return 0
        if args.command == "verify-manifest":
            ok, problems = verify_manifest(args.database)
            if ok:
                print(f"OK: backup manifest {args.database}")
                return 0
            for problem in problems:
                print(f"ERROR: {problem}", file=sys.stderr)
            return 1
        if args.command == "restore":
            out = restore(args.backup, args.destination, force=args.force)
            print(f"Restore verified: {out}")
            return 0
    except Exception as exc:  # CLI should be useful in cron/deploy logs
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
