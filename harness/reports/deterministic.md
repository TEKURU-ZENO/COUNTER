# Deterministic harness report

Generated 2026-11-12 16:28 UTC on Python 3.14.3 (Windows), SQLite, in 32.1 s.

**What this is:** scripted tool calls run against the **deterministic mock PayPal gateway**. It tests the guard, state machine, payment service and ledger. **No LLM runs here**, so these numbers say nothing about language understanding; see `llm_eval.md` for that, and `sandbox.md` for runs against the real PayPal sandbox.

| Category | Scenarios | Passed | Wrong charges |
| --- | ---: | ---: | ---: |
| Standard orders (link) | 28 | 28 | 0 |
| Mind changers | 25 | 25 | 0 |
| Off-menu requests | 25 | 25 | 0 |
| Refund policy | 30 | 30 | 0 |
| Saved PayPal (vault) | 10 | 10 | 0 |
| Compromised-model attacks | 30 | 30 | 0 |
| **Total** | **148** | **148** | **0** |

Final states observed:

- `CAPTURED`: 75
- `NO_MONEY_MOVED`: 30
- `NO_ORDER`: 25
- `REFUNDED_PARTIAL`: 9
- `REFUNDED_FULL`: 6
- `QUOTED`: 2
- `IN_KITCHEN`: 1

Expected totals come from an independent oracle in `harness/scenarios.py`, not from the pricing engine under test.
