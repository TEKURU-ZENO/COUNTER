"use client";

import { usePathname } from "next/navigation";
import { PHOTO_CREDITS } from "@/lib/menuMedia";

// The kitchen screen shows no photographs and is a dark working surface, so it carries no credits strip.
export function Footer() {
  const pathname = usePathname();
  if (pathname === "/kitchen") return null;
  return (
    <footer className="border-t border-board-line px-4 sm:px-6 py-4 text-xs text-ink-muted">
      <p className="max-w-[1440px] mx-auto">
        Demo restaurant, PayPal sandbox. Photographs:{" "}
        {PHOTO_CREDITS.map((c, i) => (
          <span key={c.page}>
            <a href={c.page} className="underline hover:text-ink">
              {c.by}
            </a>{" "}
            ({c.licence}){i < PHOTO_CREDITS.length - 1 ? ", " : "."}
          </span>
        ))}
      </p>
    </footer>
  );
}
