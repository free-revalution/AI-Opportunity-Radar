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
import type { ContentCenterResponse } from "@/types";

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
): Promise<ContentCenterResponse> {
  const params = new URLSearchParams({
    only_qualified: String(only_qualified),
    limit: String(limit),
  });
  return jsonFetchWithSecret<ContentCenterResponse>(
    `/api/internal/content/by_opportunity?${params.toString()}`,
  );
}

export interface MarkContentResult {
  opportunity_id: number;
  content_status: string;
  commercial_status: string;
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
