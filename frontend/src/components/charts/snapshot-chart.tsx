"use client";

import { useState } from "react";

import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { shortDate } from "@/lib/format";

export interface SeriesPoint {
  t: number;
  value: number;
}

/**
 * One measure over time (one axis, one series): price as a step line (a price holds until it
 * changes), favourites as a line. The hover tooltip shows the exact value and date.
 */
export function SnapshotChart({
  points,
  label,
  format,
  color = "var(--series-1)",
  step = false,
  extendToNow = false,
}: {
  points: SeriesPoint[];
  label: string;
  format: (v: number) => string;
  color?: string;
  step?: boolean;
  extendToNow?: boolean;
}) {
  const [now] = useState(() => Date.now());
  const data = extendToNow && points.length ? [...points, { t: Math.max(now, points[points.length - 1]!.t), value: points[points.length - 1]!.value }] : points;
  return (
    <div className="h-40 w-full" role="img" aria-label={`${label}: ${points.map((p) => `${shortDate(new Date(p.t).toISOString())} ${format(p.value)}`).join(", ")}`}>
      <ResponsiveContainer>
        <LineChart data={data} margin={{ top: 8, right: 12, bottom: 0, left: 0 }}>
          <CartesianGrid vertical={false} stroke="var(--chart-grid)" />
          <XAxis
            dataKey="t"
            type="number"
            domain={["dataMin", "dataMax"]}
            scale="time"
            tickFormatter={(t) => new Date(t).toLocaleDateString("en-GB", { day: "numeric", month: "short" })}
            tick={{ fontSize: 11, fill: "var(--chart-muted)" }}
            axisLine={{ stroke: "var(--chart-axis)" }}
            tickLine={false}
            minTickGap={32}
          />
          <YAxis
            width={48}
            tickFormatter={(v) => format(Number(v))}
            tick={{ fontSize: 11, fill: "var(--chart-muted)" }}
            axisLine={false}
            tickLine={false}
            domain={["auto", "auto"]}
            allowDecimals={false}
          />
          <Tooltip
            cursor={{ stroke: "var(--chart-axis)" }}
            contentStyle={{ background: "var(--surface)", border: "1px solid var(--border)", borderRadius: 10, fontSize: 12 }}
            labelFormatter={(t) => new Date(Number(t)).toLocaleString("en-GB", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" })}
            formatter={(v) => [format(Number(v)), label]}
          />
          <Line
            type={step ? "stepAfter" : "monotone"}
            dataKey="value"
            stroke={color}
            strokeWidth={2}
            dot={{ r: 4, fill: color, stroke: "var(--chart-surface)", strokeWidth: 2 }}
            activeDot={{ r: 5 }}
            isAnimationActive={false}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
