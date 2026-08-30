import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// ---- Mock next/navigation ------------------------------------------------
const mockNav = vi.hoisted(() => ({
  pathname: "/admin/signals",
  searchParams: new URLSearchParams(""),
  push: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: mockNav.push }),
  usePathname: () => mockNav.pathname,
  useSearchParams: () => mockNav.searchParams,
}));

const fetchSignals = vi.fn();

vi.mock("@/lib/api", () => ({
  fetchSignals: (...args: unknown[]) =>
    (fetchSignals as (...args: unknown[]) => unknown)(...args),
}));

import { SignalsPanel } from "@/components/SignalsPanel";
import type { Signal } from "@/types";

// ---- Fixtures ------------------------------------------------------------
function makeSig(overrides: Partial<Signal> = {}): Signal {
  return {
    id: 1,
    raw_item_id: 100,
    signal_type: "trend",
    keyword: "AI",
    category: "AI SaaS",
    title: "AI legal contract review tool",
    summary: "LLM parses 100-page PDF contracts.",
    signal_score: 85,
    confidence_score: 90,
    status: "verified",
    compliance_status: "clean",
    risk_score: 0.1,
    created_at: "2026-08-27T10:00:00Z",
    ...overrides,
  };
}

beforeEach(() => {
  mockNav.pathname = "/admin/signals";
  mockNav.searchParams = new URLSearchParams("");
  mockNav.push.mockReset();
  fetchSignals.mockReset();
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("SignalsPanel", () => {
  it("renders one row per signal with all columns", () => {
    fetchSignals.mockResolvedValue({
      items: [],
      total: 0,
      limit: 50,
      offset: 0,
    });
    render(
      <SignalsPanel
        initialItems={[makeSig({ id: 1 }), makeSig({ id: 2, status: "rejected" })]}
        initialTotal={2}
        initialStatus=""
        initialMinScore={null}
      />,
    );
    expect(screen.getByTestId("signals-panel")).toBeInTheDocument();
    expect(screen.getByTestId("signal-row-1")).toBeInTheDocument();
    expect(screen.getByTestId("signal-row-2")).toBeInTheDocument();
    expect(screen.getByTestId("signal-status-1")).toHaveTextContent("verified");
    expect(screen.getByTestId("signal-status-2")).toHaveTextContent("rejected");
  });

  it("shows empty state when there are no items", () => {
    fetchSignals.mockResolvedValue({
      items: [],
      total: 0,
      limit: 50,
      offset: 0,
    });
    render(
      <SignalsPanel
        initialItems={[]}
        initialTotal={0}
        initialStatus=""
        initialMinScore={null}
      />,
    );
    expect(screen.getByTestId("signals-empty")).toBeInTheDocument();
  });

  it("changing status filter pushes URL and re-fetches", async () => {
    fetchSignals.mockResolvedValue({
      items: [],
      total: 0,
      limit: 50,
      offset: 0,
    });
    render(
      <SignalsPanel
        initialItems={[makeSig()]}
        initialTotal={1}
        initialStatus=""
        initialMinScore={null}
      />,
    );
    fireEvent.change(screen.getByTestId("signal-filter-status"), {
      target: { value: "rejected" },
    });
    await waitFor(() =>
      expect(mockNav.push).toHaveBeenCalledWith(
        expect.stringContaining("status=rejected"),
      ),
    );
    await waitFor(() =>
      expect(fetchSignals).toHaveBeenCalledWith(
        expect.objectContaining({ status: "rejected" }),
      ),
    );
  });

  it("applying min_signal_score sends the numeric value", async () => {
    fetchSignals.mockResolvedValue({
      items: [],
      total: 0,
      limit: 50,
      offset: 0,
    });
    render(
      <SignalsPanel
        initialItems={[makeSig()]}
        initialTotal={1}
        initialStatus=""
        initialMinScore={null}
      />,
    );
    fireEvent.change(screen.getByTestId("signal-filter-min-score"), {
      target: { value: "75" },
    });
    fireEvent.click(screen.getByTestId("signal-filter-apply"));

    await waitFor(() =>
      expect(mockNav.push).toHaveBeenCalledWith(
        expect.stringContaining("min_signal_score=75"),
      ),
    );
    await waitFor(() =>
      expect(fetchSignals).toHaveBeenCalledWith(
        expect.objectContaining({ min_signal_score: 75 }),
      ),
    );
  });

  it("reset button clears filters and pushes a clean URL", async () => {
    fetchSignals.mockResolvedValue({
      items: [],
      total: 0,
      limit: 50,
      offset: 0,
    });
    render(
      <SignalsPanel
        initialItems={[makeSig()]}
        initialTotal={1}
        initialStatus="rejected"
        initialMinScore={50}
      />,
    );
    fireEvent.click(screen.getByTestId("signal-filter-reset"));
    await waitFor(() =>
      expect(mockNav.push).toHaveBeenCalledWith("/admin/signals"),
    );
    await waitFor(() =>
      expect(fetchSignals).toHaveBeenCalledWith(
        expect.objectContaining({
          status: undefined,
          min_signal_score: undefined,
        }),
      ),
    );
  });
});