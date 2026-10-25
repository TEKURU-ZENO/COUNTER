"use client";

import React, { useState } from "react";
import { UploadCloud, Check, Edit2, AlertCircle, Sparkles, RefreshCw, Layers } from "lucide-react";

interface ExtractedItem {
  id: string;
  name: string;
  category: string;
  priceCents: number;
  confidence: number;
  modifiers: string[];
}

export default function OnboardingPage() {
  const [fileUploaded, setFileUploaded] = useState(false);
  const [isExtracting, setIsExtracting] = useState(false);
  const [items, setItems] = useState<ExtractedItem[]>([
    {
      id: "it_1",
      name: "Margherita Wood-Fired Pizza",
      category: "Pizza",
      priceCents: 1450,
      confidence: 0.98,
      modifiers: ["Extra Mozzarella (+$2.50)", "Kalamata Olives (+$1.50)"],
    },
    {
      id: "it_2",
      name: "Spicy Pepperoni Pizza",
      category: "Pizza",
      priceCents: 1650,
      confidence: 0.96,
      modifiers: ["Hot Honey (+$1.00)"],
    },
    {
      id: "it_3",
      name: "Rosemary Garlic Bread",
      category: "Sides",
      priceCents: 650,
      confidence: 0.82, // Low confidence flagged for review
      modifiers: [],
    },
    {
      id: "it_4",
      name: "House-made Tiramisu",
      category: "Dessert",
      priceCents: 750,
      confidence: 0.95,
      modifiers: [],
    },
  ]);

  const [published, setPublished] = useState(false);

  const simulateUpload = () => {
    setIsExtracting(true);
    setTimeout(() => {
      setIsExtracting(false);
      setFileUploaded(true);
    }, 1200);
  };

  const publishToCatalog = () => {
    setPublished(true);
  };

  return (
    <div className="max-w-5xl mx-auto px-4 sm:px-6 py-10 space-y-8">
      {/* Header */}
      <div>
        <div className="inline-flex items-center space-x-1.5 px-3 py-1 rounded-full bg-terracotta-100 text-terracotta-800 text-xs font-semibold uppercase mb-2">
          <Sparkles className="w-3.5 h-3.5" />
          <span>AI Vision Menu Extraction</span>
        </div>
        <h1 className="text-3xl font-serif font-black text-stone-900 tracking-tight">
          Restaurant Menu Onboarding & Catalog Sync
        </h1>
        <p className="text-stone-600 text-sm mt-1 max-w-2xl">
          Take a photo of your paper menu or upload a PDF. Our vision pipeline extracts items, prices,
          and modifiers into strict Pydantic schemas and mirrors them into PayPal Catalog Products.
        </p>
      </div>

      {/* Upload Box */}
      {!fileUploaded && (
        <div
          onClick={simulateUpload}
          className="border-2 border-dashed border-stone-300 hover:border-terracotta-500 rounded-2xl p-12 text-center cursor-pointer bg-white transition space-y-4"
        >
          {isExtracting ? (
            <div className="flex flex-col items-center space-y-3">
              <RefreshCw className="w-10 h-10 text-terracotta-600 animate-spin" />
              <p className="font-serif font-bold text-stone-800">Analyzing menu layout & price columns with vision model...</p>
            </div>
          ) : (
            <>
              <div className="w-16 h-16 rounded-full bg-terracotta-50 text-terracotta-600 mx-auto flex items-center justify-center">
                <UploadCloud className="w-8 h-8" />
              </div>
              <div>
                <h3 className="font-serif font-bold text-lg text-stone-900">Upload paper menu photo or PDF</h3>
                <p className="text-xs text-stone-500 mt-1">Supports PNG, JPG, or PDF up to 25MB</p>
              </div>
              <button className="px-5 py-2.5 rounded-xl bg-terracotta-600 text-white font-medium text-xs hover:bg-terracotta-700 shadow-sm">
                Choose File or Simulate Paper Menu Upload
              </button>
            </>
          )}
        </div>
      )}

      {/* Extraction Review Table */}
      {fileUploaded && (
        <div className="bg-white rounded-2xl border border-stone-200 p-6 shadow-sm space-y-6">
          <div className="flex items-center justify-between border-b border-stone-100 pb-4">
            <div>
              <h3 className="font-serif font-bold text-lg text-stone-900">Extracted Menu Items & Modifiers</h3>
              <p className="text-xs text-stone-500">
                Items with confidence below 90% are highlighted in yellow for owner confirmation.
              </p>
            </div>
            {published ? (
              <span className="px-3 py-1.5 rounded-xl bg-emerald-100 text-emerald-800 font-medium text-xs flex items-center space-x-1 border border-emerald-200">
                <Check className="w-4 h-4" />
                <span>Mirrored to PayPal Catalog</span>
              </span>
            ) : (
              <button
                onClick={publishToCatalog}
                className="px-5 py-2.5 bg-stone-900 hover:bg-stone-800 text-white font-medium text-xs rounded-xl shadow-md transition flex items-center space-x-2"
              >
                <Layers className="w-4 h-4" />
                <span>Confirm & Publish to Phone Agent</span>
              </button>
            )}
          </div>

          <div className="space-y-3">
            {items.map((item) => {
              const isLowConfidence = item.confidence < 0.90;
              return (
                <div
                  key={item.id}
                  className={`p-4 rounded-xl border flex flex-col sm:flex-row sm:items-center justify-between gap-3 ${
                    isLowConfidence
                      ? "bg-amber-50/70 border-amber-300"
                      : "bg-stone-50 border-stone-200"
                  }`}
                >
                  <div className="space-y-1">
                    <div className="flex items-center space-x-2">
                      <span className="font-bold text-sm text-stone-900">{item.name}</span>
                      <span className="text-[11px] px-2 py-0.5 rounded-md bg-white border border-stone-200 text-stone-600">
                        {item.category}
                      </span>
                      {isLowConfidence && (
                        <span className="text-[10px] font-bold px-2 py-0.5 rounded-md bg-amber-200 text-amber-900 flex items-center space-x-1">
                          <AlertCircle className="w-3 h-3" />
                          <span>Review Confidence ({(item.confidence * 100).toFixed(0)}%)</span>
                        </span>
                      )}
                    </div>
                    {item.modifiers.length > 0 && (
                      <div className="text-xs text-stone-500">
                        Modifiers: {item.modifiers.join(", ")}
                      </div>
                    )}
                  </div>

                  <div className="flex items-center space-x-4">
                    <div className="font-mono font-bold text-sm text-stone-900">
                      ${(item.priceCents / 100).toFixed(2)}
                    </div>
                    <button className="p-1.5 hover:bg-stone-200 rounded text-stone-500">
                      <Edit2 className="w-3.5 h-3.5" />
                    </button>
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}
