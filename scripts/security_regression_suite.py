#!/usr/bin/env python3
"""Dependency-free adversarial/source regression checks for TITAN.

These are deliberately conservative checks that can run before the full HTTP
security suite. They do not claim penetration-test coverage.
"""
from __future__ import annotations
import ast, re, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
FAIL = []

def text(path):
    return (ROOT/path).read_text(encoding='utf-8', errors='replace')

# High-risk mutation/admin surfaces should use CSRF + permission helpers.
admin = text(Path('blueprints/admin.py'))
for marker in ['@login_required', '@requires_permission']:
    if marker not in admin:
        FAIL.append(f'admin.py missing {marker}')
if 'check_csrf()' not in admin:
    FAIL.append('admin.py has no CSRF mutation guard')

# Financial endpoints should retain idempotency / invariant primitives.
gateway = text(Path('payment/gateways.py')) if (ROOT/'payment/gateways.py').exists() else ''
if 'X-Refund-Idempotency' not in gateway:
    FAIL.append('payment gateway lacks refund idempotency semantics')
backend = text(Path('backend_kernel.py'))
if 'begin_idempotent_operation' not in backend:
    FAIL.append('backend_kernel.py missing idempotency primitive')

# No obvious production debug backdoors.
config = text(Path('config.py')) if (ROOT/'config.py').exists() else ''
for token in ['ALLOW_STORE_TEST_MODE', 'OTP_DEV_MODE', 'DEBUG', 'CSRF_ENABLED']:
    if token not in config:
        FAIL.append(f'production config guard missing {token}')

# Production must never silently ship with test checkout, OTP dev mode, or CSRF off.
for bad_default in [
    ('ALLOW_STORE_TEST_MODE', 'true'),
    ('OTP_DEV_MODE', 'true'),
    ('CSRF_ENABLED', 'false'),
]:
    name, value = bad_default
    if f'os.environ.get("{name}", "{value}")' in config:
        FAIL.append(f'insecure production default detected for {name}')

# High-risk role separation: low-privilege presets must never carry
# approval or administrative authority. This is a source-level invariant.
permissions = text(Path('permissions.py'))
role_lines = {line.split(':', 1)[0].strip().strip('"'): line for line in permissions.splitlines() if ':' in line}
for role in ['support_agent', 'customer_support', 'catalog_manager', 'content_manager']:
    line = role_lines.get(role, '')
    for forbidden in ['governance.approve', 'admin.manage']:
        if forbidden in line:
            FAIL.append(f'{role} unexpectedly grants {forbidden}')

# Production readiness must explicitly keep safety switches safe by default.
if 'CSRF_ENABLED = os.environ.get("CSRF_ENABLED", "true")' not in config:
    FAIL.append('CSRF_ENABLED must default to true')
if 'ALLOW_STORE_TEST_MODE = os.environ.get("ALLOW_STORE_TEST_MODE", "true" if DEBUG else "false")' not in config:
    FAIL.append('store test mode must default off when DEBUG is false')
if 'OTP_DEV_MODE = os.environ.get("OTP_DEV_MODE", "false")' not in config:
    FAIL.append('OTP_DEV_MODE must default false')

# Parse every Python file in the project.
for p in ROOT.rglob('*.py'):
    if any(part in {'.venv','__pycache__'} for part in p.parts):
        continue
    try:
        ast.parse(p.read_text(encoding='utf-8', errors='replace'))
    except Exception as exc:
        FAIL.append(f'parse failure: {p.relative_to(ROOT)}: {exc}')

print('TITAN SECURITY REGRESSION SUITE: PASS' if not FAIL else 'TITAN SECURITY REGRESSION SUITE: FAIL')
for item in FAIL:
    print('FAIL -', item)
sys.exit(0 if not FAIL else 1)
