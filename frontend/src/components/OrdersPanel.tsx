"use client";

/**
 * OrdersPanel — operator dashboard for the v2.0 commercial-order system.
 *
 * Server-side fetches `fetchOrders()` + `fetchOrderStats()` and hands
 * the initial data to this client component for interactivity
 * (filter changes, status transitions, refresh).
 */

import { useCallback, useState } from "react";

import { fetchOrders, fetchOrderStats, updateOrderStatus } from "@/lib/api";
import type {
  DeliveryStatus,
  OrderChannel,
  OrderListResponse,
  OrderRecord,
  OrderStatsResponse,
} from "@/types";

const CHANNEL_LABELS: Record<string, string> = {
  xianyu: "闲鱼",
  xiaohongshu: "小红书",
  wechat: "微信",
  wechat_article: "公众号",
  feishu: "飞书群",
  direct: "直接联系",
  other: "其他",
};

const DELIVERY_LABELS: Record<DeliveryStatus, string> = {
  pending: "待发货",
  delivered: "已发货",
  confirmed: "已确认",
  refunded: "已退款",
  cancelled: "已取消",
};

const NEXT_STATUS: Record<DeliveryStatus, DeliveryStatus | null> = {
  pending: "delivered",
  delivered: "confirmed",
  confirmed: "refunded",
  refunded: null,
  cancelled: null,
};

export interface OrdersPanelProps {
  initialItems: OrderRecord[];
  initialTotal: number;
  initialStats: OrderStatsResponse;
}

export function OrdersPanel({
  initialItems,
  initialTotal,
  initialStats,
}: OrdersPanelProps) {
  const [items, setItems] = useState<OrderRecord[]>(initialItems);
  const [total, setTotal] = useState<number>(initialTotal);
  const [stats, setStats] = useState<OrderStatsResponse>(initialStats);
  const [channel, setChannel] = useState<string>("");
  const [deliveryStatus, setDeliveryStatus] = useState<string>("");
  const [busyId, setBusyId] = useState<number | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [toast, setToast] = useState<{ kind: "ok" | "err"; text: string } | null>(
    null,
  );

  const showToast = useCallback(
    (kind: "ok" | "err", text: string) => {
      setToast({ kind, text });
      window.setTimeout(() => setToast(null), 2500);
    },
    [],
  );

  const refresh = useCallback(async () => {
    setRefreshing(true);
    try {
      const [list, freshStats] = await Promise.all([
        fetchOrders({
          channel: channel || undefined,
          delivery_status: deliveryStatus || undefined,
          limit: 100,
        }),
        fetchOrderStats(),
      ]);
      setItems(list.items);
      setTotal(list.total);
      setStats(freshStats);
    } catch (err) {
      showToast("err", (err as Error).message);
    } finally {
      setRefreshing(false);
    }
  }, [channel, deliveryStatus, showToast]);

  const applyFilter = useCallback(
    async (key: "channel" | "delivery_status", value: string) => {
      if (key === "channel") setChannel(value);
      if (key === "delivery_status") setDeliveryStatus(value);
      const params: { channel?: string; delivery_status?: string; limit: number } = {
        limit: 100,
      };
      if (key === "channel" ? value : channel) params.channel = key === "channel" ? value : channel;
      if (key === "delivery_status" ? value : deliveryStatus) {
        params.delivery_status = key === "delivery_status" ? value : deliveryStatus;
      }
      try {
        const list = await fetchOrders(params);
        setItems(list.items);
        setTotal(list.total);
      } catch (err) {
        showToast("err", (err as Error).message);
      }
    },
    [channel, deliveryStatus, showToast],
  );

  const transition = useCallback(
    async (order: OrderRecord, next: DeliveryStatus) => {
      setBusyId(order.id);
      try {
        const updated = await updateOrderStatus(order.id, next);
        setItems((prev) => prev.map((it) => (it.id === order.id ? updated : it)));
        showToast("ok", `订单 #${order.id} → ${DELIVERY_LABELS[next]}`);
        // Refresh stats (revenue / counts change with status).
        fetchOrderStats().then(setStats).catch(() => undefined);
      } catch (err) {
        showToast("err", (err as Error).message);
      } finally {
        setBusyId(null);
      }
    },
    [showToast],
  );

  return (
    <div className="space-y-8" data-testid="orders-panel">
      {/* Stats cards */}
      <section
        className="grid gap-4 md:grid-cols-4"
        data-testid="orders-stats"
      >
        <StatCard
          label="总订单"
          value={String(stats.total_orders)}
          testid="stat-total-orders"
        />
        <StatCard
          label="总营收 (CNY)"
          value={`¥${stats.total_revenue_cny.toFixed(2)}`}
          testid="stat-total-revenue"
        />
        <StatCard
          label="已成交 (delivered + confirmed)"
          value={String(stats.delivered_count + stats.confirmed_count)}
          testid="stat-confirmed"
        />
        <StatCard
          label="待发货"
          value={String(stats.pending_count)}
          testid="stat-pending"
        />
      </section>

      {/* By-channel breakdown */}
      {stats.by_channel.length > 0 && (
        <section className="rounded-xl border border-border p-4 text-sm">
          <h3 className="mb-2 font-semibold">按销售渠道</h3>
          <ul className="space-y-1">
            {stats.by_channel.map((row) => (
              <li
                key={row.channel}
                className="flex justify-between text-xs"
                data-testid={`channel-row-${row.channel}`}
              >
                <span>{CHANNEL_LABELS[row.channel] ?? row.channel}</span>
                <span className="font-mono">
                  {row.count} 单 · ¥{row.revenue_cny.toFixed(2)}
                </span>
              </li>
            ))}
          </ul>
        </section>
      )}

      {/* Filters + table */}
      <section className="space-y-3">
        <div className="flex flex-wrap items-center gap-2">
          <label className="text-xs text-muted-foreground">
            渠道
            <select
              value={channel}
              onChange={(e) => applyFilter("channel", e.target.value)}
              className="ml-1 rounded-md border border-border bg-background px-2 py-1 text-xs"
              data-testid="filter-channel"
            >
              <option value="">全部</option>
              {Object.entries(CHANNEL_LABELS).map(([v, l]) => (
                <option key={v} value={v}>
                  {l}
                </option>
              ))}
            </select>
          </label>
          <label className="text-xs text-muted-foreground">
            状态
            <select
              value={deliveryStatus}
              onChange={(e) => applyFilter("delivery_status", e.target.value)}
              className="ml-1 rounded-md border border-border bg-background px-2 py-1 text-xs"
              data-testid="filter-delivery-status"
            >
              <option value="">全部</option>
              {Object.entries(DELIVERY_LABELS).map(([v, l]) => (
                <option key={v} value={v}>
                  {l}
                </option>
              ))}
            </select>
          </label>
          <button
            onClick={refresh}
            disabled={refreshing}
            className="ml-auto rounded-md border border-border px-3 py-1 text-xs hover:bg-muted disabled:opacity-50"
            data-testid="orders-refresh"
          >
            {refreshing ? "刷新中…" : "刷新"}
          </button>
        </div>

        {items.length === 0 ? (
          <div
            className="rounded-xl border border-dashed border-border p-12 text-center text-sm text-muted-foreground"
            data-testid="orders-empty"
          >
            还没有订单记录。
            <br />
            <span className="text-xs">
              在
              <code className="mx-1 rounded bg-muted px-1">Content Center</code>
              点击&ldquo;标记已售出&rdquo;开始记录销售。
            </span>
          </div>
        ) : (
          <div className="overflow-x-auto rounded-xl border border-border">
            <table
              className="w-full text-xs"
              data-testid="orders-table"
            >
              <thead className="bg-muted/30 text-left">
                <tr>
                  <th className="px-3 py-2 font-medium">ID</th>
                  <th className="px-3 py-2 font-medium">机会</th>
                  <th className="px-3 py-2 font-medium">客户</th>
                  <th className="px-3 py-2 font-medium">金额</th>
                  <th className="px-3 py-2 font-medium">渠道</th>
                  <th className="px-3 py-2 font-medium">状态</th>
                  <th className="px-3 py-2 font-medium">时间</th>
                  <th className="px-3 py-2 font-medium"></th>
                </tr>
              </thead>
              <tbody>
                {items.map((o) => {
                  const next = NEXT_STATUS[o.delivery_status];
                  return (
                    <tr
                      key={o.id}
                      className="border-t border-border"
                      data-testid={`order-row-${o.id}`}
                    >
                      <td className="px-3 py-2 font-mono">#{o.id}</td>
                      <td className="px-3 py-2">
                        <a
                          href={`/opportunities/${o.opportunity_id}`}
                          className="text-accent hover:underline"
                        >
                          {o.opportunity_title ?? `#${o.opportunity_id}`}
                        </a>
                      </td>
                      <td className="px-3 py-2">
                        <div>{o.customer_name}</div>
                        {o.customer_contact && (
                          <div className="text-[10px] text-muted-foreground">
                            {o.customer_contact}
                          </div>
                        )}
                      </td>
                      <td className="px-3 py-2 font-mono">
                        ¥{o.amount_cny.toFixed(2)}
                      </td>
                      <td className="px-3 py-2">
                        {CHANNEL_LABELS[o.channel] ?? o.channel}
                      </td>
                      <td className="px-3 py-2">
                        <span
                          className={
                            "rounded-full px-2 py-0.5 " +
                            deliveryStatusClass(o.delivery_status)
                          }
                          data-testid={`delivery-status-${o.id}`}
                        >
                          {DELIVERY_LABELS[o.delivery_status] ?? o.delivery_status}
                        </span>
                      </td>
                      <td className="px-3 py-2 text-[10px] text-muted-foreground">
                        {formatDate(o.created_at)}
                      </td>
                      <td className="px-3 py-2">
                        {next && (
                          <button
                            onClick={() => transition(o, next)}
                            disabled={busyId === o.id}
                            className="rounded border border-border px-2 py-0.5 text-[10px] font-medium hover:bg-muted disabled:opacity-50"
                            data-testid={`advance-${o.id}`}
                          >
                            → {DELIVERY_LABELS[next]}
                          </button>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
            {total > items.length && (
              <div className="border-t border-border px-3 py-2 text-[10px] text-muted-foreground">
                显示前 {items.length} 条,共 {total} 条
              </div>
            )}
          </div>
        )}
      </section>

      {toast && (
        <div
          role="status"
          className={
            "fixed bottom-6 left-1/2 -translate-x-1/2 rounded-md px-4 py-2 text-sm shadow-lg " +
            (toast.kind === "ok"
              ? "bg-emerald-600 text-white"
              : "bg-red-600 text-white")
          }
        >
          {toast.text}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
function StatCard({
  label,
  value,
  testid,
}: {
  label: string;
  value: string;
  testid: string;
}) {
  return (
    <div
      className="rounded-xl border border-border bg-card/40 p-4"
      data-testid={testid}
    >
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className="mt-2 text-2xl font-semibold">{value}</div>
    </div>
  );
}

function deliveryStatusClass(status: string): string {
  switch (status) {
    case "confirmed":
      return "bg-emerald-500/20 text-emerald-300";
    case "delivered":
      return "bg-blue-500/20 text-blue-300";
    case "refunded":
      return "bg-amber-500/20 text-amber-300";
    case "cancelled":
      return "bg-zinc-500/20 text-zinc-300";
    case "pending":
    default:
      return "bg-orange-500/20 text-orange-300";
  }
}

function formatDate(iso: string | null | undefined): string {
  if (!iso) return "";
  try {
    const d = new Date(iso);
    return `${d.getMonth() + 1}/${d.getDate()} ${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
  } catch {
    return iso;
  }
}
