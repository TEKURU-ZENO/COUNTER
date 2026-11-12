"use client";

import { useEffect, useRef, useState, type ReactNode } from "react";
import { money, type OrderLine } from "@/lib/api";

// What the stamp says for each order state. Only paid states get the green PAID stamp.
const STAMPS: Record<string, { text: string; tone: string }> = {
  LINK_SENT: { text: "Awaiting PayPal", tone: "text-pending" },
  APPROVED: { text: "Capturing", tone: "text-pending" },
  PAYMENT_PENDING: { text: "PayPal pending", tone: "text-pending" },
  CAPTURED: { text: "Paid", tone: "text-paid" },
  IN_KITCHEN: { text: "Cooking", tone: "text-paid" },
  READY: { text: "Ready", tone: "text-paid" },
  PICKED_UP: { text: "Collected", tone: "text-paid" },
  PAYMENT_DENIED: { text: "Declined", tone: "text-declined" },
  LINK_EXPIRED: { text: "Link expired", tone: "text-declined" },
  CANCELLED: { text: "Cancelled", tone: "text-declined" },
  NEEDS_REVIEW: { text: "Under review", tone: "text-declined" },
  REVERSED: { text: "Reversed", tone: "text-declined" },
  REFUNDED_FULL: { text: "Refunded", tone: "text-carbon-ink" },
  REFUNDED_PARTIAL: { text: "Part refunded", tone: "text-carbon-ink" },
  NO_SHOW: { text: "No-show", tone: "text-declined" },
};

export const PAID = new Set(["CAPTURED", "IN_KITCHEN", "READY", "PICKED_UP"]);

export function Stamp({ state }: { state: string }) {
  const s = STAMPS[state];
  const [landed, setLanded] = useState(false);
  const prev = useRef<string | null>(null);
  useEffect(() => {
    // The stamp animates only when the state changes to paid while the ticket is on screen.
    if (prev.current !== null && prev.current !== state && PAID.has(state)) setLanded(true);
    prev.current = state;
  }, [state]);
  if (!s) return null;
  return (
    <span role="status" aria-live="polite" className={`stamp text-[13px] uppercase ${s.tone} ${landed ? "stamp-land" : ""}`}>
      {s.text}
    </span>
  );
}

export function Ticket({
  number,
  state,
  lines,
  subtotal,
  tax,
  total,
  currency = "USD",
  meta,
  children,
  large = false,
  emptyText = "Your order appears here as the agent prices it.",
}: {
  number?: string;
  state?: string;
  lines: OrderLine[];
  subtotal?: number;
  tax?: number;
  total?: number;
  currency?: string;
  meta?: ReactNode;
  children?: ReactNode;
  large?: boolean;
  emptyText?: string;
}) {
  return (
    <div className="relative">
      <div className="ticket-clip" aria-hidden />
      <div className="ticket rounded-b-xl px-5 pt-6 pb-5">
      <div className="flex flex-wrap items-start justify-between gap-x-3 gap-y-2 min-h-[34px]">
        <div className="font-mono text-[12px] uppercase tracking-wide opacity-80 pt-1">{number ? `Ticket ${number}` : "New ticket"}</div>
        {state && <Stamp state={state} />}
      </div>
      {meta && <div className="mt-2 text-[13px] opacity-90">{meta}</div>}
      {lines.length === 0 ? (
        <p className="mt-4 text-[15px] opacity-80">{emptyText}</p>
      ) : (
        <ul className={`mt-4 space-y-2 ${large ? "text-[20px]" : "text-[15px]"}`}>
          {lines.map((l, i) => (
            <li key={i} className="flex items-baseline justify-between gap-3">
              <span className="min-w-0">
                <span className="font-mono font-bold tabular mr-2">{l.qty}×</span>
                <span className="font-semibold">{l.name}</span>
                {l.modifiers.length > 0 && (
                  <span className={`block pl-8 ${large ? "text-[16px]" : "text-[13px]"} opacity-80`}>+ {l.modifiers.join(", ")}</span>
                )}
              </span>
              {!large && <span className="font-mono tabular shrink-0">{money(l.line_total_cents, currency)}</span>}
            </li>
          ))}
        </ul>
      )}
      {total !== undefined && lines.length > 0 && (
        <dl className="ticket-rule mt-4 pt-3 font-mono tabular text-[14px] space-y-1">
          {subtotal !== undefined && (
            <div className="flex justify-between opacity-80">
              <dt>Subtotal</dt>
              <dd>{money(subtotal, currency)}</dd>
            </div>
          )}
          {tax !== undefined && (
            <div className="flex justify-between opacity-80">
              <dt>Tax</dt>
              <dd>{money(tax, currency)}</dd>
            </div>
          )}
          <div className="flex justify-between text-[17px] font-bold">
            <dt>Total</dt>
            <dd>{money(total, currency)}</dd>
          </div>
        </dl>
      )}
      {children}
      </div>
    </div>
  );
}
