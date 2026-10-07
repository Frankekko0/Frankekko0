"use client";

import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { api, errorMessage } from "./api";
import type {
  Accuracy,
  ExtensionKey,
  ExtensionKeyCreated,
  AcquisitionStatus,
  AiAnalysis,
  AlertItem,
  BatchImportInput,
  BatchImportResult,
  Brand,
  Category,
  EmailImportResult,
  FavoriteState,
  Flip,
  ImportResult,
  Insights,
  Item,
  ItemDetail,
  ItemFilters,
  LinkImportResult,
  ManualListingInput,
  MarketSegment,
  NotificationSettings,
  OpportunityCard,
  OpportunityDetail,
  OpportunityFilters,
  Page,
  ParsedQuery,
  Portfolio,
  Preferences,
  PricingEvidence,
  QuickAnalysis,
  QuickStats,
  RefreshResult,
  SearchResponse,
  SegmentStats,
  SystemStatus,
  UnreadCount,
  User,
  Watchlist,
  WatchlistInput,
} from "./types";

export const qk = {
  me: ["me"] as const,
  opportunities: (f: OpportunityFilters) => ["opportunities", f] as const,
  opportunity: (id: string) => ["opportunity", id] as const,
  stats: ["stats"] as const,
  brands: ["brands"] as const,
  categories: ["categories"] as const,
  watchlists: ["watchlists"] as const,
  alerts: (f: Record<string, unknown>) => ["alerts", f] as const,
  unread: ["alerts", "unread"] as const,
  flips: ["flips"] as const,
  portfolio: ["portfolio"] as const,
  status: ["status"] as const,
};

const LIVE = 30_000;

export function useMe() {
  return useQuery({ queryKey: qk.me, queryFn: () => api<User>("/auth/me"), staleTime: 5 * 60_000, retry: false });
}

export function useOpportunities(filters: OpportunityFilters, opts: { live?: boolean; enabled?: boolean } = {}) {
  return useQuery({
    queryKey: qk.opportunities(filters),
    queryFn: ({ signal }) => api<Page<OpportunityCard>>("/opportunities", { query: { ...filters }, signal }),
    placeholderData: keepPreviousData,
    refetchInterval: opts.live ? LIVE : false,
    enabled: opts.enabled ?? true,
  });
}

export function useOpportunity(id: string) {
  return useQuery({ queryKey: qk.opportunity(id), queryFn: () => api<OpportunityDetail>(`/opportunities/${id}`) });
}

export function useQuickStats() {
  return useQuery({ queryKey: qk.stats, queryFn: () => api<QuickStats>("/opportunities/stats"), refetchInterval: LIVE });
}

export function useBrands() {
  return useQuery({ queryKey: qk.brands, queryFn: () => api<Brand[]>("/brands"), staleTime: 30 * 60_000 });
}

export function useCategories() {
  return useQuery({ queryKey: qk.categories, queryFn: () => api<Category[]>("/categories"), staleTime: 30 * 60_000 });
}

export function useSearch(q: string, page = 1) {
  return useQuery({
    queryKey: ["search", q, page],
    queryFn: ({ signal }) => api<SearchResponse>("/search", { query: { q, page, page_size: 24 }, signal }),
    enabled: q.trim().length > 0,
    placeholderData: keepPreviousData,
  });
}

export function useParsedQuery(q: string) {
  return useQuery({
    queryKey: ["search-parse", q],
    queryFn: ({ signal }) => api<ParsedQuery>("/search/parse", { query: { q }, signal }),
    enabled: q.trim().length > 2,
    staleTime: 60_000,
  });
}

export function usePopularSearches() {
  return useQuery({ queryKey: ["search-popular"], queryFn: () => api<{ query: string; count: number }[]>("/search/popular"), staleTime: 60_000 });
}

export function useSetFavorite() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, state }: { id: string; state: FavoriteState | null }) =>
      state
        ? api(`/opportunities/${id}/state`, { method: "PUT", body: { state } })
        : api(`/opportunities/${id}/state`, { method: "DELETE" }),
    onSuccess: (_d, v) => {
      qc.invalidateQueries({ queryKey: ["opportunities"] });
      qc.invalidateQueries({ queryKey: qk.opportunity(v.id) });
      const labels: Record<string, string> = {
        saved: "Deal saved",
        ignored: "Deal hidden — we'll show fewer like it",
        watching: "Added to watching",
        purchased: "Marked as purchased",
        sold: "Marked as sold",
      };
      toast.success(v.state ? labels[v.state] : "Removed from your list");
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
}

export function useRunAi(id: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => api<AiAnalysis>(`/opportunities/${id}/ai-analysis`, { method: "POST" }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: qk.opportunity(id) });
      toast.success("AI analysis updated");
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
}

export function useWatchlists() {
  return useQuery({ queryKey: qk.watchlists, queryFn: () => api<Watchlist[]>("/watchlists") });
}

export function useSaveWatchlist() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, data }: { id?: string; data: WatchlistInput }) =>
      id ? api<Watchlist>(`/watchlists/${id}`, { method: "PATCH", body: data }) : api<Watchlist>("/watchlists", { method: "POST", body: data }),
    onSuccess: (_d, v) => {
      qc.invalidateQueries({ queryKey: qk.watchlists });
      toast.success(v.id ? "Watchlist updated" : "Watchlist created — you'll be alerted on new matches");
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
}

export function useDeleteWatchlist() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api(`/watchlists/${id}`, { method: "DELETE" }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: qk.watchlists });
      toast.success("Watchlist deleted");
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
}

export function useWatchlistMatches(id: string | null) {
  return useQuery({
    queryKey: ["watchlist-matches", id],
    queryFn: () => api<Page<OpportunityCard>>(`/watchlists/${id}/matches`, { query: { page_size: 12 } }),
    enabled: Boolean(id),
  });
}

export function useAlerts(filters: { unread_only?: boolean; type?: string; page?: number }) {
  return useQuery({
    queryKey: qk.alerts(filters),
    queryFn: () => api<Page<AlertItem>>("/alerts", { query: { ...filters, page_size: 30 } }),
    refetchInterval: LIVE,
    placeholderData: keepPreviousData,
  });
}

export function useUnreadCount() {
  return useQuery({ queryKey: qk.unread, queryFn: () => api<UnreadCount>("/alerts/unread-count"), refetchInterval: 20_000 });
}

export function useMarkRead() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string | "all") =>
      id === "all" ? api("/alerts/read-all", { method: "POST" }) : api(`/alerts/${id}/read`, { method: "POST" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["alerts"] }),
  });
}

export function useFlips() {
  return useQuery({ queryKey: qk.flips, queryFn: () => api<Flip[]>("/flips") });
}

export function usePortfolio() {
  return useQuery({ queryKey: qk.portfolio, queryFn: () => api<Portfolio>("/analytics/portfolio") });
}

function invalidatePortfolio(qc: ReturnType<typeof useQueryClient>) {
  qc.invalidateQueries({ queryKey: qk.flips });
  qc.invalidateQueries({ queryKey: qk.portfolio });
  qc.invalidateQueries({ queryKey: ["opportunities"] });
}

export function useCreatePurchase() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: Record<string, unknown>) => api<Flip>("/purchases", { method: "POST", body }),
    onSuccess: () => {
      invalidatePortfolio(qc);
      toast.success("Purchase recorded");
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
}

export function useUpdatePurchase() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: Record<string, unknown> }) => api<Flip>(`/purchases/${id}`, { method: "PATCH", body }),
    onSuccess: () => invalidatePortfolio(qc),
    onError: (e) => toast.error(errorMessage(e)),
  });
}

export function useDeletePurchase() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api(`/purchases/${id}`, { method: "DELETE" }),
    onSuccess: () => {
      invalidatePortfolio(qc);
      toast.success("Purchase deleted");
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
}

export function useCreateSale() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: Record<string, unknown>) => api<Flip>("/sales", { method: "POST", body }),
    onSuccess: (flip) => {
      invalidatePortfolio(qc);
      toast.success(`Sale recorded · profit ${flip.profit !== null ? `€${flip.profit.toFixed(2)}` : "—"}`);
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
}

export function useDeleteSale() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api(`/sales/${id}`, { method: "DELETE" }),
    onSuccess: () => {
      invalidatePortfolio(qc);
      toast.success("Sale removed — item is back in inventory");
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
}

export function useBrandAnalytics() {
  return useQuery({ queryKey: ["analytics", "brands"], queryFn: () => api<SegmentStats[]>("/analytics/brands", { query: { limit: 40 } }) });
}

export function useCategoryAnalytics() {
  return useQuery({ queryKey: ["analytics", "categories"], queryFn: () => api<SegmentStats[]>("/analytics/categories") });
}

export function useMarketDatabase(params: { brand?: string; category?: string; q?: string; page: number }) {
  return useQuery({
    queryKey: ["analytics", "market", params],
    queryFn: () => api<Page<MarketSegment>>("/analytics/market", { query: { ...params, page_size: 25 } }),
    placeholderData: keepPreviousData,
  });
}

export function useAccuracy() {
  return useQuery({ queryKey: ["analytics", "accuracy"], queryFn: () => api<Accuracy>("/analytics/accuracy"), staleTime: 10 * 60_000 });
}

/** Price data: concluded sales, negotiation discount, external search and measured accuracy. */
export function usePricingEvidence() {
  return useQuery({ queryKey: ["pricing-evidence"], queryFn: () => api<PricingEvidence>("/pricing/evidence"), refetchInterval: 60_000 });
}

/** "Refresh now": queues the evidence sync and the external refresh (rate limited server-side). */
export function useRefreshPricingEvidence() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => api<{ queued: boolean }>("/pricing/evidence/refresh", { method: "POST" }),
    onSuccess: () => {
      // The jobs run in the background: read the status again once they had time to finish.
      void qc.invalidateQueries({ queryKey: ["pricing-evidence"] });
      setTimeout(() => void qc.invalidateQueries({ queryKey: ["pricing-evidence"] }), 15_000);
    },
  });
}

export function useInsights() {
  return useQuery({ queryKey: ["analytics", "insights"], queryFn: () => api<Insights>("/analytics/insights"), refetchInterval: 120_000 });
}

export function usePreferences() {
  return useQuery({ queryKey: ["preferences"], queryFn: () => api<Preferences>("/settings/preferences") });
}

export function useSavePreferences() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: Preferences) => api<Preferences>("/settings/preferences", { method: "PUT", body }),
    onSuccess: () => {
      qc.invalidateQueries();
      toast.success("Settings saved — profits recalculated with your costs");
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
}

export function useNotificationSettings() {
  return useQuery({ queryKey: ["notification-settings"], queryFn: () => api<NotificationSettings>("/settings/notifications") });
}

export function useSaveNotificationSettings() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: NotificationSettings) => api<NotificationSettings>("/settings/notifications", { method: "PUT", body }),
    onSuccess: (data) => {
      qc.setQueryData(["notification-settings"], data);
      toast.success("Notification settings saved");
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
}

export function useSystemStatus() {
  return useQuery({ queryKey: qk.status, queryFn: () => api<SystemStatus>("/system/status"), refetchInterval: 15_000 });
}

export function useTriggerScan() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => api<{ message: string }>("/system/scan", { method: "POST" }),
    onSuccess: (d) => {
      toast.success(d.message);
      setTimeout(() => qc.invalidateQueries(), 4000);
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
}

/** Import a listing found by the user and analyse it now (it then appears in the feed). */
export function useImportListing() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: ManualListingInput) => api<ImportResult>("/listings/import", { method: "POST", body }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["opportunities"] });
      qc.invalidateQueries({ queryKey: qk.stats });
    },
  });
}

export function useItems(filters: ItemFilters) {
  return useQuery({
    queryKey: ["items", filters],
    queryFn: ({ signal }) => api<Page<Item>>("/items", { query: { ...filters }, signal }),
    placeholderData: keepPreviousData,
  });
}

export function useItem(ref: string) {
  return useQuery({
    queryKey: ["item", ref],
    queryFn: ({ signal }) => api<ItemDetail>(`/items/${encodeURIComponent(ref)}`, { signal }),
    retry: (count, err) => (err as { status?: number }).status !== 404 && count < 2,
  });
}

export function useTrackItem() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, track }: { id: string; track: boolean }) =>
      api<{ tracked: boolean }>(`/items/${id}/track`, { method: track ? "POST" : "DELETE" }),
    onSuccess: (r) => {
      qc.invalidateQueries({ queryKey: ["items"] });
      qc.invalidateQueries({ queryKey: ["item"] });
      qc.invalidateQueries({ queryKey: ["opportunities"] });
      toast.success(r.tracked ? "Tracking on: FlipFinder will check its status" : "Tracking off: history kept");
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
}

export function useImportLinks() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (text: string) => api<LinkImportResult>("/listings/import/links", { method: "POST", body: { text } }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["items"] }),
  });
}

export function useAcquisitionStatus() {
  return useQuery({
    queryKey: ["acquisition-status"],
    queryFn: () => api<AcquisitionStatus>("/acquisition/status"),
    refetchInterval: 30_000,
  });
}

export function useUploadEmails() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (files: File[]) => {
      const total: EmailImportResult = { messages: 0, ignored: 0, sold: 0, price_drops: 0, new_items: 0, created: 0 };
      for (const f of files) {
        const r = await api<EmailImportResult>("/acquisition/email", { method: "POST", raw: f, contentType: "message/rfc822" });
        for (const k of Object.keys(total) as (keyof EmailImportResult)[]) total[k] += r[k];
      }
      return total;
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["items"] });
      qc.invalidateQueries({ queryKey: ["acquisition-status"] });
      qc.invalidateQueries({ queryKey: ["opportunities"] });
    },
  });
}

export function useRefreshItem() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api<RefreshResult>(`/items/${id}/refresh`, { method: "POST" }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["item"] });
      qc.invalidateQueries({ queryKey: ["items"] });
    },
  });
}

/** Imports and analyses many listings at once (a Vinted search page); returns them ranked. */
export function useBatchImport() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: BatchImportInput) => api<BatchImportResult>("/listings/import/batch", { method: "POST", body }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["opportunities"] });
      qc.invalidateQueries({ queryKey: qk.stats });
    },
  });
}

/** What-if analysis with the user's costs and targets; nothing is stored. */
export function useQuickAnalysis() {
  return useMutation({
    mutationFn: (body: ManualListingInput) => api<QuickAnalysis>("/analyze", { method: "POST", body }),
  });
}

export function useExtensionKeys() {
  return useQuery({ queryKey: ["extension-keys"], queryFn: () => api<ExtensionKey[]>("/extension/keys") });
}

export function useCreateExtensionKey() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (name: string) => api<ExtensionKeyCreated>("/extension/keys", { method: "POST", body: { name } }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["extension-keys"] }),
  });
}

export function useRevokeExtensionKey() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api<ExtensionKey>(`/extension/keys/${id}`, { method: "DELETE" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["extension-keys"] }),
  });
}
