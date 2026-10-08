import { AlertTriangle, ShieldAlert, ShieldCheck, ShieldX } from "lucide-react";
import type { CSSProperties } from "react";
import { RISK_LABEL } from "@/lib/format";
import type { RiskLevel } from "@/lib/types";
import { cn } from "@/lib/utils";
import { Badge, type BadgeTone } from "@/components/ui/badge";

export function scoreColor(score: number): string {
  if (score >= 90) return "var(--ultra)";
  if (score >= 80) return "var(--success)";
  if (score >= 70) return "var(--accent)";
  if (score >= 60) return "var(--warning)";
  return "var(--text-3)";
}

/** Circular score gauge. The arc length carries the value; the number is always printed. */
export function ScoreRing({
  score,
  size = 48,
  stroke = 4,
  label,
  className,
  color,
}: {
  score: number;
  size?: number;
  stroke?: number;
  label?: string;
  className?: string;
  color?: string;
}) {
  const r = (size - stroke) / 2;
  const c = 2 * Math.PI * r;
  const value = Math.max(0, Math.min(100, score));
  return (
    <div className={cn("relative inline-flex shrink-0 items-center justify-center", className)} style={{ width: size, height: size }}>
      <svg width={size} height={size} className="-rotate-90" aria-hidden>
        <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="var(--surface-3)" strokeWidth={stroke} />
        {/* Draws in on mount (ring-draw), then eases between values when the score changes. */}
        <circle
          cx={size / 2}
          cy={size / 2}
          r={r}
          fill="none"
          stroke={color ?? scoreColor(value)}
          strokeWidth={stroke}
          strokeLinecap="round"
          strokeDasharray={c}
          strokeDashoffset={c * (1 - value / 100)}
          style={{ "--ring-len": c, animation: "ring-draw 0.9s var(--ease-out) backwards" } as CSSProperties}
          className="transition-[stroke-dashoffset] duration-700 ease-[var(--ease-out)]"
        />
      </svg>
      <span className="absolute inset-0 flex flex-col items-center justify-center leading-none" aria-hidden>
        <span className="font-semibold tracking-tight text-fg" style={{ fontSize: size * 0.32 }}>
          {Math.round(value)}
        </span>
        {label && <span className="mt-0.5 text-[9px] font-medium uppercase tracking-wider text-fg-3">{label}</span>}
      </span>
      <span className="sr-only">{`${label && /score/i.test(label) ? label : `${label ?? "Flip"} score`} ${Math.round(value)} out of 100`}</span>
    </div>
  );
}

const RISK_TONE: Record<RiskLevel, BadgeTone> = { low: "success", moderate: "warning", high: "serious", very_high: "danger" };
const RISK_ICON = { low: ShieldCheck, moderate: ShieldAlert, high: AlertTriangle, very_high: ShieldX };

/** Status always ships with icon + label, never color alone. */
export function RiskBadge({ level, score, className }: { level: RiskLevel; score?: number; className?: string }) {
  const Icon = RISK_ICON[level] ?? ShieldAlert;
  return (
    <Badge tone={RISK_TONE[level] ?? "neutral"} className={className}>
      <Icon aria-hidden />
      Risk {RISK_LABEL[level] ?? level}
      {score !== undefined && <span className="font-medium">· {score}</span>}
    </Badge>
  );
}

export function Meter({ value, color, className }: { value: number; color?: string; className?: string }) {
  return (
    <div className={cn("h-1.5 w-full overflow-hidden rounded-full bg-surface-3", className)}>
      <div
        className="h-full origin-left rounded-full transition-[width] duration-700 ease-[var(--ease-out)]"
        style={{
          width: `${Math.max(0, Math.min(100, value))}%`,
          background: color ?? scoreColor(value),
          animation: "grow-x 0.9s var(--ease-out) backwards",
        }}
      />
    </div>
  );
}
