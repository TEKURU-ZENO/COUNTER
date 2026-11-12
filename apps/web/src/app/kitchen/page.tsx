"use client";

import { Check, ChefHat, Flame } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { RequireMerchant } from "@/components/RequireMerchant";
import { Empty, ErrorState, LiveBadge, Loading } from "@/components/States";
import { api, ApiError, type Order } from "@/lib/api";
import { useEventStream } from "@/lib/useEventStream";

// Only paid orders appear here: CAPTURED and IN_KITCHEN. PAYMENT_PENDING never does.
const KITCHEN_STATES = "CAPTURED,IN_KITCHEN";

function Kitchen() {
  const [orders, setOrders] = useState<Order[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [acting, setActing] = useState<string | null>(null);

  const load = useCallback(() => {
    api<Order[]>(`/api/v1/merchant/orders?state=${KITCHEN_STATES}`)
      .then((o) => {
        setOrders(o.sort((a, b) => (a.created_at ?? "").localeCompare(b.created_at ?? "")));
        setError(null);
      })
      .catch((e: ApiError) => setError(e.message));
  }, []);

  useEffect(load, [load]);
  const status = useEventStream("/api/v1/merchant/stream", load);

  async function act(id: string, action: "kitchen_accept" | "mark_ready") {
    setActing(id);
    try {
      await api(`/api/v1/merchant/orders/${id}/actions`, { method: "POST", body: JSON.stringify({ action }) });
      load();
    } catch (e) {
      setError((e as ApiError).message);
    } finally {
      setActing(null);
    }
  }

  return (
    <div className="min-h-[calc(100vh-3.5rem)] bg-charcoal-950 text-white p-6 space-y-6">
      <div className="flex items-center justify-between flex-wrap gap-3">
        <h1 className="text-2xl font-serif font-bold flex items-center gap-2">
          <ChefHat className="w-6 h-6" aria-hidden /> Kitchen
        </h1>
        <div className="flex items-center gap-3 text-sm text-stone-300">
          <span>Paid orders only</span>
          <LiveBadge status={status} dark />
        </div>
      </div>
      {error && <ErrorState title="Kitchen display problem" message={error} />}
      {orders === null ? (
        <div className="text-stone-300"><Loading label="Loading tickets…" /></div>
      ) : orders.length === 0 ? (
        <Empty dark title="No tickets" message="Paid orders appear here the moment PayPal confirms the capture." />
      ) : (
        <ul className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
          {orders.map((o) => (
            <li key={o.id} className={`rounded-2xl border p-5 bg-stone-900 ${o.state === "IN_KITCHEN" ? "border-sky-500" : "border-terracotta-500"}`}>
              <div className="flex justify-between text-sm text-stone-300">
                <span className="font-mono">#{o.id.slice(-6)}</span>
                <span>
                  Pickup{" "}
                  <strong className="text-white">
                    {o.pickup_at ? new Date(o.pickup_at).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" }) : "ASAP"}
                  </strong>
                </span>
              </div>
              <ul className="my-4 space-y-2">
                {o.lines.map((l, i) => (
                  <li key={i}>
                    <span className="text-lg font-bold">
                      <span className="text-terracotta-300 font-mono">{l.qty}×</span> {l.name}
                    </span>
                    {l.modifiers.map((m) => (
                      <div key={m} className="pl-6 text-sm text-amber-200">+ {m}</div>
                    ))}
                  </li>
                ))}
              </ul>
              {o.state === "CAPTURED" ? (
                <button type="button" disabled={acting === o.id} onClick={() => act(o.id, "kitchen_accept")}
                  className="w-full rounded-xl bg-terracotta-700 py-3 font-bold flex items-center justify-center gap-2 disabled:opacity-60">
                  <Flame className="w-4 h-4" aria-hidden /> Start cooking
                </button>
              ) : (
                <button type="button" disabled={acting === o.id} onClick={() => act(o.id, "mark_ready")}
                  className="w-full rounded-xl bg-emerald-700 py-3 font-bold flex items-center justify-center gap-2 disabled:opacity-60">
                  <Check className="w-4 h-4" aria-hidden /> Mark ready
                </button>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export default function KitchenPage() {
  return (
    <RequireMerchant>
      <Kitchen />
    </RequireMerchant>
  );
}
