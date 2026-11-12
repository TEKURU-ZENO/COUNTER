# Threat model

Scope: the phone/web ordering agent, the API, the worker, and their use of PayPal. Each control
links to the code that implements it and the test that exercises it.

## Assets

- Money moving through the merchant's PayPal account (captures, refunds, saved-wallet charges).
- Customers' saved PayPal wallets (vault tokens) and phone numbers.
- Integrity of the order ledger the kitchen and owner act on.

## Trust boundaries

| Boundary | Trusted? | Notes |
| --- | --- | --- |
| Caller speech / text | No | Includes prompt-injection attempts. Reaches only the LLM. |
| LLM output | No | Treated like user input: it can only pick a tool and fill typed arguments. |
| Caller ID (`From`) | No | Spoofable. Used to look a caller up, never to authenticate them. |
| PayPal webhooks | Only after verification | Signature checked with PayPal; event id deduplicated. |
| PayPal API responses | Yes, but reconciled | Every capture is compared against our quote. |
| Merchant browser | After login | Signed, http-only session cookie. |

## Threats and controls

### 1. Prompt injection / a manipulated model
*An attacker talks the model into discounts, free food, refunds or charges.*

- The model has no tool that sets prices, discounts or refund amounts. Tool arguments are Pydantic
  models with `extra="forbid"`, so a `price_cents` or `discount_pct` argument is rejected before code
  runs ([apps/agent/tools.py](../apps/agent/tools.py)).
- Prices come from the catalog; the guard checks the quote fingerprint, order state, policy limits and
  slot capacity ([packages/domain/guard.py](../packages/domain/guard.py)).
- There is no "charge" tool at all (see 3).
- Tested as an architectural property, not by keywords: a scripted *fully compromised* model sends
  price overrides, invented charge/discount/refund tools, forged quote fingerprints, absurd quantities,
  and claims success. The test asserts the ledger is unchanged
  ([tests/test_security_and_agent.py](../tests/test_security_and_agent.py), `test_compromised_model_cannot_move_money`;
  30 more variants in [harness/deterministic.py](../harness/deterministic.py)). Adversarial callers generated
  by an attacker LLM are judged the same way in [harness/llm_eval.py](../harness/llm_eval.py).

### 2. Caller-ID spoofing against saved wallets
*Anyone can spoof a regular's number and try to spend their saved PayPal.*

Controls, all required for a saved-wallet charge:

1. **Something the caller has:** before the first vault charge in each call, a 6-digit one-time code is
   texted to the number on file and must be read back (5-minute expiry, 3 attempts, stored as an HMAC)
   ([apps/agent/tools.py](../apps/agent/tools.py) `use_saved_paypal` / `verify_sms_code`). Spoofing caller
   ID does not deliver the SMS to the attacker.
2. **Recorded spoken consent:** the system (not the model) reads one exact sentence with the amount and a
   4-digit code derived from the quote fingerprint. The caller's reply is classified YES/NO/UNCLEAR by a
   deterministic, conservative classifier and stored as a `consent_events` row with transcript, timestamp
   and quote fingerprint ([packages/domain/consent.py](../packages/domain/consent.py)). Barge-in over the
   sentence repeats it instead of counting.
3. **The guard reads both records itself** and checks order, call, quote, amount and age (≤ 5 min). It does
   not accept a boolean from its caller ([packages/domain/guard.py](../packages/domain/guard.py)
   `evaluate_charge_saved_payment`).
4. **Caps and rate limit:** per-mandate cap (default $50) and at most 3 vault charges per mandate per 24 h.
5. **Revocation:** each mandate has a revoke token.

Residual risk: SIM-swap or a compromised handset defeats the SMS factor. The cap and daily limit bound
the loss, and every charge appears in the customer's own PayPal account. A push approval link would be stronger.

Caller numbers are stored only as `HMAC-SHA256(CALLER_HASH_KEY, E.164 number)`, never raw. A plain hash
of a phone number is reversible by enumeration; a keyed one is not without the key
([apps/core/identity.py](../apps/core/identity.py)).

### 3. Charging without real consent
The `spoken_consent_recorded=True` default is gone. Consent is an event, see 2. Tests:
`test_vault_charge_requires_code_and_spoken_yes`, `test_vault_charge_not_made_on_no_or_wrong_code`,
`test_barge_in_repeats_consent_sentence`, `test_spoofed_number_without_code_cannot_charge`.

### 4. Forged or replayed webhooks
- Missing transmission headers → 400; no defaults are substituted.
- Signature verified by PayPal's `verify-webhook-signature` using the raw body; cert URLs not on
  `*.paypal.com` over https are rejected.
- Events without an id → 400. Event ids are a primary key, so a replay is stored once and applied once.
- The endpoint stores and returns 200; state changes happen in the worker in the same transaction as the
  `webhook_events` status update.
([apps/api/routes_public.py](../apps/api/routes_public.py), [apps/worker/worker.py](../apps/worker/worker.py))

### 5. Paying the wrong amount, or a capture that isn't really complete
Every capture is reconciled: status COMPLETED **and** amount, currency and `custom_id` equal to the quote.
PENDING → `PAYMENT_PENDING` (no kitchen ticket); DECLINED → `PAYMENT_DENIED`; any mismatch →
`NEEDS_REVIEW` plus an alert event ([packages/domain/reconcile.py](../packages/domain/reconcile.py),
[apps/core/payments.py](../apps/core/payments.py)).

### 6. Double charges and over-refunds
- Every money-moving call carries a deterministic `PayPal-Request-Id` (`order:{id}:capture`, ...).
- Refunds are bounded by captured minus already-refunded cents. Concurrent refunds that both pass our check
  are still bounded by PayPal, which rejects the excess (`REFUND_AMOUNT_EXCEEDED`).
- Large policy refunds go to an owner approval; the approver is stored in `approvals.decided_by`.

### 7. Unauthorised access to merchant functions
Merchant endpoints require a signed session cookie; agent endpoints require `SERVICE_TOKEN`; CORS allows
only `WEB_BASE_URL`; public endpoints are rate-limited; customer status links are signed and scoped to one
order ([apps/api/security.py](../apps/api/security.py)).

### 8. Misconfiguration that "works" without moving money
`PAYPAL_MODE` has no default and mock mode is never inferred. Sandbox/live refuse to start without
credentials; staging/production refuse mock mode, default secrets, missing merchant credentials and SQLite
([apps/core/settings.py](../apps/core/settings.py), `test_deployed_environment_refuses_unsafe_config`).

## Known gaps

- Single owner account; no roles or per-staff accounts.
- No migration tool yet (tables are created on start).
- The phone channel (Twilio `<Gather>`) is implemented but has not been exercised on a live number.
- SMS factor is weaker than a push approval (see 2).
