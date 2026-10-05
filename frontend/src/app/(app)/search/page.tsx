"use client";

import { ArrowRight, Search as SearchIcon, Sparkles } from "lucide-react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState, type FormEvent } from "react";
import { DealCard, DealCardSkeleton } from "@/components/deal/deal-card";
import { Button } from "@/components/ui/button";
import { EmptyState, ErrorState } from "@/components/ui/feedback";
import { errorMessage } from "@/lib/api";
import { paramsFromFilters } from "@/lib/filters";
import { plural } from "@/lib/format";
import { usePopularSearches, useSearch } from "@/lib/queries";

const EXAMPLES = [
  "fammi vedere felpe Ralph Lauren sotto 25 euro con almeno 50% ROI",
  "Ralph Lauren hoodie M under 20",
  "sneakers nike 42 tra 30 e 60 euro profitto almeno 15",
  "maglie calcio vintage anni 90",
  "piumini The North Face basso rischio",
  "giacche Carhartt vendita veloce",
];

function SearchInner() {
  const params = useSearchParams();
  const router = useRouter();
  const q = params.get("q") ?? "";
  const [value, setValue] = useState(q);
  const [page, setPage] = useState(1);
  const [prevQ, setPrevQ] = useState(q);
  if (prevQ !== q) {
    setPrevQ(q);
    setValue(q);
    setPage(1);
  }
  const res = useSearch(q, page);
  const popular = usePopularSearches();

  function submit(e: FormEvent) {
    e.preventDefault();
    if (value.trim()) router.push(`/search?q=${encodeURIComponent(value.trim())}`);
  }

  const data = res.data;
  return (
    <div className="space-y-6">
      <div>
        <h1 className="flex items-center gap-2 text-2xl font-semibold tracking-tight">
          <Sparkles className="size-5 text-accent" /> Smart search
        </h1>
        <p className="mt-1 text-sm text-fg-2">Describe what you want, in Italian or English. FlipFinder turns it into precise filters.</p>
      </div>
      <form onSubmit={submit} className="relative" role="search">
        <SearchIcon className="pointer-events-none absolute left-4 top-1/2 size-5 -translate-y-1/2 text-fg-3" />
        <input
          value={value}
          onChange={(e) => setValue(e.target.value)}
          autoFocus
          aria-label="Search query"
          placeholder="e.g. felpe Ralph Lauren sotto 25 euro con almeno 50% ROI"
          className="h-14 w-full rounded-2xl border border-line-strong bg-surface pl-12 pr-28 text-[15px] shadow-card focus:border-accent focus:outline-none focus:ring-4 focus:ring-[var(--ring)]"
        />
        <Button type="submit" className="absolute right-2 top-1/2 -translate-y-1/2">
          Search
        </Button>
      </form>

      {!q && (
        <div className="grid grid-cols-1 gap-6 md:grid-cols-2">
          <div>
            <p className="mb-2 text-xs font-semibold uppercase tracking-[0.08em] text-fg-3">Try</p>
            <div className="flex flex-wrap gap-2">
              {EXAMPLES.map((e) => (
                <Link key={e} href={`/search?q=${encodeURIComponent(e)}`} className="rounded-full border border-line bg-surface px-3 py-1.5 text-[13px] text-fg-2 hover:border-accent hover:text-accent">
                  {e}
                </Link>
              ))}
            </div>
          </div>
          {popular.data && popular.data.length > 0 && (
            <div>
              <p className="mb-2 text-xs font-semibold uppercase tracking-[0.08em] text-fg-3">Popular searches</p>
              <div className="flex flex-wrap gap-2">
                {popular.data.map((p) => (
                  <Link key={p.query} href={`/search?q=${encodeURIComponent(p.query)}`} className="rounded-full bg-surface-2 px-3 py-1.5 text-[13px] text-fg-2 hover:text-fg">
                    {p.query} <span className="text-fg-3">· {p.count}</span>
                  </Link>
                ))}
              </div>
            </div>
          )}
        </div>
      )}

      {q && data && (
        <div className="flex flex-col gap-3 rounded-2xl border border-line bg-surface p-4 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <p className="text-xs font-medium text-fg-3">Understood as</p>
            <div className="mt-1.5 flex flex-wrap gap-1.5">
              {data.parsed.understood.length ? (
                data.parsed.understood.map((u) => (
                  <span key={u} className="rounded-full bg-accent-soft px-2.5 py-0.5 text-xs font-medium text-accent">
                    {u}
                  </span>
                ))
              ) : (
                <span className="text-[13px] text-fg-3">Free-text search on titles</span>
              )}
            </div>
          </div>
          <Button asChild variant="outline" size="sm">
            <Link href={`/deals?${paramsFromFilters(data.parsed.filters).toString()}`}>
              Refine in Deals <ArrowRight />
            </Link>
          </Button>
        </div>
      )}

      {q && res.isError && <ErrorState message={errorMessage(res.error)} onRetry={() => res.refetch()} />}
      {q && res.isLoading && (
        <div className="grid grid-cols-1 gap-4 min-[480px]:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
          {Array.from({ length: 4 }).map((_, i) => (
            <DealCardSkeleton key={i} />
          ))}
        </div>
      )}
      {q && data && (
        <>
          <p className="text-sm text-fg-3">{plural(data.results.total, "matching deal")}</p>
          {data.results.items.length === 0 ? (
            <EmptyState icon={<SearchIcon />} title="No deals match this search" description="Try a higher price limit or fewer constraints. New listings are analysed continuously." />
          ) : (
            <div className="grid grid-cols-1 gap-4 min-[480px]:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
              {data.results.items.map((d) => (
                <DealCard key={d.id} deal={d} />
              ))}
            </div>
          )}
          {data.results.has_more && (
            <div className="flex justify-center">
              <Button variant="outline" onClick={() => setPage(page + 1)}>
                Next page
              </Button>
            </div>
          )}
        </>
      )}
    </div>
  );
}

export default function SearchPage() {
  return (
    <Suspense>
      <SearchInner />
    </Suspense>
  );
}
