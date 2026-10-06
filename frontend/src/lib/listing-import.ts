/**
 * Listing drafts for the Analyze page, including one-click imports from the
 * "FlipFinder for Vinted" browser extension (`/analyze#import=<base64url JSON>`).
 *
 * The payload comes from another site's page, so it is treated as untrusted input: every
 * field is type-checked, trimmed and length-capped, and only http(s) links survive.
 */

export interface ListingDraft {
  url: string;
  title: string;
  price: string;
  shipping: string;
  brand: string;
  category: string;
  size: string;
  condition: string;
  color: string;
  description: string;
  images: string;
  sellerName: string;
  sellerRating: string;
  sellerReviews: string;
}

export const EMPTY_DRAFT: ListingDraft = {
  url: "",
  title: "",
  price: "",
  shipping: "",
  brand: "",
  category: "",
  size: "",
  condition: "",
  color: "",
  description: "",
  images: "",
  sellerName: "",
  sellerRating: "",
  sellerReviews: "",
};

export interface ImportedListing {
  source: string;
  draft: ListingDraft;
  /** Fields the user still has to fill in before the analysis can run. */
  missing: ("url" | "title" | "price")[];
}

export const CONDITIONS = new Set(["new_with_tags", "new_without_tags", "very_good", "good", "satisfactory"]);
const MAX_PAYLOAD = 200_000;

export function str(value: unknown, max: number): string {
  if (typeof value !== "string" && typeof value !== "number") return "";
  return String(value).replace(/[\u0000-\u0008\u000b-\u001f]/g, "").trim().slice(0, max);
}

export function httpUrl(value: unknown): string {
  const s = str(value, 2000);
  if (!/^https?:\/\//i.test(s)) return "";
  try {
    return new URL(s).href;
  } catch {
    return "";
  }
}

export function base64UrlToBytes(payload: string): Uint8Array<ArrayBuffer> {
  const b64 = payload.replace(/-/g, "+").replace(/_/g, "/");
  const binary = atob(b64 + "=".repeat((4 - (b64.length % 4)) % 4));
  return Uint8Array.from(binary, (c) => c.charCodeAt(0));
}

function decodeBase64Url(payload: string): string {
  return new TextDecoder("utf-8", { fatal: true }).decode(base64UrlToBytes(payload));
}

/** Parses `#import=...`; returns null when absent or unreadable. */
export function decodeImportHash(hash: string): ImportedListing | null {
  const payload = /^#?import=([A-Za-z0-9_-]+)$/.exec(hash.trim())?.[1];
  if (!payload || payload.length > MAX_PAYLOAD) return null;
  let raw: unknown;
  try {
    raw = JSON.parse(decodeBase64Url(payload));
  } catch {
    return null;
  }
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return null;
  const d = raw as Record<string, unknown>;
  if (d.v !== 1) return null;

  const price = typeof d.price === "number" && d.price > 0 && d.price <= 100_000 ? String(d.price) : "";
  const images = Array.isArray(d.image_urls) ? d.image_urls.map(httpUrl).filter(Boolean).slice(0, 20) : [];
  const condition = typeof d.condition === "string" && CONDITIONS.has(d.condition) ? d.condition : "";

  const draft: ListingDraft = {
    ...EMPTY_DRAFT,
    url: httpUrl(d.url),
    title: str(d.title, 300),
    price,
    brand: str(d.brand, 120),
    size: str(d.size, 60),
    condition,
    color: str(d.color, 60),
    description: typeof d.description === "string" ? d.description.slice(0, 5000).trim() : "",
    images: images.join("\n"),
    sellerName: str(d.seller_username, 120),
  };
  const missing = (["url", "title", "price"] as const).filter((k) => !draft[k]);
  return { source: str(d.source, 30) || "import", draft, missing };
}
