"use client";

import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { eur, shortDate } from "@/lib/format";

/** Price history: single 2px step line, hairline grid, crosshair tooltip. */
export function PriceHistoryChart({ points }: { points: { price: number; observed_at: string }[] }) {
  const data = [...points, { price: points[points.length - 1]!.price, observed_at: new Date().toISOString() }].map((p) => ({
    t: new Date(p.observed_at).getTime(),
    price: p.price,
  }));
  return (
    <div className="h-44 w-full">
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
            tickFormatter={(v) => eur(v)}
            tick={{ fontSize: 11, fill: "var(--chart-muted)" }}
            axisLine={false}
            tickLine={false}
            domain={["auto", "auto"]}
          />
          <Tooltip
            cursor={{ stroke: "var(--chart-axis)" }}
            contentStyle={{ background: "var(--surface)", border: "1px solid var(--border)", borderRadius: 10, fontSize: 12 }}
            labelFormatter={(t) => shortDate(new Date(Number(t)).toISOString())}
            formatter={(v) => [eur(Number(v)), "Price"]}
          />
          <Line type="stepAfter" dataKey="price" stroke="var(--series-1)" strokeWidth={2} dot={{ r: 4, fill: "var(--series-1)", stroke: "var(--chart-surface)", strokeWidth: 2 }} isAnimationActive={false} />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
