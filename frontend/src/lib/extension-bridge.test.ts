import { describe, expect, it } from "vitest";
import { buyAtFirstClick, canBuyInOneClick, confirmReady, noteBridge, request, type BridgeHost } from "./extension-bridge";

type Reply = (type: string, payload: Record<string, unknown>) => unknown | undefined;

/**
 * A page with a fake extension bridge: every request posted by the web app is answered by
 * ``reply`` (undefined = the extension does not answer, e.g. not installed or asleep).
 */
function fakeHost(reply: Reply, bridge = 2): BridgeHost & { sent: { type: string; payload: Record<string, unknown> }[] } {
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
        const data = bridge ? { ff: "response", id: m.id, result, bridge } : { ff: "response", id: m.id, result };
        for (const l of [...listeners]) l({ source: host, origin: host.location.origin, data } as unknown as MessageEvent);
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

/** A page whose bridge said it buys in one click. */
function newBridge(reply: Reply) {
  const host = fakeHost(reply);
  noteBridge(host, { bridge: 2, features: ["buy"] });
  return host;
}

describe("Buy at the first click", () => {
  it("one request opens the checkout and carries the analysed price", async () => {
    const host = newBridge((type) => (type === "ff:vinted-buy" ? { ok: true, price: 20, status: "active" } : undefined));
    const out = await buyAtFirstClick(host, item, fast);
    expect(out.kind).toBe("opened");
    expect(host.sent).toEqual([{ type: "ff:vinted-buy", payload: { vid: "4242", url: item.url, expect_price: 20 } }]);
  });

  it("a changed price comes back without any click, with the new price", async () => {
    const host = newBridge(() => ({ ok: false, code: "price_changed", price: 17.5 }));
    const out = await buyAtFirstClick(host, item, fast);
    expect(out).toMatchObject({ kind: "price_changed", price: 17.5 });
    expect(host.sent.map((s) => s.type)).toEqual(["ff:vinted-buy"]);
  });

  it("sold, reserved or removed items are unavailable (by code or by status)", async () => {
    const byCode = await buyAtFirstClick(newBridge(() => ({ ok: false, code: "sold" })), item, fast);
    expect(byCode).toMatchObject({ kind: "unavailable", code: "sold" });
    const byStatus = await buyAtFirstClick(newBridge(() => ({ ok: false, code: "not_buyable", status: "reserved" })), item, fast);
    expect(byStatus).toMatchObject({ kind: "unavailable", code: "reserved" });
  });

  it("an older extension gets the check (two-step flow) and never the one-click request", async () => {
    const host = fakeHost((type) => (type === "ff:vinted-buy-check" ? { ok: true, status: "active", price: 20, signedIn: true } : undefined), 0);
    expect(canBuyInOneClick(host)).toBe(false);
    const out = await buyAtFirstClick(host, item, fast);
    expect(out.kind).toBe("checked");
    expect(host.sent.map((s) => s.type)).toEqual(["ff:vinted-buy-check"]);
  });

  it("a refusal of the one-click request is an error, never a second purchase flow", async () => {
    const host = newBridge(() => ({ ok: false, code: "forbidden", message: "Azione non consentita." }));
    expect(await buyAtFirstClick(host, item, fast)).toMatchObject({ kind: "error" });
    expect(host.sent.map((s) => s.type)).toEqual(["ff:vinted-buy"]);
  });

  it("signed out on the check and silence from the extension are errors", async () => {
    const signedOut = fakeHost(() => ({ ok: true, status: "active", signedIn: false }), 0);
    expect(await buyAtFirstClick(signedOut, item, fast)).toMatchObject({ kind: "error", result: { code: "signed_out" } });
    expect(await buyAtFirstClick(newBridge(() => undefined), item, fast)).toMatchObject({ kind: "error", result: { code: "timeout" } });
  });
});

describe("old bridge left on the page after an update", () => {
  it("the stamped answer wins over an older bridge's earlier answer", async () => {
    const host = fakeHost(() => undefined);
    const listeners: ((e: MessageEvent) => void)[] = [];
    const orig = host.addEventListener;
    host.addEventListener = (t, l) => {
      listeners.push(l);
      orig(t, l);
    };
    host.postMessage = (message: unknown) => {
      const m = message as { id: string };
      const send = (data: unknown) => queueMicrotask(() => listeners.forEach((l) => l({ source: host, origin: host.location.origin, data } as unknown as MessageEvent)));
      send({ ff: "response", id: m.id, result: { ok: false, code: "forbidden" } }); // old bridge, first
      setTimeout(() => send({ ff: "response", id: m.id, result: { ok: true }, bridge: 2 }), 5); // new bridge
    };
    expect(await request(host, "ff:vinted-favourite", {}, 1000)).toEqual({ ok: true });
  });

  it("once a newer bridge is known, unstamped answers are ignored", async () => {
    const host = fakeHost(() => ({ ok: false, code: "forbidden" }), 0);
    noteBridge(host, { bridge: 2, features: ["buy"] });
    expect(await request(host, "ff:vinted-favourite", {}, 30)).toBeNull();
  });
});
