"use client";

import { useParams, useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useState } from "react";
import { ErrorState, LiveBadge, Loading, PayPalButton } from "@/components/States";
import { Ticket } from "@/components/Ticket";
import { api, ApiError, type Order } from "@/lib/api";
import { MENU_MEDIA, OVEN } from "@/lib/menuMedia";
import { useEventStream } from "@/lib/useEventStream";

const MESSAGES: Record<string, { title: string; body: string }> = {
  LINK_SENT: { title: "Waiting for your payment", body: "Finish paying on PayPal. This page updates on its own." },
  APPROVED: { title: "Confirming your payment", body: "PayPal approved it. We're collecting the payment now." },
  PAYMENT_PENDING: { title: "PayPal is still processing", body: "We start cooking the moment PayPal confirms. Nothing to do on your side." },
  CAPTURED: { title: "Paid. The kitchen has it.", body: "Your ticket is on the rail and will be started shortly." },
  IN_KITCHEN: { title: "It's in the oven", body: "We'll update this page when it's ready to collect." },
  READY: { title: "Ready to collect", body: "Come to the counter and give your ticket number." },
  PICKED_UP: { title: "Enjoy", body: "This order has been collected." },
  LINK_EXPIRED: { title: "This payment link expired", body: "You weren't charged. Call again to place the order." },
  PAYMENT_DENIED: { title: "PayPal declined the payment", body: "You weren't charged. Call again to pay another way." },
  CANCELLED: { title: "Order cancelled", body: "You weren't charged." },
  NEEDS_REVIEW: { title: "We're checking your payment", body: "Something didn't match on our side, so the restaurant will contact you before cooking." },
  REFUNDED_FULL: { title: "Refunded", body: "The full amount went back to your PayPal account." },
  REFUNDED_PARTIAL: { title: "Partly refunded", body: "A partial refund went back to your PayPal account." },
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
        message={error.status === 403 ? "Use the newest link from your text or your PayPal receipt." : error.message}
        action={
          <button type="button" className="underline font-semibold min-h-[44px]" onClick={load}>
            Try again
          </button>
        }
      />
    );
  }
  if (!order) return <Loading label="Finding your ticket…" />;

  const msg = MESSAGES[order.state] ?? { title: order.state, body: "" };
  const media = order.lines.map((l) => (l.item_id ? MENU_MEDIA[l.item_id] : undefined)).find(Boolean) ?? OVEN;

  return (
    <div className="grid gap-10 md:grid-cols-[minmax(0,1fr)_380px] items-start">
      <div>
        <div className="pin-photo">
          <img src={media.src} srcSet={media.srcSet} sizes="(max-width: 768px) 100vw, 640px" width={media.width} height={media.height} alt={media.alt} className="block w-full h-auto" />
        </div>
        {params.get("cancelled") && order.state === "LINK_SENT" && (
          <p role="status" className="mt-6 rounded-2xl bg-pending-soft p-4 text-[15px] text-ink-soft">
            You left PayPal without paying. The link still works until it expires.
          </p>
        )}
        <div aria-live="polite" className="mt-6">
          <h1 className="text-[clamp(32px,4vw,52px)] font-extrabold leading-[1] tracking-[-0.04em] text-balance">{msg.title}</h1>
          <p className="mt-3 text-[17px] text-ink-soft max-w-[52ch]">{msg.body}</p>
        </div>
      </div>
      <div>
        <div className="rail mx-2" aria-hidden />
        <div className="px-5 -mt-1">
          <Ticket
            number={`#${order.id.slice(-4).toUpperCase()}`}
            state={order.state}
            lines={order.lines}
            subtotal={order.subtotal_cents}
            tax={order.tax_cents}
            total={order.total_cents}
            currency={order.currency}
            meta={
              <span className="block">
                <span className="block">{order.shop_name}</span>
                {order.pickup_at && (
                  <span className="block font-mono tabular">
                    Pickup {new Date(order.pickup_at).toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" })}
                  </span>
                )}
              </span>
            }
          >
            {order.state === "LINK_SENT" && order.approval_url && (
              <div className="mt-4">
                <PayPalButton href={order.approval_url} />
              </div>
            )}
          </Ticket>
        </div>
        <div className="mt-4 px-5">
          <LiveBadge status={stream} />
        </div>
      </div>
    </div>
  );
}

export default function OrderStatusPage() {
  return (
    <div className="max-w-[1200px] mx-auto px-4 sm:px-6 py-10">
      <Suspense fallback={<Loading />}>
        <Status />
      </Suspense>
    </div>
  );
}
