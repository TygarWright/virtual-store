# TITAN MASTER CHECKLIST — PRE-PHASE-9

Status meanings:
- ⬜ Not started
- 🔄 In progress
- ⚠️ External/runtime certification required
- ❌ Failed / blocked
- ✅ Implementation verified

> Phase 9 is LOCKED until all Phase 0–8 implementation gates below are ✅.
> External live/staging certification is tracked separately in Phase 10 and must not be confused with missing repository implementation.

## PHASE 0 — REALITY LOCK
- ✅ Repository and route audit completed
- ✅ Authentication/payment/webhook/deployment audit completed
- ✅ Baseline architecture mapped
**Gate:** ✅ PASS

## PHASE 1 — SECURITY FOUNDATION
- ✅ CSRF protection
- ✅ Security headers / CSP / HSTS policy
- ✅ Explicit sensitive-route rate limits
- ✅ Secure cookie/session configuration
- ✅ Input/error hardening
- ✅ Dependency audit in CI
- ✅ Secret-handling safeguards
**Gate:** ✅ IMPLEMENTATION COMPLETE

## PHASE 2 — PAYMENT + ORDER CORRECTNESS
- ✅ Payment provider abstraction
- ✅ Provider amount/currency/capture verification
- ✅ Payment/order state transitions
- ✅ Webhook deduplication/idempotency
- ✅ Refund provider integration + idempotency
- ✅ Refund webhook reconciliation
- ✅ Inventory reservation/commit integration
- ✅ Expired webhook/outbox lease recovery
**Gate:** ✅ IMPLEMENTATION COMPLETE

## PHASE 3 — DATA FOUNDATION
- ✅ Migration ledger for legacy sqlite schema changes
- ✅ Flask-Migrate/Alembic baseline present for SQLAlchemy models
- ✅ Important indexes/unique guards
- ✅ Foreign-key enforcement enabled on local SQLite connections
- ✅ Verified online-backup tooling
- ✅ Verified restore tooling
- ✅ Inventory reservation correlation indexed on orders
**Gate:** ✅ IMPLEMENTATION COMPLETE

## PHASE 4 — ARCHITECTURE
- ✅ Application factory
- ✅ Blueprint organization
- ✅ Shared extension instances
- ✅ Service/provider boundaries
- ✅ Repository layer
- ✅ Dependency-injection container
- ✅ Domain boundaries
- ✅ Production configuration validation
**Gate:** ✅ PASS

## PHASE 5 — OPERATIONS + OBSERVABILITY
- ✅ Structured logging
- ✅ Request correlation IDs
- ✅ /healthz and /readyz
- ✅ Sentry integration/configuration hooks
- ✅ Prometheus metrics
- ✅ Retry infrastructure
- ✅ Durable outbox persistence
- ✅ Durable outbox worker
- ✅ Expired worker-lease reclamation
- ✅ Safe synchronous email fallback when worker mode is disabled
- ✅ Optional shared-DB Render worker manifest
- ✅ Admin audit logging foundations
**Gate:** ✅ IMPLEMENTATION COMPLETE

## PHASE 6 — COMMERCE ENGINE
- ✅ Guest/authenticated carts exist
- ✅ Checkout price recalculation exists
- ✅ Coupon/promotion engine exists
- ✅ Inventory reservation integrated into new orders
- ✅ Payment-driven inventory commit
- ✅ Payment failure/cancel reservation release
- ✅ Refund workflow
- ✅ Digital entitlement/download delivery foundations
- ✅ Multi-item inventory reservation logic
- ✅ Idempotent fulfillment/payment pathways
**Gate:** ✅ IMPLEMENTATION COMPLETE

## PHASE 7 — ADMIN / OPERATIONS CENTER
- ✅ Existing admin CRUD and order operations
- ✅ Payment diagnostics foundations
- ✅ Audit-log foundations
- ✅ System health visibility
- ✅ Full operations dashboard alerts
- ✅ Product bulk operations
- ✅ Bulk order delivery workflow
- ✅ Inventory operations UX
- ✅ Customer operations console
- ✅ Server-side permission enforcement
- ✅ CSRF-protected admin mutations
**Gate:** ✅ IMPLEMENTATION COMPLETE

## PHASE 8 — STOREFRONT + UX
- ✅ Existing storefront/product discovery
- ✅ Search, category, price, delivery-type and rating filters
- ✅ Sorting
- ✅ Server-side catalogue pagination after filtering/sorting
- ✅ Pagination preserves active search/filter/sort state
- ✅ Product detail/reviews/wishlist/recently-viewed/cart/checkout flows retained
- ✅ Responsive/mobile navigation foundations
- ✅ Reduced-motion support
- ✅ Accessible pagination/navigation labels
- ✅ Product structured-data currency correctness
- ✅ Product structured-data availability correctness
- ✅ Product sharing control keyboard/form safety
- ✅ Customer-facing empty/error states
- ✅ SEO sitemap/robots/canonical/structured-data foundations
**Gate:** ✅ IMPLEMENTATION COMPLETE

## PRE-PHASE-9 STATIC GATE
- ✅ `python scripts/verify_titan_pre9.py`
- ✅ All Python files compile
- ✅ No bundled DB/password/backup/cache artifacts
- ✅ CI includes dependency audit + compile + pre-9 gate
- ✅ Development-only Google debug page removed from publishable tree

## EXTERNAL CERTIFICATION — RESERVED FOR PHASE 10
- ⚠️ Full dependency-backed pytest suite in CI
- ⚠️ Real Razorpay test payment
- ⚠️ Real Razorpay refund
- ⚠️ Real webhook replay/duplicate test
- ⚠️ Production migration rehearsal
- ⚠️ Staging backup/restore rehearsal
- ⚠️ Browser + real-device accessibility review
- ⚠️ Load/performance measurement
- ⚠️ Production deployment/rollback rehearsal

These are intentionally not counted as incomplete Phase 0–8 implementation. They are the launch certification gate.

## PHASE 9 LOCK
**✅ ALL PRE-PHASE-9 IMPLEMENTATION GATES PASS.**

Do not begin Phase 9 until the release candidate is accepted by the project owner and the external certification plan is understood.

## PHASE 9 — INTELLIGENCE
- ✅ Intelligent search ranking
- ✅ Product recommendation engine
- ✅ Customer-personalized recommendations
- ✅ Analytics event capture
- ✅ Business intelligence dashboard
- ✅ Inventory velocity forecasting
- ✅ Anomaly detection
- ✅ Read-only natural-language intelligence assistant
- ✅ Optional OpenAI-compatible presentation provider with deterministic fallback
- ✅ Intelligence API endpoints
- ✅ Intelligence schema/indexes
- ✅ AI safety boundary: no commerce mutations
- ✅ `python scripts/verify_titan_phase9.py`
**Gate:** ✅ IMPLEMENTATION COMPLETE

## PHASE 10 — LAUNCH CERTIFICATION
- ⬜ Full dependency-backed pytest suite in CI
- ⬜ Real Razorpay test payment
- ⬜ Real Razorpay refund
- ⬜ Real webhook delivery + duplicate/replay test
- ⬜ Production migration rehearsal
- ⬜ Staging backup/restore rehearsal
- ⬜ Browser + real-device UX/accessibility verification
- ⬜ Performance/load verification
- ⬜ Production deployment verification
- ⬜ Rollback rehearsal
- ⬜ Final GO / GO WITH KNOWN RISKS / NO-GO decision
**Gate:** ⬜ NOT STARTED
