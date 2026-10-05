"use client";

import { Brain, Database, Layers, Store } from "lucide-react";
import { useState, type CSSProperties } from "react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { EmptyState, Skeleton } from "@/components/ui/feedback";
import { Input, Select } from "@/components/ui/input";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/misc";
import { Button } from "@/components/ui/button";
import { eur, pct } from "@/lib/format";
import { useBrandAnalytics, useBrands, useCategories, useCategoryAnalytics, useInsights, useMarketDatabase } from "@/lib/queries";
import type { SegmentStats } from "@/lib/types";

function SegmentTable({ rows, loading, kind }: { rows?: SegmentStats[]; loading: boolean; kind: "brand" | "category" }) {
  if (loading) return <Skeleton className="h-80 rounded-xl" />;
  if (!rows?.length) return <EmptyState icon={<Layers />} title="Not enough data yet" description="Analytics fill up as the scanner analyses more listings." />;
  const max = Math.max(...rows.map((r) => r.flip_index), 1);
  return (
    <div className="-mx-5 overflow-x-auto">
      <table className="w-full min-w-[820px] text-left text-[13px]">
        <thead>
          <tr className="border-b border-line text-xs text-fg-3">
            <th className="px-5 py-2 font-medium">{kind === "brand" ? "Brand" : "Category"}</th>
            <th className="w-[28%] px-2 py-2 font-medium">Flip index</th>
            <th className="px-2 py-2 text-right font-medium">Opportunities</th>
            <th className="px-2 py-2 text-right font-medium">Avg ROI</th>
            <th className="px-2 py-2 text-right font-medium">Avg profit</th>
            <th className="px-2 py-2 text-right font-medium">Sell-through</th>
            <th className="px-2 py-2 text-right font-medium">Avg sale time</th>
            <th className="px-5 py-2 text-right font-medium">Analysed</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.slug} className="border-b border-line transition-colors last:border-0 hover:bg-surface-2/60">
              <td className="px-5 py-2.5 font-medium text-fg">
                {kind === "category" ? (r.name_it ?? r.name) : r.name}
                {r.segment && <span className="ml-1.5 text-xs text-fg-3">segment</span>}
              </td>
              <td className="px-2 py-2.5">
                <div className="flex items-center gap-2">
                  <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-surface-3">
                    <div
                      className="h-full origin-left rounded-full bg-[var(--series-1)]"
                      style={{ width: `${(r.flip_index / max) * 100}%`, animation: "grow-x 0.9s var(--ease-out) backwards" }}
                    />
                  </div>
                  <span className="w-9 text-right text-xs font-semibold tnum">{r.flip_index.toFixed(0)}</span>
                </div>
              </td>
              <td className="px-2 py-2.5 text-right tnum">
                {r.opportunities} <span className="text-fg-3">({pct(r.opportunity_rate, { digits: 1 })})</span>
              </td>
              <td className="px-2 py-2.5 text-right font-medium tnum text-success">{pct(r.average_roi)}</td>
              <td className="px-2 py-2.5 text-right tnum">{eur(r.average_profit)}</td>
              <td className="px-2 py-2.5 text-right tnum">{pct(r.sell_through)}</td>
              <td className="px-2 py-2.5 text-right tnum">{r.average_days_to_sell !== null ? `${r.average_days_to_sell.toFixed(1)}d` : "—"}</td>
              <td className="px-5 py-2.5 text-right tnum text-fg-3">{r.listings_analyzed.toLocaleString()}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function MarketDatabase() {
  const brands = useBrands();
  const categories = useCategories();
  const [brand, setBrand] = useState("");
  const [category, setCategory] = useState("");
  const [q, setQ] = useState("");
  const [page, setPage] = useState(1);
  const db = useMarketDatabase({ brand: brand || undefined, category: category || undefined, q: q || undefined, page });
  return (
    <div>
      <div className="mb-4 grid grid-cols-1 gap-2 sm:grid-cols-3">
        <Select value={brand} onChange={(e) => { setBrand(e.target.value); setPage(1); }} aria-label="Brand">
          <option value="">All brands</option>
          {(brands.data ?? []).map((b) => (
            <option key={b.slug} value={b.slug}>
              {b.name}
            </option>
          ))}
        </Select>
        <Select value={category} onChange={(e) => { setCategory(e.target.value); setPage(1); }} aria-label="Category">
          <option value="">All categories</option>
          {(categories.data ?? []).filter((c) => c.parent).map((c) => (
            <option key={c.slug} value={c.slug}>
              {c.name_it}
            </option>
          ))}
        </Select>
        <Input placeholder="Model (e.g. Nuptse, 501)" value={q} onChange={(e) => { setQ(e.target.value); setPage(1); }} />
      </div>
      {db.isLoading ? (
        <Skeleton className="h-80 rounded-xl" />
      ) : !db.data?.items.length ? (
        <EmptyState icon={<Database />} title="No segments found" description="The market database is rebuilt every 15 minutes from observed listings." />
      ) : (
        <>
          <div className="-mx-5 overflow-x-auto">
            <table className="w-full min-w-[860px] text-left text-[13px]">
              <thead>
                <tr className="border-b border-line text-xs text-fg-3">
                  <th className="px-5 py-2 font-medium">Product segment</th>
                  <th className="px-2 py-2 font-medium">Size</th>
                  <th className="px-2 py-2 text-right font-medium">Median price</th>
                  <th className="px-2 py-2 text-right font-medium">Typical range</th>
                  <th className="px-2 py-2 text-right font-medium">Avg listing</th>
                  <th className="px-2 py-2 text-right font-medium">Sell-through</th>
                  <th className="px-2 py-2 text-right font-medium">Days to sale</th>
                  <th className="px-5 py-2 text-right font-medium">Sample</th>
                </tr>
              </thead>
              <tbody>
                {db.data.items.map((m) => (
                  <tr key={m.segment_key} className="border-b border-line last:border-0">
                    <td className="px-5 py-2.5">
                      <p className="font-medium text-fg">
                        {m.brand.name} {m.model_name ?? ""}
                      </p>
                      <p className="text-xs text-fg-3">{m.category.name}</p>
                    </td>
                    <td className="px-2 py-2.5 text-fg-2">{m.size ?? "All"}</td>
                    <td className="px-2 py-2.5 text-right font-semibold tnum">{eur(m.median_price)}</td>
                    <td className="px-2 py-2.5 text-right tnum text-fg-2">
                      {eur(m.p25_price)} – {eur(m.p75_price)}
                    </td>
                    <td className="px-2 py-2.5 text-right tnum">{eur(m.avg_listing_price)}</td>
                    <td className="px-2 py-2.5 text-right tnum">{pct(m.sell_through_rate)}</td>
                    <td className="px-2 py-2.5 text-right tnum">{m.avg_days_to_sale !== null ? m.avg_days_to_sale.toFixed(1) : "—"}</td>
                    <td className="px-5 py-2.5 text-right tnum text-fg-3">
                      {m.sold_count} sold / {m.active_count} listed
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="mt-4 flex items-center justify-between text-[13px] text-fg-3">
            <span>{db.data.total.toLocaleString()} segments</span>
            <div className="flex gap-2">
              <Button variant="outline" size="sm" disabled={page === 1} onClick={() => setPage(page - 1)}>
                Previous
              </Button>
              <Button variant="outline" size="sm" disabled={!db.data.has_more} onClick={() => setPage(page + 1)}>
                Next
              </Button>
            </div>
          </div>
        </>
      )}
    </div>
  );
}

function Personal() {
  const insights = useInsights();
  const rows = insights.data?.your_best_segments ?? [];
  return (
    <div className="space-y-4">
      <p className="text-[13px] leading-relaxed text-fg-2">
        The learning engine compares the ROI of your completed flips per brand, category, size and price band with your average, and lowers the priority of segments
        you keep ignoring (never hiding them). After 3 completed flips this produces your <span className="font-semibold text-fg">Personal Flip Score</span>, available as a
        sort option in Deals.
      </p>
      {!rows.length ? (
        <EmptyState icon={<Brain />} title="Not enough personal data yet" description="Record purchases and sales in My Flips: the more you flip, the more personal the ranking becomes." />
      ) : (
        <table className="w-full text-left text-[13px]">
          <thead>
            <tr className="border-b border-line text-xs text-fg-3">
              <th className="py-2 font-medium">Segment</th>
              <th className="py-2 text-right font-medium">Flips</th>
              <th className="py-2 text-right font-medium">Avg ROI</th>
              <th className="py-2 text-right font-medium">Score adjustment</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.dimension + r.key} className="border-b border-line last:border-0">
                <td className="py-2.5">
                  <span className="text-fg-3">{r.dimension.replace("_", " ")}:</span> <span className="font-medium">{r.key}</span>
                </td>
                <td className="py-2.5 text-right tnum">{r.flips}</td>
                <td className="py-2.5 text-right tnum">{pct(r.avg_roi)}</td>
                <td className={`py-2.5 text-right font-semibold tnum ${r.adjustment >= 0 ? "text-success" : "text-danger"}`}>
                  {r.adjustment >= 0 ? "+" : ""}
                  {r.adjustment.toFixed(1)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

export default function AnalyticsPage() {
  const brands = useBrandAnalytics();
  const categories = useCategoryAnalytics();
  return (
    <div className="space-y-6">
      <div>
        <h1 className="enter text-[28px] font-semibold leading-tight tracking-[-0.025em] sm:text-[32px]">Analytics</h1>
        <p className="enter mt-1.5 text-sm text-fg-2" style={{ "--i": 1 } as CSSProperties}>Which brands and categories statistically produce the best flips (last 30 days).</p>
      </div>
      <Tabs defaultValue="brands">
        <TabsList>
          <TabsTrigger value="brands">Brands</TabsTrigger>
          <TabsTrigger value="categories">Categories</TabsTrigger>
          <TabsTrigger value="market">Market database</TabsTrigger>
          <TabsTrigger value="personal">Your performance</TabsTrigger>
        </TabsList>
        <TabsContent value="brands" className="mt-4">
          <Card className="reveal">
            <CardHeader>
              <div>
                <CardTitle className="flex items-center gap-2">
                  <Store className="size-4 text-fg-3" /> Best brands for flipping
                </CardTitle>
                <CardDescription>Flip index = profitable ROI × deal frequency × demand. Opportunities = active deals with Flip ≥ 70.</CardDescription>
              </div>
            </CardHeader>
            <CardContent>
              <SegmentTable rows={brands.data} loading={brands.isLoading} kind="brand" />
            </CardContent>
          </Card>
        </TabsContent>
        <TabsContent value="categories" className="mt-4">
          <Card className="reveal">
            <CardHeader>
              <div>
                <CardTitle className="flex items-center gap-2">
                  <Layers className="size-4 text-fg-3" /> Categories
                </CardTitle>
                <CardDescription>Including cross-cutting segments: Vintage and Designer.</CardDescription>
              </div>
            </CardHeader>
            <CardContent>
              <SegmentTable rows={categories.data} loading={categories.isLoading} kind="category" />
            </CardContent>
          </Card>
        </TabsContent>
        <TabsContent value="market" className="mt-4">
          <Card className="reveal">
            <CardHeader>
              <div>
                <CardTitle className="flex items-center gap-2">
                  <Database className="size-4 text-fg-3" /> Market database
                </CardTitle>
                <CardDescription>Realized prices, typical ranges and sales speed per product segment, rebuilt continuously.</CardDescription>
              </div>
            </CardHeader>
            <CardContent>
              <MarketDatabase />
            </CardContent>
          </Card>
        </TabsContent>
        <TabsContent value="personal" className="mt-4">
          <Card className="reveal">
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <Brain className="size-4 text-fg-3" /> Your performance & learning
              </CardTitle>
            </CardHeader>
            <CardContent>
              <Personal />
            </CardContent>
          </Card>
        </TabsContent>
      </Tabs>
    </div>
  );
}
