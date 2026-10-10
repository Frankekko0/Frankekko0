// Selling cycle, autonomy and business mode: types and data hooks.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { api, errorMessage } from "./api";

// ------------------------------------------------------------------ selling
export interface SellingItem {
  purchase_id: string;
  title: string;
  brand: string | null;
  stage: string;
  stage_label: string;
  total_cost: number;
  listed_price: number | null;
  initial_price: number | null;
  min_price: number | null;
  listed_at: string | null;
  days_listed: number | null;
  views: number | null;
  favourites: number | null;
  listing_url: string | null;
  price_history: { at: string; price: string; reason: string }[];
  expected_sale_price: number | null;
}
export interface SellingInventory {
  items: SellingItem[];
  by_stage: Record<string, number>;
  stages: [string, string][];
}
export interface ResalePlan {
  start_price: number;
  best_price: number;
  floor: number;
  expected_days_at_best: number;
  p_sold_30d_at_best: number;
  markdowns: { day: number; price: number; why: string }[];
  basis: "measured" | "prior";
  reliable: boolean;
  note: string;
}
export interface SellingPlan {
  item: SellingItem;
  reference_price: number | null;
  draft: { title: string; description: string; photo_checklist: string[]; to_confirm: string[]; not_claimed: string[] };
  plan: ResalePlan | null;
  plan_unavailable: string | null;
  survival: { basis: string; reliable: boolean; events: number; n: number };
  reprice: { action: "lower" | "raise" | "hold" | "improve_listing"; new_price: number | null; reason: string } | null;
}
export interface OfferDecision {
  action: "accept" | "counter" | "decline";
  counter_price: number | null;
  profit_at_offer: number;
  value_of_waiting: number;
  reason: string;
  reply: string;
  floor: number;
  asking: number;
}

export function useSellingInventory() {
  return useQuery({ queryKey: ["selling", "inventory"], queryFn: () => api<SellingInventory>("/selling/inventory") });
}
export function usePatchInventory() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: Record<string, unknown> }) => api<SellingItem>(`/selling/inventory/${id}`, { method: "PATCH", body }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["selling"] });
      qc.invalidateQueries({ queryKey: ["flips"] });
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
}
export function useSellingPlan(id: string | null) {
  return useQuery({ queryKey: ["selling", "plan", id], queryFn: () => api<SellingPlan>(`/selling/inventory/${id}/plan`), enabled: Boolean(id) });
}
export function useEvaluateOffer(id: string) {
  return useMutation({
    mutationFn: (body: { offer: number; buyer_name?: string }) => api<OfferDecision>(`/selling/inventory/${id}/offer`, { method: "POST", body }),
    onError: (e) => toast.error(errorMessage(e)),
  });
}

// ------------------------------------------------------------------ negotiation
export interface Negotiation {
  asked: number;
  ideal_offer: number | null;
  max_acceptable: number | null;
  discount_needed: number | null;
  discount_needed_pct: number | null;
  profit_table: { price: number; profit: number; roi: number | null }[];
  willingness: number;
  willingness_factors: string[];
  reasons: string[];
  messages: Record<string, string>;
  /** Where each message comes from; absent on an older server (then every message is a template). */
  messages_meta?: Record<string, MessageMeta>;
  ai?: NegotiationAi;
  note: string;
}
export type DraftTone = "polite" | "direct" | "firm";
export interface MessageMeta {
  source: "template" | "model";
  /** The rules a model draft broke (the template stayed). */
  violations: string[];
  tone?: DraftTone | null;
}
export interface NegotiationAi {
  /** The model can be asked to write the messages (feature on and a model configured). */
  enabled: boolean;
  /** At least one message is the model's. */
  used: boolean;
  provider: string | null;
  model: string | null;
  /** Why the templates stayed: disabled, no_model, injection, cooldown, daily_cap, rate_limited, unavailable, no_answer, guardrail. */
  fallback: string | null;
  retry_after: number | null;
  prompt_version: string | null;
  tone: DraftTone | null;
  cached: boolean;
  generated_at: string | null;
  injection_suspected: boolean;
}
export function useNegotiation(opportunityId: string, enabled: boolean) {
  return useQuery({ queryKey: ["negotiation", opportunityId], queryFn: () => api<Negotiation>(`/opportunities/${opportunityId}/negotiation`), enabled });
}
/** Ask the model to write the messages (one request per click). Always answers with a usable plan; ``ai.fallback`` says why the templates stayed. */
export function useDraftNegotiation(opportunityId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { tone: DraftTone; regenerate?: boolean }) =>
      api<Negotiation>(`/opportunities/${opportunityId}/negotiation/draft`, { method: "POST", body }),
    onSuccess: (data) => qc.setQueryData(["negotiation", opportunityId], data),
    onError: (e) => toast.error(errorMessage(e)),
  });
}

// ------------------------------------------------------------------ autonomy
export interface AutonomyState {
  enabled: boolean;
  killed: boolean;
  suspended: boolean;
  suspended_reason: string | null;
  dry_run: boolean;
  dry_run_until: string | null;
  mode: "off" | "dry_run" | "assisted";
  limits: Record<string, unknown>;
  channels: Record<string, string>;
}
export interface AutonomyAction {
  id: string;
  kind: string;
  status: "dry_run" | "blocked" | "pending_user" | "done" | "rejected" | "failed";
  channel: string;
  reasons: { code: string; label: string }[];
  opportunity_id: string | null;
  payload: Record<string, unknown>;
  verifier: { agrees: boolean; issues: { code: string; label: string; blocking: boolean }[] } | null;
  created_at: string;
}
export interface DryRunReport {
  actions: number;
  by_status: Record<string, number>;
  blocked_reasons: Record<string, number>;
  would_have_bought: number;
  still_available: number;
  gone_since: number;
  verifier_disagreed: number;
  verifier_disagreement_rate: number | null;
  note: string;
}
export interface AuditEvent {
  at: string;
  kind: string;
  actor: string;
  subject: string;
  payload: Record<string, unknown>;
}

const refreshAutonomy = (qc: ReturnType<typeof useQueryClient>) => qc.invalidateQueries({ queryKey: ["autonomy"] });

export function useAutonomy() {
  return useQuery({ queryKey: ["autonomy", "state"], queryFn: () => api<AutonomyState>("/autonomy"), refetchInterval: 30_000 });
}
export function useAutonomyActions() {
  return useQuery({ queryKey: ["autonomy", "actions"], queryFn: () => api<AutonomyAction[]>("/autonomy/actions", { query: { limit: 60 } }) });
}
export function useDryRunReport() {
  return useQuery({ queryKey: ["autonomy", "report"], queryFn: () => api<DryRunReport>("/autonomy/dry-run-report") });
}
export function useAudit() {
  return useQuery({ queryKey: ["autonomy", "audit"], queryFn: () => api<AuditEvent[]>("/autonomy/audit", { query: { limit: 40 } }) });
}
export function useAutonomyCommand(command: "kill" | "resume" | "disable" | "run") {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => api<Record<string, unknown>>(`/autonomy/${command}`, { method: "POST" }),
    onSuccess: () => refreshAutonomy(qc),
    onError: (e) => toast.error(errorMessage(e)),
  });
}
export function useEnableAutonomy() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { limits: Record<string, unknown>; skip_dry_run: boolean }) => api<AutonomyState>("/autonomy/enable", { method: "PUT", body }),
    onSuccess: () => {
      refreshAutonomy(qc);
      toast.success("Autonomy configured");
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
}
export function useResolveAction() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, outcome }: { id: string; outcome: "done" | "rejected" }) => api(`/autonomy/actions/${id}/resolve`, { method: "POST", body: { outcome } }),
    onSuccess: () => refreshAutonomy(qc),
    onError: (e) => toast.error(errorMessage(e)),
  });
}

// ------------------------------------------------------------------ business
export interface BusinessGoals {
  monthly_profit_target: number | null;
  initial_capital: number | null;
  max_capital: number | null;
  weekly_hours: number | null;
  horizon_months: number;
  reinvest_pct: number;
  min_reserve: number;
  explore_share: number;
  holder_status: "private" | "occasional" | "habitual" | "business";
  tax_thresholds: { name: string; amount: number; metric?: string; source: string; as_of: string }[];
}
export interface CeoReport {
  period: string;
  start: string;
  end: string;
  figures: Record<string, number | null>;
  text: string;
}
export interface CashflowRow {
  horizon_days: number;
  opening_cash: number;
  inflow: number;
  planned_purchases: number;
  running_costs: number;
  closing_cash: number;
  liquidity_risk: boolean;
}
export interface Cashflow {
  base: CashflowRow[];
  stress: CashflowRow[];
  stress_definition: { sales: string; payments_delayed_days: number; returns: string };
  liquidity_risk_in_stress: boolean;
  message: string;
  items_without_price: number;
  basis: string;
}
export interface Kpis {
  sales: number;
  realized_profit: number;
  contribution_margin_per_item: number | null;
  gmroi: number | null;
  sell_through: number | null;
  inventory_turnover: number | null;
  aging: { bucket: string; items: number; cost: number }[];
  cash_conversion_days: number | null;
  idle_capital_cost: number;
  hourly_profit: number | null;
  hours_basis: string;
  return_rate: number | null;
}
export interface Niches {
  niches: { niche: string; n: number; profit: number; per_euro_day: number; win_rate: number; trend: number; declining: boolean; growing: boolean }[];
  allocation: { niche: string; action: string; share: number; amount: number; reason: string }[];
  playbooks: Record<string, Record<string, unknown>>;
  capital: number;
  note: string;
}
export interface BusinessPlan {
  operating: { avg_cost: number; avg_profit: number; hold_days: number; minutes_per_item: number; basis: "measured" | "assumption" };
  scenarios: { name: string; items_per_month: number; capital_needed: number; hours_per_month: number; rotation_per_month: number; achievable_profit: number; limited_by: string | null; feasible: boolean }[];
  trajectory: { month: number; capital: number; profit: number; cumulative_profit: number }[];
  month_target_reached: number | null;
  note: string;
}
export interface TaxState {
  holder_status: string;
  notices: { name: string; level: "ok" | "approaching" | "exceeded"; share: number; message: string; source: string; as_of: string }[];
  checklist: string[];
  note: string;
}
export interface ScanResult {
  verdict: "BUY" | "NEGOTIATE" | "PASS" | "NOT_VERIFIED";
  shown_price: number;
  max_buy_price: number | null;
  max_buy_price_fast_sale: number | null;
  expected_profit: number | null;
  roi: number | null;
  confidence: number;
  reason: string;
  identification: { brand: string | null; category: string | null; size: string | null; known: string[] };
  elapsed_ms: number;
  latency_target_ms: number;
  limits: string;
}
export interface RefurbResult {
  worth_it: boolean;
  roi_as_is: number | null;
  roi_after: number | null;
  profit_as_is: number;
  profit_after: number;
  materials: number;
  labour: number;
  steps: string[];
  reason: string;
  assumption: string;
}

export function useGoals() {
  return useQuery({ queryKey: ["business", "goals"], queryFn: () => api<BusinessGoals>("/business/goals") });
}
export function useSaveGoals() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: BusinessGoals) => api<BusinessGoals>("/business/goals", { method: "PUT", body }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["business"] });
      toast.success("Goals saved");
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
}
export const useCeoReport = (period: "week" | "month") => useQuery({ queryKey: ["business", "report", period], queryFn: () => api<CeoReport>("/business/report", { query: { period } }) });
export const useKpis = () => useQuery({ queryKey: ["business", "kpis"], queryFn: () => api<Kpis>("/business/kpis") });
export const useCashflow = () => useQuery({ queryKey: ["business", "cashflow"], queryFn: () => api<Cashflow>("/business/cashflow") });
export const useNiches = () => useQuery({ queryKey: ["business", "niches"], queryFn: () => api<Niches>("/business/niches") });
export const useBusinessPlan = (enabled: boolean) => useQuery({ queryKey: ["business", "plan"], queryFn: () => api<BusinessPlan>("/business/plan"), enabled, retry: false });
export const useTax = () => useQuery({ queryKey: ["business", "tax"], queryFn: () => api<TaxState>("/business/tax") });
export function useScan() {
  return useMutation({
    mutationFn: (body: { shown_price: number; brand?: string; category?: string; size?: string; label_text?: string }) => api<ScanResult>("/business/scan", { method: "POST", body }),
    onError: (e) => toast.error(errorMessage(e)),
  });
}
export function useRefurb() {
  return useMutation({
    mutationFn: (body: { purchase_cost: number; resale_clean: number; defects: string[]; min_roi?: number }) => api<RefurbResult>("/business/refurb", { method: "POST", body }),
    onError: (e) => toast.error(errorMessage(e)),
  });
}
