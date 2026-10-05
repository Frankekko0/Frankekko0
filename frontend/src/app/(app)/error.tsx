"use client";

import { ErrorState } from "@/components/ui/feedback";

export default function AppError({ reset }: { error: Error; reset: () => void }) {
  return (
    <div className="py-10">
      <ErrorState message="Non è stato possibile caricare questa pagina. Il problema è stato registrato." onRetry={reset} />
    </div>
  );
}
