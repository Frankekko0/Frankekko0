// Types mirroring the backend API schemas (app/schemas/*.py).

export type Uuid = string;

export interface Page<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
  has_more: boolean;
}

export interface User {
  id: Uuid;
  email: string;
  display_name: string | null;
  created_at: string;
}

export interface AuthConfig {
  registration_enabled: boolean;
  demo_login_enabled: boolean;
  demo_user_email: string | null;
}

export interface Session {
  user: User;
  csrf_token: string;
  expires_in: number;
}

export interface BrandRef {
  slug: string;
  name: string;
}

export interface CategoryRef {
  slug: string;
  name: string;
  name_it?: string | null;
}

export interface Reason {
  type: "positive" | "negative" | "neutral" | "info";
  code: string;
  label: string;
  impact: number | null;
}

export type DealTier = "exceptional" | "excellent" | "good" | "moderate" | "low_priority";
export type RiskLevel = "low" | "moderate" | "high" | "very_high";
export type DemandLevel = "very_low" | "low" | "medium" | "high" | "very_high";
export type FavoriteState = "saved" | "ignored" | "purchased" | "watching" | "sold";
export type Action = "buy_now" | "make_offer" | "watch" | "skip";

export interface OpportunityCard {
  id: Uuid;
  listing_id: Uuid;
  title: string;
  brand: BrandRef | null;
  category: CategoryRef | null;
  model_name: string | null;
  size: string | null;
  condition: string;
  country: string | null;
  image_url: string | null;
  url: string;
  is_active: boolean;
  listing_status: string;
  listing_price: number;
  currency: string;
  total_acquisition_cost: number;
  fair_market_value: number | null;
  expected_sale_price: number | null;
  expected_profit: number | null;
  expected_roi: number | null;
  discount_vs_market: number | null;
  flip_score: number;
  personal_flip_score: number | null;
  confidence_score: number;
  risk_score: number;
  risk_level: RiskLevel;
  demand_level: DemandLevel | null;
  velocity_score: number | null;
  estimated_days_to_sell: number | null;
  velocity_bucket: string | null;
  deal_tier: DealTier;
  is_ultra_deal: boolean;
  verdict: "BUY" | "CONSIDER" | "SKIP";
  recommended_action: Action;
  published_at: string | null;
  analyzed_at: string;
  favorite_state: FavoriteState | null;
  previous_price: number | null;
  top_reasons: Reason[];
}

export interface QuickStats {
  opportunities_today: number;
  average_expected_roi: number | null;
  potential_profit: number;
  ultra_deals: number;
  listings_analyzed: number;
  active_opportunities: number;
  listings_tracked: number;
  last_scan_at: string | null;
}

export interface CostLine {
  label: string;
  amount: number;
}

export interface Scenario {
  name: "conservative" | "expected" | "optimistic";
  sale_price: number;
  total_acquisition_cost: number;
  net_sale_revenue: number;
  net_profit: number;
  roi: number;
  estimated_days: number | null;
  acquisition_breakdown: CostLine[];
  sale_breakdown: CostLine[];
}

export interface SmartBuy {
  max_buy_price: number | null;
  good_buy_price: number | null;
  suggested_offer: number | null;
  listed_price: number;
  action: Action;
  rationale: string;
  min_profit: number;
  min_roi: number;
}

export interface Comparable {
  listing_id: Uuid;
  title: string;
  url: string;
  price: number;
  adjusted_price: number;
  condition: string;
  size: string | null;
  country: string | null;
  status: string;
  similarity: number;
  is_sold: boolean;
  included: boolean;
  exclusion_reason: string | null;
  listing_date: string | null;
  sold_at: string | null;
  image_url: string | null;
}

export interface Attribute {
  value: string | null;
  certainty: "certain" | "probable" | "unverifiable";
  source: string | null;
  confidence: number;
  name?: string | null;
}

export interface Identification {
  brand: Attribute;
  line: Attribute;
  model: Attribute;
  category: Attribute;
  gender: Attribute;
  color: Attribute;
  size: Attribute;
  material: Attribute;
  seasonality: Attribute;
  season: Attribute;
  team: Attribute;
  product_code: Attribute;
  authenticity: Attribute;
  is_vintage: boolean;
  suspicious_terms: string[];
  defect_terms: string[];
  evidence: string[];
  confidence: number;
  photos_reused_by_other_seller?: boolean;
  vision?: Record<string, unknown>;
}

export interface RiskFactor {
  code: string;
  label: string;
  points: number;
  severity: "info" | "low" | "medium" | "high";
}

export interface ScoreComponent {
  score: number;
  weight: number;
  contribution: number;
}

export interface AiAnalysis {
  verdict: "BUY" | "CONSIDER" | "SKIP";
  summary: string;
  pros: string[];
  cons: string[];
  risks: string[];
  recommended_resale_price: number | null;
  suggested_max_offer: number | null;
  provider: string;
  model: string | null;
}

export interface Seller {
  username: string | null;
  rating: number | null;
  review_count: number;
  account_created_at: string | null;
  item_count: number | null;
  sold_count: number | null;
  country: string | null;
  reliability_score: number | null;
  reliability: { score: number; level: string; smoothed_rating: number | null; factors: { label: string; impact: number }[] } | null;
}

export interface ListingDetail {
  id: Uuid;
  external_id: string;
  provider: string;
  url: string;
  title: string;
  description: string;
  price: number;
  currency: string;
  brand_raw: string | null;
  category_raw: string | null;
  size_raw: string | null;
  condition_raw: string | null;
  condition: string;
  color: string | null;
  material: string | null;
  country: string | null;
  status: string;
  published_at: string | null;
  first_seen_at: string;
  last_seen_at: string;
  favourite_count: number;
  view_count: number;
  shipping_fee: number | null;
  buyer_protection_fee: number | null;
  buyer_protection_available: boolean;
  is_vintage: boolean;
  images: { url: string; position: number }[];
  seller: Seller | null;
  duplicate_of_id: Uuid | null;
}

export interface DistributionStats {
  n: number;
  median: number;
  mean: number;
  p10: number;
  p25: number;
  p75: number;
  p90: number;
  min_reasonable: number;
  max_reasonable: number;
  dispersion: number;
}

export interface MarketInfo {
  fair_market_value: number | null;
  median: number | null;
  mean: number | null;
  p25: number | null;
  p75: number | null;
  min_reasonable: number | null;
  max_reasonable: number | null;
  quick_sale_price: number | null;
  expected_sale_price: number | null;
  optimistic_sale_price: number | null;
  listing_price: number;
  discount_vs_market: number | null;
  discount_vs_median: number | null;
  n_used: number | null;
  n_sold: number | null;
  n_active: number | null;
  n_outliers: number | null;
  avg_similarity: number | null;
  ask_to_sale_ratio: number | null;
  confidence: number | null;
  confidence_breakdown: Record<string, number> | null;
  notes: string[] | null;
  histogram: { start: number; end: number; count: number }[] | null;
  sold_stats: DistributionStats | null;
  active_stats: DistributionStats | null;
  used_prior: boolean | null;
}

export interface OpportunityDetail {
  card: OpportunityCard;
  listing: ListingDetail;
  identification: Identification | null;
  market: MarketInfo;
  comparables: Comparable[];
  demand: { level: DemandLevel | null; score: number | null; sell_through_rate: number | null; observations?: number; pool_size?: number };
  velocity: { estimated_days: number | null; bucket: string | null; score: number | null; sample_size?: number; quick_sale_days?: number; optimistic_sale_days?: number };
  scenarios: Scenario[];
  smart_buy: SmartBuy;
  risk: { score: number; level: RiskLevel; factors: RiskFactor[] };
  score: {
    flip_score: number;
    personal_flip_score: number | null;
    confidence_score: number;
    deal_tier: DealTier;
    is_ultra_deal: boolean;
    components: Record<string, ScoreComponent> | null;
    penalties: { code: string; label: string; points: number }[] | null;
    cap: { max: number; reason: string } | null;
    base: number | null;
    confidence_components: Record<string, number> | null;
    algorithm_version: string;
    analyzed_at: string;
  };
  explanation: Reason[];
  ai_analysis: AiAnalysis | null;
  price_history: { price: number; observed_at: string }[];
  recommended_action: Action;
}

export interface OpportunityFilters {
  q?: string;
  brands?: string[];
  categories?: string[];
  sizes?: string[];
  conditions?: string[];
  countries?: string[];
  demand_levels?: string[];
  min_price?: number;
  max_price?: number;
  min_profit?: number;
  min_roi?: number;
  min_flip?: number;
  min_confidence?: number;
  max_risk?: number;
  min_velocity?: number;
  published_within_hours?: number;
  vintage_only?: boolean;
  ultra_only?: boolean;
  include_inactive?: boolean;
  state?: FavoriteState;
  preset?: string;
  sort?: string;
  page?: number;
  page_size?: number;
}

export interface Brand {
  slug: string;
  name: string;
  tier: string;
  counterfeit_risk: number;
}

export interface Category {
  slug: string;
  name: string;
  name_it: string;
  parent: string | null;
}

export interface Watchlist {
  id: Uuid;
  name: string;
  query: string | null;
  brand_slugs: string[];
  category_slugs: string[];
  sizes: string[];
  conditions: string[];
  countries: string[];
  vintage_only: boolean;
  max_buy_price: number | null;
  min_profit: number | null;
  min_roi: number | null;
  min_flip_score: number | null;
  min_confidence: number | null;
  max_risk_score: number | null;
  is_active: boolean;
  notify: boolean;
  created_at: string;
  updated_at: string;
  last_matched_at: string | null;
  match_count: number | null;
}

export type WatchlistInput = Omit<Watchlist, "id" | "created_at" | "updated_at" | "last_matched_at" | "match_count">;

export interface AlertItem {
  id: Uuid;
  type: "new_opportunity" | "ultra_deal" | "price_drop" | "watchlist_match" | "system";
  priority: "normal" | "high";
  title: string;
  body: string;
  payload: Record<string, unknown>;
  opportunity_id: Uuid | null;
  listing_id: Uuid | null;
  watchlist_id: Uuid | null;
  read_at: string | null;
  created_at: string;
  deliveries: { channel: string; status: string; attempts: number; sent_at: string | null }[];
}

export interface UnreadCount {
  unread: number;
  latest_high_priority: AlertItem | null;
}

export interface Sale {
  id: Uuid;
  purchase_id: Uuid;
  sale_price: number;
  selling_fees: number;
  shipping_cost: number;
  packaging_cost: number;
  other_costs: number;
  net_revenue: number;
  profit: number;
  roi: number;
  holding_days: number;
  sale_date: string;
  platform: string;
  notes: string | null;
  created_at: string;
}

export interface Flip {
  purchase_id: Uuid;
  title: string;
  brand: string | null;
  category: string | null;
  size: string | null;
  condition: string | null;
  purchase_price: number;
  total_cost: number;
  purchase_date: string;
  expected_sale_price: number | null;
  opportunity_id: Uuid | null;
  status: "in_stock" | "listed" | "sold" | "returned";
  listed_price: number | null;
  sale: Sale | null;
  profit: number | null;
  roi: number | null;
  holding_days: number | null;
  notes: string | null;
}

export interface Portfolio {
  total_invested: number;
  inventory_items: number;
  inventory_cost: number;
  inventory_value: number;
  revenue: number;
  profit: number;
  average_roi: number | null;
  average_holding_days: number | null;
  win_rate: number | null;
  flips_completed: number;
  purchases: number;
  monthly: { month: string; profit: number; revenue: number; sales: number; invested: number }[];
  best_flip: { title: string | null; profit: number; roi: number } | null;
}

export interface SegmentStats {
  slug: string;
  name: string;
  name_it?: string;
  tier?: string;
  segment?: boolean;
  listings_analyzed: number;
  opportunities: number;
  opportunity_rate: number;
  average_roi: number | null;
  average_profit: number | null;
  average_flip_score: number | null;
  best_flip_score: number | null;
  average_days_to_sell: number | null;
  sell_through: number | null;
  market_sample: number;
  flip_index: number;
}

export interface MarketSegment {
  segment_key: string;
  brand: BrandRef;
  category: CategoryRef;
  model_name: string | null;
  size: string | null;
  sample_size: number;
  sold_count: number;
  active_count: number;
  median_price: number;
  p25_price: number;
  p75_price: number;
  min_reasonable_price: number;
  max_reasonable_price: number;
  avg_listing_price: number;
  sell_through_rate: number;
  avg_days_to_sale: number | null;
  computed_at: string;
}

export interface Insights {
  top_brands: SegmentStats[];
  hottest_categories: SegmentStats[];
  price_drops: { opportunity_id: Uuid; title: string; price: number; previous_price: number; drop_pct: number; flip_score: number }[];
  your_best_segments: { dimension: string; key: string; flips: number; avg_roi: number | null; adjustment: number }[];
}

export interface CostProfile {
  buyer_protection_fixed: number;
  buyer_protection_pct: number;
  shipping_in: number;
  use_listing_shipping: boolean;
  other_acquisition: number;
  selling_fee_fixed: number;
  selling_fee_pct: number;
  shipping_out: number;
  packaging: number;
  advertising: number;
  payment_fee_fixed: number;
  payment_fee_pct: number;
  other_sale: number;
}

export interface Preferences {
  preferred_brands: string[];
  preferred_categories: string[];
  sizes: string[];
  min_profit: number;
  min_roi: number;
  max_purchase_price: number | null;
  min_flip_score: number | null;
  max_risk_score: number | null;
  min_confidence: number | null;
  cost_profile: CostProfile;
  score_weights: Record<string, number> | null;
  personalization_enabled: boolean;
}

export interface NotificationSettings {
  in_app_enabled: boolean;
  web_push_enabled: boolean;
  email_enabled: boolean;
  email_address: string | null;
  telegram_enabled: boolean;
  telegram_chat_id: string | null;
  discord_enabled: boolean;
  discord_webhook_url: string | null;
  new_opportunity_alerts: boolean;
  ultra_deal_alerts: boolean;
  price_drop_alerts: boolean;
  watchlist_alerts: boolean;
  alert_min_flip_score: number;
  alert_min_roi: number;
  alert_min_profit: number;
  alert_min_confidence: number;
  alert_max_risk_score: number | null;
  available_channels?: { in_app: boolean; web_push: boolean; email: boolean; telegram: boolean; discord: boolean };
  vapid_public_key?: string | null;
}

export interface ParsedQuery {
  filters: OpportunityFilters;
  understood: string[];
  remaining_text: string;
}

export interface SearchResponse {
  query: string;
  parsed: ParsedQuery;
  results: Page<OpportunityCard>;
}

export interface SystemStatus {
  provider: { name: string; capabilities: Record<string, boolean>; demo_mode: boolean };
  scanner: { interval_seconds: number; last_run: Record<string, unknown> | null };
  queues: { high?: number; default?: number };
  listings_tracked: number;
  listings_active: number;
  active_opportunities: number;
  ai: { enabled: boolean; model: string | null };
  algorithm_version: string;
}

/** Manual import / quick check of a listing the user found (POST /listings/import, /analyze). */
export interface ManualListingInput {
  url: string;
  title: string;
  price: number;
  brand?: string;
  category?: string;
  size?: string;
  condition?: string;
  color?: string;
  description?: string;
  image_urls?: string[];
  seller_username?: string;
  seller_rating?: number;
  seller_review_count?: number;
  shipping_fee?: number;
}

export interface BatchImportInput {
  items: ManualListingInput[];
  source: "vinted_search" | "manual";
}

export interface BatchImportResult {
  received: number;
  unique: number;
  imported: number;
  updated: number;
  /** Re-posts of listings FlipFinder already knew. */
  reposts: number;
  price_drops: number;
  analyzed: number;
  /** Best opportunity first, with your costs and targets. */
  items: OpportunityCard[];
}

export interface ImportResult {
  listing_id: Uuid;
  opportunity_id: Uuid;
  flip_score: number;
  is_new: boolean;
}

export interface QuickAnalysis {
  fair_market_value: number | null;
  market: { n_used: number; n_sold: number; n_active: number; n_outliers: number; notes: string[] };
  scenarios: {
    name: "conservative" | "expected" | "optimistic";
    sale_price: number;
    net_profit: number;
    roi: number | null;
    total_acquisition_cost: number;
    net_sale_revenue: number;
  }[];
  max_buy_price: number | null;
  good_buy_price: number | null;
  offer: { suggested_offer: number | null; action: string; rationale: string };
  flip_score: number;
  confidence_score: number;
  risk: { score: number; level: RiskLevel; factors: { code: string; label: string; points: number }[] };
  demand: { level: string; sell_through_rate: number };
  velocity: { days: number; bucket: string };
  explanation: Reason[];
  ai_analysis: { verdict: "BUY" | "CONSIDER" | "SKIP"; summary: string };
  comparables_used: number;
}
