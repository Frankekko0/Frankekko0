"use client";

import { Copy, ListChecks, Tag } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { EmptyState, SectionHeader, Skeleton } from "@/components/ui/feedback";
import { Field, Input, Select } from "@/components/ui/input";
import { Chip, Dialog, DialogContent } from "@/components/ui/misc";
import { eur, pct } from "@/lib/format";
import { useEvaluateOffer, usePatchInventory, useSellingInventory, useSellingPlan, type SellingItem } from "@/lib/ops";

const NEXT: Record<string, string[]> = {
  purchased: ["arriving", "to_list"],
  arriving: ["to_list", "returned"],
  to_list: ["listed", "returned"],
  listed: ["sold", "returned", "unsold", "to_list"],
  unsold: ["listed", "to_list"],
  returned: ["to_list", "unsold"],
  sold: ["returned"],
};
const LABEL: Record<string, string> = { identified: "Identified", purchased: "Bought", arriving: "Arriving", to_list: "To list", listed: "Listed", sold: "Sold", returned: "Returned", unsold: "Unsold" };

function copy(text: string) {
  navigator.clipboard.writeText(text).then(() => toast.success("Copied"), () => toast.error("Could not copy"));
}

function PlanDialog({ item, onClose }: { item: SellingItem; onClose: () => void }) {
  const plan = useSellingPlan(item.purchase_id);
  const offer = useEvaluateOffer(item.purchase_id);
  const [value, setValue] = useState("");
  const p = plan.data;
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent title={item.title} description={`${item.stage_label} · cost ${eur(item.total_cost)}`} className="max-w-2xl">
        {plan.isLoading || !p ? (
          <Skeleton className="h-48" />
        ) : (
          <div className="space-y-5 text-[13px]">
            <section>
              <h3 className="mb-1.5 text-[13px] font-semibold">Price plan</h3>
              {p.plan ? (
                <>
                  <div className="grid grid-cols-3 gap-2">
                    {[["Start", p.plan.start_price], ["Best (profit/day)", p.plan.best_price], ["Floor", p.plan.floor]].map(([l, v]) => (
                      <div key={l as string} className="rounded-xl border border-line p-2.5">
                        <p className="text-[11px] text-fg-3">{l}</p>
                        <p className="text-base font-semibold tnum">{eur(v as number)}</p>
                      </div>
                    ))}
                  </div>
                  <p className="mt-2 text-fg-2">
                    About {p.plan.expected_days_at_best.toFixed(0)} days at the best price, {pct(p.plan.p_sold_30d_at_best)} chance to sell within 30 days.{" "}
                    <Badge tone={p.plan.reliable ? "success" : "warning"}>{p.plan.reliable ? "measured" : "assumption"}</Badge>
                  </p>
                  <p className="text-xs text-fg-3">{p.plan.note}</p>
                  <ol className="mt-2 space-y-1">
                    {p.plan.markdowns.map((m) => (
                      <li key={m.day} className="flex justify-between gap-3 rounded-lg bg-surface-2 px-2.5 py-1.5">
                        <span className="text-fg-2">Day {m.day} · {m.why}</span>
                        <b className="tnum">{eur(m.price)}</b>
                      </li>
                    ))}
                  </ol>
                </>
              ) : (
                <p className="text-fg-3">{p.plan_unavailable}</p>
              )}
              {p.reprice && (
                <p className="mt-2 rounded-xl bg-accent-soft px-3 py-2">
                  <b>Today:</b> {p.reprice.action.replace("_", " ")}
                  {p.reprice.new_price ? ` to ${eur(p.reprice.new_price)}` : ""} — {p.reprice.reason}
                </p>
              )}
            </section>

            <section>
              <h3 className="mb-1.5 flex items-center justify-between text-[13px] font-semibold">
                Listing draft
                <Button size="xs" variant="outline" onClick={() => copy(`${p.draft.title}\n\n${p.draft.description}`)}>
                  <Copy /> Copy
                </Button>
              </h3>
              <p className="font-medium">{p.draft.title}</p>
              <p className="mt-1 whitespace-pre-line text-fg-2">{p.draft.description}</p>
              {p.draft.to_confirm.length > 0 && (
                <div className="mt-2">
                  <p className="text-xs font-medium text-warning">Confirm before publishing</p>
                  <ul className="list-disc pl-5 text-fg-2">{p.draft.to_confirm.map((t) => <li key={t}>{t}</li>)}</ul>
                </div>
              )}
              <details className="mt-2">
                <summary className="flex cursor-pointer items-center gap-1.5 text-fg-2"><ListChecks className="size-4" /> Photo checklist ({p.draft.photo_checklist.length})</summary>
                <ul className="mt-1 list-disc pl-5 text-fg-2">{p.draft.photo_checklist.map((t) => <li key={t}>{t}</li>)}</ul>
              </details>
              <p className="mt-2 text-xs text-fg-3">Not claimed: {p.draft.not_claimed.join(" · ")}</p>
            </section>

            {item.stage === "listed" && (
              <section>
                <h3 className="mb-1.5 text-[13px] font-semibold">A buyer made an offer</h3>
                <form
                  className="flex gap-2"
                  onSubmit={(e) => {
                    e.preventDefault();
                    const n = Number(value);
                    if (n > 0) offer.mutate({ offer: n });
                  }}
                >
                  <Input inputMode="decimal" placeholder="Offer in €" value={value} onChange={(e) => setValue(e.target.value)} aria-label="Offer in euros" />
                  <Button type="submit" size="sm" loading={offer.isPending}>Evaluate</Button>
                </form>
                {offer.data && (
                  <div className="mt-2 rounded-xl border border-line p-3">
                    <Badge tone={offer.data.action === "accept" ? "success" : offer.data.action === "counter" ? "accent" : "danger"}>
                      {offer.data.action === "counter" && offer.data.counter_price ? `Counter at ${eur(offer.data.counter_price)}` : offer.data.action}
                    </Badge>
                    <p className="mt-1 text-fg-2">{offer.data.reason}</p>
                    <p className="mt-1 text-xs text-fg-3 tnum">Profit at this offer {eur(offer.data.profit_at_offer)} · waiting is worth {eur(offer.data.value_of_waiting)} · floor {eur(offer.data.floor)}</p>
                    <div className="mt-2 flex items-start gap-2 rounded-lg bg-surface-2 p-2.5">
                      <p className="flex-1">{offer.data.reply}</p>
                      <Button size="xs" variant="outline" onClick={() => copy(offer.data!.reply)}><Copy /> Copy</Button>
                    </div>
                  </div>
                )}
              </section>
            )}
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}

function Row({ item, onPlan }: { item: SellingItem; onPlan: () => void }) {
  const patch = usePatchInventory();
  const [price, setPrice] = useState(item.listed_price?.toString() ?? "");
  const moves = NEXT[item.stage] ?? [];
  return (
    <Card className="p-3.5">
      <div className="flex flex-wrap items-start gap-x-4 gap-y-2">
        <div className="min-w-0 flex-1">
          <p className="truncate text-[14px] font-semibold">{item.title}</p>
          <p className="text-xs text-fg-3 tnum">
            Cost {eur(item.total_cost)}
            {item.listed_price ? ` · asking ${eur(item.listed_price)}` : ""}
            {item.days_listed !== null ? ` · ${item.days_listed.toFixed(0)} days listed` : ""}
          </p>
        </div>
        <Badge tone={item.stage === "sold" ? "success" : item.stage === "listed" ? "accent" : "neutral"}>{LABEL[item.stage] ?? item.stage}</Badge>
      </div>
      <div className="mt-3 flex flex-wrap items-end gap-2">
        {moves.length > 0 && item.stage !== "sold" && (
          <Field label="Move to">
            <Select value="" onChange={(e) => e.target.value && patch.mutate({ id: item.purchase_id, body: { stage: e.target.value } })} aria-label="Move to stage">
              <option value="">Choose…</option>
              {moves.map((m) => <option key={m} value={m}>{LABEL[m]}</option>)}
            </Select>
          </Field>
        )}
        {(item.stage === "listed" || item.stage === "to_list") && (
          <form
            className="flex items-end gap-2"
            onSubmit={(e) => {
              e.preventDefault();
              const n = Number(price);
              if (n > 0) patch.mutate({ id: item.purchase_id, body: { listed_price: n, reason: "prezzo aggiornato" } });
            }}
          >
            <Field label="Asking price €"><Input className="w-28" inputMode="decimal" value={price} onChange={(e) => setPrice(e.target.value)} /></Field>
            <Button size="sm" variant="outline" type="submit" loading={patch.isPending}>Set</Button>
          </form>
        )}
        <Button size="sm" variant="primary" className="ml-auto" onClick={onPlan}><Tag /> Plan & listing</Button>
      </div>
    </Card>
  );
}

export default function SellingPage() {
  const inv = useSellingInventory();
  const [stage, setStage] = useState<string | null>(null);
  const [open, setOpen] = useState<SellingItem | null>(null);
  const items = (inv.data?.items ?? []).filter((i) => !stage || i.stage === stage);
  return (
    <div className="space-y-5">
      <SectionHeader title="Selling" description="Everything you bought, by stage: a truthful listing, a price plan with markdowns and offers evaluated by what waiting is worth." />
      <div className="flex flex-wrap gap-1.5">
        <Chip active={!stage} onClick={() => setStage(null)}>All {inv.data?.items.length ?? 0}</Chip>
        {Object.entries(inv.data?.by_stage ?? {}).map(([s, n]) => (
          <Chip key={s} active={stage === s} onClick={() => setStage(stage === s ? null : s)}>{LABEL[s] ?? s} {n}</Chip>
        ))}
      </div>
      {inv.isLoading ? (
        <Skeleton className="h-40" />
      ) : items.length === 0 ? (
        <EmptyState icon={<Tag />} title="Nothing here yet" description="Record a purchase in My Flips (from a deal or by hand) and it appears here, ready to list." />
      ) : (
        <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">{items.map((i) => <Row key={i.purchase_id} item={i} onPlan={() => setOpen(i)} />)}</div>
      )}
      <Card>
        <CardHeader><div><CardTitle>How prices are decided</CardTitle><CardDescription>The start price leaves room for a first offer; the floor is the lowest price that still earns your minimum profit. How long to wait at each step comes from how fast similar items sold; with too few sales it says “assumption”.</CardDescription></div></CardHeader>
        <CardContent className="text-[13px] text-fg-3">Nothing is published for you: FlipFinder prepares the text and the price, you publish on Vinted.</CardContent>
      </Card>
      {open && <PlanDialog item={open} onClose={() => setOpen(null)} />}
    </div>
  );
}
