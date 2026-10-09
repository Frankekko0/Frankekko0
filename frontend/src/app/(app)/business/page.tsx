"use client";

import { Briefcase, Hammer, ScanLine, Scale } from "lucide-react";
import { useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { SectionHeader, Skeleton } from "@/components/ui/feedback";
import { Field, Input, Select } from "@/components/ui/input";
import { Segmented, Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/misc";
import { StatTile } from "@/components/ui/stat";
import { eur, pct } from "@/lib/format";
import {
  useBusinessPlan,
  useCashflow,
  useCeoReport,
  useGoals,
  useKpis,
  useNiches,
  useRefurb,
  useSaveGoals,
  useScan,
  useTax,
  type BusinessGoals,
  type CashflowRow,
} from "@/lib/ops";

const dash = (v: number | null | undefined, f: (n: number) => string) => (v === null || v === undefined ? "—" : f(v));

function Report() {
  const [period, setPeriod] = useState<"week" | "month">("week");
  const r = useCeoReport(period);
  return (
    <div className="space-y-4">
      <Segmented label="Period" value={period} onValueChange={setPeriod} options={[{ value: "week", label: "Week" }, { value: "month", label: "Month" }]} />
      {r.isLoading || !r.data ? <Skeleton className="h-40" /> : (
        <Card><CardContent className="pt-4"><p className="whitespace-pre-line text-[14px] leading-relaxed">{r.data.text}</p>
          <p className="mt-3 text-xs text-fg-3">{r.data.start} → {r.data.end} · built from your ledger only, nothing estimated</p></CardContent></Card>
      )}
    </div>
  );
}

function CashTable({ rows, title }: { rows: CashflowRow[]; title: string }) {
  return (
    <div>
      <h4 className="mb-1 text-[13px] font-semibold">{title}</h4>
      <div className="overflow-x-auto">
        <table className="w-full text-[13px] tnum">
          <thead className="text-left text-xs text-fg-3"><tr><th className="py-1 pr-3 font-medium">Days</th><th className="pr-3 font-medium">In</th><th className="pr-3 font-medium">Purchases</th><th className="pr-3 font-medium">Costs</th><th className="font-medium">Cash</th></tr></thead>
          <tbody>
            {rows.map((c) => (
              <tr key={c.horizon_days} className="border-t border-line">
                <td className="py-1.5 pr-3">{c.horizon_days}</td><td className="pr-3">{eur(c.inflow)}</td><td className="pr-3">{eur(c.planned_purchases)}</td><td className="pr-3">{eur(c.running_costs)}</td>
                <td className={c.liquidity_risk ? "font-semibold text-danger" : "font-semibold"}>{eur(c.closing_cash)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function Numbers() {
  const k = useKpis();
  const c = useCashflow();
  return (
    <div className="space-y-4">
      {k.isLoading || !k.data ? <Skeleton className="h-28" /> : (
        <>
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            <StatTile label="Realised profit" value={eur(k.data.realized_profit)} sub={`${eur(k.data.sales)} sold`} />
            <StatTile label="Profit per item" value={dash(k.data.contribution_margin_per_item, eur)} />
            <StatTile label="Sell-through" value={dash(k.data.sell_through, pct)} />
            <StatTile label="Profit per hour" value={dash(k.data.hourly_profit, eur)} sub={k.data.hours_basis} />
            <StatTile label="GMROI" value={dash(k.data.gmroi, (n) => n.toFixed(2))} />
            <StatTile label="Days to cash" value={dash(k.data.cash_conversion_days, (n) => n.toFixed(0))} />
            <StatTile label="Idle capital cost" value={eur(k.data.idle_capital_cost)} />
            <StatTile label="Return rate" value={dash(k.data.return_rate, pct)} />
          </div>
          {k.data.aging.length > 0 && (
            <Card><CardHeader><div><CardTitle>Stock age</CardTitle></div></CardHeader><CardContent>
              <ul className="flex flex-wrap gap-2">{k.data.aging.map((a) => <Badge key={a.bucket} tone="neutral">{a.bucket}: {a.items} · {eur(a.cost)}</Badge>)}</ul></CardContent></Card>
          )}
        </>
      )}
      {c.isLoading || !c.data ? <Skeleton className="h-40" /> : (
        <Card>
          <CardHeader><div><CardTitle>Cash flow</CardTitle><CardDescription>{c.data.basis}</CardDescription></div></CardHeader>
          <CardContent className="space-y-4">
            <CashTable rows={c.data.base} title="Expected" />
            <CashTable rows={c.data.stress} title={`Stress: sales ${c.data.stress_definition.sales}, payments ${c.data.stress_definition.payments_delayed_days} days later, returns ${c.data.stress_definition.returns}`} />
            <p className={c.data.liquidity_risk_in_stress ? "rounded-xl bg-danger-soft px-3 py-2 text-[13px] text-danger" : "rounded-xl bg-success-soft px-3 py-2 text-[13px] text-success"}>{c.data.message}</p>
            {c.data.items_without_price > 0 && <p className="text-xs text-fg-3">{c.data.items_without_price} items have no expected price and are left out of the inflow.</p>}
          </CardContent>
        </Card>
      )}
    </div>
  );
}

function Niches() {
  const n = useNiches();
  if (n.isLoading || !n.data) return <Skeleton className="h-40" />;
  return (
    <div className="space-y-4">
      <Card><CardHeader><div><CardTitle>Where the money works hardest</CardTitle><CardDescription>{n.data.note}</CardDescription></div></CardHeader><CardContent>
        {n.data.niches.length === 0 ? <p className="text-[13px] text-fg-3">No closed sales yet, so no niche can be ranked.</p> : (
          <ul className="divide-y divide-line text-[13px]">
            {n.data.niches.map((x) => (
              <li key={x.niche} className="flex flex-wrap items-center gap-x-3 gap-y-1 py-2">
                <b className="min-w-32 flex-1">{x.niche}</b>
                <span className="tnum text-fg-2">{x.n} sold · {eur(x.profit)} · {eur(x.per_euro_day)}/€·day · win {pct(x.win_rate)}</span>
                {x.growing && <Badge tone="success">growing</Badge>}{x.declining && <Badge tone="danger">declining</Badge>}
              </li>
            ))}
          </ul>
        )}
      </CardContent></Card>
      {n.data.allocation.length > 0 && (
        <Card><CardHeader><div><CardTitle>Where to put the next euro</CardTitle></div></CardHeader><CardContent>
          <ul className="space-y-1.5 text-[13px]">{n.data.allocation.map((a) => <li key={a.niche} className="flex justify-between gap-3 rounded-lg bg-surface-2 px-3 py-2"><span><b>{a.niche}</b> — {a.reason}</span><b className="tnum">{eur(a.amount)}</b></li>)}</ul></CardContent></Card>
      )}
    </div>
  );
}

function Plan() {
  const goals = useGoals();
  if (!goals.data) return <Skeleton className="h-40" />;
  return <PlanForm initial={goals.data} />;
}

type Threshold = BusinessGoals["tax_thresholds"][number];
const METRICS = [["revenue", "Revenue"], ["profit", "Profit"], ["sales_count", "Number of sales"]] as const;

function ThresholdEditor({ value, onChange }: { value: Threshold[]; onChange: (v: Threshold[]) => void }) {
  const [t, setT] = useState({ name: "", amount: "", metric: "revenue", source: "", as_of: "" });
  const ok = t.name.trim() && Number(t.amount) > 0 && t.source.trim() && /^\d{4}-\d{2}-\d{2}$/.test(t.as_of);
  return (
    <div className="space-y-2 rounded-xl border border-line p-3">
      <p className="text-[13px] font-semibold">Thresholds you want to watch</p>
      <p className="text-xs text-fg-3">FlipFinder has no thresholds built in. Add one only from an official source you have read, with the date you read it; it refuses one without both.</p>
      {value.map((x, i) => (
        <div key={`${x.name}-${i}`} className="flex items-center justify-between gap-2 rounded-lg bg-surface-2 px-2.5 py-1.5 text-[13px]">
          <span className="min-w-0 truncate"><b>{x.name}</b> · {x.amount} ({x.metric ?? "revenue"}) · {x.source}, {x.as_of}</span>
          <Button type="button" size="xs" variant="ghost" onClick={() => onChange(value.filter((_, j) => j !== i))}>Remove</Button>
        </div>
      ))}
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-5">
        <Field label="Name"><Input value={t.name} onChange={(e) => setT({ ...t, name: e.target.value })} /></Field>
        <Field label="Amount"><Input inputMode="decimal" value={t.amount} onChange={(e) => setT({ ...t, amount: e.target.value })} /></Field>
        <Field label="Measured on">
          <Select value={t.metric} onChange={(e) => setT({ ...t, metric: e.target.value })}>{METRICS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</Select>
        </Field>
        <Field label="Source"><Input value={t.source} onChange={(e) => setT({ ...t, source: e.target.value })} /></Field>
        <Field label="Read on"><Input type="date" value={t.as_of} onChange={(e) => setT({ ...t, as_of: e.target.value })} /></Field>
      </div>
      <Button
        type="button" size="sm" variant="outline" disabled={!ok}
        onClick={() => { onChange([...value, { name: t.name.trim(), amount: Number(t.amount), metric: t.metric, source: t.source.trim(), as_of: t.as_of }]); setT({ name: "", amount: "", metric: "revenue", source: "", as_of: "" }); }}
      >Add threshold</Button>
    </div>
  );
}

function PlanForm({ initial }: { initial: BusinessGoals }) {
  const save = useSaveGoals();
  const [g, setG] = useState<BusinessGoals>(initial);
  const plan = useBusinessPlan(Boolean(initial.monthly_profit_target));
  const tax = useTax();
  const num = (k: keyof BusinessGoals) => (e: React.ChangeEvent<HTMLInputElement>) => setG({ ...g, [k]: e.target.value === "" ? null : Number(e.target.value) });
  return (
    <div className="space-y-4">
      <Card>
        <CardHeader><div><CardTitle>Your goals</CardTitle><CardDescription>The plan is computed from these and from your own results. Where your data is thin it says “assumption”.</CardDescription></div></CardHeader>
        <CardContent>
          <form className="space-y-3" onSubmit={(e) => { e.preventDefault(); save.mutate(g); }}>
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
              <Field label="Monthly profit target €"><Input inputMode="decimal" value={g.monthly_profit_target ?? ""} onChange={num("monthly_profit_target")} /></Field>
              <Field label="Starting capital €"><Input inputMode="decimal" value={g.initial_capital ?? ""} onChange={num("initial_capital")} /></Field>
              <Field label="Capital ceiling €"><Input inputMode="decimal" value={g.max_capital ?? ""} onChange={num("max_capital")} /></Field>
              <Field label="Hours per week"><Input inputMode="numeric" value={g.weekly_hours ?? ""} onChange={num("weekly_hours")} /></Field>
              <Field label="Cash reserve €"><Input inputMode="decimal" value={g.min_reserve} onChange={num("min_reserve")} /></Field>
              <Field label="Reinvest (0–1)"><Input inputMode="decimal" value={g.reinvest_pct} onChange={num("reinvest_pct")} /></Field>
              <Field label="Explore share (0–1)"><Input inputMode="decimal" value={g.explore_share} onChange={num("explore_share")} /></Field>
              <Field label="Status" hint="Used only for the notices below">
                <Select value={g.holder_status} onChange={(e) => setG({ ...g, holder_status: e.target.value as BusinessGoals["holder_status"] })}>
                  <option value="private">Private seller</option><option value="occasional">Occasional</option><option value="habitual">Habitual</option><option value="business">Business</option>
                </Select>
              </Field>
            </div>
            <ThresholdEditor value={g.tax_thresholds} onChange={(v) => setG({ ...g, tax_thresholds: v })} />
            <Button type="submit" loading={save.isPending}>Save goals</Button>
          </form>
        </CardContent>
      </Card>

      {plan.data && (
        <Card>
          <CardHeader><div><CardTitle>Plan to reach it <Badge tone={plan.data.operating.basis === "measured" ? "success" : "warning"}>{plan.data.operating.basis}</Badge></CardTitle><CardDescription>{plan.data.note}</CardDescription></div></CardHeader>
          <CardContent className="space-y-3">
            <div className="grid gap-2 sm:grid-cols-3">
              {plan.data.scenarios.map((s) => (
                <div key={s.name} className="rounded-xl border border-line p-3 text-[13px]">
                  <p className="font-semibold capitalize">{s.name}</p>
                  <p className="tnum text-fg-2">{s.items_per_month.toFixed(0)} items/month · {eur(s.capital_needed)} capital · {s.hours_per_month.toFixed(0)} h</p>
                  <p className="mt-1">Profit about <b className="tnum">{eur(s.achievable_profit)}</b> {!s.feasible && <Badge tone="warning">limited by {s.limited_by}</Badge>}</p>
                </div>
              ))}
            </div>
            <p className="text-[13px] text-fg-2">{plan.data.month_target_reached ? `The target is reached in month ${plan.data.month_target_reached}.` : "The target is not reached within the horizon at this pace."}</p>
          </CardContent>
        </Card>
      )}

      {tax.data && (
        <Card>
          <CardHeader><div><CardTitle className="flex items-center gap-2"><Scale className="size-4" /> Thresholds to watch</CardTitle><CardDescription>{tax.data.note}</CardDescription></div></CardHeader>
          <CardContent className="space-y-2 text-[13px]">
            {tax.data.notices.length === 0 ? <p className="text-fg-3">No threshold set. Add one with the source and the date you read it: nothing is built in.</p> : tax.data.notices.map((n) => (
              <p key={n.name} className={n.level === "exceeded" ? "rounded-xl bg-danger-soft px-3 py-2 text-danger" : n.level === "approaching" ? "rounded-xl bg-warning-soft px-3 py-2 text-warning" : "rounded-xl bg-surface-2 px-3 py-2"}>{n.message} <span className="text-xs opacity-80">({n.source}, {n.as_of})</span></p>
            ))}
            <ul className="list-disc pl-5 text-fg-2">{tax.data.checklist.map((c) => <li key={c}>{c}</li>)}</ul>
          </CardContent>
        </Card>
      )}
    </div>
  );
}

function Scanner() {
  const scan = useScan();
  const [f, setF] = useState({ shown_price: "", brand: "", category: "", size: "" });
  const r = scan.data;
  return (
    <div className="space-y-4">
      <Card>
        <CardHeader><div><CardTitle>In-store check</CardTitle><CardDescription>Type what is on the tag. It answers from your market data in about a second, or says it cannot verify.</CardDescription></div></CardHeader>
        <CardContent>
          <form className="grid grid-cols-2 gap-3 sm:grid-cols-4" onSubmit={(e) => { e.preventDefault(); const p = Number(f.shown_price); if (p > 0) scan.mutate({ shown_price: p, brand: f.brand || undefined, category: f.category || undefined, size: f.size || undefined }); }}>
            <Field label="Shown price €"><Input inputMode="decimal" value={f.shown_price} onChange={(e) => setF({ ...f, shown_price: e.target.value })} /></Field>
            <Field label="Brand"><Input value={f.brand} onChange={(e) => setF({ ...f, brand: e.target.value })} /></Field>
            <Field label="Category"><Input value={f.category} onChange={(e) => setF({ ...f, category: e.target.value })} /></Field>
            <Field label="Size"><Input value={f.size} onChange={(e) => setF({ ...f, size: e.target.value })} /></Field>
            <div className="col-span-2 sm:col-span-4"><Button type="submit" loading={scan.isPending}><ScanLine /> Check</Button></div>
          </form>
        </CardContent>
      </Card>
      {r && (
        <Card><CardContent className="space-y-2 pt-4 text-[13px]">
          <Badge tone={r.verdict === "BUY" ? "success" : r.verdict === "NEGOTIATE" ? "accent" : r.verdict === "PASS" ? "danger" : "warning"}>{r.verdict === "NOT_VERIFIED" ? "Not verified" : r.verdict}</Badge>
          <p>{r.reason}</p>
          {r.max_buy_price !== null && <p className="tnum">Pay at most <b>{eur(r.max_buy_price)}</b>{r.max_buy_price_fast_sale !== null && <> · <b>{eur(r.max_buy_price_fast_sale)}</b> for a quick sale</>} · expected profit {dash(r.expected_profit, eur)}</p>}
          <p className="text-xs text-fg-3">{r.limits}</p>
        </CardContent></Card>
      )}
    </div>
  );
}

function Refurb() {
  const refurb = useRefurb();
  const [f, setF] = useState({ purchase_cost: "", resale_clean: "", defects: "" });
  const r = refurb.data;
  return (
    <div className="space-y-4">
      <Card>
        <CardHeader><div><CardTitle>Is it worth fixing?</CardTitle><CardDescription>Compares the return as is with the return after repair, including your time.</CardDescription></div></CardHeader>
        <CardContent>
          <form className="grid grid-cols-2 gap-3" onSubmit={(e) => { e.preventDefault(); const c = Number(f.purchase_cost), s = Number(f.resale_clean); if (c > 0 && s > 0) refurb.mutate({ purchase_cost: c, resale_clean: s, defects: f.defects.split(",").map((d) => d.trim()).filter(Boolean) }); }}>
            <Field label="Purchase cost €"><Input inputMode="decimal" value={f.purchase_cost} onChange={(e) => setF({ ...f, purchase_cost: e.target.value })} /></Field>
            <Field label="Resale price if clean €"><Input inputMode="decimal" value={f.resale_clean} onChange={(e) => setF({ ...f, resale_clean: e.target.value })} /></Field>
            <div className="col-span-2"><Field label="Defects (comma separated)" hint="e.g. stain, missing button, loose seam"><Input value={f.defects} onChange={(e) => setF({ ...f, defects: e.target.value })} /></Field></div>
            <div className="col-span-2"><Button type="submit" loading={refurb.isPending}><Hammer /> Evaluate</Button></div>
          </form>
        </CardContent>
      </Card>
      {r && (
        <Card><CardContent className="space-y-2 pt-4 text-[13px]">
          <Badge tone={r.worth_it ? "success" : "danger"}>{r.worth_it ? "Worth it" : "Not worth it"}</Badge>
          <p>{r.reason}</p>
          <p className="tnum text-fg-2">As is {eur(r.profit_as_is)} ({dash(r.roi_as_is, pct)}) · after repair {eur(r.profit_after)} ({dash(r.roi_after, pct)}) · materials {eur(r.materials)} · your time {eur(r.labour)}</p>
          <p className="text-xs text-fg-3">{r.assumption}</p>
        </CardContent></Card>
      )}
    </div>
  );
}

export default function BusinessPage() {
  return (
    <div className="space-y-5">
      <SectionHeader title="Business" description="Treat flipping like a small business: results, cash, where to put capital, and what to watch." icon={<Briefcase />} />
      <Tabs defaultValue="report">
        <TabsList>
          <TabsTrigger value="report">Report</TabsTrigger>
          <TabsTrigger value="numbers">Numbers</TabsTrigger>
          <TabsTrigger value="niches">Niches</TabsTrigger>
          <TabsTrigger value="plan">Plan</TabsTrigger>
          <TabsTrigger value="scan">Scanner</TabsTrigger>
          <TabsTrigger value="refurb">Repair</TabsTrigger>
        </TabsList>
        <TabsContent value="report" className="pt-4"><Report /></TabsContent>
        <TabsContent value="numbers" className="pt-4"><Numbers /></TabsContent>
        <TabsContent value="niches" className="pt-4"><Niches /></TabsContent>
        <TabsContent value="plan" className="pt-4"><Plan /></TabsContent>
        <TabsContent value="scan" className="pt-4"><Scanner /></TabsContent>
        <TabsContent value="refurb" className="pt-4"><Refurb /></TabsContent>
      </Tabs>
    </div>
  );
}
