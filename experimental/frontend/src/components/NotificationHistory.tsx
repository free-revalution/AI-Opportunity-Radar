import { fetchRecentNotifications } from "@/lib/api";
import { cn, formatRelativeTime } from "@/lib/utils";

/**
 * Recent-notifications activity feed — surfaces the last few rows from
 * the `notifications` table so the operator can spot digests that failed
 * to deliver without leaving the dashboard.
 */
export async function NotificationHistory({ limit = 8 }: { limit?: number }) {
  let data: Awaited<ReturnType<typeof fetchRecentNotifications>> | null = null;
  let errored = false;
  try {
    data = await fetchRecentNotifications(limit);
  } catch {
    errored = true;
  }

  return (
    <section
      className="glass rounded-xl p-6"
      aria-label="Recent notifications"
      data-testid="notification-history"
    >
      <header className="flex items-center justify-between">
        <h2 className="text-sm font-semibold uppercase tracking-wider text-muted-foreground">
          Recent activity
        </h2>
        <span className="text-xs text-muted-foreground">
          {data?.count ?? 0} notification{data?.count === 1 ? "" : "s"}
        </span>
      </header>

      {errored ? (
        <p className="mt-4 text-sm text-muted-foreground">
          Could not load notification history — the backend may be unreachable.
        </p>
      ) : (data?.items.length ?? 0) === 0 ? (
        <p className="mt-4 text-sm text-muted-foreground">
          No notifications yet. Daily digests and alerts will appear here once
          the Telegram sender runs.
        </p>
      ) : (
        <ul className="mt-4 divide-y divide-border" data-testid="notification-list">
          {data!.items.map((n) => {
            const payload = n.payload || {};
            const kind =
              typeof payload.kind === "string" ? (payload.kind as string) : "notification";
            const chatId =
              typeof payload.chat_id === "string" ? (payload.chat_id as string) : "—";
            const entryCount = Array.isArray(payload.entry_ids)
              ? (payload.entry_ids as unknown[]).length
              : null;
            return (
              <li
                key={n.id}
                className="flex items-start justify-between gap-4 py-3 text-sm"
                data-testid="notification-item"
              >
                <div>
                  <div className="flex items-center gap-2">
                    <span
                      className={cn(
                        "chip",
                        n.delivered_at
                          ? "chip-success"
                          : n.error
                            ? "chip-danger"
                            : "chip-warning",
                      )}
                    >
                      {n.delivered_at ? "delivered" : n.error ? "failed" : "pending"}
                    </span>
                    <span className="font-medium capitalize">
                      {kind.replace(/_/g, " ")}
                    </span>
                    <span className="text-xs text-muted-foreground">
                      → chat {chatId}
                    </span>
                  </div>
                  <div className="mt-1 text-xs text-muted-foreground">
                    {entryCount !== null
                      ? `${entryCount} opportunit${entryCount === 1 ? "y" : "ies"}`
                      : null}
                    {n.error ? ` · error: ${n.error}` : null}
                  </div>
                </div>
                <time
                  dateTime={n.created_at}
                  className="shrink-0 text-xs text-muted-foreground"
                  title={n.created_at}
                >
                  {formatRelativeTime(n.created_at)}
                </time>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
