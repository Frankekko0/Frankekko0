import { describe, expect, it } from "vitest";
import { decodeImportHash } from "./listing-import";

function encode(data: unknown): string {
  const bytes = new TextEncoder().encode(JSON.stringify(data));
  let binary = "";
  bytes.forEach((b) => (binary += String.fromCharCode(b)));
  return "#import=" + btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

const VALID = {
  v: 1,
  source: "vinted",
  url: "https://www.vinted.it/items/4242-felpa",
  title: "Felpa Ralph Lauren “blu” — perfetta",
  price: 18,
  brand: "Ralph Lauren",
  size: "M",
  condition: "very_good",
  color: "Blu",
  description: "Ottime condizioni",
  image_urls: ["https://images1.vinted.net/a.jpg", "javascript:alert(1)", "https://images1.vinted.net/b.jpg"],
  seller_username: "armadio_8832",
};

describe("decodeImportHash", () => {
  it("decodes an extension payload into a draft (UTF-8 safe)", () => {
    const res = decodeImportHash(encode(VALID));
    expect(res?.source).toBe("vinted");
    expect(res?.missing).toEqual([]);
    expect(res?.draft).toMatchObject({
      url: VALID.url,
      title: VALID.title,
      price: "18",
      brand: "Ralph Lauren",
      size: "M",
      condition: "very_good",
      images: "https://images1.vinted.net/a.jpg\nhttps://images1.vinted.net/b.jpg",
    });
  });

  it("drops unsafe or invalid values and reports what is missing", () => {
    const res = decodeImportHash(
      encode({ ...VALID, url: "javascript:alert(1)", price: "18", condition: "mint", title: 42 }),
    );
    expect(res?.draft.url).toBe("");
    expect(res?.draft.price).toBe("");
    expect(res?.draft.condition).toBe("");
    expect(res?.draft.title).toBe("42");
    expect(res?.missing).toEqual(["url", "price"]);
  });

  it("caps long fields", () => {
    const res = decodeImportHash(encode({ ...VALID, title: "x".repeat(1000), description: "y".repeat(9000) }));
    expect(res?.draft.title).toHaveLength(300);
    expect(res?.draft.description).toHaveLength(5000);
  });

  it("ignores anything that is not a v1 import", () => {
    expect(decodeImportHash("")).toBeNull();
    expect(decodeImportHash("#section-market")).toBeNull();
    expect(decodeImportHash("#import=%%%")).toBeNull();
    expect(decodeImportHash("#import=bm90LWpzb24")).toBeNull(); // "not-json"
    expect(decodeImportHash(encode({ ...VALID, v: 2 }))).toBeNull();
    expect(decodeImportHash(encode([1, 2, 3]))).toBeNull();
  });
});
