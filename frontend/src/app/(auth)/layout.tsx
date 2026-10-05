import type { ReactNode } from "react";
import { Logo } from "@/components/layout/brand";

export default function AuthLayout({ children }: { children: ReactNode }) {
  return (
    <div className="relative flex min-h-dvh flex-col items-center justify-center px-4 py-10">
      <div
        className="pointer-events-none absolute inset-x-0 top-0 h-80 opacity-60 dark:opacity-30"
        style={{ background: "radial-gradient(60% 100% at 50% 0%, var(--ultra-soft), transparent)" }}
        aria-hidden
      />
      <div className="relative w-full max-w-sm">
        <div className="mb-8 flex justify-center">
          <Logo className="scale-125" />
        </div>
        {children}
      </div>
    </div>
  );
}
