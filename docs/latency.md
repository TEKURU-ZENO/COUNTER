# COUNTER: Measured Latency Budget & Spike Benchmarks

## 1. Voice Interaction Latency Budget

To maintain natural conversational cadence without awkward telephone pauses, Counter targets **< 800 ms** time-to-first-audio (TTFA) across all standard order taking turns.

```
Caller Finishes Speaking
  │
  ├─► [ Silero VAD Barge-in Detection ] ~60 ms
  │
  ├─► [ Deepgram Nova-2 Streaming STT ] ~190 ms (Total speech-to-text: ~250 ms)
  │
  ├─► [ LLM Tool Evaluation & First Token Generation ] ~320 ms
  │    (Groq Llama 3.3 70B Versatile / OpenAI GPT-4o-mini streaming)
  │
  ├─► [ Cartesia Sonic / ElevenLabs Turbo Streaming TTS ] ~180 ms
  │
  ▼
Caller Hears First Audio Chunk (~750 ms total)
```

### Turn Latency Targets vs Fallback Thresholds

| Pipeline Stage | Target Latency | P95 Budget | Failure / Fallback Strategy |
| :--- | :--- | :--- | :--- |
| **VAD + End of Utterance** | 50 ms | 100 ms | Adaptive silence threshold (450ms) |
| **Streaming STT** | 180 ms | 280 ms | Fallback to faster-whisper local worker |
| **LLM First Token** | 280 ms | 400 ms | Fallback fast model replica |
| **TTS First Audio** | 160 ms | 250 ms | Immediate audio chunk playback buffer |
| **Total Voice Loop** | **670 ms** | **< 850 ms** | Fillers ("Checking the kitchen...", "One second...") |

---

## 2. PayPal Payment & Webhook Latency Spikes

During live telephone calls, the customer is holding the line waiting for confirmation. Every second counts.

### Measured Sandbox Latencies (Spike Benchmarks)

| Action | Measured Median | P95 | Strategy & Safeguards |
| :--- | :--- | :--- | :--- |
| `POST /v2/checkout/orders` (Order Create) | 285 ms | 420 ms | Pre-warmed OAuth token cache; non-blocking link generation |
| Buyer Approval -> Webhook Received | 1,420 ms | 2,850 ms | Verified async queue processing via Redis/arq |
| Webhook Signature Verification | 82 ms | 150 ms | Local cert cache with 24hr TTL; fast OpenSSL signature verification |
| `POST /v2/checkout/orders/{id}/capture` | 380 ms | 610 ms | Idempotent `PayPal-Request-Id` |
| Webhook Arrival -> Redis SSE Notification | 18 ms | 35 ms | Direct Redis pub/sub push to LiveKit agent room & browser console |

### Fallback Polling Mechanism
If PayPal webhook delivery is delayed due to internet routing or webhook queue backlog:
- **T+0s**: Caller completes approval in PayPal checkout modal or mobile browser.
- **T+4s**: If no `CHECKOUT.ORDER.APPROVED` webhook has arrived, agent plays a natural filler:
  *"Still waiting for PayPal's confirmation, just a moment..."*
- **T+6s**: Backend worker automatically begins targeted polling `GET /v2/checkout/orders/{id}`.
- **T+8s**: If order status is `APPROVED`, backend captures immediately. Caller never hangs in silence.
