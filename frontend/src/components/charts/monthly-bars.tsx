"use client";

import { Bar, BarChart, CartesianGrid, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { eur } from "@/lib/format";

/** Monthly realized profit: single series columns (<=24px, 4px rounded data end), per-bar tooltip. */
export function MonthlyProfitChart({ data }: { data: { month: string; profit: number; sales: number }[] }) {
  const rows = data.map((d) => ({
    ...d,
    label: new Date(`${d.month}-01T00:00:00`).toLocaleDateString("en-GB", { month: "short", year: "2-digit" }),
  }));
  return (
    <div className="h-56 w-full">
      <ResponsiveContainer>
        <BarChart data={rows} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
          <CartesianGrid vertical={false} stroke="var(--chart-grid)" />
          <XAxis dataKey="label" tick={{ fontSize: 11, fill: "var(--chart-muted)" }} axisLine={{ stroke: "var(--chart-axis)" }} tickLine={false} />
          <YAxis width={52} tickFormatter={(v) => eur(v)} tick={{ fontSize: 11, fill: "var(--chart-muted)" }} axisLine={false} tickLine={false} />
          <Tooltip
            cursor={{ fill: "var(--surface-2)" }}
            contentStyle={{ background: "var(--surface)", border: "1px solid var(--border)", borderRadius: 10, fontSize: 12 }}
            formatter={(v, _n, item) => [`${eur(Number(v))} · ${(item.payload as { sales: number }).sales} sales`, "Profit"]}
          />
          <Bar dataKey="profit" maxBarSize={24} radius={[4, 4, 0, 0]} isAnimationActive={false}>
            {rows.map((r) => (
              <Cell key={r.month} fill={r.profit >= 0 ? "var(--series-1)" : "var(--danger)"} />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
