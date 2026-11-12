"use client";

import type { ColDef, ICellRendererParams } from "ag-grid-community";
import { AgGridReact } from "ag-grid-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { RequireMerchant } from "@/components/RequireMerchant";
import { Empty, ErrorState, LiveBadge, Loading, StateBadge } from "@/components/States";
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

function StatusCell({ value }: ICellRendererParams<Order, string>) {
  if (!value) return null;
  const s = STATE_LABELS[value] ?? { label: value, tone: "info" as const };
  return (
    <span className="flex h-full items-center leading-5">
      <StateBadge state={value} label={s.label} tone={s.tone} />
    </span>
  );
}

const SUGGESTIONS = ["How much did we take today?", "How much did we refund this week?", "What sold best this week?"];

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
      // Sized to fit the console column at desktop widths, so the grid does not scroll sideways there.
      { field: "id", headerName: "Ticket", width: 96, valueFormatter: (p) => `#${String(p.value).slice(-4).toUpperCase()}` },
      { field: "state", headerName: "Status", width: 176, cellRenderer: StatusCell },
      { headerName: "Items", flex: 2, minWidth: 160, valueGetter: (p) => p.data?.lines.map((l) => `${l.qty}× ${l.name}`).join(", ") ?? "" },
      { field: "total_cents", headerName: "Total", width: 96, type: "rightAligned", valueFormatter: (p) => money(p.value), cellClass: "font-mono tabular" },
      { field: "review_reason", headerName: "Review note", flex: 1, minWidth: 110 },
      {
        field: "created_at",
        headerName: "Time",
        width: 96,
        sort: "desc",
        valueFormatter: (p) => (p.value ? new Date(p.value).toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" }) : ""),
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

  async function ask(q: string) {
    if (q.trim().length < 3) return;
    setQuestion(q);
    setAsking(true);
    setCopilotError(null);
    try {
      setAnswer(await api<CopilotAnswer>("/api/v1/merchant/copilot", { method: "POST", body: JSON.stringify({ question: q }) }));
    } catch (err) {
      setCopilotError((err as ApiError).message);
    } finally {
      setAsking(false);
    }
  }

  const pending = approvals.filter((a) => a.status === "PENDING");
  const review = orders?.filter((o) => o.state === "NEEDS_REVIEW").length ?? 0;

  return (
    <div className="max-w-[1440px] mx-auto px-4 sm:px-6 py-8">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-[clamp(30px,3.6vw,48px)] font-extrabold tracking-[-0.04em] leading-none">Tonight&rsquo;s tickets</h1>
          <p className="mt-2 text-[16px] text-ink-soft">
            {orders === null ? "Loading" : `${orders.length} orders`}
            {review > 0 && <span className="text-declined font-semibold"> · {review} need review</span>}
            {pending.length > 0 && <span className="text-pending font-semibold"> · {pending.length} {pending.length === 1 ? "refund" : "refunds"} to approve</span>}
          </p>
        </div>
        <LiveBadge status={status} />
      </div>
      {error && (
        <div className="mt-6">
          <ErrorState title="The console couldn't load" message={error} />
        </div>
      )}

      <div className="mt-8 grid gap-8 xl:grid-cols-[minmax(0,1fr)_380px]">
        <section aria-label="Orders" className="min-w-0">
          {orders === null ? (
            <Loading label="Loading orders…" />
          ) : orders.length === 0 ? (
            <div className="rounded-[24px] bg-board-sunk">
              <Empty title="No tickets yet" message="Orders appear here live as callers place them." />
            </div>
          ) : (
            <div className="ag-theme-quartz">
              <AgGridReact<Order> rowData={orders} columnDefs={columns} getRowId={(p) => p.data.id} domLayout="autoHeight" animateRows />
            </div>
          )}
        </section>

        <div className="space-y-6">
          <section aria-labelledby="approvals" className="rounded-[24px] bg-board-sunk p-5">
            <h2 id="approvals" className="text-[20px] font-bold tracking-[-0.02em]">
              Refunds to approve
            </h2>
            {pending.length === 0 ? (
              <p className="mt-2 text-[15px] text-ink-muted">Nothing waiting. Refunds above the auto-refund limit land here.</p>
            ) : (
              <ul className="mt-2 divide-y divide-board-line">
                {pending.map((a) => (
                  <li key={a.id} className="py-4 text-[15px]">
                    <div className="flex items-baseline justify-between gap-3">
                      <span className="font-semibold">Ticket #{a.order_id.slice(-4).toUpperCase()}</span>
                      <span className="font-mono tabular font-bold">{money(a.amount_cents)}</span>
                    </div>
                    <p className="mt-1 text-[13px] text-ink-muted">{a.reason}</p>
                    <div className="mt-3 flex gap-2">
                      <button type="button" onClick={() => decide(a.id, true)} className="btn-press flex-1 min-h-[44px] rounded-full bg-ink text-white font-semibold">
                        Refund {money(a.amount_cents)}
                      </button>
                      <button type="button" onClick={() => decide(a.id, false)} className="btn-press min-h-[44px] rounded-full border border-board-line bg-white px-4 font-semibold">
                        Decline
                      </button>
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </section>

          <section aria-labelledby="copilot" className="rounded-[24px] border border-board-line p-5">
            <h2 id="copilot" className="text-[20px] font-bold tracking-[-0.02em]">
              Ask about the money
            </h2>
            <p className="mt-1 text-[14px] text-ink-muted">Read-only. Every figure comes from your ledger or PayPal, or the answer is withheld.</p>
            <form
              className="mt-4 flex gap-2"
              onSubmit={(e) => {
                e.preventDefault();
                void ask(question);
              }}
            >
              <label htmlFor="q" className="sr-only">
                Question
              </label>
              <input
                id="q"
                value={question}
                onChange={(e) => setQuestion(e.target.value)}
                placeholder="Ask about takings or refunds"
                className="min-w-0 flex-1 min-h-[44px] rounded-full border border-board-line bg-white px-4 text-[15px] placeholder:text-ink-muted"
              />
              <button type="submit" disabled={asking} className="btn-press min-h-[44px] rounded-full bg-ink px-5 text-white font-semibold disabled:opacity-60">
                {asking ? "Asking…" : "Ask"}
              </button>
            </form>
            <div className="mt-3 flex flex-wrap gap-2">
              {SUGGESTIONS.map((s) => (
                <button key={s} type="button" onClick={() => ask(s)} className="min-h-[36px] rounded-full bg-board-sunk px-3 text-[13px] font-medium hover:bg-board-line">
                  {s}
                </button>
              ))}
            </div>
            {copilotError && (
              <div className="mt-4">
                <ErrorState title="The copilot couldn't answer" message={copilotError} />
              </div>
            )}
            {answer && (
              <div aria-live="polite" className="mt-4 space-y-2">
                <p className={`text-[16px] leading-relaxed ${answer.grounded ? "" : "text-pending"}`}>{answer.answer}</p>
                <details className="text-[13px]">
                  <summary className="cursor-pointer font-semibold min-h-[32px]">Where the figures come from</summary>
                  <pre className="mt-2 max-h-64 overflow-auto rounded-xl bg-board-sunk p-3 font-mono text-[12px]">{JSON.stringify(answer.trace, null, 2)}</pre>
                </details>
              </div>
            )}
          </section>
        </div>
      </div>
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
