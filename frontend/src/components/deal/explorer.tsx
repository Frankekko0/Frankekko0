"use client";

import { useInfiniteQuery } from "@tanstack/react-query";
import { Radar, SlidersHorizontal, UserCheck, X } from "lucide-react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useMemo, useState, type ReactNode, type CSSProperties } from "react";
import { api, errorMessage } from "@/lib/api";
import { FILTER_KEYS, PRESETS, SORTS, activeFilterCount, filtersFromParams, paramsFromFilters } from "@/lib/filters";
import { usePreferences } from "@/lib/queries";
import type { OpportunityCard, OpportunityFilters, Page } from "@/lib/types";
import { Button } from "@/components/ui/button";
import { EmptyState, ErrorState } from "@/components/ui/feedback";
import { Select } from "@/components/ui/input";
import { Chip, Dialog, DialogContent, DialogTrigger } from "@/components/ui/misc";
import { cn } from "@/lib/utils";
import { DealCard, DealCardSkeleton } from "./deal-card";
import { FilterPanel } from "./filter-panel";

const PAGE_SIZE = 24;

function useFeed(filters: OpportunityFilters) {
  return useInfiniteQuery({
    queryKey: ["opportunities", "infinite", filters],
    initialPageParam: 1,
    queryFn: ({ pageParam, signal }) =>
      api<Page<OpportunityCard>>("/opportunities", { query: { ...filters, page: pageParam, page_size: PAGE_SIZE }, signal }),
    getNextPageParam: (last) => (last.has_more ? last.page + 1 : undefined),
    refetchInterval: 60_000,
  });
}

export function DealsExplorer({
  title,
  description,
  fixed = {},
  showPresets = true,
  empty,
}: {
  title: string;
  description: string;
  fixed?: OpportunityFilters;
  showPresets?: boolean;
  empty?: ReactNode;
}) {
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  const urlFilters = useMemo(() => filtersFromParams(new URLSearchParams(params.toString())), [params]);
  const filters: OpportunityFilters = { sort: "flip", ...urlFilters, ...fixed };
  const feed = useFeed(filters);
  const prefs = usePreferences();
  const [open, setOpen] = useState(false);
  const items = feed.data?.pages.flatMap((p) => p.items) ?? [];
  const total = feed.data?.pages[0]?.total;
  const count = activeFilterCount({ ...urlFilters });

  function update(next: OpportunityFilters) {
    const clean = { ...next };
    for (const k of Object.keys(fixed)) delete (clean as Record<string, unknown>)[k];
    // Keep unrelated params (e.g. the active tab) and replace only the filter ones.
    const merged = new URLSearchParams(params.toString());
    FILTER_KEYS.forEach((k) => merged.delete(k));
    paramsFromFilters(clean).forEach((v, k) => merged.append(k, v));
    const qs = merged.toString();
    router.replace(qs ? `${pathname}?${qs}` : pathname, { scroll: false });
  }

  const chips: { key: string; label: string; clear: () => void }[] = [];
  const f = urlFilters;
  const add = (key: keyof OpportunityFilters, label: string) =>
    chips.push({ key, label, clear: () => update({ ...f, [key]: undefined }) });
  if (f.min_price !== undefined) add("min_price", `≥ €${f.min_price}`);
  if (f.max_price !== undefined) add("max_price", `≤ €${f.max_price}`);
  if (f.min_profit !== undefined) add("min_profit", `Profit ≥ €${f.min_profit}`);
  if (f.min_roi !== undefined) add("min_roi", `ROI ≥ ${Math.round(f.min_roi * 100)}%`);
  if (f.min_flip !== undefined) add("min_flip", `Flip ≥ ${f.min_flip}`);
  if (f.min_confidence !== undefined) add("min_confidence", `Confidence ≥ ${f.min_confidence}`);
  if (f.max_risk !== undefined) add("max_risk", `Risk ≤ ${f.max_risk}`);
  if (f.min_velocity !== undefined) add("min_velocity", `Velocity ≥ ${f.min_velocity}`);
  if (f.published_within_hours) add("published_within_hours", `Last ${f.published_within_hours}h`);
  if (f.vintage_only) add("vintage_only", "Vintage");
  if (f.ultra_only) add("ultra_only", "Ultra only");
  if (f.include_inactive) add("include_inactive", "Incl. sold");
  if (f.q) add("q", `“${f.q}”`);
  for (const key of ["brands", "categories", "sizes", "conditions", "countries", "demand_levels"] as const) {
    for (const v of f[key] ?? []) {
      chips.push({ key: `${key}:${v}`, label: v, clear: () => update({ ...f, [key]: (f[key] ?? []).filter((x) => x !== v) }) });
    }
  }

  return (
    <div>
      <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <h1 className="enter text-[28px] font-semibold leading-tight tracking-[-0.025em] sm:text-[32px]">{title}</h1>
          <p className="enter mt-1.5 text-sm text-fg-2" style={{ "--i": 1 } as CSSProperties}>
            {description}
            {total !== undefined && <span className="text-fg-3"> · {total.toLocaleString()} results</span>}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Dialog open={open} onOpenChange={setOpen}>
            <DialogTrigger asChild>
              <Button variant="outline" size="sm" className="lg:hidden">
                <SlidersHorizontal /> Filters {count > 0 && <span className="rounded-full bg-accent px-1.5 text-[10px] text-white">{count}</span>}
              </Button>
            </DialogTrigger>
            <DialogContent title="Filters" side="right">
              <FilterPanel filters={f} onChange={update} />
            </DialogContent>
          </Dialog>
          <label className="sr-only" htmlFor="sort">
            Sort by
          </label>
          <div className="w-48">
            <Select id="sort" value={filters.sort} onChange={(e) => update({ ...f, sort: e.target.value })} className="h-8 text-[13px]">
              {SORTS.map((s) => (
                <option key={s.id} value={s.id}>
                  Sort: {s.label}
                </option>
              ))}
            </Select>
          </div>
        </div>
      </div>

      {showPresets && (
        <div className="scrollbar-none -mx-4 mt-4 flex gap-2 overflow-x-auto px-4 pb-1 sm:mx-0 sm:px-0">
          <Chip active={!f.preset} onClick={() => update({ ...f, preset: undefined })}>
            All
          </Chip>
          {prefs.data && (
            <Chip
              onClick={() => {
                const pr = prefs.data!;
                update({
                  sort: f.sort,
                  min_profit: pr.min_profit || undefined,
                  min_roi: pr.min_roi || undefined,
                  max_price: pr.max_purchase_price ?? undefined,
                  min_flip: pr.min_flip_score ?? undefined,
                  max_risk: pr.max_risk_score ?? undefined,
                  min_confidence: pr.min_confidence ?? undefined,
                  brands: pr.preferred_brands.length ? pr.preferred_brands : undefined,
                  categories: pr.preferred_categories.length ? pr.preferred_categories : undefined,
                  sizes: pr.sizes.length ? pr.sizes : undefined,
                });
              }}
            >
              <UserCheck /> My criteria
            </Chip>
          )}
          {PRESETS.map((p) => (
            <Chip key={p.id} active={f.preset === p.id} onClick={() => update({ ...f, preset: f.preset === p.id ? undefined : p.id })}>
              {p.label}
            </Chip>
          ))}
        </div>
      )}

      {chips.length > 0 && (
        <div className="mt-3 flex flex-wrap items-center gap-1.5">
          {chips.map((c) => (
            <button
              key={c.key}
              onClick={c.clear}
              className="inline-flex items-center gap-1 rounded-full bg-surface-2 px-2.5 py-1 text-xs font-medium text-fg-2 hover:bg-surface-3"
            >
              {c.label} <X className="size-3" />
            </button>
          ))}
          <button onClick={() => update({ sort: f.sort, preset: f.preset })} className="px-2 text-xs font-medium text-accent hover:underline">
            Clear all
          </button>
        </div>
      )}

      <div className="mt-5 grid grid-cols-1 gap-6 lg:grid-cols-[260px_minmax(0,1fr)]">
        <aside className="hidden lg:block">
          <div className="sticky top-20 max-h-[calc(100dvh-6rem)] overflow-y-auto rounded-2xl border border-line bg-surface p-4 shadow-card">
            <FilterPanel filters={f} onChange={update} />
          </div>
        </aside>
        <div className={cn("transition-opacity duration-200", feed.isFetching && !feed.isFetchingNextPage && items.length ? "opacity-60" : "opacity-100")}>
          {feed.isError ? (
            <ErrorState message={errorMessage(feed.error)} onRetry={() => feed.refetch()} />
          ) : feed.isLoading ? (
            <div className="grid grid-cols-1 gap-4 min-[480px]:grid-cols-2 xl:grid-cols-3">
              {Array.from({ length: 6 }).map((_, i) => (
                <DealCardSkeleton key={i} index={i} />
              ))}
            </div>
          ) : items.length === 0 ? (
            empty ?? (
              <EmptyState
                icon={<Radar />}
                title="No deals match these filters"
                description="Try relaxing a filter: new listings are analysed continuously, so check back soon."
                action={
                  <Button variant="outline" size="sm" onClick={() => update({ sort: f.sort })}>
                    Reset filters
                  </Button>
                }
              />
            )
          ) : (
            <>
              <div className="grid grid-cols-1 gap-4 min-[480px]:grid-cols-2 xl:grid-cols-3">
                {items.map((d, i) => (
                  <DealCard key={d.id} deal={d} index={i % 24} />
                ))}
              </div>
              {feed.hasNextPage && (
                <div className="mt-6 flex justify-center">
                  <Button variant="outline" onClick={() => feed.fetchNextPage()} loading={feed.isFetchingNextPage}>
                    Load more
                  </Button>
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}
