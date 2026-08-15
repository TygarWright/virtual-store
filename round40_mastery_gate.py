from pathlib import Path
import ast, re
for f in ['analytics_mastery.py','mastery_services.py','blueprints/admin.py','database.py','schema_contract.py']:
    ast.parse(Path(f).read_text())
assert 'conclude_experiment' in Path('analytics_mastery.py').read_text()
assert 'effectiveness_score' in Path('mastery_services.py').read_text()
assert 'ALTER TABLE experiments ADD COLUMN conclusion' in Path('database.py').read_text()
assert 'ColumnSpec("experiments", "conclusion"' in Path('schema_contract.py').read_text()
assert 'Safely conclude' in Path('templates/admin/experiments.html').read_text()
assert 'effectiveness_score' in Path('templates/admin/institutional_memory.html').read_text()
print('ROUND40 STATIC MASTERY GATE: PASS')
