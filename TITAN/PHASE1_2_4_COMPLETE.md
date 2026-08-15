# TITAN — Phases 1, 2 and 4 Completion Record

## Phase 1 — Security Foundation

Implemented and wired:
- Shared Flask-Limiter extension with global limits plus explicit limits on admin login, admin API login, OTP, payment verification, coupons, newsletter, webhook and CSP-report endpoints.
- Shared Flask-WTF CSRF extension.
- Explicit CSRF exemption only for provider webhooks/CSP reporting and the bearer-token admin API blueprint; browser push endpoints still perform explicit CSRF checks.
- Talisman security headers and HTTPS-aware secure cookies.
- Production configuration validation for admin credentials, test gateway and OTP development mode.
- Safer rate-limit storage configuration with support for shared Redis storage.

Verification available:
- All Python sources compile successfully.
- Sensitive-route limiter coverage is statically inspected.
- Full runtime pytest suite still requires the project's Python dependencies to be installed; this sandbox cannot reach PyPI.

## Phase 2 — Payment + Order Correctness

Implemented and strengthened:
- Provider abstraction retained for Razorpay/test gateways.
- Explicit payment/order state machines.
- Provider-side payment status verification before finalizing a browser payment.
- Order amount/currency validation in browser verification and webhook flows.
- Webhook idempotency with Razorpay event ID and SHA-256 body fallback.
- Refunds use Razorpay's X-Refund-Idempotency header.
- Refund retries distinguish transient network/5xx/429/409 failures from terminal 4xx failures.
- Concurrent identical open refunds are prevented by a partial unique database index.
- Refund webhook reconciliation for created/processed/failed states.
- Inventory reservation and transactional wrappers retained.

## Phase 4 — Architecture

Implemented and wired:
- Shared SQLAlchemy/Migrate extension instead of a second local SQLAlchemy instance.
- Model metadata is now attached to the shared migration DB instance.
- Explicit provider contracts for authentication, notifications, storage and search.
- Default provider adapters.
- Small dependency-injection/service container exposed through `app.extensions['titan.services']`.
- Explicit domain boundary package for HTTP-independent commerce validation.
- Immutable runtime configuration object plus production configuration validation.
- SQLAlchemy engine health/pool settings.

## Remaining external verification

These phases are code-complete to the extent possible in the current offline sandbox, but the final release gate still requires:
1. Install the exact pinned dependencies in CI/staging.
2. Run the complete pytest suite.
3. Run a real Razorpay test payment, webhook and refund cycle.
4. Verify migration upgrade/rollback against a copy of the real database.
