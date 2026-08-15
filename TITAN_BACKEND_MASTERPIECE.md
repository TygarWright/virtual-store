# TITAN Backend Masterpiece Layer

The backend keeps the original Virtual Store Python/SQLite/Flask DNA and adds only battle-tested principles:

- **Medusa:** HTTP → workflow/domain operation → module/repository → datastore boundaries.
- **Vendure:** explicit lifecycle/state transitions and extensibility seams.
- **Saleor:** commerce as versioned, observable configuration and event-driven extensions.
- **Django Oscar:** domain-driven, customizable commerce boundaries without forcing a rewrite of the host application.
- **WooCommerce:** durable webhook/outbox processing, retry visibility and operational logs.
- **Stripe-style idempotency:** repeated financial requests must not create repeated business effects.

TITAN-native additions in this layer:

1. Durable domain events paired with the existing outbox.
2. Generic idempotency records for future payment/refund/admin operations.
3. Schema-aware repair for backend-kernel tables instead of trusting migration history alone.
4. Safe backend capability diagnostics at `/healthz/backend` with no secrets exposed.
5. Payment-captured domain event emission without coupling payment correctness to optional event delivery.

We intentionally did **not** add Kafka, microservices, GraphQL, Elasticsearch or Kubernetes. The host application is still a single understandable Flask commerce system.
