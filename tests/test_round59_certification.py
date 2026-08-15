import hashlib, json, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TMP = ROOT / "reports" / "test_evidence.bin"
REG = ROOT / "reports" / "PHASE10_EXTERNAL_EVIDENCE.json"

ITEMS = [
    "razorpay_checkout_refund_webhook","browser_mobile_accessibility","concurrency_load_failure_injection",
    "staging_migration_restore","deployment_rollback","adversarial_security","production_operations","performance_load"
]


def run(*args):
    return subprocess.run([sys.executable, *map(str,args)], cwd=ROOT, text=True, capture_output=True)


def test_external_evidence_registration_and_hash_validation():
    TMP.write_bytes(b"deterministic external evidence")
    cmd = ROOT / "scripts/certification/external_evidence.py"
    assert run(cmd, "init").returncode == 0
    for item in ITEMS:
        assert run(cmd, "register", item, TMP, "--environment", "staging-fixture", "--reviewer", "qa").returncode == 0
    assert run(cmd, "verify").returncode == 0
    TMP.write_bytes(b"tampered")
    assert run(cmd, "verify").returncode == 1
    REG.unlink(missing_ok=True); TMP.unlink(missing_ok=True)
