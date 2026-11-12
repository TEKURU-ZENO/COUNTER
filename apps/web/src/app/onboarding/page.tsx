"use client";

import { ImageUp } from "lucide-react";
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
  price_unreadable: "Price unreadable",
  price_out_of_range: "Price looks wrong",
  low_confidence_name: "Check the name",
  low_confidence_price: "Check the price",
  low_confidence_category: "Check the section",
  possible_duplicate: "Possible duplicate",
};

const parseDollars = (v: string): number | null => {
  const m = v.trim().match(/^(\d+)(?:\.(\d{1,2}))?$/);
  return m ? Number(m[1]) * 100 + Number((m[2] ?? "").padEnd(2, "0")) : null;
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
      if (!res.ok) throw new ApiError(res.status, typeof body.detail === "string" ? body.detail : res.statusText);
      setDraftId(body.draft_id);
      setItems(body.items);
      setConfirmed(false);
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
        body: JSON.stringify({ confirmed: true, items: items.map(({ name, category, price_cents }) => ({ name, category, price_cents })) }),
      });
      const body = await res.json();
      if (!res.ok) throw new ApiError(res.status, typeof body.detail === "string" ? body.detail : "Some dishes are incomplete");
      setPublished(body.published);
    } catch (e) {
      setError((e as ApiError).message);
    } finally {
      setBusy(false);
    }
  }

  const update = (i: number, patch: Partial<DraftItem>) => setItems((xs) => xs.map((x, j) => (j === i ? { ...x, ...patch, flags: [] } : x)));
  const invalid = items.some((i) => i.price_cents === null || i.price_cents < 50 || i.price_cents > 20000 || !i.name.trim());
  const flagged = items.filter((i) => i.flags.length > 0).length;

  return (
    <div className="max-w-[1440px] mx-auto px-4 sm:px-6 py-8">
      <h1 className="text-[clamp(30px,3.6vw,48px)] font-extrabold tracking-[-0.04em] leading-none">Menu from a photo</h1>
      <p className="mt-3 max-w-[60ch] text-[16px] text-ink-soft">
        Photograph your printed menu. A vision model reads every dish and price; anything it&rsquo;s unsure of is flagged for you.
        Nothing goes live until you confirm.
      </p>

      <label className="btn-press mt-6 inline-flex items-center gap-2 min-h-[48px] cursor-pointer rounded-full bg-ink px-6 text-white font-bold text-[16px] focus-within:outline focus-within:outline-2 focus-within:outline-offset-2 focus-within:outline-ink">
        <ImageUp className="w-5 h-5" aria-hidden />
        {items.length ? "Read another photo" : "Choose a menu photo"}
        <input type="file" accept="image/jpeg,image/png,image/webp,image/gif" className="sr-only" onChange={(e) => e.target.files?.[0] && upload(e.target.files[0])} />
      </label>

      {items.length === 0 && !busy && published === null && (
        <figure className="mt-10" aria-labelledby="example-caption">
          <p id="example-caption" className="text-[15px] font-semibold text-ink-soft">
            Example of what you&rsquo;ll review (not your menu)
          </p>
          <ul className="mt-3 grid gap-4 sm:grid-cols-3 max-w-4xl" aria-hidden>
            {[
              { name: "Margherita", section: "Pizza", price: "14.50", flag: null },
              { name: "Garlic bread", section: "Sides", price: "6.50", flag: null },
              { name: "Tiramisu", section: "Dessert", price: "75.00", flag: "Price looks wrong" },
            ].map((ex) => (
              <li key={ex.name} className={`rounded-pin p-4 space-y-2 ${ex.flag ? "bg-pending-soft" : "bg-board-sunk"}`}>
                {ex.flag && <p className="text-[13px] font-semibold text-pending">{ex.flag}</p>}
                <p className="text-[20px] font-bold tracking-[-0.02em]">{ex.name}</p>
                <p className="flex justify-between text-[14px] text-ink-soft">
                  <span>{ex.section}</span>
                  <span className="font-mono tabular">${ex.price}</span>
                </p>
              </li>
            ))}
          </ul>
        </figure>
      )}

      <div className="mt-6 space-y-4">
        {error && <ErrorState title="The menu couldn't be read" message={error} />}
        {busy && <Loading label="Reading the menu…" />}
        {published !== null && (
          <p role="status" className="rounded-2xl bg-paid-soft p-4 text-[16px] text-paid font-semibold">
            {published} dishes are live. Callers can order them now.
          </p>
        )}
      </div>

      {items.length > 0 && published === null && (
        <>
          <p className="mt-6 text-[15px] text-ink-soft">
            {items.length} dishes found{flagged > 0 && <span className="text-pending font-semibold"> · {flagged} need a look</span>}
          </p>
          <ul className="board mt-4 columns-1 sm:columns-2 lg:columns-4">
            {items.map((it, i) => (
              <li key={i} className="pin">
                <div className={`rounded-pin p-4 space-y-3 ${it.flags.length ? "bg-pending-soft" : "bg-board-sunk"}`}>
                  {it.flags.length > 0 && <p className="text-[13px] font-semibold text-pending">{it.flags.map((f) => FLAG_TEXT[f] ?? f).join(" · ")}</p>}
                  <div>
                    <label htmlFor={`name-${i}`} className="block text-[12px] font-semibold text-ink-muted">
                      Dish
                    </label>
                    <input id={`name-${i}`} value={it.name} onChange={(e) => update(i, { name: e.target.value })}
                      className="w-full bg-transparent text-[20px] font-bold tracking-[-0.02em] border-b border-transparent focus:border-ink" />
                  </div>
                  <div className="flex gap-3">
                    <div className="flex-1">
                      <label htmlFor={`cat-${i}`} className="block text-[12px] font-semibold text-ink-muted">
                        Section
                      </label>
                      <input id={`cat-${i}`} value={it.category} onChange={(e) => update(i, { category: e.target.value })}
                        className="w-full min-h-[40px] rounded-full bg-white px-3 text-[14px]" />
                    </div>
                    <div className="w-28">
                      <label htmlFor={`price-${i}`} className="block text-[12px] font-semibold text-ink-muted">
                        Price ($)
                      </label>
                      <input id={`price-${i}`} inputMode="decimal"
                        defaultValue={it.price_cents != null ? (it.price_cents / 100).toFixed(2) : ""}
                        onChange={(e) => update(i, { price_cents: parseDollars(e.target.value) })}
                        className="w-full min-h-[40px] rounded-full bg-white px-3 font-mono tabular text-[14px]" />
                    </div>
                  </div>
                </div>
              </li>
            ))}
          </ul>
          <div className="sticky bottom-0 mt-6 -mx-4 sm:-mx-6 border-t border-board-line bg-white/95 px-4 sm:px-6 py-4 flex flex-wrap items-center gap-4">
            <label className="flex items-center gap-2 text-[15px] min-h-[44px]">
              <input type="checkbox" className="w-5 h-5 accent-ink" checked={confirmed} onChange={(e) => setConfirmed(e.target.checked)} />
              I&rsquo;ve checked every name and price
            </label>
            <button type="button" disabled={!confirmed || invalid || busy} onClick={publish}
              className="btn-press min-h-[48px] rounded-full bg-ink px-6 text-white font-bold disabled:opacity-40">
              Publish {items.length} dishes
            </button>
            {invalid && <span className="text-[14px] text-declined">Every dish needs a name and a price between $0.50 and $200.</span>}
          </div>
        </>
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
