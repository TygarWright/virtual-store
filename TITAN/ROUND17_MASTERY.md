# TITAN Round 17 — Mastery Stack

This round adds the next layer of institutional-grade backend discipline:

- explicit business invariants (“business physics”)
- durable resumable workflows with persisted step state and compensation hooks
- workflow run/step history for operational diagnosis
- stronger refund invariant enforcement
- schema + workflow primitives designed to remain lightweight and SQLite/Turso friendly

Design rule: borrow principles from mature systems without importing unnecessary infrastructure.


## Non-negotiable invariants

- A refund cannot exceed the remaining refundable amount.
- A promotion cannot create negative price or violate the configured margin floor.
- Critical workflows persist step state and can resume without repeating completed steps.
- Workflow failure is recorded with step-level evidence.
