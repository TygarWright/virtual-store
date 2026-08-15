"""Deterministic performance-budget drill for critical local Flask routes.

This is not a substitute for production load testing. It catches catastrophic
regressions early and produces measurable p50/p95 timings using only stdlib timing
plus Flask's test client.
"""
from __future__ import annotations

import os
import statistics
import tempfile
import shutil
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

BUDGETS_MS = {"/": 250.0, "/healthz": 100.0, "/privacy": 250.0, "/terms": 250.0}
ITERATIONS = 20


def percentile(values, p):
    ordered = sorted(values)
    if not ordered:
        return 0.0
    idx = (len(ordered) - 1) * p
    lo, hi = int(idx), min(int(idx) + 1, len(ordered) - 1)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (idx - lo)


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="titan-perf-"))
    try:
        os.environ.update({
            "SECRET_KEY": "perf-smoke-secret",
            "DEBUG": "false",
            "DB_PATH": str(tmp / "store.db"),
            "ADMIN_USERNAME": "admin",
            "ADMIN_PASSWORD": "perf-smoke-password",
            "RAZORPAY_KEY_ID": "rzp_test_dummy",
            "RAZORPAY_KEY_SECRET": "dummy",
            "RAZORPAY_WEBHOOK_SECRET": "dummy",
            "OTP_DEV_MODE": "false",
            "ALLOW_STORE_TEST_MODE": "false",
            "ALLOW_TEST_GATEWAY": "false",
        })
        import sys
        sys.path.insert(0, str(ROOT))
        from app import app  # noqa: E402
        app.testing = True
        client = app.test_client()
        for path, budget in BUDGETS_MS.items():
            samples = []
            for _ in range(ITERATIONS):
                t0 = time.perf_counter()
                response = client.get(path)
                elapsed = (time.perf_counter() - t0) * 1000
                if response.status_code != 200:
                    raise AssertionError(f"{path}: HTTP {response.status_code}")
                samples.append(elapsed)
            p50, p95 = percentile(samples, .50), percentile(samples, .95)
            print(f"{path}: p50={p50:.2f}ms p95={p95:.2f}ms budget={budget:.2f}ms")
            if p95 > budget:
                raise AssertionError(f"{path}: p95 budget exceeded")
        print("PERFORMANCE_BUDGET_DRILL: PASS")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
