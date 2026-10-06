"use client";

import dynamic from "next/dynamic";
import { Skeleton } from "@/components/ui/feedback";

// Client-only: the view reads a batch import from the URL fragment on its first render.
const ImportView = dynamic(() => import("@/components/import/import-view"), {
  ssr: false,
  loading: () => (
    <div className="space-y-6">
      <Skeleton className="h-16 max-w-xl" />
      <Skeleton className="h-64 rounded-2xl" />
    </div>
  ),
});

export default function ImportPage() {
  return <ImportView />;
}
