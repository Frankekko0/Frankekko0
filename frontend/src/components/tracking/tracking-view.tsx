"use client";

import {
  ArrowLeft,
  CircleCheck,
  Clock,
  ExternalLink,
  Eye,
  EyeOff,
  History,
  Radar,
  RefreshCw,
  ShieldAlert,
  Tag,
  TrendingUp,
} from "lucide-react";
import Link from "next/link";
import { useMemo, useState, type CSSProperties, type ReactNode } from "react";
import { toast } from "sonner";
import { SnapshotChart } from "@/components/charts/lazy";
import { DataQualityBanner, MarketComparisonView, RiskChecklist, TimeOnlineView } from "@/components/deal/analysis-detail";
import { ProvenanceView } from "@/components/deal/provenance";
import { ScoreRing } from "@/components/deal/score";
import { Badge, type BadgeTone } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ErrorState, Skeleton } from "@/components/ui/feedback";
import { Tip } from "@/components/ui/misc";
import { errorMessage } from "@/lib/api";
import { CAPTURE_LABEL, CONDITION_LABEL, MODE_LABEL, STATUS_LABEL, eur, pct, shortDate, timeAgo } from "@/lib/format";
import { readProvenance } from "@/lib/provenance";
import { useItem, useRefreshItem, useTrackItem } from "@/lib/queries";
import type { AnalysisSummary, ItemDetail, MarketComparison, Snapshot, TimeOnline } from "@/lib/types";
import { cn } from "@/lib/utils";
import { Gallery } from "./gallery";
import { TableScroll } from "@/components/ui/table-scroll";

const STATUS_TONE: Record<string, BadgeTone> = {
  active: "success",
  reserved: "warning",
  sold: "accent",
  removed: "neutral",
  unknown: "outline",
  to_verify: "warning",
};

function dateTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleString("en-GB", { day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" });
}

function Section({ title, icon, children, action, className }: { title: string; icon: ReactNode; children: ReactNode; action?: ReactNode; className?: string }) {
  return (
    <Card className={cn("reveal", className)}>
      <CardHeader className="items-center">
        <CardTitle className="flex items-center gap-2 [&_svg]:size-4 [&_svg]:text-fg-3">
          {icon}
          {title}
        </CardTitle>
        {action}
      </CardHeader>
      <CardContent>{children}</CardContent>
    </Card>
  );
}

function Fact({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-3 border-b border-line py-2 text-[13px] last:border-0">
      <dt className="text-fg-3">{label}</dt>
      <dd className="text-right font-medium text-fg">{value ?? "—"}</dd>
    </div>
  );
}

/* --------------------------------------------------------------- status */
function StatusPanel({ d }: { d: ItemDetail }) {
  const t = d.tracking;
  const refresh = useRefreshItem();
  const track = useTrackItem();
  const closed = t.status === "sold" || t.status === "removed";
  return (
    <Card className="enter p-5" style={{ "--i": 1 } as CSSProperties}>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <Badge tone={STATUS_TONE[t.status] ?? "neutral"} className="px-2.5 py-1 text-[13px]">
              {STATUS_LABEL[t.status] ?? t.status}
            </Badge>
            {t.tracked ? <Badge tone="accent">Tracked</Badge> : <Badge tone="outline">Not tracked</Badge>}
            <Badge tone="outline">{CAPTURE_LABEL[d.item.capture_level]}</Badge>
          </div>
          <p className="mt-2 text-[13px] text-fg-2">
            <Clock className="mr-1 inline size-3.5 -translate-y-px text-fg-3" aria-hidden />
            Last checked{" "}
            <Tip content={dateTime(t.last_checked_at)}>
              <span className="font-semibold text-fg">{timeAgo(t.last_checked_at)}</span>
            </Tip>
            {t.next_check_at && !closed && <span className="text-fg-3"> · next check {dateTime(t.next_check_at)}</span>}
            {closed && <span className="text-fg-3"> · no more checks (closed)</span>}
            {t.check_failures > 0 && <span className="text-warning"> · {t.check_failures} failed checks, retrying less often</span>}
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button
            variant="outline"
            size="sm"
            loading={refresh.isPending}
            disabled={closed}
            onClick={() =>
              refresh.mutate(d.item.id, {
                onSuccess: (r) => {
                  if (r.outcome === "updated" || r.outcome === "unchanged" || r.outcome === "not_found") toast.success(r.message);
                  else if (r.needs_extension)
                    toast.message(r.message, {
                      action: { label: "Open on Vinted", onClick: () => window.open(d.item.url, "_blank", "noopener,noreferrer") },
                    });
                  else toast.message(r.message);
                },
                onError: (e) => toast.error(errorMessage(e)),
              })
            }
          >
            <RefreshCw /> Update now
          </Button>
          <Button
            variant={t.tracked ? "outline" : "primary"}
            size="sm"
            loading={track.isPending}
            onClick={() => track.mutate({ id: d.item.id, track: !t.tracked })}
          >
            {t.tracked ? <EyeOff /> : <Eye />} {t.tracked ? "Stop tracking" : "Track"}
          </Button>
          {!closed && (
            <Button asChild variant="outline" size="sm">
              <a href={d.item.url} target="_blank" rel="noopener noreferrer">
                <ExternalLink /> Open on Vinted
              </a>
            </Button>
          )}
        </div>
      </div>

      {t.status === "sold" && (
        <dl className="mt-4 grid grid-cols-2 gap-3 rounded-xl bg-accent-soft p-3.5 sm:grid-cols-4">
          <Mini label="Sold (estimate)" value={shortDate(t.sold_at)} sub={t.last_active_at ? `between ${shortDate(t.last_active_at)} and ${shortDate(t.sold_detected_at)}` : undefined} />
          <Mini label="Last price seen" value={eur(t.last_active_price)} />
          <Mini label="Days to sell" value={t.days_to_sell !== null ? `~${Math.round(t.days_to_sell)}` : "—"} sub="from publication" />
          <Mini label="Detected" value={shortDate(t.sold_detected_at)} />
        </dl>
      )}
      {t.status === "removed" && (
        <p className="mt-4 rounded-xl bg-surface-2 p-3.5 text-[13px] text-fg-2">
          Removed on {shortDate(t.removed_at)}: the listing is no longer available and no sale was confirmed, so it is <b>not</b> counted as sold.
          Last price seen {eur(t.last_active_price)}.
        </p>
      )}
      {d.item.capture_level === "link" && (
        <p className="mt-4 rounded-xl bg-warning-soft p-3.5 text-[13px] text-fg">
          Only the link is known so far. Open the listing on Vinted with the extension installed (or enable the server read) to fill in price, photos,
          status and the analysis.
        </p>
      )}
    </Card>
  );
}

function Mini({ label, value, sub }: { label: string; value: ReactNode; sub?: string }) {
  return (
    <div className="min-w-0">
      <dt className="text-[11px] font-medium text-fg-3">{label}</dt>
      <dd className="text-[16px] font-semibold tnum text-fg">{value}</dd>
      {sub && <p className="truncate text-[11px] text-fg-3">{sub}</p>}
    </div>
  );
}

/* --------------------------------------------------------------- analysis */
function AnalysisPanel({ a }: { a: AnalysisSummary }) {
  const e = a.economics;
  const insufficient = a.data_quality === "insufficient";
  const comparison = a.market.found !== undefined ? (a.market as unknown as MarketComparison) : null;
  const online = a.velocity.comparables !== undefined ? (a.velocity as unknown as TimeOnline) : null;
  const provenance = readProvenance(a.provenance);
  return (
    <Section
      title="Analysis"
      icon={<TrendingUp />}
      action={
        <Button asChild variant="ghost" size="sm">
          <Link href={`/deals/${a.opportunity_id}`}>
            Full breakdown <ArrowLeft className="rotate-180" />
          </Link>
        </Button>
      }
    >
      <div className="space-y-4">
        <DataQualityBanner quality={a.data_quality} reason={a.insufficient_reason} />
        <div className="flex items-center gap-4">
          {insufficient ? (
            <span className="rounded-xl bg-warning-soft px-3 py-2 text-center text-[12px] font-semibold text-warning">No score</span>
          ) : (
            <ScoreRing score={a.flip_score ?? 0} size={64} stroke={5} label="Score" />
          )}
          <div className="min-w-0">
            <p className="text-[15px] font-semibold text-fg">{a.headline ?? "—"}</p>
            <p className="text-[12px] text-fg-3">
              Confidence {a.confidence_score}/100 · {a.analysis_depth === "quick" ? "quick analysis (search card data)" : "full analysis"} · algorithm{" "}
              {a.algorithm_version} · {dateTime(a.analyzed_at)} · data from {MODE_LABEL[a.acquisition_mode ?? ""] ?? a.acquisition_mode}
            </p>
          </div>
        </div>
        {!insufficient && (
          <dl className="grid grid-cols-2 gap-3 rounded-xl bg-surface-2 p-3.5 sm:grid-cols-3">
            <Mini label="Price" value={eur(e.listing_price)} />
            <Mini label="Total cost to buy" value={eur(e.total_acquisition_cost)} sub="price + protection + shipping" />
            <Mini label="Realistic resale" value={`${eur(e.resale_low)} – ${eur(e.resale_high)}`} sub={`expected ${eur(e.resale_expected)}`} />
            <Mini label="Net margin" value={<span className={(e.net_margin ?? 0) >= 0 ? "text-success" : "text-danger"}>{eur(e.net_margin, { sign: true })}</span>} sub={`range ${eur(e.net_margin_low, { sign: true })} … ${eur(e.net_margin_high, { sign: true })}`} />
            <Mini label="ROI" value={pct(e.roi)} />
            <Mini label="Max price to pay" value={eur(e.max_buy_price)} sub="for your targets" />
          </dl>
        )}
        {provenance && (
          <div>
            <p className="mb-2 text-[13px] font-medium text-fg-2">Where the numbers come from</p>
            <ProvenanceView p={provenance} compact />
          </div>
        )}
        {comparison && (
          <div>
            <p className="mb-2 text-[13px] font-medium text-fg-2">Market comparison</p>
            <MarketComparisonView c={comparison} />
          </div>
        )}
        {online && (
          <div>
            <p className="mb-2 text-[13px] font-medium text-fg-2">Selling speed of comparables</p>
            <TimeOnlineView t={online} />
          </div>
        )}
        {a.risk_signals.length > 0 && (
          <div>
            <p className="mb-2 flex items-center gap-1.5 text-[13px] font-medium text-fg-2">
              <ShieldAlert className="size-3.5" /> Risk signals
            </p>
            <RiskChecklist signals={a.risk_signals} />
          </div>
        )}
        {a.reasons.length > 0 && (
          <ul className="space-y-1 text-[13px]">
            {a.reasons.slice(0, 8).map((r) => (
              <li key={r.code + r.label} className="flex gap-2">
                <span className={cn("font-semibold", r.type === "positive" ? "text-success" : r.type === "negative" ? "text-danger" : "text-fg-3")}>
                  {r.type === "positive" ? "+" : r.type === "negative" ? "−" : "·"}
                </span>
                <span className="text-fg-2">{r.label}</span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </Section>
  );
}

/* --------------------------------------------------------------- history */
/** Snapshots where the status changed, or that carry a note (sold by email, removed, …). */
function statusEvents(snapshots: Snapshot[]): Snapshot[] {
  const out: Snapshot[] = [];
  let prev: string | null = null;
  for (const s of snapshots) {
    if ((s.status !== null && s.status !== prev) || s.note) out.push(s);
    if (s.status !== null) prev = s.status;
  }
  return out;
}

function HistoryPanel({ snapshots }: { snapshots: Snapshot[] }) {
  const [all, setAll] = useState(false);
  const price = useMemo(
    () => snapshots.filter((s) => s.price !== null).map((s) => ({ t: new Date(s.observed_at).getTime(), value: s.price! })),
    [snapshots],
  );
  const favourites = useMemo(
    () => snapshots.filter((s) => s.favourite_count !== null).map((s) => ({ t: new Date(s.observed_at).getTime(), value: s.favourite_count! })),
    [snapshots],
  );
  const events = useMemo(() => statusEvents(snapshots), [snapshots]);
  const rows = all ? [...snapshots].reverse() : [...snapshots].reverse().slice(0, 8);
  return (
    <Section title="History" icon={<History />}>
      <div className="grid grid-cols-1 gap-6 md:grid-cols-2">
        <div>
          <p className="mb-1 text-[13px] font-medium text-fg-2">Price</p>
          {price.length ? (
            <SnapshotChart points={price} label="Price" format={(v) => eur(v)} step extendToNow />
          ) : (
            <p className="py-8 text-center text-[13px] text-fg-3">No price observed yet.</p>
          )}
        </div>
        <div>
          <p className="mb-1 text-[13px] font-medium text-fg-2">Favourites</p>
          {favourites.length ? (
            <SnapshotChart points={favourites} label="Favourites" format={(v) => String(Math.round(v))} color="var(--series-2)" />
          ) : (
            <p className="py-8 text-center text-[13px] text-fg-3">Favourites not visible in the captures so far.</p>
          )}
        </div>
      </div>
      {events.length > 0 && (
        <ol className="mt-5 space-y-2 border-l border-line pl-4">
          {events.map((s, i) => (
            <li key={s.observed_at + i} className="relative text-[13px]">
              <span className="absolute -left-[21px] top-1.5 size-2.5 rounded-full border-2 border-surface bg-accent" aria-hidden />
              <span className="text-fg-3 tnum">{dateTime(s.observed_at)}</span>
              {s.status && <span className="ml-2 font-medium text-fg">{STATUS_LABEL[s.status] ?? s.status}</span>}
              {s.note && <span className="ml-2 text-fg-2">{s.note}</span>}
            </li>
          ))}
        </ol>
      )}
      <TableScroll label="History" className="-mx-1 mt-5">
        <table className="w-full min-w-[620px] text-[12px]">
          <thead>
            <tr className="text-left text-fg-3">
              <th className="pb-1.5 font-medium">Observed</th>
              <th className="pb-1.5 font-medium">Source</th>
              <th className="pb-1.5 font-medium">Status</th>
              <th className="pb-1.5 text-right font-medium">Price</th>
              <th className="pb-1.5 text-right font-medium">Favourites</th>
              <th className="pb-1.5 text-right font-medium">Views</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((s, i) => (
              <tr key={s.observed_at + i} className="border-t border-line">
                <td className="py-1.5 tnum text-fg-2">{dateTime(s.observed_at)}</td>
                <td className="py-1.5 text-fg-2">{MODE_LABEL[s.acquisition_mode] ?? s.acquisition_mode}</td>
                <td className="py-1.5">{s.status ? STATUS_LABEL[s.status] : <span className="text-fg-3">—</span>}</td>
                <td className="py-1.5 text-right tnum">{eur(s.price)}</td>
                <td className="py-1.5 text-right tnum">{s.favourite_count ?? "—"}</td>
                <td className="py-1.5 text-right tnum">{s.view_count ?? "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </TableScroll>
      {snapshots.length > 8 && (
        <Button variant="ghost" size="sm" className="mt-2" onClick={() => setAll(!all)}>
          {all ? "Show fewer" : `Show all ${snapshots.length} observations`}
        </Button>
      )}
    </Section>
  );
}

/* --------------------------------------------------------------- page */
export function TrackingView({ refId }: { refId: string }) {
  const q = useItem(refId);
  if (q.isError) return <ErrorState message={errorMessage(q.error)} onRetry={() => q.refetch()} />;
  const d = q.data;
  if (!d) {
    return (
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-[420px_minmax(0,1fr)]">
        <Skeleton className="aspect-[4/5] rounded-2xl" />
        <div className="space-y-4">
          <Skeleton className="h-28 rounded-2xl" />
          <Skeleton className="h-96 rounded-2xl" />
        </div>
      </div>
    );
  }
  const it = d.item;
  const images = d.images.map((img) => ({ src: img.local_url ?? img.url, fallback: img.local_url ? img.url : null, archived: Boolean(img.local_url) }));
  return (
    <div className="space-y-5">
      <div>
        <Link href="/items" className="inline-flex items-center gap-1 text-[13px] text-fg-3 hover:text-fg">
          <ArrowLeft className="size-3.5" /> Archive
        </Link>
        <h1 className="enter mt-1 text-[24px] font-semibold leading-tight tracking-[-0.02em] sm:text-[28px]">{it.title}</h1>
        <p className="enter mt-1 text-[13px] text-fg-3" style={{ "--i": 1 } as CSSProperties}>
          {[it.brand, it.size, CONDITION_LABEL[it.condition]].filter(Boolean).join(" · ")}
          {it.vinted_id && <> · Vinted ID {it.vinted_id}</>} · internal ID {it.id.slice(0, 8)} · first seen {dateTime(it.first_seen_at)} via{" "}
          {MODE_LABEL[it.acquisition_mode] ?? it.acquisition_mode}
        </p>
      </div>

      <StatusPanel d={d} />

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-[420px_minmax(0,1fr)]">
        <div className="space-y-4 lg:sticky lg:top-20 lg:h-fit">
          <Gallery images={images} alt={it.title} />
          <div className="flex flex-wrap gap-2">
            <Button asChild className="flex-1">
              <a href={it.url} target="_blank" rel="noopener noreferrer">
                <ExternalLink /> Original listing
              </a>
            </Button>
            {it.opportunity_id && (
              <Button asChild variant="outline">
                <Link href={`/deals/${it.opportunity_id}`}>
                  <Radar /> Full analysis
                </Link>
              </Button>
            )}
          </div>
          {d.images.some((i) => i.archive_status === "ok") && (
            <p className="flex items-center gap-1.5 text-xs text-fg-3">
              <CircleCheck className="size-3.5 text-success" /> Photos saved for internal use: they stay visible after the listing is gone.
            </p>
          )}
          <Card className="p-4">
            <p className="mb-1 flex items-center gap-1.5 text-[13px] font-medium text-fg-2">
              <Tag className="size-3.5" /> Listing details
            </p>
            <dl>
              <Fact label="Price" value={it.capture_level === "link" ? "—" : eur(it.price)} />
              <Fact label="Buyer protection" value={d.buyer_protection_fee !== null ? eur(d.buyer_protection_fee) : "your cost profile"} />
              <Fact label="Shipping" value={d.shipping_fee !== null ? eur(d.shipping_fee) : "your default"} />
              <Fact label="Category" value={d.category} />
              <Fact label="Colour · material" value={[d.color, d.material].filter(Boolean).join(" · ") || "—"} />
              <Fact label="Published" value={dateTime(d.published_at)} />
              <Fact label="Favourites · views" value={`${it.favourite_count} · ${d.view_count}`} />
              <Fact
                label="Seller"
                value={d.seller ? (d.seller.rating !== null ? `${d.seller.rating.toFixed(1)}★ · ${d.seller.review_count} reviews` : `${d.seller.review_count} reviews`) : "not captured"}
              />
            </dl>
            {d.description && <p className="mt-3 whitespace-pre-line text-[13px] text-fg-2">{d.description}</p>}
          </Card>
        </div>

        <div className="min-w-0 space-y-5">
          {d.analysis ? (
            <AnalysisPanel a={d.analysis} />
          ) : (
            <Card className="p-5 text-[13px] text-fg-2">
              {it.capture_level === "link"
                ? "Not analysed yet: only the link is known."
                : it.status === "sold" || it.status === "removed"
                  ? "No analysis stored: the listing was already closed when its data arrived, so it was only recorded (it still counts as a comparable for other items)."
                  : "Not analysed yet: the data captured so far is incomplete."}
            </Card>
          )}
          <HistoryPanel snapshots={d.snapshots} />
          {d.attempts.length > 0 && (
            <Section title="Acquisition log" icon={<RefreshCw />}>
              <ul className="space-y-1.5 text-[12px]">
                {d.attempts.map((a, i) => (
                  <li key={a.started_at + i} className="flex flex-wrap gap-x-2">
                    <span className="text-fg-3 tnum">{dateTime(a.started_at)}</span>
                    <span className="text-fg-3">{MODE_LABEL[a.mode] ?? a.mode}</span>
                    <Badge tone={a.outcome === "ok" || a.outcome === "unchanged" ? "success" : a.outcome === "not_found" ? "neutral" : "warning"}>{a.outcome}</Badge>
                    {a.http_status && <span className="text-fg-3">HTTP {a.http_status}</span>}
                    <span className="text-fg-2">{a.message}</span>
                  </li>
                ))}
              </ul>
            </Section>
          )}
        </div>
      </div>
    </div>
  );
}
