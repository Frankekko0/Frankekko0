"use client";

import { useEffect, useRef, useState, useSyncExternalStore } from "react";

const REDUCED = "(prefers-reduced-motion: reduce)";

function subscribeReduced(onChange: () => void) {
  const mq = window.matchMedia(REDUCED);
  mq.addEventListener("change", onChange);
  return () => mq.removeEventListener("change", onChange);
}

export function usePrefersReducedMotion(): boolean {
  return useSyncExternalStore(
    subscribeReduced,
    () => window.matchMedia(REDUCED).matches,
    () => true, // server: render final values, never a half-animated number
  );
}

const easeOutQuart = (t: number) => 1 - (1 - t) ** 4;

/**
 * Counts from the previous value to `value` (from 0 on first mount). KPIs are seen once per
 * visit, so the motion draws the eye to them without slowing anything down.
 */
export function AnimatedNumber({
  value,
  format = (v) => Math.round(v).toLocaleString("en-US"),
  duration = 900,
  className,
}: {
  value: number;
  format?: (v: number) => string;
  duration?: number;
  className?: string;
}) {
  const reduced = usePrefersReducedMotion();
  const [display, setDisplay] = useState(0);
  const from = useRef(0);
  const frame = useRef<number | null>(null);

  useEffect(() => {
    if (reduced) return;
    const start = performance.now();
    const origin = from.current;
    const tick = (now: number) => {
      const t = Math.min(1, (now - start) / duration);
      const current = origin + (value - origin) * easeOutQuart(t);
      setDisplay(current);
      from.current = current;
      if (t < 1) frame.current = requestAnimationFrame(tick);
    };
    frame.current = requestAnimationFrame(tick);
    return () => {
      if (frame.current !== null) cancelAnimationFrame(frame.current);
    };
  }, [value, duration, reduced]);

  return (
    <span className={className}>
      <span className="sr-only">{format(value)}</span>
      <span aria-hidden>{format(reduced ? value : display)}</span>
    </span>
  );
}
