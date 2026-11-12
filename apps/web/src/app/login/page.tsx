"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { ErrorState } from "@/components/States";
import { api, ApiError } from "@/lib/api";
import { OVEN } from "@/lib/menuMedia";

function LoginForm() {
  const router = useRouter();
  const params = useSearchParams();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api("/api/v1/merchant/auth/login", { method: "POST", body: JSON.stringify({ email, password }) });
      const next = params.get("next");
      router.replace(next && next.startsWith("/") ? next : "/console");
    } catch (err) {
      setError((err as ApiError).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} className="w-full max-w-sm space-y-4">
      <h1 className="text-[36px] font-extrabold tracking-[-0.04em] leading-none">Restaurant sign-in</h1>
      <p className="text-[15px] text-ink-soft">For the kitchen screen, the console and the menu.</p>
      {error && <ErrorState title="That didn't work" message={error} />}
      <div className="space-y-1.5">
        <label htmlFor="email" className="block text-[14px] font-semibold">
          Email
        </label>
        <input id="email" type="email" required autoComplete="username" value={email} onChange={(e) => setEmail(e.target.value)}
          className="w-full min-h-[48px] rounded-full border border-board-line px-5 text-[16px]" />
      </div>
      <div className="space-y-1.5">
        <label htmlFor="password" className="block text-[14px] font-semibold">
          Password
        </label>
        <input id="password" type="password" required autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)}
          className="w-full min-h-[48px] rounded-full border border-board-line px-5 text-[16px]" />
      </div>
      <button type="submit" disabled={busy} className="btn-press w-full min-h-[48px] rounded-full bg-ink text-white font-bold text-[16px] disabled:opacity-60">
        {busy ? "Signing in…" : "Sign in"}
      </button>
    </form>
  );
}

export default function LoginPage() {
  return (
    <div className="max-w-[1200px] mx-auto px-4 sm:px-6 py-10 grid gap-10 md:grid-cols-2 items-center">
      <div className="pin-photo order-2 md:order-1">
        <img src={OVEN.src} srcSet={OVEN.srcSet} sizes="(max-width: 768px) 100vw, 560px" width={OVEN.width} height={OVEN.height} alt={OVEN.alt} className="block w-full h-auto" />
      </div>
      <div className="order-1 md:order-2 flex justify-center">
        <Suspense>
          <LoginForm />
        </Suspense>
      </div>
    </div>
  );
}
