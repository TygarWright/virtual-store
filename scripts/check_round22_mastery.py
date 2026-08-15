from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
app=(ROOT/'app.py').read_text()
admin=(ROOT/'blueprints/admin.py').read_text()
obs=(ROOT/'observability_service.py').read_text()
css=(ROOT/'static/css/titan-ui.css').read_text()
mem=(ROOT/'mastery_services.py').read_text()
assert 'import observability_service' in app
assert 'observability_service.start_span' in app
assert 'observability_service.finish_span' in app
assert 'def admin_observability' in admin
assert 'future_recommendation' in mem and 'future_recommendation' in admin
for token in ('observability_spans','trace_id','span_id','duration_ms'):
    assert token in obs
for token in ('--titan-space-4','--titan-focus-ring','focus-visible','.status-pill'):
    assert token in css
print('ROUND22_MASTERY_GATE=PASS')
