"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { ChefHat, FileText, LayoutDashboard, PhoneCall } from "lucide-react";

const NAV = [
  { href: "/", label: "Try a call", icon: PhoneCall },
  { href: "/console", label: "Console", icon: LayoutDashboard },
  { href: "/kitchen", label: "Kitchen", icon: ChefHat },
  { href: "/onboarding", label: "Menu", icon: FileText },
];

export function Navbar() {
  const pathname = usePathname();
  return (
    <header className="border-b border-stone-200 bg-white">
      <a href="#main" className="sr-only focus:not-sr-only focus:absolute focus:p-2 focus:bg-white">
        Skip to content
      </a>
      <div className="max-w-7xl mx-auto px-4 h-14 flex items-center justify-between gap-4">
        <Link href="/" className="flex items-center gap-2 font-serif font-bold text-stone-900">
          <span className="w-8 h-8 rounded-lg bg-terracotta-700 text-white grid place-items-center" aria-hidden>
            C
          </span>
          COUNTER
        </Link>
        <nav aria-label="Main">
          <ul className="flex items-center gap-1">
            {NAV.map(({ href, label, icon: Icon }) => {
              const active = pathname === href;
              return (
                <li key={href}>
                  <Link
                    href={href}
                    aria-current={active ? "page" : undefined}
                    className={`flex items-center gap-1.5 px-3 py-2 rounded-lg text-sm font-medium focus-visible:outline focus-visible:outline-2 focus-visible:outline-terracotta-700 ${
                      active ? "bg-terracotta-50 text-terracotta-800" : "text-stone-700 hover:bg-stone-100"
                    }`}
                  >
                    <Icon className="w-4 h-4" aria-hidden />
                    <span className="hidden sm:inline">{label}</span>
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
