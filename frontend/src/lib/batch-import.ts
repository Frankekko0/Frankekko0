/**
 * Batch imports from the "FlipFinder for Vinted" extension: every listing loaded on a Vinted
 * search page, sent as `/import#batch=<base64url(deflate-raw(JSON))>`.
 *
 * Like single imports, the payload comes from another site's page and is untrusted: it is
 * size-capped before and after decompression, and every item is type-checked, trimmed and
 * length-capped. Items that can't be analysed correctly are skipped and counted, never guessed.
 */
import { CONDITIONS, base64UrlToBytes, httpUrl, str } from "./listing-import";
import type { ManualListingInput } from "./types";

/** Same limit as the API and the extension. */
export const MAX_BATCH = 200;
const MAX_PAYLOAD = 400_000;
const MAX_JSON_BYTES = 2_000_000;

export interface DecodedBatch {
  source: string;
  /** The Vinted search the listings came from, when known. */
  query: string;
  items: ManualListingInput[];
  skipped: {
    /** Priced in another currency: FlipFinder's market data is in euro. */
    currency: number;
    /** Missing a link, title or valid price. */
    invalid: number;
  };
}

/** deflate-raw bytes -> text, refusing anything that inflates past `limit` bytes. */
async function inflate(bytes: Uint8Array<ArrayBuffer>, limit: number): Promise<string> {
  const reader = new Blob([bytes]).stream().pipeThrough(new DecompressionStream("deflate-raw")).getReader();
  const chunks: Uint8Array[] = [];
  let total = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    total += value.byteLength;
    if (total > limit) {
      await reader.cancel();
      throw new Error("payload too large");
    }
    chunks.push(value);
  }
  const all = new Uint8Array(total);
  let offset = 0;
  for (const c of chunks) {
    all.set(c, offset);
    offset += c.byteLength;
  }
  return new TextDecoder("utf-8", { fatal: true }).decode(all);
}

function sanitizeItem(raw: unknown): ManualListingInput | "currency" | null {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return null;
  const d = raw as Record<string, unknown>;
  const currency = str(d.currency, 3).toUpperCase();
  if (currency && currency !== "EUR") return "currency";
  const url = httpUrl(d.url);
  const title = str(d.title, 300);
  const price = typeof d.price === "number" && Number.isFinite(d.price) && d.price > 0 && d.price <= 100_000 ? d.price : 0;
  if (!url || title.length < 3 || !price) return null;
  const condition = typeof d.condition === "string" && CONDITIONS.has(d.condition) ? d.condition : "";
  const images = Array.isArray(d.image_urls) ? d.image_urls.map(httpUrl).filter(Boolean).slice(0, 3) : [];
  const item: ManualListingInput = { url, title, price };
  const brand = str(d.brand, 120);
  const size = str(d.size, 60);
  const color = str(d.color, 60);
  if (brand) item.brand = brand;
  if (size) item.size = size;
  if (condition) item.condition = condition;
  if (color) item.color = color;
  if (images.length) item.image_urls = images;
  return item;
}

/** Parses `#batch=...`; null when absent, unreadable or not a FlipFinder batch. */
export async function decodeBatchHash(hash: string): Promise<DecodedBatch | null> {
  const payload = /^#?batch=([A-Za-z0-9_-]+)$/.exec(hash.trim())?.[1];
  if (!payload || payload.length > MAX_PAYLOAD) return null;
  let raw: unknown;
  try {
    raw = JSON.parse(await inflate(base64UrlToBytes(payload), MAX_JSON_BYTES));
  } catch {
    return null;
  }
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return null;
  const d = raw as Record<string, unknown>;
  if (d.v !== 1 || !Array.isArray(d.items)) return null;

  const items: ManualListingInput[] = [];
  const seen = new Set<string>();
  const skipped = { currency: 0, invalid: 0 };
  for (const entry of d.items.slice(0, MAX_BATCH)) {
    const item = sanitizeItem(entry);
    if (item === "currency") skipped.currency += 1;
    else if (!item) skipped.invalid += 1;
    else if (!seen.has(item.url)) {
      seen.add(item.url);
      items.push(item);
    }
  }
  return { source: str(d.source, 30) || "import", query: str(d.query, 120), items, skipped };
}
