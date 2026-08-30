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
  usePathname: () => "/admin/sources",
}));

import { SourcesPanel } from "@/components/SourcesPanel";
import { fetchSources, updateSourceCompliance } from "@/lib/api";
import type { Source, SourceListResponse } from "@/types";

vi.mock("@/lib/api", () => ({
  fetchSources: vi.fn(),
  updateSourceCompliance: vi.fn(),
}));

function makeSource(overrides: Partial<Source> = {}): Source {
  return {
    id: 1,
    name: "github",
    type: "rss",
    url: "https://example.com/feed",
    enabled: true,
    compliance_level: "A",
    commercial_use_status: null,
    access_method: null,
    retention_policy: "30d",
    source_block_reason: null,
    last_compliance_check: "2026-08-30T12:00:00Z",
    ...overrides,
  };
}

function makeResponse(
  overrides: Partial<SourceListResponse> = {},
): SourceListResponse {
  return { count: 0, items: [], ...overrides };
}

const mockedFetch = vi.mocked(fetchSources);
const mockedPatch = vi.mocked(updateSourceCompliance);

beforeEach(() => {
  mockReplace.mockClear();
  Array.from(mockSearchParams.keys()).forEach((k) =>
    mockSearchParams.delete(k),
  );
  mockedFetch.mockReset();
  mockedFetch.mockResolvedValue(makeResponse());
  mockedPatch.mockReset();
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("SourcesPanel", () => {
  it("renders empty state when no sources", () => {
    render(
      <SourcesPanel
        initial={makeResponse({ items: [], count: 0 })}
        initialFilters={{}}
      />,
    );
    expect(screen.getByTestId("sources-panel")).toBeInTheDocument();
    expect(screen.getByTestId("sources-empty")).toHaveTextContent(
      "没有 source",
    );
    expect(screen.getByTestId("stat-total")).toHaveTextContent("0");
  });

  it("renders one row per source with level chip, enabled badge, audit link", () => {
    const items = [
      makeSource({ id: 1, name: "github", compliance_level: "A", enabled: true }),
      makeSource({
        id: 2,
        name: "reddit",
        compliance_level: "E",
        enabled: false,
        source_block_reason: "付费墙",
      }),
    ];
    render(
      <SourcesPanel
        initial={makeResponse({ items, count: 2 })}
        initialFilters={{}}
      />,
    );

    expect(screen.getByTestId("source-row-1")).toBeInTheDocument();
    expect(screen.getByTestId("source-level-1")).toHaveTextContent("A");
    expect(screen.getByTestId("source-patch-1")).toBeInTheDocument();

    expect(screen.getByTestId("source-row-2")).toBeInTheDocument();
    expect(screen.getByTestId("source-level-2")).toHaveTextContent("E");
    expect(screen.getByTestId("source-row-2")).toHaveTextContent("付费墙");

    expect(screen.getByTestId("stat-enabled")).toHaveTextContent("1");
    expect(screen.getByTestId("stat-disabled")).toHaveTextContent("1");
    expect(screen.getByTestId("stat-A")).toHaveTextContent("1");
    expect(screen.getByTestId("stat-DE")).toHaveTextContent("1");

    expect(screen.getByTestId("source-audit-1").getAttribute("href")).toBe(
      "/admin/audit-logs?resource_type=source&resource_id=1",
    );
  });

  it("clicking a level chip updates URL and re-fetches", async () => {
    render(
      <SourcesPanel
        initial={makeResponse({ items: [], count: 0 })}
        initialFilters={{}}
      />,
    );

    fireEvent.click(screen.getByTestId("chip-level-E"));

    await waitFor(() => {
      expect(mockReplace).toHaveBeenCalledWith(
        "/admin/sources?compliance_level=E",
        expect.objectContaining({ scroll: false }),
      );
    });
    expect(mockedFetch).toHaveBeenCalledWith(
      expect.objectContaining({ compliance_level: "E" }),
    );
  });

  it("Reset clears filters and re-fetches", async () => {
    render(
      <SourcesPanel
        initial={makeResponse({ items: [], count: 0 })}
        initialFilters={{ compliance_level: "E", enabled: "enabled" }}
      />,
    );

    fireEvent.click(screen.getByTestId("btn-reset"));

    await waitFor(() => {
      expect(mockReplace).toHaveBeenCalledWith(
        "/admin/sources",
        expect.objectContaining({ scroll: false }),
      );
    });
    expect(mockedFetch).toHaveBeenCalledWith(
      expect.objectContaining({
        compliance_level: undefined,
      }),
    );
  });

  it("Patch Compliance flow: open modal, change level, submit calls updateSourceCompliance + refresh", async () => {
    const src = makeSource({ id: 3, name: "hackernews", compliance_level: "B" });
    const patched = makeSource({
      id: 3,
      name: "hackernews",
      compliance_level: "E",
      source_block_reason: "登录墙",
    });
    mockedPatch.mockResolvedValueOnce(patched);
    mockedFetch.mockResolvedValueOnce(
      makeResponse({ items: [patched], count: 1 }),
    );

    render(
      <SourcesPanel
        initial={makeResponse({ items: [src], count: 1 })}
        initialFilters={{}}
      />,
    );

    fireEvent.click(screen.getByTestId("source-patch-3"));
    expect(screen.getByTestId("patch-modal")).toBeInTheDocument();
    expect(screen.getByTestId("patch-form")).toHaveTextContent("hackernews");

    // Default level = source's current level (B).
    expect(
      screen.getByTestId("patch-level-B").getAttribute("data-active"),
    ).toBe("true");

    // Pick E.
    fireEvent.click(screen.getByTestId("patch-level-E"));
    fireEvent.change(screen.getByTestId("patch-block-reason"), {
      target: { value: "登录墙" },
    });
    fireEvent.click(screen.getByTestId("patch-submit"));

    await waitFor(() => {
      expect(mockedPatch).toHaveBeenCalledWith(3, {
        compliance_level: "E",
        retention_policy: "30d",
        source_block_reason: "登录墙",
      });
    });
    await waitFor(() => {
      expect(screen.queryByTestId("patch-modal")).toBeNull();
    });
    expect(screen.getByTestId("sources-toast")).toHaveTextContent(
      "hackernews 合规级别 → E",
    );
  });

  it("shows an error banner when fetch fails", async () => {
    mockedFetch.mockRejectedValueOnce(new Error("network down"));

    render(
      <SourcesPanel
        initial={makeResponse({ items: [], count: 0 })}
        initialFilters={{}}
      />,
    );

    fireEvent.click(screen.getByTestId("btn-reset"));

    await waitFor(() => {
      expect(screen.getByTestId("sources-error")).toHaveTextContent(
        "network down",
      );
    });
  });
});
