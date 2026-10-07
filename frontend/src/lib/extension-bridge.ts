"use client";

import { useCallback, useEffect, useRef, useState } from "react";

/** Result of an action done by the browser extension on Vinted. */
export interface VintedResult {
  ok: boolean;
  code?: string;
  message?: string;
  status?: string;
  price?: number | null;
  currency?: string | null;
  title?: string | null;
  signedIn?: boolean;
  favourite?: boolean | null;
  changed?: boolean;
  canBuy?: boolean;
}

export type BridgeStatus = "checking" | "absent" | "unpaired" | "ready";

export type Action = "ff:vinted-favourite" | "ff:vinted-buy" | "ff:vinted-buy-check" | "ff:vinted-buy-open" | "ff:bridge-status";

/** The page side of the bridge: `window` in the browser, a fake in tests. */
export interface BridgeHost {
  postMessage(message: unknown, targetOrigin: string): void;
  addEventListener(type: "message", listener: (e: MessageEvent) => void): void;
  removeEventListener(type: "message", listener: (e: MessageEvent) => void): void;
  setTimeout(handler: () => void, ms: number): number;
  clearTimeout(id: number): void;
  location: { origin: string };
}

/** Timeouts (ms). The service worker of the extension may be asleep: the first answer can be slow. */
export const TIMEOUTS = {
  /** Status pings at page load, one after the other (a sleeping extension wakes up meanwhile). */
  initialPings: [2000, 3000],
  /** Re-pings at click time when the status is not "ready" yet: short, so the click still counts. */
  clickPings: [500, 500, 500],
  action: 60_000,
  /** Open the item, check it, press Buy and wait for the checkout. */
  buy: 90_000,
};

let seq = 0;

/** One request to the extension; null when nobody answers within ``timeoutMs``. */
export function request<T>(host: BridgeHost, type: Action, payload: Record<string, unknown>, timeoutMs: number): Promise<T | null> {
  return new Promise((resolve) => {
    const id = `ff-${Date.now()}-${++seq}`;
    const timer = host.setTimeout(() => {
      host.removeEventListener("message", onMessage);
      resolve(null);
    }, timeoutMs);
    function onMessage(e: MessageEvent) {
      if (e.source !== (host as unknown) || e.origin !== host.location.origin) return;
      const d = e.data as { ff?: string; id?: string; result?: T } | null;
      if (!d || d.ff !== "response" || d.id !== id) return;
      host.clearTimeout(timer);
      host.removeEventListener("message", onMessage);
      resolve(d.result ?? null);
    }
    host.addEventListener("message", onMessage);
    host.postMessage({ ff: "request", id, type, payload }, host.location.origin);
  });
}

/** Asks the bridge whether it is there and paired. */
export async function pingBridge(host: BridgeHost, timeoutMs: number): Promise<Exclude<BridgeStatus, "checking">> {
  const r = await request<{ ok: boolean; paired?: boolean }>(host, "ff:bridge-status", {}, timeoutMs);
  return !r || !r.ok ? "absent" : r.paired ? "ready" : "unpaired";
}

/**
 * The status to act on: "ready" as is; otherwise the bridge is asked again a few times (it may
 * have loaded after the page, or its service worker may just have woken up) before giving up.
 */
export async function confirmReady(host: BridgeHost, current: BridgeStatus, pings: readonly number[] = TIMEOUTS.clickPings): Promise<Exclude<BridgeStatus, "checking">> {
  if (current === "ready") return "ready";
  for (const ms of pings) {
    const s = await pingBridge(host, ms);
    if (s !== "absent") return s; // the extension answered (paired or not): no need to ask again
  }
  return "absent";
}

/** An older extension that does not know the action (it answers before doing anything). */
export function isUnknownAction(r: VintedResult): boolean {
  if (r.ok) return false;
  if (r.code === "unknown_action" || r.code === "unsupported" || r.code === "unsupported_action") return true;
  return r.code === "forbidden" && /azione non consentita|unknown action|not allowed/i.test(r.message ?? "");
}

const UNAVAILABLE = new Set(["sold", "reserved", "removed"]);

export type BuyOutcome =
  /** Checkout open on Vinted: the user confirms the payment there. */
  | { kind: "opened"; result: VintedResult }
  /** The price is not the analysed one: nothing clicked; offer the checkout at the new price. */
  | { kind: "price_changed"; price: number | null; result: VintedResult }
  | { kind: "unavailable"; code: "sold" | "reserved" | "removed"; result: VintedResult }
  /** Older extension: the item was checked (the old two-step flow continues with a dialog). */
  | { kind: "checked"; result: VintedResult }
  | { kind: "error"; result: VintedResult };

/**
 * Buy at the first click: one request opens the item, checks it is still on sale at the analysed
 * price and presses Buy (``ff:vinted-buy``). An older extension that does not know the action
 * falls back to the check-then-open flow (``ff:vinted-buy-check`` now, ``ff:vinted-buy-open`` on
 * the next click).
 */
export async function buyAtFirstClick(
  host: BridgeHost,
  item: { vid: string; url: string; price: number },
  timeouts: { buy: number; action: number } = TIMEOUTS,
): Promise<BuyOutcome> {
  const r = (await request<VintedResult>(host, "ff:vinted-buy", { vid: item.vid, url: item.url, expect_price: item.price }, timeouts.buy)) ?? {
    ok: false,
    code: "timeout",
  };
  if (r.ok) return { kind: "opened", result: r };
  if (isUnknownAction(r)) {
    const c = (await request<VintedResult>(host, "ff:vinted-buy-check", { vid: item.vid, url: item.url }, timeouts.action)) ?? { ok: false, code: "timeout" };
    if (!c.ok) return { kind: "error", result: c };
    if (c.signedIn === false) return { kind: "error", result: { ok: false, code: "signed_out" } };
    return { kind: "checked", result: c };
  }
  if (r.code === "price_changed") return { kind: "price_changed", price: typeof r.price === "number" ? r.price : null, result: r };
  const gone = r.code && UNAVAILABLE.has(r.code) ? r.code : r.status && UNAVAILABLE.has(r.status) ? r.status : null;
  if (gone) return { kind: "unavailable", code: gone as "sold" | "reserved" | "removed", result: { ...r, code: gone } };
  return { kind: "error", result: r };
}

function browserHost(): BridgeHost {
  return window as unknown as BridgeHost;
}

/**
 * The FlipFinder extension on this page (its bridge is injected only on your FlipFinder address).
 * Actions run only right after your click: call `act` / `buy` directly from a click handler.
 */
export function useExtensionBridge() {
  const [status, setStatus] = useState<BridgeStatus>("checking");
  const statusRef = useRef<BridgeStatus>("checking");
  const update = useCallback((s: BridgeStatus) => {
    statusRef.current = s;
    setStatus(s);
  }, []);

  useEffect(() => {
    let alive = true;
    // A late "bridge-ready" (extension woken up after the page loaded) always wins.
    const onReady = (e: MessageEvent) => {
      if (e.source === window && e.data?.ff === "bridge-ready" && alive) update(e.data.paired ? "ready" : "unpaired");
    };
    window.addEventListener("message", onReady);
    (async () => {
      for (const ms of TIMEOUTS.initialPings) {
        const s = await pingBridge(browserHost(), ms);
        if (!alive || statusRef.current === "ready") return;
        if (s !== "absent") return update(s);
      }
      if (alive && statusRef.current === "checking") update("absent");
    })();
    return () => {
      alive = false;
      window.removeEventListener("message", onReady);
    };
  }, [update]);

  /** The status at click time, after re-asking the bridge when it was not ready. */
  const ensureReady = useCallback(async () => {
    const s = await confirmReady(browserHost(), statusRef.current);
    if (s !== statusRef.current) update(s);
    return s;
  }, [update]);

  const act = useCallback(
    async (type: Exclude<Action, "ff:bridge-status" | "ff:vinted-buy">, payload: { vid: string; url: string; want?: boolean }): Promise<VintedResult> => {
      const r = await request<VintedResult>(browserHost(), type, payload, TIMEOUTS.action);
      return r ?? { ok: false, code: "timeout" };
    },
    [],
  );

  const buy = useCallback((item: { vid: string; url: string; price: number }) => buyAtFirstClick(browserHost(), item), []);

  return { status, ensureReady, act, buy };
}

export const BRIDGE_HELP: Record<Exclude<BridgeStatus, "ready" | "checking">, string> = {
  absent:
    "The FlipFinder extension is not connected to this page: install it and, in its options, set this address and the key from Settings → Browser extension.",
  unpaired: "The extension is installed but not paired: paste the key created in Settings → Browser extension into its options.",
};

const CODE_MESSAGE: Record<string, string> = {
  signed_out: "You are not signed in to Vinted in this browser: sign in on Vinted and try again.",
  no_button: "Vinted's button was not found on the listing page: the extension's page settings need an update.",
  not_confirmed: "Vinted did not confirm the change: check the listing on Vinted.",
  unknown_state: "The extension can't read whether the listing is already in your Vinted favourites, so it did not click (its page settings need an update).",
  checkout_not_seen: "Buy was pressed on Vinted but the checkout did not open: check the Vinted tab.",
  wrong_page: "The listing page did not open: try again.",
  no_page: "The Vinted page is not responding: try again.",
  bad_url: "This is not a valid Vinted listing.",
  forbidden: "Request refused by the extension.",
  no_click: "Actions start only from your click on the button.",
  unpaired: BRIDGE_HELP.unpaired,
  extension: "The extension could not be reached: reload this page.",
  timeout: "The extension did not answer: check the Vinted tab and try again.",
  sold: "The item has been sold: it can no longer be bought.",
  reserved: "The item is reserved for another buyer: it cannot be bought now.",
  removed: "The seller removed the listing.",
  price_changed: "The price changed on Vinted: nothing was clicked.",
};

/** A user-facing explanation of a failed extension action (never a raw technical error). */
export function explain(r: VintedResult): string {
  return (r.code && CODE_MESSAGE[r.code]) || "The action on Vinted did not succeed: try again.";
}
