"use client";

import { useParams, useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useState } from "react";
import { ErrorState, LiveBadge, Loading, StateBadge } from "@/components/States";
import { api, ApiError, money, type Order, STATE_LABELS } from "@/lib/api";
import { useEventStream } from "@/lib/useEventStream";

const MESSAGES: Record<string, { title: string; body: string }> = {
  LINK_SENT: { title: "Waiting for your payment", body: "Finish paying on PayPal. This page updates by itself." },
  APPROVED: { title: "Confirming your payment", body: "PayPal approved it; we're capturing the payment now." },
  PAYMENT_PENDING: { title: "PayPal is still processing your payment", body: "We'll start cooking once PayPal confirms. No action needed." },
  CAPTURED: { title: "Paid — thank you!", body: "The kitchen has your order." },
  IN_KITCHEN: { title: "Your order is being made", body: "We'll update this page when it's ready." },
  READY: { title: "Ready for pickup", body: "Come to the counter and give your order number." },
  PICKED_UP: { title: "Enjoy your meal", body: "This order has been picked up." },
  LINK_EXPIRED: { title: "This payment link expired", body: "Call the restaurant again to place a new order. You were not charged." },
  PAYMENT_DENIED: { title: "Payment was declined", body: "You were not charged. Call the restaurant to try another way to pay." },
  CANCELLED: { title: "Order cancelled", body: "You were not charged." },
  NEEDS_REVIEW: { title: "We're checking your payment", body: "Something didn't match on our side. The restaurant will contact you." },
  REFUNDED_FULL: { title: "Refunded", body: "Your refund was sent to your PayPal account." },
  REFUNDED_PARTIAL: { title: "Partly refunded", body: "A partial refund was sent to your PayPal account." },
  REVERSED: { title: "Payment reversed", body: "Please contact the restaurant." },
};

interface StatusOrder extends Order {
  shop_name: string;
}

function Status() {
  const { orderId } = useParams<{ orderId: string }>();
  const params = useSearchParams();
  const token = params.get("t") ?? "";
  const [order, setOrder] = useState<StatusOrder | null>(null);
  const [error, setError] = useState<{ status: number; message: string } | null>(null);

  const load = useCallback(() => {
    api<StatusOrder>(`/api/v1/orders/${orderId}/status?t=${encodeURIComponent(token)}`)
      .then((o) => {
        setOrder(o);
        setError(null);
      })
      .catch((e: ApiError) => setError({ status: e.status, message: e.message }));
  }, [orderId, token]);

  useEffect(load, [load]);
  const stream = useEventStream(token && !error ? `/api/v1/orders/${orderId}/stream?t=${encodeURIComponent(token)}` : null, load);

  if (error) {
    return (
      <ErrorState
        title={error.status === 403 ? "This link is invalid or has expired" : "We couldn't load your order"}
        message={error.status === 403 ? "Use the newest link from your text message or PayPal receipt." : error.message}
        action={<button type="button" className="underline" onClick={load}>Try again</button>}
      />
    );
  }
  if (!order) return <Loading label="Loading your order…" />;

  const msg = MESSAGES[order.state] ?? { title: order.state, body: "" };
  const label = STATE_LABELS[order.state] ?? { label: order.state, tone: "info" as const };
  return (
    <div className="space-y-6">
      {params.get("cancelled") && order.state === "LINK_SENT" && (
        <p role="status" className="rounded-lg bg-amber-50 border border-amber-300 p-3 text-sm">
          You left PayPal without paying. You can still use the payment link until it expires.
        </p>
      )}
      <div className="flex items-center justify-between">
        <StateBadge state={order.state} label={label.label} tone={label.tone} />
        <LiveBadge status={stream} />
      </div>
      <div aria-live="polite">
        <h1 className="text-3xl font-serif font-bold">{msg.title}</h1>
        <p className="text-stone-700 mt-1">{msg.body}</p>
      </div>
      <div className="bg-white rounded-2xl border border-stone-200 p-6 space-y-4">
        <div className="flex justify-between text-sm">
          <span className="text-stone-700">{order.shop_name}</span>
          <span className="font-mono">#{order.id.slice(-6)}</span>
        </div>
        {order.pickup_at && (
          <p>
            Pickup at{" "}
            <strong>{new Date(order.pickup_at).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}</strong>
          </p>
        )}
        <ul className="text-sm space-y-1">
          {order.lines.map((l, i) => (
            <li key={i} className="flex justify-between">
              <span>
                {l.qty}× {l.name}
                {l.modifiers.length > 0 && <span className="text-stone-600"> ({l.modifiers.join(", ")})</span>}
              </span>
              <span className="tabular-nums">{money(l.line_total_cents, order.currency)}</span>
            </li>
          ))}
        </ul>
        <dl className="border-t border-stone-200 pt-2 text-sm tabular-nums space-y-1">
          <div className="flex justify-between"><dt>Tax</dt><dd>{money(order.tax_cents, order.currency)}</dd></div>
          <div className="flex justify-between font-semibold"><dt>Total</dt><dd>{money(order.total_cents, order.currency)}</dd></div>
        </dl>
        {order.state === "LINK_SENT" && order.approval_url && (
          <a href={order.approval_url} className="block text-center rounded-lg bg-[#0070ba] text-white py-2 font-semibold">
            Pay with PayPal
          </a>
        )}
      </div>
    </div>
  );
}

export default function OrderStatusPage() {
  return (
    <div className="max-w-xl mx-auto px-4 py-10">
      <Suspense fallback={<Loading />}>
        <Status />
      </Suspense>
    </div>
  );
}
