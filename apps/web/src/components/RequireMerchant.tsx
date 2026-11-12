"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState, type ReactNode } from "react";
import { api, ApiError } from "@/lib/api";
import { ErrorState, Loading } from "./States";

export function RequireMerchant({ children }: { children: ReactNode }) {
  const router = useRouter();
  const [state, setState] = useState<"checking" | "ok" | "error">("checking");
  const [message, setMessage] = useState("");

  useEffect(() => {
    api<{ email: string }>("/api/v1/merchant/auth/me")
      .then(() => setState("ok"))
      .catch((err: ApiError) => {
        if (err.status === 401) {
          router.replace(`/login?next=${encodeURIComponent(window.location.pathname)}`);
        } else {
          setMessage(err.message);
          setState("error");
        }
      });
  }, [router]);

  if (state === "checking") return <Loading label="Checking your session…" />;
  if (state === "error")
    return (
      <div className="max-w-xl mx-auto p-6">
        <ErrorState title="Can't load the merchant area" message={message} />
      </div>
    );
  return <>{children}</>;
}
