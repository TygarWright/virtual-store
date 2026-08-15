#!/usr/bin/env python3
"""Cheap preflight checks for professional UI quality.

This intentionally targets interface chrome (buttons, labels, navigation, status
copy) rather than legitimate customer-authored/content examples.
"""
from __future__ import annotations
from pathlib import Path
import re, sys

ROOT = Path(__file__).resolve().parents[1]
ERRORS=[]
# Emoji in interface/control text is rejected in these UI surfaces.
TARGETS=[ROOT/'templates/admin', ROOT/'templates/order_status.html', ROOT/'templates/track_order.html', ROOT/'templates/_delivery_block.html', ROOT/'templates/account_library.html', ROOT/'static/js/auto_coupons.js']
for target in TARGETS:
    paths = target.rglob('*') if target.is_dir() else [target]
    for p in paths:
        if not p.is_file() or p.suffix not in {'.html','.js'}: continue
        text=p.read_text(encoding='utf-8', errors='ignore')
        for i,line in enumerate(text.splitlines(),1):
            if any(ord(ch)>=0x1F300 for ch in line):
                ERRORS.append(f"UI emoji remains in control/status surface: {p.relative_to(ROOT)}:{i}")
                break
# Ensure shared settings are available to all templates.
app=(ROOT/'app.py').read_text(encoding='utf-8')
for needle in ('app.context_processor', 'settings=get_settings()'):
    if needle not in app:
        ERRORS.append(f"template context safeguard missing: {needle}")
if ERRORS:
    print('UI QUALITY GATE: FAIL')
    for e in ERRORS: print('-',e)
    raise SystemExit(1)
print('UI QUALITY GATE: PASS')
