"use client";

import { useState, type FormEvent, type ReactNode } from "react";
import { eur } from "@/lib/format";
import { useCreatePurchase, useCreateSale } from "@/lib/queries";
import type { Flip } from "@/lib/types";
import { Button } from "@/components/ui/button";
import { Field, Input, InputAffix, Textarea } from "@/components/ui/input";
import { Dialog, DialogContent, DialogTrigger } from "@/components/ui/misc";

const today = () => new Date().toISOString().slice(0, 10);
const toNum = (s: string) => (s.trim() === "" ? undefined : Number(s.replace(",", ".")));

export function PurchaseDialog({
  trigger,
  defaults,
}: {
  trigger: ReactNode;
  defaults?: { title?: string; opportunity_id?: string; purchase_price?: number; expected_sale_price?: number | null };
}) {
  const [open, setOpen] = useState(false);
  const create = useCreatePurchase();
  const [title, setTitle] = useState(defaults?.title ?? "");
  const [price, setPrice] = useState(defaults?.purchase_price?.toString() ?? "");
  const [bp, setBp] = useState("");
  const [ship, setShip] = useState("");
  const [other, setOther] = useState("");
  const [date, setDate] = useState(today());
  const [expected, setExpected] = useState(defaults?.expected_sale_price?.toString() ?? "");
  const [brand, setBrand] = useState("");
  const [notes, setNotes] = useState("");

  async function submit(e: FormEvent) {
    e.preventDefault();
    await create.mutateAsync({
      title,
      opportunity_id: defaults?.opportunity_id,
      brand: defaults?.opportunity_id ? undefined : brand || undefined,
      purchase_price: toNum(price),
      buyer_protection_fee: toNum(bp),
      shipping_cost: toNum(ship),
      other_costs: toNum(other) ?? 0,
      purchase_date: date,
      expected_sale_price: toNum(expected),
      notes: notes || undefined,
    });
    setOpen(false);
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>{trigger}</DialogTrigger>
      <DialogContent title="Record a purchase" description="Costs left empty are estimated from your cost profile (buyer protection, shipping).">
        <form onSubmit={submit} className="space-y-4">
          <Field label="Item" htmlFor="p-title">
            <Input id="p-title" value={title} onChange={(e) => setTitle(e.target.value)} required maxLength={300} />
          </Field>
          {!defaults?.opportunity_id && (
            <Field label="Brand" htmlFor="p-brand" hint="Used by the learning engine to personalise your scores.">
              <Input id="p-brand" value={brand} onChange={(e) => setBrand(e.target.value)} placeholder="e.g. Ralph Lauren" />
            </Field>
          )}
          <div className="grid grid-cols-2 gap-3">
            <Field label="Purchase price" htmlFor="p-price">
              <InputAffix id="p-price" prefix="€" inputMode="decimal" value={price} onChange={(e) => setPrice(e.target.value)} required />
            </Field>
            <Field label="Purchase date" htmlFor="p-date">
              <Input id="p-date" type="date" value={date} max={today()} onChange={(e) => setDate(e.target.value)} required />
            </Field>
            <Field label="Buyer protection" htmlFor="p-bp">
              <InputAffix id="p-bp" prefix="€" inputMode="decimal" placeholder="auto" value={bp} onChange={(e) => setBp(e.target.value)} />
            </Field>
            <Field label="Shipping" htmlFor="p-ship">
              <InputAffix id="p-ship" prefix="€" inputMode="decimal" placeholder="auto" value={ship} onChange={(e) => setShip(e.target.value)} />
            </Field>
            <Field label="Other costs" htmlFor="p-other">
              <InputAffix id="p-other" prefix="€" inputMode="decimal" placeholder="0" value={other} onChange={(e) => setOther(e.target.value)} />
            </Field>
            <Field label="Expected resale" htmlFor="p-exp">
              <InputAffix id="p-exp" prefix="€" inputMode="decimal" value={expected} onChange={(e) => setExpected(e.target.value)} />
            </Field>
          </div>
          <Field label="Notes" htmlFor="p-notes">
            <Textarea id="p-notes" value={notes} onChange={(e) => setNotes(e.target.value)} maxLength={2000} />
          </Field>
          <Button type="submit" className="w-full" loading={create.isPending}>
            Save purchase
          </Button>
        </form>
      </DialogContent>
    </Dialog>
  );
}

export function SaleDialog({ flip, trigger }: { flip: Flip; trigger: ReactNode }) {
  const [open, setOpen] = useState(false);
  const create = useCreateSale();
  const [price, setPrice] = useState(flip.expected_sale_price?.toString() ?? "");
  const [fees, setFees] = useState("");
  const [ship, setShip] = useState("");
  const [pack, setPack] = useState("");
  const [other, setOther] = useState("");
  const [date, setDate] = useState(today());
  const net = (toNum(price) ?? 0) - (toNum(fees) ?? 0) - (toNum(ship) ?? 0) - (toNum(pack) ?? 0) - (toNum(other) ?? 0);
  const profit = net - flip.total_cost;
  const roi = flip.total_cost > 0 ? profit / flip.total_cost : 0;

  async function submit(e: FormEvent) {
    e.preventDefault();
    await create.mutateAsync({
      purchase_id: flip.purchase_id,
      sale_price: toNum(price),
      selling_fees: toNum(fees) ?? 0,
      shipping_cost: toNum(ship) ?? 0,
      packaging_cost: toNum(pack) ?? 0,
      other_costs: toNum(other) ?? 0,
      sale_date: date,
    });
    setOpen(false);
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>{trigger}</DialogTrigger>
      <DialogContent title="Record a sale" description={flip.title}>
        <form onSubmit={submit} className="space-y-4">
          <div className="grid grid-cols-2 gap-3">
            <Field label="Sale price" htmlFor="s-price">
              <InputAffix id="s-price" prefix="€" inputMode="decimal" value={price} onChange={(e) => setPrice(e.target.value)} required />
            </Field>
            <Field label="Sale date" htmlFor="s-date">
              <Input id="s-date" type="date" value={date} min={flip.purchase_date} max={today()} onChange={(e) => setDate(e.target.value)} required />
            </Field>
            <Field label="Selling fees" htmlFor="s-fees">
              <InputAffix id="s-fees" prefix="€" inputMode="decimal" placeholder="0" value={fees} onChange={(e) => setFees(e.target.value)} />
            </Field>
            <Field label="Shipping paid by you" htmlFor="s-ship">
              <InputAffix id="s-ship" prefix="€" inputMode="decimal" placeholder="0" value={ship} onChange={(e) => setShip(e.target.value)} />
            </Field>
            <Field label="Packaging" htmlFor="s-pack">
              <InputAffix id="s-pack" prefix="€" inputMode="decimal" placeholder="0" value={pack} onChange={(e) => setPack(e.target.value)} />
            </Field>
            <Field label="Other costs" htmlFor="s-other">
              <InputAffix id="s-other" prefix="€" inputMode="decimal" placeholder="0" value={other} onChange={(e) => setOther(e.target.value)} />
            </Field>
          </div>
          <div className="grid grid-cols-3 gap-2 rounded-xl bg-surface-2 p-3 text-center">
            <div>
              <p className="text-[11px] text-fg-3">Total cost</p>
              <p className="font-semibold tnum">{eur(flip.total_cost)}</p>
            </div>
            <div>
              <p className="text-[11px] text-fg-3">Profit</p>
              <p className={`font-semibold tnum ${profit >= 0 ? "text-success" : "text-danger"}`}>{eur(profit, { sign: true })}</p>
            </div>
            <div>
              <p className="text-[11px] text-fg-3">ROI</p>
              <p className={`font-semibold tnum ${profit >= 0 ? "text-success" : "text-danger"}`}>{Math.round(roi * 100)}%</p>
            </div>
          </div>
          <Button type="submit" className="w-full" loading={create.isPending}>
            Save sale
          </Button>
        </form>
      </DialogContent>
    </Dialog>
  );
}
