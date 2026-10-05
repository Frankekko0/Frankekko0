import { Suspense } from "react";
import { DealsExplorer } from "@/components/deal/explorer";

export const metadata = { title: "Deals" };

export default function DealsPage() {
  return (
    <Suspense>
      <DealsExplorer title="Deals" description="Every analysed listing, filtered by your rules and costs." />
    </Suspense>
  );
}
