"use client";

import { BellRing, Calculator, Goal, LogOut, Scale, Send, Smartphone, Sparkles, Tags } from "lucide-react";
import { useRouter } from "next/navigation";
import { useMemo, useState, type ReactNode, type CSSProperties } from "react";
import { toast } from "sonner";
import { DataSourcesCard } from "@/components/settings/data-sources";
import { ErrorLogCard } from "@/components/settings/error-log";
import { ExtensionKeysCard } from "@/components/settings/extension-keys";
import { PriceDataCard } from "@/components/settings/price-data";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/feedback";
import { Field, Input, InputAffix } from "@/components/ui/input";
import { Chip, RangeSlider, Switch } from "@/components/ui/misc";
import { api, errorMessage } from "@/lib/api";
import { eur, pct } from "@/lib/format";
import { useBrands, useCategories, useNotificationSettings, usePreferences, useSaveNotificationSettings, useSavePreferences } from "@/lib/queries";
import type { CostProfile, NotificationSettings, Preferences } from "@/lib/types";

const WEIGHT_LABELS: Record<string, string> = {
  undervaluation: "Price undervaluation",
  roi: "Expected ROI",
  profit: "Expected net profit",
  demand: "Demand",
  velocity: "Sales velocity",
  freshness: "Listing freshness",
  seller: "Seller reliability",
};
const DEFAULT_WEIGHTS: Record<string, number> = { undervaluation: 30, roi: 20, profit: 15, demand: 15, velocity: 10, freshness: 5, seller: 5 };

function Block({ icon, title, description, children }: { icon: ReactNode; title: string; description?: string; children: ReactNode }) {
  return (
    <Card className="reveal">
      <CardHeader>
        <div>
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

function NumberInput({ value, onChange, prefix, suffix, scale = 1, nullable }: { value: number | null; onChange: (v: number | null) => void; prefix?: string; suffix?: string; scale?: number; nullable?: boolean }) {
  const shown = value === null ? "" : String(Math.round(value * scale * 1000) / 1000);
  const [draft, setDraft] = useState(shown);
  const [prev, setPrev] = useState(shown);
  if (prev !== shown) {
    setPrev(shown);
    setDraft(shown);
  }
  return (
    <InputAffix
      inputMode="decimal"
      prefix={prefix}
      suffix={suffix}
      value={draft}
      placeholder={nullable ? "No limit" : "0"}
      onChange={(e) => setDraft(e.target.value)}
      onBlur={() => {
        if (draft.trim() === "") return onChange(nullable ? null : 0);
        const n = Number(draft.replace(",", "."));
        if (!Number.isNaN(n)) onChange(n / scale);
      }}
    />
  );
}

function costExample(c: CostProfile, buy = 18, sell = 39, shipping = 3.49) {
  const bp = c.buyer_protection_fixed + c.buyer_protection_pct * buy;
  const ship = c.use_listing_shipping ? shipping : c.shipping_in;
  const tac = buy + bp + ship + c.other_acquisition;
  const nsr =
    sell -
    (c.selling_fee_fixed + c.selling_fee_pct * sell) -
    (c.payment_fee_fixed + c.payment_fee_pct * sell) -
    c.advertising -
    c.packaging -
    c.shipping_out -
    c.other_sale;
  return { tac, nsr, profit: nsr - tac, roi: (nsr - tac) / tac };
}

function PreferencesForm({ initial }: { initial: Preferences }) {
  const [p, setP] = useState<Preferences>(initial);
  const [saved, setSaved] = useState<Preferences>(initial);
  const dirty = useMemo(() => JSON.stringify(p) !== JSON.stringify(saved), [p, saved]);
  const save = useSavePreferences();
  const brands = useBrands();
  const categories = useCategories();
  const set = (patch: Partial<Preferences>) => setP({ ...p, ...patch });
  const setCost = (patch: Partial<CostProfile>) => setP({ ...p, cost_profile: { ...p.cost_profile, ...patch } });
  const weights = { ...DEFAULT_WEIGHTS, ...(p.score_weights ?? {}) };
  const totalWeight = Object.values(weights).reduce((a, b) => a + b, 0) || 1;
  const ex = useMemo(() => costExample(p.cost_profile), [p.cost_profile]);
  const toggle = (l: string[], v: string) => (l.includes(v) ? l.filter((x) => x !== v) : [...l, v]);
  const c = p.cost_profile;

  return (
    <div className="space-y-5">
      <Block icon={<Goal />} title="Goals" description="Used for the maximum buy price, offers and the “My criteria” preset.">
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
          <Field label="Minimum profit per flip">
            <NumberInput prefix="€" value={p.min_profit} onChange={(v) => set({ min_profit: v ?? 0 })} />
          </Field>
          <Field label="Minimum ROI">
            <NumberInput suffix="%" scale={100} value={p.min_roi} onChange={(v) => set({ min_roi: v ?? 0 })} />
          </Field>
          <Field label="Maximum purchase price">
            <NumberInput prefix="€" nullable value={p.max_purchase_price} onChange={(v) => set({ max_purchase_price: v })} />
          </Field>
          <Field label="Minimum Flip Score">
            <NumberInput nullable value={p.min_flip_score} onChange={(v) => set({ min_flip_score: v })} />
          </Field>
          <Field label="Maximum Risk Score">
            <NumberInput nullable value={p.max_risk_score} onChange={(v) => set({ max_risk_score: v })} />
          </Field>
          <Field label="Minimum Confidence">
            <NumberInput nullable value={p.min_confidence} onChange={(v) => set({ min_confidence: v })} />
          </Field>
        </div>
      </Block>

      <Block icon={<Calculator />} title="Costs" description="Every profit and ROI in the app is computed with these costs — including feed filters and sorting.">
        <div className="grid grid-cols-1 gap-6 lg:grid-cols-[minmax(0,1fr)_280px]">
          <div className="space-y-5">
            <div>
              <p className="mb-2 text-xs font-semibold uppercase tracking-[0.08em] text-fg-3">When buying</p>
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                <Field label="Buyer protection (fixed)">
                  <NumberInput prefix="€" value={c.buyer_protection_fixed} onChange={(v) => setCost({ buyer_protection_fixed: v ?? 0 })} />
                </Field>
                <Field label="Buyer protection (% of price)">
                  <NumberInput suffix="%" scale={100} value={c.buyer_protection_pct} onChange={(v) => setCost({ buyer_protection_pct: v ?? 0 })} />
                </Field>
                <Field label="Default inbound shipping">
                  <NumberInput prefix="€" value={c.shipping_in} onChange={(v) => setCost({ shipping_in: v ?? 0 })} />
                </Field>
                <Field label="Other acquisition costs">
                  <NumberInput prefix="€" value={c.other_acquisition} onChange={(v) => setCost({ other_acquisition: v ?? 0 })} />
                </Field>
              </div>
              <label className="mt-3 flex items-center justify-between gap-3">
                <span className="text-[13px] text-fg-2">Use the shipping cost shown on each listing when available</span>
                <Switch checked={c.use_listing_shipping} onCheckedChange={(v) => setCost({ use_listing_shipping: v })} />
              </label>
            </div>
            <div>
              <p className="mb-2 text-xs font-semibold uppercase tracking-[0.08em] text-fg-3">When selling</p>
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                <Field label="Selling fee (fixed)">
                  <NumberInput prefix="€" value={c.selling_fee_fixed} onChange={(v) => setCost({ selling_fee_fixed: v ?? 0 })} />
                </Field>
                <Field label="Selling fee (%)">
                  <NumberInput suffix="%" scale={100} value={c.selling_fee_pct} onChange={(v) => setCost({ selling_fee_pct: v ?? 0 })} />
                </Field>
                <Field label="Packaging">
                  <NumberInput prefix="€" value={c.packaging} onChange={(v) => setCost({ packaging: v ?? 0 })} />
                </Field>
                <Field label="Advertising / bumps">
                  <NumberInput prefix="€" value={c.advertising} onChange={(v) => setCost({ advertising: v ?? 0 })} />
                </Field>
                <Field label="Payment fee (fixed)">
                  <NumberInput prefix="€" value={c.payment_fee_fixed} onChange={(v) => setCost({ payment_fee_fixed: v ?? 0 })} />
                </Field>
                <Field label="Payment fee (%)">
                  <NumberInput suffix="%" scale={100} value={c.payment_fee_pct} onChange={(v) => setCost({ payment_fee_pct: v ?? 0 })} />
                </Field>
                <Field label="Shipping paid by you">
                  <NumberInput prefix="€" value={c.shipping_out} onChange={(v) => setCost({ shipping_out: v ?? 0 })} />
                </Field>
                <Field label="Other selling costs">
                  <NumberInput prefix="€" value={c.other_sale} onChange={(v) => setCost({ other_sale: v ?? 0 })} />
                </Field>
              </div>
            </div>
          </div>
          <div className="h-fit rounded-xl bg-surface-2 p-4 text-[13px] lg:sticky lg:top-20">
            <p className="font-semibold text-fg">Live example</p>
            <p className="mt-0.5 text-xs text-fg-3">Buy at €18, sell at €39, listing shipping €3.49</p>
            <dl className="mt-3 space-y-1.5">
              <div className="flex justify-between">
                <dt className="text-fg-2">Total acquisition cost</dt>
                <dd className="font-medium tnum">{eur(ex.tac)}</dd>
              </div>
              <div className="flex justify-between">
                <dt className="text-fg-2">Net sale revenue</dt>
                <dd className="font-medium tnum">{eur(ex.nsr)}</dd>
              </div>
              <div className="flex justify-between border-t border-line pt-1.5">
                <dt className="font-semibold">Net profit</dt>
                <dd className={`font-semibold tnum ${ex.profit >= 0 ? "text-success" : "text-danger"}`}>{eur(ex.profit, { sign: true })}</dd>
              </div>
              <div className="flex justify-between">
                <dt className="font-semibold">ROI</dt>
                <dd className={`font-semibold tnum ${ex.roi >= 0 ? "text-success" : "text-danger"}`}>{pct(ex.roi)}</dd>
              </div>
            </dl>
          </div>
        </div>
      </Block>

      <Block icon={<Tags />} title="Preferences" description="Brands, categories and sizes you like to flip (used by the “My criteria” preset).">
        <div className="space-y-4">
          <div>
            <p className="mb-2 text-[13px] font-medium text-fg-2">Preferred brands</p>
            <div className="flex flex-wrap gap-1.5">
              {(brands.data ?? []).map((b) => (
                <Chip key={b.slug} active={p.preferred_brands.includes(b.slug)} onClick={() => set({ preferred_brands: toggle(p.preferred_brands, b.slug) })}>
                  {b.name}
                </Chip>
              ))}
            </div>
          </div>
          <div>
            <p className="mb-2 text-[13px] font-medium text-fg-2">Preferred categories</p>
            <div className="flex flex-wrap gap-1.5">
              {(categories.data ?? [])
                .filter((x) => x.parent)
                .map((x) => (
                  <Chip key={x.slug} active={p.preferred_categories.includes(x.slug)} onClick={() => set({ preferred_categories: toggle(p.preferred_categories, x.slug) })}>
                    {x.name}
                  </Chip>
                ))}
            </div>
          </div>
          <Field label="Sizes" hint="Comma separated, e.g. M, L, EU42">
            <Input value={p.sizes.join(", ")} onChange={(e) => set({ sizes: e.target.value.split(",").map((s) => s.trim().toUpperCase()).filter(Boolean) })} />
          </Field>
        </div>
      </Block>

      <Block icon={<Scale />} title="Flip Score weights" description="Tune what matters to you. Weights are normalised to 100%.">
        <div className="grid grid-cols-1 gap-x-8 gap-y-5 sm:grid-cols-2">
          {Object.keys(DEFAULT_WEIGHTS).map((k) => (
            <RangeSlider
              key={k}
              label={WEIGHT_LABELS[k] ?? k}
              value={weights[k] ?? 0}
              max={60}
              format={(v) => `${Math.round((v / totalWeight) * 100)}%`}
              onCommit={(v) => set({ score_weights: { ...weights, [k]: v } })}
            />
          ))}
        </div>
        <label className="mt-4 flex items-center justify-between gap-3 rounded-xl bg-surface-2 p-3">
          <span className="flex items-center gap-2 text-[13px] text-fg-2">
            <Sparkles className="size-4 text-accent" /> Personal Flip Score (learns from your flips and ignored deals)
          </span>
          <Switch checked={p.personalization_enabled} onCheckedChange={(v) => set({ personalization_enabled: v })} />
        </label>
        <Button variant="ghost" size="sm" className="mt-2" onClick={() => set({ score_weights: null })}>
          Reset to defaults
        </Button>
      </Block>

      {dirty && (
        <div className="pointer-events-none fixed inset-x-0 bottom-20 z-30 flex justify-center px-4 lg:bottom-6">
          <div className="pointer-events-auto flex animate-fade-up items-center gap-2 rounded-2xl border border-line-strong bg-surface py-2 pl-4 pr-2 shadow-pop">
            <span className="mr-2 text-[13px] text-fg-2">Unsaved changes</span>
            <Button variant="ghost" size="sm" onClick={() => setP(saved)} disabled={save.isPending}>
              Discard
            </Button>
            <Button size="sm" onClick={() => save.mutate(p, { onSuccess: () => setSaved(p) })} loading={save.isPending}>
              Save settings
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}

function urlBase64ToUint8Array(base64: string) {
  const padding = "=".repeat((4 - (base64.length % 4)) % 4);
  const raw = atob((base64 + padding).replace(/-/g, "+").replace(/_/g, "/"));
  return Uint8Array.from([...raw].map((ch) => ch.charCodeAt(0)));
}

function NotificationsForm({ initial }: { initial: NotificationSettings }) {
  const [n, setN] = useState<NotificationSettings>(initial);
  const save = useSaveNotificationSettings();
  const set = (patch: Partial<NotificationSettings>) => setN({ ...n, ...patch });
  const avail = initial.available_channels;
  const [testing, setTesting] = useState<string | null>(null);
  const [permission, setPermission] = useState<string>(typeof Notification !== "undefined" ? Notification.permission : "unsupported");

  async function test(channel: string) {
    setTesting(channel);
    try {
      await save.mutateAsync(n);
      await api("/settings/notifications/test", { method: "POST", body: { channel } });
      toast.success("Test notification sent");
    } catch (e) {
      toast.error(errorMessage(e));
    } finally {
      setTesting(null);
    }
  }

  async function enableBrowser() {
    if (typeof Notification === "undefined") return toast.error("This browser does not support notifications");
    const result = await Notification.requestPermission();
    setPermission(result);
    if (result !== "granted") return toast.error("Notifications are blocked in this browser");
    if (initial.vapid_public_key && "serviceWorker" in navigator) {
      try {
        const reg = await navigator.serviceWorker.register("/sw.js");
        await navigator.serviceWorker.ready;
        const sub = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: urlBase64ToUint8Array(initial.vapid_public_key) });
        await api("/notifications/push/subscribe", { method: "POST", body: sub.toJSON() });
        set({ web_push_enabled: true });
        await save.mutateAsync({ ...n, web_push_enabled: true });
        toast.success("Push notifications enabled on this device");
      } catch (e) {
        toast.error(errorMessage(e));
      }
    } else {
      toast.success("Browser notifications enabled while FlipFinder is open");
    }
  }

  const channels: { key: keyof NotificationSettings; label: string; channel?: string; available: boolean; field?: ReactNode; icon: ReactNode }[] = [
    { key: "in_app_enabled", label: "In-app notifications", available: true, icon: <BellRing /> },
    {
      key: "web_push_enabled",
      label: "Push notifications (this device)",
      channel: "web_push",
      available: Boolean(avail?.web_push),
      icon: <Smartphone />,
      field: (
        <Button variant="outline" size="sm" onClick={enableBrowser}>
          {permission === "granted" ? "Re-register this device" : "Allow on this device"}
        </Button>
      ),
    },
    {
      key: "email_enabled",
      label: "Email",
      channel: "email",
      available: Boolean(avail?.email),
      icon: <Send />,
      field: <Input type="email" value={n.email_address ?? ""} onChange={(e) => set({ email_address: e.target.value || null })} placeholder="you@example.com" />,
    },
    {
      key: "telegram_enabled",
      label: "Telegram",
      channel: "telegram",
      available: Boolean(avail?.telegram),
      icon: <Send />,
      field: <Input value={n.telegram_chat_id ?? ""} onChange={(e) => set({ telegram_chat_id: e.target.value || null })} placeholder="Chat ID (e.g. 123456789)" />,
    },
    {
      key: "discord_enabled",
      label: "Discord",
      channel: "discord",
      available: true,
      icon: <Send />,
      field: <Input value={n.discord_webhook_url ?? ""} onChange={(e) => set({ discord_webhook_url: e.target.value || null })} placeholder="https://discord.com/api/webhooks/…" />,
    },
  ];

  return (
    <div className="space-y-5">
      <Block icon={<BellRing />} title="Notification channels" description="Alerts are always visible in the in-app inbox; other channels are optional.">
        <div className="divide-y divide-line">
          {channels.map((ch) => (
            <div key={ch.key} className="py-3 first:pt-0">
              <div className="flex items-center justify-between gap-3">
                <span className="text-[14px] font-medium text-fg">{ch.label}</span>
                <div className="flex items-center gap-2">
                  {ch.channel && n[ch.key] && ch.available && (
                    <Button variant="ghost" size="xs" onClick={() => test(ch.channel!)} loading={testing === ch.channel}>
                      Send test
                    </Button>
                  )}
                  <Switch checked={Boolean(n[ch.key])} disabled={!ch.available} onCheckedChange={(v) => set({ [ch.key]: v } as Partial<NotificationSettings>)} aria-label={ch.label} />
                </div>
              </div>
              {!ch.available && <p className="mt-1 text-xs text-fg-3">Not configured on the server (see README → notifications).</p>}
              {ch.available && ch.field && n[ch.key] !== undefined && <div className="mt-2 max-w-md">{ch.field}</div>}
            </div>
          ))}
        </div>
      </Block>

      <Block icon={<Goal />} title="Alert criteria" description="Get notified when a new listing beats all these thresholds.">
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
          <Field label="Flip Score above">
            <NumberInput value={n.alert_min_flip_score} onChange={(v) => set({ alert_min_flip_score: v ?? 0 })} />
          </Field>
          <Field label="ROI above">
            <NumberInput suffix="%" scale={100} value={n.alert_min_roi} onChange={(v) => set({ alert_min_roi: v ?? 0 })} />
          </Field>
          <Field label="Expected profit above">
            <NumberInput prefix="€" value={n.alert_min_profit} onChange={(v) => set({ alert_min_profit: v ?? 0 })} />
          </Field>
          <Field label="Confidence above">
            <NumberInput value={n.alert_min_confidence} onChange={(v) => set({ alert_min_confidence: v ?? 0 })} />
          </Field>
          <Field label="Risk below">
            <NumberInput nullable value={n.alert_max_risk_score} onChange={(v) => set({ alert_max_risk_score: v })} />
          </Field>
        </div>
        <div className="mt-4 grid grid-cols-1 gap-3 sm:grid-cols-2">
          {(
            [
              ["ultra_deal_alerts", "🔥 Ultra Deals (high priority)"],
              ["new_opportunity_alerts", "New opportunities above criteria"],
              ["price_drop_alerts", "Price drops that make a deal interesting"],
              ["watchlist_alerts", "Watchlist matches"],
            ] as const
          ).map(([key, label]) => (
            <label key={key} className="flex items-center justify-between gap-3 rounded-xl border border-line p-3">
              <span className="text-[13px] text-fg-2">{label}</span>
              <Switch checked={n[key]} onCheckedChange={(v) => set({ [key]: v })} />
            </label>
          ))}
        </div>
        <div className="mt-4 flex justify-end">
          <Button onClick={() => save.mutate(n)} loading={save.isPending}>
            Save notification settings
          </Button>
        </div>
      </Block>
    </div>
  );
}

export default function SettingsPage() {
  const prefs = usePreferences();
  const notif = useNotificationSettings();
  const router = useRouter();
  async function logoutEverywhere() {
    await api("/auth/logout?everywhere=true", { method: "POST" }).catch(() => undefined);
    router.replace("/login");
  }
  return (
    <div className="mx-auto max-w-4xl space-y-6">
      <div>
        <h1 className="enter text-[28px] font-semibold leading-tight tracking-[-0.025em] sm:text-[32px]">Settings</h1>
        <p className="enter mt-1.5 text-sm text-fg-2" style={{ "--i": 1 } as CSSProperties}>Your targets, costs and notifications shape every score and alert.</p>
      </div>
      {prefs.data ? <PreferencesForm initial={prefs.data} /> : <Skeleton className="h-96 rounded-2xl" />}
      {notif.data ? <NotificationsForm initial={notif.data} /> : <Skeleton className="h-96 rounded-2xl" />}
      <ExtensionKeysCard />
      <DataSourcesCard />
      <PriceDataCard />
      <ErrorLogCard />
      <Block icon={<LogOut />} title="Account">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <p className="text-[13px] text-fg-2">Sign out from all devices (invalidates every session).</p>
          <Button variant="outline" onClick={logoutEverywhere}>
            <LogOut /> Log out everywhere
          </Button>
        </div>
      </Block>
    </div>
  );
}
