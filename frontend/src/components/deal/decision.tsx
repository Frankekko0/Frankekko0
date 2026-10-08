"use client";

import { Check, CircleHelp, CircleMinus, CirclePlus, Copy, Fingerprint, Gauge, Lightbulb, ShieldAlert, ShieldCheck, ShieldQuestion } from "lucide-react";
import { useState, type ReactNode } from "react";
import { eur, pct, plural } from "@/lib/format";
import { DAYS_BASIS_SHORT, shortBasis } from "@/lib/provenance";
import type { AuthEvidence, AuthVerdict, DealInsights, OpportunityDetail, Provenance } from "@/lib/types";
import { cn } from "@/lib/utils";
import { Badge, type BadgeTone } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { ListingImage } from "./listing-image";
import { Meter } from "./score";

function Section({ id, title, description, icon, children }: { id: string; title: string; description?: string; icon: ReactNode; children: ReactNode }) {
  return (
    <Card id={id} className="reveal scroll-mt-28">
      <CardHeader>
        <div className="min-w-0">
          <CardTitle className="flex items-center gap-2 [&_svg]:size-4 [&_svg]:text-fg-3">
            {icon}
            {title}
          </CardTitle>
          {description && <CardDescription>{description}</CardDescription>}
        </div>
      </CardHeader>
      <CardContent>{children}</CardContent>
    </Card>
  );
}

/** Confidence of one estimate; below 40 the figure is shown as indicative. */
function Conf({ value }: { value: number }) {
  const tone: BadgeTone = value >= 70 ? "success" : value >= 40 ? "warning" : "danger";
  return (
    <Badge tone={tone} className="tnum" title="Confidence of this estimate">
      {value}%
    </Badge>
  );
}

function Figure({ label, value, conf, sub, tone }: { label: string; value: string; conf?: number; sub?: ReactNode; tone?: "success" | "danger" }) {
  return (
    <div className="min-w-0 rounded-xl border border-line p-3">
      <div className="flex items-center justify-between gap-2">
        <p className="text-[11px] font-semibold uppercase tracking-[0.08em] text-fg-3">{label}</p>
        {conf !== undefined && <Conf value={conf} />}
      </div>
      <p className={cn("mt-1 text-[18px] font-semibold tracking-tight tnum", tone === "success" ? "text-success" : tone === "danger" ? "text-danger" : "text-fg")}>{value}</p>
      {sub && <p className="text-[11px] text-fg-3">{sub}</p>}
    </div>
  );
}

const INSUFFICIENT = "insufficient data";

/** A short "where it comes from" next to a figure, linking to the Sources section. */
function SourceHint({ children }: { children: ReactNode }) {
  return (
    <a href="#sources" className="underline decoration-line-strong underline-offset-2 transition-colors hover:text-fg hover:decoration-current">
      {children}
    </a>
  );
}

export function DecisionSection({ d, provenance }: { d: OpportunityDetail; provenance?: Provenance | null }) {
  const i = d.insights;
  if (!i) return null;
  const r = i.resale;
  const rap = i.risk_adjusted_profit;
  const basis = provenance ? shortBasis(provenance) : null;
  const daysBasis = provenance ? DAYS_BASIS_SHORT[provenance.days_to_sell.basis] : undefined;
  const daysN = provenance?.days_to_sell.n ?? 0;
  return (
    <Section
      id="decision"
      icon={<Gauge />}
      title="Decision"
      description="Ranked by risk-adjusted profit: net margin × probability of selling within 30 days × probability of authenticity."
    >
      <div className="rounded-xl bg-surface-2 p-4">
        <p className="text-[11px] font-semibold uppercase tracking-[0.08em] text-fg-3">Risk-adjusted expected profit</p>
        <p className={cn("text-[28px] font-semibold tracking-tight tnum", rap === null ? "text-fg-3" : rap > 0 ? "text-success" : "text-danger")}>
          {rap === null ? INSUFFICIENT : eur(rap, { sign: true })}
        </p>
        <p className="text-xs text-fg-3 tnum">
          {eur(i.net_margin, { sign: true })} × {i.p_sale.p !== null ? pct(i.p_sale.p) : "?"} sale × {pct(i.authenticity.p_authentic)} authentic
        </p>
        <ul className="mt-3 space-y-1.5 text-[14px] leading-relaxed text-fg">
          {i.reason.map((line) => (
            <li key={line}>{line}</li>
          ))}
        </ul>
      </div>

      <div className="mt-4 grid grid-cols-2 gap-x-6 gap-y-3 sm:grid-cols-4">
        {i.pillars.map((p) => (
          <div key={p.key}>
            <div className="flex justify-between text-[13px]">
              <span className="text-fg-2">{p.label}</span>
              <span className="font-semibold tnum">{p.score}</span>
            </div>
            <Meter value={p.score} className="mt-1" />
          </div>
        ))}
      </div>

      <div className="mt-4 grid grid-cols-2 gap-2 sm:grid-cols-3">
        <Figure label="Resale min" value={eur(r.low)} conf={r.confidence} sub={r.calibrated ? "calibrated on real sales" : undefined} />
        <Figure label="Resale probable" value={eur(r.probable)} conf={r.confidence} sub={basis ? <SourceHint>from {basis}</SourceHint> : undefined} />
        <Figure label="Resale max" value={eur(r.high)} conf={r.confidence} />
        <Figure
          label="Net margin"
          value={eur(i.net_margin, { sign: true })}
          conf={i.margin_confidence}
          tone={i.net_margin === null ? undefined : i.net_margin > 0 ? "success" : "danger"}
          sub="after buyer protection and shipping"
        />
        <Figure
          label="Time to sell"
          value={i.days_to_sell !== null ? `~${Math.round(i.days_to_sell)} days` : INSUFFICIENT}
          conf={i.days_confidence}
          sub={daysBasis ? <SourceHint>{daysBasis === DAYS_BASIS_SHORT.sold && daysN ? plural(daysN, "similar item") + " sold" : daysBasis}</SourceHint> : undefined}
        />
        <Figure
          label="Sold within 30 days"
          value={i.p_sale.p !== null ? pct(i.p_sale.p) : INSUFFICIENT}
          conf={i.p_sale.confidence}
          sub={i.p_sale.p === null ? i.p_sale.reason : i.p_sale.source === "segment" ? "segment share (few similar items)" : `${i.p_sale.n} similar listings with known outcome`}
        />
        <Figure label="Max price" value={eur(i.max_price)} conf={i.offer_confidence} sub="above this the margin target is lost" />
        <Figure label="Suggested offer" value={eur(i.suggested_offer)} conf={i.offer_confidence} />
        <Figure label="Comparables" value={i.comparables_rule === "sold_only" ? "sold only" : "sold + on sale"} sub={i.comparables_rule === "sold_only" ? "asking prices shown, not used" : "few sales: asking prices discounted"} />
      </div>
    </Section>
  );
}

const VERDICT: Record<AuthVerdict, { tone: BadgeTone; icon: ReactNode }> = {
  probably_authentic: { tone: "success", icon: <ShieldCheck /> },
  uncertain: { tone: "warning", icon: <ShieldQuestion /> },
  counterfeit_risk: { tone: "danger", icon: <ShieldAlert /> },
  not_verifiable: { tone: "neutral", icon: <ShieldQuestion /> },
};

function EvidenceIcon({ e }: { e: AuthEvidence }) {
  if (e.direction === "+") return <CirclePlus className="size-4 shrink-0 text-success" />;
  if (e.direction === "-") return <CircleMinus className="size-4 shrink-0 text-danger" />;
  return <CircleHelp className="size-4 shrink-0 text-fg-3" />;
}

/** One photo with the detail the evidence refers to outlined. */
function PhotoProof({ url, e }: { url: string; e: AuthEvidence }) {
  const [x = 0, y = 0, w = 0, h = 0] = e.box ?? [];
  return (
    <figure className="w-36 shrink-0">
      <div className="relative aspect-square overflow-hidden rounded-lg border border-line bg-surface-2">
        <ListingImage src={url} alt="" className="h-full w-full" />
        {e.box && (
          <span
            className={cn("absolute rounded-sm border-2", e.direction === "-" ? "border-danger" : e.direction === "+" ? "border-success" : "border-warning")}
            style={{ left: `${x * 100}%`, top: `${y * 100}%`, width: `${w * 100}%`, height: `${h * 100}%` }}
          />
        )}
        <Badge tone="dark" className="absolute left-1 top-1">
          Photo {(e.photo ?? 0) + 1}
        </Badge>
      </div>
      <figcaption className="mt-1 line-clamp-3 text-[11px] text-fg-2">{e.label.replace(/^Foto \d+: /, "")}</figcaption>
    </figure>
  );
}

export function AuthenticitySection({ d }: { d: OpportunityDetail }) {
  const a = d.insights?.authenticity;
  const [copied, setCopied] = useState(false);
  if (!a) return null;
  const v = VERDICT[a.verdict];
  const images = d.listing.images;
  const withPhoto = a.evidence.filter((e) => e.photo !== null && images[e.photo]);
  const other = a.evidence.filter((e) => e.photo === null || !images[e.photo]);
  return (
    <Section id="authenticity" icon={<Fingerprint />} title="Authenticity" description="Evidence from every photo, the text, the price and the seller. Never certain: a good fake can look right.">
      <div className="flex flex-wrap items-center gap-2">
        <Badge tone={v.tone} className="px-2.5 py-1 text-[13px] [&_svg]:size-4">
          {v.icon} {a.label}
        </Badge>
        <span className="text-[13px] text-fg-2 tnum">
          P(authentic) {pct(a.p_authentic)} · confidence {a.confidence}%
        </span>
      </div>

      {withPhoto.length > 0 && (
        <div className="scrollbar-none mt-4 flex gap-3 overflow-x-auto pb-1">
          {withPhoto.map((e, k) => (
            <PhotoProof key={k} url={images[e.photo ?? 0]?.url ?? ""} e={e} />
          ))}
        </div>
      )}
      {other.length > 0 && (
        <ul className="mt-3 space-y-1.5">
          {other.map((e, k) => (
            <li key={k} className="flex gap-2 text-[13px] text-fg-2">
              <EvidenceIcon e={e} />
              {e.label}
            </li>
          ))}
        </ul>
      )}

      {a.missing_photos.length > 0 && (
        <div className="mt-4 rounded-xl border border-line p-3">
          <p className="text-[13px] font-semibold text-fg">Photos to ask for</p>
          <ul className="mt-1 list-disc pl-5 text-[13px] text-fg-2">
            {a.missing_photos.map((m) => (
              <li key={m}>{m}</li>
            ))}
          </ul>
          {a.seller_message && (
            <>
              <pre className="mt-3 whitespace-pre-wrap rounded-lg bg-surface-2 p-3 font-sans text-[13px] text-fg">{a.seller_message}</pre>
              <Button
                variant="outline"
                size="sm"
                className="mt-2"
                onClick={async () => {
                  try {
                    await navigator.clipboard.writeText(a.seller_message ?? "");
                    setCopied(true);
                    setTimeout(() => setCopied(false), 1500);
                  } catch {
                    setCopied(false);
                  }
                }}
              >
                {copied ? <Check /> : <Copy />} {copied ? "Copied" : "Copy message"}
              </Button>
            </>
          )}
        </div>
      )}

      {a.checks.length > 0 && (
        <details className="group mt-3 rounded-xl bg-surface-2 p-3">
          <summary className="cursor-pointer list-none text-[13px] font-medium text-fg-2">What to check for this brand</summary>
          <ul className="mt-2 list-disc space-y-1 pl-5 text-[13px] text-fg-2">
            {a.checks.map((c) => (
              <li key={c}>{c}</li>
            ))}
          </ul>
        </details>
      )}
    </Section>
  );
}

function share(v: number | null, n: number): string {
  return v === null ? `${INSUFFICIENT} (${n})` : `${pct(v)} sold (${n})`;
}

export function InsightDetails({ i }: { i: DealInsights }) {
  const dem = i.demand;
  const s = i.seller;
  const lp = s.lowers_prices;
  const id = i.identification;
  const c = i.condition;
  return (
    <Section id="insight" icon={<Lightbulb />} title="Demand, seller, product" description="Every figure from observed data; “insufficient data” when there is not enough of it.">
      {(id.hidden_opportunities.length > 0 || c.differences.length > 0) && (
        <ul className="mb-4 space-y-1.5">
          {id.hidden_opportunities.map((h) => (
            <li key={h} className="rounded-lg bg-success-soft px-3 py-2 text-[13px] text-success">
              {h}
            </li>
          ))}
          {c.differences.map((h) => (
            <li key={h} className="rounded-lg bg-warning-soft px-3 py-2 text-[13px] text-warning">
              {h}
            </li>
          ))}
        </ul>
      )}
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
        <div className="text-[13px]">
          <h3 className="mb-1.5 font-semibold text-fg">Demand</h3>
          <dl className="space-y-1.5">
            <Row k="Favourites / day" v={dem.favourites_per_day !== null ? dem.favourites_per_day.toFixed(1) : INSUFFICIENT} />
            <Row k="Listing age" v={dem.listing_age_days !== null ? `${dem.listing_age_days} days` : "—"} />
            <Row k="Price drops" v={dem.price_drops.count ? `${dem.price_drops.count} (−${pct(dem.price_drops.total_pct)})` : "none seen"} />
            <Row k="Similar items" v={share(dem.sell_share.overall, dem.sell_share.n)} />
            <Row k={`Size ${dem.size.size ?? "—"}`} v={share(dem.size.sell_share, dem.size.n)} />
            <Row k={`Colour ${dem.color.color ?? "—"}`} v={share(dem.color.sell_share, dem.color.n)} />
            <Row
              k="Seasonality"
              v={dem.seasonality.available ? `${dem.seasonality.month} ×${dem.seasonality.factor} · best ${dem.seasonality.best_months?.join(", ")}` : INSUFFICIENT}
            />
            <Row k="Your tracking" v={dem.tracked_similar.n ? `${dem.tracked_similar.n} sold, median ${dem.tracked_similar.median_days_to_sell} days` : "no tracked sales yet"} />
          </dl>
        </div>
        <div className="text-[13px]">
          <h3 className="mb-1.5 font-semibold text-fg">Seller</h3>
          <dl className="space-y-1.5">
            <Row k="Rating" v={s.rating !== null ? `${s.rating.toFixed(1)}★ · ${s.reviews} reviews` : s.reviews === 0 ? "no reviews" : "unknown"} />
            <Row k="Account age" v={s.account_age_days !== null ? `${s.account_age_days} days` : "unknown"} />
            <Row k="Last active" v={s.last_active_days !== null ? `${s.last_active_days} days ago` : "unknown"} />
            <Row k="Response time" v="not shown by Vinted" />
            <Row
              k="Lowers prices"
              v={lp.share !== null ? `${lp.with_drops}/${lp.listings_seen} listings${lp.avg_drop_pct !== null ? `, avg −${pct(lp.avg_drop_pct)}` : ""}` : INSUFFICIENT}
            />
          </dl>
        </div>
        <div className="text-[13px]">
          <h3 className="mb-1.5 font-semibold text-fg">Product</h3>
          <dl className="space-y-1.5">
            <Row k="Brand" v={id.brand ?? "not identified"} />
            <Row k="Line / model" v={[id.line, id.model].filter(Boolean).join(" · ") || "—"} />
            <Row k="Period" v={id.season ?? "—"} />
            <Row k="Code" v={id.product_code ?? "—"} />
            <Row k="List price" v={id.original_price_list !== null ? eur(id.original_price_list) : id.original_price_claimed !== null ? `${eur(id.original_price_claimed)} (stated by seller)` : "not available"} />
            <Row k="Condition" v={c.effective === c.declared ? c.declared_label : `declared ${c.declared_label}, photos lower`} />
            <Row k="Photo check" v={c.photos_checked ? `${c.defects.length} defects seen` : "photos not analysed"} />
          </dl>
        </div>
      </div>
    </Section>
  );
}

function Row({ k, v }: { k: string; v: string }) {
  return (
    <div className="flex justify-between gap-3">
      <dt className="text-fg-3">{k}</dt>
      <dd className="text-right text-fg-2 tnum">{v}</dd>
    </div>
  );
}
