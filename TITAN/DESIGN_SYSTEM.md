# TITAN Design System

The original Virtual Store visual language remains the source of truth. TITAN adds a restrained system layer rather than replacing the identity.

## Rules
- One icon language: inline SVG, never emoji for interface chrome.
- One spacing scale: 4/8px-derived rhythm exposed as `--titan-space-*` tokens.
- One focus language: visible keyboard focus on every interactive control.
- One state grammar: success, running, warning, error and neutral states use the same pill/card vocabulary.
- Dense data stays opaque and highly readable; decorative effects never compromise information density.
- Reduced-motion users receive functional equivalence without animation.
- New admin modules must extend `titan-ui.css` primitives rather than inventing one-off component systems.
