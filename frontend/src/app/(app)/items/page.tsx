"use client";

import { Archive, ChevronLeft, ChevronRight, Download, ExternalLink, Eye, EyeOff, Radar, Search } from "lucide-react";
import Link from "next/link";
import { useDeferredValue, useMemo, useState, type CSSProperties } from "react";
import { ListingImage } from "@/components/deal/listing-image";
import { Badge, type BadgeTone } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { EmptyState, ErrorState, Skeleton } from "@/components/ui/feedback";
import { Input, Select } from "@/components/ui/input";
import { Menu, MenuContent, MenuItem, MenuTrigger, Tip } from "@/components/ui/misc";
import { buildQuery, errorMessage } from "@/lib/api";
import { CAPTURE_LABEL, MODE_LABEL, STATUS_LABEL, eur, plural, timeAgo } from "@/lib/format";
import { useBrands, useItems, useTrackItem } from "@/lib/queries";
import type { Item, ItemFilters } from "@/lib/types";
import { cn } from "@/lib/utils";
import { TableScroll } from "@/components/ui/table-scroll";

const STATUS_TONE: Record<string, BadgeTone> = {
  active: "success",
  reserved: "warning",
  sold: "accent",
  removed: "neutral",
  unknown: "outline",
  to_verify: "warning",
};

const PAGE_SIZE = 50;

function ScoreCell({ item }: { item: Item }) {
  if (item.flip_score === null) return <span className="text-fg-3">Not analysed</span>;
  if (item.data_quality === "insufficient") {
    return (
      <Tip content="Too few comparable listings for a reliable estimate: no score is shown">
        <span className="text-[12px] font-medium text-warning">Insufficient data</span>
      </Tip>
    );
  }
  return (
    <div className="leading-tight">
      <span className="text-[15px] font-semibold tnum">{item.flip_score}</span>
      <span className="ml-1 text-[11px] text-fg-3">conf. {item.confidence_score}</span>
      {item.data_quality === "limited" && <p className="text-[11px] text-warning">few comparables</p>}
    </div>
  );
}

function TrackButton({ item }: { item: Item }) {
  const track = useTrackItem();
  return (
    <Tip content={item.tracked ? "Tracked: periodic status checks. Click to stop" : "Track: check its status periodically"}>
      <Button
        variant={item.tracked ? "outline" : "ghost"}
        size="icon-sm"
        aria-label={item.tracked ? "Stop tracking" : "Track"}
        aria-pressed={item.tracked}
        loading={track.isPending}
        onClick={() => track.mutate({ id: item.id, track: !item.tracked })}
      >
        {item.tracked ? <Eye className="text-accent" /> : <EyeOff />}
      </Button>
    </Tip>
  );
}

export default function ItemsPage() {
  const [q, setQ] = useState("");
  const [f, setF] = useState<ItemFilters>({ sort: "recent", date_field: "first_seen" });
  const [page, setPage] = useState(1);
  const query = useDeferredValue(q.trim());
  const filters = useMemo<ItemFilters>(() => ({ ...f, q: query || undefined, page, page_size: PAGE_SIZE }), [f, query, page]);
  const items = useItems(filters);
  const brands = useBrands();
  const set = (patch: Partial<ItemFilters>) => {
    setF((prev) => ({ ...prev, ...patch }));
    setPage(1);
  };
  const exportHref = (delimiter: "comma" | "semicolon") =>
    `/api/v1/items/export.csv${buildQuery({ ...filters, page: undefined, page_size: undefined, delimiter })}`;
  const data = items.data;

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="enter text-[28px] font-semibold leading-tight tracking-[-0.025em] sm:text-[32px]">Archive</h1>
          <p className="enter mt-1.5 max-w-2xl text-sm text-fg-2" style={{ "--i": 1 } as CSSProperties}>
            Every listing FlipFinder has seen or analysed, linked to the original, with where the data came from, its latest analysis and
            its status over time. Nothing is ever deleted.
          </p>
        </div>
        <Menu>
          <MenuTrigger asChild>
            <Button variant="outline">
              <Download /> Export CSV
            </Button>
          </MenuTrigger>
          <MenuContent align="end">
            <MenuItem asChild>
              <a href={exportHref("comma")} download>
                CSV (comma separated)
              </a>
            </MenuItem>
            <MenuItem asChild>
              <a href={exportHref("semicolon")} download>
                CSV for Excel (semicolon)
              </a>
            </MenuItem>
            <MenuItem asChild>
              <a href="/api/v1/items/export-observations.csv?delimiter=semicolon" download>
                History of observations (Excel)
              </a>
            </MenuItem>
            <MenuItem asChild>
              <a href="/api/v1/items/export-analyses.csv?delimiter=semicolon" download>
                Stored analyses (Excel)
              </a>
            </MenuItem>
            <MenuItem asChild>
              <a href="/api/v1/pricing/sold-prices/export.csv?delimiter=semicolon" download>
                Sold prices per model (Excel)
              </a>
            </MenuItem>
          </MenuContent>
        </Menu>
      </div>

      <Card className="enter p-3" style={{ "--i": 2 } as CSSProperties}>
        <div className="grid grid-cols-2 gap-2 md:grid-cols-4 xl:grid-cols-8">
          <div className="relative col-span-2">
            <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-fg-3" aria-hidden />
            <Input
              aria-label="Search by title or Vinted ID"
              placeholder="Title or Vinted ID"
              value={q}
              onChange={(e) => {
                setQ(e.target.value);
                setPage(1);
              }}
              className="pl-9"
              maxLength={120}
            />
          </div>
          <Select aria-label="Status" value={f.status ?? ""} onChange={(e) => set({ status: (e.target.value || undefined) as ItemFilters["status"] })}>
            <option value="">Any status</option>
            {Object.entries(STATUS_LABEL).map(([k, v]) => (
              <option key={k} value={k}>
                {v}
              </option>
            ))}
          </Select>
          <Select aria-label="Brand" value={f.brand ?? ""} onChange={(e) => set({ brand: e.target.value || undefined })}>
            <option value="">Any brand</option>
            {(brands.data ?? []).map((b) => (
              <option key={b.slug} value={b.slug}>
                {b.name}
              </option>
            ))}
          </Select>
          <Select aria-label="Source" value={f.mode ?? ""} onChange={(e) => set({ mode: (e.target.value || undefined) as ItemFilters["mode"] })}>
            <option value="">Any source</option>
            {Object.entries(MODE_LABEL).map(([k, v]) => (
              <option key={k} value={k}>
                {v}
              </option>
            ))}
          </Select>
          <Select
            aria-label="Tracking"
            value={f.tracked === undefined ? "" : String(f.tracked)}
            onChange={(e) => set({ tracked: e.target.value === "" ? undefined : e.target.value === "true" })}
          >
            <option value="">Tracked or not</option>
            <option value="true">Tracked</option>
            <option value="false">Not tracked</option>
          </Select>
          <Select
            aria-label="Minimum score"
            value={f.min_score === undefined ? "" : String(f.min_score)}
            onChange={(e) => set({ min_score: e.target.value === "" ? undefined : Number(e.target.value) })}
          >
            <option value="">Any score</option>
            {[50, 60, 70, 80, 90].map((s) => (
              <option key={s} value={s}>
                Score ≥ {s}
              </option>
            ))}
          </Select>
          <Select aria-label="Sort by" value={f.sort} onChange={(e) => set({ sort: e.target.value as ItemFilters["sort"] })}>
            <option value="recent">Newest first</option>
            <option value="score">Best score</option>
            <option value="profit">Highest margin</option>
            <option value="price_asc">Lowest price</option>
            <option value="price_desc">Highest price</option>
            <option value="last_checked">Last checked</option>
          </Select>
          <Select
            aria-label="Date"
            value={f.date_field}
            onChange={(e) => set({ date_field: e.target.value as ItemFilters["date_field"] })}
            className="col-span-2 md:col-span-1"
          >
            <option value="first_seen">First seen</option>
            <option value="analyzed">Analysed</option>
            <option value="last_checked">Last checked</option>
            <option value="published">Published</option>
          </Select>
          <Input type="date" aria-label="From" value={f.date_from?.slice(0, 10) ?? ""} onChange={(e) => set({ date_from: e.target.value || undefined })} />
          <Input
            type="date"
            aria-label="To"
            value={f.date_to?.slice(0, 10) ?? ""}
            onChange={(e) => set({ date_to: e.target.value ? `${e.target.value}T23:59:59` : undefined })}
          />
        </div>
      </Card>

      {items.isError ? (
        <ErrorState message={errorMessage(items.error)} onRetry={() => items.refetch()} />
      ) : !data ? (
        <Skeleton className="h-96 rounded-2xl" />
      ) : data.total === 0 ? (
        <EmptyState
          icon={<Archive />}
          title="No listings match"
          description="Change the filters, or capture listings with the browser extension, a link or the Analyze form: every one ends up here."
        />
      ) : (
        <Card className="enter overflow-hidden" style={{ "--i": 3 } as CSSProperties}>
          <div className="flex items-center justify-between border-b border-line px-4 py-2.5 text-[13px] text-fg-3">
            <span>{plural(data.total, "listing")}</span>
            <span className={cn("transition-opacity", items.isFetching ? "opacity-100" : "opacity-0")} aria-live="polite">
              Updating…
            </span>
          </div>
          <TableScroll label="Archived listings">
            <table className="w-full min-w-[980px] text-left text-[13px]">
              <thead>
                <tr className="border-b border-line text-xs text-fg-3">
                  <th className="px-4 py-2 font-medium">Listing</th>
                  <th className="px-2 py-2 font-medium">Status</th>
                  <th className="px-2 py-2 font-medium">Source</th>
                  <th className="px-2 py-2 text-right font-medium">Price</th>
                  <th className="px-2 py-2 font-medium">Score</th>
                  <th className="px-2 py-2 text-right font-medium">Net margin</th>
                  <th className="px-2 py-2 font-medium">Checked</th>
                  <th className="px-4 py-2">
                    <span className="sr-only">Actions</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {data.items.map((it) => (
                  <tr key={it.id} className="border-b border-line last:border-0 hover:bg-surface-2/50">
                    <td className="px-4 py-2">
                      <div className="flex items-center gap-3">
                        <ListingImage src={it.image_url} alt={it.title} className="size-11 shrink-0 rounded-lg" />
                        <div className="min-w-0">
                          <Link href={`/items/${it.id}`} className="block max-w-[300px] truncate font-medium text-fg hover:underline">
                            {it.title}
                          </Link>
                          <p className="text-xs text-fg-3">
                            {[it.brand, it.size, it.vinted_id ? `#${it.vinted_id}` : null].filter(Boolean).join(" · ") || "—"}
                          </p>
                        </div>
                      </div>
                    </td>
                    <td className="px-2 py-2">
                      <Badge tone={STATUS_TONE[it.status] ?? "neutral"}>{STATUS_LABEL[it.status] ?? it.status}</Badge>
                      {it.status === "sold" && it.days_to_sell !== null && <p className="mt-0.5 text-[11px] text-fg-3">in ~{Math.round(it.days_to_sell)}d</p>}
                    </td>
                    <td className="px-2 py-2">
                      <p className="text-fg-2">{MODE_LABEL[it.acquisition_mode] ?? it.acquisition_mode}</p>
                      <p className="text-[11px] text-fg-3">{CAPTURE_LABEL[it.capture_level]}</p>
                    </td>
                    <td className="px-2 py-2 text-right tnum">{eur(it.price)}</td>
                    <td className="px-2 py-2">
                      <ScoreCell item={it} />
                    </td>
                    <td
                      className={cn(
                        "px-2 py-2 text-right font-semibold tnum",
                        it.expected_profit === null || it.data_quality === "insufficient"
                          ? "text-fg-3"
                          : it.expected_profit >= 0
                            ? "text-success"
                            : "text-danger",
                      )}
                    >
                      {it.data_quality === "insufficient" ? "—" : eur(it.expected_profit, { sign: true })}
                    </td>
                    <td className="px-2 py-2 text-fg-2">
                      <Tip content={it.next_check_at ? `Next check ${timeAgo(it.next_check_at).replace(" ago", "")}` : "No periodic checks"}>
                        <span>{timeAgo(it.last_checked_at)}</span>
                      </Tip>
                    </td>
                    <td className="px-4 py-2">
                      <div className="flex items-center justify-end gap-1">
                        <TrackButton item={it} />
                        {it.opportunity_id && (
                          <Tip content="Open the analysis">
                            <Button asChild variant="ghost" size="icon-sm" aria-label="Open the analysis">
                              <Link href={`/deals/${it.opportunity_id}`}>
                                <Radar />
                              </Link>
                            </Button>
                          </Tip>
                        )}
                        <Tip content="Open the original listing">
                          <Button asChild variant="ghost" size="icon-sm" aria-label="Open the original listing">
                            <a href={it.url} target="_blank" rel="noopener noreferrer">
                              <ExternalLink />
                            </a>
                          </Button>
                        </Tip>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </TableScroll>
          <div className="flex items-center justify-between border-t border-line px-4 py-2.5 text-[13px]">
            <span className="text-fg-3">
              Page {page} of {Math.max(1, Math.ceil(data.total / PAGE_SIZE))}
            </span>
            <div className="flex gap-2">
              <Button variant="outline" size="sm" disabled={page === 1} onClick={() => setPage(page - 1)}>
                <ChevronLeft /> Previous
              </Button>
              <Button variant="outline" size="sm" disabled={!data.has_more} onClick={() => setPage(page + 1)}>
                Next <ChevronRight />
              </Button>
            </div>
          </div>
        </Card>
      )}
    </div>
  );
}
