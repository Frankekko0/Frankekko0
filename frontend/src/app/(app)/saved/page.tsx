"use client";

import { Bookmark } from "lucide-react";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense } from "react";
import { DealsExplorer } from "@/components/deal/explorer";
import { EmptyState } from "@/components/ui/feedback";
import { Segmented } from "@/components/ui/misc";
import type { FavoriteState } from "@/lib/types";

const STATES: { id: FavoriteState; label: string }[] = [
  { id: "saved", label: "Saved" },
  { id: "watching", label: "Watching" },
  { id: "purchased", label: "Purchased" },
  { id: "sold", label: "Sold" },
  { id: "ignored", label: "Ignored" },
];

function SavedInner() {
  const params = useSearchParams();
  const router = useRouter();
  const state = (params.get("tab") as FavoriteState) || "saved";
  return (
    <div className="space-y-4">
      <Segmented
        label="Show deals"
        value={state}
        onValueChange={(v) => router.replace(`/saved?tab=${v}`)}
        options={STATES.map((s) => ({ value: s.id, label: s.label }))}
      />
      <DealsExplorer
        key={state}
        title="Your deals"
        description="Deals you saved, are watching, bought, sold or hidden."
        fixed={{ state, include_inactive: true }}
        showPresets={false}
        empty={<EmptyState icon={<Bookmark />} title="Nothing here yet" description="Use the bookmark on any deal to save it, or mark it as watching / purchased from its analysis page." />}
      />
    </div>
  );
}

export default function SavedPage() {
  return (
    <Suspense>
      <SavedInner />
    </Suspense>
  );
}
