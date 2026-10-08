"use client";

import { AlertTriangle, CircleCheck, CircleHelp, CircleX, Info, Layers, ShieldAlert } from "lucide-react";
import type { ReactNode } from "react";
import { Badge } from "@/components/ui/badge";
import { eur, pct } from "@/lib/format";
import type { DataQuality, MarketComparison, PriceStats, RiskSignal, SignalLevel, TimeOnline } from "@/lib/types";
import { cn } from "@/lib/utils";
import { TableScroll } from "@/components/ui/table-scroll";

/* --------------------------------------------------------------- data quality */
export function DataQualityBanner({ quality, reason }: { quality: DataQuality | null | undefined; reason: string | null | undefined }) {
  if (!quality || quality === "ok") return null;
  const insufficient = quality === "insufficient";
  return (
    <div
      role="status"
      className={cn(
        "flex items-start gap-3 rounded-2xl border p-4",
        insufficient ? "border-warning/40 bg-warning-soft" : "border-line bg-surface-2",
      )}
    >
      <span className={cn("mt-0.5 shrink-0", insufficient ? "text-warning" : "text-fg-3")}>
        {insufficient ? <AlertTriangle className="size-5" /> : <Info className="size-5" />}
      </span>
      <div className="min-w-0 text-[13px]">
        <p className="font-semibold text-fg">{insufficient ? "Insufficient data: no score" : "Indicative estimate"}</p>
        <p className="mt-0.5 text-fg-2">
          {reason ??
            (insufficient
              ? "Too few comparable listings to estimate the value reliably."
              : "Few comparable listings: treat the numbers as a rough guide.")}
        </p>
      </div>
    </div>
  );
}

/* --------------------------------------------------------------- market comparison */
function StatsRow({ label, s, highlight }: { label: string; s: PriceStats | null; highlight?: boolean }) {
  return (
    <tr className={cn("border-t border-line", highlight && "font-semibold")}>
      <td className="py-2 pr-2 text-fg-2">{label}</td>
      <td className="py-2 text-right tnum text-fg-3">{s ? s.n : 0}</td>
      {(["min", "p25", "median", "p75", "max"] as const).map((k) => (
        <td key={k} className="py-2 text-right tnum">
          {s ? eur(s[k]) : "—"}
        </td>
      ))}
    </tr>
  );
}

function Count({ label, value, sub }: { label: string; value: ReactNode; sub?: ReactNode }) {
  return (
    <div className="min-w-0 rounded-xl bg-surface-2 px-3 py-2.5">
      <p className="text-[11px] font-medium text-fg-3">{label}</p>
      <p className="text-[17px] font-semibold tnum text-fg">{value}</p>
      {sub && <p className="truncate text-[11px] text-fg-3">{sub}</p>}
    </div>
  );
}

export function MarketComparisonView({ c }: { c: MarketComparison }) {
  const m = c.matches;
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        <Count label="Similar listings found" value={c.found} sub={`${c.found_sold} sold · ${c.found_active} on sale · ${c.found_removed} removed`} />
        <Count label="Used for the price" value={c.used} sub={`${c.used_sold} sold · ${c.used_active} on sale`} />
        <Count label="Same model" value={m.same_model ?? "—"} sub={m.same_model === null ? "model not identified" : `of ${c.used}`} />
        <Count label="Same size · condition" value={`${m.same_size ?? "—"} · ${m.same_condition}`} sub={`of ${c.used}`} />
      </div>
      <TableScroll label="Comparable prices" className="-mx-1">
        <table className="w-full min-w-[520px] text-[13px]">
          <thead>
            <tr className="text-left text-xs text-fg-3">
              <th className="pb-1.5 font-medium">Prices</th>
              <th className="pb-1.5 text-right font-medium">n</th>
              <th className="pb-1.5 text-right font-medium">Min</th>
              <th className="pb-1.5 text-right font-medium">P25</th>
              <th className="pb-1.5 text-right font-medium">Median</th>
              <th className="pb-1.5 text-right font-medium">P75</th>
              <th className="pb-1.5 text-right font-medium">Max</th>
            </tr>
          </thead>
          <tbody>
            <StatsRow label="All comparables" s={c.prices} highlight />
            <StatsRow label="Sold" s={c.sold_prices} />
            <StatsRow label="On sale (asking)" s={c.active_prices} />
          </tbody>
        </table>
      </TableScroll>
      <p className="text-xs text-fg-3">
        Same brand and category family, ranked by model, size, condition, title, colour and material.
        {c.prices_adjusted_to_condition && " Prices are brought to this item's condition, so they compare like for like."}
        {c.outliers_excluded > 0 && ` ${c.outliers_excluded} anomalous prices excluded.`}
        {c.used_segment_prior && " Few direct comparables: the estimate is blended with the brand/category statistics."}
      </p>
    </div>
  );
}

/* --------------------------------------------------------------- time online */
export function TimeOnlineView({ t }: { t: TimeOnline }) {
  if (!t.comparables) return <p className="text-sm text-fg-2">No comparable listings with a known lifecycle yet.</p>;
  return (
    <div className="space-y-2">
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        <Count label="Actually sold" value={t.sold_share !== null ? pct(t.sold_share) : "—"} sub={`${t.sold} of ${t.comparables} comparables`} />
        <Count
          label="Days to sell"
          value={t.days_to_sell ? `~${Math.round(t.days_to_sell.median)}` : "—"}
          sub={t.days_to_sell ? `median · mean ${t.days_to_sell.mean}` : "no sale observed"}
        />
        <Count
          label="Still on sale"
          value={t.active}
          sub={t.days_online_active ? `online for ~${Math.round(t.days_online_active.median)} days` : undefined}
        />
        <Count
          label="Removed, not sold"
          value={t.removed}
          sub={t.days_online_removed ? `after ~${Math.round(t.days_online_removed.median)} days` : undefined}
        />
      </div>
      <p className="text-xs text-fg-3">
        Only sales seen as such are counted: listings that disappear without a sale are &ldquo;removed&rdquo; and count as not sold.
      </p>
    </div>
  );
}

/* --------------------------------------------------------------- risk checklist */
const LEVEL: Record<SignalLevel, { icon: typeof CircleCheck; tone: string; badge: "success" | "neutral" | "warning" | "serious" | "danger"; text: string }> = {
  ok: { icon: CircleCheck, tone: "text-success", badge: "success", text: "OK" },
  info: { icon: CircleHelp, tone: "text-fg-3", badge: "neutral", text: "Not verified" },
  low: { icon: Info, tone: "text-warning", badge: "warning", text: "Check" },
  medium: { icon: AlertTriangle, tone: "text-serious", badge: "serious", text: "Warning" },
  high: { icon: CircleX, tone: "text-danger", badge: "danger", text: "High risk" },
};

export function RiskChecklist({ signals }: { signals: RiskSignal[] }) {
  if (!signals.length) return null;
  return (
    <ul className="divide-y divide-line rounded-xl border border-line">
      {signals.map((s) => {
        const L = LEVEL[s.level] ?? LEVEL.info;
        return (
          <li key={s.code} className="flex items-start gap-3 px-3.5 py-3">
            <L.icon className={cn("mt-0.5 size-4 shrink-0", L.tone)} aria-hidden />
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                <p className="text-[13px] font-semibold text-fg">{s.title}</p>
                <Badge tone={L.badge}>{L.text}</Badge>
              </div>
              <p className="mt-0.5 text-[13px] text-fg-2">{s.label}</p>
              {s.evidence.length > 0 && (
                <ul className="mt-1 space-y-0.5">
                  {s.evidence.map((e) => (
                    <li key={e} className="text-xs text-fg-3">
                      · {e}
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </li>
        );
      })}
    </ul>
  );
}

export const SECTION_ICONS = { comparison: <Layers />, risk: <ShieldAlert /> };
