from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
admin=(ROOT/'blueprints/admin.py').read_text()
app=(ROOT/'app.py').read_text()
mem=(ROOT/'mastery_services.py').read_text()
analytics=(ROOT/'analytics_mastery.py').read_text()
base=(ROOT/'templates/admin/base.html').read_text()
mem_tpl=(ROOT/'templates/admin/institutional_memory.html').read_text()
ana_tpl=(ROOT/'templates/admin/analytics.html').read_text()
icons=(ROOT/'templates/admin/_icons.html').read_text()
assert 'def admin_analytics()' in admin
assert 'analytics_overview' in admin
assert 'def memory_source_types' in mem
assert 'source_type: str = ""' in mem
assert 'def analytics_overview' in analytics
assert 'def experiment_report' in analytics
assert 'admin.admin_analytics' in base
assert 'admin_analytics' in app
assert 'future_recommendation' in mem_tpl and 'source_type' in mem_tpl
assert 'Funnel' in ana_tpl and 'Customer cohorts' in ana_tpl and 'Experiments' in ana_tpl
assert "name == 'chart'" in icons
print('ROUND23_MASTERY_GATE=PASS')
