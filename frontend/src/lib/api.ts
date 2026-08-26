import type {
  HealthResponse,
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
  const res = await fetch(url, {
    ...init,
    headers: {
      Accept: "application/json",
      ...(init?.headers ?? {}),
    },
    // SSR-friendly defaults: don't cache mutations, do cache GETs for 30s.
    next: { revalidate: 30, ...(init as { next?: unknown })?.next },
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

export const apiBaseUrl = API_BASE_URL;