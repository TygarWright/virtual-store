from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def test_schema_contract_exists_and_is_hooked():
    text=(ROOT/'schema_contract.py').read_text()
    db=(ROOT/'database.py').read_text()
    assert 'CRITICAL_COLUMNS' in text
    assert 'repair_missing_columns' in text
    assert 'repair_missing_columns(conn)' in db

def test_favicon_is_real_svg():
    text=(ROOT/'static/favicon.svg').read_text()
    assert text.lstrip().startswith('<svg') and '</svg>' in text

def test_admin_has_command_palette():
    html=(ROOT/'templates/admin/base.html').read_text()
    css=(ROOT/'static/css/titan-ui.css').read_text()
    assert 'adminCommandPalette' in html
    assert '.admin-command-palette' in css

def test_no_ui_emoji_chrome_in_core_admin_templates():
    for path in (ROOT/'templates/admin').rglob('*.html'):
        text=path.read_text(errors='ignore')
        assert '🛒' not in text and '🔔' not in text and '⚙' not in text, path
