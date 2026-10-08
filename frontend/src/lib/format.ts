// Display formatting. Money is shown the way resellers read it: "€18", "€17.50", "+€17".

const eurFmt = (digits: number) =>
  new Intl.NumberFormat("en-IE", { style: "currency", currency: "EUR", minimumFractionDigits: digits, maximumFractionDigits: digits });

export function eur(value: number | null | undefined, opts: { sign?: boolean; compact?: boolean } = {}): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  const abs = Math.abs(value);
  let body: string;
  if (opts.compact && abs >= 10_000) {
    body = new Intl.NumberFormat("en-IE", { style: "currency", currency: "EUR", notation: "compact", maximumFractionDigits: 1 }).format(abs);
  } else {
    const whole = Math.abs(abs - Math.round(abs)) < 0.005;
    body = eurFmt(whole ? 0 : 2).format(whole ? Math.round(abs) : abs);
  }
  const sign = value < 0 ? "−" : opts.sign && value > 0 ? "+" : "";
  return `${sign}${body}`;
}

export function pct(ratio: number | null | undefined, opts: { sign?: boolean; digits?: number } = {}): string {
  if (ratio === null || ratio === undefined || Number.isNaN(ratio)) return "—";
  const v = ratio * 100;
  const s = Math.abs(v).toFixed(opts.digits ?? 0);
  const sign = v < 0 ? "−" : opts.sign && v > 0 ? "+" : "";
  return `${sign}${s}%`;
}

export function num(value: number | null | undefined, digits = 0): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return new Intl.NumberFormat("en-IE", { maximumFractionDigits: digits, minimumFractionDigits: digits }).format(value);
}

export function compactNum(value: number): string {
  return new Intl.NumberFormat("en-IE", { notation: "compact", maximumFractionDigits: 1 }).format(value);
}

export function timeAgo(iso: string | null | undefined, now: number = Date.now()): string {
  if (!iso) return "—";
  const diff = Math.max(0, now - new Date(iso).getTime()) / 1000;
  if (diff < 60) return "just now";
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  const days = Math.floor(diff / 86400);
  if (days < 30) return `${days}d ago`;
  return new Date(iso).toLocaleDateString("en-GB", { day: "numeric", month: "short" });
}

export function shortDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" });
}

export function days(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  if (value < 1) return "< 1 day";
  return `~${Math.round(value)} day${Math.round(value) === 1 ? "" : "s"}`;
}

export const CONDITION_LABEL: Record<string, string> = {
  new_with_tags: "New with tags",
  new_without_tags: "New without tags",
  very_good: "Very good",
  good: "Good",
  satisfactory: "Satisfactory",
  unknown: "Unknown",
};

export const DEMAND_LABEL: Record<string, string> = {
  very_low: "Very Low",
  low: "Low",
  medium: "Medium",
  high: "High",
  very_high: "Very High",
};

export const TIER_LABEL: Record<string, string> = {
  exceptional: "Exceptional Deal",
  excellent: "Excellent Deal",
  good: "Good Deal",
  moderate: "Moderate Deal",
  low_priority: "Low Priority",
};

export const RISK_LABEL: Record<string, string> = {
  low: "Low",
  moderate: "Moderate",
  high: "High",
  very_high: "Very High",
};

export const ACTION_LABEL: Record<string, string> = {
  buy_now: "Buy now",
  make_offer: "Make an offer",
  watch: "Watch for a price drop",
  skip: "Skip",
};

/** "1 flip", "3 flips" — English count + noun (irregular plural optional). */
export function plural(count: number, noun: string, pluralNoun = `${noun}s`): string {
  return `${count.toLocaleString("en-US")} ${count === 1 ? noun : pluralNoun}`;
}

export const STATUS_LABEL: Record<string, string> = {
  active: "Active",
  reserved: "Reserved",
  sold: "Sold",
  removed: "Removed",
  unknown: "Unknown",
};

export const MODE_LABEL: Record<string, string> = {
  provider_scan: "Market scan",
  extension_item: "Extension · item page",
  extension_card: "Extension · seen while scrolling",
  extension_deep: "Extension · deep analysis",
  extension_refresh: "Extension · status check",
  extension_scan: "Extension · automatic scan",
  batch_import: "Search page import",
  link_import: "Link import",
  manual_form: "Analyze form",
  bookmarklet: "Bookmarklet",
  email: "Vinted email",
  public_fetch: "Public page (server)",
  migrated: "Before tracking",
};

export const CAPTURE_LABEL: Record<string, string> = {
  link: "Link only",
  card: "Card data",
  full: "Full listing",
};
