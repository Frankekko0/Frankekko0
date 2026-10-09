"use client";

import { Dices } from "lucide-react";
import { eur, pct } from "@/lib/format";
import type { FailureMode, Intelligence } from "@/lib/types";
import { cn } from "@/lib/utils";
import { Badge, type BadgeTone } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Meter } from "./score";

const CHECK: Record<FailureMode["check"], { label: string; tone: BadgeTone }> = {
  verified_ok: { label: "Checked", tone: "success" },
  unverifiable: { label: "Cannot be checked yet", tone: "warning" },
  contradicted: { label: "Contradicted", tone: "danger" },
};

/** The profit as a range, not a number, and the three most likely ways the purchase loses money. */
export function IntelligenceSection({ data, price }: { data: Intelligence | null | undefined; price: number }) {
  if (!data) return null;
  const d = data.distribution;
  const risk = data.seller_risk;
  const lo = d ? Math.min(d.p10, -price) : 0;
  const hi = d ? Math.max(d.p90, 1) : 1;
  const at = (v: number) => `${Math.min(100, Math.max(0, ((v - lo) / (hi - lo)) * 100))}%`;
  return (
    <Card id="scenarios" className="reveal scroll-mt-28">
      <CardHeader>
        <div className="min-w-0">
          <CardTitle className="flex items-center gap-2 [&_svg]:size-4 [&_svg]:text-fg-3">
            <Dices /> What could happen
          </CardTitle>
          <CardDescription>
            {d ? `${d.n.toLocaleString("en")} simulated outcomes of this purchase, including a counterfeit, a return or a hidden defect.` : "Not simulated: there is not enough market data."}
          </CardDescription>
        </div>
      </CardHeader>
      <CardContent className="space-y-5">
        {d && (
          <div>
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
              <Fig label="Average profit" value={eur(d.mean)} hint="after the risks" tone={d.mean >= 0 ? "success" : "danger"} />
              <Fig label="Unfavourable (P10)" value={eur(d.p10)} hint="1 time in 10 it is worse" tone={d.p10 >= 0 ? "success" : "danger"} />
              <Fig label="Typical (P50)" value={eur(d.p50)} hint="the middle outcome" />
              <Fig label="Chance of a loss" value={pct(d.p_loss)} hint={d.p_unsold_at_horizon > 0.05 ? `${pct(d.p_unsold_at_horizon)} unsold after 60 days` : "of losing money"} tone={d.p_loss <= 0.3 ? undefined : "danger"} />
            </div>
            <div className="relative mt-4 h-2 rounded-full bg-surface-3" aria-hidden>
              <span className="absolute inset-y-0 rounded-full bg-accent/30" style={{ left: at(d.p10), right: `calc(100% - ${at(d.p90)})` }} />
              <span className="absolute -top-1 h-4 w-0.5 bg-fg-3" style={{ left: at(0) }} title="Break-even" />
              <span className="absolute -top-0.5 size-3 -translate-x-1/2 rounded-full bg-accent ring-2 ring-surface" style={{ left: at(d.p50) }} />
            </div>
            <p className="mt-1.5 flex justify-between text-[11px] text-fg-3 tnum">
              <span>{eur(d.p10)}</span>
              <span>break-even at the line · 90% of outcomes between {eur(d.p10)} and {eur(d.p90)}</span>
              <span>{eur(d.p90)}</span>
            </p>
          </div>
        )}

        {data.premortem && data.premortem.modes.length > 0 && (
          <div>
            <p className="mb-2 text-[13px] font-medium text-fg-2">Before buying: how it could lose money</p>
            <ol className="space-y-2.5">
              {data.premortem.modes.map((m, i) => (
                <li key={m.code} className="rounded-xl border border-line p-3">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="text-[13px] font-semibold text-fg">
                      {i + 1}. {m.title}
                    </span>
                    <Badge tone={CHECK[m.check].tone}>{CHECK[m.check].label}</Badge>
                    <span className="ml-auto text-xs text-fg-3 tnum">
                      {pct(m.probability)} · up to {eur(m.loss)}
                    </span>
                  </div>
                  {m.evidence.length > 0 && <p className="mt-1 text-[12px] text-fg-2">{m.evidence.join(" · ")}</p>}
                  {m.how && <p className="mt-0.5 text-[12px] text-fg-3">To check: {m.how}</p>}
                </li>
              ))}
            </ol>
          </div>
        )}

        <div>
          <div className="flex items-center justify-between text-[13px]">
            <span className="text-fg-2">Seller scam risk</span>
            <span className={cn("font-semibold tnum", risk.level === "high" ? "text-danger" : risk.level === "medium" ? "text-warning" : "text-success")}>{risk.score}/100</span>
          </div>
          <Meter value={100 - risk.score} className="mt-1" />
          <ul className="mt-2 space-y-0.5 text-[12px] text-fg-3">
            {risk.factors.slice(0, 5).map((f) => (
              <li key={f.label}>
                {f.label} {f.impact ? <span className="tnum">({f.impact > 0 ? "+" : ""}{f.impact})</span> : null}
              </li>
            ))}
          </ul>
        </div>

        {data.voi && (
          <p className="rounded-xl bg-surface-2 px-3 py-2 text-[12px] text-fg-2">
            <b className="text-fg">Photo analysis:</b> {data.voi.worth_it ? "worth doing" : "not worth it now"} — {data.voi.reason}.
          </p>
        )}
      </CardContent>
    </Card>
  );
}

function Fig({ label, value, hint, tone }: { label: string; value: string; hint: string; tone?: "success" | "danger" }) {
  return (
    <div className="rounded-xl border border-line p-3">
      <p className="text-[11px] text-fg-3">{label}</p>
      <p className={cn("mt-0.5 text-lg font-semibold tnum", tone === "success" && "text-success", tone === "danger" && "text-danger")}>{value}</p>
      <p className="text-[11px] leading-tight text-fg-3">{hint}</p>
    </div>
  );
}
