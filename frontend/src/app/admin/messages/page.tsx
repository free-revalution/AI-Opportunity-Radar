import { fetchNotifications } from "@/lib/api";
import { MessagesPanel, type MessagesFilters } from "@/components/MessagesPanel";

/**
 * Phase 23 — sole-operator IM delivery history viewer landing page.
 *
 * Server component reads the URL searchParams (filter form +
 * pagination), calls `fetchNotifications`, and hands the snapshot to
 * `<MessagesPanel/>` for client-side filter/expand/pagination.
 *
 * Mirrors `audit-logs/page.tsx` shape so the operator's mental model
 * transfers — same auth path, same error banner, same `data-testid`s.
 */
export default async function AdminMessagesPage({
  searchParams,
}: {
  searchParams?: Record<string, string | string[] | undefined>;
}) {
  const pickFirst = (v: string | string[] | undefined): string | undefined =>
    Array.isArray(v) ? v[0] : v;
  const str = (k: keyof MessagesFilters): string | undefined => {
    const v = pickFirst(searchParams?.[k]);
    return v && v.length > 0 ? v : undefined;
  };
  const filters: MessagesFilters = {};
  filters.kind = str("kind");
  filters.channel = str("channel");
  filters.since = str("since");
  filters.limit = 50;
  filters.offset = 0;

  let initial: Awaited<ReturnType<typeof fetchNotifications>> | null = null;
  let errored = false;

  try {
    initial = await fetchNotifications(filters);
  } catch {
    errored = true;
  }

  return (
    <main className="container py-10" data-testid="messages-page">
      <header className="mb-8">
        <span className="chip-accent">v2.0 · Admin · Messages</span>
        <h1 className="mt-3 text-3xl font-semibold">消息发送历史</h1>
        <p className="mt-2 max-w-2xl text-sm text-muted-foreground">
          Phase 23 全量 IM 发送审计:激活码发放 + 续期提醒 + 任何未来的
          outbound 消息。按 kind / channel / 时间 过滤,点行展开 payload。
          &ldquo;打开资源&rdquo; 按钮跳到对应的激活码 / 订阅页。
          <br />
          共 <span className="font-mono">{initial?.total ?? 0}</span> 条匹配。
        </p>
      </header>

      {errored || !initial ? (
        <p
          className="rounded-md border border-danger/40 bg-danger/10 p-4 text-sm"
          data-testid="messages-error-banner"
        >
          加载失败:后端不可达或 webhook secret 无效。检查 docker compose +
          sessionStorage。
        </p>
      ) : (
        <MessagesPanel initial={initial} initialFilters={filters} />
      )}
    </main>
  );
}
