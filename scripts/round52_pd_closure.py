from pathlib import Path
import ast,re,sys
ROOT=Path(__file__).resolve().parents[1]
errors=[]
# Team/support surface requirements
team=(ROOT/'templates/admin/team_hub.html').read_text(errors='ignore')
support=(ROOT/'templates/admin/support_cockpit.html').read_text(errors='ignore')
css=(ROOT/'static/css/titan-ui.css').read_text(errors='ignore')
for needle in ['team-filter','teamReplyDialog','/admin/team-hub/reply','/admin/team-hub/reaction','/admin/team-hub/search','/admin/team-hub/notifications','/admin/team-hub/presence']:
    if needle not in team: errors.append(f'team hub missing {needle}')
if 'prompt(' in team: errors.append('team hub still uses blocking prompt UI')
for needle in ['Discuss with team','customer_context_conversations','selected_order','Open order','Support Cockpit']:
    if needle not in support: errors.append(f'support cockpit missing {needle}')
for needle in ['.titan-dialog','.team-filter--active','.titan-support-order-focus','.team-hub-filter-row']:
    if needle not in css: errors.append(f'missing UX primitive {needle}')
# Python syntax gate
for p in ROOT.rglob('*.py'):
    try: ast.parse(p.read_text(errors='ignore'))
    except Exception as exc: errors.append(f'python parse {p.relative_to(ROOT)}: {exc}')
# Ensure no emoji UI in admin templates.
emoji=re.compile(r'[\U0001F300-\U0001FAFF]')
for p in (ROOT/'templates/admin').rglob('*.html'):
    if emoji.search(p.read_text(errors='ignore')): errors.append(f'emoji in admin template {p.relative_to(ROOT)}')
print('ROUND52_TEAM_SUPPORT_UX_CLOSURE=' + ('PASS' if not errors else 'FAIL'))
for e in errors: print(' -',e)
sys.exit(1 if errors else 0)
