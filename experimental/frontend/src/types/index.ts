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
  // Phase 23 — discriminator lifted out of payload.kind so the
  // `/admin/messages` table can filter + chip without a second
  // round-trip. Older rows written before Phase 23 may have `null`.
  kind?: string | null;
  payload: Record<string, unknown>;
  delivered_at: string | null;
  error: string | null;
  created_at: string;
  // Phase 23 — `true` when the row failed delivery (server-side helper
  // for the red "FAILED" chip in the table).
  failed?: boolean;
  // Phase 23 — admin URL the operator can jump to in one click
  // (e.g. /admin/activation?id=42 for `activation_code_issued` rows).
  // `null` when no resource can be derived from the payload.
  deep_link?: string | null;
}

export interface NotificationsResponse {
  count: number;
  items: NotificationItem[];
}

// Phase 23 — paginated shape returned by GET /api/admin/notifications.
// (The legacy `/api/internal/notifications/history` shape above still
// works for the operator dashboard's recent-activity feed.)
export interface NotificationListResponse {
  items: NotificationItem[];
  total: number;
  limit: number;
  offset: number;
}

// Discriminator constants for the two Phase 23 IM kinds. Adding a new
// kind requires extending `NOTIFICATION_KIND_LABELS` + `NOTIFICATION_KIND_CHIP`
// in `lib/adminCrud.ts` so the messages panel renders it.
export const NOTIFICATION_KINDS = [
  "activation_code_issued",
  "activation_code_resend",
  "subscription_renewal_reminder",
] as const;
export type NotificationKind = (typeof NOTIFICATION_KINDS)[number];

// ---------------------------------------------------------------------------
// Content Center (Phase 3 v2.0)
// ---------------------------------------------------------------------------
export interface ContentPiece {
  notification_id: number;
  channel: string; // feishu | xianyu | xiaohongshu | wechat_article
  title: string;
  body: string;
  metadata: Record<string, unknown>;
  generator: string;
  format: string; // markdown | json | ...
  /** Phase 10 — top-level quality score (when persisted by /quality). */
  quality_score?: ContentQualityScore | null;
  created_at: string | null;
}

export interface ContentCenterOpportunity {
  id: number;
  title: string;
  slug: string;
  summary: string | null;
  total_score: number;
  content_status: string; // new | generated | published | sold
  commercial_status: string; // unqualified | promising | qualified
  target_customer: string | null;
  market_size: string | null;
  mvp_days: number;
  difficulty: string | null;
  monetization_model: string | null;
  china_gap: string | null;
  // Phase 8 — per-channel ✓/○ publish tracking. Keys: feishu / xianyu /
  // xiaohongshu / wechat_article. Value is an ISO-8601 timestamp.
  channel_published?: Record<string, string>;
}

export interface ContentCenterItem {
  opportunity: ContentCenterOpportunity;
  content: Record<string, ContentPiece>; // keyed by channel
}

export interface ContentCenterResponse {
  generated_at: string;
  items: ContentCenterItem[];
}

// ---------------------------------------------------------------------------
// Phase 8 — regenerate, export, per-channel mark_published
// ---------------------------------------------------------------------------
export type ExportFormat = "csv" | "json" | "bundle";

export interface ContentRegenerateRequest {
  generators?: string[];
  delete_previous?: boolean;
}

export interface ContentRegenerateResponse {
  opportunity_id: number;
  regenerated_count: number;
  generators: string[];
  items: Array<{
    generator: string;
    channel: string;
    title: string;
    char_count: number | null;
  }>;
}

export interface ContentExportRequest {
  opportunity_ids?: number[];
  limit?: number;
  only_qualified?: boolean;
  channels?: string[];
  format: ExportFormat;
}

export interface ContentExportCsvResponse {
  // CSV response — content body is the raw CSV text. We expose `body`
  // as a string (not parsed JSON).
  body: string;
  filename: string;
}

export interface ContentExportJsonResponse {
  exported_at: string;
  items: Array<{
    opportunity_id: number;
    opportunity_title: string;
    content: Record<string, ContentPiece>;
  }>;
}

export interface ContentExportBundleResponse {
  exported_at: string;
  files: Array<{
    filename: string;
    content_type: string;
    content: string;
  }>;
}

export interface ContentMarkChannelPublishedRequest {
  channel: string;
  commercial_status?: string;
}

// ---------------------------------------------------------------------------
// Phase 9 — content editing + version history
// ---------------------------------------------------------------------------
export interface ContentEditRequest {
  /** New body — at least one of body / title / metadata must be set. */
  body?: string;
  /** New title. */
  title?: string;
  /** Merged into source metadata (source keys are preserved). */
  metadata?: Record<string, unknown>;
  /** Operator note for the audit trail. */
  edit_note?: string;
}

export interface ContentEditResponse {
  notification_id: number;
  channel: string;
  title: string;
  body: string;
  metadata: Record<string, unknown>;
  edited_from_notification_id: number;
  created_at: string | null;
}

export interface ContentVersionItem {
  notification_id: number;
  channel: string;
  title: string;
  /** First 80 chars of body — for sidebar preview without full load. */
  preview: string;
  char_count: number;
  metadata: Record<string, unknown>;
  /** Set on rows created via POST /content/{id}/edit. */
  edited_from_notification_id?: number | null;
  edit_note?: string | null;
  created_at: string | null;
}

export interface ContentVersionsResponse {
  opportunity_id: number;
  channel: string | null;
  total: number;
  items: ContentVersionItem[];
}

// ---------------------------------------------------------------------------
// Phase 10 — LLM-as-judge quality scoring
// ---------------------------------------------------------------------------
export interface ContentQualityScore {
  hook_strength: number;
  cta_naturalness: number;
  data_accuracy: number;
  char_count_compliance: number;
  platform_style_match: number;
  total: number;
  rationale: string;
  below_threshold: boolean;
  threshold_used: number;
  dimension_floor_used: number;
}

export interface ContentQualityRequest {
  /** Override `DEFAULT_THRESHOLD` for this one call. */
  threshold?: number;
  /** When true, persist the score on the notification payload. */
  persist?: boolean;
}

export interface ContentQualityResponse {
  notification_id: number;
  channel: string;
  title: string;
  score: ContentQualityScore;
}

export interface ContentAutoImproveRequest {
  threshold?: number;
  max_attempts?: number;
  delete_previous?: boolean;
}

export interface ContentAutoImproveResponse {
  notification_id: number;
  channel: string;
  score: ContentQualityScore;
  below_threshold: boolean;
  attempts_used: number;
  max_attempts: number;
  threshold: number;
}

// ---------------------------------------------------------------------------
// Phase 11 — one-click publish
// ---------------------------------------------------------------------------
export interface PublishResult {
  notification_id: number;
  channel: string;
  publisher: string;
  success: boolean;
  skipped: boolean;
  external_id: string | null;
  external_url: string | null;
  error: string | null;
  marked_published: boolean;
}

export interface PublishChannelInfo {
  channel: string;
  publisher: string;
  configured: boolean;
}

export interface PublishChannelsResponse {
  channels: string[];
  configured: PublishChannelInfo[];
  unconfigured: PublishChannelInfo[];
}

export interface BatchPublishRequest {
  notification_ids: number[];
  mark_published?: boolean;
}

export interface BatchPublishResponse {
  requested: number;
  results: Array<{
    publisher: string;
    channel: string;
    success: boolean;
    skipped: boolean;
    external_id: string | null;
    external_url: string | null;
    error: string | null;
  }>;
  marked_published_count: number;
}

export interface PublishRequest {
  channel?: string;
  mark_published?: boolean;
}

// ---------------------------------------------------------------------------
// Commercial orders (Phase 4 v2.0)
// ---------------------------------------------------------------------------
export type OrderChannel =
  | "xianyu"
  | "xiaohongshu"
  | "wechat"
  | "wechat_article"
  | "feishu"
  | "direct"
  | "other";

export type DeliveryStatus =
  | "pending"
  | "delivered"
  | "confirmed"
  | "refunded"
  | "cancelled";

export interface OrderRecord {
  id: number;
  opportunity_id: number;
  opportunity_title?: string | null;
  customer_name: string;
  customer_contact?: string | null;
  amount_cny: number;
  channel: string; // widened on the wire — backend returns OrderChannel
  payment_method?: string | null;
  payment_reference?: string | null;
  delivery_status: DeliveryStatus;
  commercial_status_snapshot?: string | null;
  notes?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface OrderStatsResponse {
  total_orders: number;
  total_revenue_cny: number;
  delivered_count: number;
  confirmed_count: number;
  pending_count: number;
  by_channel: Array<{ channel: string; count: number; revenue_cny: number }>;
  by_delivery_status: Record<string, number>;
}

export interface OrderListResponse {
  generated_at: string;
  items: OrderRecord[];
  total: number;
  limit: number;
  offset: number;
}

export interface OrderCreatePayload {
  customer_name: string;
  customer_contact?: string | null;
  amount_cny: number;
  channel: OrderChannel;
  payment_method?: string | null;
  payment_reference?: string | null;
  delivery_status?: DeliveryStatus;
  notes?: string | null;
  mark_opportunity_sold?: boolean;
}

// ---------------------------------------------------------------------------
// On-demand deep research (Phase 5 v2.0)
// ---------------------------------------------------------------------------
export type OnDemandSeedKind = "url" | "topic";

export interface OnDemandCreatePayload {
  url?: string;
  topic?: string;
  // Optional in-line order — the same fields as OrderCreatePayload minus
  // `mark_opportunity_sold` (the on-demand path always flips the opp).
  customer_name?: string;
  customer_contact?: string;
  amount_cny?: number;
  channel?: OrderChannel;
  payment_method?: string;
  payment_reference?: string;
  notes?: string;
}

export interface OnDemandCreateResponse {
  opportunity_id: number;
  opportunity_title: string;
  opportunity_slug: string;
  job_id: number;
  status: "pending" | "running" | "completed" | "failed";
  recommendation?: Recommendation | null;
  confidence: number;
  sources_count: number;
  executive_summary?: string | null;
  order_id?: number | null;
}

export interface OnDemandListItem {
  job_id: number;
  opportunity_id: number;
  status: string;
  recommendation?: Recommendation | null;
  confidence: number;
  sources_count: number;
  error?: string | null;
  seed_url?: string | null;
  seed_topic?: string | null;
  executive_summary?: string | null;
  started_at?: string | null;
  completed_at?: string | null;
}

export interface OnDemandListResponse {
  generated_at: string;
  items: OnDemandListItem[];
  total: number;
}

export interface OnDemandDetailResponse {
  job_id: number;
  opportunity_id: number;
  opportunity_title: string;
  status: string;
  recommendation?: Recommendation | null;
  confidence: number;
  sources_count: number;
  error?: string | null;
  started_at?: string | null;
  completed_at?: string | null;
  seed_url?: string | null;
  seed_topic?: string | null;
  report: ResearchReportData | null;
}

// ---------------------------------------------------------------------------
// Phase 18 — admin Content Center (mirrors backend `_serialize_content_opportunity`)
// ---------------------------------------------------------------------------
export type ContentOpportunityStatus =
  | "draft"
  | "approved"
  | "published"
  | "rejected"
  | "archived";

export interface ContentOpportunity {
  id: number;
  signal_id: number;
  platform: string;
  audience?: string | null;
  niche?: string | null;
  tone?: string | null;
  content_angle?: string | null;
  hook?: string | null;
  title_candidates?: string[] | null;
  material_ideas?: string[] | null;
  script_outline?: string | null;
  recommended_length?: number | null;
  cta?: string | null;
  risk_warning?: string | null;
  content_score: number;
  status: ContentOpportunityStatus;
  // Compliance verdict projected from `metadata_json` by the backend.
  compliance_blocked: boolean;
  compliance_risk_score: number;
  compliance_risk_types: string[];
  metadata: Record<string, unknown>;
  created_at: string | null;
  updated_at: string | null;
}

export interface ContentOpportunityListResponse {
  items: ContentOpportunity[];
  total: number;
  limit: number;
  offset: number;
}

export interface ContentOpportunityRejectRequest {
  reason?: string | null;
}

// ---------------------------------------------------------------------------
// Phase 18 — admin Signal browser (mirrors backend `/api/signals`)
// ---------------------------------------------------------------------------
export type SignalLifecycleStatus =
  | "discovered"
  | "validating"
  | "verified"
  | "analyzing"
  | "published"
  | "expired"
  | "rejected";

export interface Signal {
  id: number;
  raw_item_id: number;
  signal_type?: string | null;
  keyword?: string | null;
  category?: string | null;
  title?: string | null;
  summary?: string | null;
  signal_score: number;
  confidence_score: number;
  status: SignalLifecycleStatus;
  compliance_status?: string | null;
  risk_score: number;
  created_at: string | null;
}

export interface SignalListResponse {
  items: Signal[];
  total: number;
  limit: number;
  offset: number;
}

// ---------------------------------------------------------------------------
// Phase 19 — admin dashboard summary
// ---------------------------------------------------------------------------
export interface DashboardActivityItem {
  id: number;
  actor_type: string;
  actor_id: string | null;
  action: string;
  resource_type: string | null;
  resource_id: string | null;
  result: string;
  // Backend stores it as `metadata_json`; frontend renames to
  // `metadata` so the wire payload matches the JS naming convention.
  metadata_json: Record<string, unknown>;
  created_at: string | null;
}

export interface DashboardContentStats {
  total: number;
  by_status: Record<ContentOpportunityStatus, number>;
  /** Drafts marked compliance_blocked=true (review queue size). */
  blocked_review_queue: number;
  recent_7d_count: number;
  new_today: number;
}

export interface DashboardSignalStats {
  total: number;
  by_status: Record<SignalLifecycleStatus, number>;
  recent_7d_count: number;
  new_today: number;
  verified_count: number;
}

export interface DashboardResponse {
  generated_at: string;
  content_opportunities: DashboardContentStats;
  signals: DashboardSignalStats;
  recent_activity: DashboardActivityItem[];
}

// ---------------------------------------------------------------------------
// Phase 20 — /admin/audit-logs viewer
// ---------------------------------------------------------------------------
/** Mirrors the backend AuditLog row. `metadata_json` keeps the backend's
 * wire naming — we don't rename it in the dashboard feed either. */
export interface AuditLogItem {
  id: number;
  actor_type: string;
  actor_id: string | null;
  action: string;
  resource_type: string | null;
  resource_id: string | null;
  result: string;
  metadata_json: Record<string, unknown> | null;
  created_at: string | null;
}

export interface AuditLogsResponse {
  items: AuditLogItem[];
  /** Total matching the filter (independent of limit/offset). */
  total: number;
  limit: number;
  offset: number;
}

export interface AuditLogFilters {
  actor_type?: string;
  actor_id?: string;
  action?: string;
  result?: string;
  resource_type?: string;
  resource_id?: string;
  since?: string;
  until?: string;
  limit?: number;
  offset?: number;
}

// ---------------------------------------------------------------------------
// Phase 24 — Compliance Engine operator surface
// ---------------------------------------------------------------------------
/** Risk-level values the backend emits in `metadata_json.risk_level`. */
export const COMPLIANCE_RISK_LEVELS = ["low", "medium", "high", "blocked"] as const;
export type ComplianceRiskLevel = (typeof COMPLIANCE_RISK_LEVELS)[number];

/** Risk-type values the backend emits in `metadata_json.risk_types`. */
export const COMPLIANCE_RISK_TYPES = [
  "pii",
  "prompt_injection",
  "content_safety",
  "copyright",
  "source_policy",
] as const;
export type ComplianceRiskType = (typeof COMPLIANCE_RISK_TYPES)[number];

/** One row from `GET /api/admin/compliance`. */
export interface ComplianceAuditItem {
  id: number;
  actor_id: string | null;
  resource_type: string | null;
  resource_id: string | null;
  risk_level: ComplianceRiskLevel | null;
  risk_types: ComplianceRiskType[];
  risk_score: number | null;
  reason: string;
  requires_human_review: boolean;
  context: string;
  overridden: boolean;
  override_reason: string | null;
  created_at: string | null;
}

export interface ComplianceAuditResponse {
  items: ComplianceAuditItem[];
  /** Total matching the filter (independent of limit/offset). */
  total: number;
  limit: number;
  offset: number;
}

export interface ComplianceAuditFilters {
  risk_level?: ComplianceRiskLevel | "";
  risk_type?: ComplianceRiskType | "";
  resource_type?: string;
  since?: string;
  limit?: number;
  offset?: number;
}

export interface ComplianceOverrideResponse {
  ok: boolean;
  original_audit_log_id: number;
  override_audit_log_id: number;
}

// ---------------------------------------------------------------------------
// Phase 22 — admin Activation / Subscription / Source CRUD types
// ---------------------------------------------------------------------------
export type ActivationStatusValue =
  | "unused"
  | "active"
  | "expired"
  | "revoked";

export interface ActivationCode {
  id: number;
  plan: string;
  status: ActivationStatusValue;
  expires_at: string | null;
  bound_feishu_open_id: string | null;
  bound_at: string | null;
  created_at: string | null;
  used_at: string | null;
}

export interface ActivationListResponse {
  count: number;
  items: ActivationCode[];
}

export interface ActivationIssueRequest {
  plan: string;
  ttl_days?: number;
  // Phase 23 — destination Feishu open_id. When set + `send_im` true,
  // the backend IM's the plaintext to the user via /im/v1/messages
  // right after the row commits.
  feishu_open_id?: string;
  // Phase 23 — operator toggle. Default true. False = hand-deliver.
  send_im?: boolean;
}

// Phase 23 — server's IM delivery status for the issue call. `null`
// when no auto-IM was attempted (operator toggle off, no open_id,
// or the `send_activation_code_via_im` kill-switch is flipped).
export interface ActivationImSend {
  sent: boolean;
  message_id?: string | null;
  error?: string | null;
}

export interface ActivationIssueResponse extends ActivationCode {
  /** Plaintext code — returned ONCE by the backend. Caller must display
   * it to the operator immediately; never re-fetchable. */
  code: string;
  // Phase 23 — see `ActivationImSend`. Null on no-op paths.
  im_send?: ActivationImSend | null;
}

// Phase 23 — `POST /api/admin/activation/{id}/resend` returns the
// `ActivationImSend` envelope directly. Backend only ever IM's a
// "please contact us" hint card — plaintext recovery is impossible
// (codes are hashed at rest).
export type ActivationResendResponse = ActivationImSend & { id: number };

export type SubscriptionStatusValue =
  | "active"
  | "expired"
  | "suspended"
  | "cancelled";

export interface Subscription {
  id: number;
  user_id: number | null;
  feishu_open_id: string | null;
  plan: string;
  status: SubscriptionStatusValue;
  source_channel: string | null;
  starts_at: string | null;
  expires_at: string | null;
  created_at: string | null;
  updated_at: string | null;
}

export interface SubscriptionListResponse {
  count: number;
  items: Subscription[];
}

export interface SubscriptionExtendRequest {
  days: number;
}

export type ComplianceLevelValue = "A" | "B" | "C" | "D" | "E";

export interface Source {
  id: number;
  name: string;
  type: string;
  url: string | null;
  enabled: boolean;
  compliance_level: ComplianceLevelValue;
  commercial_use_status: string | null;
  access_method: string | null;
  retention_policy: string | null;
  source_block_reason: string | null;
  last_compliance_check: string | null;
}

export interface SourceListResponse {
  count: number;
  items: Source[];
}

export interface SourceComplianceUpdateRequest {
  compliance_level: ComplianceLevelValue;
  retention_policy?: string | null;
  source_block_reason?: string | null;
}
