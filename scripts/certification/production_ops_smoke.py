"""TITAN production-operations smoke contract.

Repository mode is dependency-free and verifies the deployed operations contract.
Live mode (BASE_URL=...) performs real HTTP checks and fails closed if they fail.
"""
from __future__ import annotations
import os, re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
errors=[]

def text(rel):
    p=ROOT/rel
    if not p.exists(): errors.append(f"missing:{rel}"); return ""
    return p.read_text(encoding="utf-8", errors="replace")


def repo_check():
    render=text("render.yaml"); ci=text(".github/workflows/ci.yml"); gunicorn=text("gunicorn.conf.py")
    health=text("app.py")
    required=[
        (render,"healthCheckPath: /healthz","Render health check"),
        (render,"mountPath: /opt/render/project/src/instance","persistent DB mount"),
        (render,"gunicorn app:app -c gunicorn.conf.py","Gunicorn startup"),
        (ci,'python-version: "3.14"',"CI runtime parity"),
        (ci,"python scripts/render_smoke.py","Render smoke in CI"),
        (gunicorn,"workers","Gunicorn configuration"),
        (health,"/healthz","health endpoint"),
    ]
    for body, needle, label in required:
        if needle not in body: errors.append(f"{label}:missing:{needle}")
    for rel in ("render.yaml","render-reconciliation-cron.yaml","TITAN/PHASE10_RUNBOOK.md","scripts/render_smoke.py"):
        if not (ROOT/rel).exists(): errors.append(f"ops asset missing:{rel}")
    if errors: raise SystemExit("PRODUCTION_OPS_CONTRACT: FAIL\n- " + "\n- ".join(errors))
    print("PRODUCTION_OPS_CONTRACT: PASS")


def live_check(base):
    import requests
    s=requests.Session(); base=base.rstrip("/")
    for path in ("/healthz","/","/privacy","/terms","/admin/login"):
        r=s.get(base+path,timeout=15,allow_redirects=False)
        if r.status_code != 200: raise AssertionError(f"{path}: HTTP {r.status_code}")
    if 'name="csrf_token"' not in s.get(base+"/admin/login",timeout=15).text:
        raise AssertionError("live admin login missing CSRF token")
    print("PRODUCTION_OPS_LIVE_SMOKE: PASS")

if __name__=="__main__":
    base=os.getenv("BASE_URL","").strip()
    if base: live_check(base)
    else: repo_check()
