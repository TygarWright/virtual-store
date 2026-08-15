from pathlib import Path
import ast

ROOT=Path(__file__).resolve().parents[1]
for p in ROOT.rglob('*.py'):
    if any(x in p.parts for x in ('.venv','.git')): continue
    ast.parse(p.read_text(encoding='utf-8'))
admin=(ROOT/'templates/admin')
for p in admin.rglob('*.html'):
    text=p.read_text(errors='ignore')
    assert '🛒' not in text and '🔔' not in text and '⚙' not in text, p
assert (ROOT/'static/favicon.svg').read_text().lstrip().startswith('<svg')
assert 'repair_missing_columns' in (ROOT/'database.py').read_text()
assert 'integrity_hash' in (ROOT/'database.py').read_text()
print('PASS Round 12 mastery checks')
