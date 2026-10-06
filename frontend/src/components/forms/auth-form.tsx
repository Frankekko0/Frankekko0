"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, useState, type CSSProperties, type FormEvent } from "react";
import { api, errorMessage } from "@/lib/api";
import type { AuthConfig, Session } from "@/lib/types";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Field, Input } from "@/components/ui/input";

export function AuthForm({ mode }: { mode: "login" | "register" }) {
  const router = useRouter();
  const params = useSearchParams();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState<"form" | null>(null);
  const [config, setConfig] = useState<AuthConfig | null>(null);
  const next = params.get("next");
  const safeNext = next && next.startsWith("/") && !next.startsWith("//") ? next : "/";
  // The fragment never reaches the server, so the auth redirect drops it from `next`; the
  // browser keeps it on this URL. Carry it on (e.g. a listing sent by the browser extension).
  const target = () => safeNext + (typeof window !== "undefined" && safeNext !== "/" ? window.location.hash : "");

  useEffect(() => {
    // Already signed in (valid cookie): skip the form.
    api("/auth/me").then(() => router.replace(target())).catch(() => undefined);
    api<AuthConfig>("/auth/config").then(setConfig).catch(() => undefined);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- run once on mount
  }, []);

  async function signIn() {
    setError(null);
    setLoading("form");
    try {
      await api<Session>(`/auth/${mode}`, {
        method: "POST",
        body: mode === "login" ? { email, password } : { email, password, display_name: name || undefined },
      });
      router.replace(target());
      router.refresh();
    } catch (err) {
      setError(errorMessage(err));
      setLoading(null);
    }
  }

  function submit(e: FormEvent) {
    e.preventDefault();
    void signIn();
  }

  return (
    <Card className="enter highlight p-6 shadow-pop" style={{ "--i": 1 } as CSSProperties}>
      <h1 className="text-xl font-semibold tracking-tight">{mode === "login" ? "Welcome back" : "Create your account"}</h1>
      <p className="mt-1 text-[13px] text-fg-3">
        {mode === "login" ? "Sign in to see today's best flips." : "Start finding undervalued listings in minutes."}
      </p>
      <form onSubmit={submit} className="mt-6 space-y-4" noValidate>
        {mode === "register" && (
          <Field label="Name" htmlFor="name">
            <Input id="name" value={name} onChange={(e) => setName(e.target.value)} autoComplete="name" maxLength={80} />
          </Field>
        )}
        <Field label="Email" htmlFor="email">
          <Input id="email" type="email" value={email} onChange={(e) => setEmail(e.target.value)} autoComplete="email" required />
        </Field>
        <Field label="Password" htmlFor="password" hint={mode === "register" ? "At least 10 characters, letters and numbers." : undefined}>
          <Input
            id="password"
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoComplete={mode === "login" ? "current-password" : "new-password"}
            required
            minLength={mode === "register" ? 10 : 1}
          />
        </Field>
        {error && (
          <p role="alert" className="rounded-lg bg-danger-soft px-3 py-2 text-[13px] text-danger">
            {error}
          </p>
        )}
        <Button type="submit" className="w-full" size="lg" loading={loading === "form"} disabled={loading !== null}>
          {mode === "login" ? "Sign in" : "Create account"}
        </Button>
      </form>
      <p className="mt-5 text-center text-[13px] text-fg-3">
        {mode === "register" ? (
          <>
            Already registered?{" "}
            <Link href="/login" className="font-medium text-accent hover:underline">
              Sign in
            </Link>
          </>
        ) : config?.registration_enabled === false ? null : (
          <>
            New here?{" "}
            <Link href="/register" className="font-medium text-accent hover:underline">
              Create an account
            </Link>
          </>
        )}
      </p>
    </Card>
  );
}
