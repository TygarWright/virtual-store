#!/bin/sh
set -eu
BACKUP_PATH="${1:?Usage: restore_db.sh BACKUP_PATH [DESTINATION] [--force]}"
DESTINATION="${2:-instance/store.db.restored}"
python3 titan_db_tools.py restore "$BACKUP_PATH" "$DESTINATION" --force
