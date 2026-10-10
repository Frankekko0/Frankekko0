"use client";

import { Copy, MessageSquareText, Sparkles } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/feedback";
import { Segmented } from "@/components/ui/misc";
import { eur, pct } from "@/lib/format";
import { fallbackText, messageLabel, TONES, violationText } from "@/lib/negotiation";
import { useDraftNegotiation, useNegotiation, type DraftTone } from "@/lib/ops";

/**
 * What to offer, the most to pay, and ready messages. The prices are always the ones computed here; the messages are
 * templates or, on request, words written by the AI model around those prices (each one checked by the server).
 * Nothing is sent: the user copies a message and writes to the seller on Vinted.
 */
export function NegotiationCard({ opportunityId }: { opportunityId: string }) {
  const n = useNegotiation(opportunityId, true);
  const draft = useDraftNegotiation(opportunityId);
  const [tone, setTone] = useState<DraftTone>("polite");
  if (n.isLoading) return <Skeleton className="h-40" />;
  if (!n.data) return null;
  const d = n.data;
  const ai = d.ai;
  const why = fallbackText(ai);
  return (
    <Card id="negotiation" className="reveal scroll-mt-28">
      <CardHeader>
        <div className="min-w-0">
          <CardTitle className="flex items-center gap-2 [&_svg]:size-4 [&_svg]:text-fg-3"><MessageSquareText /> Negotiation</CardTitle>
          <CardDescription>{d.note}</CardDescription>
        </div>
      </CardHeader>
      <CardContent className="space-y-4 text-[13px]">
        <div className="grid grid-cols-3 gap-2">
          {[["Asked", d.asked], ["Open with", d.ideal_offer], ["Pay at most", d.max_acceptable]].map(([l, v]) => (
            <div key={l as string} className="rounded-xl border border-line p-2.5">
              <p className="text-[11px] text-fg-3">{l}</p>
              <p className="text-base font-semibold tnum">{v === null ? "—" : eur(v as number)}</p>
            </div>
          ))}
        </div>
        {d.discount_needed !== null && d.discount_needed > 0 && (
          <p className="text-fg-2">To make the minimum profit you need {eur(d.discount_needed)} off{d.discount_needed_pct !== null ? ` (${pct(d.discount_needed_pct)})` : ""}.</p>
        )}
        <p className="text-fg-2">Chance the seller accepts a discount: <b>{pct(d.willingness)}</b>{d.willingness_factors.length > 0 && <span className="text-fg-3"> — {d.willingness_factors.join("; ")}</span>}</p>
        {d.profit_table.length > 0 && (
          <table className="w-full tnum">
            <thead className="text-left text-xs text-fg-3"><tr><th className="py-1 font-medium">Price</th><th className="font-medium">Profit</th><th className="font-medium">ROI</th></tr></thead>
            <tbody>{d.profit_table.map((r) => <tr key={r.price} className="border-t border-line"><td className="py-1">{eur(r.price)}</td><td>{eur(r.profit)}</td><td>{r.roi === null ? "—" : pct(r.roi)}</td></tr>)}</tbody>
          </table>
        )}
        {ai && (
          <div className="space-y-1.5 rounded-xl border border-line p-2.5">
            <div className="flex flex-wrap items-center gap-2">
              <Segmented label="Tone of the AI messages" value={tone} onValueChange={setTone} options={TONES} />
              <Button
                size="sm"
                variant="outline"
                disabled={!ai.enabled}
                loading={draft.isPending}
                onClick={() => draft.mutate({ tone, regenerate: ai.used })}
              >
                <Sparkles /> {ai.used ? "Rewrite with AI" : "Write with AI"}
              </Button>
            </div>
            {ai.used && (
              <p className="text-xs text-fg-2">
                Messages marked AI were written by {ai.provider === "gemini" ? "Gemini" : ai.provider === "anthropic" ? "Claude" : "the model"}
                {ai.model ? ` (${ai.model})` : ""}{ai.tone ? `, ${ai.tone} tone` : ""}{ai.cached ? ", saved draft" : ""}. The prices in them are the ones above. Read them before you send anything.
              </p>
            )}
            {why && <p className={ai.enabled ? "text-xs text-warning" : "text-xs text-fg-3"}>{why}</p>}
            {!ai.used && !why && <p className="text-xs text-fg-3">These are templates. The AI can rewrite them in your tone; the prices never change.</p>}
          </div>
        )}
        <div className="space-y-2">
          {Object.entries(d.messages).map(([k, text]) => {
            const meta = d.messages_meta?.[k];
            const fromModel = meta?.source === "model";
            return (
              <div key={k} className="flex items-start gap-2 rounded-xl bg-surface-2 p-2.5">
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-1.5">
                    <p className="text-[11px] font-medium text-fg-3">{messageLabel(k)}</p>
                    <Badge tone={fromModel ? "accent" : "neutral"}>{fromModel ? <><Sparkles /> AI</> : "Template"}</Badge>
                  </div>
                  <p>{text}</p>
                  {!fromModel && meta && meta.violations.length > 0 && (
                    <p className="mt-1 text-[11px] text-fg-3">The AI draft was not used: it {violationText(meta.violations)}.</p>
                  )}
                </div>
                <Button size="xs" variant="outline" onClick={() => navigator.clipboard.writeText(text).then(() => toast.success("Copied"), () => toast.error("Could not copy"))}><Copy /> Copy</Button>
              </div>
            );
          })}
          <p className="text-[11px] text-fg-3">Nothing is sent from here: copy a message and write to the seller on Vinted.</p>
        </div>
      </CardContent>
    </Card>
  );
}
