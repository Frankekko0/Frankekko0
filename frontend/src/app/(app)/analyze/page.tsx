"use client";

import dynamic from "next/dynamic";
import { Skeleton } from "@/components/ui/feedback";

// Client-only: the view reads a one-click import from the URL fragment on its first render.
const AnalyzeView = dynamic(() => import("@/components/analyze/analyze-view"), {
  ssr: false,
  loading: () => (
    <div className="space-y-6">
      <Skeleton className="h-16 max-w-xl" />
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-[minmax(0,1fr)_400px]">
        <Skeleton className="h-[560px] rounded-2xl" />
        <Skeleton className="h-72 rounded-2xl" />
      </div>
    </div>
  ),
});

export default function AnalyzePage() {
  return <AnalyzeView />;
}
