"use client";

import { ArrowLeft, Bookmark, BookmarkCheck, Eye, EyeOff, ExternalLink, Flame, Heart, History, ShoppingBag, TrendingDown } from "lucide-react";
import Link from "next/link";
import { use, useEffect, useRef, useState, type CSSProperties } from "react";
import {
  AiSection,
  ComparablesSection,
  DemandSection,
  ExplanationSection,
  HistorySection,
  IdentificationSection,
  MarketSection,
  OfferCard,
  RiskSection,
  ScenariosSection,
  SellerSection,
} from "@/components/deal/detail-sections";
import { DataQualityBanner } from "@/components/deal/analysis-detail";
import { ListingImage } from "@/components/deal/listing-image";
import { RiskBadge, ScoreRing } from "@/components/deal/score";
import { PurchaseDialog } from "@/components/forms/flip-forms";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { AnimatedNumber } from "@/components/ui/motion";
import { ErrorState, Skeleton } from "@/components/ui/feedback";
import { errorMessage } from "@/lib/api";
import { CONDITION_LABEL, DEMAND_LABEL, TIER_LABEL, days, eur, pct, timeAgo } from "@/lib/format";
import { useOpportunity, useSetFavorite } from "@/lib/queries";
import type { OpportunityDetail } from "@/lib/types";
import { cn } from "@/lib/utils";

const NAV = [
  ["why", "Why"],
  ["offer", "Action"],
  ["market", "Market"],
  ["profit", "Profit"],
  ["comparables", "Comparables"],
  ["demand", "Demand"],
  ["risk", "Risk"],
  ["seller", "Seller"],
  ["identification", "Product"],
  ["ai", "AI analysis"],
] as const;

function Gallery({ d }: { d: OpportunityDetail }) {
  const images = d.listing.images.length ? d.listing.images : [{ url: d.card.image_url ?? "", position: 0 }];
  const [active, setActive] = useState(0);
  return (
    <div>
      <div className="group relative aspect-square overflow-hidden rounded-2xl border border-line bg-surface-2">
        <ListingImage key={active} src={images[active]?.url || null} alt={d.listing.title} className="zoom-on-hover h-full w-full animate-fade-in" />
        {d.card.is_ultra_deal && (
          <Badge tone="ultra" className="absolute left-3 top-3 px-2.5 py-1 text-xs">
            <Flame /> ULTRA DEAL
          </Badge>
        )}
      </div>
      {images.length > 1 && (
        <div className="scrollbar-none mt-2 flex gap-2 overflow-x-auto">
          {images.map((img, i) => (
            <button
              key={img.url + i}
              onClick={() => setActive(i)}
              className={cn(
                "press size-16 shrink-0 overflow-hidden rounded-lg border-2 transition-[border-color,opacity,transform] duration-200",
                i === active ? "border-accent" : "border-transparent opacity-60 hover:opacity-100",
              )}
              aria-label={`Photo ${i + 1}`}
            >
              <ListingImage src={img.url} alt="" className="h-full w-full" />
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

function Summary({ d }: { d: OpportunityDetail }) {
  const c = d.card;
  const setFav = useSetFavorite();
  const state = c.favorite_state;
  const positive = (c.expected_profit ?? 0) > 0;
  return (
    <Card className={cn("enter p-5", c.is_ultra_deal && "ultra-border border-transparent")}>
      <div className="flex items-start justify-between gap-4">
        <div>
          <p className="text-xs font-medium text-fg-3">{c.data_quality === "insufficient" ? "Insufficient data" : TIER_LABEL[c.deal_tier]}</p>
          <div className="mt-2 flex flex-wrap items-center gap-1.5">
            <Badge tone="outline">Confidence {c.confidence_score}</Badge>
            <RiskBadge level={c.risk_level} score={c.risk_score} />
            {c.personal_flip_score !== null && c.personal_flip_score !== c.flip_score && (
              <Badge tone="accent">Personal {c.personal_flip_score}</Badge>
            )}
          </div>
        </div>
        {c.data_quality === "insufficient" ? (
          <span className="rounded-xl bg-warning-soft px-3 py-2 text-center text-[12px] font-semibold leading-tight text-warning">
            No score
            <br />
            <span className="font-normal">too few comparables</span>
          </span>
        ) : (
          <ScoreRing score={c.flip_score} size={76} stroke={6} label="Flip" />
        )}
      </div>

      <div className="mt-5 grid grid-cols-2 gap-4 rounded-xl bg-surface-2 p-4">
        <div>
          <p className="text-[11px] font-semibold uppercase tracking-[0.08em] text-fg-3">Buy</p>
          <p className="text-[26px] font-semibold leading-tight tracking-tight tnum">{eur(c.listing_price)}</p>
          {c.previous_price && (
            <p className="flex items-center gap-1 text-xs text-success">
              <TrendingDown className="size-3" /> was {eur(c.previous_price)}
            </p>
          )}
        </div>
        <div>
          <p className="text-[11px] font-semibold uppercase tracking-[0.08em] text-fg-3">Sell</p>
          <p className="text-[26px] font-semibold leading-tight tracking-tight tnum">{eur(c.expected_sale_price)}</p>
          <p className="text-xs text-fg-3">market value {eur(c.fair_market_value)}</p>
        </div>
        <div>
          <p className="text-[11px] font-semibold uppercase tracking-[0.08em] text-fg-3">Net profit</p>
          <p className={cn("text-[22px] font-semibold tnum", positive ? "text-success" : "text-danger")}>
            {c.expected_profit !== null ? <AnimatedNumber value={c.expected_profit} format={(v) => eur(v, { sign: true })} /> : "—"}
          </p>
          <p className="text-xs text-fg-3">total cost {eur(c.total_acquisition_cost)}</p>
        </div>
        <div>
          <p className="text-[11px] font-semibold uppercase tracking-[0.08em] text-fg-3">ROI</p>
          <p className={cn("text-[22px] font-semibold tnum", positive ? "text-success" : "text-danger")}>
            {c.expected_roi !== null ? <AnimatedNumber value={c.expected_roi} format={(v) => pct(v)} /> : "—"}
          </p>
          {c.discount_vs_market !== null && <p className="text-xs text-fg-3">{pct(-c.discount_vs_market, { sign: true, digits: 1 })} vs market</p>}
        </div>
      </div>

      <dl className="mt-4 grid grid-cols-3 gap-2 text-center">
        <div className="rounded-xl border border-line p-2">
          <dt className="text-[11px] text-fg-3">Demand</dt>
          <dd className="text-[13px] font-semibold">{c.demand_level ? DEMAND_LABEL[c.demand_level] : "—"}</dd>
        </div>
        <div className="rounded-xl border border-line p-2">
          <dt className="text-[11px] text-fg-3">Resale time</dt>
          <dd className="text-[13px] font-semibold">{days(c.estimated_days_to_sell)}</dd>
        </div>
        <div className="rounded-xl border border-line p-2">
          <dt className="text-[11px] text-fg-3">Velocity</dt>
          <dd className="text-[13px] font-semibold">{c.velocity_score ?? "—"}/100</dd>
        </div>
      </dl>

      <Button asChild variant={c.is_ultra_deal ? "ultra" : "primary"} size="lg" className="mt-5 w-full">
        <a href={c.url} target="_blank" rel="noopener noreferrer">
          {c.is_ultra_deal ? <Flame /> : <ExternalLink />} VIEW DEAL on {d.listing.provider === "mock" ? "Vinted (demo)" : "the marketplace"}
        </a>
      </Button>
      <div className="mt-2 grid grid-cols-2 gap-2">
        <Button variant="outline" size="sm" onClick={() => setFav.mutate({ id: c.id, state: state === "saved" ? null : "saved" })}>
          {state === "saved" ? <BookmarkCheck className="text-accent" /> : <Bookmark />} {state === "saved" ? "Saved" : "Save"}
        </Button>
        <Button variant="outline" size="sm" onClick={() => setFav.mutate({ id: c.id, state: state === "watching" ? null : "watching" })}>
          <Eye className={state === "watching" ? "text-accent" : ""} /> {state === "watching" ? "Watching" : "Watch"}
        </Button>
        <PurchaseDialog
          defaults={{ title: d.listing.title, opportunity_id: c.id, purchase_price: c.listing_price, expected_sale_price: c.expected_sale_price }}
          trigger={
            <Button variant="outline" size="sm">
              <ShoppingBag /> I bought it
            </Button>
          }
        />
        <Button variant="ghost" size="sm" onClick={() => setFav.mutate({ id: c.id, state: state === "ignored" ? null : "ignored" })}>
          <EyeOff /> {state === "ignored" ? "Ignored" : "Ignore"}
        </Button>
      </div>
      <Button asChild variant="ghost" size="sm" className="mt-2 w-full">
        <Link href={`/items/${c.listing_id}`}>
          <History /> Tracking, photos & history
        </Link>
      </Button>
      {!c.is_active && <p className="mt-3 rounded-lg bg-warning-soft px-3 py-2 text-[13px] text-warning">This listing is no longer available ({c.listing_status}).</p>}
    </Card>
  );
}

/** Section links that follow the reader: the section in view is highlighted. */
function SectionNav() {
  const [current, setCurrent] = useState<string>(NAV[0][0]);
  const navRef = useRef<HTMLElement>(null);
  useEffect(() => {
    const visible = new Map<string, number>();
    const io = new IntersectionObserver(
      (entries) => {
        for (const e of entries) visible.set(e.target.id, e.isIntersecting ? e.boundingClientRect.top : Infinity);
        const top = [...visible.entries()].filter(([, y]) => y !== Infinity).sort((a, b) => a[1] - b[1])[0];
        if (top) setCurrent(top[0]);
      },
      { rootMargin: "-120px 0px -55% 0px" },
    );
    NAV.forEach(([id]) => {
      const el = document.getElementById(id);
      if (el) io.observe(el);
    });
    return () => io.disconnect();
  }, []);
  useEffect(() => {
    // Keep the highlighted link visible in the horizontally scrolling bar. Scroll only the bar:
    // scrollIntoView would also move the page.
    const nav = navRef.current;
    const link = nav?.querySelector<HTMLElement>(`[data-section="${current}"]`);
    if (!nav || !link) return;
    const left = link.offsetLeft - nav.clientWidth / 2 + link.offsetWidth / 2;
    nav.scrollTo({ left: Math.max(0, left), behavior: "smooth" });
  }, [current]);
  return (
    <nav
      ref={navRef}
      className="scrollbar-none sticky top-14 z-30 -mx-4 flex gap-1 overflow-x-auto border-b border-line bg-bg/80 px-4 py-2 backdrop-blur-xl backdrop-saturate-150 sm:mx-0 sm:rounded-xl sm:border sm:px-2"
      aria-label="Sections"
    >
      {NAV.map(([href, label]) => (
        <a
          key={href}
          href={`#${href}`}
          data-section={href}
          aria-current={current === href ? "true" : undefined}
          className={cn(
            "press rounded-lg px-2.5 py-1 text-[13px] font-medium whitespace-nowrap transition-[background-color,color,transform] duration-200",
            current === href ? "bg-surface text-fg shadow-card" : "text-fg-2 hover:bg-surface-2 hover:text-fg",
          )}
        >
          {label}
        </a>
      ))}
    </nav>
  );
}

export default function DealPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const q = useOpportunity(id);
  if (q.isLoading) {
    return (
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-[minmax(0,1fr)_380px]">
        <div className="space-y-4">
          <Skeleton className="h-8 w-2/3" />
          <Skeleton className="aspect-square max-w-md rounded-2xl" />
          <Skeleton className="h-64 rounded-2xl" />
        </div>
        <Skeleton className="h-[520px] rounded-2xl" />
      </div>
    );
  }
  if (q.isError || !q.data) return <ErrorState message={errorMessage(q.error)} onRetry={() => q.refetch()} />;
  const d = q.data;
  const c = d.card;
  return (
    <div className="pb-20 lg:pb-0">
      <Link href="/deals" className="group inline-flex items-center gap-1.5 text-[13px] font-medium text-fg-3 transition-colors hover:text-fg">
        <ArrowLeft className="size-4 transition-transform duration-200 group-hover:-translate-x-0.5" /> Back to deals
      </Link>

      <div className="mt-3 grid grid-cols-1 gap-6 lg:grid-cols-[minmax(0,1fr)_380px]">
        <div className="min-w-0 space-y-5">
          <header className="grid grid-cols-1 gap-5 md:grid-cols-[minmax(0,320px)_1fr]">
            <div className="enter">
              <Gallery d={d} />
            </div>
            <div className="enter min-w-0" style={{ "--i": 1 } as CSSProperties}>
              <div className="flex flex-wrap gap-1.5">
                {c.brand && <Badge tone="neutral">{c.brand.name}</Badge>}
                {c.model_name && <Badge tone="neutral">{c.model_name}</Badge>}
                {c.category && <Badge tone="neutral">{c.category.name}</Badge>}
                {c.size && <Badge tone="neutral">Size {c.size}</Badge>}
                <Badge tone="neutral">{CONDITION_LABEL[c.condition]}</Badge>
                {d.listing.is_vintage && <Badge tone="accent">Vintage</Badge>}
              </div>
              <h1 className="mt-3 text-[22px] font-semibold leading-tight tracking-[-0.02em] sm:text-[28px]">{d.listing.title}</h1>
              <p className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-[13px] text-fg-3">
                <span>Listed {timeAgo(d.listing.published_at)}</span>
                <span className="inline-flex items-center gap-1">
                  <Heart className="size-3.5" /> {d.listing.favourite_count}
                </span>
                <span className="inline-flex items-center gap-1">
                  <Eye className="size-3.5" /> {d.listing.view_count}
                </span>
                {d.listing.country && <span>{d.listing.country}</span>}
                {d.listing.shipping_fee !== null && <span>Shipping {eur(d.listing.shipping_fee)}</span>}
              </p>
              {d.listing.description && <p className="mt-4 whitespace-pre-line text-[14px] leading-relaxed text-fg-2">{d.listing.description}</p>}
              {d.ai_analysis && (
                <div className="highlight mt-4 rounded-xl border border-line bg-surface p-3.5">
                  <p className="text-xs font-semibold uppercase tracking-[0.08em] text-fg-3">Verdict · {d.ai_analysis.verdict}</p>
                  <p className="mt-1 text-[14px] leading-relaxed text-fg">{d.ai_analysis.summary}</p>
                </div>
              )}
            </div>
          </header>

          <SectionNav />

          <div className="lg:hidden">
            <Summary d={d} />
          </div>
          <DataQualityBanner quality={d.score.data_quality} reason={d.score.insufficient_reason} />
          <ExplanationSection d={d} />
          <OfferCard d={d} />
          <MarketSection d={d} />
          <ScenariosSection d={d} />
          <ComparablesSection items={d.comparables} />
          <DemandSection d={d} />
          <RiskSection d={d} />
          <SellerSection d={d} />
          <IdentificationSection d={d} />
          <AiSection d={d} />
          <HistorySection d={d} />
        </div>
        <aside className="hidden lg:block">
          <div className="sticky top-20">
            <Summary d={d} />
          </div>
        </aside>
      </div>

      <div className="fixed inset-x-0 bottom-[57px] z-30 border-t border-line bg-bg/85 px-4 py-2.5 backdrop-blur-xl backdrop-saturate-150 [animation:sheet-in_0.45s_var(--ease-drawer)_0.2s_backwards] lg:hidden">
        <div className="mx-auto flex max-w-md items-center gap-3">
          <div className="min-w-0 flex-1">
            <p className="text-[11px] text-fg-3">
              Buy {eur(c.listing_price)} → sell {eur(c.expected_sale_price)}
            </p>
            <p className={cn("text-sm font-semibold", (c.expected_profit ?? 0) > 0 ? "text-success" : "text-danger")}>
              {eur(c.expected_profit, { sign: true })} · ROI {pct(c.expected_roi)}
            </p>
          </div>
          <Button asChild variant={c.is_ultra_deal ? "ultra" : "primary"}>
            <a href={c.url} target="_blank" rel="noopener noreferrer">
              <ExternalLink /> VIEW DEAL
            </a>
          </Button>
        </div>
      </div>
    </div>
  );
}
