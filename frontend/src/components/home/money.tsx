"use client";

import { ArrowRight, PiggyBank, Wallet } from "lucide-react";
import Link from "next/link";
import { VerdictBadge } from "@/components/deal/verdict";
import { ListingImage } from "@/components/deal/listing-image";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { EmptyState } from "@/components/ui/feedback";
import { eur, timeAgo } from "@/lib/format";
import type { Plan } from "@/lib/types";
import { cn } from "@/lib/utils";

function Figure({ label, value, sub, tone }: { label: string; value: string; sub: string; tone?: "success" | "muted" }) {
  return (
    <div className="min-w-0 rounded-2xl border border-line bg-surface p-3.5 shadow-card">
      <p className="text-xs font-medium text-fg-3">{label}</p>
      <p className={cn("mt-1 truncate text-[22px] font-semibold tracking-[-0.02em] tnum", tone === "success" && "text-success", tone === "muted" && "text-fg-2")}>
        {value}
      </p>
      <p className="mt-0.5 text-[12px] leading-snug text-fg-3">{sub}</p>
    </div>
  );
}

/** Capital, earned profit and forecast side by side: a forecast is never shown as a result. */
export function MoneyStrip({ plan }: { plan: Plan }) {
  return (
    <section aria-label="Your money" className="grid grid-cols-2 gap-3 md:grid-cols-4">
      <Figure
        label="In stock"
        value={eur(plan.invested)}
        sub={plan.owned_items ? `${plan.owned_items} item${plan.owned_items === 1 ? "" : "s"} bought, not sold yet` : "Nothing bought and unsold"}
      />
      <Figure
        label="Available"
        value={plan.available !== null ? eur(Math.max(0, plan.available)) : "—"}
        sub={plan.budget !== null ? `of your ${eur(plan.budget)} budget` : "Set a budget in Settings"}
      />
      <Figure label="Profit earned" value={eur(plan.realized_profit)} sub="Closed sales only" tone={plan.realized_profit > 0 ? "success" : undefined} />
      <Figure
        label="Profit forecast"
        value={plan.selected.length ? eur(plan.potential_profit) : "—"}
        sub="If you follow the plan below · not guaranteed"
        tone="muted"
      />
    </section>
  );
}

/** What to buy with the budget: the best combination, not the best single deal. */
export function PurchasePlan({ plan }: { plan: Plan }) {
  return (
    <Card className="reveal highlight">
      <CardHeader>
        <div className="min-w-0">
          <CardTitle className="flex items-center gap-2 [&_svg]:size-4 [&_svg]:text-fg-3">
            <PiggyBank /> What to buy with your budget
          </CardTitle>
          <CardDescription>
            The combination with the most risk-adjusted profit that fits{plan.available !== null ? ` ${eur(Math.max(0, plan.available))}` : " the budget"}. Only verified Strong buy and Buy compete.
          </CardDescription>
        </div>
        <Link href="/settings" className="shrink-0 text-[13px] font-medium text-accent hover:underline">
          Budget
        </Link>
      </CardHeader>
      <CardContent className="pt-2">
        {plan.selected.length === 0 ? (
          <EmptyState
            className="border-0 py-6"
            icon={<Wallet />}
            title="No plan yet"
            description={plan.reason ?? "Nothing to propose right now."}
            action={
              plan.budget === null ? (
                <Button asChild size="sm" variant="outline">
                  <Link href="/settings">Set a budget</Link>
                </Button>
              ) : undefined
            }
          />
        ) : (
          <>
            <ul className="-mx-2 divide-y divide-line">
              {plan.selected.map((p) => (
                <li key={p.opportunity_id}>
                  <Link href={`/deals/${p.opportunity_id}`} className="press flex items-center gap-3 rounded-lg px-2 py-2.5 transition-[background-color,transform] duration-150 hover:bg-surface-2">
                    <ListingImage src={p.image_url} alt="" className="size-12 shrink-0 rounded-lg" />
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-[13px] font-semibold text-fg">{p.title}</span>
                      <span className="mt-0.5 flex flex-wrap items-center gap-1.5 text-xs text-fg-3">
                        <VerdictBadge verdict={p.verdict} />
                        <span className="tnum">checked {timeAgo(p.last_seen_at)}</span>
                      </span>
                    </span>
                    <span className="shrink-0 text-right">
                      <span className="block text-[13px] font-semibold tnum">{eur(p.total_cost)}</span>
                      <span className="block text-xs text-success tnum">{p.risk_adjusted_profit !== null ? `+${eur(p.risk_adjusted_profit)}` : "—"}</span>
                    </span>
                  </Link>
                </li>
              ))}
            </ul>
            <div className="mt-3 flex flex-wrap items-center justify-between gap-2 rounded-xl bg-surface-2 px-3 py-2.5 text-[13px]">
              <span className="text-fg-2">
                Spend <b className="text-fg tnum">{eur(plan.total_cost)}</b>
                {plan.left !== null && <> · left <span className="tnum">{eur(plan.left)}</span></>}
              </span>
              <Badge tone="neutral" className="tnum">
                forecast +{eur(plan.potential_profit)}
              </Badge>
            </div>
            <p className="mt-2 flex items-center gap-1 text-[11px] text-fg-3">
              Profit is the expected value after probability of sale and authenticity; it is not guaranteed.
              <Link href="/deals?preset=strong_buy" className="ml-auto inline-flex items-center gap-1 font-medium text-accent">
                All buys <ArrowRight className="size-3" />
              </Link>
            </p>
          </>
        )}
      </CardContent>
    </Card>
  );
}
