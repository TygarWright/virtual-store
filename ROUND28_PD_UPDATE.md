# TITAN Round 28 — Two PDs pushed hard

## PD 1 — Security Certification
- Added a deterministic security regression suite that checks critical admin authorization/CSRF surfaces, production config guards, payment refund idempotency, and full Python parse coverage.
- Added the security regression suite to CI.
- Expanded ASVS evidence with a deterministic business-invariant verification item.
- Kept environment-dependent penetration/HTTP/concurrency tests explicitly pending rather than mislabeling source checks as certification.

## PD 2 — TITAN Invariants
- Added a canonical invariant registry with named business-physics rules.
- Added deterministic verification for money, refunds, protected margins, inventory non-negativity, and state transitions.
- Added invariant checks to CI and mastery/security gating.

## Verification
- Invariant suite: 6/6 PASS
- Security regression suite: PASS
- ASVS gate: PASS
- Full Python compile: PASS
- Clean release tree: PASS
