from pathlib import Path
import ast, sys
ROOT=Path(__file__).resolve().parents[1]
checks=[
 ('mastery_services.py', (ROOT/'mastery_services.py').exists()),
 ('Team Hub backend enhancements', all(x in (ROOT/'permissions_comm.py').read_text() for x in ['search_messages','pin_message','list_notifications','set_presence'])),
 ('Institutional Memory UI', (ROOT/'templates/admin/institutional_memory.html').exists()),
 ('Feature Flags UI', (ROOT/'templates/admin/feature_flags.html').exists()),
 ('Experiments UI', (ROOT/'templates/admin/experiments.html').exists()),
 ('ASVS matrix', (ROOT/'TITAN/OWASP_ASVS_MATRIX.md').exists()),
 ('ASVS gate', (ROOT/'scripts/check_asvs_matrix.py').exists()),
 ('Trace header', 'X-Trace-ID' in (ROOT/'app.py').read_text()),
 ('Schema contract additions', 'feature_flags' in (ROOT/'schema_contract.py').read_text()),
]
failed=[n for n,ok in checks if not ok]
for n,ok in checks: print(('PASS' if ok else 'FAIL')+' - '+n)
print('ROUND19_MASTERY_GATE_PASS' if not failed else 'ROUND19_MASTERY_GATE_FAIL')
sys.exit(0 if not failed else 1)
