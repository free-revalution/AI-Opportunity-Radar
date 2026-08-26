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
