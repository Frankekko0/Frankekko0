import type { HTMLAttributes } from "react";
import { cn } from "@/lib/utils";

const tones = {
  neutral: "bg-surface-2 text-fg-2",
  outline: "border border-line-strong text-fg-2",
  accent: "bg-accent-soft text-accent",
  success: "bg-success-soft text-success",
  warning: "bg-warning-soft text-warning",
  serious: "bg-serious-soft text-serious",
  danger: "bg-danger-soft text-danger",
  ultra: "bg-gradient-to-r from-ultra-solid to-ultra-solid-2 text-white",
  dark: "bg-black/65 text-white backdrop-blur-md",
} as const;

export type BadgeTone = keyof typeof tones;

export function Badge({ className, tone = "neutral", ...props }: HTMLAttributes<HTMLSpanElement> & { tone?: BadgeTone }) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-semibold leading-4 whitespace-nowrap [&_svg]:size-3",
        tones[tone],
        className,
      )}
      {...props}
    />
  );
}
