"use client";

import React from "react";
import { CheckCircle2, Clock, MapPin, Receipt, ShieldCheck } from "lucide-react";

export default function OrderStatusPage() {
  return (
    <div className="max-w-3xl mx-auto px-4 sm:px-6 py-12 space-y-8">
      {/* Status Header */}
      <div className="text-center space-y-3">
        <div className="inline-flex items-center space-x-2 px-3 py-1 rounded-full bg-emerald-100 text-emerald-800 text-xs font-semibold uppercase">
          <CheckCircle2 className="w-4 h-4" />
          <span>Payment Verified via PayPal</span>
        </div>
        <h1 className="text-3xl font-serif font-black text-stone-900 tracking-tight">
          Your Order is Being Prepared!
        </h1>
        <p className="text-stone-600 text-sm">
          We’ve received your order and payment. Our kitchen is baking your food fresh right now.
        </p>
      </div>

      {/* Pickup Card */}
      <div className="bg-white rounded-2xl border border-stone-200 p-8 shadow-sm space-y-6">
        <div className="flex flex-col sm:flex-row sm:items-center justify-between pb-6 border-b border-stone-100 gap-4">
          <div>
            <span className="text-xs font-bold text-stone-400 uppercase tracking-wider">Estimated Ready Time</span>
            <div className="text-3xl font-serif font-black text-stone-900 mt-0.5">7:15 PM</div>
          </div>
          <div className="text-sm font-mono px-3 py-1.5 bg-stone-100 rounded-lg text-stone-700 w-fit">
            Order #101-MARG
          </div>
        </div>

        {/* Progress Timeline */}
        <div className="space-y-4">
          <div className="flex items-center space-x-3 text-sm">
            <div className="w-8 h-8 rounded-full bg-emerald-500 text-white flex items-center justify-center font-bold">✓</div>
            <div>
              <div className="font-bold text-stone-900">Order Placed & Paid</div>
              <div className="text-xs text-stone-500">Paid via PayPal Sandbox • $18.40</div>
            </div>
          </div>
          <div className="flex items-center space-x-3 text-sm">
            <div className="w-8 h-8 rounded-full bg-terracotta-500 text-white flex items-center justify-center font-bold animate-pulse">●</div>
            <div>
              <div className="font-bold text-stone-900">In Wood-Fired Oven</div>
              <div className="text-xs text-stone-500">Baking at 800°F (approx. 10 mins remaining)</div>
            </div>
          </div>
          <div className="flex items-center space-x-3 text-sm opacity-50">
            <div className="w-8 h-8 rounded-full bg-stone-200 text-stone-500 flex items-center justify-center font-bold">3</div>
            <div>
              <div className="font-bold text-stone-900">Ready for Pickup</div>
              <div className="text-xs text-stone-500">Head to the Counter pickup shelf</div>
            </div>
          </div>
        </div>

        {/* Receipt Box */}
        <div className="p-4 bg-stone-50 rounded-xl border border-stone-200 text-xs space-y-2">
          <div className="font-bold text-stone-700 flex items-center space-x-1.5">
            <Receipt className="w-4 h-4 text-stone-500" />
            <span>PayPal Payment Confirmation</span>
          </div>
          <div className="space-y-1 font-mono text-stone-600">
            <div className="flex justify-between">
              <span>1x Margherita Wood-Fired Pizza (Extra Mozzarella)</span>
              <span>$17.00</span>
            </div>
            <div className="flex justify-between text-stone-500">
              <span>Sales Tax (8.25%)</span>
              <span>$1.40</span>
            </div>
            <div className="flex justify-between font-bold text-stone-900 pt-1 border-t border-stone-200">
              <span>Total Paid</span>
              <span>$18.40</span>
            </div>
          </div>
        </div>

        {/* Pickup Location */}
        <div className="flex items-start space-x-3 text-xs text-stone-600">
          <MapPin className="w-4 h-4 text-terracotta-600 shrink-0 mt-0.5" />
          <div>
            <span className="font-bold text-stone-800">Luigi's Artisan Pizza: </span>
            <span>42 Bleecker Street, New York, NY 10012. Park out front and mention your name at the counter.</span>
          </div>
        </div>
      </div>
    </div>
  );
}
