"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const NAV = [
  { href: "/", label: "Order" },
  { href: "/kitchen", label: "Kitchen" },
  { href: "/console", label: "Console" },
  { href: "/onboarding", label: "Menu" },
];

export function Navbar() {
  const pathname = usePathname();
  const dark = pathname === "/kitchen";
  return (
    <header className={dark ? "bg-steel-950 text-white" : "bg-board text-ink border-b border-board-line"}>
      <a
        href="#main"
        className="sr-only focus:not-sr-only focus:absolute focus:z-50 focus:m-2 focus:rounded-full focus:bg-ink focus:px-4 focus:py-2 focus:text-white"
      >
        Skip to content
      </a>
      <div className="max-w-[1440px] mx-auto px-4 sm:px-6 h-16 flex items-center justify-between gap-4">
        <Link href="/" className="flex items-center gap-2.5 font-extrabold tracking-[-0.03em] text-[19px]">
          <span
            aria-hidden
            className={`grid place-items-center w-8 h-8 rounded-full ${dark ? "bg-carbon text-carbon-ink" : "bg-ink text-white"}`}
          >
            <svg viewBox="0 0 20 20" className="w-4 h-4" fill="none" stroke="currentColor" strokeWidth="2.2">
              <path d="M3 6h14M6 6v9a1 1 0 0 0 1 1h6a1 1 0 0 0 1-1V6" strokeLinecap="round" />
            </svg>
          </span>
          <span className="hidden sm:inline">COUNTER</span>
          <span className="sr-only sm:hidden">COUNTER</span>
        </Link>
        <nav aria-label="Main">
          <ul className="flex items-center gap-1">
            {NAV.map(({ href, label }) => {
              const active = pathname === href || (href !== "/" && pathname.startsWith(href));
              return (
                <li key={href}>
                  <Link
                    href={href}
                    aria-current={active ? "page" : undefined}
                    className={`inline-flex items-center min-h-[44px] px-3 sm:px-4 rounded-full text-[14px] sm:text-[15px] font-semibold transition-colors ${
                      active
                        ? dark
                          ? "bg-white text-ink"
                          : "bg-ink text-white"
                        : dark
                          ? "text-steel-200 hover:bg-steel-800"
                          : "text-ink-soft hover:bg-board-sunk"
                    }`}
                  >
                    {label}
                  </Link>
                </li>
              );
            })}
          </ul>
        </nav>
      </div>
    </header>
  );
}
