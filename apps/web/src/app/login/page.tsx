"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { ErrorState } from "@/components/States";
import { api, ApiError } from "@/lib/api";

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
    <form onSubmit={submit} className="bg-white rounded-xl border border-stone-200 p-6 space-y-4">
      <h1 className="text-xl font-serif font-bold">Restaurant sign in</h1>
      {error && <ErrorState title="Sign in failed" message={error} />}
      <div className="space-y-1">
        <label htmlFor="email" className="block text-sm font-medium">Email</label>
        <input id="email" type="email" required autoComplete="username" value={email}
          onChange={(e) => setEmail(e.target.value)} className="w-full rounded-lg border border-stone-300 px-3 py-2" />
      </div>
      <div className="space-y-1">
        <label htmlFor="password" className="block text-sm font-medium">Password</label>
        <input id="password" type="password" required autoComplete="current-password" value={password}
          onChange={(e) => setPassword(e.target.value)} className="w-full rounded-lg border border-stone-300 px-3 py-2" />
      </div>
      <button type="submit" disabled={busy}
        className="w-full rounded-lg bg-terracotta-700 text-white py-2 font-semibold disabled:opacity-60">
        {busy ? "Signing in…" : "Sign in"}
      </button>
    </form>
  );
}

export default function LoginPage() {
  return (
    <div className="max-w-sm mx-auto px-4 py-16">
      <Suspense>
        <LoginForm />
      </Suspense>
    </div>
  );
}
