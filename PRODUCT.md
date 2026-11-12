# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

- **Callers** ordering takeaway by voice from an independent restaurant; they want the order right and paid
  without friction, then to know when it is ready.
- **Restaurant staff:** the cook at the kitchen display during a rush, and the owner in the console handling
  refunds, approvals and the menu.
- **Hackathon judges** (PayPal AI hackathon) watching the demo. Confirmed priority: product first, but the
  first impression must land with judges.

## Product Purpose

A phone ordering agent for independent takeaways. An LLM takes the order by voice; deterministic code prices
it and decides what is allowed; PayPal collects payment while the caller is on the line. Only paid orders
reach the kitchen. Success: orders arrive paid, the kitchen never cooks an unpaid order, the owner trusts
every figure.

## Positioning

"The LLM talks, code decides, PayPal moves the money." The model cannot set a price, discount or refund; every
capture is reconciled against the quote; saved-wallet charges need an SMS code and a recorded spoken yes.

## Operating Context

- Caller: web demo with browser speech, or a phone via Twilio; pays on PayPal's own checkout page.
- Kitchen: a screen in a hot, busy kitchen, read at a distance, tapped with one hand.
- Owner: laptop console, live order grid (AG Grid), approvals, copilot questions, menu import from a photo.
- Live updates over SSE.

## Capabilities and Constraints

- Menu items, modifiers, prices (integer cents), order states (DRAFT → QUOTED → LINK_SENT → … CAPTURED →
  IN_KITCHEN → READY → PICKED_UP, plus PAYMENT_PENDING, PAYMENT_DENIED, NEEDS_REVIEW, REFUNDED_*).
- Stack: Next.js 15 app router, Tailwind 3, lucide icons, AG Grid; API proxied under /api.
- Demo restaurant: "Luigi's Artisan Pizza & Trattoria" (demo data, not a real client).
- PayPal sandbox; payment buttons must read as PayPal's.

## Brand Commitments

- Name: COUNTER.
- User-requested direction: a Pinterest-like, photo-led feel, "world class".

## Evidence on Hand

- No real food photography and no real restaurant client. Confirmed: licensed stand-in food photographs
  may be used, credited, and listed as stand-ins.
- No testimonials, customer counts or benchmarks may be invented. Generated reports live in harness/reports.

## Product Principles

1. Money truth over decoration: amounts and payment state are always exact and always visible.
2. Only paid orders reach the kitchen; the UI never implies otherwise.
3. Show the real thing: real menu, real PayPal checkout, real state, never staged data.
4. Calm under pressure: the kitchen and console stay scannable in a rush.

## Accessibility & Inclusion

WCAG 2.1 AA (axe scan in harness/a11y.py currently passes); kitchen display readable at a distance; voice
input is optional, typing always works.
