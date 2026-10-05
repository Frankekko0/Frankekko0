import { describe, expect, it } from "vitest";
import { activeFilterCount, filtersFromParams, paramsFromFilters } from "./filters";

describe("filters <-> url", () => {
  it("round-trips", () => {
    const f = { brands: ["nike", "ralph-lauren"], min_roi: 0.5, vintage_only: true, sort: "roi", q: "polo" };
    const back = filtersFromParams(paramsFromFilters(f));
    expect(back).toEqual(f);
  });
  it("counts only real filters", () => {
    expect(activeFilterCount({ sort: "roi", page: 2, brands: ["nike"], min_flip: 70 })).toBe(2);
  });
});
