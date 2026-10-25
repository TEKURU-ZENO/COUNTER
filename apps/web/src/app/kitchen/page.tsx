"use client";

import React, { useState } from "react";
import { ChefHat, Check, Clock, Flame, Sparkles } from "lucide-react";

interface KitchenTicket {
  orderId: string;
  state: "CAPTURED" | "IN_KITCHEN";
  pickupTime: string;
  elapsedMin: number;
  items: {
    name: string;
    qty: number;
    modifiers: string[];
  }[];
}

export default function KitchenScreenPage() {
  const [tickets, setTickets] = useState<KitchenTicket[]>([
    {
      orderId: "ord_demo_101",
      state: "CAPTURED",
      pickupTime: "7:15 PM",
      elapsedMin: 3,
      items: [
        {
          name: "Margherita Wood-Fired Pizza",
          qty: 1,
          modifiers: ["Extra Buffalo Mozzarella", "Crispy Crust"],
        },
      ],
    },
    {
      orderId: "ord_demo_102",
      state: "IN_KITCHEN",
      pickupTime: "7:30 PM",
      elapsedMin: 9,
      items: [
        {
          name: "Spicy Pepperoni Pizza",
          qty: 1,
          modifiers: ["Hot Honey Drizzle"],
        },
        {
          name: "Rosemary Garlic Bread",
          qty: 1,
          modifiers: [],
        },
      ],
    },
  ]);

  const updateState = (orderId: string, nextState: "IN_KITCHEN" | "READY") => {
    if (nextState === "READY") {
      setTickets((prev) => prev.filter((t) => t.orderId !== orderId));
    } else {
      setTickets((prev) =>
        prev.map((t) => (t.orderId === orderId ? { ...t, state: nextState } : t))
      );
    }
  };

  return (
    <div className="min-h-[calc(100vh-4rem)] bg-charcoal-950 text-white p-6 sm:p-8 space-y-6">
      {/* Kitchen Screen Header */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between border-b border-stone-800 pb-5 gap-4">
        <div className="flex items-center space-x-3">
          <div className="w-12 h-12 rounded-xl bg-terracotta-600 flex items-center justify-center text-white shadow-lg">
            <ChefHat className="w-7 h-7" />
          </div>
          <div>
            <h1 className="text-2xl sm:text-3xl font-serif font-black tracking-tight flex items-center space-x-2">
              <span>Kitchen Display System</span>
              <span className="text-xs font-mono font-normal px-2.5 py-0.5 rounded-full bg-emerald-950 text-emerald-400 border border-emerald-800">
                PAID ORDERS ONLY
              </span>
            </h1>
            <p className="text-stone-400 text-xs">
              Every ticket below is guaranteed 100% pre-paid via PayPal before kitchen receipt.
            </p>
          </div>
        </div>

        <div className="flex items-center space-x-3 text-xs font-mono">
          <div className="px-3 py-1.5 rounded-lg bg-stone-900 border border-stone-800 text-stone-300">
            Active Tickets: <span className="font-bold text-white">{tickets.length}</span>
          </div>
        </div>
      </div>

      {/* Ticket Grid */}
      {tickets.length === 0 ? (
        <div className="h-96 flex flex-col items-center justify-center text-center space-y-3 text-stone-500">
          <Sparkles className="w-10 h-10 text-stone-600" />
          <h3 className="font-serif font-bold text-lg text-stone-300">All Kitchen Orders Clear!</h3>
          <p className="text-xs max-w-sm">
            Waiting for new customer orders. When a telephone customer pays via PayPal, their ticket will instantly appear here.
          </p>
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
          {tickets.map((ticket) => (
            <div
              key={ticket.orderId}
              className={`rounded-2xl border p-6 flex flex-col justify-between shadow-xl transition-all ${
                ticket.state === "IN_KITCHEN"
                  ? "bg-stone-900/90 border-blue-500/50"
                  : "bg-stone-900/90 border-terracotta-500/50"
              }`}
            >
              <div className="space-y-4">
                {/* Card Header */}
                <div className="flex items-center justify-between border-b border-stone-800 pb-3">
                  <div>
                    <span className="text-xs font-mono font-bold text-stone-400 uppercase">
                      #{ticket.orderId.slice(-6)}
                    </span>
                    <div className="text-xs text-stone-500 flex items-center space-x-1 mt-0.5">
                      <Clock className="w-3 h-3" />
                      <span>{ticket.elapsedMin}m ago</span>
                    </div>
                  </div>
                  <div className="text-right">
                    <span className="text-xs font-semibold text-terracotta-400">Pickup</span>
                    <div className="font-bold text-lg text-white">{ticket.pickupTime}</div>
                  </div>
                </div>

                {/* Items & Modifiers (Large typography for cooks) */}
                <div className="space-y-3 py-2">
                  {ticket.items.map((item, idx) => (
                    <div key={idx} className="space-y-1">
                      <div className="text-lg font-bold text-white flex items-center space-x-2">
                        <span className="text-terracotta-400 font-mono text-xl">{item.qty}x</span>
                        <span>{item.name}</span>
                      </div>
                      {item.modifiers.length > 0 && (
                        <div className="pl-6 space-y-0.5">
                          {item.modifiers.map((mod, mIdx) => (
                            <div key={mIdx} className="text-xs text-amber-300 font-medium">
                              + {mod}
                            </div>
                          ))}
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              </div>

              {/* Action Buttons */}
              <div className="pt-6 border-t border-stone-800 mt-4">
                {ticket.state === "CAPTURED" ? (
                  <button
                    onClick={() => updateState(ticket.orderId, "IN_KITCHEN")}
                    className="w-full py-3 px-4 bg-terracotta-600 hover:bg-terracotta-700 text-white font-bold text-sm rounded-xl flex items-center justify-center space-x-2 shadow-lg transition"
                  >
                    <Flame className="w-4 h-4" />
                    <span>Accept & Put in Oven</span>
                  </button>
                ) : (
                  <button
                    onClick={() => updateState(ticket.orderId, "READY")}
                    className="w-full py-3 px-4 bg-emerald-600 hover:bg-emerald-700 text-white font-bold text-sm rounded-xl flex items-center justify-center space-x-2 shadow-lg transition"
                  >
                    <Check className="w-4 h-4" />
                    <span>Mark Ready (Notify Customer)</span>
                  </button>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
