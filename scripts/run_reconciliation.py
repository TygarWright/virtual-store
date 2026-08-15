#!/usr/bin/env python3
"""Trigger scheduled reconciliation on the live web service.
Render cron containers are ephemeral and cannot access a web service persistent disk,
so the cron job calls the protected web endpoint instead.
"""
from __future__ import annotations
import json
import os
import sys
import requests

base = os.environ.get('SITE_URL', '').strip().rstrip('/')
secret = os.environ.get('CRON_SECRET', '').strip()
if not base or not secret:
    print(json.dumps({'status': 'failed', 'error': 'SITE_URL and CRON_SECRET are required'}))
    raise SystemExit(2)
resp = requests.post(f'{base}/internal/reconciliation', headers={'X-TITAN-CRON-SECRET': secret}, timeout=(5, 60))
try:
    data = resp.json()
except Exception:
    data = {'status': 'failed', 'error': resp.text[:500]}
print(json.dumps(data, sort_keys=True, default=str))
raise SystemExit(0 if resp.ok and data.get('status') in {'completed', 'skipped'} else 2)
