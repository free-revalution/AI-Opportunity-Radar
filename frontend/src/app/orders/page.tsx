import { fetchOrderStats, fetchOrders } from "@/lib/api";

import { OrdersPanel } from "@/components/OrdersPanel";

/**
 * Orders dashboard — Phase 4 (v2.0) commercial-order centre.
 *
 * Server component: fetches initial list + stats, hands off to the
 * client-side OrdersPanel for filter + status interactions.
 */
export default async function OrdersPage() {
  let items: Awaited<ReturnType<typeof fetchOrders>>["items"] = [];
  let total = 0;
  let stats: Awaited<ReturnType<typeof fetchOrderStats>> | null = null;
  let errored = false;

  try {
    const [list, s] = await Promise.all([
      fetchOrders({ limit: 100 }),
      fetchOrderStats(),
    ]);
    items = list.items;
    total = list.total;
    stats = s;
  } catch {
    errored = true;
  }

  // stats is guaranteed non-null on the happy path; provide a safe
  // empty default if the backend is unreachable.
  const safeStats = stats ?? {
    total_orders: 0,
    total_revenue_cny: 0,
    delivered_count: 0,
    confirmed_count: 0,
    pending_count: 0,
    by_channel: [],
    by_delivery_status: {},
  };

  return (
    <main className="container py-10" data-testid="orders-page">
      <header className="mb-8">
        <span className="chip-accent">v2.0 · 商业订单管理</span>
        <h1 className="mt-3 text-3xl font-semibold">销售订单</h1>
        <p className="mt-2 max-w-2xl text-sm text-muted-foreground">
          每一笔实际发生的销售都会在这里记录:客户、金额、渠道、发货状态。
          订单由 Content Center 的&ldquo;标记已售出&rdquo;对话框创建,
          也可直接通过 API 录入。
        </p>
      </header>

      {errored ? (
        <p
          className="rounded-md border border-danger/40 bg-danger/10 p-4 text-sm"
          data-testid="orders-error"
        >
          加载失败:后端不可达。检查 docker compose 状态。
        </p>
      ) : (
        <OrdersPanel
          initialItems={items}
          initialTotal={total}
          initialStats={safeStats}
        />
      )}
    </main>
  );
}
