export type Recommendation =
  | "strongly_recommend"
  | "recommend"
  | "watch"
  | "not_recommended"
  | "insufficient_data";

export interface Opportunity {
  id: string;
  title: string;
  score: number;
  category?: string;
  summary?: string;
  china_gap?: number;
  execution_score?: number;
  monetization?: string;
  recommendation?: Recommendation;
}

export interface OpportunitiesResponse {
  items: Opportunity[];
  total: number;
  limit: number;
  offset: number;
  generated_at: string;
}

export interface DemandEvidence {
  source: string;
  url: string;
  note?: string;
}

export interface Competitor {
  name: string;
  price?: string;
  weakness?: string;
}

export interface ResearchMVP {
  features: string[];
  estimated_days?: number;
  estimated_cost?: number;
}

export interface ResearchReportData {
  id: string;
  opportunity_id: string;
  executive_summary?: string;
  problem?: string;
  target_customers?: string[];
  demand_evidence?: DemandEvidence[];
  competitors?: Competitor[];
  china_market?: string;
  china_gap?: number;
  monetization?: string[];
  recommended_pricing?: string[];
  mvp?: ResearchMVP;
  risks?: string[];
  recommendation?: Recommendation;
  confidence?: number;
  sources?: { url: string; title?: string }[];
}

export interface HealthResponse {
  status: "healthy" | "degraded" | "down";
  service: string;
  version: string;
  components: Record<string, { status: string; [k: string]: unknown }>;
}