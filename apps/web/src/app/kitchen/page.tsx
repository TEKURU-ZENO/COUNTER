"use client";

import { Check, Flame } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { RequireMerchant } from "@/components/RequireMerchant";
import { Empty, ErrorState, LiveBadge, Loading } from "@/components/States";
import { Ticket } from "@/components/Ticket";
import { api, ApiError, type Order } from "@/lib/api";
import { useEventStream } from "@/lib/useEventStream";

// Only orders PayPal has captured. PAYMENT_PENDING never reaches this screen.
const KITCHEN_STATES = "CAPTURED,IN_KITCHEN";

const time = (iso: string | null | undefined) =>
  iso ? new Date(iso).toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" }) : "ASAP";
const ago = (iso?: string) => (iso ? Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 60000)) : 0);

function Kitchen() {
  const [orders, setOrders] = useState<Order[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [acting, setActing] = useState<string | null>(null);
  const [, tick] = useState(0);

  const load = useCallback(() => {
    api<Order[]>(`/api/v1/merchant/orders?state=${KITCHEN_STATES}`)
      .then((o) => {
        setOrders(o.sort((a, b) => (a.created_at ?? "").localeCompare(b.created_at ?? "")));
        setError(null);
      })
      .catch((e: ApiError) => setError(e.message));
  }, []);

  useEffect(load, [load]);
  useEffect(() => {
    const id = setInterval(() => tick((n) => n + 1), 30_000);
    return () => clearInterval(id);
  }, []);
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

  const waiting = orders?.filter((o) => o.state === "CAPTURED").length ?? 0;
  const cooking = orders?.filter((o) => o.state === "IN_KITCHEN").length ?? 0;

  return (
    <div className="min-h-[calc(100vh-4rem)] bg-steel-950 text-white">
      <div className="max-w-[1440px] mx-auto px-4 sm:px-6 py-8">
        <div className="flex flex-wrap items-end justify-between gap-4">
          <div>
            <h1 className="text-[clamp(30px,3.6vw,48px)] font-extrabold tracking-[-0.04em] leading-none">The pass</h1>
            <p className="mt-2 text-[16px] text-steel-200">
              {orders === null ? "Loading tickets" : `${waiting} to start · ${cooking} cooking`} · paid orders only
            </p>
          </div>
          <LiveBadge status={status} dark />
        </div>

        {error && (
          <div className="mt-6">
            <ErrorState title="The kitchen screen lost its data" message={error} />
          </div>
        )}

        <div className="rail rail-dark mt-8" aria-hidden />
        {orders === null ? (
          <Loading label="Hanging tickets…" dark />
        ) : orders.length === 0 ? (
          <Empty dark title="Rail's clear" message="A ticket drops here the moment PayPal confirms a payment." />
        ) : (
          <ul className="columns-1 sm:columns-2 xl:columns-4 gap-6 pt-1">
            {orders.map((o) => {
              const cookingNow = o.state === "IN_KITCHEN";
              return (
                <li key={o.id} className="break-inside-avoid mb-6 px-1 pt-3">
                  <Ticket
                    large
                    number={`#${o.id.slice(-4).toUpperCase()}`}
                    state={o.state}
                    lines={o.lines}
                    meta={
                      <span className="font-mono tabular">
                        Pickup {time(o.pickup_at)} · placed {ago(o.created_at)} min ago
                      </span>
                    }
                  >
                    <button
                      type="button"
                      disabled={acting === o.id}
                      onClick={() => act(o.id, cookingNow ? "mark_ready" : "kitchen_accept")}
                      className={`btn-press mt-5 w-full min-h-[56px] rounded-full font-bold text-[17px] flex items-center justify-center gap-2 disabled:opacity-60 ${
                        cookingNow ? "bg-paid text-white" : "bg-ink text-white"
                      }`}
                    >
                      {cookingNow ? <Check className="w-5 h-5" aria-hidden /> : <Flame className="w-5 h-5" aria-hidden />}
                      {cookingNow ? "Ready for pickup" : "Start cooking"}
                    </button>
                  </Ticket>
                </li>
              );
            })}
          </ul>
        )}
      </div>
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
