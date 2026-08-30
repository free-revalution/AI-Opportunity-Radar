import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// ---- Mock lib/api --------------------------------------------------------
const approveContentOpportunity = vi.fn();
const publishContentOpportunity = vi.fn();
const rejectContentOpportunity = vi.fn();

vi.mock("@/lib/api", () => ({
  approveContentOpportunity: (...args: unknown[]) =>
    (approveContentOpportunity as (...args: unknown[]) => unknown)(...args),
  publishContentOpportunity: (...args: unknown[]) =>
    (publishContentOpportunity as (...args: unknown[]) => unknown)(...args),
  rejectContentOpportunity: (...args: unknown[]) =>
    (rejectContentOpportunity as (...args: unknown[]) => unknown)(...args),
}));

import { ContentOpportunityDetail } from "@/components/ContentOpportunityDetail";
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
    material_ideas: ["市场数据", "用户访谈"],
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
  approveContentOpportunity.mockReset();
  publishContentOpportunity.mockReset();
  rejectContentOpportunity.mockReset();
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("ContentOpportunityDetail", () => {
  it("renders meta + content fields for a draft row", () => {
    render(<ContentOpportunityDetail initial={makeCO()} />);
    expect(screen.getByTestId("co-detail-panel")).toBeInTheDocument();
    expect(screen.getByTestId("co-detail-status")).toHaveTextContent("草稿");
    expect(screen.getByTestId("co-hook")).toHaveTextContent("AI 颠覆跨境电商");
    expect(screen.getByTestId("co-title-candidates")).toHaveTextContent("跨境 AI 工具");
    expect(screen.getByTestId("co-script-outline")).toHaveTextContent("1. 引子");
    expect(screen.getByTestId("co-cta")).toHaveTextContent("评论留言");
  });

  it("draft state offers 批准 + 驳回, no 发布", () => {
    render(<ContentOpportunityDetail initial={makeCO()} />);
    expect(screen.getByTestId("co-approve")).toBeInTheDocument();
    expect(screen.getByTestId("co-reject")).toBeInTheDocument();
    expect(screen.queryByTestId("co-publish")).toBeNull();
  });

  it("approved state offers 发布 + 驳回, no 批准", () => {
    render(<ContentOpportunityDetail initial={makeCO({ status: "approved" })} />);
    expect(screen.getByTestId("co-publish")).toBeInTheDocument();
    expect(screen.getByTestId("co-reject")).toBeInTheDocument();
    expect(screen.queryByTestId("co-approve")).toBeNull();
  });

  it("rejected state shows the terminal-state notice with no buttons", () => {
    render(<ContentOpportunityDetail initial={makeCO({ status: "rejected" })} />);
    expect(screen.getByTestId("co-no-actions")).toHaveTextContent("终态");
    expect(screen.queryByTestId("co-approve")).toBeNull();
    expect(screen.queryByTestId("co-publish")).toBeNull();
    expect(screen.queryByTestId("co-reject")).toBeNull();
  });

  it("clicking 批准 calls approveContentOpportunity and updates state", async () => {
    approveContentOpportunity.mockResolvedValueOnce(
      makeCO({ id: 1, status: "approved" }),
    );
    render(<ContentOpportunityDetail initial={makeCO()} />);
    fireEvent.click(screen.getByTestId("co-approve"));

    await waitFor(() =>
      expect(approveContentOpportunity).toHaveBeenCalledWith(1),
    );
    await waitFor(() =>
      expect(screen.getByTestId("co-detail-status")).toHaveTextContent("已批准"),
    );
    expect(screen.getByTestId("co-toast")).toHaveTextContent("已批准");
  });

  it("clicking 发布 calls publishContentOpportunity", async () => {
    publishContentOpportunity.mockResolvedValueOnce(
      makeCO({ status: "published" }),
    );
    render(
      <ContentOpportunityDetail initial={makeCO({ status: "approved" })} />,
    );
    fireEvent.click(screen.getByTestId("co-publish"));
    await waitFor(() =>
      expect(publishContentOpportunity).toHaveBeenCalledWith(1),
    );
    await waitFor(() =>
      expect(screen.getByTestId("co-detail-status")).toHaveTextContent("已发布"),
    );
  });

  it("clicking 驳回 opens modal; submit calls rejectContentOpportunity with reason", async () => {
    rejectContentOpportunity.mockResolvedValueOnce(
      makeCO({ status: "rejected" }),
    );
    render(<ContentOpportunityDetail initial={makeCO()} />);
    fireEvent.click(screen.getByTestId("co-reject"));
    expect(screen.getByTestId("co-reject-modal")).toBeInTheDocument();

    fireEvent.change(screen.getByTestId("co-reject-reason"), {
      target: { value: "包含违禁关键词" },
    });
    fireEvent.click(screen.getByTestId("co-reject-confirm"));

    await waitFor(() =>
      expect(rejectContentOpportunity).toHaveBeenCalledWith(1, {
        reason: "包含违禁关键词",
      }),
    );
    await waitFor(() =>
      expect(screen.getByTestId("co-detail-status")).toHaveTextContent("已驳回"),
    );
  });

  it("驳回 modal cancel button closes without calling the API", () => {
    render(<ContentOpportunityDetail initial={makeCO()} />);
    fireEvent.click(screen.getByTestId("co-reject"));
    expect(screen.getByTestId("co-reject-modal")).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("co-reject-cancel"));
    expect(screen.queryByTestId("co-reject-modal")).toBeNull();
    expect(rejectContentOpportunity).not.toHaveBeenCalled();
  });

  it("shows the 🛡️ compliance badge for blocked rows", () => {
    render(
      <ContentOpportunityDetail
        initial={makeCO({
          compliance_blocked: true,
          compliance_risk_score: 0.9,
          compliance_risk_types: ["medical_advice"],
        })}
      />,
    );
    const badge = screen.getByTestId("co-detail-blocked");
    expect(badge).toHaveTextContent("合规拦截");
    expect(badge).toHaveTextContent("90%");
  });
});