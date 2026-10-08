"use client";

import { ImageOff } from "lucide-react";
import { useState } from "react";
import { cn } from "@/lib/utils";

/** `eager`: the image is likely the largest one on first paint (top of a list), so fetch it first. */
export function ListingImage({ src, alt, className, eager = false }: { src: string | null; alt: string; className?: string; eager?: boolean }) {
  const [failed, setFailed] = useState(false);
  if (!src || failed) {
    return (
      <div className={cn("flex items-center justify-center bg-surface-2 text-fg-3", className)}>
        <ImageOff className="size-6" aria-hidden />
        {alt && <span className="sr-only">{alt}</span>}
      </div>
    );
  }
  return (
    <img
      src={src}
      alt={alt}
      loading={eager ? "eager" : "lazy"}
      fetchPriority={eager ? "high" : "auto"}
      decoding="async"
      referrerPolicy="no-referrer"
      onError={() => setFailed(true)}
      className={cn("bg-surface-2 object-cover", className)}
    />
  );
}
