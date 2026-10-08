"use client";

import { ExternalLink, Info, Receipt, Tag } from "lucide-react";
import { useState, type ReactNode } from "react";
import { days, eur, pct, plural } from "@/lib/format";
import {
  DAYS_BASIS_SHORT,
  EXPECTED_BASIS_LABEL,
  KIND_LABEL,
  PROBABILITY_BASIS_LABEL,
  RANGE_BASIS_LABEL,
  conditionLabel,
  dayDate,
  lastPriceNote,
  money,
  realSalesHeadline,
  safeUrl,
  salesMix,
  salesParts,
  sortedReferences,
  sourceDomain,
} from "@/lib/provenance";
import type { ExternalReference, Provenance } from "@/lib/types";
import { cn } from "@/lib/utils";
import { Badge, type BadgeTone } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";

/* ------------------------------------------------------------ real sales */
const MIX = [
  { key: "own", label: (n: number) => `${n} yours`, dot: "bg-success" },
  { key: "vinted", label: (n: number) => `${n} Vinted`, dot: "bg-accent" },
  { key: "external", label: (n: number) => `${n} other market${n === 1 ? "" : "s"}`, dot: "bg-accent/45" },
  { key: "asking", label: (n: number) => plural(n, "asking price"), dot: "bg-fg-3/45" },
] as const;

/** How many real (concluded) sales the estimate rests on, by origin, with asking prices beside. */
function RealSales({ p }: { p: Provenance }) {
  const mix = salesMix(p);
  const all = mix.total + mix.asking;
  const parts = MIX.filter((m) => mix[m.key] > 0);
  const note = lastPriceNote(p);
  const noSales = mix.total === 0;
  return (
    <div className="rounded-xl bg-surface-2 p-3.5">
      <div className="flex items-start gap-2.5">
        <span
          className={cn(
            "mt-0.5 flex size-7 shrink-0 items-center justify-center rounded-lg [&_svg]:size-3.5",
            noSales ? "bg-warning-soft text-warning" : "bg-success-soft text-success",
          )}
          aria-hidden
        >
          {noSales ? <Info /> : <Receipt />}
        </span>
        <div className="min-w-0">
          <p className="text-[15px] font-semibold leading-snug tracking-tight text-fg" data-testid="real-sales">
            {realSalesHeadline(p)}
          </p>
          {mix.total > 0 && mix.asking > 0 && (
            <p className="text-[12px] text-fg-3">plus {plural(mix.asking, "asking price")}, discounted to a sale price</p>
          )}
        </div>
      </div>
      {all > 0 && (
        <>
          <div className="mt-3 flex h-1.5 gap-px overflow-hidden rounded-full bg-surface-3" aria-hidden>
            {parts.map((m) => (
              <span key={m.key} className={cn("h-full", m.dot)} style={{ width: `${(mix[m.key] / all) * 100}%` }} />
            ))}
          </div>
          <ul className="mt-2 flex flex-wrap gap-x-3.5 gap-y-1 text-[12px] text-fg-2" aria-label={salesParts(mix) || "Comparables"}>
            {parts.map((m) => (
              <li key={m.key} className="inline-flex items-center gap-1.5 tnum">
                <span className={cn("size-2 rounded-full", m.dot)} aria-hidden />
                {m.label(mix[m.key])}
              </li>
            ))}
          </ul>
        </>
      )}
      {note && (
        <p className="mt-3 flex gap-2 border-t border-line pt-3 text-[12px] leading-relaxed text-fg-2">
          <Tag className="mt-0.5 size-3.5 shrink-0 text-fg-3" aria-hidden />
          <span>
            <span className="font-medium text-fg">Last price seen.</span> {note.text}{" "}
            <span className={note.discount !== null ? "text-fg" : undefined}>{note.discountText}</span>
          </span>
        </p>
      )}
    </div>
  );
}

/* ------------------------------------------------------------ numbers */
function NumberRow({ label, value, basis, source, children }: { label: string; value: string; basis?: string | null; source: string; children?: ReactNode }) {
  return (
    <li className="px-3.5 py-3">
      <div className="flex items-baseline justify-between gap-3">
        <p className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1 text-[13px] font-medium text-fg">
          {label}
          {basis && (
            <Badge tone="outline" className="font-medium">
              {basis}
            </Badge>
          )}
        </p>
        <p className="shrink-0 text-[15px] font-semibold tracking-tight text-fg tnum">{value}</p>
      </div>
      {source && <p className="mt-1 text-[12px] leading-snug text-fg-3">{source}</p>}
      {children}
    </li>
  );
}

function NewPriceShops({ sources }: { sources: NonNullable<Provenance["new_price"]>["sources"] }) {
  if (!sources.length) return null;
  return (
    <ul className="mt-2 flex flex-wrap gap-1.5">
      {sources.map((s, i) => {
        const url = safeUrl(s.url);
        const body = (
          <>
            <span className="text-fg-2">{sourceDomain(s.source, s.url)}</span>
            <span className="font-semibold text-fg tnum">{money(s.price, s.currency)}</span>
            {url && <ExternalLink className="size-3 text-fg-3" aria-hidden />}
          </>
        );
        const cls = "inline-flex items-center gap-1.5 rounded-lg border border-line px-2 py-1 text-[12px]";
        return (
          <li key={`${s.source}-${i}`}>
            {url ? (
              <a href={url} target="_blank" rel="noopener noreferrer nofollow" className={cn(cls, "transition-colors hover:border-line-strong hover:bg-surface-2")} title={`${dayDate(s.date)}`}>
                {body}
              </a>
            ) : (
              <span className={cls}>{body}</span>
            )}
          </li>
        );
      })}
    </ul>
  );
}

/* ------------------------------------------------------------ external references */
const KIND_TONE: Record<ExternalReference["kind"], BadgeTone> = { sold: "success", asking: "neutral", new: "accent" };

function ReferenceRow({ r }: { r: ExternalReference }) {
  const url = safeUrl(r.url);
  const domain = sourceDomain(r.source, r.url);
  const foreign = (r.currency || "EUR").toUpperCase() !== "EUR";
  return (
    <li className="flex items-start gap-3 px-3.5 py-2.5">
      <Badge tone={KIND_TONE[r.kind] ?? "neutral"} className="mt-0.5 w-14 justify-center">
        {KIND_LABEL[r.kind] ?? r.kind}
      </Badge>
      <div className="min-w-0 flex-1">
        <div className="flex items-baseline justify-between gap-3">
          {url ? (
            <a
              href={url}
              target="_blank"
              rel="noopener noreferrer nofollow"
              className="inline-flex min-w-0 items-center gap-1 text-[13px] font-medium text-fg transition-colors hover:text-accent"
            >
              <span className="truncate">{domain}</span>
              <ExternalLink className="size-3 shrink-0 text-fg-3" aria-hidden />
            </a>
          ) : (
            <span className="truncate text-[13px] font-medium text-fg">{domain}</span>
          )}
          <span className="shrink-0 text-[13px] font-semibold text-fg tnum">{money(r.price, r.currency)}</span>
        </div>
        {r.title && (
          <p className="truncate text-[12px] text-fg-3" title={r.title}>
            {r.title}
          </p>
        )}
        <p className="mt-0.5 flex flex-wrap items-center gap-x-1.5 gap-y-0.5 text-[11px] text-fg-3">
          <span className="tnum">{dayDate(r.date)}</span>
          <span aria-hidden>·</span>
          <span>{conditionLabel(r.condition)}</span>
          {foreign && (
            <>
              <span aria-hidden>·</span>
              <span className="tnum">≈ {eur(r.price_eur)}</span>
            </>
          )}
          <span aria-hidden>·</span>
          <span className={r.used_in_estimate ? "font-medium text-success" : undefined}>{r.used_in_estimate ? "used in the estimate" : "reference only"}</span>
        </p>
      </div>
    </li>
  );
}

function References({ refs, initial }: { refs: ExternalReference[]; initial: number }) {
  const [all, setAll] = useState(false);
  const sorted = sortedReferences(refs);
  const used = refs.filter((r) => r.used_in_estimate).length;
  const shown = all ? sorted : sorted.slice(0, initial);
  return (
    <div>
      <div className="mb-2 flex flex-wrap items-baseline justify-between gap-x-3 gap-y-0.5">
        <p className="text-[13px] font-medium text-fg-2">External references</p>
        <p className="text-[12px] text-fg-3 tnum">
          {refs.length ? `${refs.length} found · ${used} used in the estimate` : "none for this model yet"}
        </p>
      </div>
      {refs.length > 0 ? (
        <>
          <ul className="divide-y divide-line rounded-xl border border-line">
            {shown.map((r, i) => (
              <ReferenceRow key={`${r.url}-${r.kind}-${i}`} r={r} />
            ))}
          </ul>
          {sorted.length > initial && (
            <Button variant="ghost" size="xs" className="mt-1.5" onClick={() => setAll(!all)} aria-expanded={all}>
              {all ? "Show fewer" : `Show all ${sorted.length}`}
            </Button>
          )}
        </>
      ) : (
        <p className="rounded-xl border border-dashed border-line-strong px-3.5 py-3 text-[12px] text-fg-3">
          No prices from other markets for this model yet: they are searched in the background, never while analysing.
        </p>
      )}
    </div>
  );
}

/* ------------------------------------------------------------ view */
/** Where every number of an analysis comes from (expected price, range, time to sell, P(sale), new price). */
export function ProvenanceView({ p, compact = false }: { p: Provenance; compact?: boolean }) {
  const e = p.expected_price;
  const r = p.price_range;
  const t = p.days_to_sell;
  const s = p.sale_probability;
  const np = p.new_price;
  return (
    <div className="space-y-4" data-testid="provenance">
      <RealSales p={p} />
      <ul className="divide-y divide-line rounded-xl border border-line">
        <NumberRow label="Expected price" value={eur(e.value)} basis={EXPECTED_BASIS_LABEL[e.basis] ?? null} source={e.label} />
        <NumberRow
          label="Min – max"
          value={r.low !== null || r.high !== null ? `${eur(r.low)} – ${eur(r.high)}` : "—"}
          basis={RANGE_BASIS_LABEL[r.basis] ?? null}
          source={r.label}
        />
        <NumberRow
          label="Days to sell"
          value={t.value !== null ? days(t.value) : "—"}
          basis={DAYS_BASIS_SHORT[t.basis] ?? null}
          source={t.label}
        />
        <NumberRow
          label="Sale probability"
          value={s.value !== null ? pct(s.value) : "—"}
          basis={PROBABILITY_BASIS_LABEL[s.basis] ?? null}
          source={s.label}
        />
        {np && (
          <NumberRow label="New price" value={eur(np.value)} basis={plural(np.n, "shop")} source={np.label}>
            {!compact && <NewPriceShops sources={np.sources ?? []} />}
          </NumberRow>
        )}
      </ul>
      <References refs={p.external} initial={compact ? 3 : 6} />
    </div>
  );
}

/** The Analysis page section: shown only when the analysis recorded its provenance. */
export function ProvenanceSection({ p }: { p: Provenance }) {
  return (
    <Card id="sources" className="reveal scroll-mt-28">
      <CardHeader>
        <div className="min-w-0">
          <CardTitle className="flex items-center gap-2 [&_svg]:size-4 [&_svg]:text-fg-3">
            <Receipt />
            Where the numbers come from
          </CardTitle>
          <CardDescription>Every estimate with the data behind it. Source notes are shown as the analysis recorded them.</CardDescription>
        </div>
      </CardHeader>
      <CardContent>
        <ProvenanceView p={p} />
      </CardContent>
    </Card>
  );
}
