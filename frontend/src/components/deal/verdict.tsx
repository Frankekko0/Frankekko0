"use client";

import { Ban, Binoculars, CircleCheck, CircleHelp, CircleX, Handshake, ShieldCheck } from "lucide-react";
import type { ReactNode } from "react";
import { eur } from "@/lib/format";
import type { DecisionInfo, DecisionVerdict } from "@/lib/types";
import { cn } from "@/lib/utils";
import { Badge, type BadgeTone } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Meter } from "./score";

export const VERDICT_STYLE: Record<DecisionVerdict, { label: string; tone: BadgeTone; icon: ReactNode; hint: string }> = {
  STRONG_BUY: { label: "Strong buy", tone: "success", icon: <ShieldCheck />, hint: "Every requirement is verified" },
  BUY: { label: "Buy", tone: "success", icon: <CircleCheck />, hint: "Meets your targets at this price" },
  NEGOTIATE: { label: "Negotiate", tone: "accent", icon: <Handshake />, hint: "Works only at a lower price" },
  WATCHLIST: { label: "Watchlist", tone: "warning", icon: <Binoculars />, hint: "Promising, not yet a buy" },
  PASS: { label: "Pass", tone: "neutral", icon: <Ban />, hint: "Not worth it" },
  INSUFFICIENT_EVIDENCE: { label: "Insufficient evidence", tone: "outline", icon: <CircleHelp />, hint: "Not enough to judge" },
};

const SCORES: { key: keyof DecisionInfo["scores"]; label: string; hint: string; invert?: boolean }[] = [
  { key: "flip", label: "Flip", hint: "how attractive" },
  { key: "confidence", label: "Confidence", hint: "how sure the analysis is" },
  { key: "risk", label: "Risk", hint: "how likely it goes wrong", invert: true },
  { key: "completeness", label: "Data", hint: "how much of the listing was read" },
];

/** The verdict and the four scores kept apart: no single number decides. */
export function VerdictSection({ decision }: { decision: DecisionInfo | null | undefined }) {
  if (!decision) return null;
  const v = VERDICT_STYLE[decision.verdict];
  const unmet = decision.strong_buy_requirements.filter((r) => !r.met);
  const binding = decision.vetoes.filter((x) => x.binding);
  return (
    <Card id="verdict" className="reveal scroll-mt-28">
      <CardHeader>
        <div className="min-w-0">
          <CardTitle className="flex items-center gap-2">Verdict</CardTitle>
          <CardDescription>
            Decided from the evidence, not from one score. A high margin cannot buy back counterfeit risk or missing data.
          </CardDescription>
        </div>
      </CardHeader>
      <CardContent>
        <div className="flex flex-wrap items-center gap-3">
          <Badge tone={v.tone} className="px-3 py-1.5 text-[14px] [&_svg]:size-4">
            {v.icon} {v.label}
          </Badge>
          <span className="text-[13px] text-fg-2">{v.hint}</span>
          {decision.threshold_price !== null && decision.verdict !== "STRONG_BUY" && decision.verdict !== "BUY" && (
            <span className="text-[13px] font-medium text-fg tnum">Works at {eur(Number(decision.threshold_price))} or less</span>
          )}
        </div>

        <div className="mt-4 grid grid-cols-2 gap-x-6 gap-y-3 sm:grid-cols-4">
          {SCORES.map((s) => {
            const value = decision.scores[s.key];
            return (
              <div key={s.key}>
                <div className="flex justify-between text-[13px]">
                  <span className="text-fg-2">{s.label}</span>
                  <span className="font-semibold tnum">{value}</span>
                </div>
                <Meter value={s.invert ? 100 - value : value} className="mt-1" />
                <p className="mt-0.5 text-[11px] text-fg-3">{s.hint}</p>
              </div>
            );
          })}
        </div>

        {decision.reasons.length > 0 && <Bullets title="Why" tone="success" items={decision.reasons} />}
        {(binding.length > 0 || decision.warnings.length > 0) && (
          <Bullets title="Watch out" tone="warning" items={[...new Set([...binding.map((b) => b.label), ...decision.warnings])]} />
        )}
        {decision.missing_info.length > 0 && (
          <Bullets title="Still missing" tone="neutral" items={decision.missing_info.map((m) => m.label)} />
        )}

        {unmet.length > 0 && decision.verdict !== "INSUFFICIENT_EVIDENCE" && (
          <details className="group mt-4 rounded-xl bg-surface-2 p-3">
            <summary className="cursor-pointer list-none text-[13px] font-medium text-fg-2">
              What a Strong buy still needs ({unmet.length})
            </summary>
            <ul className="mt-2 space-y-1.5">
              {decision.strong_buy_requirements.map((r) => (
                <li key={r.code} className="flex items-start gap-2 text-[13px] text-fg-2">
                  {r.met ? <CircleCheck className="mt-0.5 size-4 shrink-0 text-success" aria-hidden /> : <CircleX className="mt-0.5 size-4 shrink-0 text-fg-3" aria-hidden />}
                  <span className={cn(!r.met && "text-fg")}>{r.label}</span>
                </li>
              ))}
            </ul>
          </details>
        )}
        <p className="mt-3 text-[11px] text-fg-3">Rules {decision.rules_version}</p>
      </CardContent>
    </Card>
  );
}

function Bullets({ title, items, tone }: { title: string; items: string[]; tone: "success" | "warning" | "neutral" }) {
  return (
    <div className="mt-4">
      <p className="text-[11px] font-semibold uppercase tracking-[0.08em] text-fg-3">{title}</p>
      <ul className="mt-1.5 space-y-1">
        {items.map((line) => (
          <li key={line} className="flex gap-2 text-[14px] text-fg">
            <span
              aria-hidden
              className={cn("mt-2 size-1.5 shrink-0 rounded-full", tone === "success" ? "bg-success" : tone === "warning" ? "bg-warning" : "bg-fg-3")}
            />
            {line}
          </li>
        ))}
      </ul>
    </div>
  );
}
