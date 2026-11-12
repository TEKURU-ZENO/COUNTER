"use client";

import type { ColDef } from "ag-grid-community";
import { AgGridReact } from "ag-grid-react";
import { MessageSquare } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { RequireMerchant } from "@/components/RequireMerchant";
import { Empty, ErrorState, LiveBadge, Loading } from "@/components/States";
import { api, ApiError, money, type Order, STATE_LABELS } from "@/lib/api";
import { useEventStream } from "@/lib/useEventStream";

interface Approval {
  id: string;
  order_id: string;
  amount_cents: number;
  reason: string;
  status: string;
  decided_by: string | null;
}
interface CopilotAnswer {
  answer: string;
  grounded: boolean;
  untraced_figures: string[];
  trace: { tool: string; args: Record<string, unknown>; result: unknown }[];
  model: string | null;
}

function Console() {
  const [orders, setOrders] = useState<Order[] | null>(null);
  const [approvals, setApprovals] = useState<Approval[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState<CopilotAnswer | null>(null);
  const [asking, setAsking] = useState(false);
  const [copilotError, setCopilotError] = useState<string | null>(null);

  const load = useCallback(() => {
    Promise.all([api<Order[]>("/api/v1/merchant/orders"), api<Approval[]>("/api/v1/merchant/approvals")])
      .then(([o, a]) => {
        setOrders(o);
        setApprovals(a);
        setError(null);
      })
      .catch((e: ApiError) => setError(e.message));
  }, []);
  useEffect(load, [load]);
  const status = useEventStream("/api/v1/merchant/stream", load);

  const columns = useMemo<ColDef<Order>[]>(
    () => [
      { field: "id", headerName: "Order", width: 150, valueFormatter: (p) => `#${String(p.value).slice(-6)}` },
      { field: "state", headerName: "Status", width: 190, valueFormatter: (p) => STATE_LABELS[p.value]?.label ?? p.value },
      {
        headerName: "Items", flex: 2,
        valueGetter: (p) => p.data?.lines.map((l) => `${l.qty}× ${l.name}`).join(", ") ?? "",
      },
      { field: "total_cents", headerName: "Total", width: 110, valueFormatter: (p) => money(p.value) },
      { field: "review_reason", headerName: "Review note", flex: 1 },
      {
        field: "created_at", headerName: "Created", width: 120, sort: "desc",
        valueFormatter: (p) => (p.value ? new Date(p.value).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" }) : ""),
      },
    ],
    [],
  );

  async function decide(id: string, approve: boolean) {
    try {
      await api(`/api/v1/merchant/approvals/${id}/decision`, { method: "POST", body: JSON.stringify({ approve }) });
      load();
    } catch (e) {
      setError((e as ApiError).message);
    }
  }

  async function ask(e: React.FormEvent) {
    e.preventDefault();
    setAsking(true);
    setCopilotError(null);
    try {
      setAnswer(await api<CopilotAnswer>("/api/v1/merchant/copilot", { method: "POST", body: JSON.stringify({ question }) }));
    } catch (err) {
      setCopilotError((err as ApiError).message);
    } finally {
      setAsking(false);
    }
  }

  const pending = approvals.filter((a) => a.status === "PENDING");
  return (
    <div className="max-w-7xl mx-auto px-4 py-8 space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-serif font-bold">Console</h1>
        <LiveBadge status={status} />
      </div>
      {error && <ErrorState title="Couldn't load orders" message={error} />}

      {pending.length > 0 && (
        <section aria-labelledby="approvals" className="bg-amber-50 border border-amber-300 rounded-xl p-4">
          <h2 id="approvals" className="font-semibold">Refunds waiting for your approval</h2>
          <ul className="mt-2 divide-y divide-amber-200">
            {pending.map((a) => (
              <li key={a.id} className="py-2 flex flex-wrap items-center justify-between gap-2 text-sm">
                <span>
                  #{a.order_id.slice(-6)}: <strong>{money(a.amount_cents)}</strong> — {a.reason}
                </span>
                <span className="flex gap-2">
                  <button type="button" onClick={() => decide(a.id, true)} className="rounded-lg bg-emerald-700 text-white px-3 py-1">
                    Approve refund
                  </button>
                  <button type="button" onClick={() => decide(a.id, false)} className="rounded-lg border border-stone-400 px-3 py-1">
                    Reject
                  </button>
                </span>
              </li>
            ))}
          </ul>
        </section>
      )}

      <section aria-label="Orders" className="bg-white rounded-xl border border-stone-200 p-2">
        {orders === null ? (
          <Loading label="Loading orders…" />
        ) : orders.length === 0 ? (
          <Empty title="No orders yet" message="Orders appear here live as callers place them." />
        ) : (
          <div className="ag-theme-alpine" style={{ height: 420 }}>
            <AgGridReact<Order> rowData={orders} columnDefs={columns} getRowId={(p) => p.data.id} animateRows />
          </div>
        )}
      </section>

      <section aria-labelledby="copilot" className="bg-white rounded-xl border border-stone-200 p-5 space-y-3">
        <h2 id="copilot" className="font-semibold flex items-center gap-2">
          <MessageSquare className="w-4 h-4" aria-hidden /> Ask about your orders
        </h2>
        <p className="text-xs text-stone-600">
          Read-only. Answers only include figures that come straight from your ledger or PayPal reporting.
        </p>
        <form onSubmit={ask} className="flex gap-2">
          <label htmlFor="q" className="sr-only">Question</label>
          <input id="q" value={question} onChange={(e) => setQuestion(e.target.value)} minLength={3} required
            placeholder="How much did we refund this week?" className="flex-1 rounded-lg border border-stone-300 px-3 py-2" />
          <button type="submit" disabled={asking} className="rounded-lg bg-terracotta-700 text-white px-4 disabled:opacity-60">
            {asking ? "Asking…" : "Ask"}
          </button>
        </form>
        {copilotError && <ErrorState title="Copilot unavailable" message={copilotError} />}
        {answer && (
          <div aria-live="polite" className="text-sm space-y-2">
            <p className={answer.grounded ? "" : "text-amber-900"}>{answer.answer}</p>
            <details>
              <summary className="cursor-pointer text-stone-700">Where these figures come from</summary>
              <pre className="mt-2 bg-stone-50 p-3 rounded-lg overflow-x-auto text-xs">{JSON.stringify(answer.trace, null, 2)}</pre>
            </details>
          </div>
        )}
      </section>
    </div>
  );
}

export default function ConsolePage() {
  return (
    <RequireMerchant>
      <Console />
    </RequireMerchant>
  );
}
