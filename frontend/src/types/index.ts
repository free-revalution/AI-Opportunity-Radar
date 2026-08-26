export type Recommendation =
  | "strongly_recommend"
  | "recommend"
  | "watch"
  | "not_recommended"
  | "insufficient_data";

export type OpportunityStatus =
  | "detected"
  | "screened"
  | "scored"
  | "research_eligible"
  | "research_complete"
  | "screen_failed"
  | "failed";

export interface Opportunity {
  id: string;
  slug: string;
  title: string;
  score: number;
  total_score?: number;
  summary?: string;
  category?: string;
  market?: string;
  target_user?: string;
  source_count?: number;
  status?: OpportunityStatus;

  // Sub-scores — all optional for backward compatibility with demo data.
  trend_score?: number;
  demand_score?: number;
  monetization_score?: number;
  competition_gap_score?: number;
  china_gap_score?: number;
  execution_score?: number;

  // Legacy aliases used by the demo seed.
  china_gap?: number;
  execution_score_legacy?: number;
  monetization?: string;
  recommendation?: Recommendation;

  created_at?: string;
  updated_at?: string;
}

export interface OpportunitiesResponse {
  items: Opportunity[];
  total: number;
  limit: number;
  offset: number;
  generated_at: string;
}

export interface ResearchReportSource {
  url: string;
  title?: string;
}

export interface ResearchReportData {
  id: string;
  opportunity_id: string;

  // New Phase 7 schema — all seven sections.
  executive_summary?: string;
  market_analysis?: string;
  competition_analysis?: string;
  china_analysis?: string;
  monetization_analysis?: string;
  mvp_analysis?: string;
  risk_analysis?: string;

  recommendation?: Recommendation;
  confidence?: number;
  sources?: ResearchReportSource[];

  // `pending=true` when no Phase 7 report exists yet — the API returns
  // a synthesised fallback. The dashboard surfaces a friendly CTA.
  pending?: boolean;
}

// Legacy aliases (kept for any consumers still on the old shape).
export type DemandEvidence = { source: string; url: string; note?: string };
export type Competitor = {
  name: string;
  price?: string;
  weakness?: string;
};
export type ResearchMVP = {
  features: string[];
  estimated_days?: number;
  estimated_cost?: number;
};

export interface HealthComponent {
  status: string;
  [k: string]: unknown;
}

export interface HealthResponse {
  status: "healthy" | "degraded" | "down";
  service: string;
  version: string;
  components: Record<string, HealthComponent>;
}

export interface NotificationItem {
  id: number;
  channel: string;
  payload: Record<string, unknown>;
  delivered_at: string | null;
  error: string | null;
  created_at: string;
}

export interface NotificationsResponse {
  count: number;
  items: NotificationItem[];
}
