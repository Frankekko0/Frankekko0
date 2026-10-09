"use client";

import { Copy, MessageSquareText } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/feedback";
import { eur, pct } from "@/lib/format";
import { useNegotiation } from "@/lib/ops";

const TONE: Record<string, string> = { polite: "Polite", direct: "Direct", bundle: "Bundle", firm: "Firm" };

/** What to offer, the most to pay, and ready messages. Nothing is sent: the user writes to the seller on Vinted. */
export function NegotiationCard({ opportunityId }: { opportunityId: string }) {
  const n = useNegotiation(opportunityId, true);
  if (n.isLoading) return <Skeleton className="h-40" />;
  if (!n.data) return null;
  const d = n.data;
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
        <div className="space-y-2">
          {Object.entries(d.messages).map(([k, text]) => (
            <div key={k} className="flex items-start gap-2 rounded-xl bg-surface-2 p-2.5">
              <div className="flex-1"><p className="text-[11px] font-medium text-fg-3">{TONE[k] ?? k}</p><p>{text}</p></div>
              <Button size="xs" variant="outline" onClick={() => navigator.clipboard.writeText(text).then(() => toast.success("Copied"), () => toast.error("Could not copy"))}><Copy /> Copy</Button>
            </div>
          ))}
        </div>
      </CardContent>
    </Card>
  );
}
