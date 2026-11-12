"use client";

import { Mic, MicOff, PhoneOff, Send, Volume2, VolumeX } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { type MenuItem, Pin } from "@/components/Pin";
import { ErrorState, LiveBadge, Loading, PayPalButton } from "@/components/States";
import { Ticket } from "@/components/Ticket";
import { api, ApiError, type Order } from "@/lib/api";
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

export default function OrderPage() {
  const [menu, setMenu] = useState<MenuItem[] | null>(null);
  const [menuError, setMenuError] = useState<string | null>(null);
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
  const sayRef = useRef<HTMLInputElement>(null);
  const phoneRef = useRef<HTMLInputElement>(null);
  const transcriptEnd = useRef<HTMLLIElement>(null);

  useEffect(() => {
    api<MenuItem[]>("/api/v1/menu")
      .then(setMenu)
      .catch((e: ApiError) => setMenuError(e.message));
  }, []);

  useEffect(() => {
    transcriptEnd.current?.scrollIntoView({ block: "nearest" });
  }, [turns, busy]);

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
      const r = await api<{ call_id: string; call_token: string; speech: string; demo_sms_inbox: boolean }>("/api/v1/calls", {
        method: "POST",
        body: JSON.stringify({ caller_phone: phone }),
      });
      setCallId(r.call_id);
      setToken(r.call_token);
      setTurns([{ speaker: "agent", text: r.speech }]);
      setOrder(null);
      setEnded(false);
      setSmsInbox(r.demo_sms_inbox ? [] : null);
      say(r.speech);
      setTimeout(() => sayRef.current?.focus(), 0);
    } catch (err) {
      const e2 = err as ApiError;
      setError({ title: e2.status === 503 ? "The ordering assistant isn't set up" : "The call didn't connect", message: e2.message });
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
        body: JSON.stringify({ text, interrupted: !!interrupted, stt_ms: opts.stt_ms ?? null, tts_ms: pendingTts.current, caller_phone: phone }),
      });
      pendingTts.current = null;
      apply(r);
    } catch (err) {
      const e2 = err as ApiError;
      setError({ title: e2.status === 503 ? "The assistant is busy" : "That didn't go through", message: e2.message });
    } finally {
      setBusy(false);
    }
  }

  function toggleMic() {
    const W = window as unknown as { SpeechRecognition?: new () => Recognition; webkitSpeechRecognition?: new () => Recognition };
    const Ctor = W.SpeechRecognition || W.webkitSpeechRecognition;
    if (!Ctor) {
      setError({ title: "Voice isn't available in this browser", message: "Type what you'd say instead." });
      return;
    }
    if (listening) {
      recognition.current?.stop();
      return;
    }
    if (window.speechSynthesis?.speaking) window.speechSynthesis.cancel();
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

  function pick(item: MenuItem) {
    if (!callId || ended) {
      phoneRef.current?.focus();
      setError({ title: "Start the call first", message: `Then ask for the ${item.name}, or tap it again.` });
      return;
    }
    setInput((cur) => (cur.trim() ? `${cur.trim()} and a ${item.name}` : `Can I get a ${item.name}?`));
    sayRef.current?.focus();
  }

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

  return (
    <div className="max-w-[1440px] mx-auto px-4 sm:px-6 pt-8 pb-28 lg:pb-16 grid gap-x-8 gap-y-8 lg:gap-y-0 [grid-template-areas:'head'_'board'_'side'] lg:grid-cols-[minmax(0,1fr)_400px] lg:[grid-template-areas:'head_side'_'board_side'] lg:grid-rows-[auto_1fr]">
      <header className="[grid-area:head] min-w-0">
        <h1 className="text-[clamp(34px,4.6vw,60px)] font-extrabold leading-[0.98] tracking-[-0.04em] max-w-[16ch] text-balance">
          Order by voice. Paid before the oven&rsquo;s lit.
        </h1>
        <p className="mt-4 max-w-[58ch] text-[17px] leading-relaxed text-ink-soft">
          Call Luigi&rsquo;s, say what you want, and pay on PayPal while you&rsquo;re still on the line. The kitchen only sees
          tickets PayPal has confirmed.
        </p>
      </header>
      <section aria-labelledby="board-heading" className="[grid-area:board] min-w-0">
        <h2 id="board-heading" className="sr-only">
          Menu
        </h2>
        <div className="lg:mt-8">
          {menuError ? (
            <ErrorState title="The menu didn't load" message={menuError} />
          ) : menu === null ? (
            <Loading label="Laying out the menu…" />
          ) : menu.length === 0 ? (
            <p className="text-ink-muted">The menu is empty. The owner can add dishes from a photo of the menu.</p>
          ) : (
            <ul className="board columns-2 md:columns-3 xl:columns-4">
              {menu.map((item, i) => (
                <Pin
                  key={item.id}
                  item={item}
                  index={i}
                  onPick={pick}
                  disabled={busy}
                  onTicket={order?.lines.filter((l) => l.name === item.name).reduce((n, l) => n + l.qty, 0) ?? 0}
                />
              ))}
            </ul>
          )}
        </div>
      </section>

      <aside aria-label="Your call and ticket" className="[grid-area:side] lg:sticky lg:top-6 self-start space-y-6">
        <section id="call" aria-labelledby="call-heading" className="scroll-mt-6 rounded-[24px] bg-board-sunk p-4 sm:p-5">
          <div className="flex items-center justify-between gap-3">
            <h2 id="call-heading" className="text-[20px] font-bold tracking-[-0.02em]">
              {callId && !ended ? "On the line" : "Call the restaurant"}
            </h2>
            {lastModel && <span className="text-[12px] text-ink-muted">{lastModel}</span>}
          </div>

          {error && (
            <div className="mt-3">
              <ErrorState title={error.title} message={error.message} />
            </div>
          )}

          {!callId ? (
            <form onSubmit={startCall} className="mt-4 space-y-3">
              <label htmlFor="phone" className="block text-[14px] font-semibold">
                Your phone number
                <span className="block font-normal text-ink-muted">Used as caller ID, and for a code if you pay with saved PayPal.</span>
              </label>
              <input
                ref={phoneRef}
                id="phone"
                type="tel"
                required
                minLength={7}
                autoComplete="tel"
                value={phone}
                onChange={(e) => setPhone(e.target.value)}
                className="w-full min-h-[48px] rounded-full border border-board-line bg-white px-5 text-[16px] placeholder:text-ink-muted"
                placeholder="+1 415 555 0100"
              />
              <button
                type="submit"
                disabled={busy}
                className="btn-press w-full min-h-[48px] rounded-full bg-ink text-white font-bold text-[16px] disabled:opacity-60"
              >
                {busy ? "Connecting…" : "Start the call"}
              </button>
            </form>
          ) : (
            <>
              <ol aria-live="polite" aria-label="Call transcript" className="mt-4 space-y-2.5 max-h-[34vh] overflow-y-auto pr-1">
                {turns.map((t, i) => (
                  <li key={i} className={t.speaker === "agent" ? "flex" : "flex justify-end"}>
                    <span className="sr-only">{t.speaker === "agent" ? "Agent:" : "You:"}</span>
                    <span
                      className={`inline-block max-w-[88%] rounded-[18px] px-4 py-2.5 text-[15px] leading-snug ${
                        t.speaker === "agent" ? "bg-white text-ink rounded-bl-md" : "bg-ink text-white rounded-br-md"
                      }`}
                    >
                      {t.text}
                    </span>
                  </li>
                ))}
                {busy && (
                  <li role="status" className="text-[13px] text-ink-muted pl-1">
                    The agent is thinking…
                  </li>
                )}
                <li ref={transcriptEnd} aria-hidden />
              </ol>
              {ended ? (
                <div className="mt-4 flex items-center justify-between text-[15px]">
                  <span className="flex items-center gap-2 text-ink-soft">
                    <PhoneOff className="w-4 h-4" aria-hidden /> Call ended
                  </span>
                  <button type="button" className="underline font-semibold min-h-[44px]" onClick={() => setCallId(null)}>
                    Call again
                  </button>
                </div>
              ) : (
                <form
                  className="mt-4 flex gap-2"
                  onSubmit={(e) => {
                    e.preventDefault();
                    void sendTurn(input);
                  }}
                >
                  <label htmlFor="say" className="sr-only">
                    What you say
                  </label>
                  <input
                    ref={sayRef}
                    id="say"
                    value={input}
                    onChange={(e) => setInput(e.target.value)}
                    disabled={busy}
                    autoComplete="off"
                    className="min-w-0 flex-1 min-h-[48px] rounded-full border border-board-line bg-white px-4 text-[16px] placeholder:text-ink-muted"
                    placeholder={awaiting === "consent" ? "Say yes or no" : awaiting === "otp" ? "Read the code" : "Say something, or tap a dish"}
                  />
                  <button
                    type="button"
                    onClick={toggleMic}
                    aria-pressed={listening}
                    aria-label={listening ? "Stop listening" : "Speak"}
                    className={`btn-press grid place-items-center w-12 h-12 rounded-full ${listening ? "bg-declined text-white" : "bg-white border border-board-line"}`}
                  >
                    {listening ? <MicOff className="w-5 h-5" aria-hidden /> : <Mic className="w-5 h-5" aria-hidden />}
                  </button>
                  <button
                    type="submit"
                    disabled={busy || !input.trim()}
                    aria-label="Send"
                    className="btn-press grid place-items-center w-12 h-12 rounded-full bg-ink text-white disabled:opacity-40"
                  >
                    <Send className="w-5 h-5" aria-hidden />
                  </button>
                </form>
              )}
              <button
                type="button"
                onClick={() => setSpeak((s) => !s)}
                aria-pressed={speak}
                className="mt-2 inline-flex items-center gap-1.5 min-h-[44px] text-[13px] text-ink-muted hover:text-ink"
              >
                {speak ? <Volume2 className="w-4 h-4" aria-hidden /> : <VolumeX className="w-4 h-4" aria-hidden />}
                {speak ? "Agent voice on" : "Agent voice off"}
              </button>
            </>
          )}
        </section>

        <section aria-label="Your ticket">
          <div className="rail mx-2" aria-hidden />
          <div className="px-5 -mt-1">
            {!order ? (
              <div className="relative">
                <div className="ticket-clip" aria-hidden />
                <p className="ticket rounded-b-xl px-5 pt-5 pb-4 text-[14px]">Your ticket starts when the agent prices your order.</p>
              </div>
            ) : (
            <Ticket
              number={order ? `#${order.id.slice(-4).toUpperCase()}` : undefined}
              state={order?.state}
              lines={order?.lines ?? []}
              subtotal={order?.subtotal_cents}
              tax={order?.tax_cents}
              total={order?.total_cents}
              currency={order?.currency}
              meta={order ? <LiveBadge status={stream} /> : undefined}
            >
              {order?.state === "LINK_SENT" && order.approval_url && (
                <div className="mt-4">
                  <PayPalButton href={order.approval_url} />
                </div>
              )}
              {order?.status_url && (
                <a href={order.status_url} className="mt-3 inline-flex min-h-[44px] items-center text-[14px] font-semibold underline">
                  Follow this order
                </a>
              )}
            </Ticket>
            )}
          </div>
        </section>

        <a
          href="#call"
          className="btn-press lg:hidden fixed inset-x-4 bottom-4 z-40 flex items-center justify-center gap-2 min-h-[52px] rounded-full bg-ink text-white font-bold text-[16px] shadow-lift"
        >
          {callId && !ended ? (order ? `Back to the call · ${order.lines.reduce((n, l) => n + l.qty, 0)} on your ticket` : "Back to the call") : "Call to order"}
        </a>

        {smsInbox !== null && (
          <section aria-labelledby="sms-heading" className="rounded-[20px] border border-board-line p-4 text-[14px]">
            <h2 id="sms-heading" className="font-bold">
              Your phone&rsquo;s texts
            </h2>
            <p className="text-ink-muted text-[13px] mt-0.5">Sandbox only: stands in for your handset so you can read the code.</p>
            {smsInbox.length === 0 ? (
              <p className="mt-2 text-ink-muted">No texts yet.</p>
            ) : (
              <ul className="mt-2 space-y-1" aria-live="polite">
                {smsInbox.map((m, i) => (
                  <li key={i} className="font-mono text-[13px]">
                    {m}
                  </li>
                ))}
              </ul>
            )}
          </section>
        )}
      </aside>
    </div>
  );
}
