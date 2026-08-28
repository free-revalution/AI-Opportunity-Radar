import type {
  HealthResponse,
  NotificationsResponse,
  OpportunitiesResponse,
  Opportunity,
  ResearchReportData,
} from "@/types";

/**
 * Centralised HTTP client for the FastAPI backend.
 *
 * In the Docker network the backend is reachable as `http://backend:8000`.
 * Locally (npm run dev outside Docker) it falls back to `localhost:8000`.
 */
const API_BASE_URL =
  process.env.API_BASE_URL_INTERNAL ??
  process.env.NEXT_PUBLIC_API_BASE_URL ??
  "http://localhost:8000";

async function jsonFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const url = `${API_BASE_URL}${path}`;
  const nextOption =
    init && typeof (init as { next?: unknown }).next === "object"
      ? (init as { next?: object }).next
      : undefined;
  const res = await fetch(url, {
    ...init,
    headers: {
      Accept: "application/json",
      ...(init?.headers ?? {}),
    },
    // SSR-friendly defaults: don't cache mutations, do cache GETs for 30s.
    next: { revalidate: 30, ...(nextOption ?? {}) },
  });
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(`API ${res.status} ${res.statusText}: ${text || url}`);
  }
  return (await res.json()) as T;
}

export async function fetchOpportunities(): Promise<OpportunitiesResponse> {
  return jsonFetch<OpportunitiesResponse>("/api/opportunities");
}

export async function fetchOpportunity(id: string): Promise<Opportunity> {
  return jsonFetch<Opportunity>(`/api/opportunities/${encodeURIComponent(id)}`);
}

export async function fetchResearch(id: string): Promise<ResearchReportData> {
  return jsonFetch<ResearchReportData>(`/api/research/${encodeURIComponent(id)}`);
}

export async function fetchHealth(): Promise<HealthResponse> {
  return jsonFetch<HealthResponse>("/api/health");
}

export async function fetchRecentNotifications(
  limit: number = 20,
  channel?: string,
): Promise<NotificationsResponse> {
  const params = new URLSearchParams();
  params.set("limit", String(limit));
  if (channel) params.set("channel", channel);
  return jsonFetch<NotificationsResponse>(
    `/api/notifications/recent?${params.toString()}`,
  );
}

export const apiBaseUrl = API_BASE_URL;

// ---------------------------------------------------------------------------
// Content Center (Phase 3 v2.0)
// ---------------------------------------------------------------------------
import type {
  ContentCenterResponse,
  ContentExportBundleResponse,
  ContentExportJsonResponse,
  ContentExportRequest,
  ContentMarkChannelPublishedRequest,
  ContentRegenerateRequest,
  ContentRegenerateResponse,
} from "@/types";

/** Header value for internal API auth. Browser-side we read it from
 * `NEXT_PUBLIC_RADAR_WEBHOOK_SECRET` (falling back to the literal the
 * dev `.env.example` ships). If unset, the request still goes through —
 * the backend short-circuits auth when no secret is configured. */
const WEBHOOK_SECRET: string | undefined =
  process.env.NEXT_PUBLIC_RADAR_WEBHOOK_SECRET ||
  process.env.NEXT_PUBLIC_APP_SECRET_KEY ||
  undefined;

async function jsonFetchWithSecret<T>(
  path: string,
  init?: RequestInit,
): Promise<T> {
  const url = `${API_BASE_URL}${path}`;
  const headers: Record<string, string> = {
    Accept: "application/json",
    ...(init?.headers as Record<string, string> | undefined),
  };
  if (WEBHOOK_SECRET) {
    headers["X-Radar-Webhook"] = WEBHOOK_SECRET;
  }
  const res = await fetch(url, {
    ...init,
    headers,
    cache: "no-store", // operator-facing data — never stale
  });
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(`API ${res.status} ${res.statusText}: ${text || url}`);
  }
  return (await res.json()) as T;
}

export async function fetchContentCenter(
  only_qualified: boolean = true,
  limit: number = 20,
  channel?: string,
): Promise<ContentCenterResponse> {
  const params = new URLSearchParams({
    only_qualified: String(only_qualified),
    limit: String(limit),
  });
  if (channel) params.set("channel", channel);
  return jsonFetchWithSecret<ContentCenterResponse>(
    `/api/internal/content/by_opportunity?${params.toString()}`,
  );
}

export interface MarkContentResult {
  opportunity_id: number;
  content_status: string;
  commercial_status: string;
  // Phase 8 — full per-channel publish map returned by /mark_published.
  channel_published?: Record<string, string>;
}

export async function markContentPublished(
  opportunityId: number,
  commercialStatus?: string,
): Promise<MarkContentResult> {
  return jsonFetchWithSecret<MarkContentResult>(
    `/api/internal/content/${opportunityId}/mark_published`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(
        commercialStatus ? { commercial_status: commercialStatus } : {},
      ),
    },
  );
}

/** Phase 8 — stamp just ONE channel (no legacy "mark all" fallback). */
export async function markChannelPublished(
  opportunityId: number,
  channel: string,
): Promise<MarkContentResult> {
  return jsonFetchWithSecret<MarkContentResult>(
    `/api/internal/content/${opportunityId}/mark_published`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ channel }),
    },
  );
}

export async function markContentSold(
  opportunityId: number,
  order?: OrderCreatePayload,
): Promise<MarkContentResult & { order?: OrderRecord }> {
  return jsonFetchWithSecret<MarkContentResult & { order?: OrderRecord }>(
    `/api/internal/content/${opportunityId}/mark_sold`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(order ? { order } : {}),
    },
  );
}

/** Phase 8 — regenerate content for a single opportunity. */
export async function regenerateContent(
  opportunityId: number,
  body: ContentRegenerateRequest = {},
): Promise<ContentRegenerateResponse> {
  return jsonFetchWithSecret<ContentRegenerateResponse>(
    `/api/internal/content/regenerate/${opportunityId}`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    },
  );
}

/** Phase 8 — bulk export. Returns the parsed JSON / bundle envelope;
 * for CSV returns the raw text + filename so the caller can stream it
 * straight into a Blob for browser download. */
export async function exportContent(
  body: ContentExportRequest,
): Promise<
  | { format: "csv"; body: string; filename: string }
  | { format: "json"; data: ContentExportJsonResponse; filename: string }
  | { format: "bundle"; data: ContentExportBundleResponse; filename: string }
> {
  if (body.format === "csv") {
    // CSV response is text/csv — bypass jsonFetchWithSecret and stream bytes.
    const url = `${API_BASE_URL}/api/internal/content/export`;
    const headers: Record<string, string> = { Accept: "text/csv" };
    if (WEBHOOK_SECRET) headers["X-Radar-Webhook"] = WEBHOOK_SECRET;
    const res = await fetch(url, {
      method: "POST",
      headers: { ...headers, "Content-Type": "application/json" },
      body: JSON.stringify(body),
      cache: "no-store",
    });
    if (!res.ok) {
      const text = await res.text().catch(() => "");
      throw new Error(`API ${res.status} ${res.statusText}: ${text || url}`);
    }
    const cd = res.headers.get("content-disposition") ?? "";
    const m = /filename="?([^";]+)"?/.exec(cd);
    return {
      format: "csv",
      body: await res.text(),
      filename: m?.[1] ?? "content_export.csv",
    };
  }
  if (body.format === "json") {
    const data = await jsonFetchWithSecret<ContentExportJsonResponse>(
      "/api/internal/content/export",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      },
    );
    return { format: "json", data, filename: "content_export.json" };
  }
  // bundle
  const data = await jsonFetchWithSecret<ContentExportBundleResponse>(
    "/api/internal/content/export",
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    },
  );
  return {
    format: "bundle",
    data,
    filename: "content_export_bundle.json",
  };
}

// ---------------------------------------------------------------------------
// Commercial orders (Phase 4 v2.0)
// ---------------------------------------------------------------------------
// Types live in `@/types` — re-imported here so the api helpers can name
// them in their return signatures without redefining a parallel shape.
import type {
  OrderChannel,
  OrderRecord,
  DeliveryStatus,
  OrderCreatePayload,
  OrderStatsResponse,
} from "@/types";

export type {
  OrderChannel,
  DeliveryStatus,
  OrderCreatePayload,
  OrderRecord,
  OrderStatsResponse,
};

export async function createOrder(
  payload: OrderCreatePayload,
): Promise<OrderRecord> {
  return jsonFetchWithSecret<OrderRecord>(`/api/internal/orders`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export async function fetchOrders(
  params: {
    channel?: string;
    delivery_status?: string;
    opportunity_id?: number;
    limit?: number;
    offset?: number;
  } = {},
): Promise<{
  generated_at: string;
  items: OrderRecord[];
  total: number;
  limit: number;
  offset: number;
}> {
  const search = new URLSearchParams();
  if (params.channel) search.set("channel", params.channel);
  if (params.delivery_status) search.set("delivery_status", params.delivery_status);
  if (typeof params.opportunity_id === "number") {
    search.set("opportunity_id", String(params.opportunity_id));
  }
  search.set("limit", String(params.limit ?? 50));
  search.set("offset", String(params.offset ?? 0));
  const qs = search.toString();
  return jsonFetchWithSecret(`/api/internal/orders${qs ? `?${qs}` : ""}`);
}

export async function fetchOrderStats(): Promise<OrderStatsResponse> {
  return jsonFetchWithSecret<OrderStatsResponse>("/api/internal/orders/stats");
}

export async function fetchOrder(orderId: number): Promise<OrderRecord> {
  return jsonFetchWithSecret<OrderRecord>(
    `/api/internal/orders/${orderId}`,
  );
}

export async function updateOrderStatus(
  orderId: number,
  deliveryStatus:
    | "pending"
    | "delivered"
    | "confirmed"
    | "refunded"
    | "cancelled",
): Promise<OrderRecord> {
  return jsonFetchWithSecret<OrderRecord>(
    `/api/internal/orders/${orderId}/status`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ delivery_status: deliveryStatus }),
    },
  );
}

// ---------------------------------------------------------------------------
// On-demand deep research (Phase 5 v2.0)
// ---------------------------------------------------------------------------
import type {
  OnDemandCreatePayload,
  OnDemandCreateResponse,
  OnDemandDetailResponse,
  OnDemandListResponse,
} from "@/types";

export type {
  OnDemandCreatePayload,
  OnDemandCreateResponse,
  OnDemandDetailResponse,
  OnDemandListResponse,
};

export async function createOnDemandResearch(
  payload: OnDemandCreatePayload,
): Promise<OnDemandCreateResponse> {
  return jsonFetchWithSecret<OnDemandCreateResponse>(
    "/api/internal/research/on_demand",
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    },
  );
}

export async function fetchOnDemandRecent(
  limit: number = 20,
): Promise<OnDemandListResponse> {
  const params = new URLSearchParams({ limit: String(limit) });
  return jsonFetchWithSecret<OnDemandListResponse>(
    `/api/internal/research/on_demand/recent?${params.toString()}`,
  );
}

export async function fetchOnDemandDetail(
  jobId: number,
): Promise<OnDemandDetailResponse> {
  return jsonFetchWithSecret<OnDemandDetailResponse>(
    `/api/internal/research/on_demand/${jobId}`,
  );
}

// ---------------------------------------------------------------------------
// Phase 9 — content editing + version history
// ---------------------------------------------------------------------------
import type {
  ContentEditRequest,
  ContentEditResponse,
  ContentVersionsResponse,
} from "@/types";

/**
 * Phase 9 — create a new version by editing an existing notification.
 *
 * Backend: `POST /api/internal/content/{notification_id}/edit`
 *
 * Behaviour:
 *   - At least one of `body` / `title` / `metadata` must be supplied
 *     (otherwise 422 from the server).
 *   - Returns a NEW Notification row; the original is left intact.
 *     The new row's `payload` carries `edited_from_notification_id` and
 *     `edit_note` so the audit trail is queryable.
 */
export async function editContent(
  notificationId: number,
  body: ContentEditRequest,
): Promise<ContentEditResponse> {
  return jsonFetchWithSecret<ContentEditResponse>(
    `/api/internal/content/${notificationId}/edit`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    },
  );
}

/**
 * Phase 9 — list all versions of generated content for one opportunity,
 * DESC by `created_at` (newest first). Pass `channel` to scope to a single
 * channel (matches the keys used by Content Center: feishu / xianyu /
 * xiaohongshu / wechat_article).
 */
export async function fetchContentVersions(
  opportunityId: number,
  channel?: string,
  limit: number = 50,
): Promise<ContentVersionsResponse> {
  const params = new URLSearchParams({ limit: String(limit) });
  if (channel) params.set("channel", channel);
  return jsonFetchWithSecret<ContentVersionsResponse>(
    `/api/internal/content/${opportunityId}/versions?${params.toString()}`,
  );
}

// ---------------------------------------------------------------------------
// Phase 10 — LLM-as-judge quality scoring
// ---------------------------------------------------------------------------
import type {
  ContentAutoImproveRequest,
  ContentAutoImproveResponse,
  ContentQualityRequest,
  ContentQualityResponse,
} from "@/types";

/**
 * Phase 10 — score one generated piece. Returns 5 sub-scores + weighted
 * total + a one-line rationale. Use `persist: true` to write the score
 * into the notification payload so the next Content Center load shows
 * the badge without re-running the scorer.
 */
export async function fetchContentQuality(
  notificationId: number,
  body: ContentQualityRequest = {},
): Promise<ContentQualityResponse> {
  return jsonFetchWithSecret<ContentQualityResponse>(
    `/api/internal/content/${notificationId}/quality`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    },
  );
}

/**
 * Phase 10 — score + auto-regenerate. Returns the final score envelope
 * plus how many LLM attempts were used and the resulting
 * notification_id (which may be the source if the first attempt already
 * passed).
 */
export async function autoImproveContent(
  notificationId: number,
  body: ContentAutoImproveRequest = {},
): Promise<ContentAutoImproveResponse> {
  return jsonFetchWithSecret<ContentAutoImproveResponse>(
    `/api/internal/content/${notificationId}/auto_improve`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    },
  );
}

// ---------------------------------------------------------------------------
// Phase 11 — one-click publish
// ---------------------------------------------------------------------------
import type {
  BatchPublishRequest,
  BatchPublishResponse,
  PublishChannelsResponse,
  PublishRequest,
  PublishResult,
} from "@/types";

/** Phase 11 — list which channels have a publisher registered and
 * whether each publisher is configured with credentials. */
export async function fetchPublishChannels(): Promise<PublishChannelsResponse> {
  return jsonFetchWithSecret<PublishChannelsResponse>(
    "/api/internal/publish/channels",
  );
}

/** Phase 11 — publish a single notification to its target platform. */
export async function publishNotification(
  notificationId: number,
  body: PublishRequest = {},
): Promise<PublishResult> {
  return jsonFetchWithSecret<PublishResult>(
    `/api/internal/content/${notificationId}/publish`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    },
  );
}

/** Phase 11 — publish a batch of notifications in one call. */
export async function batchPublishNotifications(
  body: BatchPublishRequest,
): Promise<BatchPublishResponse> {
  return jsonFetchWithSecret<BatchPublishResponse>(
    "/api/internal/content/batch_publish",
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    },
  );
}
