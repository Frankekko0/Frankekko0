import { cn } from "@/lib/utils";

export function Logo({ className, withText = true }: { className?: string; withText?: boolean }) {
  return (
    <span className={cn("inline-flex items-center gap-2", className)}>
      <svg viewBox="0 0 32 32" className="size-7 shrink-0" aria-hidden>
        <defs>
          <linearGradient id="ff-g" x1="0" y1="0" x2="1" y2="1">
            <stop offset="0" stopColor="var(--ultra)" />
            <stop offset="1" stopColor="var(--ultra-2)" />
          </linearGradient>
        </defs>
        <rect width="32" height="32" rx="9" fill="var(--text)" />
        <path d="M9 22.5 15 16l3.5 3.5L24 13" fill="none" stroke="url(#ff-g)" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" />
        <circle cx="24" cy="13" r="2.2" fill="var(--ultra-2)" />
      </svg>
      {withText && (
        <span className="text-[15px] font-semibold tracking-tight text-fg">
          Flip<span className="text-fg-3">Finder</span>
        </span>
      )}
    </span>
  );
}
