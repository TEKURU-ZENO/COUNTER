"use client";

import { AlertTriangle, Loader2 } from "lucide-react";
import type { ReactNode } from "react";
import type { StreamStatus } from "@/lib/useEventStream";

export function Loading({ label = "Loading…", dark = false }: { label?: string; dark?: boolean }) {
  return (
    <div role="status" aria-live="polite" className={`flex items-center gap-2 py-12 justify-center text-[15px] ${dark ? "text-steel-200" : "text-ink-muted"}`}>
      <Loader2 className="w-4 h-4 animate-spin" aria-hidden />
      <span>{label}</span>
    </div>
  );
}

export function ErrorState({ title, message, action }: { title: string; message: string; action?: ReactNode }) {
  return (
    <div role="alert" className="rounded-2xl bg-declined-soft p-5 text-[15px] text-declined space-y-2">
      <div className="flex items-center gap-2 font-bold">
        <AlertTriangle className="w-4 h-4" aria-hidden />
        {title}
      </div>
      <p className="text-ink-soft">{message}</p>
      {action}
    </div>
  );
}

export function Empty({ title, message, dark = false, children }: { title: string; message: string; dark?: boolean; children?: ReactNode }) {
  return (
    <div className={`py-16 text-center space-y-2 ${dark ? "text-steel-200" : "text-ink-muted"}`}>
      <h2 className={`text-[22px] font-bold tracking-[-0.02em] ${dark ? "text-white" : "text-ink"}`}>{title}</h2>
      <p className="text-[15px] max-w-sm mx-auto">{message}</p>
      {children}
    </div>
  );
}

export function LiveBadge({ status, dark = false }: { status: StreamStatus; dark?: boolean }) {
  const live = status === "live";
  const text = live ? "Live" : status === "connecting" ? "Connecting" : status === "reconnecting" ? "Reconnecting" : "Offline";
  return (
    <span
      role="status"
      aria-live="polite"
      className={`inline-flex items-center gap-2 rounded-full px-3 py-1 text-[13px] font-semibold ${
        dark ? "bg-steel-800 text-steel-200" : "bg-board-sunk text-ink-soft"
      }`}
    >
      <span aria-hidden className={`w-2 h-2 rounded-full ${live ? "bg-paid" : "bg-pending animate-pulse"}`} />
      {text}
    </span>
  );
}

export function StateBadge({ state, label, tone }: { state: string; label: string; tone: "ok" | "wait" | "bad" | "info" }) {
  const cls = {
    ok: "bg-paid-soft text-paid",
    wait: "bg-pending-soft text-pending",
    bad: "bg-declined-soft text-declined",
    info: "bg-board-sunk text-ink-soft",
  }[tone];
  return (
    <span className={`inline-flex h-auto items-center rounded-full px-2.5 py-1 text-[13px] leading-none font-semibold ${cls}`} title={state}>
      {label}
    </span>
  );
}

export function PayPalButton({ href }: { href: string }) {
  return (
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      className="btn-press flex items-center justify-center gap-2 rounded-full bg-paypal-yellow text-paypal-blue min-h-[48px] px-5 font-bold text-[16px] hover:brightness-95"
    >
      Pay with PayPal
      <span className="sr-only">(opens PayPal in a new tab)</span>
    </a>
  );
}
