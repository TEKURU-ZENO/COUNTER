"use client";

import { Upload } from "lucide-react";
import { useState } from "react";
import { RequireMerchant } from "@/components/RequireMerchant";
import { ErrorState, Loading } from "@/components/States";
import { ApiError } from "@/lib/api";

interface DraftItem {
  name: string;
  category: string;
  price_cents: number | null;
  price_as_printed: string;
  flags: string[];
}

const FLAG_TEXT: Record<string, string> = {
  price_unreadable: "price unreadable",
  price_out_of_range: "price looks wrong",
  low_confidence_name: "check name",
  low_confidence_price: "check price",
  low_confidence_category: "check section",
  possible_duplicate: "possible duplicate",
};

function Onboarding() {
  const [draftId, setDraftId] = useState<string | null>(null);
  const [items, setItems] = useState<DraftItem[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [published, setPublished] = useState<number | null>(null);
  const [confirmed, setConfirmed] = useState(false);

  async function upload(file: File) {
    setBusy(true);
    setError(null);
    setPublished(null);
    const form = new FormData();
    form.append("file", file);
    try {
      const res = await fetch("/api/v1/merchant/menu/extract", { method: "POST", body: form, credentials: "same-origin" });
      const body = await res.json();
      if (!res.ok) throw new ApiError(res.status, body.detail ?? res.statusText);
      setDraftId(body.draft_id);
      setItems(body.items);
    } catch (e) {
      setError((e as ApiError).message ?? "Upload failed");
    } finally {
      setBusy(false);
    }
  }

  async function publish() {
    if (!draftId) return;
    setBusy(true);
    setError(null);
    try {
      const res = await fetch(`/api/v1/merchant/menu/drafts/${draftId}/publish`, {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          confirmed: true,
          items: items.map(({ name, category, price_cents }) => ({ name, category, price_cents })),
        }),
      });
      const body = await res.json();
      if (!res.ok) throw new ApiError(res.status, typeof body.detail === "string" ? body.detail : "Some items are invalid");
      setPublished(body.published);
    } catch (e) {
      setError((e as ApiError).message);
    } finally {
      setBusy(false);
    }
  }

  const update = (i: number, patch: Partial<DraftItem>) =>
    setItems((xs) => xs.map((x, j) => (j === i ? { ...x, ...patch, flags: [] } : x)));
  const invalid = items.some((i) => i.price_cents === null || i.price_cents < 50 || i.price_cents > 20000 || !i.name);

  return (
    <div className="max-w-4xl mx-auto px-4 py-8 space-y-6">
      <h1 className="text-2xl font-serif font-bold">Menu from a photo</h1>
      <p className="text-sm text-stone-700">
        A vision model reads your menu. Flagged rows need a look. Nothing goes live until you confirm.
      </p>
      {error && <ErrorState title="Menu import problem" message={error} />}
      <label className="flex items-center gap-2 w-fit cursor-pointer rounded-lg border border-stone-300 bg-white px-4 py-2 font-medium">
        <Upload className="w-4 h-4" aria-hidden /> Choose a menu photo
        <input type="file" accept="image/jpeg,image/png,image/webp,image/gif" className="sr-only"
          onChange={(e) => e.target.files?.[0] && upload(e.target.files[0])} />
      </label>
      {busy && <Loading label="Reading your menu…" />}
      {published !== null && (
        <p role="status" className="rounded-lg bg-emerald-50 border border-emerald-300 p-3">
          Published {published} items. Callers can order them now.
        </p>
      )}
      {items.length > 0 && published === null && (
        <div className="bg-white rounded-xl border border-stone-200 overflow-x-auto">
          <table className="w-full text-sm">
            <caption className="sr-only">Extracted menu items</caption>
            <thead className="bg-stone-100 text-left">
              <tr><th className="p-2">Name</th><th className="p-2">Section</th><th className="p-2">Price ($)</th><th className="p-2">Check</th></tr>
            </thead>
            <tbody>
              {items.map((it, i) => (
                <tr key={i} className={it.flags.length ? "bg-amber-50" : ""}>
                  <td className="p-2">
                    <input aria-label={`Name of item ${i + 1}`} value={it.name} onChange={(e) => update(i, { name: e.target.value })}
                      className="w-full rounded border border-stone-300 px-2 py-1" />
                  </td>
                  <td className="p-2">
                    <input aria-label={`Section of item ${i + 1}`} value={it.category} onChange={(e) => update(i, { category: e.target.value })}
                      className="w-full rounded border border-stone-300 px-2 py-1" />
                  </td>
                  <td className="p-2">
                    <input aria-label={`Price of item ${i + 1} in dollars`} inputMode="decimal"
                      defaultValue={it.price_cents != null ? (it.price_cents / 100).toFixed(2) : ""}
                      onChange={(e) => {
                        const m = e.target.value.match(/^(\d+)(?:\.(\d{1,2}))?$/);
                        update(i, { price_cents: m ? Number(m[1]) * 100 + Number((m[2] ?? "").padEnd(2, "0")) : null });
                      }}
                      className="w-24 rounded border border-stone-300 px-2 py-1 tabular-nums" />
                  </td>
                  <td className="p-2 text-amber-900">{it.flags.map((f) => FLAG_TEXT[f] ?? f).join(", ")}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="p-4 border-t border-stone-200 flex flex-wrap items-center gap-4">
            <label className="flex items-center gap-2 text-sm">
              <input type="checkbox" checked={confirmed} onChange={(e) => setConfirmed(e.target.checked)} />
              I checked these names and prices
            </label>
            <button type="button" disabled={!confirmed || invalid || busy} onClick={publish}
              className="rounded-lg bg-terracotta-700 text-white px-4 py-2 font-semibold disabled:opacity-50">
              Publish menu
            </button>
            {invalid && <span className="text-sm text-red-800">Every item needs a name and a price between $0.50 and $200.</span>}
          </div>
        </div>
      )}
    </div>
  );
}

export default function OnboardingPage() {
  return (
    <RequireMerchant>
      <Onboarding />
    </RequireMerchant>
  );
}
