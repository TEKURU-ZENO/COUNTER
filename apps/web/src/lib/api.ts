// All requests go to the same origin; next.config.js proxies /api to the FastAPI service,
// so the merchant session cookie stays first-party and CORS never needs to be opened up.

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  let res: Response;
  try {
    res = await fetch(path, {
      ...init,
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", ...(init.headers || {}) },
    });
  } catch {
    throw new ApiError(0, "Can't reach the server. Check your connection and try again.");
  }
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail ?? body);
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(res.status, detail);
  }
  return (await res.json()) as T;
}

export const money = (cents: number | null | undefined, currency = "USD") =>
  cents == null
    ? "–"
    : new Intl.NumberFormat("en-US", { style: "currency", currency }).format(cents / 100);

export const STATE_LABELS: Record<string, { label: string; tone: "ok" | "wait" | "bad" | "info" }> = {
  DRAFT: { label: "Building order", tone: "info" },
  QUOTED: { label: "Quoted", tone: "info" },
  LINK_SENT: { label: "Waiting for payment", tone: "wait" },
  APPROVED: { label: "Approved, capturing", tone: "wait" },
  PAYMENT_PENDING: { label: "Payment pending at PayPal", tone: "wait" },
  CAPTURED: { label: "Paid", tone: "ok" },
  IN_KITCHEN: { label: "In the kitchen", tone: "ok" },
  READY: { label: "Ready for pickup", tone: "ok" },
  PICKED_UP: { label: "Picked up", tone: "ok" },
  CANCELLED: { label: "Cancelled", tone: "bad" },
  LINK_EXPIRED: { label: "Payment link expired", tone: "bad" },
  PAYMENT_DENIED: { label: "Payment declined", tone: "bad" },
  REVERSED: { label: "Payment reversed", tone: "bad" },
  NEEDS_REVIEW: { label: "Needs review", tone: "bad" },
  REFUNDED_FULL: { label: "Refunded", tone: "info" },
  REFUNDED_PARTIAL: { label: "Partly refunded", tone: "info" },
  NO_SHOW: { label: "No-show", tone: "bad" },
  CONFIRMED_CATERING: { label: "Catering confirmed", tone: "ok" },
};

export interface OrderLine {
  item_id?: string;
  name: string;
  qty: number;
  line_total_cents: number;
  modifiers: string[];
}

export interface Order {
  id: string;
  state: string;
  currency: string;
  subtotal_cents: number;
  tax_cents: number;
  total_cents: number;
  pickup_at: string | null;
  lines: OrderLine[];
  approval_url?: string | null;
  review_reason?: string | null;
  created_at?: string;
  updated_at?: string;
  call_id?: string | null;
}
