import { fireEvent, render, renderHook, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// ---- Mock next/link so it renders a plain anchor --------------------------
vi.mock("next/link", () => ({
  default: ({ href, children }: { href: string; children: React.ReactNode }) => (
    <a href={href}>{children}</a>
  ),
}));

// ---- Mock useRouter + useSearchParams -------------------------------------
const mockReplace = vi.fn();
const mockBack = vi.fn();
const mockForward = vi.fn();
const mockSearchParams = new URLSearchParams();

vi.mock("next/navigation", () => ({
  useRouter: () => ({
    replace: mockReplace,
    back: mockBack,
    forward: mockForward,
  }),
  useSearchParams: () => mockSearchParams,
  usePathname: () => "/admin/audit-logs",
}));

import { AuditLogsPanel } from "@/components/AuditLogsPanel";
import { fetchAuditLogs } from "@/lib/api";
import type { AuditLogItem, AuditLogsResponse } from "@/types";

vi.mock("@/lib/api", () => ({
  fetchAuditLogs: vi.fn(),
}));

// ---- Fixtures -------------------------------------------------------------
function makeItem(
  overrides: Partial<AuditLogItem> = {},
): AuditLogItem {
  return {
    id: 1,
    actor_type: "webhook",
    actor_id: "webhook",
    action: "content_opportunity_transition",
    resource_type: "content_opportunity",
    resource_id: "42",
    result: "success",
    metadata_json: { from: "draft", to: "approved" },
    created_at: "2026-08-30T14:35:00Z",
    ...overrides,
  };
}

function makeResponse(
  overrides: Partial<AuditLogsResponse> = {},
): AuditLogsResponse {
  return {
    items: [],
    total: 0,
    limit: 50,
    offset: 0,
    ...overrides,
  };
}

const mockedFetch = vi.mocked(fetchAuditLogs);

beforeEach(() => {
  mockReplace.mockClear();
  mockBack.mockClear();
  mockForward.mockClear();
  // Clear search params between tests
  Array.from(mockSearchParams.keys()).forEach((k) =>
    mockSearchParams.delete(k),
  );
  mockedFetch.mockReset();
  mockedFetch.mockResolvedValue(makeResponse());
});

afterEach(() => {
  vi.clearAllMocks();
});

// ---------------------------------------------------------------------------
describe("AuditLogsPanel", () => {
  it("renders the panel + empty state when no items", () => {
    render(
      <AuditLogsPanel
        initial={makeResponse({ items: [], total: 0 })}
        initialFilters={{}}
      />,
    );
    expect(screen.getByTestId("audit-logs-panel")).toBeInTheDocument();
    expect(screen.getByTestId("audit-empty")).toHaveTextContent("暂无审计日志");
  });

  it("renders one row per item with actor / result / metadata summary", () => {
    const items = [
      makeItem({
        id: 1,
        actor_type: "admin",
        actor_id: "secret",
        action: "content_opportunity_transition",
        resource_type: "content_opportunity",
        resource_id: "42",
        result: "success",
        metadata_json: { from: "draft", to: "approved" },
      }),
      makeItem({
        id: 2,
        actor_type: "user",
        actor_id: "ou_xyz",
        action: "activate",
        resource_type: "activation_code",
        resource_id: "7",
        result: "blocked",
        metadata_json: { plan: "pro", idempotent: false },
      }),
    ];
    render(
      <AuditLogsPanel
        initial={makeResponse({ items, total: 2 })}
        initialFilters={{}}
      />,
    );

    // Row 1: content_opportunity_transition, target link.
    expect(screen.getByTestId("audit-row-1")).toBeInTheDocument();
    expect(screen.getByTestId("audit-actor-1")).toHaveTextContent("admin");
    expect(screen.getByTestId("audit-result-1")).toHaveTextContent("success");
    expect(screen.getByTestId("audit-target-1").getAttribute("href")).toBe(
      "/admin/content-opportunities/42",
    );
    expect(screen.getByTestId("audit-row-1")).toHaveTextContent(
      "draft → approved",
    );

    // Row 2: activation_code — no detail page, shows raw label.
    expect(screen.getByTestId("audit-row-2")).toBeInTheDocument();
    expect(screen.getByTestId("audit-actor-2")).toHaveTextContent("user");
    expect(screen.getByTestId("audit-result-2")).toHaveTextContent("blocked");
    expect(screen.getByTestId("audit-row-2")).toHaveTextContent(
      "activation_code#7",
    );
    expect(screen.getByTestId("audit-row-2")).toHaveTextContent("plan=pro");
  });

  it("clicking an actor_type chip updates URL and re-fetches", async () => {
    render(
      <AuditLogsPanel
        initial={makeResponse({ items: [], total: 0 })}
        initialFilters={{}}
      />,
    );

    fireEvent.click(screen.getByTestId("chip-actor_type-admin"));

    await waitFor(() => {
      expect(mockReplace).toHaveBeenCalledWith(
        "/admin/audit-logs?actor_type=admin",
        expect.objectContaining({ scroll: false }),
      );
    });
    expect(mockedFetch).toHaveBeenCalledWith(
      expect.objectContaining({ actor_type: "admin", offset: 0, limit: 50 }),
    );
  });

  it("clicking a result chip applies the result filter", async () => {
    render(
      <AuditLogsPanel
        initial={makeResponse({ items: [], total: 0 })}
        initialFilters={{}}
      />,
    );

    fireEvent.click(screen.getByTestId("chip-result-blocked"));

    await waitFor(() => {
      expect(mockReplace).toHaveBeenCalledWith(
        "/admin/audit-logs?result=blocked",
        expect.objectContaining({ scroll: false }),
      );
    });
  });

  it("the Reset button clears all filters and re-fetches", async () => {
    render(
      <AuditLogsPanel
        initial={makeResponse({ items: [], total: 0 })}
        initialFilters={{ actor_type: "admin", result: "blocked" }}
      />,
    );

    fireEvent.click(screen.getByTestId("btn-reset"));

    await waitFor(() => {
      expect(mockReplace).toHaveBeenCalledWith(
        "/admin/audit-logs",
        expect.objectContaining({ scroll: false }),
      );
    });
    expect(mockedFetch).toHaveBeenCalledWith(
      expect.objectContaining({
        actor_type: undefined,
        result: undefined,
        offset: 0,
      }),
    );
  });

  it("clicking next pushes offset and re-fetches; prev is disabled on first page", async () => {
    // Seed 120 items so page 2 exists.
    const items = Array.from({ length: 50 }, (_, i) =>
      makeItem({ id: i + 1, resource_id: String(i + 1) }),
    );
    mockedFetch.mockResolvedValueOnce(
      makeResponse({ items, total: 120, limit: 50, offset: 0 }),
    );

    render(
      <AuditLogsPanel
        initial={makeResponse({ items, total: 120, limit: 50, offset: 0 })}
        initialFilters={{}}
      />,
    );

    // Prev should be disabled (offset=0).
    expect(screen.getByTestId("btn-prev")).toBeDisabled();
    expect(screen.getByTestId("btn-next")).not.toBeDisabled();

    fireEvent.click(screen.getByTestId("btn-next"));

    await waitFor(() => {
      expect(mockReplace).toHaveBeenCalledWith(
        "/admin/audit-logs?offset=50",
        expect.objectContaining({ scroll: false }),
      );
    });
    expect(mockedFetch).toHaveBeenCalledWith(
      expect.objectContaining({ offset: 50 }),
    );
  });

  it("clicking the ··· button expands the metadata drawer; second click collapses", async () => {
    const item = makeItem({
      id: 99,
      metadata_json: { from: "draft", to: "approved", reason: "looks good" },
    });
    render(
      <AuditLogsPanel
        initial={makeResponse({ items: [item], total: 1 })}
        initialFilters={{}}
      />,
    );

    // Initially collapsed — no metadata pre.
    expect(screen.queryByTestId("audit-meta-99")).toBeNull();

    fireEvent.click(screen.getByTestId("audit-toggle-99"));
    expect(screen.getByTestId("audit-meta-99")).toBeInTheDocument();
    expect(screen.getByTestId("audit-meta-pre-99")).toHaveTextContent(
      '"reason": "looks good"',
    );

    // Second click collapses.
    fireEvent.click(screen.getByTestId("audit-toggle-99"));
    expect(screen.queryByTestId("audit-meta-99")).toBeNull();
  });

  it("renders an error banner when the fetch fails", async () => {
    mockedFetch.mockRejectedValueOnce(new Error("network down"));

    render(
      <AuditLogsPanel
        initial={makeResponse({ items: [], total: 0 })}
        initialFilters={{}}
      />,
    );

    fireEvent.click(screen.getByTestId("btn-reset"));

    await waitFor(() => {
      expect(screen.getByTestId("audit-logs-error")).toHaveTextContent(
        "network down",
      );
    });
  });
});
