import type { OpportunityFilters } from "./types";

const ARRAY_KEYS = ["brands", "categories", "sizes", "conditions", "countries", "demand_levels"] as const;
const NUMBER_KEYS = [
  "min_price", "max_price", "min_profit", "min_roi", "min_flip", "min_confidence", "max_risk", "min_velocity",
  "published_within_hours", "page",
] as const;
const BOOL_KEYS = ["vintage_only", "ultra_only", "include_inactive"] as const;
const STRING_KEYS = ["q", "preset", "sort", "state"] as const;

export const FILTER_KEYS: string[] = [...ARRAY_KEYS, ...NUMBER_KEYS, ...BOOL_KEYS, ...STRING_KEYS];

/** URL search params <-> feed filters (so every filtered view is shareable/bookmarkable). */
export function filtersFromParams(params: URLSearchParams): OpportunityFilters {
  const f: Record<string, unknown> = {};
  for (const k of ARRAY_KEYS) {
    const v = params.getAll(k).filter(Boolean);
    if (v.length) f[k] = v;
  }
  for (const k of NUMBER_KEYS) {
    const raw = params.get(k);
    if (raw !== null && raw !== "" && !Number.isNaN(Number(raw))) f[k] = Number(raw);
  }
  for (const k of BOOL_KEYS) if (params.get(k) === "true") f[k] = true;
  for (const k of STRING_KEYS) {
    const v = params.get(k);
    if (v) f[k] = v;
  }
  return f as OpportunityFilters;
}

export function paramsFromFilters(f: OpportunityFilters): URLSearchParams {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(f)) {
    if (v === undefined || v === null || v === "" || v === false) continue;
    if (Array.isArray(v)) v.forEach((x) => p.append(k, String(x)));
    else p.set(k, String(v));
  }
  return p;
}

export function activeFilterCount(f: OpportunityFilters): number {
  const ignore = new Set(["page", "page_size", "sort", "preset", "q"]);
  return Object.entries(f).filter(([k, v]) => !ignore.has(k) && v !== undefined && v !== false && !(Array.isArray(v) && !v.length)).length;
}

export const PRESETS: { id: string; label: string; hint: string }[] = [
  { id: "best_deals", label: "Best Deals", hint: "Flip Score ≥ 70" },
  { id: "ultra", label: "🔥 Ultra Deals", hint: "Flip > 90 · Confidence > 80 · ROI > 60%" },
  { id: "high_profit", label: "High Profit", hint: "Profit ≥ €20" },
  { id: "high_roi", label: "High ROI", hint: "ROI ≥ 80%" },
  { id: "fast_flip", label: "Fast Flip", hint: "Velocity ≥ 70" },
  { id: "low_risk", label: "Low Risk", hint: "Risk ≤ 25 · Confidence ≥ 60" },
  { id: "just_listed", label: "Just Listed", hint: "Published in the last 6 hours" },
  { id: "hidden_gems", label: "Hidden Gems", hint: "Poorly described but undervalued" },
  { id: "under_20", label: "Under €20", hint: "Price ≤ €20 with profit" },
];

export const SORTS: { id: string; label: string }[] = [
  { id: "expected", label: "Risk-adjusted profit" },
  { id: "flip", label: "Flip Score" },
  { id: "personal", label: "Personal Score" },
  { id: "profit", label: "Profit" },
  { id: "roi", label: "ROI" },
  { id: "discount", label: "Discount vs market" },
  { id: "newest", label: "Newest" },
  { id: "velocity", label: "Fastest to sell" },
  { id: "confidence", label: "Confidence" },
  { id: "risk_asc", label: "Lowest risk" },
  { id: "price_asc", label: "Lowest price" },
];
