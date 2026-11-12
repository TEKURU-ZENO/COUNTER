"use client";

import { ExternalLink, Mic, MicOff, PhoneCall, PhoneOff, Send, Volume2, VolumeX } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { ErrorState, LiveBadge, StateBadge } from "@/components/States";
import { api, ApiError, money, type Order, STATE_LABELS } from "@/lib/api";
import { useEventStream } from "@/lib/useEventStream";

interface Turn {
  speaker: "agent" | "caller";
  text: string;
}
interface TurnResponse {
  speech: string;
  order: (Order & { status_url: string; approval_url: string | null }) | null;
  ended: boolean;
  handoff: string | null;
  awaiting: string | null;
  llm?: { model: string | null; ttft_ms: number | null; llm_ms: number };
}

// Minimal typing for the browser speech API (not in the TS DOM lib).
type Recognition = {
  lang: string;
  interimResults: boolean;
  onresult: ((e: { results: ArrayLike<ArrayLike<{ transcript: string }>> }) => void) | null;
  onspeechend: (() => void) | null;
  onend: (() => void) | null;
  onerror: (() => void) | null;
  start: () => void;
  stop: () => void;
};

export default function DemoCallPage() {
  const [phone, setPhone] = useState("");
  const [callId, setCallId] = useState<string | null>(null);
  const [token, setToken] = useState("");
  const [turns, setTurns] = useState<Turn[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<{ title: string; message: string } | null>(null);
  const [order, setOrder] = useState<TurnResponse["order"]>(null);
  const [ended, setEnded] = useState(false);
  const [awaiting, setAwaiting] = useState<string | null>(null);
  const [smsInbox, setSmsInbox] = useState<string[] | null>(null);
  const [speak, setSpeak] = useState(true);
  const [listening, setListening] = useState(false);
  const [lastModel, setLastModel] = useState<string | null>(null);
  const pendingTts = useRef<number | null>(null);
  const recognition = useRef<Recognition | null>(null);
  const speechEndAt = useRef<number | null>(null);

  const say = useCallback(
    (text: string) => {
      if (!speak || typeof window === "undefined" || !("speechSynthesis" in window)) return;
      const t0 = performance.now();
      const u = new SpeechSynthesisUtterance(text);
      u.onstart = () => {
        pendingTts.current = Math.round(performance.now() - t0);
      };
      window.speechSynthesis.cancel();
      window.speechSynthesis.speak(u);
    },
    [speak],
  );

  const apply = useCallback(
    (r: TurnResponse) => {
      setTurns((t) => [...t, { speaker: "agent", text: r.speech }]);
      setOrder(r.order);
      setEnded(r.ended || !!r.handoff);
      setAwaiting(r.awaiting);
      if (r.llm?.model) setLastModel(r.llm.model);
      say(r.speech);
    },
    [say],
  );

  async function startCall(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      const r = await api<{ call_id: string; call_token: string; speech: string; demo_sms_inbox: boolean }>(
        "/api/v1/calls",
        { method: "POST", body: JSON.stringify({ caller_phone: phone }) },
      );
      setCallId(r.call_id);
      setToken(r.call_token);
      setTurns([{ speaker: "agent", text: r.speech }]);
      setOrder(null);
      setEnded(false);
      setSmsInbox(r.demo_sms_inbox ? [] : null);
      say(r.speech);
    } catch (err) {
      const e2 = err as ApiError;
      setError({
        title: e2.status === 503 ? "The ordering assistant isn't configured" : "Couldn't start the call",
        message: e2.message,
      });
    } finally {
      setBusy(false);
    }
  }

  async function sendTurn(text: string, opts: { stt_ms?: number | null } = {}) {
    if (!callId || !text.trim()) return;
    const interrupted = typeof window !== "undefined" && window.speechSynthesis?.speaking;
    if (interrupted) window.speechSynthesis.cancel();
    setTurns((t) => [...t, { speaker: "caller", text }]);
    setInput("");
    setBusy(true);
    setError(null);
    try {
      const r = await api<TurnResponse>(`/api/v1/calls/${callId}/turns`, {
        method: "POST",
        headers: { "X-Call-Token": token },
        body: JSON.stringify({
          text,
          interrupted: !!interrupted,
          stt_ms: opts.stt_ms ?? null,
          tts_ms: pendingTts.current,
          caller_phone: phone,
        }),
      });
      pendingTts.current = null;
      apply(r);
    } catch (err) {
      const e2 = err as ApiError;
      setError({ title: e2.status === 503 ? "The assistant is unavailable" : "That turn failed", message: e2.message });
    } finally {
      setBusy(false);
    }
  }

  function toggleMic() {
    const W = window as unknown as { SpeechRecognition?: new () => Recognition; webkitSpeechRecognition?: new () => Recognition };
    const Ctor = W.SpeechRecognition || W.webkitSpeechRecognition;
    if (!Ctor) {
      setError({ title: "Voice input isn't available", message: "This browser has no speech recognition. Type instead." });
      return;
    }
    if (listening) {
      recognition.current?.stop();
      return;
    }
    if (window.speechSynthesis?.speaking) window.speechSynthesis.cancel(); // barge-in
    const rec = new Ctor();
    rec.lang = "en-US";
    rec.interimResults = false;
    rec.onspeechend = () => {
      speechEndAt.current = performance.now();
    };
    rec.onresult = (e) => {
      const text = e.results[0][0].transcript;
      const stt = speechEndAt.current ? Math.round(performance.now() - speechEndAt.current) : null;
      void sendTurn(text, { stt_ms: stt });
    };
    rec.onend = () => setListening(false);
    rec.onerror = () => setListening(false);
    recognition.current = rec;
    setListening(true);
    rec.start();
  }

  // Live order status via SSE on the customer-scoped stream.
  const statusToken = order?.status_url ? new URLSearchParams(order.status_url.split("?")[1]).get("t") : null;
  const streamUrl = order && statusToken ? `/api/v1/orders/${order.id}/stream?t=${encodeURIComponent(statusToken)}` : null;
  const stream = useEventStream(streamUrl, (ev) => {
    const next = ev.payload?.state as string | undefined;
    if (next) setOrder((o) => (o ? { ...o, state: next } : o));
  });

  useEffect(() => {
    if (!callId || smsInbox === null || awaiting !== "otp") return;
    const id = setInterval(() => {
      api<{ messages: string[] }>(`/api/v1/calls/${callId}/demo-sms`, { headers: { "X-Call-Token": token } })
        .then((r) => setSmsInbox(r.messages))
        .catch(() => undefined);
    }, 2000);
    return () => clearInterval(id);
  }, [callId, token, awaiting, smsInbox]);

  const label = order ? STATE_LABELS[order.state] ?? { label: order.state, tone: "info" as const } : null;

  return (
    <div className="max-w-6xl mx-auto px-4 py-8 grid lg:grid-cols-5 gap-6">
      <section className="lg:col-span-3 space-y-4" aria-labelledby="call-heading">
        <div>
          <h1 id="call-heading" className="text-2xl font-serif font-bold">
            Try a phone order
          </h1>
          <p className="text-sm text-stone-700 mt-1">
            You are the caller. The agent is a real LLM choosing tools; prices, payment and refunds are decided by
            the server. Payment happens on PayPal&apos;s checkout page.
          </p>
        </div>

        {error && <ErrorState title={error.title} message={error.message} />}

        {!callId ? (
          <form onSubmit={startCall} className="bg-white rounded-xl border border-stone-200 p-5 space-y-3">
            <label htmlFor="phone" className="block text-sm font-medium">
              Your phone number (used as caller ID for this call)
            </label>
            <input
              id="phone"
              type="tel"
              required
              minLength={7}
              autoComplete="tel"
              value={phone}
              onChange={(e) => setPhone(e.target.value)}
              className="w-full rounded-lg border border-stone-300 px-3 py-2"
              placeholder="+1 415 555 0100"
            />
            <button
              type="submit"
              disabled={busy}
              className="inline-flex items-center gap-2 rounded-lg bg-terracotta-700 text-white px-4 py-2 font-semibold disabled:opacity-60"
            >
              <PhoneCall className="w-4 h-4" aria-hidden /> {busy ? "Calling…" : "Start call"}
            </button>
          </form>
        ) : (
          <div className="bg-white rounded-xl border border-stone-200">
            <ol aria-live="polite" aria-label="Call transcript" className="p-4 space-y-3 max-h-[28rem] overflow-y-auto">
              {turns.map((t, i) => (
                <li key={i} className={t.speaker === "agent" ? "" : "text-right"}>
                  <span className="sr-only">{t.speaker === "agent" ? "Agent:" : "You:"}</span>
                  <span
                    className={`inline-block rounded-2xl px-3 py-2 text-sm max-w-[85%] ${
                      t.speaker === "agent" ? "bg-stone-100 text-stone-900" : "bg-terracotta-700 text-white"
                    }`}
                  >
                    {t.text}
                  </span>
                </li>
              ))}
              {busy && (
                <li role="status" className="text-xs text-stone-600">
                  Agent is thinking…
                </li>
              )}
            </ol>
            {ended ? (
              <div className="border-t border-stone-200 p-4 flex items-center justify-between text-sm">
                <span className="flex items-center gap-2 text-stone-700">
                  <PhoneOff className="w-4 h-4" aria-hidden /> Call ended
                </span>
                <button type="button" className="underline" onClick={() => setCallId(null)}>
                  Start a new call
                </button>
              </div>
            ) : (
              <form
                className="border-t border-stone-200 p-3 flex gap-2"
                onSubmit={(e) => {
                  e.preventDefault();
                  void sendTurn(input);
                }}
              >
                <label htmlFor="say" className="sr-only">
                  What you say
                </label>
                <input
                  id="say"
                  value={input}
                  onChange={(e) => setInput(e.target.value)}
                  disabled={busy}
                  autoComplete="off"
                  className="flex-1 rounded-lg border border-stone-300 px-3 py-2"
                  placeholder={awaiting === "consent" ? "Say yes or no" : "Type what you'd say"}
                />
                <button
                  type="button"
                  onClick={toggleMic}
                  aria-pressed={listening}
                  aria-label={listening ? "Stop listening" : "Speak"}
                  className="rounded-lg border border-stone-300 px-3"
                >
                  {listening ? <MicOff className="w-4 h-4" aria-hidden /> : <Mic className="w-4 h-4" aria-hidden />}
                </button>
                <button
                  type="button"
                  onClick={() => setSpeak((s) => !s)}
                  aria-pressed={speak}
                  aria-label={speak ? "Mute agent voice" : "Unmute agent voice"}
                  className="rounded-lg border border-stone-300 px-3"
                >
                  {speak ? <Volume2 className="w-4 h-4" aria-hidden /> : <VolumeX className="w-4 h-4" aria-hidden />}
                </button>
                <button
                  type="submit"
                  disabled={busy || !input.trim()}
                  className="rounded-lg bg-terracotta-700 text-white px-3 disabled:opacity-60"
                  aria-label="Send"
                >
                  <Send className="w-4 h-4" aria-hidden />
                </button>
              </form>
            )}
          </div>
        )}
        {lastModel && <p className="text-xs text-stone-600">Model: {lastModel}</p>}
      </section>

      <aside className="lg:col-span-2 space-y-4" aria-label="Order">
        <div className="bg-white rounded-xl border border-stone-200 p-5">
          <div className="flex items-center justify-between">
            <h2 className="font-semibold">Order</h2>
            {order && <LiveBadge status={stream} />}
          </div>
          {!order ? (
            <p className="text-sm text-stone-600 mt-3">Nothing ordered yet. Items appear here once the agent prices them.</p>
          ) : (
            <div className="mt-3 space-y-3 text-sm">
              {label && <StateBadge state={order.state} label={label.label} tone={label.tone} />}
              <ul className="space-y-1">
                {order.lines.map((l, i) => (
                  <li key={i} className="flex justify-between gap-2">
                    <span>
                      {l.qty}× {l.name}
                      {l.modifiers.length > 0 && <span className="text-stone-600"> ({l.modifiers.join(", ")})</span>}
                    </span>
                    <span className="tabular-nums">{money(l.line_total_cents, order.currency)}</span>
                  </li>
                ))}
              </ul>
              <dl className="border-t border-stone-200 pt-2 space-y-1 tabular-nums">
                <div className="flex justify-between text-stone-700">
                  <dt>Tax</dt>
                  <dd>{money(order.tax_cents, order.currency)}</dd>
                </div>
                <div className="flex justify-between font-semibold">
                  <dt>Total</dt>
                  <dd>{money(order.total_cents, order.currency)}</dd>
                </div>
              </dl>
              {order.state === "LINK_SENT" && order.approval_url && (
                <a
                  href={order.approval_url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="flex items-center justify-center gap-2 rounded-lg bg-[#0070ba] text-white py-2 font-semibold"
                >
                  Pay with PayPal <ExternalLink className="w-4 h-4" aria-hidden />
                  <span className="sr-only">(opens in a new tab)</span>
                </a>
              )}
              {order.status_url && (
                <a href={order.status_url} className="block text-center underline text-stone-700">
                  Open the customer status page
                </a>
              )}
            </div>
          )}
        </div>

        {smsInbox !== null && (
          <div className="bg-amber-50 border border-amber-300 rounded-xl p-4 text-sm">
            <h2 className="font-semibold text-amber-900">Demo SMS inbox</h2>
            <p className="text-amber-900 text-xs mt-1">
              Sandbox only. Stands in for your phone so you can read the code the agent texts. Disabled in production.
            </p>
            {smsInbox.length === 0 ? (
              <p className="mt-2 text-amber-900">No messages.</p>
            ) : (
              <ul className="mt-2 space-y-1" aria-live="polite">
                {smsInbox.map((m, i) => (
                  <li key={i} className="font-mono text-xs">
                    {m}
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}
      </aside>
    </div>
  );
}
