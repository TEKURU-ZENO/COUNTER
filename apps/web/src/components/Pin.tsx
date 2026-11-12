"use client";

import { money } from "@/lib/api";
import { MENU_MEDIA } from "@/lib/menuMedia";

export interface MenuItem {
  id: string;
  name: string;
  category: string;
  price_cents: number;
  available: boolean;
  dietary_tags: string[];
  modifiers: { id: string; name: string; price_delta_cents: number }[];
}

// Pins without a photograph are set as type on a tinted ground, never an empty box.
const TYPE_GROUNDS = ["bg-carbon text-carbon-ink", "bg-steel-900 text-white", "bg-paid-soft text-paid", "bg-board-sunk text-ink"];

export function Pin({
  item,
  index,
  onPick,
  disabled,
  onTicket = 0,
}: {
  item: MenuItem;
  index: number;
  onPick?: (item: MenuItem) => void;
  disabled?: boolean;
  onTicket?: number;
}) {
  const media = MENU_MEDIA[item.id];
  const content = (
    <>
      <div className="pin-photo relative">
        {media ? (
          <img
            src={media.src}
            srcSet={media.srcSet}
            sizes="(max-width: 640px) 50vw, (max-width: 1280px) 33vw, 360px"
            width={media.width}
            height={media.height}
            alt={media.alt}
            loading={index < 4 ? "eager" : "lazy"}
            decoding="async"
            className="block w-full h-auto"
          />
        ) : (
          <div className={`aspect-[4/5] grid place-items-center p-6 ${TYPE_GROUNDS[index % TYPE_GROUNDS.length]}`}>
            <span className="text-[28px] leading-[1.05] font-extrabold tracking-[-0.03em] text-balance text-center">{item.name}</span>
          </div>
        )}
        {!item.available && (
          <span className="absolute left-3 top-3 rounded-full bg-white/95 px-3 py-1 text-[13px] font-semibold text-ink">Sold out</span>
        )}
        {onTicket > 0 && (
          <span className="absolute left-3 top-3 rounded-full bg-carbon px-3 py-1 text-[13px] font-bold text-carbon-ink shadow-pin">
            On your ticket <span className="font-mono tabular">×{onTicket}</span>
          </span>
        )}
      </div>
      <div className="px-1 pt-2.5 text-left">
        <div className="flex items-baseline justify-between gap-3">
          <h3 className="text-[16px] font-bold leading-snug tracking-[-0.01em]">{item.name}</h3>
          <span className="font-mono tabular text-[14px] shrink-0">{money(item.price_cents)}</span>
        </div>
        {item.modifiers.length > 0 && (
          <p className="mt-1 text-[13px] text-ink-muted leading-snug">
            {item.modifiers.map((m) => `${m.name} +${money(m.price_delta_cents)}`).join(" · ")}
          </p>
        )}
      </div>
    </>
  );
  return (
    <li className="pin">
      {onPick ? (
        <button
          type="button"
          disabled={disabled || !item.available}
          onClick={() => onPick(item)}
          aria-label={`Ask for ${item.name}, ${money(item.price_cents)}`}
          className="pin-button block w-full rounded-pin disabled:cursor-not-allowed"
        >
          {content}
        </button>
      ) : (
        content
      )}
    </li>
  );
}
