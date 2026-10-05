"use client";

import { ArrowRight, BellRing, Flame, Gauge, Layers, Radar, RefreshCw, ScanSearch, Sparkles, Timer, TrendingDown, TrendingUp, Wallet } from "lucide-react";
import Link from "next/link";
import { DealCard, DealCardSkeleton, DealRow } from "@/components/deal/deal-card";
import { GlobalSearch, ScanStatus } from "@/components/layout/app-shell";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { EmptyState, SectionHeader } from "@/components/ui/feedback";
import { StatTile } from "@/components/ui/stat";
import { compactNum, eur, pct, plural, timeAgo } from "@/lib/format";
import { useAlerts, useInsights, useMe, useOpportunities, useQuickStats, useTriggerScan } from "@/lib/queries";
import type { OpportunityCard } from "@/lib/types";

const EXAMPLES = [
  "felpe Ralph Lauren sotto 25 euro con almeno 50% ROI",
  "sneakers nike 42 profitto almeno 15",
  "maglie calcio vintage",
  "piumini basso rischio",
];

function DealGrid({ items, loading, count = 8 }: { items?: OpportunityCard[]; loading: boolean; count?: number }) {
  if (loading && !items) {
    return (
      <div className="grid grid-cols-1 gap-4 min-[480px]:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
        {Array.from({ length: count }).map((_, i) => (
          <DealCardSkeleton key={i} />
        ))}
      </div>
    );
  }
  if (!items?.length) {
    return (
      <EmptyState
        icon={<Radar />}
        title="No opportunities yet"
        description="The scanner is analysing the market. New deals appear here automatically as soon as they are found."
      />
    );
  }
  return (
    <div className="grid grid-cols-1 gap-4 min-[480px]:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
      {items.map((d, i) => (
        <div key={d.id} className="animate-fade-up" style={{ animationDelay: `${Math.min(i, 8) * 30}ms` }}>
          <DealCard deal={d} />
        </div>
      ))}
    </div>
  );
}

function ListCard({ title, icon, items, metric, href, loading }: { title: string; icon: React.ReactNode; items?: OpportunityCard[]; metric: "roi" | "velocity" | "profit" | "new"; href: string; loading: boolean }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2 [&_svg]:size-4 [&_svg]:text-fg-3">
          {icon}
          {title}
        </CardTitle>
        <Link href={href} className="text-[13px] font-medium text-accent hover:underline">
          See all
        </Link>
      </CardHeader>
      <CardContent className="pt-2">
        {loading && !items ? (
          <div className="space-y-2">
            {Array.from({ length: 4 }).map((_, i) => (
              <div key={i} className="skeleton h-14 rounded-xl" />
            ))}
          </div>
        ) : items?.length ? (
          <div className="-mx-2">
            {items.map((d) => (
              <DealRow key={d.id} deal={d} metric={metric} />
            ))}
          </div>
        ) : (
          <p className="py-6 text-center text-[13px] text-fg-3">Nothing here right now.</p>
        )}
      </CardContent>
    </Card>
  );
}

export default function DashboardPage() {
  const { data: me } = useMe();
  const stats = useQuickStats();
  const top = useOpportunities({ preset: "best_deals", page_size: 14 }, { live: true });
  const ultra = useOpportunities({ preset: "ultra", page_size: 6 }, { live: true });
  const roi = useOpportunities({ preset: "high_roi", page_size: 5 }, { live: true });
  const fast = useOpportunities({ preset: "fast_flip", page_size: 5 }, { live: true });
  const recent = useOpportunities({ preset: "just_listed", min_flip: 50, page_size: 5 }, { live: true });
  const alerts = useAlerts({ page: 1 });
  const insights = useInsights();
  const scan = useTriggerScan();
  const s = stats.data;
  const ultraIds = new Set((ultra.data?.items ?? []).map((d) => d.id));
  const topItems = top.data?.items.filter((d) => !ultraIds.has(d.id)).slice(0, 8);
  const hour = new Date().getHours();
  const greeting = hour < 12 ? "Good morning" : hour < 18 ? "Good afternoon" : "Good evening";

  return (
    <div className="space-y-8">
      <section className="flex flex-col gap-4 md:flex-row md:items-end md:justify-between">
        <div>
          <p className="text-[13px] font-medium text-fg-3">
            {greeting}
            {me?.display_name ? `, ${me.display_name}` : ""}
          </p>
          <h1 className="mt-1 text-2xl font-semibold tracking-tight sm:text-[28px]">Today&apos;s best flips</h1>
          <p className="mt-1 text-sm text-fg-2">Listings priced below their real market value, ranked by profit, ROI, demand and risk.</p>
        </div>
        <div className="flex items-center gap-2">
          <div className="xl:hidden">
            <ScanStatus />
          </div>
          <Button variant="outline" size="sm" onClick={() => scan.mutate()} loading={scan.isPending}>
            <RefreshCw /> Scan now
          </Button>
          <Button asChild size="sm">
            <Link href="/analyze">
              <ScanSearch /> Analyze a listing
            </Link>
          </Button>
        </div>
      </section>

      <section className="md:hidden">
        <GlobalSearch />
      </section>
      <section className="hidden flex-wrap items-center gap-2 md:flex">
        <span className="text-xs text-fg-3">Try:</span>
        {EXAMPLES.map((e) => (
          <Link key={e} href={`/search?q=${encodeURIComponent(e)}`} className="rounded-full border border-line bg-surface px-3 py-1 text-xs text-fg-2 transition-colors hover:border-accent hover:text-accent">
            {e}
          </Link>
        ))}
      </section>

      <section aria-label="Quick stats" className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-5">
        <StatTile label="Opportunities today" value={s ? s.opportunities_today.toLocaleString() : "—"} sub={s ? `${s.active_opportunities} active now` : undefined} icon={<Sparkles />} loading={stats.isLoading} />
        <StatTile label="Average expected ROI" value={pct(s?.average_expected_roi)} sub="on deals with Flip ≥ 60" icon={<TrendingUp />} tone="success" loading={stats.isLoading} />
        <StatTile label="Potential profit" value={eur(s?.potential_profit, { compact: true })} sub="sum of top deals (Flip ≥ 70)" icon={<Wallet />} tone="success" loading={stats.isLoading} />
        <StatTile label="Ultra deals found" value={s?.ultra_deals ?? "—"} sub="Flip > 90 · Conf. > 80 · ROI > 60%" icon={<Flame />} tone="ultra" loading={stats.isLoading} />
        <StatTile
          label="Listings analyzed"
          value={s ? compactNum(s.listings_analyzed) : "—"}
          sub={s ? `${compactNum(s.listings_tracked)} tracked in total` : undefined}
          icon={<Gauge />}
          loading={stats.isLoading}
          className="col-span-2 md:col-span-1"
        />
      </section>

      {(ultra.data?.items.length ?? 0) > 0 && (
        <section>
          <SectionHeader
            icon={<Flame className="text-ultra" />}
            title="Ultra Deals"
            description="Rare: Flip Score above 90 with high confidence and ROI above 60%. Move fast."
            action={
              <Link href="/deals?preset=ultra" className="inline-flex items-center gap-1 text-[13px] font-medium text-accent hover:underline">
                All ultra deals <ArrowRight className="size-3.5" />
              </Link>
            }
          />
          <div className="scrollbar-none -mx-4 flex snap-x gap-4 overflow-x-auto px-4 pb-2 sm:mx-0 sm:px-0">
            {ultra.data!.items.map((d) => (
              <div key={d.id} className="w-[280px] shrink-0 snap-start">
                <DealCard deal={d} />
              </div>
            ))}
          </div>
        </section>
      )}

      <section>
        <SectionHeader
          icon={<Sparkles className="text-accent" />}
          title="Top opportunities"
          description="Best balance of profit, ROI, demand, sales velocity and reliability."
          action={
            <Link href="/deals?preset=best_deals" className="inline-flex items-center gap-1 text-[13px] font-medium text-accent hover:underline">
              Explore all <ArrowRight className="size-3.5" />
            </Link>
          }
        />
        <DealGrid items={topItems} loading={top.isLoading} />
      </section>

      <section className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        <ListCard title="High ROI" icon={<TrendingUp />} items={roi.data?.items} metric="roi" href="/deals?preset=high_roi" loading={roi.isLoading} />
        <ListCard title="Fast flips" icon={<Timer />} items={fast.data?.items} metric="velocity" href="/deals?preset=fast_flip" loading={fast.isLoading} />
        <ListCard title="Recently listed" icon={<Radar />} items={recent.data?.items} metric="new" href="/deals?preset=just_listed" loading={recent.isLoading} />
      </section>

      <section className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <BellRing className="size-4 text-fg-3" /> Watchlist alerts
            </CardTitle>
            <Link href="/alerts" className="text-[13px] font-medium text-accent hover:underline">
              Open inbox
            </Link>
          </CardHeader>
          <CardContent className="pt-2">
            {alerts.data?.items.length ? (
              <ul className="-mx-2 divide-y divide-line">
                {alerts.data.items.slice(0, 5).map((a) => (
                  <li key={a.id}>
                    <Link href={a.opportunity_id ? `/deals/${a.opportunity_id}` : "/alerts"} className="flex items-start gap-3 rounded-lg px-2 py-2.5 hover:bg-surface-2">
                      <span className={`mt-1.5 size-2 shrink-0 rounded-full ${a.read_at ? "bg-surface-3" : a.priority === "high" ? "bg-ultra" : "bg-accent"}`} aria-hidden />
                      <span className="min-w-0 flex-1">
                        <span className="block truncate text-[13px] font-semibold text-fg">{a.title}</span>
                        <span className="block truncate text-xs text-fg-3">{a.body}</span>
                      </span>
                      <span className="shrink-0 text-[11px] text-fg-3">{timeAgo(a.created_at)}</span>
                    </Link>
                  </li>
                ))}
              </ul>
            ) : (
              <EmptyState
                className="border-0 py-6"
                icon={<BellRing />}
                title="No alerts yet"
                description="Create a watchlist and you will be notified as soon as a matching deal appears."
                action={
                  <Button asChild size="sm" variant="outline">
                    <Link href="/watchlists">Create a watchlist</Link>
                  </Button>
                }
              />
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Layers className="size-4 text-fg-3" /> Market insights
            </CardTitle>
            <Link href="/analytics" className="text-[13px] font-medium text-accent hover:underline">
              Analytics
            </Link>
          </CardHeader>
          <CardContent className="space-y-5 pt-3">
            <div>
              <p className="mb-2 text-xs font-medium text-fg-3">Best brands for flipping (30 days)</p>
              <ul className="space-y-2">
                {(insights.data?.top_brands ?? []).slice(0, 4).map((b) => (
                  <li key={b.slug} className="flex items-center gap-3 text-[13px]">
                    <span className="w-28 truncate font-medium text-fg">{b.name}</span>
                    <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-surface-3">
                      <div className="h-full rounded-full bg-[var(--series-1)]" style={{ width: `${Math.min(100, b.flip_index)}%` }} />
                    </div>
                    <span className="w-24 text-right text-xs text-fg-3 tnum">
                      {plural(b.opportunities, "deal")} · {pct(b.average_roi)}
                    </span>
                  </li>
                ))}
                {!insights.data?.top_brands?.length && <li className="text-[13px] text-fg-3">Collecting data…</li>}
              </ul>
            </div>
            <div>
              <p className="mb-2 text-xs font-medium text-fg-3">Biggest price drops (24h)</p>
              <ul className="space-y-1.5">
                {(insights.data?.price_drops ?? []).slice(0, 3).map((p) => (
                  <li key={p.opportunity_id}>
                    <Link href={`/deals/${p.opportunity_id}`} className="flex items-center gap-2 text-[13px] hover:underline">
                      <TrendingDown className="size-3.5 shrink-0 text-success" />
                      <span className="min-w-0 flex-1 truncate text-fg">{p.title}</span>
                      <span className="shrink-0 text-xs text-fg-3 tnum">
                        {eur(p.previous_price)} → <span className="font-medium text-fg">{eur(p.price)}</span>
                      </span>
                    </Link>
                  </li>
                ))}
                {!insights.data?.price_drops?.length && <li className="text-[13px] text-fg-3">No significant drops in the last 24 hours.</li>}
              </ul>
            </div>
          </CardContent>
        </Card>
      </section>
    </div>
  );
}
