# TITAN Phase 10 — Launch Certification Runbook

This phase is the final proof stage. A green source tree is not equivalent to a production launch.

## 1. CI / Runtime Tests

Run in GitHub Actions with the repository's pinned dependencies:

```bash
python -m pip install -r requirements.txt pytest
python -m pip_audit -r requirements.txt
python -m compileall -q .
python scripts/verify_titan_pre9.py
python scripts/verify_titan_phase9.py
python scripts/verify_titan_phase10.py
pytest -q
```

The final runtime gate is green only when the full test suite passes.

## 2. Staging Payment Certification

Using Razorpay test credentials:

1. Create a real test order.
2. Complete a test payment.
3. Verify server-side signature, amount, currency and captured state.
4. Verify exactly one local payment/order outcome.
5. Replay the same provider webhook.
6. Confirm no duplicate order/fulfillment.
7. Request a refund.
8. Verify provider refund and local refund state.
9. Replay refund webhook(s).
10. Confirm no duplicate refund state transition.

Record provider references and application correlation IDs.

## 3. Migration Certification

Against a copy of the real production database:

```text
backup
→ restore copy
→ run migrations
→ verify schema
→ run integrity checks
→ execute representative read/write flows
```

Never rehearse destructive migrations on the only live copy.

## 4. Backup / Restore Certification

```text
production-like DB
→ backup
→ create known verification marker
→ restore into clean target
→ verify marker + row counts + foreign keys
→ run application against restored DB
```

The restore must be independently usable.

## 5. Browser / Device Certification

Test at minimum:

- Android Chrome
- desktop Chromium
- slow network
- interrupted network
- checkout refresh
- browser back navigation
- payment return flow
- failed payment recovery
- accessibility keyboard/focus paths

## 6. Performance Certification

Measure:

- TTFB
- homepage latency
- catalog latency
- product-page latency
- checkout latency
- payment initiation latency
- error rate
- database latency

Run a representative load test against staging, not production.

## 7. Deployment Certification

Verify:

- production environment variables
- HTTPS
- persistent storage
- health checks
- readiness checks
- worker configuration where applicable
- logs
- Sentry/monitoring

## 8. Rollback Certification

1. Deploy release candidate.
2. Verify health.
3. Verify key customer flow.
4. Deploy a controlled rollback candidate.
5. Verify health and customer flow again.
6. Verify database compatibility.

## 9. Final Decision

Only after every required runtime item is evidenced:

- `GO`
- `GO WITH KNOWN RISKS`
- `NO-GO`

Never mark `GO` because the code looks correct. Evidence is required.
