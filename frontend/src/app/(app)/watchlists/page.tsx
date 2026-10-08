"use client";

import { Bell, BellOff, Pencil, Plus, Target, Trash2 } from "lucide-react";
import { useMemo, useState, type FormEvent, type CSSProperties } from "react";
import { DealRow } from "@/components/deal/deal-card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { EmptyState, Skeleton } from "@/components/ui/feedback";
import { Field, Input, InputAffix } from "@/components/ui/input";
import { Chip, Dialog, DialogContent, Switch } from "@/components/ui/misc";
import { CONDITION_LABEL, eur, pct, timeAgo } from "@/lib/format";
import { useBrands, useCategories, useDeleteWatchlist, useSaveWatchlist, useWatchlistMatches, useWatchlists } from "@/lib/queries";
import type { Watchlist, WatchlistInput } from "@/lib/types";

const EMPTY: WatchlistInput = {
  name: "",
  query: null,
  brand_slugs: [],
  category_slugs: [],
  sizes: [],
  conditions: [],
  countries: [],
  vintage_only: false,
  max_buy_price: null,
  min_profit: null,
  min_roi: null,
  min_flip_score: null,
  min_confidence: null,
  max_risk_score: null,
  is_active: true,
  notify: true,
};

const num = (s: string) => (s.trim() === "" ? null : Number(s.replace(",", ".")));
const toggle = (l: string[], v: string) => (l.includes(v) ? l.filter((x) => x !== v) : [...l, v]);

function WatchlistForm({ initial, onDone }: { initial: Watchlist | null; onDone: () => void }) {
  const save = useSaveWatchlist();
  const brands = useBrands();
  const categories = useCategories();
  const [w, setW] = useState<WatchlistInput>(initial ? { ...EMPTY, ...initial } : EMPTY);
  const [brandQuery, setBrandQuery] = useState("");
  const set = (patch: Partial<WatchlistInput>) => setW({ ...w, ...patch });

  async function submit(e: FormEvent) {
    e.preventDefault();
    await save.mutateAsync({ id: initial?.id, data: w });
    onDone();
  }
  const leafCategories = (categories.data ?? []).filter((c) => c.parent);
  return (
    <form onSubmit={submit} className="space-y-5">
      <Field label="Name" htmlFor="w-name">
        <Input id="w-name" value={w.name} onChange={(e) => set({ name: e.target.value })} placeholder="e.g. Ralph Lauren under €25" required maxLength={120} />
      </Field>
      <Field label="Keywords in title" htmlFor="w-q" hint="All words must appear (optional).">
        <Input id="w-q" value={w.query ?? ""} onChange={(e) => set({ query: e.target.value || null })} placeholder="e.g. half zip" />
      </Field>
      <div>
        <p className="mb-1.5 text-[13px] font-medium text-fg-2">Brands</p>
        <Input placeholder="Filter brands" value={brandQuery} onChange={(e) => setBrandQuery(e.target.value)} className="mb-2 h-9" />
        <div className="flex max-h-32 flex-wrap gap-1.5 overflow-y-auto">
          {(brands.data ?? [])
            .filter((b) => b.name.toLowerCase().includes(brandQuery.toLowerCase()))
            .map((b) => (
              <Chip key={b.slug} active={w.brand_slugs.includes(b.slug)} onClick={() => set({ brand_slugs: toggle(w.brand_slugs, b.slug) })}>
                {b.name}
              </Chip>
            ))}
        </div>
      </div>
      <div>
        <p className="mb-1.5 text-[13px] font-medium text-fg-2">Categories</p>
        <div className="flex flex-wrap gap-1.5">
          {leafCategories.map((c) => (
            <Chip key={c.slug} active={w.category_slugs.includes(c.slug)} onClick={() => set({ category_slugs: toggle(w.category_slugs, c.slug) })}>
              {c.name}
            </Chip>
          ))}
        </div>
      </div>
      <div>
        <p className="mb-1.5 text-[13px] font-medium text-fg-2">Condition</p>
        <div className="flex flex-wrap gap-1.5">
          {Object.entries(CONDITION_LABEL)
            .filter(([k]) => k !== "unknown")
            .map(([k, label]) => (
              <Chip key={k} active={w.conditions.includes(k)} onClick={() => set({ conditions: toggle(w.conditions, k) })}>
                {label}
              </Chip>
            ))}
        </div>
      </div>
      <Field label="Sizes" htmlFor="w-sizes" hint="Comma separated, e.g. M, L, EU42, W32">
        <Input id="w-sizes" value={w.sizes.join(", ")} onChange={(e) => set({ sizes: e.target.value.split(",").map((s) => s.trim().toUpperCase()).filter(Boolean) })} />
      </Field>
      <div className="grid grid-cols-2 gap-3">
        <Field label="Max buy price" htmlFor="w-max">
          <InputAffix id="w-max" prefix="€" inputMode="decimal" value={w.max_buy_price ?? ""} onChange={(e) => set({ max_buy_price: num(e.target.value) })} />
        </Field>
        <Field label="Min profit" htmlFor="w-profit">
          <InputAffix id="w-profit" prefix="€" inputMode="decimal" value={w.min_profit ?? ""} onChange={(e) => set({ min_profit: num(e.target.value) })} />
        </Field>
        <Field label="Min ROI" htmlFor="w-roi">
          <InputAffix
            id="w-roi"
            suffix="%"
            inputMode="decimal"
            value={w.min_roi === null ? "" : Math.round(w.min_roi * 100)}
            onChange={(e) => set({ min_roi: num(e.target.value) === null ? null : (num(e.target.value) as number) / 100 })}
          />
        </Field>
        <Field label="Min Flip Score" htmlFor="w-flip">
          <Input id="w-flip" inputMode="numeric" value={w.min_flip_score ?? ""} onChange={(e) => set({ min_flip_score: num(e.target.value) })} />
        </Field>
        <Field label="Min Confidence" htmlFor="w-conf">
          <Input id="w-conf" inputMode="numeric" value={w.min_confidence ?? ""} onChange={(e) => set({ min_confidence: num(e.target.value) })} />
        </Field>
        <Field label="Max Risk" htmlFor="w-risk">
          <Input id="w-risk" inputMode="numeric" value={w.max_risk_score ?? ""} onChange={(e) => set({ max_risk_score: num(e.target.value) })} />
        </Field>
      </div>
      <div className="space-y-3 rounded-xl bg-surface-2 p-3">
        {(
          [
            ["vintage_only", "Vintage items only"],
            ["notify", "Send me alerts for new matches"],
            ["is_active", "Active"],
          ] as const
        ).map(([key, label]) => (
          <label key={key} className="flex items-center justify-between gap-3">
            <span className="text-[13px] text-fg-2">{label}</span>
            <Switch checked={w[key]} onCheckedChange={(v) => set({ [key]: v })} />
          </label>
        ))}
      </div>
      <p className="text-xs text-fg-3">Without profit, ROI or Flip criteria, a quality floor of Flip Score 60 is applied so you are not flooded with poor deals.</p>
      <Button type="submit" className="w-full" loading={save.isPending}>
        {initial ? "Save changes" : "Create watchlist"}
      </Button>
    </form>
  );
}

function rules(w: Watchlist, names: Map<string, string>): string[] {
  const r: string[] = [];
  if (w.query) r.push(`“${w.query}”`);
  if (w.category_slugs.length) r.push(w.category_slugs.map((s) => names.get(s) ?? s).join(", "));
  if (w.sizes.length) r.push(`size ${w.sizes.join("/")}`);
  if (w.max_buy_price !== null) r.push(`max buy ${eur(w.max_buy_price)}`);
  if (w.min_profit !== null) r.push(`profit ≥ ${eur(w.min_profit)}`);
  if (w.min_roi !== null) r.push(`ROI ≥ ${pct(w.min_roi)}`);
  if (w.min_flip_score !== null) r.push(`Flip ≥ ${w.min_flip_score}`);
  if (w.max_risk_score !== null) r.push(`Risk ≤ ${w.max_risk_score}`);
  if (w.vintage_only) r.push("vintage");
  return r;
}

function Matches({ id }: { id: string }) {
  const m = useWatchlistMatches(id);
  if (m.isLoading) return <Skeleton className="h-24 rounded-xl" />;
  if (!m.data?.items.length) return <p className="py-4 text-center text-[13px] text-fg-3">No current matches — you will be alerted when one appears.</p>;
  return (
    <div className="-mx-2">
      {m.data.items.slice(0, 5).map((d) => (
        <DealRow key={d.id} deal={d} metric="profit" />
      ))}
    </div>
  );
}

export default function WatchlistsPage() {
  const list = useWatchlists();
  const del = useDeleteWatchlist();
  const save = useSaveWatchlist();
  const [editing, setEditing] = useState<Watchlist | null>(null);
  const [open, setOpen] = useState(false);
  const [expanded, setExpanded] = useState<string | null>(null);
  const brands = useBrands();
  const categories = useCategories();
  const names = useMemo(
    () => new Map([...(brands.data ?? []), ...(categories.data ?? [])].map((x) => [x.slug, x.name] as const)),
    [brands.data, categories.data],
  );

  return (
    <div className="space-y-6">
      <div className="flex items-end justify-between gap-3">
        <div>
          <h1 className="enter text-[28px] font-semibold leading-tight tracking-[-0.025em] sm:text-[32px]">Watchlists</h1>
          <p className="enter mt-1.5 text-sm text-fg-2" style={{ "--i": 1 } as CSSProperties}>Your buying strategies. Matching deals trigger alerts on your channels.</p>
        </div>
        <Button
          onClick={() => {
            setEditing(null);
            setOpen(true);
          }}
        >
          <Plus /> New watchlist
        </Button>
      </div>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent title={editing ? "Edit watchlist" : "New watchlist"} description="Combine brands, categories and economic targets." side="right">
          <WatchlistForm key={editing?.id ?? "new"} initial={editing} onDone={() => setOpen(false)} />
        </DialogContent>
      </Dialog>

      {list.isLoading ? (
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
          {Array.from({ length: 4 }).map((_, i) => (
            <Skeleton key={i} className="h-40 rounded-2xl" />
          ))}
        </div>
      ) : !list.data?.length ? (
        <EmptyState
          icon={<Target />}
          title="No watchlists yet"
          description="For example: Ralph Lauren, max buy €25, min profit €15, min ROI 50%."
          action={
            <Button onClick={() => setOpen(true)}>
              <Plus /> Create your first watchlist
            </Button>
          }
        />
      ) : (
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
          {list.data.map((w, i) => (
            <Card key={w.id} className={`enter lift p-5 ${w.is_active ? "" : "opacity-60"}`} style={{ "--i": i } as CSSProperties}>
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    <h2 className="truncate text-[15px] font-semibold">{w.name}</h2>
                    {!w.is_active && <Badge tone="neutral">Paused</Badge>}
                  </div>
                  <p className="mt-1 text-[13px] text-fg-3">
                    {w.brand_slugs.length ? w.brand_slugs.map((s) => names.get(s) ?? s).join(", ") : "All brands"}
                    {rules(w, names).length ? ` · ${rules(w, names).join(" · ")}` : ""}
                  </p>
                </div>
                <div className="flex shrink-0 items-center gap-1">
                  <Button
                    variant="ghost"
                    size="icon-sm"
                    aria-label={w.notify ? "Mute alerts" : "Enable alerts"}
                    onClick={() => save.mutate({ id: w.id, data: { ...w, notify: !w.notify } })}
                  >
                    {w.notify ? <Bell className="text-accent" /> : <BellOff />}
                  </Button>
                  <Button
                    variant="ghost"
                    size="icon-sm"
                    aria-label="Edit"
                    onClick={() => {
                      setEditing(w);
                      setOpen(true);
                    }}
                  >
                    <Pencil />
                  </Button>
                  <Button variant="ghost" size="icon-sm" aria-label="Delete" onClick={() => confirm(`Delete “${w.name}”?`) && del.mutate(w.id)}>
                    <Trash2 />
                  </Button>
                </div>
              </div>
              <div className="mt-4 flex items-center justify-between">
                <p className="text-[13px]">
                  <span className="text-xl font-semibold tnum">{w.match_count ?? 0}</span> <span className="text-fg-3">{(w.match_count ?? 0) === 1 ? "matching deal" : "matching deals"} now</span>
                </p>
                <p className="text-xs text-fg-3">{w.last_matched_at ? `last alert ${timeAgo(w.last_matched_at)}` : "no alerts yet"}</p>
              </div>
              <Button variant="outline" size="sm" className="mt-3 w-full" onClick={() => setExpanded(expanded === w.id ? null : w.id)}>
                {expanded === w.id ? "Hide matches" : "Show matches"}
              </Button>
              {expanded === w.id && (
                <div className="mt-3">
                  <Matches id={w.id} />
                </div>
              )}
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}
