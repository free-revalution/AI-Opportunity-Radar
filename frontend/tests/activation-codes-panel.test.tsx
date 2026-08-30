import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// ---- Mock next/link so it renders a plain anchor --------------------------
vi.mock("next/link", () => ({
  default: ({ href, children }: { href: string; children: React.ReactNode }) => (
    <a href={href}>{children}</a>
  ),
}));

// ---- Mock useRouter + useSearchParams -------------------------------------
const mockReplace = vi.fn();
const mockSearchParams = new URLSearchParams();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: mockReplace }),
  useSearchParams: () => mockSearchParams,
  usePathname: () => "/admin/activation",
}));

import { ActivationCodesPanel } from "@/components/ActivationCodesPanel";
import {
  fetchActivationCodes,
  issueActivationCode,
  revokeActivationCode,
} from "@/lib/api";
import type {
  ActivationCode,
  ActivationIssueResponse,
  ActivationListResponse,
} from "@/types";

vi.mock("@/lib/api", () => ({
  fetchActivationCodes: vi.fn(),
  issueActivationCode: vi.fn(),
  revokeActivationCode: vi.fn(),
}));

// ---- Fixtures -------------------------------------------------------------
function makeCode(overrides: Partial<ActivationCode> = {}): ActivationCode {
  return {
    id: 1,
    plan: "basic",
    status: "unused",
    expires_at: "2027-08-30T00:00:00Z",
    bound_feishu_open_id: null,
    bound_at: null,
    created_at: "2026-08-30T12:00:00Z",
    used_at: null,
    ...overrides,
  };
}

function makeResponse(
  overrides: Partial<ActivationListResponse> = {},
): ActivationListResponse {
  return { count: 0, items: [], ...overrides };
}

const mockedFetch = vi.mocked(fetchActivationCodes);
const mockedIssue = vi.mocked(issueActivationCode);
const mockedRevoke = vi.mocked(revokeActivationCode);

beforeEach(() => {
  mockReplace.mockClear();
  Array.from(mockSearchParams.keys()).forEach((k) =>
    mockSearchParams.delete(k),
  );
  mockedFetch.mockReset();
  mockedFetch.mockResolvedValue(makeResponse());
  mockedIssue.mockReset();
  mockedRevoke.mockReset();
});

afterEach(() => {
  vi.clearAllMocks();
});

// ---------------------------------------------------------------------------
describe("ActivationCodesPanel", () => {
  it("renders the panel + empty state when no codes", () => {
    render(
      <ActivationCodesPanel
        initial={makeResponse({ items: [], count: 0 })}
        initialFilters={{}}
      />,
    );
    expect(screen.getByTestId("activation-codes-panel")).toBeInTheDocument();
    expect(screen.getByTestId("activation-empty")).toHaveTextContent(
      "还没有激活码",
    );
    expect(screen.getByTestId("stat-total")).toHaveTextContent("0");
  });

  it("renders one row per code with plan, status chip, bound, expires", () => {
    const items = [
      makeCode({ id: 1, plan: "basic", status: "unused" }),
      makeCode({
        id: 2,
        plan: "pro",
        status: "active",
        bound_feishu_open_id: "ou_xyz",
      }),
    ];
    render(
      <ActivationCodesPanel
        initial={makeResponse({ items, count: 2 })}
        initialFilters={{}}
      />,
    );

    expect(screen.getByTestId("activation-row-1")).toBeInTheDocument();
    expect(screen.getByTestId("activation-status-1")).toHaveTextContent("未用");
    expect(screen.getByTestId("activation-revoke-1")).toBeInTheDocument();

    expect(screen.getByTestId("activation-row-2")).toBeInTheDocument();
    expect(screen.getByTestId("activation-status-2")).toHaveTextContent("已激活");
    expect(screen.getByTestId("activation-row-2")).toHaveTextContent("ou_xyz");

    // Revoke button hidden for non-revokable statuses? Actually it shows
    // for all except "revoked".
    expect(screen.getByTestId("activation-revoke-2")).toBeInTheDocument();
    expect(screen.getByTestId("stat-unused")).toHaveTextContent("1");
    expect(screen.getByTestId("stat-active")).toHaveTextContent("1");

    // Audit deep-link per row.
    expect(
      screen.getByTestId("activation-audit-1").getAttribute("href"),
    ).toBe("/admin/audit-logs?resource_type=activation_code&resource_id=1");
  });

  it("hides the revoke button for already-revoked rows", () => {
    const items = [
      makeCode({ id: 7, plan: "basic", status: "revoked" }),
    ];
    render(
      <ActivationCodesPanel
        initial={makeResponse({ items, count: 1 })}
        initialFilters={{}}
      />,
    );
    expect(screen.queryByTestId("activation-revoke-7")).toBeNull();
  });

  it("clicking a status chip updates URL and re-fetches", async () => {
    render(
      <ActivationCodesPanel
        initial={makeResponse({ items: [], count: 0 })}
        initialFilters={{}}
      />,
    );

    fireEvent.click(screen.getByTestId("chip-status-revoked"));

    await waitFor(() => {
      expect(mockReplace).toHaveBeenCalledWith(
        "/admin/activation?status=revoked",
        expect.objectContaining({ scroll: false }),
      );
    });
    expect(mockedFetch).toHaveBeenCalledWith(
      expect.objectContaining({ status: "revoked" }),
    );
  });

  it("the Reset button clears all filters and re-fetches", async () => {
    mockedFetch.mockResolvedValue(
      makeResponse({
        items: [makeCode({ id: 5, plan: "pro" })],
        count: 1,
      }),
    );

    render(
      <ActivationCodesPanel
        initial={makeResponse({ items: [], count: 0 })}
        initialFilters={{ status: "revoked", plan: "pro", id: "5" }}
      />,
    );

    fireEvent.click(screen.getByTestId("btn-reset"));

    await waitFor(() => {
      expect(mockReplace).toHaveBeenCalledWith(
        "/admin/activation",
        expect.objectContaining({ scroll: false }),
      );
    });
    expect(mockedFetch).toHaveBeenCalledWith(
      expect.objectContaining({
        status: undefined,
        plan: undefined,
      }),
    );
  });

  it("Issue modal flow: open, fill form, submit, display returned code + refresh", async () => {
    const newCode = makeCode({ id: 99, plan: "pro", status: "unused" });
    const issued: ActivationIssueResponse = {
      ...newCode,
      code: "ABCD-1234-EFGH",
    };
    mockedIssue.mockResolvedValueOnce(issued);
    // After issue, refresh fetch returns the new code.
    mockedFetch.mockResolvedValueOnce(makeResponse({ items: [issued], count: 1 }));

    render(
      <ActivationCodesPanel
        initial={makeResponse({ items: [], count: 0 })}
        initialFilters={{}}
      />,
    );

    // Open the modal.
    fireEvent.click(screen.getByTestId("btn-issue"));
    expect(screen.getByTestId("issue-modal")).toBeInTheDocument();

    // Submit (defaults: plan=basic, ttl=365).
    fireEvent.change(screen.getByTestId("issue-plan"), {
      target: { value: "pro" },
    });
    fireEvent.click(screen.getByTestId("issue-submit"));

    await waitFor(() => {
      expect(mockedIssue).toHaveBeenCalledWith({ plan: "pro", ttl_days: 365 });
    });

    // Modal closes, banner + toast display the code.
    await waitFor(() => {
      expect(screen.queryByTestId("issue-modal")).toBeNull();
    });
    expect(screen.getByTestId("activation-banner")).toHaveTextContent(
      "ABCD-1234-EFGH",
    );
    expect(screen.getByTestId("activation-toast")).toHaveTextContent(
      "ABCD-1234-EFGH",
    );

    // Refresh fired once after the successful issue.
    await waitFor(() => {
      expect(mockedFetch).toHaveBeenCalledTimes(1);
    });
  });

  it("Revoke confirm flow: open, confirm, calls revokeActivationCode + refresh", async () => {
    const items = [makeCode({ id: 11, plan: "basic", status: "unused" })];
    mockedRevoke.mockResolvedValueOnce({
      ...items[0],
      status: "revoked",
    });
    mockedFetch.mockResolvedValueOnce(makeResponse({ items: [], count: 0 }));

    render(
      <ActivationCodesPanel
        initial={makeResponse({ items, count: 1 })}
        initialFilters={{}}
      />,
    );

    fireEvent.click(screen.getByTestId("activation-revoke-11"));
    expect(screen.getByTestId("revoke-modal")).toBeInTheDocument();
    expect(screen.getByTestId("revoke-form")).toHaveTextContent("#11");

    fireEvent.click(screen.getByTestId("revoke-submit"));

    await waitFor(() => {
      expect(mockedRevoke).toHaveBeenCalledWith(11);
    });
    await waitFor(() => {
      expect(screen.queryByTestId("revoke-modal")).toBeNull();
    });
    expect(screen.getByTestId("activation-toast")).toHaveTextContent("已撤销");
  });

  it("shows an error banner when the fetch fails", async () => {
    mockedFetch.mockRejectedValueOnce(new Error("network down"));

    render(
      <ActivationCodesPanel
        initial={makeResponse({ items: [], count: 0 })}
        initialFilters={{}}
      />,
    );

    fireEvent.click(screen.getByTestId("btn-reset"));

    await waitFor(() => {
      expect(screen.getByTestId("activation-error")).toHaveTextContent(
        "network down",
      );
    });
  });
});
