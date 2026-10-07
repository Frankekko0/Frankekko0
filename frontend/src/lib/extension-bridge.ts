"use client";

import { useCallback, useEffect, useState } from "react";

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

type Action = "ff:vinted-favourite" | "ff:vinted-buy-check" | "ff:vinted-buy-open" | "ff:bridge-status";

let seq = 0;

function send<T>(type: Action, payload: Record<string, unknown>, timeoutMs: number): Promise<T | null> {
  return new Promise((resolve) => {
    const id = `ff-${Date.now()}-${++seq}`;
    const timer = window.setTimeout(() => {
      window.removeEventListener("message", onMessage);
      resolve(null);
    }, timeoutMs);
    function onMessage(e: MessageEvent) {
      if (e.source !== window || e.origin !== window.location.origin) return;
      const d = e.data as { ff?: string; id?: string; result?: T } | null;
      if (!d || d.ff !== "response" || d.id !== id) return;
      window.clearTimeout(timer);
      window.removeEventListener("message", onMessage);
      resolve(d.result ?? null);
    }
    window.addEventListener("message", onMessage);
    window.postMessage({ ff: "request", id, type, payload }, window.location.origin);
  });
}

/**
 * The FlipFinder extension on this page (its bridge is injected only on your FlipFinder address).
 * Actions run only right after your click: call `act` directly from a click handler.
 */
export function useExtensionBridge() {
  const [status, setStatus] = useState<BridgeStatus>("checking");

  useEffect(() => {
    let alive = true;
    const onReady = (e: MessageEvent) => {
      if (e.source === window && e.data?.ff === "bridge-ready" && alive) setStatus(e.data.paired ? "ready" : "unpaired");
    };
    window.addEventListener("message", onReady);
    send<{ ok: boolean; paired?: boolean }>("ff:bridge-status", {}, 1500).then((r) => {
      if (!alive) return;
      setStatus(!r || !r.ok ? "absent" : r.paired ? "ready" : "unpaired");
    });
    return () => {
      alive = false;
      window.removeEventListener("message", onReady);
    };
  }, []);

  const act = useCallback(
    async (type: Exclude<Action, "ff:bridge-status">, payload: { vid: string; url: string; want?: boolean }): Promise<VintedResult> => {
      const r = await send<VintedResult>(type, payload, 60_000);
      return r ?? { ok: false, code: "timeout" };
    },
    [],
  );

  return { status, act };
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
};

/** A user-facing explanation of a failed extension action (never a raw technical error). */
export function explain(r: VintedResult): string {
  return (r.code && CODE_MESSAGE[r.code]) || "The action on Vinted did not succeed: try again.";
}
