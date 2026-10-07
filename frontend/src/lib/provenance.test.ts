import { describe, expect, it } from "vitest";
import {
  accuracyDelta,
  conditionLabel,
  dayDate,
  describeDelta,
  lastPriceNote,
  money,
  monthlyCostUsd,
  readProvenance,
  realSalesHeadline,
  rejectedList,
  safeUrl,
  salesMix,
  shortBasis,
  sortedReferences,
  sourceDomain,
  usedShare,
} from "./provenance";
import type { ExternalReference, Provenance } from "./types";

function provenance(over: Partial<Provenance> = {}, expected: Partial<Provenance["expected_price"]> = {}): Provenance {
  return {
    expected_price: {
      value: 45,
      basis: "sold",
      n: 9,
      by_source: { own_sale: 1, own_purchase: 0, vinted_sold: 6, external_sold: 2, vinted_asking: 0, external_asking: 0 },
      negotiation_discount: 0.07,
      calibrated: true,
      prior: { used: false, level: null, n: 0 },
      label: "Mediana pesata di 9 vendite concluse (1 tua, 6 Vinted, 2 da altri mercati)",
      ...expected,
    },
    price_range: { low: 38, high: 52, basis: "calibration", label: "…" },
    days_to_sell: { value: 9, n: 6, basis: "sold", label: "…" },
    sale_probability: { value: 0.62, n: 14, basis: "similar", label: "…" },
    new_price: null,
    real_sales: { total: 9, own: 1, vinted_sold: 6, external_sold: 2 },
    external: [],
    ...over,
  };
}

function ref(over: Partial<ExternalReference>): ExternalReference {
  return {
    kind: "asking",
    source: "ebay.it",
    price: 40,
    currency: "EUR",
    price_eur: 40,
    date: "2026-09-01",
    condition: "unknown",
    url: "https://www.ebay.it/itm/1",
    title: "Nike Air Max 90",
    used_in_estimate: false,
    ...over,
  };
}

describe("readProvenance", () => {
  it("hides old analyses without provenance", () => {
    expect(readProvenance(null)).toBeNull();
    expect(readProvenance(undefined)).toBeNull();
    expect(readProvenance({})).toBeNull();
    expect(readProvenance({ expected_price: { value: 1 } })).toBeNull();
    expect(readProvenance("x")).toBeNull();
  });
  it("tolerates missing optional parts", () => {
    const p = readProvenance({ expected_price: provenance().expected_price, real_sales: { total: 0, own: 0, vinted_sold: 0, external_sold: 0 } });
    expect(p).not.toBeNull();
    expect(p!.external).toEqual([]);
    expect(p!.new_price).toBeNull();
  });
});

describe("real sales headline", () => {
  it("counts the real sales per origin", () => {
    expect(realSalesHeadline(provenance())).toBe("Based on 9 real sales: 1 yours, 6 Vinted, 2 other markets");
    expect(
      realSalesHeadline(provenance({ real_sales: { total: 1, own: 0, vinted_sold: 0, external_sold: 1 } })),
    ).toBe("Based on 1 real sale: 1 other market");
  });
  it("says when the estimate rests on asking prices only", () => {
    const p = provenance(
      { real_sales: { total: 0, own: 0, vinted_sold: 0, external_sold: 0 } },
      { basis: "asking", by_source: { vinted_asking: 7, external_asking: 1 } },
    );
    expect(realSalesHeadline(p)).toBe("No real sales: estimate from asking prices");
    expect(shortBasis(p)).toBe("asking prices only");
    expect(salesMix(p).asking).toBe(8);
  });
  it("covers segment statistics and missing estimates", () => {
    const none = { real_sales: { total: 0, own: 0, vinted_sold: 0, external_sold: 0 } };
    expect(realSalesHeadline(provenance(none, { basis: "prior" }))).toBe("No real sales: estimate from segment statistics");
    expect(realSalesHeadline(provenance(none, { basis: "none", value: null }))).toBe("No real sales and too few comparables for an estimate");
    expect(shortBasis(provenance(none, { basis: "none" }))).toBeNull();
  });
  it("gives a short basis for mixed estimates", () => {
    const p = provenance(
      { real_sales: { total: 3, own: 0, vinted_sold: 3, external_sold: 0 } },
      { basis: "mixed", by_source: { vinted_sold: 3, vinted_asking: 4 } },
    );
    expect(shortBasis(p)).toBe("3 real sales + 4 asking prices");
  });
});

describe("last price seen", () => {
  it("explains Vinted sold prices with the negotiation discount", () => {
    const note = lastPriceNote(provenance())!;
    expect(note.text).toMatch(/last price seen/);
    expect(note.discount).toBe(0.07);
    expect(note.discountText).toBe("Negotiation discount of 7% applied, measured on your own purchases.");
  });
  it("says when the discount is not measured", () => {
    const note = lastPriceNote(provenance({}, { negotiation_discount: null }))!;
    expect(note.discount).toBeNull();
    expect(note.discountText).toMatch(/not measured yet/);
  });
  it("is absent without Vinted sales", () => {
    const p = provenance({ real_sales: { total: 1, own: 1, vinted_sold: 0, external_sold: 0 } }, { by_source: { own_sale: 1 } });
    expect(lastPriceNote(p)).toBeNull();
  });
});

describe("external references", () => {
  it("renders only http(s) links", () => {
    expect(safeUrl("https://www.ebay.it/itm/1")).toBe("https://www.ebay.it/itm/1");
    expect(safeUrl("javascript:alert(1)")).toBeNull();
    expect(safeUrl("not a url")).toBeNull();
    expect(safeUrl(null)).toBeNull();
  });
  it("names the source domain", () => {
    expect(sourceDomain("ebay.it")).toBe("ebay.it");
    expect(sourceDomain("", "https://www.zalando.it/x")).toBe("zalando.it");
    expect(sourceDomain(null, "javascript:x")).toBe("unknown source");
  });
  it("formats prices in their own currency", () => {
    expect(money(44, "EUR")).toBe("€44");
    expect(money(40, "GBP")).toBe("£40");
    expect(money(45.5, "usd")).toBe("US$45.50");
    expect(money(null, "EUR")).toBe("—");
  });
  it("shows date-only values as calendar days", () => {
    expect(dayDate("2026-09-20")).toBe("20 Sep 2026");
    expect(dayDate("")).toBe("—");
    expect(dayDate(null)).toBe("—");
  });
  it("labels conditions", () => {
    expect(conditionLabel("very_good")).toBe("Very good");
    expect(conditionLabel("unknown")).toBe("condition not stated");
    expect(conditionLabel("used")).toBe("Used");
  });
  it("lists the references used in the estimate first", () => {
    const refs = [
      ref({ kind: "new", source: "zalando.it" }),
      ref({ kind: "asking", used_in_estimate: true, date: "2026-09-02" }),
      ref({ kind: "sold", used_in_estimate: true, date: "2026-08-01" }),
      ref({ kind: "sold", date: "2026-09-10" }),
    ];
    expect(sortedReferences(refs).map((r) => `${r.kind}:${r.used_in_estimate}`)).toEqual(["sold:true", "asking:true", "sold:false", "new:false"]);
  });
});

describe("price data", () => {
  it("compares the error with and without external data", () => {
    const base = { subjects: 40, estimated: 38, mape: 0.2, median_ape: 0.15, bias: 0, in_range: 0.8 };
    expect(accuracyDelta({ ...base, mae_eur: 6.2 }, { ...base, mae_eur: 5.4 })).toBe(-0.8);
    expect(describeDelta(-0.8)).toBe("Prices from other markets lower the mean error by €0.80.");
    expect(describeDelta(1.2)).toBe("Prices from other markets raise the mean error by €1.20.");
    expect(describeDelta(0)).toBe("Prices from other markets leave the mean error unchanged.");
    expect(accuracyDelta(null, { ...base, mae_eur: 5 })).toBeNull();
    expect(describeDelta(null)).toBeNull();
  });
  it("computes budget share and monthly cost", () => {
    expect(usedShare(12, 900)).toBeCloseTo(0.0133, 3);
    expect(usedShare(950, 900)).toBe(1);
    expect(usedShare(5, 0)).toBe(0);
    expect(usedShare(null, 900)).toBe(0);
    expect(monthlyCostUsd(120, 0.001)).toBe(0.12);
    expect(monthlyCostUsd(null, 0.001)).toBeNull();
  });
  it("lists rejection reasons with a count, largest first", () => {
    expect(rejectedList({ kids: 3, replica: 0, other_model: 7, weird_reason: 1 })).toEqual([
      { reason: "other_model", label: "other models", n: 7 },
      { reason: "kids", label: "kids' sizes", n: 3 },
      { reason: "weird_reason", label: "weird reason", n: 1 },
    ]);
    expect(rejectedList(null)).toEqual([]);
  });
});
