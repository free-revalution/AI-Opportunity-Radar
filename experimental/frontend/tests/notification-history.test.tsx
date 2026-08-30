import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

const fetchRecentNotifications = vi.fn();
vi.mock("@/lib/api", () => ({
  fetchRecentNotifications: (limit?: number) => fetchRecentNotifications(limit),
}));

import { NotificationHistory } from "@/components/NotificationHistory";

describe("NotificationHistory", () => {
  it("renders the empty state when no items are returned", async () => {
    fetchRecentNotifications.mockResolvedValueOnce({ count: 0, items: [] });
    render(await NotificationHistory({}));
    expect(
      screen.getByText(/No notifications yet/i),
    ).toBeInTheDocument();
  });

  it("renders the error state when the backend is unreachable", async () => {
    fetchRecentNotifications.mockRejectedValueOnce(new Error("boom"));
    render(await NotificationHistory({}));
    expect(
      screen.getByText(/backend may be unreachable/i),
    ).toBeInTheDocument();
  });

  it("renders one row per notification with the correct status pill", async () => {
    fetchRecentNotifications.mockResolvedValueOnce({
      count: 2,
      items: [
        {
          id: 1,
          channel: "telegram",
          payload: { kind: "digest", chat_id: "100", entry_ids: ["a", "b"] },
          delivered_at: "2026-08-26T10:00:00Z",
          error: null,
          created_at: new Date(Date.now() - 60_000).toISOString(),
        },
        {
          id: 2,
          channel: "telegram",
          payload: { kind: "opportunity_alert", chat_id: "200" },
          delivered_at: null,
          error: "Telegram 400: bad chat id",
          created_at: new Date(Date.now() - 5 * 60_000).toISOString(),
        },
      ],
    });
    render(await NotificationHistory({}));
    const items = screen.getAllByTestId("notification-item");
    expect(items.length).toBe(2);
    expect(items[0]).toHaveTextContent("delivered");
    expect(items[0]).toHaveTextContent("digest");
    expect(items[0]).toHaveTextContent("2 opportunities");
    expect(items[1]).toHaveTextContent("failed");
    expect(items[1]).toHaveTextContent("Telegram 400");
  });

  it("forwards the limit prop to the fetcher", async () => {
    fetchRecentNotifications.mockResolvedValueOnce({ count: 0, items: [] });
    await NotificationHistory({ limit: 4 });
    expect(fetchRecentNotifications).toHaveBeenCalledWith(4);
  });
});
