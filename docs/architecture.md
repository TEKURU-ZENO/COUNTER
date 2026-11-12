# Architecture

```
 caller ──speech──▶ Twilio <Gather> ─┐            ┌──────────── Next.js (web) ─────────────┐
 browser demo (Web Speech API) ──────┤            │ call demo · console · kitchen · status │
                                     ▼            └───────────────┬────────────────────────┘
                             FastAPI  (apps/api)  ◀── /api proxy ─┘   SSE (Last-Event-ID)
                                     │
                    CallSession (apps/agent/session.py)
                     │  LLM picks a tool + typed args      consent reply ─▶ deterministic classifier
                     ▼
              AgentTools ──▶ PaymentService (apps/core/payments.py) ──▶ PayPal Orders v2 / Payments v2
                     │              │  Guard + FSM + reconciliation (packages/domain, pure)
                     ▼              ▼
                       Postgres: orders, order_events, outbox, payments, refunds,
                       webhook_events, consent_events, step_up_challenges, call_turns
                                    │
         PayPal webhooks ──verify──▶ webhook_events ──▶ worker (apps/worker) ──▶ state changes
                                    outbox (FOR UPDATE SKIP LOCKED) ──▶ Redis pub/sub ──▶ every API ──▶ SSE
                                                                    └─▶ Zapier hook (optional)
```

## Principles

1. **The LLM talks; code decides.** The model chooses a tool and fills Pydantic-validated arguments.
   Prices, refund amounts, consent and payment state are computed by code.
2. **Integer cents end to end.** `cents_to_value` / `value_to_cents` use integer arithmetic; the PayPal
   purchase unit is checked for `sum(items) == item_total` and `item_total + tax_total == amount`
   before it is sent ([packages/domain/money.py](../packages/domain/money.py)).
3. **One authoritative capture path.** The buyer's return from PayPal triggers a server-side capture;
   `CHECKOUT.ORDER.APPROVED` is a recovery trigger for the same idempotent function. `PAYMENT.CAPTURE.*`
   webhooks confirm or correct.
4. **Transactional outbox.** A state change, its `order_events` row and its outbox row commit together.
   The worker publishes outbox rows in per-order sequence to Redis; every API process fans out to its SSE
   clients; clients resume with `Last-Event-ID`.
5. **No PayPal call inside a database transaction.**
6. **One container.** Settings → one factory → dependency injection ([apps/core/container.py](../apps/core/container.py)).

## Order state machine

```
DRAFT → QUOTED → LINK_SENT → APPROVED → CAPTURED → IN_KITCHEN → READY → PICKED_UP
           │          │           └→ PAYMENT_PENDING ─┘                    └→ NO_SHOW
           │          ├→ LINK_EXPIRED / CANCELLED
           │          └→ PAYMENT_DENIED
           └→ CAPTURED (saved wallet, after code + consent)
 any paid state → REFUNDED_PARTIAL / REFUNDED_FULL · REVERSED · NEEDS_REVIEW (mismatch)
```
Defined as data in [packages/domain/fsm.py](../packages/domain/fsm.py). The kitchen can accept only `CAPTURED`.

## Packages

| Path | Role |
| --- | --- |
| `packages/domain` | Pure rules: pricing, FSM, guard, consent, reconciliation, money. Strict mypy, 95% branch-coverage gate. |
| `packages/paypal_gateway` | `PayPalClient` (sandbox/live HTTP) and `MockPayPalGateway` (explicit `PAYPAL_MODE=mock` only). |
| `apps/agent` | LLM adapters (Anthropic, OpenAI-compatible e.g. Groq), tools, call orchestration, menu matching. |
| `apps/core` | Settings, container, payment service, event bus, identity, SMS, telemetry, integrations. |
| `apps/api` | Routes: public/customer, merchant, Twilio, copilot, menu extraction, SSE. |
| `apps/worker` | Webhook processing, outbox publishing, link-expiry and no-show sweeps. |
| `apps/web` | Next.js 15 front end. |
| `harness` | Deterministic harness, LLM evaluation, load test, accessibility scan. |
