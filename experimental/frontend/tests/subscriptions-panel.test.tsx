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
  usePathname: () => "/admin/subscriptions",
}));

import { SubscriptionsPanel } from "@/components/SubscriptionsPanel";
import {
  cancelSubscription,
  extendSubscription,
  fetchSubscriptions,
} from "@/lib/api";
import type {
  Subscription,
  SubscriptionListResponse,
} from "@/types";

vi.mock("@/lib/api", () => ({
  fetchSubscriptions: vi.fn(),
  extendSubscription: vi.fn(),
  cancelSubscription: vi.fn(),
}));

function makeSub(overrides: Partial<Subscription> = {}): Subscription {
  return {
    id: 1,
    user_id: null,
    feishu_open_id: "ou_test_1",
    plan: "basic",
    status: "active",
    source_channel: "feishu",
    starts_at: "2026-08-01T00:00:00Z",
    expires_at: "2026-09-01T00:00:00Z",
    created_at: "2026-08-01T00:00:00Z",
    updated_at: "2026-08-30T12:00:00Z",
    ...overrides,
  };
}

function makeResponse(
  overrides: Partial<SubscriptionListResponse> = {},
): SubscriptionListResponse {
  return { count: 0, items: [], ...overrides };
}

const mockedFetch = vi.mocked(fetchSubscriptions);
const mockedExtend = vi.mocked(extendSubscription);
const mockedCancel = vi.mocked(cancelSubscription);

beforeEach(() => {
  mockReplace.mockClear();
  Array.from(mockSearchParams.keys()).forEach((k) =>
    mockSearchParams.delete(k),
  );
  mockedFetch.mockReset();
  mockedFetch.mockResolvedValue(makeResponse());
  mockedExtend.mockReset();
  mockedCancel.mockReset();
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("SubscriptionsPanel", () => {
  it("renders empty state when no subscriptions", () => {
    render(
      <SubscriptionsPanel
        initial={makeResponse({ items: [], count: 0 })}
        initialFilters={{}}
      />,
    );
    expect(screen.getByTestId("subscriptions-panel")).toBeInTheDocument();
    expect(screen.getByTestId("subscriptions-empty")).toHaveTextContent(
      "没有订阅记录",
    );
    expect(screen.getByTestId("stat-total")).toHaveTextContent("0");
  });

  it("renders one row per subscription with plan, status chip, expires, audit link", () => {
    const items = [
      makeSub({ id: 1, plan: "basic", status: "active" }),
      makeSub({
        id: 2,
        plan: "pro",
        status: "cancelled",
        feishu_open_id: "ou_xyz",
      }),
    ];
    render(
      <SubscriptionsPanel
        initial={makeResponse({ items, count: 2 })}
        initialFilters={{}}
      />,
    );
    expect(screen.getByTestId("sub-row-1")).toBeInTheDocument();
    expect(screen.getByTestId("sub-status-1")).toHaveTextContent("活跃");
    expect(screen.getByTestId("sub-extend-1")).toBeInTheDocument();
    expect(screen.getByTestId("sub-cancel-1")).toBeInTheDocument();

    expect(screen.getByTestId("sub-row-2")).toBeInTheDocument();
    expect(screen.getByTestId("sub-status-2")).toHaveTextContent("已取消");
    // Cancel button hidden for already-cancelled rows.
    expect(screen.queryByTestId("sub-cancel-2")).toBeNull();

    expect(screen.getByTestId("stat-active")).toHaveTextContent("1");
    expect(screen.getByTestId("stat-cancelled")).toHaveTextContent("1");

    expect(screen.getByTestId("sub-audit-1").getAttribute("href")).toBe(
      "/admin/audit-logs?resource_type=subscription&resource_id=1",
    );
  });

  it("clicking a status chip updates URL and re-fetches", async () => {
    render(
      <SubscriptionsPanel
        initial={makeResponse({ items: [], count: 0 })}
        initialFilters={{}}
      />,
    );

    fireEvent.click(screen.getByTestId("chip-status-active"));

    await waitFor(() => {
      expect(mockReplace).toHaveBeenCalledWith(
        "/admin/subscriptions?status=active",
        expect.objectContaining({ scroll: false }),
      );
    });
    expect(mockedFetch).toHaveBeenCalledWith(
      expect.objectContaining({ status: "active" }),
    );
  });

  it("Reset button clears filters and re-fetches", async () => {
    render(
      <SubscriptionsPanel
        initial={makeResponse({ items: [], count: 0 })}
        initialFilters={{
          status: "active",
          plan: "pro",
          feishu_open_id: "ou_xyz",
        }}
      />,
    );
    fireEvent.click(screen.getByTestId("btn-reset"));
    await waitFor(() => {
      expect(mockReplace).toHaveBeenCalledWith(
        "/admin/subscriptions",
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

  it("Extend flow: open modal, change days, submit calls extendSubscription + refresh", async () => {
    const sub = makeSub({ id: 5, status: "active" });
    const extended = makeSub({
      id: 5,
      status: "active",
      expires_at: "2026-12-01T00:00:00Z",
    });
    mockedExtend.mockResolvedValueOnce(extended);
    mockedFetch.mockResolvedValueOnce(
      makeResponse({ items: [extended], count: 1 }),
    );

    render(
      <SubscriptionsPanel
        initial={makeResponse({ items: [sub], count: 1 })}
        initialFilters={{}}
      />,
    );

    fireEvent.click(screen.getByTestId("sub-extend-5"));
    expect(screen.getByTestId("extend-modal")).toBeInTheDocument();
    expect(screen.getByTestId("extend-form")).toHaveTextContent(
      "当前到期",
    );

    fireEvent.change(screen.getByTestId("extend-days"), {
      target: { value: "60" },
    });
    fireEvent.click(screen.getByTestId("extend-submit"));

    await waitFor(() => {
      expect(mockedExtend).toHaveBeenCalledWith(5, { days: 60 });
    });
    await waitFor(() => {
      expect(screen.queryByTestId("extend-modal")).toBeNull();
    });
    expect(screen.getByTestId("subscriptions-toast")).toHaveTextContent(
      "已延期",
    );
  });

  it("Cancel flow: confirm modal → cancelSubscription + refresh", async () => {
    const sub = makeSub({ id: 7, status: "active" });
    const cancelled = makeSub({ id: 7, status: "cancelled" });
    mockedCancel.mockResolvedValueOnce(cancelled);
    mockedFetch.mockResolvedValueOnce(
      makeResponse({ items: [cancelled], count: 1 }),
    );

    render(
      <SubscriptionsPanel
        initial={makeResponse({ items: [sub], count: 1 })}
        initialFilters={{}}
      />,
    );

    fireEvent.click(screen.getByTestId("sub-cancel-7"));
    expect(screen.getByTestId("cancel-modal")).toBeInTheDocument();
    expect(screen.getByTestId("cancel-form")).toHaveTextContent("#7");

    fireEvent.click(screen.getByTestId("cancel-submit"));

    await waitFor(() => {
      expect(mockedCancel).toHaveBeenCalledWith(7);
    });
    await waitFor(() => {
      expect(screen.queryByTestId("cancel-modal")).toBeNull();
    });
    expect(screen.getByTestId("subscriptions-toast")).toHaveTextContent(
      "已取消",
    );
  });

  it("shows an error banner when fetch fails", async () => {
    mockedFetch.mockRejectedValueOnce(new Error("network down"));

    render(
      <SubscriptionsPanel
        initial={makeResponse({ items: [], count: 0 })}
        initialFilters={{}}
      />,
    );

    fireEvent.click(screen.getByTestId("btn-reset"));

    await waitFor(() => {
      expect(screen.getByTestId("subscriptions-error")).toHaveTextContent(
        "network down",
      );
    });
  });
});
