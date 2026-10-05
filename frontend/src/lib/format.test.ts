import { describe, expect, it } from "vitest";
import { buildQuery } from "./api";
import { eur, pct, timeAgo } from "./format";

describe("format", () => {
  it("formats euros the reseller way", () => {
    expect(eur(18)).toBe("€18");
    expect(eur(17.5)).toBe("€17.50");
    expect(eur(17, { sign: true })).toBe("+€17");
    expect(eur(-3.2)).toBe("−€3.20");
    expect(eur(null)).toBe("—");
  });
  it("formats ratios as percentages", () => {
    expect(pct(0.68)).toBe("68%");
    expect(pct(-0.595, { digits: 1 })).toBe("−59.5%");
    expect(pct(0.1, { sign: true })).toBe("+10%");
  });
  it("formats relative time", () => {
    const now = Date.parse("2026-10-01T12:00:00Z");
    expect(timeAgo("2026-10-01T11:58:00Z", now)).toBe("2m ago");
    expect(timeAgo("2026-10-01T09:00:00Z", now)).toBe("3h ago");
    expect(timeAgo("2026-09-28T12:00:00Z", now)).toBe("3d ago");
  });
});

describe("buildQuery", () => {
  it("skips empty values and repeats arrays", () => {
    expect(buildQuery({ a: 1, b: undefined, c: "", d: false, e: ["x", "y"], f: true })).toBe("?a=1&e=x&e=y&f=true");
    expect(buildQuery({})).toBe("");
  });
});
