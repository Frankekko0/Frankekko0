"use client";

import { ExternalLink, MoreHorizontal, Package, Plus, Receipt, Trash2, Trophy } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { MonthlyProfitChart } from "@/components/charts/monthly-bars";
import { PurchaseDialog, SaleDialog } from "@/components/forms/flip-forms";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { EmptyState, Skeleton } from "@/components/ui/feedback";
import { Menu, MenuContent, MenuItem, MenuSeparator, MenuTrigger, Tabs, TabsList, TabsTrigger } from "@/components/ui/misc";
import { StatTile } from "@/components/ui/stat";
import { eur, pct, plural, shortDate } from "@/lib/format";
import { useDeletePurchase, useDeleteSale, useFlips, usePortfolio, useUpdatePurchase } from "@/lib/queries";
import type { Flip } from "@/lib/types";
import { cn } from "@/lib/utils";

const STATUS: Record<Flip["status"], { label: string; tone: "neutral" | "accent" | "success" | "danger" }> = {
  in_stock: { label: "In stock", tone: "neutral" },
  listed: { label: "Listed", tone: "accent" },
  sold: { label: "Sold", tone: "success" },
  returned: { label: "Returned", tone: "danger" },
};

function FlipActions({ f }: { f: Flip }) {
  const del = useDeletePurchase();
  const delSale = useDeleteSale();
  const upd = useUpdatePurchase();
  return (
    <div className="flex items-center justify-end gap-1">
      {f.status !== "sold" && (
        <SaleDialog
          flip={f}
          trigger={
            <Button size="xs" variant="outline">
              <Receipt /> Sold
            </Button>
          }
        />
      )}
      <Menu>
        <MenuTrigger className="rounded-md p-1.5 text-fg-3 hover:bg-surface-2 hover:text-fg" aria-label="More actions">
          <MoreHorizontal className="size-4" />
        </MenuTrigger>
        <MenuContent>
          {f.opportunity_id && (
            <MenuItem asChild>
              <Link href={`/deals/${f.opportunity_id}`}>
                <ExternalLink /> Open analysis
              </Link>
            </MenuItem>
          )}
          {f.status === "in_stock" && <MenuItem onSelect={() => upd.mutate({ id: f.purchase_id, body: { inventory_status: "listed" } })}>Mark as listed</MenuItem>}
          {f.status === "listed" && <MenuItem onSelect={() => upd.mutate({ id: f.purchase_id, body: { inventory_status: "in_stock" } })}>Back in stock</MenuItem>}
          {f.status !== "sold" && f.status !== "returned" && (
            <MenuItem onSelect={() => upd.mutate({ id: f.purchase_id, body: { inventory_status: "returned" } })}>Mark as returned</MenuItem>
          )}
          {f.sale && <MenuItem onSelect={() => delSale.mutate(f.sale!.id)}>Undo sale</MenuItem>}
          <MenuSeparator />
          <MenuItem className="text-danger data-[highlighted]:text-danger" onSelect={() => confirm("Delete this purchase and its sale?") && del.mutate(f.purchase_id)}>
            <Trash2 /> Delete
          </MenuItem>
        </MenuContent>
      </Menu>
    </div>
  );
}

export default function FlipsPage() {
  const flips = useFlips();
  const portfolio = usePortfolio();
  const [tab, setTab] = useState("all");
  const p = portfolio.data;
  const rows = (flips.data ?? []).filter((f) => tab === "all" || (tab === "inventory" ? f.status !== "sold" : f.status === "sold"));

  return (
    <div className="space-y-6">
      <div className="flex items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">My Flips</h1>
          <p className="mt-1 text-sm text-fg-2">Your purchases, inventory and realized profit. Every flip teaches your personal score.</p>
        </div>
        <PurchaseDialog
          trigger={
            <Button>
              <Plus /> Add purchase
            </Button>
          }
        />
      </div>

      <section className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <StatTile label="Profit" value={eur(p?.profit)} sub={p ? `${plural(p.flips_completed, "flip")} completed` : undefined} tone="success" icon={<Trophy />} loading={portfolio.isLoading} />
        <StatTile label="Average ROI" value={pct(p?.average_roi)} sub={p?.win_rate !== null && p?.win_rate !== undefined ? `win rate ${pct(p.win_rate)}` : "no sales yet"} loading={portfolio.isLoading} />
        <StatTile label="Revenue" value={eur(p?.revenue)} sub={p ? `invested ${eur(p.total_invested)}` : undefined} loading={portfolio.isLoading} />
        <StatTile
          label="Inventory value"
          value={eur(p?.inventory_value)}
          sub={p ? `${plural(p.inventory_items, "item")} · cost ${eur(p.inventory_cost)}` : undefined}
          icon={<Package />}
          loading={portfolio.isLoading}
        />
      </section>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
        <Card>
          <CardHeader>
            <CardTitle>Monthly profit</CardTitle>
          </CardHeader>
          <CardContent>
            {p?.monthly.length ? (
              <MonthlyProfitChart data={p.monthly} />
            ) : (
              <p className="py-10 text-center text-[13px] text-fg-3">Record your first sale to see monthly profit.</p>
            )}
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle>Performance</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3 text-[13px]">
            {[
              ["Total invested", eur(p?.total_invested)],
              ["Average holding time", p?.average_holding_days !== null && p?.average_holding_days !== undefined ? `${p.average_holding_days} days` : "—"],
              ["Win rate", pct(p?.win_rate)],
              ["Purchases", p?.purchases ?? "—"],
            ].map(([k, v]) => (
              <div key={String(k)} className="flex justify-between border-b border-line pb-2">
                <span className="text-fg-3">{k}</span>
                <span className="font-semibold tnum">{v}</span>
              </div>
            ))}
            {p?.best_flip && (
              <div className="rounded-xl bg-success-soft p-3">
                <p className="text-xs text-fg-3">Best flip</p>
                <p className="truncate font-semibold text-fg">{p.best_flip.title}</p>
                <p className="text-success">
                  {eur(p.best_flip.profit, { sign: true })} · ROI {pct(p.best_flip.roi)}
                </p>
              </div>
            )}
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader className="items-center">
          <CardTitle>Purchases & sales</CardTitle>
          <Tabs value={tab} onValueChange={setTab}>
            <TabsList>
              <TabsTrigger value="all">All</TabsTrigger>
              <TabsTrigger value="inventory">Inventory</TabsTrigger>
              <TabsTrigger value="sold">Sold</TabsTrigger>
            </TabsList>
          </Tabs>
        </CardHeader>
        <CardContent>
          {flips.isLoading ? (
            <Skeleton className="h-40 rounded-xl" />
          ) : !rows.length ? (
            <EmptyState
              icon={<Package />}
              title="No flips recorded yet"
              description="Open a deal and press “I bought it”, or add a purchase manually. Then record the sale to track profit and ROI."
              className="border-0"
            />
          ) : (
            <div className="-mx-5 overflow-x-auto">
              <table className="w-full min-w-[760px] text-left text-[13px]">
                <thead>
                  <tr className="border-b border-line text-xs text-fg-3">
                    <th className="px-5 py-2 font-medium">Item</th>
                    <th className="px-2 py-2 font-medium">Status</th>
                    <th className="px-2 py-2 text-right font-medium">Bought</th>
                    <th className="px-2 py-2 text-right font-medium">Total cost</th>
                    <th className="px-2 py-2 text-right font-medium">Sold</th>
                    <th className="px-2 py-2 text-right font-medium">Profit</th>
                    <th className="px-2 py-2 text-right font-medium">ROI</th>
                    <th className="px-2 py-2 text-right font-medium">Holding</th>
                    <th className="px-5 py-2" />
                  </tr>
                </thead>
                <tbody>
                  {rows.map((f) => (
                    <tr key={f.purchase_id} className="border-b border-line last:border-0">
                      <td className="px-5 py-2.5">
                        <p className="max-w-[260px] truncate font-medium text-fg">{f.title}</p>
                        <p className="text-xs text-fg-3">
                          {[f.brand, f.size].filter(Boolean).join(" · ") || "—"} · {shortDate(f.purchase_date)}
                        </p>
                      </td>
                      <td className="px-2 py-2.5">
                        <Badge tone={STATUS[f.status].tone}>{STATUS[f.status].label}</Badge>
                      </td>
                      <td className="px-2 py-2.5 text-right tnum">{eur(f.purchase_price)}</td>
                      <td className="px-2 py-2.5 text-right tnum">{eur(f.total_cost)}</td>
                      <td className="px-2 py-2.5 text-right tnum">{f.sale ? eur(f.sale.sale_price) : <span className="text-fg-3">exp. {eur(f.expected_sale_price)}</span>}</td>
                      <td className={cn("px-2 py-2.5 text-right font-semibold tnum", f.profit === null ? "text-fg-3" : f.profit >= 0 ? "text-success" : "text-danger")}>
                        {f.profit === null ? "—" : eur(f.profit, { sign: true })}
                      </td>
                      <td className="px-2 py-2.5 text-right tnum">{pct(f.roi)}</td>
                      <td className="px-2 py-2.5 text-right tnum">{f.holding_days !== null ? `${f.holding_days}d` : "—"}</td>
                      <td className="px-5 py-2.5">
                        <FlipActions f={f} />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
