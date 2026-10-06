import { createRequire } from "node:module";
import path from "node:path";
import { describe, expect, it } from "vitest";
import { decodeBatchHash, MAX_BATCH } from "./batch-import";

// The real encoder of the browser extension: what it writes, this page must read.
const extension = createRequire(import.meta.url)(path.resolve(__dirname, "../../../extension/src/parse.js")) as {
  encodeBatch: (data: unknown) => Promise<string>;
  parseCatalog: (cards: unknown[], location: string) => { data: unknown };
};

async function deflate(text: string): Promise<string> {
  const zipped = await new Response(new Blob([text]).stream().pipeThrough(new CompressionStream("deflate-raw"))).arrayBuffer();
  let binary = "";
  new Uint8Array(zipped).forEach((b) => (binary += String.fromCharCode(b)));
  return "#batch=" + btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

const item = (i: number, extra: Record<string, unknown> = {}) => ({
  url: `https://www.vinted.it/items/${1000 + i}-polo`,
  title: `Polo Ralph Lauren “${i}”`,
  price: 10 + i,
  currency: "EUR",
  brand: "Ralph Lauren",
  size: "M",
  condition: "very_good",
  condition_label: "Ottime",
  color: "",
  image_urls: [`https://images1.vinted.net/t/${i}.jpeg`],
  ...extra,
});

describe("decodeBatchHash", () => {
  it("reads what the browser extension writes", async () => {
    const cards = Array.from({ length: 3 }, (_, i) => ({
      href: `/items/${500 + i}-felpa?referrer=catalog`,
      summary: `Felpa Ralph Lauren ${i}, brand: Ralph Lauren, condizioni: Ottime, taglia: L, ${15 + i},00 €, 16,45 €`,
      image: `https://images1.vinted.net/t/${i}.jpeg`,
    }));
    const { data } = extension.parseCatalog(cards, "https://www.vinted.it/catalog?search_text=felpa+ralph+lauren");
    const res = await decodeBatchHash("#batch=" + (await extension.encodeBatch(data)));
    expect(res?.source).toBe("vinted_search");
    expect(res?.query).toBe("felpa ralph lauren");
    expect(res?.items).toHaveLength(3);
    expect(res?.items[1]).toEqual({
      url: "https://www.vinted.it/items/501-felpa",
      title: "Felpa Ralph Lauren 1",
      price: 16,
      brand: "Ralph Lauren",
      size: "L",
      condition: "very_good",
      image_urls: ["https://images1.vinted.net/t/1.jpeg"],
    });
  });

  it("drops unsafe or unusable items and counts them, never guessing", async () => {
    const res = await decodeBatchHash(
      await deflate(
        JSON.stringify({
          v: 1,
          source: "vinted_search",
          items: [
            item(1),
            item(1), // same URL twice
            item(2, { url: "javascript:alert(1)" }),
            item(3, { price: -4 }),
            item(4, { price: "12" }),
            item(5, { title: "ab" }),
            item(6, { currency: "GBP" }),
            item(7, { condition: "mint!!", image_urls: ["javascript:x", "https://ok.example/a.jpg"] }),
            "not an object",
          ],
        }),
      ),
    );
    expect(res?.items.map((i) => i.url)).toEqual(["https://www.vinted.it/items/1001-polo", "https://www.vinted.it/items/1007-polo"]);
    expect(res?.items[1]?.condition).toBeUndefined();
    expect(res?.items[1]?.image_urls).toEqual(["https://ok.example/a.jpg"]);
    expect(res?.skipped).toEqual({ currency: 1, invalid: 5 });
  });

  it("caps the batch at the API limit", async () => {
    const res = await decodeBatchHash(
      await deflate(JSON.stringify({ v: 1, items: Array.from({ length: MAX_BATCH + 20 }, (_, i) => item(i)) })),
    );
    expect(res?.items).toHaveLength(MAX_BATCH);
  });

  it("rejects malformed, foreign and oversized payloads", async () => {
    expect(await decodeBatchHash("")).toBeNull();
    expect(await decodeBatchHash("#import=abc")).toBeNull();
    expect(await decodeBatchHash("#batch=!!!")).toBeNull();
    expect(await decodeBatchHash("#batch=bm90LWRlZmxhdGU")).toBeNull();
    expect(await decodeBatchHash(await deflate(JSON.stringify({ v: 2, items: [item(1)] })))).toBeNull();
    expect(await decodeBatchHash(await deflate("[1,2,3]"))).toBeNull();
    // A tiny payload that inflates past the limit (compression bomb) is refused.
    expect(await decodeBatchHash(await deflate(JSON.stringify({ v: 1, items: [], pad: "x".repeat(3_000_000) })))).toBeNull();
  });
});
