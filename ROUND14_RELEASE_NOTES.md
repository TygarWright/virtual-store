# TITAN Round 14 — Control Plane Hardening

This round is intentionally narrow: it does not redesign the storefront. It hardens the live admin control plane after the production Guardian failure.

## Guardian
- Added an explicit live-schema readiness probe for `assigned_to`, `due_at`, `escalated_at`, and `escalation_reason`.
- Guardian now repairs additive schema before executing its scan/query.
- Added a single Turso/libSQL connection refresh retry for stale schema metadata.
- If the remote schema still cannot be repaired, Guardian fails closed with an operator-facing message instead of a Flask 500.
- Kept the migration ledger and schema-contract repair paths intact; this is an additional runtime guard, not a replacement.

## Existing Round 13 capabilities retained
- Team Hub for global/direct/team communication without tickets.
- Global, role-focused and employee-focused admin ticket creation.
- Site Notices with enable/disable, priority and scheduling fields.
- Active sidebar module state plus workspace context label.
- SVG/icon-based admin UI; no emoji chrome.

## Certification
- Round 12 static gate: PASS
- Phase 10 static gate: PASS
- Phase 9 static gate: PASS
- Pre-9 TITAN gate: PASS
- Python syntax compilation: PASS
- Live production certification still required after deployment: Guardian route, ticket scopes, Team Hub messaging, Site Notices, sidebar state, migration/restore rehearsal.
