"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, Heart, ShoppingCart } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { api } from "@/lib/api";
import { qk } from "@/lib/queries";
import { BRIDGE_HELP, explain, useExtensionBridge, type VintedResult } from "@/lib/extension-bridge";
import { eur, timeAgo } from "@/lib/format";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent } from "@/components/ui/misc";

interface ActionState {
  value: boolean | null;
  price: number | null;
  source: string;
  at: string;
}

interface VintedState {
  favourite: ActionState | null;
  checkout_opened: ActionState | null;
  purchased: ActionState | null;
}

const STATUS_LABEL: Record<string, string> = { sold: "sold", reserved: "reserved for another buyer", removed: "removed by the seller" };

/** After Buy: the old extension's check (two steps), or a price that changed since the analysis. */
interface BuyDialog {
  kind: "checked" | "price_changed";
  result: VintedResult;
}

export function useVintedState(listingId: string) {
  return useQuery({ queryKey: ["vinted", listingId], queryFn: () => api<VintedState>(`/listings/${listingId}/vinted`), refetchInterval: 30_000 });
}

/**
 * Vinted favourite and Buy, done by the FlipFinder extension in your Vinted session: one click,
 * one action (Buy opens Vinted's checkout when the item is still on sale at the analysed price).
 * The payment is always confirmed by you on Vinted.
 */
export function VintedActions({
  listingId,
  vintedId,
  url,
  price,
  showFavourite = true,
  className,
}: {
  listingId: string;
  vintedId: string | null;
  url: string;
  price: number;
  showFavourite?: boolean;
  className?: string;
}) {
  const bridge = useExtensionBridge();
  const state = useVintedState(listingId);
  const qc = useQueryClient();
  const [busy, setBusy] = useState<"favourite" | "buy" | "open" | null>(null);
  const [dialog, setDialog] = useState<BuyDialog | null>(null);
  const refresh = () => {
    void qc.invalidateQueries({ queryKey: ["vinted", listingId] });
    void qc.invalidateQueries({ queryKey: qk.flips });
  };
  const fav = state.data?.favourite?.value ?? null;
  const notReady = bridge.status === "absent" || bridge.status === "unpaired";
  if (!vintedId) return null; // not a Vinted listing

  function warn(r: VintedResult) {
    toast.error(explain(r));
  }

  /** The bridge may answer late (extension asleep, loaded after the page): ask again before refusing. */
  async function ready(): Promise<boolean> {
    const s = await bridge.ensureReady();
    if (s === "ready") return true;
    toast.error(BRIDGE_HELP[s]);
    return false;
  }

  async function onFavourite() {
    setBusy("favourite");
    try {
      if (!(await ready())) return;
      const r = await bridge.act("ff:vinted-favourite", { vid: vintedId!, url, want: !fav });
      if (!r.ok) return warn(r);
      toast.success(r.favourite ? "Added to your Vinted favourites." : "Removed from your Vinted favourites.");
      refresh();
    } finally {
      setBusy(null);
    }
  }

  /** One click: the extension opens the item, checks price and availability and presses Buy. */
  async function onBuy() {
    setBusy("buy");
    try {
      if (!(await ready())) return;
      const out = await bridge.buy({ vid: vintedId!, url, price });
      switch (out.kind) {
        case "opened":
          toast.success(
            `Vinted checkout open${out.result.price != null ? ` at ${eur(out.result.price)}` : ""}: review it and confirm the payment yourself on Vinted.`,
          );
          refresh();
          return;
        case "price_changed":
          setDialog({ kind: "price_changed", result: { ...out.result, price: out.price } });
          return;
        case "checked":
          setDialog({ kind: "checked", result: out.result });
          return;
        case "unavailable":
          warn(out.result);
          refresh();
          return;
        default:
          warn(out.result);
      }
    } finally {
      setBusy(null);
    }
  }

  async function onOpen() {
    setBusy("open");
    // The price you are shown in the dialog: Buy is pressed only if it is still the price on Vinted.
    const expect = dialog?.result.price ?? price;
    const r = await bridge.act("ff:vinted-buy-open", { vid: vintedId!, url, ...(typeof expect === "number" && expect > 0 ? { expect_price: expect } : {}) });
    setBusy(null);
    setDialog(null);
    if (!r.ok) return warn(r);
    toast.success("Vinted checkout open: review it and confirm the payment yourself on Vinted.");
    refresh();
  }

  const check = dialog?.result ?? null;
  const unavailable = dialog?.kind === "checked" && Boolean(check?.status) && check?.status !== "active";
  const newPrice = check?.price ?? null;
  const priceChanged =
    dialog?.kind === "price_changed" || (dialog?.kind === "checked" && !unavailable && newPrice != null && Math.abs(newPrice - price) >= 0.01);
  const purchased = state.data?.purchased;
  const started = state.data?.checkout_opened;

  return (
    <div className={cn("space-y-2", className)}>
      <div className={cn("grid gap-2", showFavourite ? "grid-cols-2" : "grid-cols-1")}>
        {showFavourite && (
          <Button variant="outline" size="sm" className="h-auto min-h-8 py-1.5 leading-tight whitespace-normal" onClick={onFavourite} loading={busy === "favourite"} disabled={busy !== null} aria-pressed={fav === true}>
            <Heart className={fav ? "fill-danger text-danger" : ""} /> {fav ? "In Vinted favourites" : "Add to Vinted favourites"}
          </Button>
        )}
        <Button size="sm" className="h-auto min-h-8 py-1.5 leading-tight whitespace-normal" onClick={onBuy} loading={busy === "buy"} disabled={busy !== null || Boolean(purchased)}>
          <ShoppingCart /> {purchased ? "Bought" : "Buy on Vinted"}
        </Button>
      </div>
      {notReady && <p className="text-[12px] text-warning">{BRIDGE_HELP[bridge.status as "absent" | "unpaired"]}</p>}
      {(purchased || started || state.data?.favourite) && (
        <p className="text-[12px] text-fg-3">
          {purchased
            ? `Bought ${timeAgo(purchased.at)} · paid ${eur(purchased.price)}`
            : started
              ? `Checkout opened ${timeAgo(started.at)} at ${eur(started.price)} · waiting for your payment on Vinted`
              : state.data?.favourite
                ? `Favourite state ${state.data.favourite.source === "page" ? "seen on Vinted" : "set from FlipFinder"} ${timeAgo(state.data.favourite.at)}`
                : null}
        </p>
      )}

      <Dialog open={dialog !== null} onOpenChange={(o) => !o && setDialog(null)}>
        <DialogContent
          title="Buy on Vinted"
          description={
            dialog?.kind === "price_changed"
              ? "Checked just now on the listing page, in your Vinted session: nothing was clicked."
              : "Checked just now on the listing page, in your Vinted session."
          }
        >
          <div className="space-y-4 px-5 py-4 text-[14px]">
            {unavailable ? (
              <p className="flex gap-2 rounded-xl bg-danger-soft p-3 text-danger">
                <AlertTriangle className="mt-0.5 size-4 shrink-0" />
                <span>This item is {STATUS_LABEL[check!.status!] ?? check!.status}: it cannot be bought.</span>
              </p>
            ) : priceChanged ? (
              <p className="flex gap-2 rounded-xl bg-warning-soft p-3 text-warning">
                <AlertTriangle className="mt-0.5 size-4 shrink-0" />
                <span>
                  {newPrice != null ? (
                    <>
                      Price changed: {eur(price)} → <strong>{eur(newPrice)}</strong>.
                    </>
                  ) : (
                    <>The price changed on Vinted.</>
                  )}{" "}
                  The analysis was made at {eur(price)}: check the margin before buying.
                </span>
              </p>
            ) : (
              <p className="rounded-xl bg-success-soft p-3 text-success">Available at {eur(newPrice ?? price)}, same price as analysed.</p>
            )}
            <p className="text-fg-2">The next click opens Vinted&apos;s checkout in the tab just opened. Nothing is paid until you confirm on Vinted.</p>
            <div className="flex flex-wrap justify-end gap-2">
              <Button variant="ghost" onClick={() => setDialog(null)}>
                Cancel
              </Button>
              {!unavailable && (
                <Button onClick={onOpen} loading={busy === "open"}>
                  <ShoppingCart /> {priceChanged && newPrice != null ? `Open checkout at ${eur(newPrice)}` : "Open checkout"}
                </Button>
              )}
            </div>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}
