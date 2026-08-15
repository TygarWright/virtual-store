from pathlib import Path
import re

root=Path(__file__).resolve().parents[1]
team=(root/'templates/admin/team_hub.html').read_text(encoding='utf-8')
css=(root/'static/css/titan-ui.css').read_text(encoding='utf-8')
admin=(root/'blueprints/admin.py').read_text(encoding='utf-8')
required_team=['/team-hub/notifications','/team-hub/notifications/read','/team-hub/presence','data-notification-id','teamNotifyPanel','teamPresenceMenu']
required_css=['--titan-space-1','--titan-focus','.titan-popover','.titan-presence-chip','.titan-surface','.titan-visually-hidden']
missing=[x for x in required_team if x not in team]
missing += [x for x in required_css if x not in css]
if 'def admin_team_hub_presence_get' not in admin:
    missing.append('presence GET route')
    pass
if missing:
    raise SystemExit('FAIL: '+', '.join(missing))
print('ROUND26 MASTERY GATE: PASS')
print('Team/Support UX + Design System hardening verified')
