"use client";

import { ArrowRight, BadgeEuro, Import, PlugZap, ScanSearch, ThumbsUp, TrendingDown, Trophy } from "lucide-react";
import Link from "next/link";
import { useEffect, useMemo, useRef, useState, type CSSProperties } from "react";
import { DealCard, DealCardSkeleton } from "@/components/deal/deal-card";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { EmptyState, ErrorState, Skeleton } from "@/components/ui/feedback";
import { Select } from "@/components/ui/input";
import { Chip } from "@/components/ui/misc";
import { AnimatedNumber } from "@/components/ui/motion";
import { StatTile } from "@/components/ui/stat";
import { errorMessage } from "@/lib/api";
import { decodeBatchHash, MAX_BATCH, type DecodedBatch } from "@/lib/batch-import";
import { eur } from "@/lib/format";
import { useBatchImport } from "@/lib/queries";
import type { BatchImportResult, FavoriteState, OpportunityCard } from "@/lib/types";

type Show = "all" | "buy" | "profit";
type Sort = "best" | "profit" | "roi" | "price" | "discount";

const SORTS: Record<Sort, { label: string; key?: (d: OpportunityCard) => number }> = {
  best: { label: "Best opportunity" },
  profit: { label: "Highest profit", key: (d) => -(d.expected_profit ?? -Infinity) },
  roi: { label: "Highest ROI", key: (d) => -(d.expected_roi ?? -Infinity) },
  price: { label: "Lowest price", key: (d) => d.listing_price },
  discount: { label: "Biggest discount vs market", key: (d) => -(d.discount_vs_market ?? -Infinity) },
};

function plural(n: number, one: string, many: string): string {
  return `${n} ${n === 1 ? one : many}`;
}

export default function ImportView() {
  // Read once: the fragment is removed right away, so a refresh never imports twice.
  const [hash] = useState(() => window.location.hash);
  const [batch, setBatch] = useState<DecodedBatch | null | undefined>(hash ? undefined : null);
  const [result, setResult] = useState<BatchImportResult | null>(null);
  const importer = useBatchImport();
  const started = useRef(false);

  function run(b: DecodedBatch) {
    importer.mutate({ items: b.items, source: b.source === "vinted_search" ? "vinted_search" : "manual" }, { onSuccess: setResult });
  }

  useEffect(() => {
    if (!hash || started.current) return;
    started.current = true;
    window.history.replaceState(null, "", window.location.pathname + window.location.search);
    void decodeBatchHash(hash).then((b) => {
      setBatch(b);
      if (b && b.items.length) run(b);
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps -- one-shot on mount
  }, []);

  const query = batch?.query ? `“${batch.query}”` : "your Vinted search";

  return (
    <div className="space-y-6">
      <div>
        <h1 className="enter text-[28px] font-semibold leading-tight tracking-[-0.025em] sm:text-[32px]">
          {result ? <>Best picks from {query}</> : "Import a Vinted search"}
        </h1>
        <p className="enter mt-1.5 max-w-2xl text-sm text-fg-2" style={{ "--i": 1 } as CSSProperties}>
          {result
            ? "Every listing you had loaded, analysed with your costs and targets and ranked by opportunity. Open one for the full breakdown before you decide."
            : "Search on Vinted as you normally do, then let FlipFinder analyse every listing on the page and rank them for you."}
        </p>
      </div>

      {hash && batch === undefined ? (
        <Skeleton className="h-40 rounded-2xl" />
      ) : hash && batch === null ? (
        <ErrorState message="This import link can't be read. Go back to the Vinted search and click the FlipFinder button again." />
      ) : batch && batch.items.length === 0 ? (
        <EmptyState
          icon={<ScanSearch />}
          title="No listings could be read from that page"
          description={skippedNote(batch) || "Scroll the search results so the listings load, then click the FlipFinder button again."}
        />
      ) : batch && importer.isError ? (
        <ErrorState message={errorMessage(importer.error)} onRetry={() => run(batch)} />
      ) : batch && result ? (
        <Results batch={batch} result={result} />
      ) : batch ? (
        <Analysing count={batch.items.length} query={query} />
      ) : (
        <HowTo />
      )}
    </div>
  );
}

function skippedNote(batch: DecodedBatch): string {
  const notes: string[] = [];
  if (batch.skipped.currency) {
    notes.push(`${plural(batch.skipped.currency, "listing", "listings")} priced in another currency left out (market data is in euro).`);
  }
  if (batch.skipped.invalid) notes.push(`${plural(batch.skipped.invalid, "card", "cards")} without a readable title or price skipped.`);
  return notes.join(" ");
}

function Analysing({ count, query }: { count: number; query: string }) {
  return (
    <div className="space-y-5" role="status" aria-live="polite">
      <Card className="enter overflow-hidden p-5">
        <div className="flex items-center gap-3">
          <span className="size-5 animate-spin rounded-full border-2 border-accent border-r-transparent" aria-hidden />
          <div>
            <p className="text-[15px] font-semibold">
              Analysing {plural(count, "listing", "listings")} from {query}…
            </p>
            <p className="text-[13px] text-fg-3">Market value, resale scenarios, profit, risk. This usually takes a couple of seconds.</p>
          </div>
        </div>
        <div className="progress-indeterminate mt-4 h-1 rounded-full bg-surface-2" aria-hidden />
      </Card>
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
        {Array.from({ length: Math.min(count, 8) }, (_, i) => (
          <DealCardSkeleton key={i} index={i} />
        ))}
      </div>
    </div>
  );
}

function Results({ batch, result }: { batch: DecodedBatch; result: BatchImportResult }) {
  const [items, setItems] = useState(result.items);
  const [show, setShow] = useState<Show>("all");
  const [sort, setSort] = useState<Sort>("best");

  const rank = useMemo(() => new Map(result.items.map((d, i) => [d.id, i + 1])), [result.items]);
  const buy = items.filter((d) => d.verdict === "BUY");
  const profitable = items.filter((d) => (d.expected_profit ?? 0) > 0);
  const best = profitable.reduce<OpportunityCard | null>((top, d) => (!top || (d.expected_profit ?? 0) > (top.expected_profit ?? 0) ? d : top), null);

  const visible = useMemo(() => {
    const list = show === "buy" ? buy : show === "profit" ? profitable : items;
    const key = SORTS[sort].key;
    return key ? [...list].sort((a, b) => key(a) - key(b)) : list;
  }, [items, buy, profitable, show, sort]);

  function onStateChange(id: string, state: FavoriteState | null) {
    setItems((prev) =>
      state === "ignored" ? prev.filter((d) => d.id !== id) : prev.map((d) => (d.id === id ? { ...d, favorite_state: state } : d)),
    );
  }

  const notes = [
    skippedNote(batch),
    result.reposts ? `${plural(result.reposts, "listing was a re-post", "listings were re-posts")} of items FlipFinder already knew.` : "",
  ].filter(Boolean);

  return (
    <div className="space-y-6">
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <StatTile
          index={0}
          label="Worth buying"
          icon={<ThumbsUp />}
          tone="success"
          value={<AnimatedNumber value={buy.length} />}
          sub={`of ${plural(result.analyzed, "listing", "listings")} analysed`}
        />
        <StatTile
          index={1}
          label="Best expected profit"
          icon={<Trophy />}
          tone="ultra"
          value={best ? <AnimatedNumber value={best.expected_profit ?? 0} format={(v) => eur(v, { sign: true })} /> : "—"}
          sub={best ? best.title : "No profitable listing on this page"}
        />
        <StatTile
          index={2}
          label="Profitable with your costs"
          icon={<BadgeEuro />}
          value={<AnimatedNumber value={profitable.length} />}
          sub="expected profit above zero"
        />
        <StatTile
          index={3}
          label="Price drops"
          icon={<TrendingDown />}
          tone="accent"
          value={<AnimatedNumber value={result.price_drops} />}
          sub={`${result.imported} new · ${result.updated} already tracked`}
        />
      </div>

      {notes.length > 0 && <p className="enter text-[13px] text-fg-3">{notes.join(" ")}</p>}

      <div className="enter flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between" style={{ "--i": 4 } as CSSProperties}>
        <div className="flex flex-wrap gap-2" role="group" aria-label="Show">
          <Chip active={show === "all"} onClick={() => setShow("all")}>
            All <span className="tnum text-fg-3">{items.length}</span>
          </Chip>
          <Chip active={show === "buy"} onClick={() => setShow("buy")}>
            Worth buying <span className="tnum text-fg-3">{buy.length}</span>
          </Chip>
          <Chip active={show === "profit"} onClick={() => setShow("profit")}>
            Profitable <span className="tnum text-fg-3">{profitable.length}</span>
          </Chip>
        </div>
        <label className="flex items-center gap-2 text-[13px] text-fg-3">
          Sort by
          <Select aria-label="Sort by" value={sort} onChange={(e) => setSort(e.target.value as Sort)} className="h-9 w-auto">
            {Object.entries(SORTS).map(([k, s]) => (
              <option key={k} value={k}>
                {s.label}
              </option>
            ))}
          </Select>
        </label>
      </div>

      {visible.length === 0 ? (
        <EmptyState
          icon={<ScanSearch />}
          title={show === "buy" ? "Nothing worth buying on this page" : "No profitable listing on this page"}
          description="That's useful to know too. Try another search, a different size or a lower price range."
          action={
            <Button variant="outline" size="sm" onClick={() => setShow("all")}>
              Show all {items.length}
            </Button>
          }
        />
      ) : (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
          {visible.map((d, i) => (
            <DealCard
              key={d.id}
              deal={d}
              index={Math.min(i, 12)}
              priority={i === 0 && sort === "best"}
              rank={rank.get(d.id)}
              onStateChange={(state) => onStateChange(d.id, state)}
            />
          ))}
        </div>
      )}

      <Card className="enter flex flex-col items-start justify-between gap-3 p-4 sm:flex-row sm:items-center">
        <p className="text-[13px] text-fg-2">
          All of them are now in your feed: FlipFinder keeps their price history, so importing the same search again later shows the drops.
        </p>
        <Button asChild variant="outline" size="sm">
          <Link href="/deals">
            Open Deals <ArrowRight />
          </Link>
        </Button>
      </Card>
    </div>
  );
}

function HowTo() {
  return (
    <div className="grid grid-cols-1 gap-6 lg:grid-cols-[minmax(0,1fr)_380px]">
      <Card className="enter overflow-hidden" style={{ "--i": 2 } as CSSProperties}>
        <div className="border-b border-line bg-gradient-to-br from-ultra-soft via-transparent to-accent-soft px-5 py-4">
          <div className="flex items-center gap-3">
            <span className="flex size-9 items-center justify-center rounded-xl bg-gradient-to-br from-ultra to-ultra-2 text-white shadow-[0_6px_18px_-8px_var(--ultra)]">
              <Import className="size-4" />
            </span>
            <div>
              <p className="text-[14px] font-semibold">A whole search page in one click</p>
              <p className="text-xs text-fg-3">With the FlipFinder for Vinted browser extension</p>
            </div>
          </div>
        </div>
        <ol className="space-y-3 px-5 py-4 text-[13px] text-fg-2">
          {[
            <>Search on Vinted as usual (e.g. “felpa ralph lauren”), with the filters you like.</>,
            <>Scroll the results: every listing loaded on the page is included, up to {MAX_BATCH} at a time.</>,
            <>
              Click <span className="font-medium text-fg">Analizza N articoli</span> in the bottom-right corner (or the extension icon).
            </>,
            <>FlipFinder analyses them all with your costs and opens them here, best opportunity first.</>,
          ].map((step, i) => (
            <li key={i} className="flex gap-3">
              <span className="flex size-6 shrink-0 items-center justify-center rounded-full bg-surface-2 text-[12px] font-semibold text-fg tnum">
                {i + 1}
              </span>
              <span className="pt-0.5">{step}</span>
            </li>
          ))}
        </ol>
      </Card>
      <Card className="enter p-5" style={{ "--i": 3 } as CSSProperties}>
        <div className="flex items-center gap-2 text-[14px] font-semibold">
          <PlugZap className="size-4 text-accent" /> Install the extension
        </div>
        <p className="mt-2 text-[13px] text-fg-2">
          Open <code className="rounded bg-surface-2 px-1 py-0.5 text-[12px]">chrome://extensions</code>, turn on Developer mode, click{" "}
          <span className="font-medium text-fg">Load unpacked</span> and choose the{" "}
          <code className="rounded bg-surface-2 px-1 py-0.5 text-[12px]">extension</code> folder of FlipFinder.
        </p>
        <p className="mt-3 text-xs text-fg-3">
          It only reads the page you are looking at: no automatic browsing or scrolling, no purchases, no messages to sellers.
        </p>
        <Button asChild variant="outline" size="sm" className="mt-4">
          <Link href="/analyze">
            <ScanSearch /> Analyze a single listing instead
          </Link>
        </Button>
      </Card>
    </div>
  );
}
