# COUNTER: System Architecture & Data Flow

## 1. High-Level Topology

```
                 ┌──────────────────────────────────────────────┐
  Caller ──────► │  LiveKit (WebRTC room / SIP from Twilio)     │
  (browser or    └───────────────┬──────────────────────────────┘
   phone)                        │ audio
                 ┌───────────────▼──────────────────────────────┐
                 │  Voice Agent Worker (LiveKit Agents, Python) │
                 │  VAD → STT → LLM (tool calls) → TTS          │
                 └───────────────┬──────────────────────────────┘
                                 │ typed tool calls (Pydantic)
                 ┌───────────────▼──────────────────────────────┐
                 │  Core API (FastAPI)                          │
                 │  ┌─────────┐ ┌─────────┐ ┌────────────────┐  │
                 │  │ Guard   │►│ Order   │►│ PayPal Gateway │──┼──► PayPal REST
                 │  │ (policy)│ │ Engine  │ │ (idempotent)   │  │    (Orders, Payments,
                 │  └─────────┘ │ (FSM +  │ └────────────────┘  │     Invoicing, Reporting,
                 │              │ pricing)│                     │     Catalog, Webhooks)
                 │              └────┬────┘                     │
                 │                   │ outbox events            │◄── PayPal webhooks
                 └───────────────────┼──────────────────────────┘    (verified)
                                     │
                   Postgres ◄────────┤────────► Redis (pub/sub, jobs, locks)
                                     │
                 ┌───────────────────▼──────────────────────────┐
                 │  Worker (arq): webhook processing, link       │
                 │  expiry, reconciliation, no-show sweeps      │
                 └──────────────────────────────────────────────┘
                                     │ live events (SSE)
                 ┌───────────────────▼──────────────────────────┐
                 │  Web (Next.js): Merchant Console (AG Grid),   │
                 │  Ops Copilot, Onboarding, Kitchen Screen,     │
                 │  Order Status Page, Public Demo Page          │
                 └──────────────────────────────────────────────┘
```

## 2. Invariable Invariants

1. **Deterministic Rule Enforcement**: The Voice Agent communicates with Core API exclusively via typed Pydantic tools. It has zero credentials to PayPal and cannot execute any financial mutation.
2. **One-Way Financial Direction**: Integer cents flow strictly from `quote(cart, menu, tax_rule)` to PayPal Orders v2. No LLM output string is ever parsed into an amount.
3. **Optimistic Locking**: Every order state transition enforces `WHERE id=:order_id AND version=:version`. Race conditions between incoming webhooks and caller cancellation are mathematically resolved without dirty writes.
4. **Transactional Outbox**: All state mutations append an immutable record to `order_events` within the primary database transaction. The worker streams these outbox events onto Redis channels for agent injection and SSE frontend delivery.
5. **Exact-Once Processing**: `webhook_events.paypal_event_id` is enforced by database uniqueness. Repeated deliveries from PayPal result in instantaneous, harmless no-ops.

## 3. Order Engine State Machine

```
DRAFT ──quote──► QUOTED ──send_link──► LINK_SENT ──(webhook APPROVED)──► APPROVED
  │                │                      │                                  │ capture
  │                │                      └──(ttl)──► LINK_EXPIRED           ▼
  └──cancel──► CANCELLED ◄──cancel────────┘                              CAPTURED
                                                                             │ kitchen_accept
                         REFUNDED_FULL ◄──refund(policy)── IN_KITCHEN ◄──────┘
                         REFUNDED_PARTIAL ◄──refund(policy)──┘   │ mark_ready
                                                               READY ──pickup──► PICKED_UP
                                                                 └──(sweep)──► NO_SHOW
```

## 4. Transition Table Specification

| Current State | Permitted Action | Destination State | Side Effects & Pre-conditions |
| :--- | :--- | :--- | :--- |
| `DRAFT` | `quote` | `QUOTED` | Computes subtotal, tax, and quote SHA-256 hash |
| `DRAFT` | `cancel` | `CANCELLED` | Releases reserved slot |
| `QUOTED` | `send_link` | `LINK_SENT` | Validates quote hash match; invokes PayPal Orders v2 with `PayPal-Request-Id` |
| `QUOTED` | `cancel` | `CANCELLED` | Clears cart; releases slot |
| `LINK_SENT` | `expire` | `LINK_EXPIRED` | Background sweep expires order after policy TTL |
| `LINK_SENT` | `cancel` | `CANCELLED` | Customer hangs up or rejects |
| `LINK_SENT` | `approve` | `APPROVED` | Verified `CHECKOUT.ORDER.APPROVED` webhook arrives |
| `APPROVED` | `capture` | `CAPTURED` | PayPal Orders v2 capture succeeds; outbox event dispatched; kitchen ticket created |
| `CAPTURED` | `kitchen_accept` | `IN_KITCHEN` | Kitchen screen accepts ticket; prep begins |
| `CAPTURED` | `refund_full` | `REFUNDED_FULL` | Cancelled before kitchen prep; 100% refund via PayPal Payments v2 refund |
| `IN_KITCHEN` | `mark_ready` | `READY` | Kitchen finishes order; customer notified via SMS |
| `IN_KITCHEN` | `refund_partial` | `REFUNDED_PARTIAL` | Cancelled during prep; policy-governed partial refund (e.g. 50%) |
| `READY` | `pickup` | `PICKED_UP` | Customer picks up food; final state |
| `READY` | `sweep_no_show`| `NO_SHOW` | Customer does not arrive after grace period; revenue retained |
