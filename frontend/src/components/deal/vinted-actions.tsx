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

export function useVintedState(listingId: string) {
  return useQuery({ queryKey: ["vinted", listingId], queryFn: () => api<VintedState>(`/listings/${listingId}/vinted`), refetchInterval: 30_000 });
}

/**
 * Vinted favourite and Buy, done by the FlipFinder extension in your Vinted session: one click,
 * one action. The payment is always confirmed by you on Vinted.
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
  const [busy, setBusy] = useState<"favourite" | "check" | "open" | null>(null);
  const [check, setCheck] = useState<VintedResult | null>(null);
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

  async function onFavourite() {
    if (notReady) return toast.error(BRIDGE_HELP[bridge.status as "absent" | "unpaired"]);
    setBusy("favourite");
    const r = await bridge.act("ff:vinted-favourite", { vid: vintedId!, url, want: !fav });
    setBusy(null);
    if (!r.ok) return warn(r);
    toast.success(r.favourite ? "Added to your Vinted favourites." : "Removed from your Vinted favourites.");
    refresh();
  }

  async function onCheck() {
    if (notReady) return toast.error(BRIDGE_HELP[bridge.status as "absent" | "unpaired"]);
    setBusy("check");
    const r = await bridge.act("ff:vinted-buy-check", { vid: vintedId!, url });
    setBusy(null);
    if (!r.ok) return warn(r);
    if (r.signedIn === false) return warn({ ok: false, code: "signed_out" });
    setCheck(r);
  }

  async function onOpen() {
    setBusy("open");
    const r = await bridge.act("ff:vinted-buy-open", { vid: vintedId!, url });
    setBusy(null);
    setCheck(null);
    if (!r.ok) return warn(r);
    toast.success("Vinted checkout open: review it and confirm the payment yourself on Vinted.");
    refresh();
  }

  const unavailable = check && check.status && check.status !== "active";
  const priceChanged = check && !unavailable && check.price != null && Math.abs(check.price - price) >= 0.01;
  const purchased = state.data?.purchased;
  const started = state.data?.checkout_opened;

  return (
    <div className={cn("space-y-2", className)}>
      <div className={cn("grid gap-2", showFavourite ? "grid-cols-2" : "grid-cols-1")}>
        {showFavourite && (
          <Button variant="outline" size="sm" onClick={onFavourite} loading={busy === "favourite"} disabled={busy !== null} aria-pressed={fav === true}>
            <Heart className={fav ? "fill-danger text-danger" : ""} /> {fav ? "In Vinted favourites" : "Add to Vinted favourites"}
          </Button>
        )}
        <Button size="sm" onClick={onCheck} loading={busy === "check"} disabled={busy !== null || Boolean(purchased)}>
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

      <Dialog open={check !== null} onOpenChange={(o) => !o && setCheck(null)}>
        <DialogContent title="Buy on Vinted" description="Checked just now on the listing page, in your Vinted session.">
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
                  Price changed: {eur(price)} → <strong>{eur(check!.price)}</strong>. The analysis was made at the old price: check the margin before buying.
                </span>
              </p>
            ) : (
              <p className="rounded-xl bg-success-soft p-3 text-success">Available at {eur(check?.price ?? price)}, same price as analysed.</p>
            )}
            <p className="text-fg-2">The next click opens Vinted&apos;s checkout in the tab just opened. Nothing is paid until you confirm on Vinted.</p>
            <div className="flex justify-end gap-2">
              <Button variant="ghost" onClick={() => setCheck(null)}>
                Cancel
              </Button>
              {!unavailable && (
                <Button onClick={onOpen} loading={busy === "open"}>
                  <ShoppingCart /> {priceChanged ? `Open checkout at ${eur(check!.price)}` : "Open checkout"}
                </Button>
              )}
            </div>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}
