# TITAN Frontend Ecosystem Report

## Baseline
The original TYG4R/virtual-store public storefront was used as the visual baseline. The original CSS file is preserved byte-for-byte as the prefix of `static/css/style.css`; TITAN enhancements are additive and live in `static/css/titan-ui.css`.

- Original CSS bytes: 156,536
- TITAN baseline CSS prefix matches original: True
- Additional legacy-TITAN CSS bytes after baseline: 2,346
- Additional experience-layer CSS: 10,770

## Exact public-file matches
- `templates/cart.html`
- `templates/order_status.html`
- `templates/track_order.html`
- `templates/account_hub.html`
- `templates/account_library.html`
- `templates/account_orders.html`
- `templates/account_wishlist.html`
- `templates/privacy_policy.html`
- `templates/refund_policy.html`
- `templates/terms_of_service.html`
- `templates/unsubscribed.html`
- `static/js/animations.js`
- `static/js/cart.js`
- `static/js/cart_checkout.js`
- `static/js/checkout.js`
- `static/js/countries.js`
- `static/js/delivery.js`
- `static/js/auto_coupons.js`

## Intentionally changed public files
- `templates/base.html` — functional/TITAN enhancement
- `templates/index.html` — functional/TITAN enhancement
- `templates/product.html` — functional/TITAN enhancement
- `templates/_auth_modal.html` — functional/TITAN enhancement
- `static/css/style.css` — functional/TITAN enhancement
- `static/js/ui.js` — functional/TITAN enhancement

## Design principles hand-picked
- **Original Virtual Store:** preserve its editorial black/white identity, typography, whitespace and vanilla HTML/CSS/JS architecture.
- **GitHub Primer:** cohesive, responsive, accessible interaction patterns and consistent navigation language. Primer is MIT-licensed and open source.
- **Saleor Paper:** mobile-first checkout, resilience, accessibility, focused forms and reusable commerce UI patterns.
- **Medusa storefront patterns:** performance, componentized commerce interactions and focused responsive flows.

We implement these as principles in the existing Flask/Jinja/vanilla stack; we do not copy their UI code or import their frameworks.

## Rule
Backend contracts and frontend presentation are treated as one ecosystem: route context, permissions, settings, notices, active navigation, CSRF, loading/error states and accessibility semantics must use the same design vocabulary.
