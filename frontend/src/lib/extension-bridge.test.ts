import { describe, expect, it } from "vitest";
import { buyAtFirstClick, confirmReady, isUnknownAction, type BridgeHost, type VintedResult } from "./extension-bridge";

type Reply = (type: string, payload: Record<string, unknown>) => unknown | undefined;

/**
 * A page with a fake extension bridge: every request posted by the web app is answered by
 * ``reply`` (undefined = the extension does not answer, e.g. not installed or asleep).
 */
function fakeHost(reply: Reply): BridgeHost & { sent: { type: string; payload: Record<string, unknown> }[] } {
  const listeners = new Set<(e: MessageEvent) => void>();
  const host = {
    sent: [] as { type: string; payload: Record<string, unknown> }[],
    location: { origin: "https://flip.example" },
    addEventListener: (_t: "message", l: (e: MessageEvent) => void) => listeners.add(l),
    removeEventListener: (_t: "message", l: (e: MessageEvent) => void) => listeners.delete(l),
    setTimeout: (h: () => void, ms: number) => setTimeout(h, Math.min(ms, 20)) as unknown as number,
    clearTimeout: (id: number) => clearTimeout(id),
    postMessage(message: unknown) {
      const m = message as { ff: string; id: string; type: string; payload: Record<string, unknown> };
      if (m.ff !== "request") return;
      host.sent.push({ type: m.type, payload: m.payload });
      const result = reply(m.type, m.payload);
      if (result === undefined) return;
      queueMicrotask(() => {
        for (const l of [...listeners]) l({ source: host, origin: host.location.origin, data: { ff: "response", id: m.id, result } } as unknown as MessageEvent);
      });
    },
  };
  return host;
}

const item = { vid: "4242", url: "https://www.vinted.it/items/4242-felpa", price: 20 };
const fast = { buy: 50, action: 50 };

describe("bridge readiness at click time", () => {
  it("acts at once when the bridge is already ready", async () => {
    const host = fakeHost(() => undefined);
    expect(await confirmReady(host, "ready")).toBe("ready");
    expect(host.sent).toHaveLength(0);
  });

  it("asks again when the extension answered late (service worker asleep at page load)", async () => {
    let calls = 0;
    const host = fakeHost((type) => (type === "ff:bridge-status" && ++calls >= 2 ? { ok: true, paired: true } : undefined));
    expect(await confirmReady(host, "absent", [10, 10, 10])).toBe("ready");
    expect(calls).toBe(2);
  });

  it("reports unpaired as soon as the extension answers, absent only after every ping", async () => {
    expect(await confirmReady(fakeHost(() => ({ ok: true, paired: false })), "checking", [10])).toBe("unpaired");
    const silent = fakeHost(() => undefined);
    expect(await confirmReady(silent, "checking", [10, 10])).toBe("absent");
    expect(silent.sent).toHaveLength(2);
  });
});

describe("Buy at the first click", () => {
  it("one request opens the checkout and carries the analysed price", async () => {
    const host = fakeHost((type) => (type === "ff:vinted-buy" ? { ok: true, price: 20, status: "active" } : undefined));
    const out = await buyAtFirstClick(host, item, fast);
    expect(out.kind).toBe("opened");
    expect(host.sent).toEqual([{ type: "ff:vinted-buy", payload: { vid: "4242", url: item.url, expect_price: 20 } }]);
  });

  it("a changed price comes back without any click, with the new price", async () => {
    const host = fakeHost(() => ({ ok: false, code: "price_changed", price: 17.5 }));
    const out = await buyAtFirstClick(host, item, fast);
    expect(out).toMatchObject({ kind: "price_changed", price: 17.5 });
    expect(host.sent.map((s) => s.type)).toEqual(["ff:vinted-buy"]);
  });

  it("sold, reserved or removed items are unavailable (by code or by status)", async () => {
    const byCode = await buyAtFirstClick(fakeHost(() => ({ ok: false, code: "sold" })), item, fast);
    expect(byCode).toMatchObject({ kind: "unavailable", code: "sold" });
    const byStatus = await buyAtFirstClick(fakeHost(() => ({ ok: false, code: "not_buyable", status: "reserved" })), item, fast);
    expect(byStatus).toMatchObject({ kind: "unavailable", code: "reserved" });
  });

  it("an older extension that refuses the new action falls back to the check (two-step flow)", async () => {
    const host = fakeHost((type) =>
      type === "ff:vinted-buy"
        ? { ok: false, code: "forbidden", message: "Azione non consentita." }
        : type === "ff:vinted-buy-check"
          ? { ok: true, status: "active", price: 20, signedIn: true }
          : undefined,
    );
    const out = await buyAtFirstClick(host, item, fast);
    expect(out.kind).toBe("checked");
    expect(host.sent.map((s) => s.type)).toEqual(["ff:vinted-buy", "ff:vinted-buy-check"]);
  });

  it("signed out on the fallback check and silence from the extension are errors", async () => {
    const signedOut = fakeHost((type) =>
      type === "ff:vinted-buy" ? { ok: false, code: "forbidden", message: "Azione non consentita." } : { ok: true, status: "active", signedIn: false },
    );
    expect(await buyAtFirstClick(signedOut, item, fast)).toMatchObject({ kind: "error", result: { code: "signed_out" } });
    expect(await buyAtFirstClick(fakeHost(() => undefined), item, fast)).toMatchObject({ kind: "error", result: { code: "timeout" } });
  });

  it("a refusal for another reason is not mistaken for an older extension", () => {
    const r: VintedResult = { ok: false, code: "forbidden", message: "Richiesta non autorizzata." };
    expect(isUnknownAction(r)).toBe(false);
    expect(isUnknownAction({ ok: false, code: "unknown_action" })).toBe(true);
    expect(isUnknownAction({ ok: true })).toBe(false);
  });
});
