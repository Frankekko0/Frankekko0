"use client";

import { Sparkles } from "lucide-react";
import { Meter } from "@/components/deal/score";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { ErrorState, Skeleton } from "@/components/ui/feedback";
import { useAiUsage } from "@/lib/queries";

const usd = (v: string) => `$${Number(v).toFixed(Number(v) < 1 ? 3 : 2)}`;

function Spend({ label, spent, cap, left }: { label: string; spent: string; cap: string; left: string }) {
  const share = Number(cap) > 0 ? Math.min(100, (Number(spent) / Number(cap)) * 100) : 100;
  return (
    <div>
      <div className="flex justify-between text-[13px]">
        <span className="text-fg-2">{label}</span>
        <span className="font-semibold tnum">
          {usd(spent)} <span className="font-normal text-fg-3">of {usd(cap)}</span>
        </span>
      </div>
      <Meter value={share} color={share >= 90 ? "var(--danger)" : "var(--series-1)"} className="mt-1" />
      <p className="mt-0.5 text-[11px] text-fg-3 tnum">{usd(left)} left</p>
    </div>
  );
}

/** What the AI has cost today and this month, against the caps that stop it. */
export function AiUsageCard() {
  const { data, isPending, isError, refetch } = useAiUsage();
  return (
    <Card className="reveal">
      <CardHeader>
        <div className="min-w-0">
          <CardTitle className="flex items-center gap-2 [&_svg]:size-4 [&_svg]:text-fg-3">
            <Sparkles /> AI spend
          </CardTitle>
          <CardDescription>
            Every paid model call is counted. At the cap nothing more is called and the app uses its rules.
          </CardDescription>
        </div>
      </CardHeader>
      <CardContent>
        {isPending ? (
          <Skeleton className="h-24" />
        ) : isError || !data ? (
          <ErrorState message="Could not read the AI spend." onRetry={() => refetch()} />
        ) : (
          <div className="space-y-4">
            <div className="flex flex-wrap items-center gap-2">
              <Badge tone={data.ai_enabled ? "success" : "neutral"}>{data.ai_enabled ? "AI on" : "No AI key: rules only"}</Badge>
              {data.exhausted && <Badge tone="danger">Cap reached: AI paused</Badge>}
              <span className="text-xs text-fg-3 tnum">{data.calls_today} calls today</span>
            </div>
            <div className="grid gap-4 sm:grid-cols-2">
              <Spend label="Today" spent={data.day_spent} cap={data.day_cap} left={data.day_left} />
              <Spend label="This month" spent={data.month_spent} cap={data.month_cap} left={data.month_left} />
            </div>
            <p className="text-[11px] text-fg-3">
              Costs use the token prices set in the server configuration (assumed, not read from the provider). Models: {data.models.strong} for
              decisions, {data.models.cheap} for volume.
            </p>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
