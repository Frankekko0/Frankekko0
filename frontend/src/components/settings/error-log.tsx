"use client";

import { useQuery } from "@tanstack/react-query";
import { ScrollText } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/feedback";
import { api } from "@/lib/api";
import { timeAgo } from "@/lib/format";

interface ErrorEntry {
  at: string | null;
  level: string | null;
  process: string;
  event: string | null;
  detail: Record<string, unknown>;
}

/** Latest warnings and errors of the server and the background worker (sensitive values hidden). */
export function ErrorLogCard() {
  const q = useQuery({
    queryKey: ["system", "errors"],
    queryFn: () => api<{ enabled: boolean; items: ErrorEntry[] }>("/system/errors?limit=50"),
    refetchInterval: 60_000,
  });
  const d = q.data;
  return (
    <Card className="reveal">
      <CardHeader>
        <div>
          <CardTitle className="flex items-center gap-2">
            <ScrollText className="size-4 text-fg-3" /> Error log
          </CardTitle>
          <CardDescription>Warnings and errors of the server and the background jobs. Keys, tokens and passwords are never written.</CardDescription>
        </div>
      </CardHeader>
      <CardContent>
        {q.isLoading ? (
          <Skeleton className="h-24" />
        ) : !d?.enabled ? (
          <p className="text-[13px] text-fg-2">Not enabled on this installation (set ERROR_LOG_DIR; the production setup does).</p>
        ) : d.items.length === 0 ? (
          <p className="text-[13px] text-fg-2">No warnings or errors recorded.</p>
        ) : (
          <ul className="divide-y divide-line">
            {d.items.map((e, i) => (
              <li key={`${e.at}-${i}`} className="flex flex-wrap items-baseline gap-x-3 gap-y-1 py-2 text-[13px]">
                <Badge tone={e.level === "error" || e.level === "critical" ? "danger" : "warning"}>{e.level}</Badge>
                <span className="font-medium text-fg">{e.event}</span>
                <span className="text-fg-3">
                  {e.process} · {e.at ? timeAgo(e.at) : "—"}
                </span>
                {Object.keys(e.detail).length > 0 && (
                  <code className="w-full truncate text-[12px] text-fg-3">{JSON.stringify(e.detail)}</code>
                )}
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}
