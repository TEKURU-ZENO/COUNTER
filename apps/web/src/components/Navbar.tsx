"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { ChefHat, PhoneCall, LayoutDashboard, MonitorPlay, FileText, CheckCircle2 } from "lucide-react";

export function Navbar() {
  const pathname = usePathname();

  const navItems = [
    { href: "/", label: "Live Demo", icon: PhoneCall },
    { href: "/console", label: "Merchant Console", icon: LayoutDashboard },
    { href: "/kitchen", label: "Kitchen Display", icon: ChefHat },
    { href: "/order-status", label: "Customer Status", icon: MonitorPlay },
    { href: "/onboarding", label: "Menu Onboarding", icon: FileText },
  ];

  return (
    <header className="border-b border-stone-200 bg-white/95 backdrop-blur sticky top-0 z-50">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 h-16 flex items-center justify-between">
        <div className="flex items-center space-x-3">
          <div className="w-10 h-10 rounded-xl bg-terracotta-600 flex items-center justify-center text-white shadow-md shadow-terracotta-500/20">
            <span className="font-serif font-black text-xl">C</span>
          </div>
          <div>
            <div className="flex items-center space-x-2">
              <span className="font-serif font-bold text-lg text-stone-900 tracking-tight">COUNTER</span>
              <span className="text-xs px-2 py-0.5 rounded-full font-medium bg-emerald-100 text-emerald-800 border border-emerald-200">
                PayPal Verified
              </span>
            </div>
            <p className="text-xs text-stone-500">Luigi's Artisan Pizza & Trattoria</p>
          </div>
        </div>

        <nav className="flex items-center space-x-1 sm:space-x-2">
          {navItems.map((item) => {
            const Icon = item.icon;
            const active = pathname === item.href;
            return (
              <Link
                key={item.href}
                href={item.href}
                className={`flex items-center space-x-1.5 px-3 py-2 rounded-lg text-xs sm:text-sm font-medium transition-colors ${
                  active
                    ? "bg-terracotta-50 text-terracotta-700 border border-terracotta-200"
                    : "text-stone-600 hover:text-stone-900 hover:bg-stone-100"
                }`}
              >
                <Icon className={`w-4 h-4 ${active ? "text-terracotta-600" : "text-stone-500"}`} />
                <span>{item.label}</span>
              </Link>
            );
          })}
        </nav>

        <div className="hidden lg:flex items-center space-x-2">
          <div className="flex items-center space-x-1.5 text-xs text-emerald-700 font-mono bg-emerald-50 px-2.5 py-1.5 rounded-md border border-emerald-200">
            <span className="w-2 h-2 rounded-full bg-emerald-500 animate-pulse"></span>
            <span>Deterministic Guard: ACTIVE</span>
          </div>
        </div>
      </div>
    </header>
  );
}
