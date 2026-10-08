"use client";

import { ChartNoAxesColumn, CircleAlert, RefreshCw } from "lucide-react";
import type { ReactNode } from "react";
import { toast } from "sonner";
import { Meter } from "@/components/deal/score";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { ErrorState, Skeleton } from "@/components/ui/feedback";
import { errorMessage } from "@/lib/api";
import { eur, num, pct, plural, timeAgo } from "@/lib/format";
import { accuracyDelta, describeDelta, freeQueriesMonths, refreshErrorMessage, rejectedList, usd, usedShare } from "@/lib/provenance";
import { usePricingEvidence, useRefreshPricingEvidence } from "@/lib/queries";
import type { EvidenceMetrics, ExternalSearchStatus, PricingEvidence, SoldSaleSource } from "@/lib/types";
import { cn } from "@/lib/utils";
import { TableScroll } from "@/components/ui/table-scroll";

const SALE_SOURCES: { key: SoldSaleSource; label: string; detail: string }[] = [
  { key: "own_sale", label: "Your sales", detail: "price received" },
  { key: "own_purchase", label: "Your purchases", detail: "price paid" },
  { key: "vinted_sold", label: "Vinted, sold", detail: "last price seen" },
  { key: "external_sold", label: "Other markets, sold", detail: "price reported" },
];

function SubHeading({ children, aside }: { children: ReactNode; aside?: ReactNode }) {
  return (
    <div className="mb-2 flex flex-wrap items-center justify-between gap-x-3 gap-y-1">
      <p className="text-xs font-semibold uppercase tracking-[0.08em] text-fg-3">{children}</p>
      {aside}
    </div>
  );
}

function Tile({ label, value, sub }: { label: string; value: ReactNode; sub?: ReactNode }) {
  return (
    <div className="min-w-0">
      <dt className="text-[11px] font-semibold uppercase tracking-[0.08em] text-fg-3">{label}</dt>
      <dd className="mt-0.5 text-[20px] font-semibold leading-tight tracking-tight text-fg tnum">{value}</dd>
      {sub && <dd className="text-[12px] text-fg-3">{sub}</dd>}
    </div>
  );
}

function Line({ k, v, sub }: { k: ReactNode; v: ReactNode; sub?: ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-3 py-1.5">
      <dt className="min-w-0 text-[13px] text-fg-2">
        {k}
        {sub && <span className="text-fg-3"> · {sub}</span>}
      </dt>
      <dd className="shrink-0 text-right text-[13px] font-medium text-fg tnum">{v}</dd>
    </div>
  );
}

/* ------------------------------------------------------------ concluded sales */
function SoldSales({ s }: { s: PricingEvidence["sold_sales"] }) {
  const max = Math.max(1, ...SALE_SOURCES.map((x) => s.by_source[x.key] ?? 0));
  return (
    <div>
      <SubHeading aside={<span className="text-[12px] text-fg-3">{s.last_sync ? `synced ${timeAgo(s.last_sync)}` : "not synced yet"}</span>}>
        Concluded sales
      </SubHeading>
      <ul className="space-y-2.5">
        {SALE_SOURCES.map((x) => {
          const n = s.by_source[x.key] ?? 0;
          return (
            <li key={x.key}>
              <div className="flex items-baseline justify-between gap-3 text-[13px]">
                <span className="min-w-0 text-fg-2">
                  {x.label} <span className="text-fg-3">· {x.detail}</span>
                </span>
                <span className="font-medium text-fg tnum">{num(n)}</span>
              </div>
              <Meter value={(n / max) * 100} color="var(--accent)" className="mt-1" />
            </li>
          );
        })}
      </ul>
      <div className="mt-3 flex items-baseline justify-between gap-3 border-t border-line pt-2 text-[13px]">
        <span className="font-semibold text-fg">Total</span>
        <span className="font-semibold text-fg tnum">{num(s.total)}</span>
      </div>
      {s.outliers > 0 && <p className="mt-1 text-[12px] text-fg-3">{plural(s.outliers, "implausible price")} excluded as outliers.</p>}
    </div>
  );
}

/* ------------------------------------------------------------ external search */
function Budget({ label, used, max }: { label: string; used: number | null; max: number | null }) {
  const share = usedShare(used, max);
  return (
    <div>
      <div className="flex items-baseline justify-between gap-3 text-[13px]">
        <span className="text-fg-2">{label}</span>
        <span className="font-medium text-fg tnum">
          {num(used)} <span className="font-normal text-fg-3">/ {max !== null ? `${num(max)} queries` : "—"}</span>
        </span>
      </div>
      <Meter value={share * 100} color={share >= 0.9 ? "var(--warning)" : "var(--accent)"} className="mt-1" />
    </div>
  );
}

function ExternalSearch({ e }: { e: ExternalSearchStatus }) {
  const unreadable = e.provider === null;
  const on = e.enabled && e.provider === "serper";
  const freeMonths = freeQueriesMonths(e.expected_monthly_queries, e.free_queries);
  const rejected = rejectedList(e.rejected);
  const prices = e.prices;
  return (
    <div>
      <SubHeading
        aside={
          !unreadable && (
            <span className="flex flex-wrap gap-1.5">
              <Badge tone={on ? "success" : "neutral"}>{e.provider === "serper" ? "Serper" : "No provider"}</Badge>
              <Badge tone={on ? "success" : "outline"}>{on ? "On" : "Off"}</Badge>
              <Badge tone={e.key_configured ? "outline" : "warning"}>{e.key_configured ? "Key configured" : "No API key"}</Badge>
            </span>
          )
        }
      >
        External search
      </SubHeading>
      {unreadable ? (
        <p className="flex gap-2 rounded-xl bg-warning-soft p-3 text-[13px] text-warning">
          <CircleAlert className="mt-0.5 size-4 shrink-0" aria-hidden />
          {e.last_error ?? "The external search status is not available right now."}
        </p>
      ) : (
        <>
          {!on && (
            <p className="mb-3 rounded-xl bg-surface-2 p-3 text-[13px] text-fg-2">
              Off: estimates use your sales and Vinted only. To add new, asking and sold prices from other markets, set{" "}
              <code className="rounded bg-surface-3 px-1 py-0.5 text-[12px]">EXTERNAL_SEARCH_PROVIDER=serper</code> and{" "}
              <code className="rounded bg-surface-3 px-1 py-0.5 text-[12px]">SERPER_API_KEY</code> on the server (docs/EXTERNAL_PRICES.md).
            </p>
          )}
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <Budget label="This month" used={e.month_used} max={e.month_budget} />
            <Budget label="Today" used={e.today_used} max={e.daily_max} />
          </div>
          <dl className="mt-3 divide-y divide-line">
            <Line
              k="Cost"
              v={e.cost_per_query_usd !== null ? `${usd(e.cost_per_query_usd)} / query` : "—"}
              sub={e.free_queries ? `first ${num(e.free_queries)} free` : undefined}
            />
            <Line
              k="Expected use"
              v={e.expected_monthly_queries !== null ? `~${num(e.expected_monthly_queries)} queries / month` : "—"}
              sub={
                freeMonths !== null
                  ? `free queries last ~${freeMonths} months; then the smallest pack is $50 for 50,000 queries, valid 6 months`
                  : undefined
              }
            />
            <Line
              k="Models"
              v={`${num(e.models_cached)} cached · ${num(e.models_due)} due · ${num(e.models_pending)} pending`}
              sub={e.refresh_days ? `refreshed every ${e.refresh_days} days` : undefined}
            />
            <Line k="Prices stored" v={prices ? `${num(prices.new)} new · ${num(prices.asking)} asking · ${num(prices.sold)} sold` : "—"} />
            <Line
              k="Rejected"
              v={rejected.length ? `${num(rejected.reduce((a, r) => a + r.n, 0))}` : "none"}
              sub={rejected.length ? rejected.map((r) => `${r.n} ${r.label}`).join(", ") : undefined}
            />
            <Line k="Last run" v={e.last_run ? timeAgo(e.last_run) : "never"} />
          </dl>
          {e.last_error && (
            <p className="mt-2 flex gap-2 rounded-xl bg-warning-soft p-3 text-[13px] text-warning">
              <CircleAlert className="mt-0.5 size-4 shrink-0" aria-hidden />
              <span>Last error: {e.last_error}</span>
            </p>
          )}
        </>
      )}
    </div>
  );
}

/* ------------------------------------------------------------ accuracy */
function metricCells(m: EvidenceMetrics | null) {
  return {
    mae: m?.mae_eur != null ? eur(m.mae_eur) : "—",
    mape: m?.mape != null ? pct(m.mape, { digits: 1 }) : "—",
    n: m ? `${num(m.estimated)} of ${num(m.subjects)}` : "—",
  };
}

function AccuracyBlock({ a }: { a: PricingEvidence["accuracy"] }) {
  const without = metricCells(a.without_external);
  const withExt = metricCells(a.with_external);
  const delta = accuracyDelta(a.without_external, a.with_external);
  const measured = Boolean(a.measured_at) && (a.without_external !== null || a.with_external !== null);
  return (
    <div>
      <SubHeading aside={<span className="text-[12px] text-fg-3">{a.measured_at ? `measured ${timeAgo(a.measured_at)}` : "not measured yet"}</span>}>
        Accuracy on past sales
      </SubHeading>
      {measured ? (
        <>
          <TableScroll label="Estimate accuracy">
            <table className="w-full min-w-[320px] text-[13px]">
              <thead>
                <tr className="text-left text-[11px] font-semibold uppercase tracking-[0.06em] text-fg-3">
                  <th className="py-1.5 pr-3 font-semibold" scope="col">
                    <span className="sr-only">Metric</span>
                  </th>
                  <th className="py-1.5 pr-3 text-right font-semibold" scope="col">
                    Without external
                  </th>
                  <th className="py-1.5 text-right font-semibold" scope="col">
                    With external
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-line tnum">
                <tr>
                  <th scope="row" className="py-1.5 pr-3 text-left font-normal text-fg-2">
                    Mean error (MAE)
                  </th>
                  <td className="py-1.5 pr-3 text-right font-medium text-fg">{without.mae}</td>
                  <td className="py-1.5 text-right font-medium text-fg">{withExt.mae}</td>
                </tr>
                <tr>
                  <th scope="row" className="py-1.5 pr-3 text-left font-normal text-fg-2">
                    Mean % error (MAPE)
                  </th>
                  <td className="py-1.5 pr-3 text-right text-fg">{without.mape}</td>
                  <td className="py-1.5 text-right text-fg">{withExt.mape}</td>
                </tr>
                <tr>
                  <th scope="row" className="py-1.5 pr-3 text-left font-normal text-fg-2">
                    Sales estimated
                  </th>
                  <td className="py-1.5 pr-3 text-right text-fg-2">{without.n}</td>
                  <td className="py-1.5 text-right text-fg-2">{withExt.n}</td>
                </tr>
              </tbody>
            </table>
          </TableScroll>
          {describeDelta(delta) && (
            <p className={cn("mt-2 text-[13px] font-medium", delta !== null && delta > 0.005 ? "text-warning" : "text-fg")}>{describeDelta(delta)}</p>
          )}
        </>
      ) : (
        <p className="rounded-xl bg-surface-2 p-3 text-[13px] text-fg-2">
          Not measured yet: the comparison runs daily on your past sales, newer half against older half.
        </p>
      )}
      <div className="mt-3 flex flex-wrap gap-1.5">
        <Badge tone={a.external_in_use ? "success" : "warning"}>Other markets: {a.external_in_use ? "in use" : "excluded"}</Badge>
        <Badge tone={a.own_purchases_in_use ? "success" : "warning"}>Your purchases: {a.own_purchases_in_use ? "in use" : "excluded"}</Badge>
      </div>
      {a.note && <p className="mt-2 text-[12px] leading-relaxed text-fg-3">{a.note}</p>}
    </div>
  );
}

/* ------------------------------------------------------------ card */
/** Settings → Price data: where resale estimates come from and how accurate they are. */
export function PriceDataCard() {
  const q = usePricingEvidence();
  const refresh = useRefreshPricingEvidence();
  const d = q.data;

  function onRefresh() {
    refresh.mutate(undefined, {
      onSuccess: () => toast.success("Refresh queued: sales, discount and statistics update in the background; other markets within the daily budget."),
      onError: (e) => toast.error(refreshErrorMessage(e)),
    });
  }

  const n = d?.negotiation;
  const mae = d?.accuracy.with_external?.mae_eur ?? d?.accuracy.without_external?.mae_eur ?? null;
  return (
    <Card className="reveal" id="price-data">
      <CardHeader>
        <div className="min-w-0">
          <CardTitle className="flex items-center gap-2 [&_svg]:size-4 [&_svg]:text-fg-3">
            <ChartNoAxesColumn /> Price data
          </CardTitle>
          <CardDescription>
            Real sales come first, asking prices only when sales are missing. Prices from other markets are cached per model and never searched while analysing.
          </CardDescription>
        </div>
        <Button variant="outline" size="sm" className="shrink-0" onClick={onRefresh} loading={refresh.isPending} disabled={!d}>
          <RefreshCw /> Refresh now
        </Button>
      </CardHeader>
      <CardContent>
        {q.isError && !d ? (
          <ErrorState message={errorMessage(q.error)} onRetry={() => q.refetch()} />
        ) : !d ? (
          <Skeleton className="h-72 rounded-xl" />
        ) : (
          <div className="space-y-6">
            <dl className="grid grid-cols-2 gap-4 rounded-xl bg-surface-2 p-4 sm:grid-cols-4">
              <Tile label="Concluded sales" value={num(d.sold_sales.total)} sub="all sources" />
              <Tile
                label="Models ≥ 5 sales"
                value={num(d.sold_sales.models_with_5_sales)}
                sub={`of ${plural(d.sold_sales.models_total, "model")} with sales`}
              />
              <Tile
                label="Negotiation"
                value={n?.discount != null ? `−${pct(n.discount)}` : "—"}
                sub={n?.discount != null ? `on ${plural(n.n, "purchase")}` : "not measured"}
              />
              <Tile label="Mean error" value={mae !== null ? eur(mae) : "—"} sub={mae !== null ? "per estimate, past sales" : "not measured"} />
            </dl>

            <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
              <SoldSales s={d.sold_sales} />
              <div>
                <SubHeading aside={n?.measured_at ? <span className="text-[12px] text-fg-3">measured {timeAgo(n.measured_at)}</span> : undefined}>
                  Negotiation discount
                </SubHeading>
                <p className="text-[22px] font-semibold tracking-tight text-fg tnum">{n?.discount != null ? `−${pct(n.discount)}` : "Not measured"}</p>
                <p className="mt-1 text-[13px] leading-relaxed text-fg-2">{n?.note}</p>
                <p className="mt-2 text-[12px] leading-relaxed text-fg-3">
                  Applied to Vinted sold prices, which are the last price seen on the listing and not always the price paid.
                </p>
              </div>
            </div>

            <ExternalSearch e={d.external} />
            <AccuracyBlock a={d.accuracy} />
          </div>
        )}
      </CardContent>
    </Card>
  );
}
