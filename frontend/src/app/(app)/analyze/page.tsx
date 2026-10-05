"use client";

import { ArrowRight, ChevronDown, CircleCheck, CircleMinus, CirclePlus, Info, ScanSearch, Sparkles } from "lucide-react";
import { useRouter } from "next/navigation";
import { useMemo, useState, type FormEvent } from "react";
import { toast } from "sonner";
import { RiskBadge, ScoreRing } from "@/components/deal/score";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Field, Input, InputAffix, Select, Textarea } from "@/components/ui/input";
import { errorMessage } from "@/lib/api";
import { ACTION_LABEL, CONDITION_LABEL, DEMAND_LABEL, days, eur, pct } from "@/lib/format";
import { useBrands, useCategories, useImportListing, useQuickAnalysis } from "@/lib/queries";
import type { ManualListingInput, QuickAnalysis } from "@/lib/types";
import { cn } from "@/lib/utils";

interface FormState {
  url: string;
  title: string;
  price: string;
  shipping: string;
  brand: string;
  category: string;
  size: string;
  condition: string;
  color: string;
  description: string;
  images: string;
  sellerName: string;
  sellerRating: string;
  sellerReviews: string;
}

const EMPTY: FormState = {
  url: "",
  title: "",
  price: "",
  shipping: "",
  brand: "",
  category: "",
  size: "",
  condition: "",
  color: "",
  description: "",
  images: "",
  sellerName: "",
  sellerRating: "",
  sellerReviews: "",
};

const SCENARIO_LABEL = { conservative: "Quick sale", expected: "Expected", optimistic: "Optimistic" } as const;
const VERDICT_TONE = { BUY: "success", CONSIDER: "warning", SKIP: "danger" } as const;

function num(v: string): number | undefined {
  if (v.trim() === "") return undefined;
  const n = Number(v.replace(",", "."));
  return Number.isFinite(n) ? n : undefined;
}

/** Client-side checks mirror the API's; returns the payload or a readable error. */
function toPayload(f: FormState): { body?: ManualListingInput; error?: string } {
  const url = f.url.trim();
  if (!/^https?:\/\/\S+\.\S+/i.test(url)) return { error: "Enter the listing link (it must start with https://)." };
  if (f.title.trim().length < 3) return { error: "Enter the listing title (at least 3 characters)." };
  const price = num(f.price);
  if (price === undefined || price <= 0) return { error: "Enter the asking price." };
  const images = f.images
    .split(/\s+/)
    .map((s) => s.trim())
    .filter((s) => /^https?:\/\//i.test(s))
    .slice(0, 20);
  const body: ManualListingInput = {
    url,
    title: f.title.trim(),
    price,
    shipping_fee: num(f.shipping),
    brand: f.brand.trim() || undefined,
    category: f.category || undefined,
    size: f.size.trim() || undefined,
    condition: f.condition || undefined,
    color: f.color.trim() || undefined,
    description: f.description.trim() || undefined,
    image_urls: images.length ? images : undefined,
    seller_username: f.sellerName.trim() || undefined,
    seller_rating: num(f.sellerRating),
    seller_review_count: num(f.sellerReviews),
  };
  if (body.seller_rating !== undefined && (body.seller_rating < 0 || body.seller_rating > 5)) {
    return { error: "Seller rating must be between 0 and 5." };
  }
  return { body };
}

export default function AnalyzePage() {
  const router = useRouter();
  const [f, setF] = useState<FormState>(EMPTY);
  const [formError, setFormError] = useState<string | null>(null);
  const [showSeller, setShowSeller] = useState(false);
  const [checked, setChecked] = useState<{ result: QuickAnalysis; body: ManualListingInput } | null>(null);
  const brands = useBrands();
  const categories = useCategories();
  const importer = useImportListing();
  const quick = useQuickAnalysis();
  const set = (patch: Partial<FormState>) => setF((prev) => ({ ...prev, ...patch }));
  const leafCategories = useMemo(() => (categories.data ?? []).filter((c) => c.parent), [categories.data]);
  const busy = importer.isPending || quick.isPending;

  function track(body: ManualListingInput) {
    importer.mutate(body, {
      onSuccess: (r) => {
        toast.success(r.is_new ? "Listing analysed and added to your feed" : "Listing re-analysed with fresh data");
        router.push(`/deals/${r.opportunity_id}`);
      },
      onError: (e) => toast.error(errorMessage(e)),
    });
  }

  function submit(e: FormEvent, mode: "track" | "check") {
    e.preventDefault();
    const { body, error } = toPayload(f);
    setFormError(error ?? null);
    if (!body) return;
    if (mode === "track") return track(body);
    quick.mutate(body, {
      onSuccess: (result) => setChecked({ result, body }),
      onError: (err) => toast.error(errorMessage(err)),
    });
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Analyze a listing</h1>
        <p className="mt-1 max-w-2xl text-sm text-fg-2">
          Found something on Vinted or another marketplace? Paste its key details: FlipFinder estimates the market value, resale scenarios and
          profit with your own costs. The more details you add, the more precise the comparison.
        </p>
      </div>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-[minmax(0,1fr)_400px]">
        <Card className="p-5">
          <form onSubmit={(e) => submit(e, "track")} className="space-y-4" noValidate>
            <Field label="Listing link" htmlFor="url">
              <Input
                id="url"
                type="url"
                inputMode="url"
                placeholder="https://www.vinted.it/items/…"
                value={f.url}
                onChange={(e) => set({ url: e.target.value })}
                required
              />
            </Field>
            <Field label="Title" htmlFor="title">
              <Input id="title" placeholder="e.g. Felpa Ralph Lauren blu logo ricamato" value={f.title} onChange={(e) => set({ title: e.target.value })} maxLength={300} required />
            </Field>
            <div className="grid grid-cols-2 gap-3">
              <Field label="Asking price" htmlFor="price">
                <InputAffix id="price" prefix="€" inputMode="decimal" placeholder="0" value={f.price} onChange={(e) => set({ price: e.target.value })} required />
              </Field>
              <Field label="Shipping to you" htmlFor="shipping" hint="Leave empty to use your default">
                <InputAffix id="shipping" prefix="€" inputMode="decimal" placeholder="Default" value={f.shipping} onChange={(e) => set({ shipping: e.target.value })} />
              </Field>
            </div>
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <Field label="Brand" htmlFor="brand">
                <Input id="brand" list="brand-options" placeholder="e.g. Ralph Lauren" value={f.brand} onChange={(e) => set({ brand: e.target.value })} maxLength={120} />
                <datalist id="brand-options">
                  {(brands.data ?? []).map((b) => (
                    <option key={b.slug} value={b.name} />
                  ))}
                </datalist>
              </Field>
              <Field label="Category" htmlFor="category">
                <Select id="category" value={f.category} onChange={(e) => set({ category: e.target.value })}>
                  <option value="">Detect from title</option>
                  {leafCategories.map((c) => (
                    <option key={c.slug} value={c.slug}>
                      {c.name}
                    </option>
                  ))}
                </Select>
              </Field>
              <Field label="Size" htmlFor="size">
                <Input id="size" placeholder="e.g. M, EU42" value={f.size} onChange={(e) => set({ size: e.target.value })} maxLength={60} />
              </Field>
              <Field label="Condition" htmlFor="condition">
                <Select id="condition" value={f.condition} onChange={(e) => set({ condition: e.target.value })}>
                  <option value="">Not specified</option>
                  {Object.entries(CONDITION_LABEL)
                    .filter(([k]) => k !== "unknown")
                    .map(([k, label]) => (
                      <option key={k} value={k}>
                        {label}
                      </option>
                    ))}
                </Select>
              </Field>
              <Field label="Color" htmlFor="color">
                <Input id="color" placeholder="e.g. blu" value={f.color} onChange={(e) => set({ color: e.target.value })} maxLength={60} />
              </Field>
            </div>
            <Field label="Description" htmlFor="description" hint="Paste it as-is: defects, materials and keywords all count.">
              <Textarea id="description" rows={4} value={f.description} onChange={(e) => set({ description: e.target.value })} maxLength={5000} />
            </Field>
            <Field label="Photo links (optional)" htmlFor="images" hint="One per line. Used for the image checks when available.">
              <Textarea id="images" rows={2} placeholder="https://…" value={f.images} onChange={(e) => set({ images: e.target.value })} />
            </Field>

            <div className="rounded-xl border border-line">
              <button
                type="button"
                onClick={() => setShowSeller(!showSeller)}
                className="flex w-full items-center justify-between px-4 py-3 text-left text-[13px] font-medium text-fg-2"
                aria-expanded={showSeller}
              >
                Seller details (optional)
                <ChevronDown className={cn("size-4 transition-transform", showSeller && "rotate-180")} />
              </button>
              {showSeller && (
                <div className="grid grid-cols-1 gap-3 border-t border-line p-4 sm:grid-cols-3">
                  <Field label="Username" htmlFor="seller">
                    <Input id="seller" value={f.sellerName} onChange={(e) => set({ sellerName: e.target.value })} maxLength={120} />
                  </Field>
                  <Field label="Rating (0–5)" htmlFor="rating">
                    <Input id="rating" inputMode="decimal" value={f.sellerRating} onChange={(e) => set({ sellerRating: e.target.value })} />
                  </Field>
                  <Field label="Reviews" htmlFor="reviews">
                    <Input id="reviews" inputMode="numeric" value={f.sellerReviews} onChange={(e) => set({ sellerReviews: e.target.value })} />
                  </Field>
                </div>
              )}
            </div>

            {formError && (
              <p role="alert" className="rounded-lg bg-danger-soft px-3 py-2 text-[13px] text-danger">
                {formError}
              </p>
            )}
            <div className="flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
              <Button type="button" variant="outline" onClick={(e) => submit(e, "check")} loading={quick.isPending} disabled={busy}>
                <ScanSearch /> Quick check
              </Button>
              <Button type="submit" loading={importer.isPending} disabled={busy}>
                <Sparkles /> Analyze &amp; track
              </Button>
            </div>
            <p className="text-xs text-fg-3">
              Quick check computes everything without saving. Analyze &amp; track adds the listing to your feed, so you can save it, watch its price
              and record the purchase. FlipFinder never contacts the seller or buys for you.
            </p>
          </form>
        </Card>

        <div className="lg:sticky lg:top-20 lg:h-fit">
          {checked ? (
            <QuickResult data={checked.result} onTrack={() => track(checked.body)} tracking={importer.isPending} />
          ) : (
            <Card className="p-5">
              <p className="text-[15px] font-semibold">What you will get</p>
              <ul className="mt-3 space-y-2.5 text-[13px] text-fg-2">
                {[
                  "Fair market value from comparable listings, outliers excluded",
                  "Quick, expected and optimistic resale price with net profit and ROI",
                  "Maximum price to pay for your profit and ROI targets",
                  "Flip Score, confidence and risk, each with its reasons",
                  "Demand and estimated time to sell",
                ].map((t) => (
                  <li key={t} className="flex gap-2">
                    <CircleCheck className="mt-0.5 size-4 shrink-0 text-success" /> {t}
                  </li>
                ))}
              </ul>
            </Card>
          )}
        </div>
      </div>
    </div>
  );
}

function QuickResult({ data, onTrack, tracking }: { data: QuickAnalysis; onTrack: () => void; tracking: boolean }) {
  const expected = data.scenarios.find((s) => s.name === "expected");
  const noMarket = data.fair_market_value === null;
  return (
    <Card className="animate-fade-up p-5">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-xs font-medium text-fg-3">Quick check · not saved</p>
          <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
            <Badge tone="outline">Confidence {data.confidence_score}</Badge>
            <RiskBadge level={data.risk.level} score={data.risk.score} />
            {data.ai_analysis?.verdict && <Badge tone={VERDICT_TONE[data.ai_analysis.verdict]}>{data.ai_analysis.verdict}</Badge>}
          </div>
        </div>
        <ScoreRing score={data.flip_score} size={64} stroke={5} label="Flip" />
      </div>

      {noMarket ? (
        <div className="mt-4 flex gap-2 rounded-xl bg-warning-soft p-3 text-[13px] text-fg">
          <Info className="mt-0.5 size-4 shrink-0 text-warning" />
          <p>{data.market.notes?.[0] ?? "Non ci sono abbastanza dati per stimare con affidabilità il prezzo di mercato."}</p>
        </div>
      ) : (
        <>
          <dl className="mt-4 grid grid-cols-2 gap-3 rounded-xl bg-surface-2 p-3.5">
            <div>
              <dt className="text-[11px] font-semibold uppercase tracking-wider text-fg-3">Market value</dt>
              <dd className="text-lg font-semibold tnum">{eur(data.fair_market_value)}</dd>
            </div>
            <div>
              <dt className="text-[11px] font-semibold uppercase tracking-wider text-fg-3">Expected profit</dt>
              <dd className={cn("text-lg font-semibold tnum", (expected?.net_profit ?? 0) > 0 ? "text-success" : "text-danger")}>
                {expected ? eur(expected.net_profit, { sign: true }) : "—"}
              </dd>
            </div>
            <div>
              <dt className="text-[11px] font-semibold uppercase tracking-wider text-fg-3">Max to pay</dt>
              <dd className="text-[15px] font-semibold tnum">{eur(data.max_buy_price)}</dd>
            </div>
            <div>
              <dt className="text-[11px] font-semibold uppercase tracking-wider text-fg-3">Suggested offer</dt>
              <dd className="text-[15px] font-semibold tnum text-accent">{eur(data.offer.suggested_offer)}</dd>
            </div>
          </dl>

          <table className="mt-4 w-full text-[13px]">
            <thead>
              <tr className="text-left text-xs text-fg-3">
                <th className="pb-1.5 font-medium">Scenario</th>
                <th className="pb-1.5 text-right font-medium">Sell at</th>
                <th className="pb-1.5 text-right font-medium">Profit</th>
                <th className="pb-1.5 text-right font-medium">ROI</th>
              </tr>
            </thead>
            <tbody>
              {data.scenarios.map((s) => (
                <tr key={s.name} className="border-t border-line">
                  <td className="py-1.5 text-fg-2">{SCENARIO_LABEL[s.name]}</td>
                  <td className="py-1.5 text-right tnum">{eur(s.sale_price)}</td>
                  <td className={cn("py-1.5 text-right font-medium tnum", s.net_profit > 0 ? "text-success" : "text-danger")}>
                    {eur(s.net_profit, { sign: true })}
                  </td>
                  <td className="py-1.5 text-right tnum">{pct(s.roi)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="mt-2 text-xs text-fg-3">
            Total cost {eur(expected?.total_acquisition_cost)} · {data.comparables_used} comparables · {DEMAND_LABEL[data.demand.level] ?? data.demand.level} demand · sells
            in {days(data.velocity.days)}
          </p>
        </>
      )}

      <div className="mt-4 rounded-xl border border-line p-3">
        <p className="text-[13px] font-semibold">{ACTION_LABEL[data.offer.action] ?? data.offer.action}</p>
        <p className="mt-0.5 text-[13px] text-fg-2">{data.offer.rationale}</p>
      </div>

      {data.explanation.length > 0 && (
        <ul className="mt-4 space-y-1.5 text-[13px]">
          {data.explanation.slice(0, 7).map((r) => (
            <li key={r.code + r.label} className="flex items-start gap-2">
              {r.type === "positive" ? (
                <CirclePlus className="mt-0.5 size-3.5 shrink-0 text-success" />
              ) : r.type === "negative" ? (
                <CircleMinus className="mt-0.5 size-3.5 shrink-0 text-danger" />
              ) : (
                <Info className="mt-0.5 size-3.5 shrink-0 text-fg-3" />
              )}
              <span className="text-fg-2">{r.label}</span>
            </li>
          ))}
        </ul>
      )}

      <Button className="mt-5 w-full" onClick={onTrack} loading={tracking}>
        Track this listing <ArrowRight />
      </Button>
    </Card>
  );
}
