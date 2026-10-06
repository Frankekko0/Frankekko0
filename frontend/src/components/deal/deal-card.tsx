"use client";

import { Bookmark, BookmarkCheck, Clock, ExternalLink, EyeOff, Flame, TrendingDown, Zap } from "lucide-react";
import Link from "next/link";
import type { CSSProperties } from "react";
import { CONDITION_LABEL, DEMAND_LABEL, days, eur, pct, timeAgo } from "@/lib/format";
import { useSetFavorite } from "@/lib/queries";
import type { FavoriteState, OpportunityCard } from "@/lib/types";
import { cn } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Tip } from "@/components/ui/misc";
import { ListingImage } from "./listing-image";
import { RiskBadge, ScoreRing } from "./score";

function ProfitLine({ deal }: { deal: OpportunityCard }) {
  const positive = (deal.expected_profit ?? 0) > 0;
  return (
    <div className="grid grid-cols-2 gap-x-3 gap-y-2.5">
      <Metric label="Buy" value={eur(deal.listing_price)} hint={deal.previous_price ? `was ${eur(deal.previous_price)}` : undefined} />
      <Metric label="Sell" value={eur(deal.expected_sale_price)} hint={deal.fair_market_value ? "expected" : "no market data"} />
      <Metric
        label="Profit"
        value={eur(deal.expected_profit, { sign: true })}
        tone={positive ? "success" : "danger"}
        hint={`ROI ${pct(deal.expected_roi)} · cost ${eur(deal.total_acquisition_cost)}`}
      />
      <Metric
        label="Risk-adjusted"
        value={deal.risk_adjusted_profit !== null ? eur(deal.risk_adjusted_profit, { sign: true }) : "—"}
        tone={deal.risk_adjusted_profit === null ? undefined : deal.risk_adjusted_profit > 0 ? "success" : "danger"}
        hint={
          deal.sale_probability !== null && deal.authenticity_probability !== null
            ? `sale ${pct(deal.sale_probability)} · authentic ${pct(deal.authenticity_probability)}`
            : "insufficient data"
        }
      />
    </div>
  );
}

function Metric({ label, value, hint, tone }: { label: string; value: string; hint?: string; tone?: "success" | "danger" }) {
  return (
    <div className="min-w-0">
      <p className="text-[10px] font-semibold uppercase tracking-[0.08em] text-fg-3">{label}</p>
      <p className={cn("text-[17px] font-semibold tracking-tight tnum", tone === "success" ? "text-success" : tone === "danger" ? "text-danger" : "text-fg")}>
        {value}
      </p>
      {hint && <p className="truncate text-[11px] text-fg-3">{hint}</p>}
    </div>
  );
}

export function DealCard({
  deal,
  priority = false,
  index = 0,
  rank,
  onStateChange,
}: {
  deal: OpportunityCard;
  priority?: boolean;
  index?: number;
  /** Position in a ranking (1 = best), shown on the photo. */
  rank?: number;
  /** For lists held outside the query cache (e.g. an import's results). */
  onStateChange?: (state: FavoriteState | null) => void;
}) {
  const setFav = useSetFavorite();
  const saved = deal.favorite_state === "saved" || deal.favorite_state === "watching";
  const setState = (state: FavoriteState | null) =>
    setFav.mutate({ id: deal.id, state }, { onSuccess: () => onStateChange?.(state) });
  const discount = deal.discount_vs_market;
  return (
    <article
      className={cn(
        "enter lift highlight group relative flex flex-col rounded-2xl border bg-surface",
        deal.is_ultra_deal ? "ultra-border border-transparent" : "border-line",
        !deal.is_active && "opacity-60",
      )}
      style={{ "--i": index } as CSSProperties}
    >
      <Link
        href={`/deals/${deal.id}`}
        className="relative block aspect-[4/3] overflow-hidden rounded-t-[15px]"
        aria-label={`Open analysis: ${deal.title}`}
      >
        <ListingImage src={deal.image_url} alt={deal.title} className="zoom-on-hover h-full w-full" />
        {/* Scrims keep the overlaid badges legible on any photo. */}
        <span className="pointer-events-none absolute inset-x-0 top-0 h-14 bg-gradient-to-b from-black/15 to-transparent" aria-hidden />
        <span className="pointer-events-none absolute inset-x-0 bottom-0 h-14 bg-gradient-to-t from-black/30 to-transparent" aria-hidden />
        <div className="absolute inset-x-0 top-0 flex items-start justify-between p-2.5">
          <div className="flex flex-col items-start gap-1.5">
            {rank !== undefined && (
              <Badge tone="dark" className="tnum">
                #{rank}
              </Badge>
            )}
            {deal.is_ultra_deal && (
              <Badge tone="ultra" className="shadow-card">
                <Flame /> ULTRA DEAL
              </Badge>
            )}
            {deal.previous_price && (
              <Badge tone="dark">
                <TrendingDown /> Price drop
              </Badge>
            )}
            {!deal.is_active && <Badge tone="dark">{deal.listing_status === "sold" ? "Sold" : "No longer available"}</Badge>}
          </div>
          {deal.data_quality === "insufficient" ? (
            <Tip content={deal.insufficient_reason ?? "Too few comparable listings for a reliable estimate"}>
              <Badge tone="dark">Insufficient data</Badge>
            </Tip>
          ) : (
            <div className="rounded-full bg-surface/90 p-0.5 shadow-card ring-1 ring-black/5 backdrop-blur-md">
              <ScoreRing score={deal.flip_score} size={44} stroke={3.5} />
            </div>
          )}
        </div>
        <div className="absolute inset-x-0 bottom-0 flex items-end justify-between p-2.5">
          <Badge tone="dark">
            <Clock /> {timeAgo(deal.published_at)}
          </Badge>
          {discount !== null && discount > 0.05 && <Badge tone="dark">−{Math.round(discount * 100)}% vs market</Badge>}
        </div>
      </Link>

      <div className="flex flex-1 flex-col gap-3 p-3.5">
        <div className="min-w-0">
          <p className="truncate text-[12px] font-medium text-fg-3">
            {[deal.brand?.name ?? "Unknown brand", deal.size, CONDITION_LABEL[deal.condition]].filter(Boolean).join(" · ")}
          </p>
          <Link
            href={`/deals/${deal.id}`}
            className="mt-0.5 line-clamp-2 text-[14px] font-semibold leading-snug text-fg decoration-fg-3/40 underline-offset-2 hover:underline"
          >
            {deal.title}
          </Link>
        </div>

        <ProfitLine deal={deal} />

        <div className="flex flex-wrap items-center gap-1.5">
          <Tip content="How sure the system is about this analysis">
            <span>
              <Badge tone="outline">Confidence {deal.confidence_score}</Badge>
            </span>
          </Tip>
          <RiskBadge level={deal.risk_level} />
          {deal.demand_level && <Badge tone="neutral">Demand {DEMAND_LABEL[deal.demand_level]}</Badge>}
          {deal.estimated_days_to_sell !== null && (
            <Badge tone="neutral">
              <Zap /> {days(deal.estimated_days_to_sell)}
            </Badge>
          )}
        </div>

        <div className="mt-auto flex items-center gap-2 pt-1">
          <Button asChild variant={deal.is_ultra_deal ? "ultra" : "primary"} size="sm" className="flex-1">
            <a href={deal.url} target="_blank" rel="noopener noreferrer">
              {deal.is_ultra_deal ? <Flame /> : <ExternalLink />} VIEW DEAL
            </a>
          </Button>
          <Tip content={saved ? "Remove from saved" : "Save deal"}>
            <Button
              variant="outline"
              size="icon-sm"
              aria-label={saved ? "Remove from saved" : "Save deal"}
              onClick={() => setState(saved ? null : "saved")}
            >
              {saved ? <BookmarkCheck className="text-accent" /> : <Bookmark />}
            </Button>
          </Tip>
          <Tip content="Not interested (teaches your recommendations)">
            <Button variant="ghost" size="icon-sm" aria-label="Ignore deal" onClick={() => setState("ignored")}>
              <EyeOff />
            </Button>
          </Tip>
        </div>
      </div>
      {priority && <span className="sr-only">Top opportunity</span>}
    </article>
  );
}

export function DealCardSkeleton({ index = 0 }: { index?: number }) {
  return (
    <div className="enter overflow-hidden rounded-2xl border border-line bg-surface" style={{ "--i": index } as CSSProperties}>
      <div className="skeleton aspect-[4/3]" />
      <div className="space-y-3 p-3.5">
        <div className="skeleton h-3 w-1/2 rounded" />
        <div className="skeleton h-4 w-5/6 rounded" />
        <div className="grid grid-cols-2 gap-3">
          {Array.from({ length: 4 }).map((_, i) => (
            <div key={i} className="skeleton h-9 rounded" />
          ))}
        </div>
        <div className="skeleton h-8 rounded-lg" />
      </div>
    </div>
  );
}

/** Compact row for side lists (High ROI, Fast Flips...). */
export function DealRow({ deal, metric }: { deal: OpportunityCard; metric: "roi" | "velocity" | "profit" | "new" }) {
  const right =
    metric === "roi"
      ? { value: pct(deal.expected_roi), label: "ROI" }
      : metric === "velocity"
        ? { value: days(deal.estimated_days_to_sell), label: "to sell" }
        : metric === "profit"
          ? { value: eur(deal.expected_profit, { sign: true }), label: "profit" }
          : { value: timeAgo(deal.published_at), label: "listed" };
  return (
    <Link href={`/deals/${deal.id}`} className="group press flex items-center gap-3 rounded-xl p-2 transition-[background-color,transform] duration-150 hover:bg-surface-2">
      <span className="size-12 shrink-0 overflow-hidden rounded-lg">
        <ListingImage src={deal.image_url} alt={deal.title} className="zoom-on-hover size-12" />
      </span>
      <div className="min-w-0 flex-1">
        <p className="truncate text-[13px] font-semibold text-fg">{deal.title}</p>
        <p className="truncate text-xs text-fg-3">
          {eur(deal.listing_price)} → {eur(deal.expected_sale_price)} · {deal.brand?.name ?? "—"}
        </p>
      </div>
      <div className="text-right">
        <p className={cn("text-sm font-semibold tnum", metric === "roi" || metric === "profit" ? "text-success" : "text-fg")}>{right.value}</p>
        <p className="text-[11px] text-fg-3">{right.label}</p>
      </div>
      <ScoreRing score={deal.flip_score} size={34} stroke={3} />
    </Link>
  );
}
