# TITAN OWASP ASVS-Inspired Verification Matrix

This is a project-level verification matrix, not a claim of formal certification.

| Area | Control | Status | Evidence |
|---|---|---|---|
| V1 | Architecture & threat model | IMPLEMENTED | domain boundaries, providers, workflows |
| V2 | Authentication | IMPLEMENTED | admin/customer auth, 2FA support, session controls |
| V3 | Session management | IMPLEMENTED | secure cookies, token versioning, logout controls |
| V4 | Access control | IMPLEMENTED | permission checks, temporary grants, approval separation |
| V5 | Validation | IMPLEMENTED | server-side pricing, margin/inventory validation |
| V6 | Stored cryptography | IMPLEMENTED | password hashing, signed sessions/tokens |
| V7 | Error handling & logging | IMPLEMENTED | structured logs, request/trace IDs, Sentry hooks |
| V8 | Data protection | IMPLEMENTED | secret separation, sensitive release-tree hygiene |
| V9 | Communications | IMPLEMENTED | HTTPS enforcement in production, webhook verification |
| V10 | Malicious code / uploads | IMPLEMENTED | upload validation and protected product-file paths |
| V11 | Business logic | IMPLEMENTED | invariants, idempotency, workflow state, margin controls |
| V12 | Files / resources | IMPLEMENTED | constrained uploads/download tokens |
| V13 | API / web services | PARTIAL | auth/CSRF/rate limits implemented; full adversarial E2E remains |
| V14 | Configuration | IMPLEMENTED | production guards, deployment doctor |
| Certification | Browser/E2E/security/load evidence | PENDING | requires staging/real environment |
