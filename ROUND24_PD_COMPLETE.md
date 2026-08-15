# Round 24 — Two PDs completed

## Durable workflow adoption
- Payment confirmation/finalization paths (single-product, cart, gateway verification, webhook) now execute through the durable order.payment_confirmation workflow adapter.
- Existing idempotent business finalizer remains authoritative.
- Workflow run/step status is visible in Admin > Workflows.
- Workflow schema is self-contained and supports persisted attempt/compensation state.

## Reconciliation
- Durable provider reconciliation runs and discrepancy records remain in place.
- Discrepancies can be reviewed and explicitly resolved with an accountable admin note.
- Scheduled reconciliation is implemented as a Render cron that calls the persistent web service over a secret-protected endpoint; this avoids incorrectly assuming a cron container can access the web service persistent disk. Render cron jobs are ephemeral and do not have access to web-service persistent disks.
- Daily schedule defaults to 02:00 UTC and can be changed in the cron Blueprint.
