import { describe, expect, it } from "vitest";
import { bookmarkletCode } from "@/components/analyze/acquire-cards";

describe("bookmarklet", () => {
  it("is valid JavaScript that opens FlipFinder's analyze page", () => {
    const code = bookmarkletCode("https://flip.example.com/");
    expect(code.startsWith("javascript:")).toBe(true);
    expect(() => new Function(code.slice("javascript:".length))).not.toThrow();
    expect(code).toContain('"https://flip.example.com"+\'/analyze#import=\'');
    expect(code).toContain("source:'bookmarklet'");
  });

  it("reads the listing's structured data and encodes it like the extension", () => {
    const opened: string[] = [];
    const ld = {
      "@type": "Product",
      name: "Felpa Ralph Lauren blu",
      brand: { name: "Ralph Lauren" },
      image: ["https://images1.vinted.net/a.jpg"],
      offers: { price: "18.00" },
    };
    const doc = {
      title: "Felpa | Vinted",
      querySelectorAll: () => [{ textContent: JSON.stringify(ld) }],
      querySelector: () => null,
    };
    const run = new Function("document", "location", "window", "btoa", "unescape", "encodeURIComponent", bookmarkletCode("http://localhost:3000").slice(11));
    run(doc, { origin: "https://www.vinted.it", pathname: "/items/42-felpa" }, { open: (u: string) => opened.push(u) }, btoa, unescape, encodeURIComponent);
    expect(opened).toHaveLength(1);
    const payload = opened[0]!.split("#import=")[1]!;
    const json = JSON.parse(new TextDecoder().decode(Uint8Array.from(atob(payload.replace(/-/g, "+").replace(/_/g, "/")), (c) => c.charCodeAt(0))));
    expect(json).toMatchObject({ v: 1, source: "bookmarklet", url: "https://www.vinted.it/items/42-felpa", title: "Felpa Ralph Lauren blu", price: 18, brand: "Ralph Lauren" });
  });
});
