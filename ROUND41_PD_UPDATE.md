# TITAN Mastery Round 41 — Two PDs pushed hard

## 1. Governance / approval engine
- Added multi-step approvals through `approval_steps`.
- Added `required_approvals` policy control with safe additive migration.
- Approval completion now occurs only after every required step is approved by distinct admins.
- Approval detail/list APIs expose step progress.
- Added approval-policy inspection/update API.
- Approval lifecycle emits durable domain events.
- Self-approval and duplicate step approval remain blocked.

## 2. Event spine / cross-domain cohesion
- Governance approval request/approval/rejection emits durable domain events.
- Guardian exception lifecycle events are emitted into the same durable domain-event spine.
- Added governance event timeline API with topic/aggregate filters.
- Existing outbox and idempotency mechanisms remain the delivery/duplication boundaries.
