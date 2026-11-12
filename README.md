# COUNTER

A phone ordering agent for independent takeaway restaurants. It takes the order by voice, gets it paid
through PayPal while the caller is still on the line, and only sends paid orders to the kitchen.

The design rule: **the LLM talks, code decides what is allowed, PayPal moves the money.** The model picks
a tool and fills typed arguments; prices, refund amounts, consent and payment state are decided by
deterministic code that the model cannot reach around.

> Status: hackathon project, running against the **PayPal sandbox**. See [Limitations](#limitations).

## What it does

| Flow | How | Code |
| --- | --- | --- |
| Take an order | LLM tool-calling loop; menu items matched lexically and phonetically, low confidence asks the caller | [apps/agent/session.py](apps/agent/session.py), [catalog_match.py](apps/agent/catalog_match.py) |
| Price it | Integer cents, tax with half-up rounding, a SHA-256 fingerprint of the quote to detect changes after the read-back | [packages/domain/pricing.py](packages/domain/pricing.py) |
| Pay by link | PayPal Orders v2 with itemised breakdown; buyer approves on PayPal's checkout; server captures on return | [apps/core/payments.py](apps/core/payments.py) |
| Pay with saved PayPal | Vault on first purchase (opt-in), then SMS one-time code + recorded spoken consent before a merchant-initiated charge | [docs/threat-model.md](docs/threat-model.md#2-caller-id-spoofing-against-saved-wallets) |
| Reconcile | Capture counts as paid only if COMPLETED and amount/currency/`custom_id` match the quote; otherwise pending, denied or needs review | [packages/domain/reconcile.py](packages/domain/reconcile.py) |
| Refund | Policy refunds by phone (full before cooking, partial during), owner refunds bounded by what is left, approvals above a threshold | [packages/domain/guard.py](packages/domain/guard.py) |
| Webhooks | Verified with PayPal, stored once by event id, applied by the worker | [apps/api/routes_public.py](apps/api/routes_public.py), [apps/worker/worker.py](apps/worker/worker.py) |
| Live screens | Kitchen, console (AG Grid) and customer status page update over SSE fed by a transactional outbox through Redis | [apps/api/sse.py](apps/api/sse.py), [apps/web](apps/web) |
| Ops copilot | LLM with read-only ledger + PayPal reporting tools; answers with a figure it can't trace to a tool result are withheld | [apps/api/copilot.py](apps/api/copilot.py) |
| Menu from a photo | Vision model returns per-field confidence; prices validated; nothing publishes without owner confirmation | [apps/api/menu_extract.py](apps/api/menu_extract.py) |
| Phone channel | Twilio `<Gather>`/`<Say>` with signature verification | [apps/api/routes_twilio.py](apps/api/routes_twilio.py) |

Architecture: [docs/architecture.md](docs/architecture.md). Threats and controls: [docs/threat-model.md](docs/threat-model.md).

## PayPal surfaces used

- **Orders v2:** create (`payment_source.paypal.experience_context`, optional `vault.store_in_vault=ON_SUCCESS`), capture, get; merchant-initiated charge with `payment_source.paypal.vault_id`.
- **Payments v2:** refunds with idempotency keys.
- **Webhooks:** `verify-webhook-signature`; `CHECKOUT.ORDER.APPROVED`, `PAYMENT.CAPTURE.COMPLETED/PENDING/DENIED/REVERSED/REFUNDED`, `VAULT.PAYMENT-TOKEN.CREATED`.
- **Transaction Search:** used by the copilot's `paypal_transactions` tool.
- **Invoicing v2:** client methods for catering deposits; the end-to-end catering flow is not built (see limitations).

Every money-moving call sends a deterministic `PayPal-Request-Id` (e.g. `order:{id}:capture`).

## Evidence

Each claim above has a test or a report. Numbers are only those produced by running the code.

| Evidence | What it shows | Where |
| --- | --- | --- |
| Unit and flow tests | Guard, FSM, money, consent, reconciliation, webhooks, refunds, vault flow, auth, SSE resume, compromised-model suite | `pytest` · [tests/](tests) |
| Domain coverage gate | ≥ 95% branch coverage on `packages/domain`, enforced in CI; strict mypy | [pyproject.toml](pyproject.toml), [.github/workflows/ci.yml](.github/workflows/ci.yml) |
| Deterministic harness | 148 scripted scenarios run **against the deterministic mock gateway** (no LLM): order, re-quote, off-menu, refund policy, saved wallet, 30 compromised-model attacks; outcomes read from the database, expected totals from an independent oracle | [harness/reports/deterministic.md](harness/reports/deterministic.md) |
| LLM evaluation | The real agent against an LLM-simulated caller, several models, Wilson intervals; adversarial callers judged on the ledger | [harness/reports/llm_eval.md](harness/reports/llm_eval.md) |
| Real PayPal sandbox | Opt-in suite: create → buyer approval in a real browser → capture (+ replay) → partial refund → vault charge; appends to a report | [tests/sandbox](tests/sandbox), `harness/reports/sandbox.md` once run |
| Postgres concurrency | 200 concurrent bookings of one pickup slot on Postgres | CI `postgres` job · [harness/load_test.py](harness/load_test.py) |
| Accessibility | axe-core WCAG 2.1 A/AA on every page | [harness/reports/a11y.md](harness/reports/a11y.md) |
| Latency | Generated from stored measurements only | [docs/latency.md](docs/latency.md) |

## Run it locally

Requirements: Python 3.12+, Node 22.

```bash
python -m venv .venv && .venv/bin/pip install -r requirements.txt -r requirements-dev.txt
cp .env.example .env        # set PAYPAL_MODE, secrets, LLM_* (see comments in the file)
python -m infra.scripts.hash_password   # -> OWNER_PASSWORD_HASH
uvicorn apps.api.main:create_app --factory --port 8000
cd apps/web && npm ci && npm run dev    # http://localhost:3000, proxies /api to :8000
```

- `PAYPAL_MODE=mock` runs offline with an explicit, clearly labelled stand-in for PayPal's approval page.
- `PAYPAL_MODE=sandbox` needs a sandbox REST app's client id/secret and a webhook id. Buyers approve on
  `sandbox.paypal.com` with a sandbox personal account. Enable "Save payment methods" on the app for vaulting.
- Without `LLM_PROVIDER`/`LLM_MODEL` and a key, calls return 503. Nothing falls back to keyword matching.
- Full stack with Postgres and Redis: `docker compose up --build`.

Tests:

```bash
pytest                                    # unit + flow tests (mock gateway)
python -m harness.deterministic           # writes harness/reports/deterministic.md
python -m harness.llm_eval --agents openai_compatible:openai/gpt-oss-120b,openai_compatible:qwen/qwen3.8-27b \
  --simulator openai_compatible:openai/gpt-oss-20b --seeds 3
pytest -m sandbox -o addopts="" tests/sandbox   # real sandbox; needs buyer credentials
```

Deploy: [render.yaml](render.yaml) (API, worker, web, Postgres, Key Value). The API refuses to start in
staging/production with mock PayPal, default secrets, missing merchant credentials or SQLite.

## Integrations

| Tool | Use |
| --- | --- |
| AG Grid | Live order grid in the merchant console |
| Render | Blueprint for API, worker, web, Postgres and Key Value |
| Postman | [postman/COUNTER.postman_collection.json](postman/COUNTER.postman_collection.json): flow checks plus webhook and auth negative tests |
| Elastic | OpenTelemetry traces (agent turn, LLM, tools, client-reported STT/TTS) export over OTLP to Elastic Observability |
| Zapier | Signed order events (paid, needs review, reversal, refund approval) to a Zapier Catch Hook |
| KERNEL | Hosted browser for the sandbox approval step of the integration suite |

## Limitations

- **No real merchant yet.** Nobody outside the team has used it with their own menu and customers.
- **Sandbox only.** Live PayPal mode exists but has not been used.
- **Voice:** the web demo uses the browser's speech recognition and synthesis; the Twilio phone channel is
  implemented but untested on a live number. Speech timings are client-reported.
- **LLM quality numbers come from a small evaluation** with an LLM-simulated caller; see the report for sample sizes and intervals.
- **Free-tier LLM limits:** on Groq's free tier the agent can hit token-per-minute limits; it retries and asks the
  caller to repeat, which adds delay.
- **Saved-wallet identity** relies on an SMS code (SIM-swap risk); a push approval would be stronger.
- **Menu matching** is lexical + phonetic; no embeddings.
- **Catering** (deposit invoices) has client methods but no end-to-end flow.
- **Operations:** one owner account, no roles; no migration tool (tables are created at start-up); no POS/printer integration.
- **Webhooks need a public URL;** locally, payment is confirmed through the buyer's return to the site.

## Licence

MIT, see [LICENSE](LICENSE).
