"use client";

import { use } from "react";
import { TrackingView } from "@/components/tracking/tracking-view";

/** Tracking page of one listing. ``ref`` is the internal id or the Vinted ID. */
export default function ItemPage({ params }: { params: Promise<{ ref: string }> }) {
  const { ref } = use(params);
  return <TrackingView refId={decodeURIComponent(ref)} />;
}
