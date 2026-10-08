"use client";

import dynamic from "next/dynamic";
import { Skeleton } from "@/components/ui/feedback";

// The chart library is the largest dependency of the app: pages fetch it only when a chart is
// rendered, after the rest of the page is interactive. Placeholders keep each chart's height.

export const PriceHistoryChart = dynamic(() => import("./history-line").then((m) => m.PriceHistoryChart), {
  ssr: false,
  loading: () => <Skeleton className="h-44 w-full rounded-xl" />,
});

export const MonthlyProfitChart = dynamic(() => import("./monthly-bars").then((m) => m.MonthlyProfitChart), {
  ssr: false,
  loading: () => <Skeleton className="h-56 w-full rounded-xl" />,
});

export const SnapshotChart = dynamic(() => import("./snapshot-chart").then((m) => m.SnapshotChart), {
  ssr: false,
  loading: () => <Skeleton className="h-40 w-full rounded-xl" />,
});
