import os, sqlite3, tempfile, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from schema_contract import CRITICAL_COLUMNS, LAZY_TABLES, repair_missing_columns, missing_columns

fd, path = tempfile.mkstemp(suffix='.db'); os.close(fd)
try:
    c = sqlite3.connect(path)
    # Model a booted database where all mandatory/core tables exist with their
    # contract columns, while lazy subsystem tables have not been invoked yet.
    by_table = {}
    for spec in CRITICAL_COLUMNS:
        if spec.table in LAZY_TABLES:
            continue
        by_table.setdefault(spec.table, []).append(spec)
    for table, specs in by_table.items():
        cols=[]
        for i,s in enumerate(specs):
            typedef=s.definition
            # Avoid multiple PKs; the contract test only needs column presence.
            cols.append(f'"{s.name}" {typedef}')
        c.execute(f'CREATE TABLE "{table}" ({", ".join(cols)})')
    c.commit()
    missing = repair_missing_columns(c)
    assert not [m for m in missing if m.table in LAZY_TABLES]
    assert missing_columns(c) == []

    # Once a lazy table exists, a missing required column must still be repaired.
    c.execute('CREATE TABLE team_message_reactions (id INTEGER)')
    repaired = repair_missing_columns(c)
    names={(r.table,r.name) for r in repaired}
    assert ('team_message_reactions','message_id') in names
    cols={r[1] for r in c.execute('PRAGMA table_info(team_message_reactions)').fetchall()}
    assert 'message_id' in cols
    print('ROUND60_SCHEMA_BOOT_ACCEPTANCE_PASS')
finally:
    try: c.close()
    except Exception: pass
    os.unlink(path)
