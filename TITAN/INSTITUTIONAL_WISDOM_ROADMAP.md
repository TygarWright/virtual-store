# TITAN Institutional Wisdom Roadmap

The goal is not more software for its own sake. The goal is to encode proven business-control practices.

## Governance & people
- [x] Fine-grained permissions
- [x] Role presets for finance, inventory and customer support
- [x] Approval-request foundation
- [x] Self-approval prevention
- [ ] Temporary/expiring permissions
- [ ] Break-glass access with automatic expiry
- [ ] Two-person approval enforcement on configured high-risk actions

## Institutional memory
- [x] Admin audit logging foundation
- [x] Decision journal
- [x] SOP documents
- [ ] Immutable audit archive with integrity hashes
- [ ] Staff training/simulation mode with scored scenarios

## Money discipline
- [x] Internal financial reconciliation snapshot
- [x] Refund idempotency
- [x] Payment/order mismatch exception detection
- [ ] Provider-side automatic reconciliation against Razorpay API
- [ ] Configurable approval thresholds for large refunds/discounts
- [ ] Margin-aware promotion guardrails

## Exception management
- [x] Business exception store
- [x] Exception scanner
- [x] Severity levels
- [x] Resolve workflow
- [ ] Automatic notifications/escalation rules
- [ ] SLA timers and aging alerts

## Inventory intelligence
- [x] Reservation/commit model
- [x] Existing forecasting foundation
- [ ] Safety stock / reorder-point model
- [ ] Supplier lead-time model
- [ ] Damaged/quarantined/returned stock states

## Customer service
- [x] Customer operations view
- [x] Order/refund history
- [ ] Support interaction timeline
- [ ] Customer recovery playbooks
- [ ] Customer trust/abuse signal summary

## Simulation
- [x] Side-effect-free business simulation endpoint
- [ ] Scenario packs: payment outage, stockout, refund surge, coupon abuse
- [ ] Simulation report/dashboard

## Guardian
- [ ] Unified TITAN Guardian dashboard
- [ ] Daily exception digest
- [ ] Cross-domain risk scoring
- [ ] Human-approval recommendations only; no autonomous money movement

## Latest implementation batch
- [x] Temporary/expiring permission grants + automatic expiry checks
- [x] Temporary permission revocation
- [x] Audit-log persistence with rolling SHA-256 integrity head
- [x] Inventory control metadata: safety stock, reorder point, lead time, damaged/quarantined/returned quantities
- [x] Guardian scan endpoint with weighted risk score
- [x] Support interaction timeline storage/API
- [x] Business failure simulation scenarios: payment outage, stockout, refund surge, coupon abuse
- [x] Scenario simulation persistence and risk classification
- [x] Forward migration for the new institutional-control tables
