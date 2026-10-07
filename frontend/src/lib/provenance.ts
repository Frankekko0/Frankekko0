// Where the numbers of an analysis come from: plain helpers over `provenance` (no React), so the
// Analysis page, the item page and the tests read it the same way.

import { CONDITION_LABEL, eur, pct, plural } from "./format";
import type { EvidenceMetrics, ExternalReference, Provenance } from "./types";

/**
 * The provenance when it can be shown, else null: analyses made before it was recorded have none,
 * and a malformed value (missing its main parts) is treated the same way.
 */
export function readProvenance(value: unknown): Provenance | null {
  if (!value || typeof value !== "object") return null;
  const p = value as Partial<Provenance>;
  if (!p.expected_price || typeof p.expected_price !== "object" || !p.real_sales || typeof p.real_sales !== "object") return null;
  return {
    ...(p as Provenance),
    external: Array.isArray(p.external) ? p.external : [],
    new_price: p.new_price && typeof p.new_price === "object" ? p.new_price : null,
  };
}

const n0 = (v: number | null | undefined) => (typeof v === "number" && Number.isFinite(v) && v > 0 ? v : 0);

/** Concluded sales behind the estimate, per origin, plus the asking prices used next to them. */
export function salesMix(p: Provenance) {
  const by = p.expected_price.by_source ?? {};
  const own = n0(p.real_sales.own);
  const vinted = n0(p.real_sales.vinted_sold);
  const external = n0(p.real_sales.external_sold);
  return {
    total: n0(p.real_sales.total) || own + vinted + external,
    own,
    vinted,
    external,
    asking: n0(by.vinted_asking) + n0(by.external_asking),
  };
}

/** "1 yours, 6 Vinted, 2 other markets" (only the origins that contributed). */
export function salesParts(mix: { own: number; vinted: number; external: number }): string {
  const parts: string[] = [];
  if (mix.own) parts.push(`${mix.own} yours`);
  if (mix.vinted) parts.push(`${mix.vinted} Vinted`);
  if (mix.external) parts.push(`${mix.external} other market${mix.external === 1 ? "" : "s"}`);
  return parts.join(", ");
}

/** The headline under every estimate: how many real (concluded) sales it rests on. */
export function realSalesHeadline(p: Provenance): string {
  const mix = salesMix(p);
  if (mix.total > 0) {
    const parts = salesParts(mix);
    return `Based on ${plural(mix.total, "real sale")}${parts ? `: ${parts}` : ""}`;
  }
  switch (p.expected_price.basis) {
    case "asking":
      return "No real sales: estimate from asking prices";
    case "prior":
      return "No real sales: estimate from segment statistics";
    case "none":
      return "No real sales and too few comparables for an estimate";
    default:
      return mix.asking > 0 ? "No real sales: estimate from asking prices" : "No real sales behind this estimate";
  }
}

/** A few words next to a figure (e.g. "9 real sales"), for tight spots like the Decision tiles. */
export function shortBasis(p: Provenance): string | null {
  const mix = salesMix(p);
  switch (p.expected_price.basis) {
    case "sold":
      return mix.total ? plural(mix.total, "real sale") : "real sales";
    case "mixed":
      return `${plural(mix.total, "real sale")} + ${plural(mix.asking, "asking price")}`;
    case "asking":
      return "asking prices only";
    case "prior":
      return "segment statistics";
    default:
      return null;
  }
}

export const DAYS_BASIS_SHORT: Record<string, string> = {
  sold: "similar items sold",
  segment: "segment average",
  category_baseline: "category baseline",
};

export const EXPECTED_BASIS_LABEL: Record<string, string> = {
  sold: "real sales",
  mixed: "sales + asking",
  asking: "asking prices",
  prior: "segment stats",
  none: "insufficient",
};

export const RANGE_BASIS_LABEL: Record<string, string> = { calibration: "calibrated", percentiles: "percentiles" };

export const PROBABILITY_BASIS_LABEL: Record<string, string> = {
  similar: "similar listings",
  segment: "segment",
  insufficient: "insufficient",
};

export interface LastPriceNote {
  /** Always shown when Vinted sold prices are in the estimate. */
  text: string;
  discount: number | null;
  discountText: string;
}

/**
 * Vinted's sold price is the last price seen on the listing, not always the price paid: when such
 * sales are in the estimate, say so, with the negotiation discount applied (or why it is not).
 */
export function lastPriceNote(p: Provenance): LastPriceNote | null {
  const vinted = n0(p.real_sales.vinted_sold) || n0(p.expected_price.by_source?.vinted_sold);
  if (!vinted) return null;
  const d = p.expected_price.negotiation_discount;
  const discount = typeof d === "number" && d > 0 ? d : null;
  return {
    text: `Vinted sales use the last price seen on the listing, which is not always the price paid.`,
    discount,
    discountText:
      discount !== null
        ? `Negotiation discount of ${pct(discount)} applied, measured on your own purchases.`
        : "Negotiation discount not measured yet (needs a few of your purchases): Vinted prices are used as seen.",
  };
}

export const KIND_LABEL: Record<ExternalReference["kind"], string> = { new: "New", asking: "Asking", sold: "Sold" };

/** Only http(s) links are rendered: the URLs come from search results. */
export function safeUrl(url: string | null | undefined): string | null {
  if (!url) return null;
  try {
    const u = new URL(url);
    return u.protocol === "https:" || u.protocol === "http:" ? u.toString() : null;
  } catch {
    return null;
  }
}

/** "ebay.it": the stored source, else the link's host without "www.". */
export function sourceDomain(source: string | null | undefined, url?: string | null): string {
  const s = (source ?? "").trim();
  if (s) return s.replace(/^www\./, "");
  const safe = safeUrl(url);
  return safe ? new URL(safe).hostname.replace(/^www\./, "") : "unknown source";
}

/** A price in its own currency: "€44", "£40.00", "$45.50". */
export function money(value: number | null | undefined, currency: string | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  const cur = (currency || "EUR").toUpperCase();
  if (cur === "EUR") return eur(value);
  try {
    const whole = Math.abs(value - Math.round(value)) < 0.005;
    return new Intl.NumberFormat("en-IE", {
      style: "currency",
      currency: cur,
      minimumFractionDigits: whole ? 0 : 2,
      maximumFractionDigits: whole ? 0 : 2,
    }).format(value);
  } catch {
    return `${value.toFixed(2)} ${cur}`;
  }
}

/** "20 Sep 2026" from a date-only "2026-09-20" (read as a calendar day, not UTC midnight). */
export function dayDate(value: string | null | undefined): string {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(value ?? "");
  if (!m) return "—";
  const d = new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]));
  return d.toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" });
}

/** Condition of an external price, in the app's words ("condition not stated" when unknown). */
export function conditionLabel(c: string | null | undefined): string {
  const v = (c ?? "").trim().toLowerCase();
  if (!v || v === "unknown") return "condition not stated";
  if (v === "used") return "Used";
  if (v === "new") return "New";
  return CONDITION_LABEL[v] ?? v;
}

/** External references, those used in the estimate first (sold, then asking), then the rest. */
export function sortedReferences(refs: ExternalReference[]): ExternalReference[] {
  const rank = { sold: 0, asking: 1, new: 2 } as const;
  return [...refs].sort(
    (a, b) =>
      Number(b.used_in_estimate) - Number(a.used_in_estimate) || (rank[a.kind] ?? 3) - (rank[b.kind] ?? 3) || (b.date ?? "").localeCompare(a.date ?? ""),
  );
}

/* ----------------------------------------------------------- price data (Settings) */

/** How much the extra sources move the mean absolute error (EUR); null when not measured. */
export function accuracyDelta(without: EvidenceMetrics | null | undefined, withExt: EvidenceMetrics | null | undefined): number | null {
  const a = without?.mae_eur;
  const b = withExt?.mae_eur;
  if (typeof a !== "number" || typeof b !== "number") return null;
  return Math.round((b - a) * 100) / 100;
}

/** "lowers the mean error by €0.80" / "raises it by €1.20" / "leaves it unchanged". */
export function describeDelta(delta: number | null): string | null {
  if (delta === null) return null;
  if (Math.abs(delta) < 0.005) return "Prices from other markets leave the mean error unchanged.";
  return delta < 0
    ? `Prices from other markets lower the mean error by ${eur(-delta)}.`
    : `Prices from other markets raise the mean error by ${eur(delta)}.`;
}

/** Share of a budget used, clipped to 0..1 (0 when the budget is unknown). */
export function usedShare(used: number | null | undefined, budget: number | null | undefined): number {
  if (!used || !budget || budget <= 0) return 0;
  return Math.min(1, Math.max(0, used / budget));
}

/** Expected monthly cost of the external search in USD (null when unknown). */
export function monthlyCostUsd(queries: number | null | undefined, costPerQuery: number | null | undefined): number | null {
  if (typeof queries !== "number" || typeof costPerQuery !== "number") return null;
  return Math.round(queries * costPerQuery * 100) / 100;
}

export function usd(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  const digits = value !== 0 && Math.abs(value) < 0.01 ? 3 : 2;
  return new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", minimumFractionDigits: digits, maximumFractionDigits: digits }).format(value);
}

export const REJECT_LABEL: Record<string, string> = {
  kids: "kids' sizes",
  replica: "replicas",
  lot: "lots",
  other_model: "other models",
  brand: "brand not named",
  model: "model not named",
  accessory: "parts & accessories",
  not_item: "not a single item",
  source: "excluded sources",
  no_price: "no price",
  currency: "unknown currency",
  price: "absurd price",
  outlier: "implausible price",
};

/** Rejection reasons with a count, largest first. */
export function rejectedList(rejected: Record<string, number> | null | undefined): { reason: string; label: string; n: number }[] {
  return Object.entries(rejected ?? {})
    .filter(([, n]) => typeof n === "number" && n > 0)
    .map(([reason, n]) => ({ reason, label: REJECT_LABEL[reason] ?? reason.replace(/_/g, " "), n }))
    .sort((a, b) => b.n - a.n);
}
