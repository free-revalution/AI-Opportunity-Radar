import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// ---- Mock next/navigation ------------------------------------------------
const mockNav = vi.hoisted(() => ({
  pathname: "/admin/content-opportunities",
  searchParams: new URLSearchParams(""),
  push: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: mockNav.push }),
  usePathname: () => mockNav.pathname,
  useSearchParams: () => mockNav.searchParams,
}));

const fetchContentOpportunities = vi.fn();

vi.mock("@/lib/api", () => ({
  fetchContentOpportunities: (...args: unknown[]) =>
    (fetchContentOpportunities as (...args: unknown[]) => unknown)(...args),
}));

import { ContentOpportunitiesPanel } from "@/components/ContentOpportunitiesPanel";
import type { ContentOpportunity } from "@/types";

// ---- Fixtures ------------------------------------------------------------
function makeCO(overrides: Partial<ContentOpportunity> = {}): ContentOpportunity {
  return {
    id: 1,
    signal_id: 42,
    platform: "xiaohongshu",
    audience: "creators",
    niche: "AI",
    tone: "专业",
    content_angle: "AI 颠覆跨境",
    hook: "AI 颠覆跨境电商",
    title_candidates: ["跨境 AI 工具"],
    material_ideas: ["市场数据"],
    script_outline: "1. 引子\n2. 痛点\n3. 方案",
    recommended_length: 600,
    cta: "评论留言",
    risk_warning: null,
    content_score: 88,
    status: "draft",
    compliance_blocked: false,
    compliance_risk_score: 0,
    compliance_risk_types: [],
    metadata: {},
    created_at: "2026-08-27T10:00:00Z",
    updated_at: "2026-08-27T10:00:00Z",
    ...overrides,
  };
}

beforeEach(() => {
  mockNav.pathname = "/admin/content-opportunities";
  mockNav.searchParams = new URLSearchParams("");
  mockNav.push.mockReset();
  fetchContentOpportunities.mockReset();
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("ContentOpportunitiesPanel", () => {
  it("renders stat cards, table, and rows from initialItems", () => {
    fetchContentOpportunities.mockResolvedValue({
      items: [],
      total: 0,
      limit: 200,
      offset: 0,
    });
    render(
      <ContentOpportunitiesPanel
        initialItems={[makeCO({ id: 1 }), makeCO({ id: 2, status: "approved" })]}
        initialTotal={2}
        initialStatusFilter=""
        initialComplianceFilter=""
        initialSignalId={null}
        initialAllItems={[makeCO({ id: 1 }), makeCO({ id: 2, status: "approved" })]}
      />,
    );
    expect(screen.getByTestId("content-opportunities-panel")).toBeInTheDocument();
    expect(screen.getByTestId("stat-draft")).toHaveTextContent("1");
    expect(screen.getByTestId("stat-approved")).toHaveTextContent("1");
    expect(screen.getByTestId("co-row-1")).toBeInTheDocument();
    expect(screen.getByTestId("co-row-2")).toBeInTheDocument();
    expect(screen.getByTestId("co-status-1")).toHaveTextContent("草稿");
  });

  it("shows the empty state when items is empty", () => {
    fetchContentOpportunities.mockResolvedValue({
      items: [],
      total: 0,
      limit: 200,
      offset: 0,
    });
    render(
      <ContentOpportunitiesPanel
        initialItems={[]}
        initialTotal={0}
        initialStatusFilter=""
        initialComplianceFilter=""
        initialSignalId={null}
        initialAllItems={[]}
      />,
    );
    expect(screen.getByTestId("content-opportunities-empty")).toBeInTheDocument();
  });

  it("shows the 🛡️ 待复核 count and links to the filtered list", async () => {
    const blocked = makeCO({
      id: 99,
      compliance_blocked: true,
      compliance_risk_score: 0.9,
      compliance_risk_types: ["medical_advice"],
      status: "draft",
    });
    fetchContentOpportunities.mockResolvedValue({
      items: [blocked],
      total: 1,
      limit: 200,
      offset: 0,
    });
    render(
      <ContentOpportunitiesPanel
        initialItems={[blocked]}
        initialTotal={1}
        initialStatusFilter=""
        initialComplianceFilter=""
        initialSignalId={null}
        initialAllItems={[blocked]}
      />,
    );
    const btn = screen.getByTestId("stat-review-queue");
    expect(btn).toHaveTextContent("1");
    fireEvent.click(btn);
    await waitFor(() =>
      expect(mockNav.push).toHaveBeenCalledWith(
        expect.stringContaining("status=draft"),
      ),
    );
    await waitFor(() =>
      expect(mockNav.push).toHaveBeenCalledWith(
        expect.stringContaining("compliance_blocked=true"),
      ),
    );
  });

  it("changing status filter pushes URL + re-fetches", async () => {
    fetchContentOpportunities.mockResolvedValue({
      items: [],
      total: 0,
      limit: 200,
      offset: 0,
    });
    render(
      <ContentOpportunitiesPanel
        initialItems={[makeCO()]}
        initialTotal={1}
        initialStatusFilter=""
        initialComplianceFilter=""
        initialSignalId={null}
        initialAllItems={[makeCO()]}
      />,
    );
    fireEvent.change(screen.getByTestId("filter-status"), {
      target: { value: "approved" },
    });
    await waitFor(() =>
      expect(mockNav.push).toHaveBeenCalledWith(
        expect.stringContaining("status=approved"),
      ),
    );
    await waitFor(() =>
      expect(fetchContentOpportunities).toHaveBeenCalledWith(
        expect.objectContaining({ status: "approved" }),
      ),
    );
  });

  it("renders compliance_blocked badge with risk title for blocked rows", () => {
    const blocked = makeCO({
      id: 7,
      compliance_blocked: true,
      compliance_risk_score: 0.9,
      compliance_risk_types: ["medical_advice"],
    });
    fetchContentOpportunities.mockResolvedValue({
      items: [],
      total: 0,
      limit: 200,
      offset: 0,
    });
    render(
      <ContentOpportunitiesPanel
        initialItems={[blocked]}
        initialTotal={1}
        initialStatusFilter=""
        initialComplianceFilter=""
        initialSignalId={null}
        initialAllItems={[blocked]}
      />,
    );
    const badge = screen.getByTestId("co-compliance-blocked-7");
    expect(badge).toHaveTextContent("拦截");
    expect(badge.title).toContain("90%");
    expect(badge.title).toContain("medical_advice");
  });

  it("renders compliance ok badge for non-blocked rows", () => {
    fetchContentOpportunities.mockResolvedValue({
      items: [],
      total: 0,
      limit: 200,
      offset: 0,
    });
    render(
      <ContentOpportunitiesPanel
        initialItems={[makeCO({ id: 3 })]}
        initialTotal={1}
        initialStatusFilter=""
        initialComplianceFilter=""
        initialSignalId={null}
        initialAllItems={[makeCO({ id: 3 })]}
      />,
    );
    expect(screen.getByTestId("co-compliance-ok-3")).toHaveTextContent("通过");
  });
});