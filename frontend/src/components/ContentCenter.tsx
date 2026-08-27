"use client";

/**
 * Content Center — operator console for v2.0 generated sales copy.
 *
 * Renders one card per opportunity with the generated content for
 * every sales channel (feishu / xianyu / xiaohongshu / wechat_article).
 * Each channel exposes:
 *
 *   • Copy  — clipboard write of the body text
 *   • Mark published / Mark sold — flip `content_status` on the opp
 *
 * The component is fully client-side so the operator can act without
 * a page reload. Initial data is server-rendered (see page.tsx) and
 * mutated in place after each action.
 */

import { useCallback, useMemo, useState } from "react";

import {
  fetchContentCenter,
  markContentPublished,
  markContentSold,
} from "@/lib/api";
import type {
  ContentCenterItem,
  ContentPiece,
  OrderCreatePayload,
} from "@/types";

import { ContentPieceCard } from "./ContentPieceCard";
import { OrderDialog } from "./OrderDialog";

const CHANNEL_ORDER = ["feishu", "xianyu", "xiaohongshu", "wechat_article"] as const;
const CHANNEL_LABELS: Record<(typeof CHANNEL_ORDER)[number], string> = {
  feishu: "飞书",
  xianyu: "闲鱼",
  xiaohongshu: "小红书",
  wechat_article: "公众号",
};

export function ContentCenter({
  initialItems,
  onlyQualified,
  limit,
}: {
  initialItems: ContentCenterItem[];
  onlyQualified: boolean;
  limit: number;
}) {
  const [items, setItems] = useState<ContentCenterItem[]>(initialItems);
  const [busyId, setBusyId] = useState<number | null>(null);
  const [toast, setToast] = useState<{ kind: "ok" | "err"; text: string } | null>(
    null,
  );
  const [refreshing, setRefreshing] = useState(false);
  const [soldDialogFor, setSoldDialogFor] = useState<{
    opportunityId: number;
    title: string;
  } | null>(null);

  const showToast = useCallback(
    (kind: "ok" | "err", text: string) => {
      setToast({ kind, text });
      window.setTimeout(() => setToast(null), 2500);
    },
    [],
  );

  const updateItem = useCallback(
    (oppId: number, patch: Partial<ContentCenterItem["opportunity"]>) => {
      setItems((prev) =>
        prev.map((it) =>
          it.opportunity.id === oppId
            ? { ...it, opportunity: { ...it.opportunity, ...patch } }
            : it,
        ),
      );
    },
    [],
  );

  const handleMarkPublished = useCallback(
    async (oppId: number) => {
      setBusyId(oppId);
      try {
        const result = await markContentPublished(oppId);
        updateItem(oppId, {
          content_status: result.content_status,
          commercial_status: result.commercial_status,
        });
        showToast("ok", "已标记为已发布");
      } catch (err) {
        showToast("err", (err as Error).message);
      } finally {
        setBusyId(null);
      }
    },
    [updateItem, showToast],
  );

  const handleMarkSold = useCallback(
    async (oppId: number, order: OrderCreatePayload) => {
      setBusyId(oppId);
      try {
        const result = await markContentSold(oppId, order);
        updateItem(oppId, {
          content_status: result.content_status,
          commercial_status: result.commercial_status,
        });
        showToast(
          "ok",
          result.order
            ? `已记录销售 ¥${order.amount_cny} · ${order.channel}`
            : "已标记为已售出 🎉",
        );
      } catch (err) {
        showToast("err", (err as Error).message);
      } finally {
        setBusyId(null);
      }
    },
    [updateItem, showToast],
  );

  const openSoldDialog = useCallback((oppId: number, title: string) => {
    setSoldDialogFor({ opportunityId: oppId, title });
  }, []);

  const closeSoldDialog = useCallback(() => {
    setSoldDialogFor(null);
  }, []);

  const handleRefresh = useCallback(async () => {
    setRefreshing(true);
    try {
      const fresh = await fetchContentCenter(onlyQualified, limit);
      setItems(fresh.items);
      showToast("ok", "已刷新");
    } catch (err) {
      showToast("err", (err as Error).message);
    } finally {
      setRefreshing(false);
    }
  }, [onlyQualified, limit, showToast]);

  const totalChannels = useMemo(
    () =>
      items.reduce(
        (acc, it) =>
          acc + CHANNEL_ORDER.filter((ch) => Boolean(it.content[ch])).length,
        0,
      ),
    [items],
  );

  return (
    <div className="space-y-6" data-testid="content-center">
      <div className="flex items-center justify-between">
        <p className="text-sm text-muted-foreground">
          {items.length} 个机会 · {totalChannels} 条已生成内容
        </p>
        <button
          onClick={handleRefresh}
          disabled={refreshing}
          className="rounded-md border border-border px-3 py-1.5 text-sm hover:bg-muted disabled:opacity-50"
          data-testid="content-center-refresh"
        >
          {refreshing ? "刷新中…" : "刷新"}
        </button>
      </div>

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

      {items.length === 0 ? (
        <div
          className="rounded-xl border border-dashed border-border p-12 text-center"
          data-testid="content-center-empty"
        >
          <p className="text-sm text-muted-foreground">
            还没有可销售内容。
          </p>
          <p className="mt-2 text-xs text-muted-foreground">
            先去 n8n 跑一遍完整流程,或在终端调:
            <code className="ml-1 rounded bg-muted px-1">
              POST /api/internal/content/generate
            </code>
          </p>
        </div>
      ) : (
        <ul className="space-y-8" data-testid="content-center-list">
          {items.map((item) => {
            const opp = item.opportunity;
            return (
              <li
                key={opp.id}
                className="glass rounded-xl p-6"
                data-testid={`content-center-row-${opp.id}`}
              >
                <header className="flex flex-wrap items-start justify-between gap-3">
                  <div>
                    <div className="flex items-center gap-2">
                      <h2 className="text-xl font-semibold">{opp.title}</h2>
                      <span
                        className={
                          "rounded-full px-2 py-0.5 text-xs " +
                          statusBadgeClass(opp.content_status)
                        }
                        data-testid={`status-${opp.id}`}
                      >
                        {contentStatusLabel(opp.content_status)}
                      </span>
                    </div>
                    <p className="mt-1 text-sm text-muted-foreground">
                      评分 {opp.total_score.toFixed(1)} · 商业 {opp.commercial_status}
                      {opp.market_size && ` · 市场 ${opp.market_size}`}
                      {opp.mvp_days ? ` · MVP ${opp.mvp_days} 天` : ""}
                    </p>
                    {opp.china_gap && (
                      <p className="mt-1 max-w-2xl text-xs text-muted-foreground">
                        中国空白:{opp.china_gap}
                      </p>
                    )}
                  </div>

                  <div className="flex flex-wrap items-center gap-2">
                    <button
                      onClick={() => handleMarkPublished(opp.id)}
                      disabled={busyId === opp.id || opp.content_status === "published"}
                      className="rounded-md bg-accent px-3 py-1.5 text-xs font-semibold text-accent-foreground hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-40"
                      data-testid={`mark-published-${opp.id}`}
                    >
                      {opp.content_status === "published" ? "已发布 ✓" : "标记已发布"}
                    </button>
                    <button
                      onClick={() => openSoldDialog(opp.id, opp.title)}
                      disabled={busyId === opp.id || opp.content_status === "sold"}
                      className="rounded-md border border-emerald-500/60 px-3 py-1.5 text-xs font-semibold text-emerald-400 hover:bg-emerald-500/10 disabled:cursor-not-allowed disabled:opacity-40"
                      data-testid={`mark-sold-${opp.id}`}
                    >
                      {opp.content_status === "sold" ? "已售出 🎉" : "标记已售出"}
                    </button>
                  </div>
                </header>

                <div className="mt-4 grid gap-3 md:grid-cols-2 xl:grid-cols-4">
                  {CHANNEL_ORDER.map((channel) => {
                    const piece: ContentPiece | undefined = item.content[channel];
                    return (
                      <ContentPieceCard
                        key={channel}
                        channel={channel}
                        label={CHANNEL_LABELS[channel]}
                        piece={piece}
                        opportunityId={opp.id}
                      />
                    );
                  })}
                </div>
              </li>
            );
          })}
        </ul>
      )}

      <OrderDialog
        open={soldDialogFor !== null}
        onClose={closeSoldDialog}
        onSubmit={(order) => {
          if (!soldDialogFor) return Promise.resolve();
          return handleMarkSold(soldDialogFor.opportunityId, order).then(() => {
            setSoldDialogFor(null);
          });
        }}
        busy={busyId !== null}
        opportunityTitle={soldDialogFor?.title ?? ""}
      />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Local helpers
// ---------------------------------------------------------------------------
function statusBadgeClass(status: string): string {
  switch (status) {
    case "published":
      return "bg-emerald-500/20 text-emerald-300";
    case "sold":
      return "bg-amber-500/20 text-amber-300";
    case "generated":
      return "bg-blue-500/20 text-blue-300";
    default:
      return "bg-zinc-500/20 text-zinc-300";
  }
}

function contentStatusLabel(status: string): string {
  switch (status) {
    case "published":
      return "已发布";
    case "sold":
      return "已售出";
    case "generated":
      return "已生成";
    case "new":
      return "未生成";
    default:
      return status;
  }
}