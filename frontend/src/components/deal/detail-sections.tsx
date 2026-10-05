"use client";

import {
  AlertTriangle,
  BadgeCheck,
  Bot,
  Calculator,
  ChevronDown,
  CircleCheck,
  CircleMinus,
  CirclePlus,
  CircleX,
  Clock,
  HandCoins,
  Info,
  RefreshCw,
  ScanSearch,
  Star,
  Store,
  TrendingUp,
} from "lucide-react";
import { useState, type ReactNode } from "react";
import { api, errorMessage } from "@/lib/api";
import { ACTION_LABEL, CONDITION_LABEL, DEMAND_LABEL, days, eur, pct, shortDate, timeAgo } from "@/lib/format";
import { useRunAi } from "@/lib/queries";
import type { Attribute, Comparable, CostLine, OpportunityDetail, Reason, Scenario } from "@/lib/types";
import { cn } from "@/lib/utils";
import { PriceDistribution } from "@/components/charts/price-distribution";
import { PriceHistoryChart } from "@/components/charts/history-line";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { InputAffix, Label } from "@/components/ui/input";
import { Tip } from "@/components/ui/misc";
import { ListingImage } from "./listing-image";
import { Meter, RiskBadge, scoreColor } from "./score";

function Section({ id, title, description, icon, action, children, className }: { id: string; title: string; description?: string; icon?: ReactNode; action?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <Card id={id} className={cn("scroll-mt-24", className)}>
      <CardHeader className="flex-wrap">
        <div className="min-w-0 flex-[1_1_240px]">
          <CardTitle className="flex items-center gap-2 [&_svg]:size-4 [&_svg]:text-fg-3">
            {icon}
            {title}
          </CardTitle>
          {description && <CardDescription>{description}</CardDescription>}
        </div>
        {action}
      </CardHeader>
      <CardContent>{children}</CardContent>
    </Card>
  );
}

function KV({ label, value, sub, strong }: { label: string; value: ReactNode; sub?: ReactNode; strong?: boolean }) {
  return (
    <div className="min-w-0">
      <p className="text-xs text-fg-3">{label}</p>
      <p className={cn("tnum", strong ? "text-lg font-semibold text-fg" : "text-[15px] font-medium text-fg")}>{value}</p>
      {sub && <p className="text-[11px] text-fg-3">{sub}</p>}
    </div>
  );
}

/* --------------------------------------------------------------- Explanation */
const COMPONENT_LABEL: Record<string, string> = {
  undervaluation: "Price undervaluation",
  roi: "Expected ROI",
  profit: "Expected net profit",
  demand: "Demand",
  velocity: "Sales velocity",
  freshness: "Listing freshness",
  seller: "Seller reliability",
};

function ReasonRow({ r }: { r: Reason }) {
  const Icon = r.type === "positive" ? CirclePlus : r.type === "negative" ? CircleMinus : Info;
  return (
    <li className="flex items-start gap-2.5 py-1.5">
      <Icon className={cn("mt-0.5 size-4 shrink-0", r.type === "positive" ? "text-success" : r.type === "negative" ? "text-danger" : "text-fg-3")} aria-hidden />
      <span className="flex-1 text-[14px] text-fg">{r.label}</span>
      {r.impact !== null && r.impact !== 0 && (
        <span className={cn("text-xs font-semibold tnum", r.impact > 0 ? "text-success" : "text-danger")}>
          {r.impact > 0 ? "+" : ""}
          {r.impact.toFixed(1)}
        </span>
      )}
    </li>
  );
}

export function ExplanationSection({ d }: { d: OpportunityDetail }) {
  const comps = d.score.components ?? {};
  return (
    <Section
      id="why"
      icon={<Info />}
      title={`Why ${d.score.flip_score}/100?`}
      description="Every point of the Flip Score, explained. Positive factors add points; penalties subtract them."
    >
      <ul className="divide-y divide-line">
        {d.explanation.map((r, i) => (
          <ReasonRow key={`${r.code}-${i}`} r={r} />
        ))}
      </ul>
      <details className="group mt-4 rounded-xl bg-surface-2 p-3">
        <summary className="flex cursor-pointer list-none items-center justify-between text-[13px] font-medium text-fg-2">
          Score breakdown by component
          <ChevronDown className="size-4 transition-transform group-open:rotate-180" />
        </summary>
        <div className="mt-3 space-y-2.5">
          {Object.entries(comps).map(([key, c]) => (
            <div key={key} className="grid grid-cols-[1fr_auto] items-center gap-x-3 gap-y-1">
              <span className="text-[13px] text-fg-2">
                {COMPONENT_LABEL[key] ?? key} <span className="text-fg-3">· weight {Math.round(c.weight * 100)}%</span>
              </span>
              <span className="text-[13px] font-semibold tnum text-fg">+{c.contribution.toFixed(1)}</span>
              <Meter value={c.score} color="var(--series-1)" className="col-span-2" />
            </div>
          ))}
          {(d.score.penalties ?? []).map((p) => (
            <div key={p.code} className="flex justify-between text-[13px]">
              <span className="text-fg-2">Penalty · {p.label}</span>
              <span className="font-semibold text-danger tnum">−{p.points.toFixed(1)}</span>
            </div>
          ))}
          {d.score.cap && <p className="text-xs text-fg-3">Capped at {d.score.cap.max}: {d.score.cap.reason}</p>}
          <p className="pt-1 text-[11px] text-fg-3">Algorithm {d.score.algorithm_version} · analysed {timeAgo(d.score.analyzed_at)}</p>
        </div>
      </details>
    </Section>
  );
}

/* --------------------------------------------------------------- Offer engine */
export function OfferCard({ d }: { d: OpportunityDetail }) {
  const s = d.smart_buy;
  const rows: { label: string; value: number | null; hint: string; tone?: string }[] = [
    { label: "Listed price", value: s.listed_price, hint: "what the seller asks" },
    { label: "Suggested first offer", value: s.suggested_offer, hint: "opening offer", tone: "text-accent" },
    { label: "Good purchase", value: s.good_buy_price, hint: "targets met even on a quick sale", tone: "text-success" },
    { label: "Maximum purchase", value: s.max_buy_price, hint: "targets met at the expected price" },
  ];
  return (
    <Section
      id="offer"
      icon={<HandCoins />}
      title="Recommended action"
      description={`Targets: profit ≥ ${eur(s.min_profit)} and ROI ≥ ${pct(s.min_roi)} (change them in Settings).`}
    >
      <div className="mb-4 flex items-center gap-2">
        <Badge tone={s.action === "buy_now" ? "success" : s.action === "make_offer" ? "accent" : s.action === "watch" ? "warning" : "danger"} className="px-2.5 py-1 text-[12px]">
          {ACTION_LABEL[s.action]}
        </Badge>
        <p className="text-[13px] text-fg-2">{s.rationale}</p>
      </div>
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        {rows.map((r) => (
          <div key={r.label} className="rounded-xl border border-line p-3">
            <p className="text-[11px] text-fg-3">{r.label}</p>
            <p className={cn("mt-0.5 text-lg font-semibold tnum", r.value === null ? "text-fg-3" : (r.tone ?? "text-fg"))}>
              {r.value === null ? "—" : r.label === "Good purchase" ? `≤ ${eur(r.value)}` : eur(r.value)}
            </p>
            <p className="text-[11px] leading-tight text-fg-3">{r.hint}</p>
          </div>
        ))}
      </div>
      <p className="mt-3 text-xs text-fg-3">FlipFinder only suggests prices: offers and purchases are always sent by you on the marketplace.</p>
    </Section>
  );
}

/* --------------------------------------------------------------- Market */
export function MarketSection({ d }: { d: OpportunityDetail }) {
  const m = d.market;
  if (m.fair_market_value === null) {
    return (
      <Section id="market" icon={<TrendingUp />} title="Market analysis">
        <p className="text-sm text-fg-2">{m.notes?.[0] ?? "Non ci sono abbastanza dati per stimare con affidabilità il prezzo di mercato."}</p>
      </Section>
    );
  }
  return (
    <Section
      id="market"
      icon={<TrendingUp />}
      title="Market analysis"
      description={`Based on ${m.n_used} comparable listings (${m.n_sold} sold, ${m.n_active} active)${m.n_outliers ? `, ${m.n_outliers} outliers excluded` : ""}.`}
      action={<Badge tone="outline">Market confidence {m.confidence ?? "—"}</Badge>}
    >
      <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
        <KV label="Fair market value" value={eur(m.fair_market_value)} strong />
        <KV label="Listing price" value={eur(m.listing_price)} sub={m.discount_vs_market !== null ? `${pct(-m.discount_vs_market, { sign: true, digits: 1 })} vs market` : undefined} strong />
        <KV label="Typical range" value={`${eur(m.p25)} – ${eur(m.p75)}`} sub="25th–75th percentile" />
        <KV label="Reasonable min / max" value={`${eur(m.min_reasonable)} – ${eur(m.max_reasonable)}`} />
        <KV label="Median" value={eur(m.median)} />
        <KV label="Average" value={eur(m.mean)} />
        <KV label="Ask → sale ratio" value={m.ask_to_sale_ratio ? pct(m.ask_to_sale_ratio) : "—"} sub="sold vs asking prices" />
        <KV label="Avg similarity" value={m.avg_similarity ? pct(m.avg_similarity) : "—"} />
      </div>
      {m.histogram && m.histogram.length > 0 && (
        <div className="mt-5">
          <p className="mb-2 text-[13px] font-medium text-fg-2">Price distribution</p>
          <PriceDistribution bins={m.histogram} listingPrice={m.listing_price} median={m.fair_market_value} p25={m.p25} p75={m.p75} />
        </div>
      )}
      {m.notes && m.notes.length > 0 && (
        <ul className="mt-4 space-y-1">
          {m.notes.map((n) => (
            <li key={n} className="flex items-start gap-2 text-[13px] text-fg-2">
              <Info className="mt-0.5 size-3.5 shrink-0 text-fg-3" /> {n}
            </li>
          ))}
        </ul>
      )}
    </Section>
  );
}

/* --------------------------------------------------------------- Profit */
const SCENARIO_LABEL = { conservative: "Conservative · quick sale", expected: "Expected", optimistic: "Optimistic" } as const;

function Breakdown({ title, lines, total, totalLabel }: { title: string; lines: CostLine[]; total: number; totalLabel: string }) {
  return (
    <div>
      <p className="mb-1.5 text-xs font-semibold uppercase tracking-[0.08em] text-fg-3">{title}</p>
      <dl className="space-y-1 text-[13px]">
        {lines
          .filter((l) => l.amount !== 0 || l.label.startsWith("Prezzo"))
          .map((l) => (
            <div key={l.label} className="flex justify-between gap-2">
              <dt className="text-fg-2">{l.label}</dt>
              <dd className="tnum text-fg">{eur(l.amount)}</dd>
            </div>
          ))}
        <div className="flex justify-between gap-2 border-t border-line pt-1 font-semibold">
          <dt>{totalLabel}</dt>
          <dd className="tnum">{eur(total)}</dd>
        </div>
      </dl>
    </div>
  );
}

export function ScenariosSection({ d }: { d: OpportunityDetail }) {
  const [open, setOpen] = useState<Scenario["name"] | null>("expected");
  return (
    <Section id="profit" icon={<Calculator />} title="Profit scenarios" description="All configured costs included: buyer protection, shipping, packaging, fees.">
      {d.scenarios.length === 0 ? (
        <p className="text-sm text-fg-3">No resale estimate available.</p>
      ) : (
        <div className="space-y-2">
          {d.scenarios.map((s) => (
            <div key={s.name} className={cn("rounded-xl border", s.name === "expected" ? "border-accent/40 bg-accent-soft/40" : "border-line")}>
              <button className="grid w-full grid-cols-[1fr_auto_auto_auto] items-center gap-3 px-3.5 py-3 text-left sm:gap-6" onClick={() => setOpen(open === s.name ? null : s.name)} aria-expanded={open === s.name}>
                <span>
                  <span className="block text-[13px] font-semibold text-fg">{SCENARIO_LABEL[s.name]}</span>
                  <span className="block text-xs text-fg-3">
                    Sell at {eur(s.sale_price)}
                    {s.estimated_days !== null && ` · ${days(s.estimated_days)}`}
                  </span>
                </span>
                <span className="text-right">
                  <span className={cn("block text-[15px] font-semibold tnum", s.net_profit >= 0 ? "text-success" : "text-danger")}>{eur(s.net_profit, { sign: true })}</span>
                  <span className="block text-[11px] text-fg-3">profit</span>
                </span>
                <span className="text-right">
                  <span className={cn("block text-[15px] font-semibold tnum", s.roi >= 0 ? "text-success" : "text-danger")}>{pct(s.roi)}</span>
                  <span className="block text-[11px] text-fg-3">ROI</span>
                </span>
                <ChevronDown className={cn("size-4 text-fg-3 transition-transform", open === s.name && "rotate-180")} />
              </button>
              {open === s.name && (
                <div className="grid grid-cols-1 gap-4 border-t border-line px-3.5 py-3 sm:grid-cols-2">
                  <Breakdown title="Acquisition" lines={s.acquisition_breakdown} total={s.total_acquisition_cost} totalLabel="Total acquisition cost" />
                  <Breakdown
                    title="Sale"
                    lines={[{ label: "Prezzo di vendita", amount: s.sale_price }, ...s.sale_breakdown.map((l) => ({ ...l, amount: -l.amount }))]}
                    total={s.net_sale_revenue}
                    totalLabel="Net sale revenue"
                  />
                </div>
              )}
            </div>
          ))}
        </div>
      )}
      <ProfitCalculator d={d} />
    </Section>
  );
}

function ProfitCalculator({ d }: { d: OpportunityDetail }) {
  const [buy, setBuy] = useState(String(d.card.listing_price));
  const [sell, setSell] = useState(String(d.card.expected_sale_price ?? ""));
  const [result, setResult] = useState<{ net_profit: number; roi: number; total_acquisition_cost: number; net_sale_revenue: number; max_buy_price: number | null } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  async function calc() {
    setLoading(true);
    setError(null);
    try {
      setResult(
        await api("/profit/calculate", {
          method: "POST",
          body: { purchase_price: Number(buy.replace(",", ".")), sale_price: Number(sell.replace(",", ".")), shipping_fee: d.listing.shipping_fee },
        }),
      );
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setLoading(false);
    }
  }
  return (
    <div className="mt-5 rounded-xl bg-surface-2 p-4">
      <p className="mb-3 text-[13px] font-semibold text-fg">What-if calculator</p>
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-[1fr_1fr_auto] sm:items-end">
        <div>
          <Label htmlFor="calc-buy">If I pay</Label>
          <InputAffix id="calc-buy" prefix="€" inputMode="decimal" value={buy} onChange={(e) => setBuy(e.target.value)} />
        </div>
        <div>
          <Label htmlFor="calc-sell">and sell at</Label>
          <InputAffix id="calc-sell" prefix="€" inputMode="decimal" value={sell} onChange={(e) => setSell(e.target.value)} />
        </div>
        <Button onClick={calc} loading={loading} className="col-span-2 sm:col-span-1">
          Calculate
        </Button>
      </div>
      {error && <p className="mt-2 text-[13px] text-danger">{error}</p>}
      {result && (
        <div className="mt-3 grid grid-cols-2 gap-3 sm:grid-cols-4">
          <KV label="Total cost" value={eur(result.total_acquisition_cost)} />
          <KV label="Net revenue" value={eur(result.net_sale_revenue)} />
          <KV label="Net profit" value={<span className={result.net_profit >= 0 ? "text-success" : "text-danger"}>{eur(result.net_profit, { sign: true })}</span>} />
          <KV label="ROI" value={<span className={result.roi >= 0 ? "text-success" : "text-danger"}>{pct(result.roi)}</span>} sub={result.max_buy_price ? `max buy ${eur(result.max_buy_price)}` : undefined} />
        </div>
      )}
    </div>
  );
}

/* --------------------------------------------------------------- Demand */
export function DemandSection({ d }: { d: OpportunityDetail }) {
  const v = d.velocity;
  return (
    <Section id="demand" icon={<Clock />} title="Demand & resale time">
      <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
        <KV label="Demand" value={d.demand.level ? DEMAND_LABEL[d.demand.level] : "—"} sub={d.demand.observations !== undefined ? `${d.demand.observations} comparable listings` : undefined} strong />
        <KV label="Sell-through rate" value={pct(d.demand.sell_through_rate)} sub="sold / (sold + still listed)" strong />
        <KV label="Estimated resale time" value={v.bucket ? `${v.bucket} days` : "—"} sub={v.estimated_days !== null ? `${days(v.estimated_days)} at the expected price` : undefined} strong />
        <KV label="Velocity score" value={`${v.score ?? "—"}/100`} sub={v.sample_size !== undefined ? `${v.sample_size} observed sales` : undefined} strong />
      </div>
      {v.quick_sale_days !== undefined && (
        <p className="mt-3 text-[13px] text-fg-2">
          Quick sale (lower price): {days(v.quick_sale_days)} · Optimistic price: {days(v.optimistic_sale_days)}
        </p>
      )}
    </Section>
  );
}

/* --------------------------------------------------------------- Risk */
const SEVERITY_ICON = { high: CircleX, medium: AlertTriangle, low: Info, info: Info };
const SEVERITY_TONE = { high: "text-danger", medium: "text-serious", low: "text-warning", info: "text-fg-3" };

export function RiskSection({ d }: { d: OpportunityDetail }) {
  return (
    <Section id="risk" icon={<AlertTriangle />} title="Risk analysis" action={<RiskBadge level={d.risk.level} score={d.risk.score} />}>
      {d.risk.factors.length === 0 ? (
        <p className="flex items-center gap-2 text-sm text-fg-2">
          <CircleCheck className="size-4 text-success" /> No risk signals detected.
        </p>
      ) : (
        <ul className="space-y-2">
          {d.risk.factors.map((f) => {
            const Icon = SEVERITY_ICON[f.severity] ?? Info;
            return (
              <li key={f.code + f.label} className="flex items-start gap-2.5">
                <Icon className={cn("mt-0.5 size-4 shrink-0", SEVERITY_TONE[f.severity])} aria-hidden />
                <span className="flex-1 text-[14px] text-fg">{f.label}</span>
                <span className="text-xs text-fg-3 tnum">+{f.points}</span>
              </li>
            );
          })}
        </ul>
      )}
      <p className="mt-3 text-xs text-fg-3">FlipFinder never certifies authenticity: verify labels, stitching and details before buying high-value items.</p>
    </Section>
  );
}

/* --------------------------------------------------------------- Seller */
export function SellerSection({ d }: { d: OpportunityDetail }) {
  const s = d.listing.seller;
  if (!s) {
    return (
      <Section id="seller" icon={<Store />} title="Seller">
        <p className="text-sm text-fg-3">Seller information is not available for this listing.</p>
      </Section>
    );
  }
  const score = s.reliability_score ?? 50;
  return (
    <Section id="seller" icon={<Store />} title="Seller analysis" description={s.username ?? undefined}>
      <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
        <KV label="Rating" value={s.rating !== null ? <span className="inline-flex items-center gap-1">{s.rating.toFixed(1)} <Star className="size-3.5 fill-current text-warning" /></span> : "No reviews"} sub={`${s.review_count} reviews`} />
        <KV label="Member since" value={shortDate(s.account_created_at)} />
        <KV label="Items / sold" value={`${s.item_count ?? "—"} / ${s.sold_count ?? "—"}`} />
        <div>
          <p className="text-xs text-fg-3">Reliability</p>
          <p className="text-[15px] font-semibold tnum" style={{ color: scoreColor(score) }}>
            {score}/100
          </p>
          <Meter value={score} className="mt-1" />
        </div>
      </div>
      {s.reliability?.factors?.length ? (
        <ul className="mt-3 list-disc space-y-1 pl-4 text-[13px] text-fg-2 marker:text-fg-3">
          {s.reliability.factors.map((f) => (
            <li key={f.label}>{f.label}</li>
          ))}
        </ul>
      ) : null}
    </Section>
  );
}

/* --------------------------------------------------------------- Identification */
const CERTAINTY_TONE = { certain: "success", probable: "accent", unverifiable: "neutral" } as const;
const ATTRS: [keyof NonNullable<OpportunityDetail["identification"]>, string][] = [
  ["brand", "Brand"],
  ["line", "Line"],
  ["model", "Model"],
  ["category", "Category"],
  ["gender", "Gender"],
  ["size", "Size"],
  ["color", "Color"],
  ["material", "Material"],
  ["season", "Season"],
  ["seasonality", "Seasonality"],
  ["product_code", "Product code"],
  ["authenticity", "Authenticity"],
];

export function IdentificationSection({ d }: { d: OpportunityDetail }) {
  const id = d.identification;
  if (!id) return null;
  return (
    <Section
      id="identification"
      icon={<ScanSearch />}
      title="Product identification"
      description="What the engine understood from title, description, structured fields and photos."
      action={<Badge tone="outline">Confidence {id.confidence}%</Badge>}
    >
      <div className="grid grid-cols-1 gap-x-6 sm:grid-cols-2">
        {ATTRS.map(([key, label]) => {
          const a = id[key] as Attribute | undefined;
          if (!a || (!a.value && key !== "brand" && key !== "authenticity")) return null;
          const value = key === "brand" ? (a.name ?? a.value) : a.value;
          return (
            <div key={key} className="flex items-center justify-between gap-3 border-b border-line py-2">
              <span className="text-[13px] text-fg-3">{label}</span>
              <span className="flex items-center gap-2 text-right">
                <span className="text-[13px] font-medium text-fg">{value ? String(value).replaceAll("_", " ").replaceAll("-", " ") : "Not identified"}</span>
                <Tip content={`Source: ${a.source ?? "—"}`}>
                  <span>
                    <Badge tone={CERTAINTY_TONE[a.certainty]}>{a.certainty}</Badge>
                  </span>
                </Tip>
              </span>
            </div>
          );
        })}
      </div>
      {(id.evidence.length > 0 || id.defect_terms.length > 0 || id.is_vintage) && (
        <ul className="mt-3 list-disc space-y-1 pl-4 text-[13px] text-fg-2 marker:text-fg-3">
          {id.is_vintage && <li>Vintage item</li>}
          {id.evidence.map((e) => (
            <li key={e}>{e}</li>
          ))}
          {id.defect_terms.length > 0 && <li className="text-serious">Declared defects: {id.defect_terms.join(", ")}</li>}
          {id.photos_reused_by_other_seller && <li className="text-danger">Photos also used by a different seller</li>}
        </ul>
      )}
    </Section>
  );
}

/* --------------------------------------------------------------- Comparables */
export function ComparablesSection({ items }: { items: Comparable[] }) {
  const [showAll, setShowAll] = useState(false);
  const shown = showAll ? items : items.slice(0, 12);
  return (
    <Section id="comparables" icon={<BadgeCheck />} title="Comparable listings" description="The listings used to estimate the price (prices adjusted to this item's condition).">
      <ul className="-mx-2 divide-y divide-line sm:hidden">
        {shown.map((c) => (
          <li key={c.listing_id} className={cn(!c.included && "opacity-50")}>
            <a href={c.url} target="_blank" rel="noopener noreferrer" className="flex items-center gap-3 rounded-lg px-2 py-2.5 active:bg-surface-2">
              <ListingImage src={c.image_url} alt={c.title} className="size-11 shrink-0 rounded-md" />
              <div className="min-w-0 flex-1">
                <p className="truncate text-[13px] text-fg">{c.title}</p>
                <p className="truncate text-xs text-fg-3">
                  {[CONDITION_LABEL[c.condition] ?? c.condition, c.size, shortDate(c.sold_at ?? c.listing_date)].filter(Boolean).join(" · ")}
                </p>
              </div>
              <div className="shrink-0 text-right">
                <p className="text-[13px] font-semibold tnum text-fg">{eur(c.price)}</p>
                <p className="text-[11px] text-fg-3">
                  {!c.included ? "Outlier" : c.is_sold ? "Sold" : "Listed"} · {Math.round(c.similarity * 100)}%
                </p>
              </div>
            </a>
          </li>
        ))}
      </ul>
      <div className="-mx-5 hidden overflow-x-auto sm:block">
        <table className="w-full min-w-[720px] text-left text-[13px]">
          <thead>
            <tr className="border-b border-line text-xs text-fg-3">
              <th className="px-5 py-2 font-medium">Product</th>
              <th className="px-2 py-2 text-right font-medium">Price</th>
              <th className="px-2 py-2 text-right font-medium">Adjusted</th>
              <th className="px-2 py-2 font-medium">Condition</th>
              <th className="px-2 py-2 font-medium">Size</th>
              <th className="px-2 py-2 font-medium">Country</th>
              <th className="px-2 py-2 font-medium">Status</th>
              <th className="px-2 py-2 text-right font-medium">Similarity</th>
              <th className="px-5 py-2 font-medium">Date</th>
            </tr>
          </thead>
          <tbody>
            {shown.map((c) => (
              <tr key={c.listing_id} className={cn("border-b border-line last:border-0", !c.included && "opacity-50")}>
                <td className="px-5 py-2">
                  <a href={c.url} target="_blank" rel="noopener noreferrer" className="flex items-center gap-2.5 hover:underline">
                    <ListingImage src={c.image_url} alt={c.title} className="size-9 shrink-0 rounded-md" />
                    <span className="line-clamp-1 max-w-[220px] text-fg">{c.title}</span>
                  </a>
                </td>
                <td className="px-2 py-2 text-right tnum">{eur(c.price)}</td>
                <td className="px-2 py-2 text-right tnum text-fg-2">{eur(c.adjusted_price)}</td>
                <td className="px-2 py-2 text-fg-2">{CONDITION_LABEL[c.condition] ?? c.condition}</td>
                <td className="px-2 py-2 text-fg-2">{c.size ?? "—"}</td>
                <td className="px-2 py-2 text-fg-2">{c.country ?? "—"}</td>
                <td className="px-2 py-2">
                  {!c.included ? (
                    <Badge tone="neutral">Outlier</Badge>
                  ) : c.is_sold ? (
                    <Badge tone="success">Sold</Badge>
                  ) : (
                    <Badge tone="outline">Listed</Badge>
                  )}
                </td>
                <td className="px-2 py-2 text-right font-medium tnum">{Math.round(c.similarity * 100)}%</td>
                <td className="px-5 py-2 text-fg-3">{shortDate(c.sold_at ?? c.listing_date)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {items.length > 12 && (
        <Button variant="ghost" size="sm" className="mt-3" onClick={() => setShowAll(!showAll)}>
          {showAll ? "Show less" : `Show all ${items.length}`}
        </Button>
      )}
    </Section>
  );
}

/* --------------------------------------------------------------- AI */
export function AiSection({ d }: { d: OpportunityDetail }) {
  const run = useRunAi(d.card.id);
  const a = d.ai_analysis;
  return (
    <Section
      id="ai"
      icon={<Bot />}
      title="AI Deal Analyst"
      description={a ? `${a.provider === "claude" ? `Claude (${a.model})` : "Rule-based analyst"} · numbers come from the statistical engine` : undefined}
      action={
        <Button variant="outline" size="sm" onClick={() => run.mutate()} loading={run.isPending}>
          <RefreshCw /> Re-run
        </Button>
      }
    >
      {!a ? (
        <p className="text-sm text-fg-3">No AI analysis yet.</p>
      ) : (
        <div className="space-y-4">
          <div className="flex items-start gap-3">
            <Badge tone={a.verdict === "BUY" ? "success" : a.verdict === "CONSIDER" ? "warning" : "danger"} className="px-3 py-1 text-[13px]">
              {a.verdict}
            </Badge>
            <p className="text-[14px] leading-relaxed text-fg">{a.summary}</p>
          </div>
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
            {(
              [
                ["Pros", a.pros, "text-success", CirclePlus],
                ["Cons", a.cons, "text-danger", CircleMinus],
                ["Risks", a.risks, "text-serious", AlertTriangle],
              ] as const
            ).map(([title, list, tone, Icon]) => (
              <div key={title}>
                <p className="mb-1.5 text-xs font-semibold uppercase tracking-[0.08em] text-fg-3">{title}</p>
                {list.length ? (
                  <ul className="space-y-1.5">
                    {list.map((x) => (
                      <li key={x} className="flex gap-2 text-[13px] text-fg-2">
                        <Icon className={cn("mt-0.5 size-3.5 shrink-0", tone)} aria-hidden />
                        {x}
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p className="text-[13px] text-fg-3">—</p>
                )}
              </div>
            ))}
          </div>
          <div className="flex flex-wrap gap-6 border-t border-line pt-3">
            <KV label="Recommended resale price" value={eur(a.recommended_resale_price)} />
            <KV label="Suggested maximum offer" value={eur(a.suggested_max_offer)} />
          </div>
        </div>
      )}
    </Section>
  );
}

/* --------------------------------------------------------------- History */
export function HistorySection({ d }: { d: OpportunityDetail }) {
  const pts = d.price_history;
  return (
    <Section id="history" icon={<TrendingUp />} title="Price history">
      {pts.length > 1 ? (
        <PriceHistoryChart points={pts} />
      ) : (
        <p className="text-sm text-fg-3">
          No price changes since {shortDate(pts[0]?.observed_at)} ({eur(pts[0]?.price)}). Enable price-drop alerts to know when it gets cheaper.
        </p>
      )}
    </Section>
  );
}
