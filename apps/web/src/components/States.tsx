"use client";

import { AlertTriangle, Inbox, Loader2, WifiOff } from "lucide-react";
import type { ReactNode } from "react";
import type { StreamStatus } from "@/lib/useEventStream";

export function Loading({ label = "Loading…" }: { label?: string }) {
  return (
    <div role="status" aria-live="polite" className="flex items-center gap-2 py-10 justify-center text-stone-600 text-sm">
      <Loader2 className="w-4 h-4 animate-spin" aria-hidden />
      <span>{label}</span>
    </div>
  );
}

export function ErrorState({ title, message, action }: { title: string; message: string; action?: ReactNode }) {
  return (
    <div role="alert" className="rounded-xl border border-red-200 bg-red-50 p-5 text-sm text-red-900 space-y-2">
      <div className="flex items-center gap-2 font-semibold">
        <AlertTriangle className="w-4 h-4" aria-hidden />
        {title}
      </div>
      <p>{message}</p>
      {action}
    </div>
  );
}

export function Empty({ title, message, dark = false }: { title: string; message: string; dark?: boolean }) {
  return (
    <div className={`py-14 text-center space-y-2 ${dark ? "text-stone-300" : "text-stone-600"}`}>
      <Inbox className="w-8 h-8 mx-auto opacity-70" aria-hidden />
      <h2 className={`font-semibold ${dark ? "text-white" : "text-stone-900"}`}>{title}</h2>
      <p className="text-sm max-w-sm mx-auto">{message}</p>
    </div>
  );
}

export function LiveBadge({ status, dark = false }: { status: StreamStatus; dark?: boolean }) {
  const text =
    status === "live" ? "Live" : status === "connecting" ? "Connecting…" : status === "reconnecting" ? "Reconnecting…" : "Offline";
  const color =
    status === "live"
      ? "bg-emerald-100 text-emerald-900 border-emerald-300"
      : "bg-amber-100 text-amber-900 border-amber-300";
  return (
    <span
      role="status"
      aria-live="polite"
      className={`inline-flex items-center gap-1.5 text-xs font-medium px-2 py-1 rounded-full border ${color} ${dark ? "" : ""}`}
    >
      {status === "live" ? <span className="w-2 h-2 rounded-full bg-emerald-600" aria-hidden /> : <WifiOff className="w-3 h-3" aria-hidden />}
      {text}
    </span>
  );
}

export function StateBadge({ state, label, tone }: { state: string; label: string; tone: "ok" | "wait" | "bad" | "info" }) {
  const cls = {
    ok: "bg-emerald-100 text-emerald-900",
    wait: "bg-amber-100 text-amber-900",
    bad: "bg-red-100 text-red-900",
    info: "bg-stone-200 text-stone-800",
  }[tone];
  return (
    <span className={`text-xs font-semibold px-2 py-0.5 rounded-full ${cls}`} title={state}>
      {label}
    </span>
  );
}
