"use client";

import { Dialog as RDialog } from "radix-ui";
import { ChevronLeft, ChevronRight, ImageOff, Maximize2, X } from "lucide-react";
import { useRef, useState, type KeyboardEvent, type PointerEvent } from "react";
import { cn } from "@/lib/utils";

export interface GalleryImage {
  /** Preferred source (the internal copy when archived). */
  src: string;
  /** Used when the preferred source fails (the original link). */
  fallback?: string | null;
  archived?: boolean;
}

/** One image with fallback, then a placeholder: a broken link never breaks the page. */
function Img({ image, alt, className, eager }: { image: GalleryImage; alt: string; className?: string; eager?: boolean }) {
  const [stage, setStage] = useState<0 | 1 | 2>(0);
  const src = stage === 0 ? image.src : stage === 1 ? image.fallback : null;
  if (!src) {
    return (
      <div className={cn("flex flex-col items-center justify-center gap-1.5 bg-surface-2 text-fg-3", className)} role="img" aria-label={`${alt} (not available)`}>
        <ImageOff className="size-6" aria-hidden />
        <span className="text-[11px]">Image not available</span>
      </div>
    );
  }
  return (
    <img
      key={src}
      src={src}
      alt={alt}
      loading={eager ? "eager" : "lazy"}
      decoding="async"
      referrerPolicy="no-referrer"
      draggable={false}
      onError={() => setStage((s) => (s === 0 && image.fallback ? 1 : 2))}
      className={className}
    />
  );
}

function useSwipe(onPrev: () => void, onNext: () => void) {
  const start = useRef<{ x: number; y: number } | null>(null);
  return {
    onPointerDown: (e: PointerEvent) => {
      start.current = { x: e.clientX, y: e.clientY };
    },
    onPointerUp: (e: PointerEvent) => {
      const s = start.current;
      start.current = null;
      if (!s) return;
      const dx = e.clientX - s.x;
      if (Math.abs(dx) > 40 && Math.abs(dx) > Math.abs(e.clientY - s.y)) (dx < 0 ? onNext : onPrev)();
    },
    onPointerCancel: () => {
      start.current = null;
    },
  };
}

export function Gallery({ images, alt }: { images: GalleryImage[]; alt: string }) {
  const [index, setIndex] = useState(0);
  const [open, setOpen] = useState(false);
  const thumbs = useRef<HTMLDivElement>(null);
  const n = images.length;
  const go = (i: number) => {
    const next = (i + n) % n;
    setIndex(next);
    const el = thumbs.current?.children[next] as HTMLElement | undefined;
    el?.scrollIntoView({ block: "nearest", inline: "nearest", behavior: "smooth" });
  };
  const prev = () => go(index - 1);
  const next = () => go(index + 1);
  const swipe = useSwipe(prev, next);
  const onKey = (e: KeyboardEvent) => {
    if (e.key === "ArrowLeft") {
      e.preventDefault();
      prev();
    } else if (e.key === "ArrowRight") {
      e.preventDefault();
      next();
    } else if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      setOpen(true);
    }
  };

  if (n === 0) {
    return (
      <div className="flex aspect-[4/5] flex-col items-center justify-center gap-2 rounded-2xl bg-surface-2 text-fg-3">
        <ImageOff className="size-8" aria-hidden />
        <p className="text-[13px]">No photos captured for this listing</p>
      </div>
    );
  }
  const current = images[index]!;

  return (
    <div className="space-y-2">
      <div
        className="group relative aspect-[4/5] touch-pan-y select-none overflow-hidden rounded-2xl bg-surface-2 outline-none focus-visible:ring-4 focus-visible:ring-[var(--ring)]"
        tabIndex={0}
        role="group"
        aria-roledescription="carousel"
        aria-label={`${alt}: photo ${index + 1} of ${n}. Use the arrow keys to browse, Enter for full screen.`}
        onKeyDown={onKey}
        {...swipe}
      >
        <button type="button" className="absolute inset-0 cursor-zoom-in" onClick={() => setOpen(true)} aria-label="Open full screen">
          <Img image={current} alt={`${alt} — ${index + 1}/${n}`} className="h-full w-full object-contain" eager />
        </button>
        {n > 1 && (
          <>
            <NavButton side="left" onClick={prev} />
            <NavButton side="right" onClick={next} />
          </>
        )}
        <span className="pointer-events-none absolute bottom-2.5 left-2.5 rounded-full bg-black/60 px-2 py-0.5 text-[11px] font-medium text-white tnum backdrop-blur">
          {index + 1} / {n}
        </span>
        <span className="pointer-events-none absolute bottom-2.5 right-2.5 flex items-center gap-1 rounded-full bg-black/60 px-2 py-0.5 text-[11px] text-white backdrop-blur">
          <Maximize2 className="size-3" aria-hidden /> {current.archived ? "Saved copy" : "Original link"}
        </span>
      </div>
      {n > 1 && (
        <div ref={thumbs} className="scrollbar-none flex gap-2 overflow-x-auto pb-1" role="tablist" aria-label="Photos">
          {images.map((img, i) => (
            <button
              key={img.src + i}
              type="button"
              role="tab"
              aria-selected={i === index}
              aria-label={`Photo ${i + 1}`}
              onClick={() => go(i)}
              className={cn(
                "size-16 shrink-0 overflow-hidden rounded-lg ring-2 ring-offset-2 ring-offset-bg transition-[box-shadow,opacity]",
                i === index ? "ring-accent" : "ring-transparent opacity-70 hover:opacity-100",
              )}
            >
              <Img image={img} alt="" className="h-full w-full object-cover" />
            </button>
          ))}
        </div>
      )}

      <RDialog.Root open={open} onOpenChange={setOpen}>
        <RDialog.Portal>
          <RDialog.Overlay className="overlay-anim fixed inset-0 z-50 bg-black/90" />
          <RDialog.Content
            className="fixed inset-0 z-50 flex flex-col outline-none"
            onKeyDown={(e) => {
              if (e.key === "ArrowLeft") prev();
              if (e.key === "ArrowRight") next();
            }}
          >
            <RDialog.Title className="sr-only">{alt}</RDialog.Title>
            <RDialog.Description className="sr-only">
              Photo {index + 1} of {n}. Arrow keys or swipe to browse, Escape to close.
            </RDialog.Description>
            <div className="flex items-center justify-between px-4 py-3 text-white">
              <span className="text-[13px] tnum">
                {index + 1} / {n}
              </span>
              <RDialog.Close className="rounded-full p-2 hover:bg-white/10" aria-label="Close">
                <X className="size-5" />
              </RDialog.Close>
            </div>
            <div className="relative min-h-0 flex-1 touch-pan-y select-none" {...swipe}>
              <Img image={current} alt={`${alt} — ${index + 1}/${n}`} className="h-full w-full object-contain" eager />
              {n > 1 && (
                <>
                  <NavButton side="left" onClick={prev} dark />
                  <NavButton side="right" onClick={next} dark />
                </>
              )}
            </div>
            {n > 1 && (
              <div className="scrollbar-none flex justify-center gap-2 overflow-x-auto px-4 py-3">
                {images.map((img, i) => (
                  <button
                    key={img.src + i}
                    type="button"
                    aria-label={`Photo ${i + 1}`}
                    aria-current={i === index}
                    onClick={() => setIndex(i)}
                    className={cn("size-12 shrink-0 overflow-hidden rounded-md", i === index ? "ring-2 ring-white" : "opacity-50 hover:opacity-90")}
                  >
                    <Img image={img} alt="" className="h-full w-full object-cover" />
                  </button>
                ))}
              </div>
            )}
          </RDialog.Content>
        </RDialog.Portal>
      </RDialog.Root>
    </div>
  );
}

function NavButton({ side, onClick, dark }: { side: "left" | "right"; onClick: () => void; dark?: boolean }) {
  const Icon = side === "left" ? ChevronLeft : ChevronRight;
  return (
    <button
      type="button"
      onClick={(e) => {
        e.stopPropagation();
        onClick();
      }}
      aria-label={side === "left" ? "Previous photo" : "Next photo"}
      className={cn(
        "absolute top-1/2 z-10 flex size-10 -translate-y-1/2 items-center justify-center rounded-full shadow-card transition-opacity",
        side === "left" ? "left-2.5" : "right-2.5",
        dark ? "bg-white/15 text-white hover:bg-white/25" : "bg-surface/90 text-fg opacity-0 group-hover:opacity-100 focus-visible:opacity-100 max-sm:opacity-100",
      )}
    >
      <Icon className="size-5" />
    </button>
  );
}
