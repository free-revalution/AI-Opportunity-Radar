import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// ---- Mock the api module ---------------------------------------------------
const fetchOrders = vi.fn();
const fetchOrderStats = vi.fn();
const updateOrderStatus = vi.fn();

vi.mock("@/lib/api", () => ({
  fetchOrders: (...args: unknown[]) =>
    (fetchOrders as (...args: unknown[]) => unknown)(...args),
  fetchOrderStats: (...args: unknown[]) =>
    (fetchOrderStats as (...args: unknown[]) => unknown)(...args),
  updateOrderStatus: (...args: unknown[]) =>
    (updateOrderStatus as (...args: unknown[]) => unknown)(...args),
}));

import { OrdersPanel } from "@/components/OrdersPanel";
import type { OrderRecord, OrderStatsResponse } from "@/types";

// ---- Fixtures --------------------------------------------------------------
function makeOrder(overrides: Partial<OrderRecord> = {}): OrderRecord {
  return {
    id: 1,
    opportunity_id: 42,
    opportunity_title: "AI 法律合同审核",
    customer_name: "张三",
    customer_contact: "wechat:zhangsan",
    amount_cny: 49,
    channel: "xianyu",
    payment_method: "wechat",
    payment_reference: "xy-2026-0001",
    delivery_status: "pending",
    commercial_status_snapshot: "qualified",
    notes: null,
    created_at: "2026-08-27T10:00:00Z",
    updated_at: "2026-08-27T10:00:00Z",
    ...overrides,
  };
}

function makeStats(overrides: Partial<OrderStatsResponse> = {}): OrderStatsResponse {
  return {
    total_orders: 3,
    total_revenue_cny: 177,
    delivered_count: 1,
    confirmed_count: 1,
    pending_count: 1,
    by_channel: [
      { channel: "xianyu", count: 2, revenue_cny: 148 },
      { channel: "xiaohongshu", count: 1, revenue_cny: 29 },
    ],
    by_delivery_status: {
      pending: 1,
      delivered: 1,
      confirmed: 1,
    },
    ...overrides,
  };
}

const EMPTY_STATS: OrderStatsResponse = {
  total_orders: 0,
  total_revenue_cny: 0,
  delivered_count: 0,
  confirmed_count: 0,
  pending_count: 0,
  by_channel: [],
  by_delivery_status: {},
};

// ---- Tests -----------------------------------------------------------------
describe("OrdersPanel", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  it("renders the empty state when no orders exist", () => {
    render(
      <OrdersPanel initialItems={[]} initialTotal={0} initialStats={EMPTY_STATS} />,
    );
    expect(screen.getByTestId("orders-empty")).toBeInTheDocument();
  });

  it("renders stats cards with the supplied totals", () => {
    render(
      <OrdersPanel
        initialItems={[makeOrder()]}
        initialTotal={1}
        initialStats={makeStats()}
      />,
    );
    expect(screen.getByTestId("stat-total-orders")).toHaveTextContent("3");
    expect(screen.getByTestId("stat-total-revenue")).toHaveTextContent("¥177.00");
    expect(screen.getByTestId("stat-confirmed")).toHaveTextContent("2");
    expect(screen.getByTestId("stat-pending")).toHaveTextContent("1");
  });

  it("renders the by-channel breakdown", () => {
    render(
      <OrdersPanel
        initialItems={[makeOrder()]}
        initialTotal={1}
        initialStats={makeStats()}
      />,
    );
    expect(screen.getByTestId("channel-row-xianyu")).toHaveTextContent("2 单");
    expect(screen.getByTestId("channel-row-xianyu")).toHaveTextContent("¥148.00");
    expect(screen.getByTestId("channel-row-xiaohongshu")).toHaveTextContent("1 单");
  });

  it("renders one row per order with status pill", () => {
    render(
      <OrdersPanel
        initialItems={[
          makeOrder({ id: 1, delivery_status: "pending" }),
          makeOrder({ id: 2, delivery_status: "delivered" }),
        ]}
        initialTotal={2}
        initialStats={EMPTY_STATS}
      />,
    );
    expect(screen.getByTestId("order-row-1")).toBeInTheDocument();
    expect(screen.getByTestId("order-row-2")).toBeInTheDocument();
    expect(screen.getByTestId("delivery-status-1")).toHaveTextContent("待发货");
    expect(screen.getByTestId("delivery-status-2")).toHaveTextContent("已发货");
  });

  it("shows the next-step button when a transition is allowed", () => {
    render(
      <OrdersPanel
        initialItems={[makeOrder({ id: 1, delivery_status: "pending" })]}
        initialTotal={1}
        initialStats={EMPTY_STATS}
      />,
    );
    const advance = screen.getByTestId("advance-1");
    expect(advance).toHaveTextContent("已发货");
  });

  it("hides the next-step button for terminal statuses", () => {
    render(
      <OrdersPanel
        initialItems={[
          makeOrder({ id: 10, delivery_status: "refunded" }),
          makeOrder({ id: 11, delivery_status: "cancelled" }),
        ]}
        initialTotal={2}
        initialStats={EMPTY_STATS}
      />,
    );
    expect(screen.queryByTestId("advance-10")).toBeNull();
    expect(screen.queryByTestId("advance-11")).toBeNull();
  });

  it("calls updateOrderStatus when the advance button is clicked", async () => {
    updateOrderStatus.mockResolvedValueOnce({
      ...makeOrder({ id: 1 }),
      delivery_status: "delivered",
    });

    render(
      <OrdersPanel
        initialItems={[makeOrder({ id: 1, delivery_status: "pending" })]}
        initialTotal={1}
        initialStats={EMPTY_STATS}
      />,
    );

    fireEvent.click(screen.getByTestId("advance-1"));

    await waitFor(() =>
      expect(updateOrderStatus).toHaveBeenCalledWith(1, "delivered"),
    );
  });

  it("filters the list when the channel select changes", async () => {
    fetchOrders.mockResolvedValueOnce({
      generated_at: "2026-08-27T10:00:00Z",
      items: [makeOrder({ id: 1, channel: "xianyu" })],
      total: 1,
      limit: 100,
      offset: 0,
    });

    render(
      <OrdersPanel
        initialItems={[
          makeOrder({ id: 1, channel: "xianyu" }),
          makeOrder({ id: 2, channel: "xiaohongshu" }),
        ]}
        initialTotal={2}
        initialStats={EMPTY_STATS}
      />,
    );

    fireEvent.change(screen.getByTestId("filter-channel"), {
      target: { value: "xianyu" },
    });

    await waitFor(() => expect(fetchOrders).toHaveBeenCalled());
    expect(fetchOrders).toHaveBeenCalledWith(
      expect.objectContaining({ channel: "xianyu" }),
    );
  });
});
