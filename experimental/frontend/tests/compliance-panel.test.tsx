import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("next/link", () => ({
  default: ({ href, children }: { href: string; children: React.ReactNode }) => (
    <a href={href}>{children}</a>
  ),
}));

const mockReplace = vi.fn();
const mockSearchParams = new URLSearchParams();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: mockReplace }),
  useSearchParams: () => mockSearchParams,
  usePathname: () => "/admin/compliance",
}));

import { CompliancePanel } from "@/components/CompliancePanel";
import {
  fetchComplianceAudits,
  overrideComplianceAudit,
} from "@/lib/api";
import type {
  ComplianceAuditItem,
  ComplianceAuditResponse,
  ComplianceRiskLevel,
  ComplianceRiskType,
} from "@/types";

vi.mock("@/lib/api", () => ({
  fetchComplianceAudits: vi.fn(),
  overrideComplianceAudit: vi.fn(),
}));

// ---- Fixtures -------------------------------------------------------------
function makeItem(
  overrides: Partial<ComplianceAuditItem> = {},
): ComplianceAuditItem {
  return {
    id: 1,
    actor_id: "compliance_gate:smoke",
    resource_type: "feishu_message",
    resource_id: "ou_001",
    risk_level: "high" as ComplianceRiskLevel,
    risk_types: ["prompt_injection" as ComplianceRiskType],
    risk_score: 0.62,
    reason: "prompt_injection detected",
    requires_human_review: true,
    context: "smoke_high",
    overridden: false,
    override_reason: null,
    created_at: "2026-08-30T14:35:00Z",
    ...overrides,
  };
}

function makeResponse(
  overrides: Partial<ComplianceAuditResponse> = {},
): ComplianceAuditResponse {
  return {
    items: [],
    total: 0,
    limit: 50,
    offset: 0,
    ...overrides,
  };
}

const mockedFetch = vi.mocked(fetchComplianceAudits);
const mockedOverride = vi.mocked(overrideComplianceAudit);

beforeEach(() => {
  mockReplace.mockClear();
  Array.from(mockSearchParams.keys()).forEach((k) =>
    mockSearchParams.delete(k),
  );
  mockedFetch.mockReset();
  mockedFetch.mockResolvedValue(makeResponse());
  mockedOverride.mockReset();
});

afterEach(() => {
  vi.clearAllMocks();
});

// ---------------------------------------------------------------------------
describe("CompliancePanel", () => {
  it("renders the panel + empty state when no items", () => {
    render(
      <CompliancePanel
        initial={makeResponse({ items: [], total: 0 })}
        initialFilters={{}}
      />,
    );
    expect(screen.getByTestId("compliance-panel")).toBeInTheDocument();
    expect(screen.getByTestId("compliance-empty")).toHaveTextContent(
      "暂无合规阻断记录",
    );
  });

  it("renders one row per item with risk chip + types + reason", () => {
    const items = [
      makeItem({
        id: 11,
        risk_level: "high",
        risk_types: ["prompt_injection"],
        reason: "prompt_injection detected",
        resource_type: "feishu_message",
        resource_id: "ou_001",
      }),
      makeItem({
        id: 12,
        risk_level: "blocked",
        risk_types: ["pii", "copyright"],
        reason: "policy combined risk",
        resource_type: "content_opportunity",
        resource_id: "42",
      }),
    ];
    render(
      <CompliancePanel
        initial={makeResponse({ items, total: 2 })}
        initialFilters={{}}
      />,
    );
    expect(screen.getByTestId("compliance-row-11")).toBeInTheDocument();
    expect(screen.getByTestId("compliance-row-12")).toBeInTheDocument();
    expect(screen.getByTestId("compliance-risk-11")).toHaveTextContent("high");
    expect(screen.getByTestId("compliance-risk-12")).toHaveTextContent(
      "blocked",
    );
  });

  it("opens the override modal when an Override button is clicked", () => {
    render(
      <CompliancePanel
        initial={makeResponse({
          items: [makeItem({ id: 21 })],
          total: 1,
        })}
        initialFilters={{}}
      />,
    );
    fireEvent.click(screen.getByTestId("compliance-override-btn-21"));
    expect(screen.getByTestId("override-modal")).toBeInTheDocument();
    expect(screen.getByTestId("override-reason-input")).toBeInTheDocument();
  });

  it("requires a reason ≥ 10 chars before submitting the override", async () => {
    mockedOverride.mockResolvedValue({
      ok: true,
      original_audit_log_id: 31,
      override_audit_log_id: 99,
    });
    render(
      <CompliancePanel
        initial={makeResponse({
          items: [makeItem({ id: 31 })],
          total: 1,
        })}
        initialFilters={{}}
      />,
    );
    fireEvent.click(screen.getByTestId("compliance-override-btn-31"));
    const input = screen.getByTestId(
      "override-reason-input",
    ) as HTMLTextAreaElement;
    // < 10 chars → submit disabled
    fireEvent.change(input, { target: { value: "too short" } });
    expect(
      (screen.getByTestId("override-submit") as HTMLButtonElement).disabled,
    ).toBe(true);

    // Valid reason → submit enabled + fires the API call.
    fireEvent.change(input, {
      target: { value: "Operator reviewed and approved" },
    });
    fireEvent.click(screen.getByTestId("override-submit"));
    await waitFor(() => {
      expect(mockedOverride).toHaveBeenCalledWith(
        31,
        "Operator reviewed and approved",
      );
    });
  });

  it("filters by risk_level chip click", async () => {
    mockedFetch.mockResolvedValue(
      makeResponse({ items: [makeItem({ id: 41, risk_level: "blocked" })], total: 1 }),
    );
    render(
      <CompliancePanel
        initial={makeResponse({
          items: [makeItem({ id: 40, risk_level: "medium" })],
          total: 1,
        })}
        initialFilters={{}}
      />,
    );
    fireEvent.click(screen.getByTestId("chip-risk_level-blocked"));
    await waitFor(() => {
      expect(mockedFetch).toHaveBeenCalledWith(
        expect.objectContaining({
          risk_level: "blocked",
        }),
      );
    });
    expect(mockReplace).toHaveBeenCalled();
  });

  it("filters by risk_type chip click", async () => {
    render(
      <CompliancePanel
        initial={makeResponse({ items: [], total: 0 })}
        initialFilters={{}}
      />,
    );
    fireEvent.click(screen.getByTestId("chip-risk_type-pii"));
    await waitFor(() => {
      expect(mockedFetch).toHaveBeenCalledWith(
        expect.objectContaining({ risk_type: "pii" }),
      );
    });
  });

  it("disables next button at end of pagination", () => {
    render(
      <CompliancePanel
        initial={makeResponse({ items: [], total: 25 })}
        initialFilters={{}}
      />,
    );
    const nextBtn = screen.getByTestId("btn-next") as HTMLButtonElement;
    expect(nextBtn.disabled).toBe(true);
    const prevBtn = screen.getByTestId("btn-prev") as HTMLButtonElement;
    expect(prevBtn.disabled).toBe(true);
  });

  it("renders error banner when fetch fails", async () => {
    mockedFetch.mockRejectedValueOnce(new Error("backend offline"));
    render(
      <CompliancePanel
        initial={makeResponse({ items: [], total: 0 })}
        initialFilters={{}}
      />,
    );
    fireEvent.click(screen.getByTestId("chip-risk_level-high"));
    await waitFor(() => {
      expect(screen.getByTestId("compliance-error")).toHaveTextContent(
        "backend offline",
      );
    });
  });
});
