#!/bin/sh
set -eu
DB_PATH="${DB_PATH:-instance/store.db}"
BACKUP_DIR="${BACKUP_DIR:-backups}"
TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
BACKUP_FILE="${BACKUP_DIR}/store.db.backup.${TIMESTAMP}"
mkdir -p "$BACKUP_DIR"
python3 titan_db_tools.py backup "$DB_PATH" "$BACKUP_FILE"
