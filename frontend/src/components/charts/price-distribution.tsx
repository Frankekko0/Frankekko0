"use client";

import { useEffect, useId, useRef, useState } from "react";
import { eur } from "@/lib/format";

interface Bin {
  start: number;
  end: number;
  count: number;
}

/**
 * Histogram of comparable prices (condition-adjusted) with the listing price and the market
 * median marked. Single series -> no legend box; reference lines are labelled directly.
 * Columns: <=24px, 4px rounded data end, 2px surface gap, hairline grid; per-bar hover tooltip.
 */
export function PriceDistribution({
  bins,
  listingPrice,
  median,
  p25,
  p75,
  height = 200,
}: {
  bins: Bin[];
  listingPrice: number;
  median: number | null;
  p25: number | null;
  p75: number | null;
  height?: number;
}) {
  const id = useId();
  const [hover, setHover] = useState<number | null>(null);
  const ref = useRef<HTMLElement>(null);
  const [width, setWidth] = useState(640);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const ro = new ResizeObserver(([entry]) => entry && setWidth(Math.max(240, Math.round(entry.contentRect.width))));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  if (!bins.length) return null;
  // Drawn at the real pixel width (no viewBox scaling) so labels stay legible on phones.
  const W = width;
  const H = height;
  const pad = { l: 28, r: 12, t: 40, b: 26 };
  const lo = Math.min(bins[0]!.start, listingPrice) * 0.95;
  const hi = Math.max(bins[bins.length - 1]!.end, listingPrice) * 1.03;
  const x = (v: number) => pad.l + ((v - lo) / (hi - lo)) * (W - pad.l - pad.r);
  const maxCount = Math.max(...bins.map((b) => b.count), 1);
  const y = (c: number) => pad.t + (1 - c / maxCount) * (H - pad.t - pad.b);
  const baseY = H - pad.b;
  const ticks = [0, Math.ceil(maxCount / 2), maxCount];
  const nTicks = W < 420 ? 3 : 5;
  const xTicks = Array.from({ length: nTicks }, (_, i) => lo + ((hi - lo) * i) / (nTicks - 1));
  // Direct labels for the two reference lines; stacked on two rows when they would collide.
  const textW = (t: string) => t.length * 6.3;
  const placeLabel = (cx: number, t: string) => Math.min(Math.max(cx - textW(t) / 2, 0), W - textW(t));
  const listingText = `This listing ${eur(listingPrice)}`;
  const medianText = median !== null ? `Median ${eur(median)}` : "";
  const listingX = placeLabel(x(listingPrice), listingText);
  let medianX = median !== null ? placeLabel(x(median), medianText) : 0;
  const collide =
    median !== null && listingX < medianX + textW(medianText) + 8 && medianX < listingX + textW(listingText) + 8;
  if (collide && median !== null) {
    // Second row: keep the median label clear of the listing's reference line.
    const lx = x(listingPrice);
    const shifted = listingPrice < median ? Math.max(lx + 6, medianX) : Math.min(lx - 6 - textW(medianText), medianX);
    medianX = Math.min(Math.max(shifted, 0), W - textW(medianText));
  }

  return (
    <figure ref={ref} className="relative">
      <svg width={W} height={H} viewBox={`0 0 ${W} ${H}`} className="block max-w-full overflow-visible" role="img" aria-labelledby={`${id}-t`}>
        <title id={`${id}-t`}>
          {`Distribuzione prezzi dei comparabili. Prezzo annuncio ${eur(listingPrice)}${median ? `, mediana ${eur(median)}` : ""}.`}
        </title>
        {ticks.map((t) => (
          <g key={t}>
            <line x1={pad.l} x2={W - pad.r} y1={y(t)} y2={y(t)} stroke="var(--chart-grid)" strokeWidth={1} />
            <text x={pad.l - 6} y={y(t) + 3} textAnchor="end" fontSize={10} fill="var(--chart-muted)" className="tnum">
              {t}
            </text>
          </g>
        ))}
        {p25 !== null && p75 !== null && (
          <rect x={x(p25)} y={pad.t} width={Math.max(0, x(p75) - x(p25))} height={baseY - pad.t} fill="var(--series-1)" opacity={0.07} />
        )}
        {bins.map((b, i) => {
          const x0 = x(b.start) + 1;
          const w = Math.max(2, Math.min(24, x(b.end) - x(b.start) - 2));
          const cx = x0 + (x(b.end) - x(b.start) - 2 - w) / 2;
          const top = y(b.count);
          const h = baseY - top;
          const r = Math.min(4, h, w / 2);
          if (b.count === 0) return null;
          return (
            <g key={i} onPointerEnter={() => setHover(i)} onPointerLeave={() => setHover(null)}>
              <rect x={x(b.start)} y={pad.t} width={x(b.end) - x(b.start)} height={baseY - pad.t} fill="transparent" />
              <path
                d={`M${cx},${baseY} V${top + r} Q${cx},${top} ${cx + r},${top} H${cx + w - r} Q${cx + w},${top} ${cx + w},${top + r} V${baseY} Z`}
                fill="var(--series-1)"
                opacity={hover === null || hover === i ? 0.9 : 0.45}
                className="transition-opacity duration-150"
                style={{
                  transformBox: "fill-box",
                  transformOrigin: "50% 100%",
                  animation: `grow-y 0.6s var(--ease-out) ${0.15 + i * 0.035}s backwards`,
                }}
              />
            </g>
          );
        })}
        <line x1={pad.l} x2={W - pad.r} y1={baseY} y2={baseY} stroke="var(--chart-axis)" strokeWidth={1} />
        {xTicks.map((t, i) => (
          <text key={i} x={x(t)} y={H - 8} textAnchor="middle" fontSize={10} fill="var(--chart-muted)" className="tnum">
            {eur(Math.round(t))}
          </text>
        ))}
        {median !== null && (
          <g>
            <line x1={x(median)} x2={x(median)} y1={collide ? pad.t - 4 : 20} y2={baseY} stroke="var(--text-2)" strokeWidth={1.5} />
            <text x={medianX} y={collide ? pad.t - 8 : 14} fontSize={11} fontWeight={600} fill="var(--text-2)">
              {medianText}
            </text>
          </g>
        )}
        <g>
          <line x1={x(listingPrice)} x2={x(listingPrice)} y1={20} y2={baseY} stroke="var(--ultra)" strokeWidth={2} />
          <circle cx={x(listingPrice)} cy={baseY} r={4} fill="var(--ultra)" stroke="var(--chart-surface)" strokeWidth={2} />
          <text x={listingX} y={14} fontSize={11} fontWeight={600} fill="var(--text)">
            {listingText}
          </text>
        </g>
      </svg>
      {hover !== null && bins[hover] && (
        <div
          className="pointer-events-none absolute z-10 -translate-x-1/2 rounded-lg border border-line bg-surface px-2.5 py-1.5 text-xs shadow-pop"
          style={{ left: Math.min(Math.max(x((bins[hover]!.start + bins[hover]!.end) / 2), 60), W - 60), top: 0 }}
        >
          <p className="font-semibold text-fg tnum">{bins[hover]!.count} comparables</p>
          <p className="text-fg-3 tnum">
            {eur(bins[hover]!.start)} – {eur(bins[hover]!.end)}
          </p>
        </div>
      )}
      <figcaption className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-fg-3">
        <span className="inline-flex items-center gap-1.5">
          <span className="h-2 w-3 rounded-sm bg-[var(--series-1)] opacity-20" /> Typical range (P25–P75)
        </span>
        <span>Prices adjusted to this item&apos;s condition · outliers excluded</span>
      </figcaption>
    </figure>
  );
}
