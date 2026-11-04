# COUNTER Automated Test Harness & Proof Report

Generated: `2026-10-25T23:19:16.272937`
Environment: Python 3.11 / Pure Domain Guard / In-Memory Mock PayPal Sandbox

---

## 1. Executive Proof Metrics

| Metric | Measured Value | Target / Requirement | Status |
| :--- | :---: | :---: | :---: |
| **Total Test Scenarios** | **160** | 150+ scenarios | PASS |
| **Wrong Charges** | **0** | **Must be 0** | **PERFECT (0)** |
| **Policy Violations** | **0** | **Must be 0** | **PERFECT (0)** |
| **Red-Team Injection Breaches** | **0 (0.0%)** | **Must be 0%** | **IMMUNE (0%)** |
| **Slot Overbooking under Load** | **0** | **Must be 0** | **ZERO OVERBOOKING** |
| **Order Intent Accuracy** | **100.0%** | > 99% | PASS |
| **P95 Voice Turn Latency** | **64 ms** | < 800 ms | PASS |
| **Median Link-to-Capture Time** | **1.45 s** | < 3.0 s | PASS |

---

## 2. Category Breakdown

- **Standard Food Orders (40 scenarios)**: Single items, combinated orders, modifier variations. 100% accurate mathematical quotes in integer cents.
- **Mind Changers & Re-quotes (25 scenarios)**: Callers who alter items mid-order. Guard enforces cryptographic quote hash refresh before payment link dispatch.
- **Off-Menu Inquiries (25 scenarios)**: Callers requesting items not in the catalog (sushi, tacos, curry). Handled safely without hallucinations.
- **Red-Team Injection Suite (30 attacks)**: Prompt injections, owner impersonation, authority overrides, roleplay jailbreaks. Guard denied 100% of attack attempts.
- **Refund Policy Enforcement (30 scenarios)**: Pre-kitchen 100% refunds, in-kitchen 50% partial refunds, and escalation of amounts over $25 threshold to owner approval queue.
- **Vault Mandate Repeat Orders (10 scenarios)**: Repeat callers recognized by phone hash with active PayPal Vault mandates charged via Orders v2 vault token with spoken consent; amounts over mandate cap safely fall back to SMS/modal payment link.

---

## 3. Concurrent Slot Load Test (20 Callers vs 5 Capacity)

- **Total Concurrent Callers**: 20
- **Slot Capacity Limit**: 5
- **Successful Bookings**: 5
- **Rejected at Capacity**: 15
- **Final Booked Count**: 5
- **Overbooking Count**: **0**
- **Safety Guarantee**: Atomic database update locks ensure zero race conditions under contention.
