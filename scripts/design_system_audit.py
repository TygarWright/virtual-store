from pathlib import Path
import re, sys
ROOT=Path(__file__).resolve().parents[1]
T=ROOT/'templates/admin'; CSS=ROOT/'static/css/titan-ui.css'
errors=[]
for p in T.rglob('*.html'):
    txt=p.read_text(errors='ignore')
    if p.name not in {'base.html','_icons.html','login.html','team.html'} and '{% extends "admin/base.html" %}' not in txt:
        errors.append(f'{p.relative_to(ROOT)}: missing admin/base.html extension')
    if re.search(r'(?i)([\U0001F300-\U0001FAFF])', txt):
        errors.append(f'{p.relative_to(ROOT)}: emoji in admin UI markup')
css=CSS.read_text(errors='ignore')
for token in ['--titan-space-1','--titan-space-7','--titan-control-md','--titan-focus-ring','--titan-surface-2']:
    if token not in css: errors.append(f'missing design token {token}')
for cls in ['.titan-surface','.titan-cluster','.titan-stack','.titan-visually-hidden','.titan-definition-list']:
    if cls not in css: errors.append(f'missing canonical class {cls}')
print(f'Design-system audit: {"PASS" if not errors else "FAIL"}')
for e in errors: print(e)
sys.exit(1 if errors else 0)
