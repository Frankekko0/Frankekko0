"use client";

import { RotateCcw } from "lucide-react";
import { useMemo, useState, type ReactNode } from "react";
import { CONDITION_LABEL, DEMAND_LABEL } from "@/lib/format";
import { useBrands, useCategories } from "@/lib/queries";
import type { OpportunityFilters } from "@/lib/types";
import { Button } from "@/components/ui/button";
import { Input, InputAffix, Label, Select } from "@/components/ui/input";
import { Chip, RangeSlider, Switch } from "@/components/ui/misc";

const LETTER_SIZES = ["XS", "S", "M", "L", "XL", "XXL"];
const SHOE_SIZES = ["EU39", "EU40", "EU41", "EU42", "EU43", "EU44", "EU45"];
const COUNTRIES = ["IT", "FR", "ES", "DE", "BE", "NL", "PT"];
const CONDITIONS = ["new_with_tags", "new_without_tags", "very_good", "good", "satisfactory"];
const DEMAND = ["very_high", "high", "medium", "low", "very_low"];

function toggle<T>(list: T[] | undefined, value: T): T[] {
  const l = list ?? [];
  return l.includes(value) ? l.filter((x) => x !== value) : [...l, value];
}

function Group({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="border-b border-line py-4 first:pt-0 last:border-0">
      <p className="mb-2.5 text-xs font-semibold uppercase tracking-[0.08em] text-fg-3">{title}</p>
      {children}
    </section>
  );
}

function NumberFilter({
  label,
  value,
  onChange,
  prefix,
  suffix,
  scale = 1,
}: {
  label: string;
  value: number | undefined;
  onChange: (v: number | undefined) => void;
  prefix?: string;
  suffix?: string;
  scale?: number;
}) {
  const shown = value === undefined ? "" : String(Math.round(value * scale * 100) / 100);
  const [draft, setDraft] = useState(shown);
  const [prev, setPrev] = useState(shown);
  if (prev !== shown) {
    setPrev(shown);
    setDraft(shown);
  }
  const commit = () => {
    const v = draft.trim() === "" ? undefined : Number(draft.replace(",", ".")) / scale;
    if (v === undefined || !Number.isNaN(v)) onChange(v);
  };
  return (
    <div>
      <Label>{label}</Label>
      <InputAffix
        inputMode="decimal"
        prefix={prefix}
        suffix={suffix}
        value={draft}
        placeholder="Any"
        onChange={(e) => setDraft(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => e.key === "Enter" && commit()}
        className="h-9"
      />
    </div>
  );
}

export function FilterPanel({ filters, onChange }: { filters: OpportunityFilters; onChange: (f: OpportunityFilters) => void }) {
  const brands = useBrands();
  const categories = useCategories();
  const [brandQuery, setBrandQuery] = useState("");
  const set = (patch: Partial<OpportunityFilters>) => onChange({ ...filters, ...patch, page: undefined });
  const visibleBrands = useMemo(
    () => (brands.data ?? []).filter((b) => b.name.toLowerCase().includes(brandQuery.toLowerCase())),
    [brands.data, brandQuery],
  );
  const parents = (categories.data ?? []).filter((c) => !c.parent);

  return (
    <div className="text-sm">
      <Group title="Economics">
        <div className="grid grid-cols-2 gap-3">
          <NumberFilter label="Min price" prefix="€" value={filters.min_price} onChange={(v) => set({ min_price: v })} />
          <NumberFilter label="Max price" prefix="€" value={filters.max_price} onChange={(v) => set({ max_price: v })} />
          <NumberFilter label="Min profit" prefix="€" value={filters.min_profit} onChange={(v) => set({ min_profit: v })} />
          <NumberFilter label="Min ROI" suffix="%" scale={100} value={filters.min_roi} onChange={(v) => set({ min_roi: v })} />
        </div>
      </Group>

      <Group title="Scores">
        <div className="space-y-4">
          <RangeSlider label="Min Flip Score" value={filters.min_flip ?? 0} onCommit={(v) => set({ min_flip: v || undefined })} />
          <RangeSlider label="Min Confidence" value={filters.min_confidence ?? 0} onCommit={(v) => set({ min_confidence: v || undefined })} />
          <RangeSlider label="Max Risk" value={filters.max_risk ?? 100} onCommit={(v) => set({ max_risk: v === 100 ? undefined : v })} />
          <RangeSlider label="Min Velocity" value={filters.min_velocity ?? 0} onCommit={(v) => set({ min_velocity: v || undefined })} />
        </div>
      </Group>

      <Group title="Brand">
        <Input placeholder="Search brands" value={brandQuery} onChange={(e) => setBrandQuery(e.target.value)} className="mb-2 h-9" />
        <div className="max-h-44 space-y-0.5 overflow-y-auto pr-1">
          {visibleBrands.map((b) => (
            <label key={b.slug} className="flex cursor-pointer items-center gap-2 rounded-md px-1.5 py-1 hover:bg-surface-2">
              <input
                type="checkbox"
                className="size-4 accent-[var(--accent)]"
                checked={filters.brands?.includes(b.slug) ?? false}
                onChange={() => set({ brands: toggle(filters.brands, b.slug) })}
              />
              <span className="text-[13px] text-fg">{b.name}</span>
            </label>
          ))}
        </div>
      </Group>

      <Group title="Category">
        <div className="space-y-3">
          {parents.map((p) => (
            <div key={p.slug}>
              <p className="mb-1.5 text-xs text-fg-3">{p.name}</p>
              <div className="flex flex-wrap gap-1.5">
                {(categories.data ?? [])
                  .filter((c) => c.parent === p.slug)
                  .map((c) => (
                    <Chip key={c.slug} active={filters.categories?.includes(c.slug)} onClick={() => set({ categories: toggle(filters.categories, c.slug) })}>
                      {c.name}
                    </Chip>
                  ))}
              </div>
            </div>
          ))}
        </div>
      </Group>

      <Group title="Size">
        <div className="flex flex-wrap gap-1.5">
          {[...LETTER_SIZES, ...SHOE_SIZES].map((s) => (
            <Chip key={s} active={filters.sizes?.includes(s)} onClick={() => set({ sizes: toggle(filters.sizes, s) })}>
              {s.replace("EU", "EU ")}
            </Chip>
          ))}
        </div>
      </Group>

      <Group title="Condition">
        <div className="flex flex-wrap gap-1.5">
          {CONDITIONS.map((c) => (
            <Chip key={c} active={filters.conditions?.includes(c)} onClick={() => set({ conditions: toggle(filters.conditions, c) })}>
              {CONDITION_LABEL[c]}
            </Chip>
          ))}
        </div>
      </Group>

      <Group title="Demand">
        <div className="flex flex-wrap gap-1.5">
          {DEMAND.map((d) => (
            <Chip key={d} active={filters.demand_levels?.includes(d)} onClick={() => set({ demand_levels: toggle(filters.demand_levels, d) })}>
              {DEMAND_LABEL[d]}
            </Chip>
          ))}
        </div>
      </Group>

      <Group title="Listing">
        <div className="space-y-3">
          <div>
            <Label>Published</Label>
            <Select
              value={filters.published_within_hours ?? ""}
              onChange={(e) => set({ published_within_hours: e.target.value ? Number(e.target.value) : undefined })}
              className="h-9"
            >
              <option value="">Any time</option>
              <option value="1">Last hour</option>
              <option value="6">Last 6 hours</option>
              <option value="24">Last 24 hours</option>
              <option value="72">Last 3 days</option>
              <option value="168">Last 7 days</option>
            </Select>
          </div>
          <div>
            <Label>Country</Label>
            <div className="flex flex-wrap gap-1.5">
              {COUNTRIES.map((c) => (
                <Chip key={c} active={filters.countries?.includes(c)} onClick={() => set({ countries: toggle(filters.countries, c) })}>
                  {c}
                </Chip>
              ))}
            </div>
          </div>
          {(
            [
              ["vintage_only", "Vintage only"],
              ["ultra_only", "Ultra deals only"],
              ["include_inactive", "Include sold / removed"],
            ] as const
          ).map(([key, label]) => (
            <label key={key} className="flex items-center justify-between gap-3 py-0.5">
              <span className="text-[13px] text-fg-2">{label}</span>
              <Switch checked={Boolean(filters[key])} onCheckedChange={(v) => set({ [key]: v || undefined })} />
            </label>
          ))}
        </div>
      </Group>

      <Button variant="ghost" size="sm" className="mt-2 w-full" onClick={() => onChange({ sort: filters.sort })}>
        <RotateCcw /> Reset filters
      </Button>
    </div>
  );
}
