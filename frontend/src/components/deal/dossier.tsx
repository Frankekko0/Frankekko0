"use client";

import { CircleCheck, CircleDashed, CircleMinus, CircleX, ScanSearch, Sparkles } from "lucide-react";
import { eur } from "@/lib/format";
import type { Dossier, DossierPass, PassStatus } from "@/lib/types";
import { cn } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Meter } from "./score";

const STATUS: Record<PassStatus, { label: string; icon: typeof CircleCheck; tone: string }> = {
  done: { label: "Done", icon: CircleCheck, tone: "text-success" },
  partial: { label: "Partial", icon: CircleDashed, tone: "text-warning" },
  not_possible: { label: "Not possible", icon: CircleX, tone: "text-fg-3" },
  not_applicable: { label: "Not applicable", icon: CircleMinus, tone: "text-fg-3" },
};
const SEVERITY_TONE = { none: "neutral", low: "neutral", medium: "warning", high: "danger" } as const;

function PassRow({ p }: { p: DossierPass }) {
  const s = STATUS[p.status];
  const Icon = s.icon;
  return (
    <li className="flex gap-2.5 py-2">
      <Icon className={cn("mt-0.5 size-4 shrink-0", s.tone)} aria-label={s.label} />
      <div className="min-w-0 flex-1">
        <p className="text-[13px] text-fg">
          <span className="text-fg-3 tnum">{p.code}</span> · {p.name}
        </p>
        <p className="text-[12px] text-fg-2">{p.summary}</p>
        {p.reason && (
          <p className="text-[12px] text-fg-3">
            {p.reason}
            {p.needs ? ` — serve: ${p.needs}` : ""}
          </p>
        )}
      </div>
    </li>
  );
}

/** Everything known about the listing, pass by pass, with what could not be analysed and why. */
export function DossierSection({ dossier }: { dossier: Dossier | null | undefined }) {
  if (!dossier) return null;
  const gaps = dossier.not_analysable.length;
  return (
    <Card id="dossier" className="reveal scroll-mt-28">
      <CardHeader>
        <div className="min-w-0">
          <CardTitle className="flex items-center gap-2 [&_svg]:size-4 [&_svg]:text-fg-3">
            <ScanSearch /> Dossier
          </CardTitle>
          <CardDescription>{dossier.coverage_note}.</CardDescription>
        </div>
      </CardHeader>
      <CardContent>
        <div className="grid grid-cols-1 gap-x-6 gap-y-3 sm:grid-cols-3">
          <div>
            <div className="flex justify-between text-[13px]">
              <span className="text-fg-2">Analysis coverage</span>
              <span className="font-semibold tnum">{dossier.analysis_coverage}%</span>
            </div>
            <Meter value={dossier.analysis_coverage} className="mt-1" />
          </div>
          <div>
            <div className="flex justify-between text-[13px]">
              <span className="text-fg-2">Photo quality</span>
              <span className="font-semibold tnum">{dossier.photo_quality ?? "—"}</span>
            </div>
            <Meter value={dossier.photo_quality ?? 0} className="mt-1" />
          </div>
          <div>
            <div className="flex justify-between text-[13px]">
              <span className="text-fg-2">Inspection coverage</span>
              <span className="font-semibold tnum">{dossier.inspection_coverage ?? "not assessed"}</span>
            </div>
            <Meter value={dossier.inspection_coverage ?? 0} className="mt-1" />
          </div>
        </div>
        {!dossier.category_plugin.covered && (
          <p className="mt-3 text-[12px] text-fg-3">Generic analysis for this category: confidence is reduced.</p>
        )}

        {dossier.hidden_gem.possibly_undervalued && (
          <div className="mt-4 rounded-xl border border-line p-3">
            <p className="flex items-center gap-1.5 text-[13px] font-semibold text-fg">
              <Sparkles className="size-4 text-accent" /> Possible hidden gem
            </p>
            <ul className="mt-1 list-disc pl-5 text-[13px] text-fg-2">
              {dossier.hidden_gem.reasons.map((r) => (
                <li key={r}>{r}</li>
              ))}
            </ul>
            <p className="mt-1 text-[12px] text-fg-3">To verify: {dossier.hidden_gem.to_verify.join(", ")}</p>
          </div>
        )}

        {dossier.contradictions.length > 0 && (
          <div className="mt-4">
            <p className="text-[11px] font-semibold uppercase tracking-[0.08em] text-fg-3">Contradictions</p>
            <ul className="mt-1.5 space-y-1.5">
              {dossier.contradictions.map((c) => (
                <li key={c.code} className="flex flex-wrap items-baseline gap-x-2 text-[13px] text-fg">
                  <Badge tone={SEVERITY_TONE[c.severity]}>{c.severity}</Badge>
                  <span className="min-w-0 flex-1">{c.detail}</span>
                  {c.impact_eur !== null && <span className="text-fg-3 tnum">≈ {eur(c.impact_eur)}</span>}
                </li>
              ))}
            </ul>
          </div>
        )}

        {dossier.missing_photos.length > 0 && (
          <div className="mt-4">
            <p className="text-[11px] font-semibold uppercase tracking-[0.08em] text-fg-3">Ask the seller for</p>
            <ul className="mt-1.5 list-disc pl-5 text-[13px] text-fg-2">
              {dossier.missing_photos.map((m) => (
                <li key={m}>{m}</li>
              ))}
            </ul>
          </div>
        )}

        <details className="group mt-4 rounded-xl bg-surface-2 p-3">
          <summary className="cursor-pointer list-none text-[13px] font-medium text-fg-2">
            Passes P0–P12 {gaps > 0 ? `· ${gaps} could not run fully` : ""}
          </summary>
          <ul className="mt-2 divide-y divide-line">
            {dossier.passes.map((p) => (
              <PassRow key={p.code} p={p} />
            ))}
          </ul>
        </details>
        {dossier.delta && dossier.delta.length > 0 && (
          <p className="mt-3 text-[12px] text-fg-2">Since the last analysis: {dossier.delta.join(" · ")}</p>
        )}
      </CardContent>
    </Card>
  );
}
