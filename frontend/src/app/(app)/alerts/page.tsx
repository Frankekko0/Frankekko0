"use client";

import { BellRing, CheckCheck, Eye, Flame, Target, TrendingDown, Sparkles } from "lucide-react";
import Link from "next/link";
import { useState, type CSSProperties } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { EmptyState, Skeleton } from "@/components/ui/feedback";
import { Segmented } from "@/components/ui/misc";
import { timeAgo } from "@/lib/format";
import { useAlerts, useMarkRead } from "@/lib/queries";
import type { AlertItem } from "@/lib/types";
import { cn } from "@/lib/utils";

const TYPE_META: Record<AlertItem["type"], { label: string; icon: typeof Flame; tone: string }> = {
  ultra_deal: { label: "Ultra deal", icon: Flame, tone: "bg-ultra-soft text-ultra" },
  price_drop: { label: "Price drop", icon: TrendingDown, tone: "bg-success-soft text-success" },
  watchlist_match: { label: "Watchlist", icon: Target, tone: "bg-accent-soft text-accent" },
  new_opportunity: { label: "New opportunity", icon: Sparkles, tone: "bg-accent-soft text-accent" },
  system: { label: "System", icon: BellRing, tone: "bg-surface-2 text-fg-2" },
};

export default function AlertsPage() {
  const [tab, setTab] = useState("all");
  const [page, setPage] = useState(1);
  const filters = tab === "unread" ? { unread_only: true, page } : tab === "all" ? { page } : { type: tab, page };
  const alerts = useAlerts(filters);
  const mark = useMarkRead();
  return (
    <div className="mx-auto max-w-3xl space-y-5">
      <div className="flex items-end justify-between gap-3">
        <div>
          <h1 className="enter text-[28px] font-semibold leading-tight tracking-[-0.025em] sm:text-[32px]">Alerts</h1>
          <p className="enter mt-1.5 text-sm text-fg-2" style={{ "--i": 1 } as CSSProperties}>Deals that matched your thresholds and watchlists.</p>
        </div>
        <Button variant="outline" size="sm" onClick={() => mark.mutate("all")} loading={mark.isPending}>
          <CheckCheck /> Mark all read
        </Button>
      </div>
      <Segmented
        label="Show alerts"
        value={tab}
        onValueChange={(v) => {
          setTab(v);
          setPage(1);
        }}
        options={[
          { value: "all", label: "All" },
          { value: "unread", label: "Unread" },
          { value: "ultra_deal", label: "Ultra deals" },
          { value: "price_drop", label: "Price drops" },
          { value: "watchlist_match", label: "Watchlists" },
          { value: "new_opportunity", label: "New" },
        ]}
      />
      {alerts.isLoading ? (
        <div className="space-y-2">
          {Array.from({ length: 6 }).map((_, i) => (
            <Skeleton key={i} className="h-20 rounded-2xl" />
          ))}
        </div>
      ) : !alerts.data?.items.length ? (
        <EmptyState icon={<BellRing />} title="You're all caught up" description="New alerts appear here and on the channels you enabled in Settings (push, email, Telegram, Discord)." />
      ) : (
        <ul className="space-y-2">
          {alerts.data.items.map((a, i) => {
            const meta = TYPE_META[a.type] ?? TYPE_META.system;
            const Icon = meta.icon;
            const p = a.payload as { price?: number; expected_profit?: number; expected_roi?: number };
            return (
              <li key={a.id} className="enter" style={{ "--i": i } as CSSProperties}>
                <Link
                  href={a.opportunity_id ? `/deals/${a.opportunity_id}` : "#"}
                  onClick={() => !a.read_at && mark.mutate(a.id)}
                  className={cn(
                    "lift highlight flex items-start gap-3 rounded-2xl border bg-surface p-4",
                    a.read_at ? "border-line" : "border-accent/30 shadow-card",
                  )}
                >
                  <span className={cn("flex size-9 shrink-0 items-center justify-center rounded-xl", meta.tone)}>
                    <Icon className="size-4" />
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="flex items-center gap-2">
                      <span className="truncate text-[14px] font-semibold text-fg">{a.title}</span>
                      {!a.read_at && <span className="size-2 shrink-0 rounded-full bg-accent" aria-label="Unread" />}
                    </span>
                    <span className="mt-0.5 block text-[13px] text-fg-2">{a.body}</span>
                    <span className="mt-2 flex flex-wrap items-center gap-1.5">
                      <Badge tone="neutral">{meta.label}</Badge>
                      {a.priority === "high" && <Badge tone="ultra">High priority</Badge>}
                      {a.deliveries
                        .filter((d) => d.channel !== "in_app")
                        .map((d) => (
                          <Badge key={d.channel} tone={d.status === "sent" ? "success" : d.status === "failed" ? "danger" : "outline"}>
                            {d.channel.replace("_", " ")} · {d.status}
                          </Badge>
                        ))}
                      {p.price !== undefined && <span className="text-xs text-fg-3">· listed at €{p.price}</span>}
                    </span>
                  </span>
                  <span className="flex shrink-0 flex-col items-end gap-1 text-[11px] text-fg-3">
                    {timeAgo(a.created_at)}
                    {a.read_at && <Eye className="size-3.5" aria-label="Read" />}
                  </span>
                </Link>
              </li>
            );
          })}
        </ul>
      )}
      {alerts.data && (alerts.data.has_more || page > 1) && (
        <div className="flex justify-center gap-2">
          <Button variant="outline" size="sm" disabled={page === 1} onClick={() => setPage(page - 1)}>
            Previous
          </Button>
          <Button variant="outline" size="sm" disabled={!alerts.data.has_more} onClick={() => setPage(page + 1)}>
            Next
          </Button>
        </div>
      )}
    </div>
  );
}
