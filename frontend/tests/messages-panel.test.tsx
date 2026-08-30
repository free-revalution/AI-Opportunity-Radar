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
  usePathname: () => "/admin/messages",
}));

import { MessagesPanel } from "@/components/MessagesPanel";
import { fetchNotifications } from "@/lib/api";
import type { NotificationItem, NotificationListResponse } from "@/types";

vi.mock("@/lib/api", () => ({
  fetchNotifications: vi.fn(),
}));

// ---- Fixtures -------------------------------------------------------------
function makeItem(overrides: Partial<NotificationItem> = {}): NotificationItem {
  return {
    id: 1,
    channel: "feishu",
    kind: "activation_code_issued",
    payload: {
      kind: "activation_code_issued",
      activation_code_id: 42,
      plan: "pro",
      code_preview: "ABCD…WXYZ",
    },
    delivered_at: "2026-08-30T14:35:00Z",
    error: null,
    failed: false,
    deep_link: "/admin/activation?id=42",
    created_at: "2026-08-30T14:35:00Z",
    ...overrides,
  };
}

function makeResponse(
  overrides: Partial<NotificationListResponse> = {},
): NotificationListResponse {
  return {
    items: [],
    total: 0,
    limit: 50,
    offset: 0,
    ...overrides,
  };
}

const mockedFetch = vi.mocked(fetchNotifications);

beforeEach(() => {
  mockReplace.mockClear();
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
describe("MessagesPanel", () => {
  it("renders 4 stat cards + empty state when no rows", () => {
    render(
      <MessagesPanel
        initial={makeResponse({ items: [], total: 0 })}
        initialFilters={{}}
      />,
    );
    expect(screen.getByTestId("messages-panel")).toBeInTheDocument();
    expect(screen.getByTestId("stat-total")).toHaveTextContent("0");
    expect(screen.getByTestId("stat-activation")).toHaveTextContent("0");
    expect(screen.getByTestId("stat-reminder")).toHaveTextContent("0");
    expect(screen.getByTestId("stat-failed")).toHaveTextContent("0");
    expect(screen.getByTestId("messages-empty")).toHaveTextContent(
      "暂无消息",
    );
  });

  it("renders rows with kind/channel chips, payload summary, deep-link", () => {
    const items = [
      makeItem({
        id: 10,
        kind: "activation_code_issued",
        payload: {
          kind: "activation_code_issued",
          activation_code_id: 42,
          plan: "pro",
          code_preview: "ABCD…WXYZ",
        },
        deep_link: "/admin/activation?id=42",
      }),
      makeItem({
        id: 11,
        kind: "subscription_renewal_reminder",
        channel: "feishu",
        payload: {
          kind: "subscription_renewal_reminder",
          subscription_id: 7,
          plan: "basic",
          days_until: 2,
        },
        delivered_at: "2026-08-30T14:36:00Z",
        error: null,
        failed: false,
        deep_link: "/admin/subscriptions?id=7",
      }),
    ];
    render(
      <MessagesPanel
        initial={makeResponse({ items, total: 2 })}
        initialFilters={{}}
      />,
    );

    // Row 10: activation code issued.
    expect(screen.getByTestId("messages-row-10")).toBeInTheDocument();
    expect(screen.getByTestId("messages-channel-10")).toHaveTextContent("feishu");
    expect(screen.getByTestId("messages-kind-10")).toHaveTextContent("激活码发放");
    expect(screen.getByTestId("messages-status-10")).toHaveTextContent("sent");
    expect(screen.getByTestId("messages-row-10")).toHaveTextContent("#42");
    expect(screen.getByTestId("messages-row-10")).toHaveTextContent("pro");
    expect(screen.getByTestId("messages-link-10").getAttribute("href")).toBe(
      "/admin/activation?id=42",
    );

    // Row 11: subscription renewal reminder.
    expect(screen.getByTestId("messages-row-11")).toBeInTheDocument();
    expect(screen.getByTestId("messages-kind-11")).toHaveTextContent("续期提醒");
    expect(screen.getByTestId("messages-row-11")).toHaveTextContent("basic");
    expect(screen.getByTestId("messages-row-11")).toHaveTextContent("2d until expiry");
    expect(screen.getByTestId("messages-link-11").getAttribute("href")).toBe(
      "/admin/subscriptions?id=7",
    );

    // Stat counts reflect the page contents.
    expect(screen.getByTestId("stat-activation")).toHaveTextContent("1");
    expect(screen.getByTestId("stat-reminder")).toHaveTextContent("1");
    expect(screen.getByTestId("stat-failed")).toHaveTextContent("0");
  });

  it("failing rows render with a red FAILED chip and row highlight", () => {
    const items = [
      makeItem({
        id: 20,
        // Use an unknown kind so notificationDeepLink returns null —
        // no `activation_code_id` / `subscription_id` keys means no
        // resource can be derived.
        kind: "some_other_kind",
        payload: { kind: "some_other_kind", plan: "pro" },
        channel: "feishu",
        delivered_at: null,
        error: "robot disabled (code=230001)",
        failed: true,
        deep_link: null,
      }),
    ];
    render(
      <MessagesPanel
        initial={makeResponse({ items, total: 1 })}
        initialFilters={{}}
      />,
    );

    const row = screen.getByTestId("messages-row-20");
    expect(row).toHaveClass("bg-red-500/5");
    expect(screen.getByTestId("messages-status-20")).toHaveTextContent("FAILED");
    expect(screen.getByTestId("stat-failed")).toHaveTextContent("1");
    // No deep-link button when payload doesn't have a recognised key.
    expect(screen.queryByTestId("messages-link-20")).toBeNull();
  });

  it("clicking a kind chip updates the URL and re-fetches with the filter", async () => {
    render(
      <MessagesPanel
        initial={makeResponse({ items: [], total: 0 })}
        initialFilters={{}}
      />,
    );

    fireEvent.click(screen.getByTestId("chip-kind-subscription_renewal_reminder"));

    await waitFor(() => {
      expect(mockReplace).toHaveBeenCalledWith(
        "/admin/messages?kind=subscription_renewal_reminder",
        expect.objectContaining({ scroll: false }),
      );
    });
    expect(mockedFetch).toHaveBeenCalledWith(
      expect.objectContaining({
        kind: "subscription_renewal_reminder",
        offset: 0,
        limit: 50,
      }),
    );
  });

  it("clicking a channel chip pushes the channel filter", async () => {
    render(
      <MessagesPanel
        initial={makeResponse({ items: [], total: 0 })}
        initialFilters={{}}
      />,
    );

    fireEvent.click(screen.getByTestId("chip-channel-telegram"));

    await waitFor(() => {
      expect(mockReplace).toHaveBeenCalledWith(
        "/admin/messages?channel=telegram",
        expect.objectContaining({ scroll: false }),
      );
    });
  });

  it("the Reset button clears all filters and re-fetches", async () => {
    render(
      <MessagesPanel
        initial={makeResponse({ items: [], total: 0 })}
        initialFilters={{ kind: "activation_code_issued", channel: "feishu" }}
      />,
    );

    fireEvent.click(screen.getByTestId("btn-reset"));

    await waitFor(() => {
      expect(mockReplace).toHaveBeenCalledWith(
        "/admin/messages",
        expect.objectContaining({ scroll: false }),
      );
    });
    expect(mockedFetch).toHaveBeenCalledWith(
      expect.objectContaining({
        kind: undefined,
        channel: undefined,
        offset: 0,
      }),
    );
  });

  it("clicking a row's ··· button expands the payload drawer; second click collapses", async () => {
    const item = makeItem({
      id: 30,
      payload: {
        kind: "activation_code_issued",
        activation_code_id: 42,
        plan: "pro",
        code_preview: "ABCD…WXYZ",
        message_id: "om_xxx",
      },
    });
    render(
      <MessagesPanel
        initial={makeResponse({ items: [item], total: 1 })}
        initialFilters={{}}
      />,
    );

    expect(screen.queryByTestId("messages-meta-30")).toBeNull();

    fireEvent.click(screen.getByTestId("messages-toggle-30"));
    expect(screen.getByTestId("messages-meta-30")).toBeInTheDocument();
    expect(screen.getByTestId("messages-meta-pre-30")).toHaveTextContent(
      '"message_id": "om_xxx"',
    );

    fireEvent.click(screen.getByTestId("messages-toggle-30"));
    expect(screen.queryByTestId("messages-meta-30")).toBeNull();
  });

  it("renders an error banner when the fetch fails", async () => {
    mockedFetch.mockRejectedValueOnce(new Error("network down"));

    render(
      <MessagesPanel
        initial={makeResponse({ items: [], total: 0 })}
        initialFilters={{}}
      />,
    );

    fireEvent.click(screen.getByTestId("btn-reset"));

    await waitFor(() => {
      expect(screen.getByTestId("messages-error")).toHaveTextContent(
        "network down",
      );
    });
  });

  it("pagination: next pushes offset and re-fetches; prev is disabled on page 1", async () => {
    const items = Array.from({ length: 50 }, (_, i) =>
      makeItem({ id: i + 1, payload: { kind: "activation_code_issued" } }),
    );
    render(
      <MessagesPanel
        initial={makeResponse({ items, total: 120, limit: 50, offset: 0 })}
        initialFilters={{}}
      />,
    );

    expect(screen.getByTestId("btn-prev")).toBeDisabled();
    expect(screen.getByTestId("btn-next")).not.toBeDisabled();

    fireEvent.click(screen.getByTestId("btn-next"));

    await waitFor(() => {
      expect(mockReplace).toHaveBeenCalledWith(
        "/admin/messages?offset=50",
        expect.objectContaining({ scroll: false }),
      );
    });
    expect(mockedFetch).toHaveBeenCalledWith(
      expect.objectContaining({ offset: 50 }),
    );
  });
});
