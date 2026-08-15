# TITAN Round 46 — PD Closure Packet

## PD 1: Governance / Approval Engine — CLOSED (engineering acceptance)

Acceptance criteria:
- Policy is snapshotted at approval creation, including version, required approvals and expiry.
- Requested business metadata is stored separately from policy metadata.
- Execution validation compares exact business metadata instead of trusting a broad approval.
- Multi-step approval requires distinct approvers; the requester cannot approve.
- Approval expiry is enforced from the captured policy, not the current policy.
- Pending approvals are swept to `expired` when stale.
- Approval UI exposes pending/recent requests and policy state.
- Policy updates are versioned and emit durable domain events.
- Approve/reject actions are permission-gated and audited through the event spine.
- Acceptance test: `tests/test_round46_pd.py` → governance section passes.

## PD 2: Institutional Memory — CLOSED (engineering acceptance)

Acceptance criteria:
- Decision reviews are append-only in `decision_review_history`.
- Current decision state is updated while historical reviews remain queryable.
- Memory indexing automatically refreshes durable related-knowledge links.
- Related knowledge is available through the service and admin UI.
- Decision review history is visible to operators.
- Effectiveness reporting remains available after multiple reviews.
- Acceptance test: `tests/test_round46_pd.py` → institutional-memory section passes.

## Honest boundary

These two PDs are closed at the engineering/acceptance-test layer. Final production certification remains external: dependency-complete Flask runtime tests, browser/device QA, adversarial security testing, real Razorpay flows, migration/restore rehearsal and staging/rollback verification.
