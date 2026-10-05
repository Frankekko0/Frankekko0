import type { ReactNode } from "react";
import { cn } from "@/lib/utils";

export function Skeleton({ className }: { className?: string }) {
  return <div className={cn("skeleton rounded-lg", className)} aria-hidden />;
}

export function EmptyState({
  icon,
  title,
  description,
  action,
  className,
}: {
  icon?: ReactNode;
  title: string;
  description?: string;
  action?: ReactNode;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "enter relative flex flex-col items-center justify-center overflow-hidden rounded-2xl border border-dashed border-line-strong px-6 py-14 text-center",
        className,
      )}
    >
      <div className="grid-bg pointer-events-none absolute inset-0 opacity-60" aria-hidden />
      {icon && (
        <div className="relative mb-4 flex size-12 items-center justify-center rounded-2xl bg-surface text-accent shadow-card ring-1 ring-line [&_svg]:size-5">
          <span className="absolute inset-0 rounded-2xl bg-gradient-to-br from-accent-soft to-transparent" aria-hidden />
          <span className="relative">{icon}</span>
        </div>
      )}
      <p className="relative text-[15px] font-semibold text-fg">{title}</p>
      {description && <p className="relative mt-1 max-w-sm text-[13px] leading-relaxed text-fg-3">{description}</p>}
      {action && <div className="relative mt-5">{action}</div>}
    </div>
  );
}

export function ErrorState({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div className="enter flex flex-col items-center gap-3 rounded-2xl border border-line bg-surface px-6 py-10 text-center">
      <p className="text-sm font-medium text-fg">Qualcosa non ha funzionato</p>
      <p className="max-w-md text-[13px] text-fg-3">{message}</p>
      {onRetry && (
        <button onClick={onRetry} className="text-[13px] font-medium text-accent hover:underline">
          Riprova
        </button>
      )}
    </div>
  );
}

export function SectionHeader({ title, description, action, icon }: { title: string; description?: string; action?: ReactNode; icon?: ReactNode }) {
  return (
    <div className="mb-3 flex items-end justify-between gap-3">
      <div className="min-w-0">
        <h2 className="flex items-center gap-2 text-[17px] font-semibold tracking-tight text-fg [&_svg]:size-[18px]">
          {icon}
          {title}
        </h2>
        {description && <p className="mt-0.5 text-[13px] text-fg-3">{description}</p>}
      </div>
      {action}
    </div>
  );
}
