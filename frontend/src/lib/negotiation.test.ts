import { describe, expect, it } from "vitest";
import { fallbackText, MESSAGE_LABELS, messageLabel, retryText, violationText } from "./negotiation";

describe("negotiation wording", () => {
  it("labels the message kinds the backend writes, not the old tone names", () => {
    for (const kind of ["first_offer", "counter_reply", "accept", "decline_politely", "bundle"]) {
      expect(MESSAGE_LABELS[kind]).toBeTruthy();
      expect(messageLabel(kind)).not.toBe(kind);
    }
    expect(messageLabel("first_offer")).toBe("First offer");
    expect(messageLabel("something_new")).toBe("something new");
  });

  it("says why the templates stayed, with when to retry", () => {
    expect(fallbackText(undefined)).toBeNull();
    expect(fallbackText({ fallback: null, retry_after: null })).toBeNull();
    expect(fallbackText({ fallback: "disabled", retry_after: null })).toContain("switched off");
    expect(fallbackText({ fallback: "no_model", retry_after: null })).toContain("AI_API_KEY");
    expect(fallbackText({ fallback: "rate_limited", retry_after: 30 })).toBe("The AI model's request limit is reached. Try again in 30 s.");
    expect(fallbackText({ fallback: "daily_cap", retry_after: 7200 })).toContain("Try again in 2 h.");
    expect(fallbackText({ fallback: "from_the_future", retry_after: null })).toBe("The AI draft was not used.");
  });

  it("turns seconds into a short retry hint", () => {
    expect(retryText(null)).toBe("");
    expect(retryText(0)).toBe("");
    expect(retryText(12.2)).toBe("Try again in 13 s.");
    expect(retryText(300)).toBe("Try again in 5 min.");
    expect(retryText(20_000)).toBe("Try again in 6 h.");
  });

  it("explains the rules a draft broke", () => {
    expect(violationText(["literal_number", "off_platform_contact"])).toBe("wrote a number of its own; pointed outside Vinted");
    expect(violationText(["brand_new_rule"])).toBe("brand new rule");
    expect(violationText([])).toBe("");
  });
});
