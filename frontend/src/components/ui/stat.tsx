import type { CSSProperties, ReactNode } from "react";
import { cn } from "@/lib/utils";

/** Stat tile: label (sentence case) · value · optional context line. */
export function StatTile({
  label,
  value,
  sub,
  icon,
  tone,
  loading,
  className,
  index = 0,
}: {
  index?: number;
  label: string;
  value: ReactNode;
  sub?: ReactNode;
  icon?: ReactNode;
  tone?: "success" | "ultra" | "accent";
  loading?: boolean;
  className?: string;
}) {
  return (
    <div
      className={cn("enter lift highlight relative overflow-hidden rounded-2xl border border-line bg-surface p-4", className)}
      style={{ "--i": index } as CSSProperties}
    >
      <div className="flex min-h-7 items-start justify-between gap-2">
        <p className="line-clamp-2 pt-1 text-[12px] font-medium leading-tight text-fg-3">{label}</p>
        {icon && (
          <span
            className={cn(
              "flex size-7 shrink-0 items-center justify-center rounded-lg ring-1 ring-inset ring-current/10 [&_svg]:size-3.5",
              tone === "success" ? "bg-success-soft text-success" : tone === "ultra" ? "bg-ultra-soft text-ultra" : "bg-accent-soft text-accent",
            )}
          >
            {icon}
          </span>
        )}
      </div>
      {loading ? (
        <div className="skeleton mt-2 h-7 w-24 rounded" />
      ) : (
        <p className="mt-1.5 text-[26px] font-semibold leading-tight tracking-[-0.02em] text-fg tnum">{value}</p>
      )}
      {sub && <p className="mt-0.5 truncate text-xs text-fg-3">{sub}</p>}
    </div>
  );
}
