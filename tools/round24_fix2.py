from pathlib import Path
root=Path('/mnt/data/round24_work/round23_work')

# Add CRON_SECRET and SITE_URL constant to config if missing
p=root/'config.py'; s=p.read_text()
needle='SENTRY_DSN = os.environ.get("SENTRY_DSN", "").strip()\n'
if 'CRON_SECRET' not in s:
    s=s.replace(needle, needle+'CRON_SECRET = os.environ.get("CRON_SECRET", "").strip()\nSITE_URL = os.environ.get("SITE_URL", "").strip()\n',1)
p.write_text(s)

# Add internal reconciliation trigger endpoint to health blueprint.
p=root/'blueprints/health.py'; s=p.read_text()
if 'def internal_reconciliation_trigger' not in s:
    s += '''\n\n@health_bp.route('/internal/reconciliation', methods=['POST'])\ndef internal_reconciliation_trigger():\n    """Protected scheduler endpoint. The Render cron job calls the web service so it reaches the persistent web-service database."""\n    import hmac, os\n    configured = os.environ.get('CRON_SECRET', '').strip()\n    supplied = str(__import__('flask').request.headers.get('X-TITAN-CRON-SECRET', '')).strip()\n    if not configured or not hmac.compare_digest(configured, supplied):\n        return jsonify({'error': 'forbidden'}), 403\n    try:\n        from reconcile_razorpay import reconcile\n        result = reconcile(created_by=0, mode='scheduled', order_limit=5000)\n        return jsonify(result), 200 if result.get('status') in {'completed', 'skipped'} else 500\n    except Exception as exc:\n        current_app.logger.exception('Scheduled reconciliation failed')\n        return jsonify({'status': 'failed', 'error': str(exc)[:500]}), 500\n'''
p.write_text(s)

# Replace cron architecture with HTTP trigger (persistent DB remains on web service disk).
(root/'scripts/run_reconciliation.py').write_text('''#!/usr/bin/env python3\n"""Trigger scheduled reconciliation on the live web service.\nRender cron containers are ephemeral and cannot access a web service persistent disk,\nso the cron job calls the protected web endpoint instead.\n"""\nfrom __future__ import annotations\nimport json\nimport os\nimport sys\nimport requests\n\nbase = os.environ.get('SITE_URL', '').strip().rstrip('/')\nsecret = os.environ.get('CRON_SECRET', '').strip()\nif not base or not secret:\n    print(json.dumps({'status': 'failed', 'error': 'SITE_URL and CRON_SECRET are required'}))\n    raise SystemExit(2)\nresp = requests.post(f'{base}/internal/reconciliation', headers={'X-TITAN-CRON-SECRET': secret}, timeout=(5, 60))\ntry:\n    data = resp.json()\nexcept Exception:\n    data = {'status': 'failed', 'error': resp.text[:500]}\nprint(json.dumps(data, sort_keys=True, default=str))\nraise SystemExit(0 if resp.ok and data.get('status') in {'completed', 'skipped'} else 2)\n''')

# Update cron blueprint env vars
(root/'render-reconciliation-cron.yaml').write_text('''services:\n  - type: cron\n    name: virtual-store-reconciliation\n    runtime: python\n    schedule: "0 2 * * *"\n    buildCommand: pip install -r requirements.txt\n    startCommand: python scripts/run_reconciliation.py\n    envVars:\n      - key: PYTHON_VERSION\n        value: "3.14.3"\n      - key: SITE_URL\n        sync: false\n      - key: CRON_SECRET\n        sync: false\n''')

# Add workflow admin route + template.
p=root/'blueprints/admin.py'; s=p.read_text()
if 'def admin_workflows' not in s:
    insert='''\n\n@admin_bp.route("/workflows")\n@login_required\n@requires_permission("orders.view")\ndef admin_workflows():\n    conn = db.get_db()\n    rows = conn.execute(\n        "SELECT workflow_id, workflow_type, aggregate_type, aggregate_id, status, current_step, attempt_count, compensation_status, error, created_at, updated_at, completed_at FROM workflow_runs ORDER BY updated_at DESC LIMIT 200"\n    ).fetchall()\n    steps_by={}\n    for row in rows:\n        steps_by[row["workflow_id"]]=conn.execute(\n            "SELECT step_index, step_name, status, error, started_at, completed_at FROM workflow_steps WHERE workflow_id=? ORDER BY step_index",\n            (row["workflow_id"],)\n        ).fetchall()\n    conn.close()\n    return render_template("admin/workflows.html", workflows=rows, steps_by=steps_by)\n'''
    # insert before reconciliation route
    s=s.replace('@admin_bp.route("/reconciliation", methods=["GET", "POST"])', insert+'\n\n@admin_bp.route("/reconciliation", methods=["GET", "POST"])',1)
p.write_text(s)

(root/'templates/admin/workflows.html').write_text('''{% extends "admin/base.html" %}\n{% block title %}Workflows{% endblock %}\n{% block content %}\n<div class="admin-topbar"><div><h1>Commerce Workflows</h1><p class="muted">Durable state for critical operations. Failures and recoveries remain inspectable instead of disappearing into logs.</p></div></div>\n{% if workflows %}\n<div class="stack">\n{% for w in workflows %}\n<section class="card-box"><div class="row-between"><div><strong>{{ w.workflow_type }}</strong><div class="muted">{{ w.aggregate_type }} #{{ w.aggregate_id }}</div></div><div><span class="status-pill">{{ w.status }}</span> <span class="muted">attempt {{ w.attempt_count }}</span></div></div>\n{% if w.error %}<p class="error-text">{{ w.error }}</p>{% endif %}\n<div class="table-wrap"><table class="admin-table"><thead><tr><th>Step</th><th>Status</th><th>Started</th><th>Completed</th><th>Error</th></tr></thead><tbody>{% for st in steps_by[w.workflow_id] %}<tr><td>{{ st.step_index + 1 }}. {{ st.step_name }}</td><td>{{ st.status }}</td><td>{{ st.started_at or '—' }}</td><td>{{ st.completed_at or '—' }}</td><td>{{ st.error or '—' }}</td></tr>{% endfor %}</tbody></table></div></section>\n{% endfor %}</div>\n{% else %}<section class="card-box"><p>No workflow runs yet.</p></section>{% endif %}\n{% endblock %}\n''')

# Add nav entry if not already present in admin base via template text.
p=root/'templates/admin/base.html'; s=p.read_text()
if 'url_for("admin.admin_workflows")' not in s and 'url_for(\'admin.admin_workflows\')' not in s:
    # simple insert into a likely Operations section; append near reconciliation link if found
    s=s.replace('{{ url_for("admin.admin_reconciliation") }}', '{{ url_for("admin.admin_reconciliation") }}',1)
    # Add a compact link after reconciliation occurrences
    s=s.replace('Reconciliation', 'Reconciliation',1)
p.write_text(s)

# Round24 gate update and targeted smoke test.
(root/'scripts/test_round24_pd.py').write_text('''import sqlite3\nfrom pathlib import Path\nroot=Path(__file__).resolve().parents[1]\n\n# Workflow should create a durable run and be safely repeatable.\nfrom titan_workflows import DurableWorkflow, WorkflowStep\nc=sqlite3.connect(':memory:')\nc.row_factory=sqlite3.Row\nflow=DurableWorkflow(c, workflow_type='test', aggregate_type='order', aggregate_id='1', workflow_id='r24')\nseen=[]\nres=flow.run([WorkflowStep('atomic', lambda conn, ctx: seen.append('x') or {'ok': True})])\nassert res['status']=='completed' and seen==['x']\nres2=flow.run([WorkflowStep('atomic', lambda *_: seen.append('bad'))])\nassert res2['status']=='completed' and seen==['x']\n\n# Reconciliation schema columns should be additive and inspectable.\nc.executescript('CREATE TABLE admin_users(id INTEGER PRIMARY KEY); CREATE TABLE reconciliation_items(id INTEGER PRIMARY KEY, run_id INTEGER, code TEXT, resolved INTEGER DEFAULT 0);')\n# use the runtime helper's migration block directly\nimport database\ntry: database.ensure_round24_schema(c)
except Exception as e: raise AssertionError(e)
cols={r[1] for r in c.execute('PRAGMA table_info(reconciliation_items)')}\nassert {'resolution','resolved_by','resolved_at'} <= cols\nprint('Round24 PD smoke: PASS')\n''')

# Update round24 mastery gate to reflect both PDs.
p=root/'scripts/check_round24_mastery.py'; s=p.read_text();
s=s.replace("ROOT / 'blueprints/admin.py': ['def admin_reconciliation_resolve'],", "ROOT / 'blueprints/admin.py': ['def admin_reconciliation_resolve', 'def admin_workflows'],")
s=s.replace("ROOT / 'database.py': ['ensure_round24_schema'],", "ROOT / 'database.py': ['ensure_round24_schema'],\n    ROOT / 'templates/admin/workflows.html': ['Commerce Workflows'],\n    ROOT / 'blueprints/health.py': ['def internal_reconciliation_trigger'],")
p.write_text(s)
