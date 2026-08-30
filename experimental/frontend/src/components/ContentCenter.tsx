"use client";

/**
 * Content Center — operator console for v2.0 generated sales copy.
 *
 * Renders one card per opportunity with the generated content for
 * every sales channel (feishu / xianyu / xiaohongshu / wechat_article).
 * Each channel exposes:
 *
 *   • Copy  — clipboard write of the body text
 *   • Mark published (per-channel) — stamp `channel_published[ch]`
 *   • Mark sold — flip `content_status` to `sold` (Phase 4 path)
 *
 * Phase 8 (v2.0) additions:
 *   • Channel tab strip above the list — clicking filters the by_opportunity
 *     payload to a single channel.
 *   • Per-channel ✓/○ dot on each card, driven by
 *     `opportunity.channel_published[ch]`.
 *   • Per-channel checkbox + bulk Export button — POSTs /content/export
 *     and triggers a browser download.
 *   • Regenerate button per opp — POSTs /content/regenerate/{id} to
 *     re-run the generators (default append mode).
 *
 * The component is fully client-side so the operator can act without
 * a page reload. Initial data is server-rendered (see page.tsx) and
 * mutated in place after each action.
 */

import { useCallback, useEffect, useMemo, useState } from "react";

import {
  autoImproveContent,
  exportContent,
  fetchContentCenter,
  fetchContentQuality,
  fetchPublishChannels,
  markChannelPublished,
  markContentPublished,
  markContentSold,
  publishNotification,
  regenerateContent,
} from "@/lib/api";
import type {
  ContentCenterItem,
  ContentPiece,
  ExportFormat,
  OrderCreatePayload,
} from "@/types";

import { ContentPieceCard } from "./ContentPieceCard";
import { ContentEditor } from "./ContentEditor";
import { ContentVersionHistory } from "./ContentVersionHistory";
import { OrderDialog } from "./OrderDialog";

const CHANNEL_ORDER = ["feishu", "xianyu", "xiaohongshu", "wechat_article"] as const;
const CHANNEL_LABELS: Record<(typeof CHANNEL_ORDER)[number], string> = {
  feishu: "飞书",
  xianyu: "闲鱼",
  xiaohongshu: "小红书",
  wechat_article: "公众号",
};
type ChannelKey = (typeof CHANNEL_ORDER)[number];

function triggerBrowserDownload(filename: string, body: string, mime: string) {
  const blob = new Blob([body], { type: mime });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  setTimeout(() => URL.revokeObjectURL(url), 0);
}

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
  const [busyChannelKey, setBusyChannelKey] = useState<string | null>(null);
  const [toast, setToast] = useState<{ kind: "ok" | "err"; text: string } | null>(
    null,
  );
  const [refreshing, setRefreshing] = useState(false);
  const [activeChannel, setActiveChannel] = useState<ChannelKey | "all">("all");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [exporting, setExporting] = useState(false);
  const [soldDialogFor, setSoldDialogFor] = useState<{
    opportunityId: number;
    title: string;
  } | null>(null);
  // Phase 9 — content editor modal + version history drawer
  const [editing, setEditing] = useState<ContentPiece | null>(null);
  const [historyFor, setHistoryFor] = useState<
    | {
        opportunityId: number;
        channel: (typeof CHANNEL_ORDER)[number];
      }
    | null
  >(null);
  // Phase 10 — quality scoring + auto-improve
  const [scoringKey, setScoringKey] = useState<string | null>(null);
  const [improvingKey, setImprovingKey] = useState<string | null>(null);
  // Phase 11 — one-click publish
  const [publishingKey, setPublishingKey] = useState<string | null>(null);
  const [publishChannels, setPublishChannels] = useState<{
    configured: string[];
    unconfigured: string[];
  }>({ configured: [], unconfigured: [] });

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

  // ----- per-channel publish (Phase 8) -----------------------------------
  const handleMarkChannelPublished = useCallback(
    async (oppId: number, channel: ChannelKey) => {
      const key = `${oppId}-${channel}`;
      setBusyChannelKey(key);
      try {
        const result = await markChannelPublished(oppId, channel);
        updateItem(oppId, {
          content_status: result.content_status,
          commercial_status: result.commercial_status,
          channel_published: result.channel_published ?? {},
        });
        showToast("ok", `${CHANNEL_LABELS[channel]} 已标记发布`);
      } catch (err) {
        showToast("err", (err as Error).message);
      } finally {
        setBusyChannelKey(null);
      }
    },
    [updateItem, showToast],
  );

  const handleMarkPublished = useCallback(
    async (oppId: number) => {
      setBusyId(oppId);
      try {
        const result = await markContentPublished(oppId);
        updateItem(oppId, {
          content_status: result.content_status,
          commercial_status: result.commercial_status,
          channel_published: result.channel_published ?? {},
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

  // ----- Phase 9 — content editor + version history ---------------------
  const refreshCurrentView = useCallback(async () => {
    try {
      const fresh = await fetchContentCenter(
        onlyQualified,
        limit,
        activeChannel === "all" ? undefined : activeChannel,
      );
      setItems(fresh.items);
    } catch (err) {
      showToast("err", (err as Error).message);
    }
  }, [onlyQualified, limit, activeChannel, showToast]);

  const openEditor = useCallback((piece: ContentPiece) => {
    setEditing(piece);
  }, []);

  const closeEditor = useCallback(() => {
    setEditing(null);
  }, []);

  const handleEditSaved = useCallback(
    async (newNotificationId: number) => {
      showToast("ok", `已保存为版本 #${newNotificationId}`);
      await refreshCurrentView();
    },
    [refreshCurrentView, showToast],
  );

  const openHistory = useCallback(
    (opportunityId: number, channel: (typeof CHANNEL_ORDER)[number]) => {
      setHistoryFor({ opportunityId, channel });
    },
    [],
  );

  const closeHistory = useCallback(() => {
    setHistoryFor(null);
  }, []);

  // ----- Phase 10 — quality score + auto-improve ------------------------
  // We use `${oppId}-${channel}` as the key so two cards on different
  // opps can score concurrently without one canceling the other.
  const scoringKeyFor = useCallback(
    (oppId: number, channel: ChannelKey) => `${oppId}-${channel}`,
    [],
  );

  const handleScore = useCallback(
    async (oppId: number, channel: ChannelKey, notificationId: number) => {
      const key = scoringKeyFor(oppId, channel);
      setScoringKey(key);
      try {
        const res = await fetchContentQuality(notificationId, {
          persist: true,
        });
        // Patch the local row so the badge renders without a refetch.
        setItems((prev) =>
          prev.map((it) =>
            it.opportunity.id === oppId
              ? {
                  ...it,
                  content: {
                    ...it.content,
                    [channel]: {
                      ...it.content[channel]!,
                      quality_score: res.score,
                    },
                  },
                }
              : it,
          ),
        );
        showToast(
          "ok",
          res.score.below_threshold
            ? `${CHANNEL_LABELS[channel]} 评分 ${res.score.total.toFixed(1)} · 低于阈值`
            : `${CHANNEL_LABELS[channel]} 评分 ${res.score.total.toFixed(1)} ✓`,
        );
      } catch (err) {
        showToast("err", (err as Error).message);
      } finally {
        setScoringKey(null);
      }
    },
    [scoringKeyFor, showToast],
  );

  const handleAutoImprove = useCallback(
    async (oppId: number, channel: ChannelKey, notificationId: number) => {
      const key = scoringKeyFor(oppId, channel);
      setImprovingKey(key);
      try {
        const res = await autoImproveContent(notificationId, {
          max_attempts: 2,
        });
        showToast(
          res.below_threshold ? "err" : "ok",
          res.below_threshold
            ? `${CHANNEL_LABELS[channel]} 自动重跑 ${res.attempts_used} 次仍未通过(${res.score.total.toFixed(1)})`
            : `${CHANNEL_LABELS[channel]} 自动重跑成功 · ${res.score.total.toFixed(1)} ✓`,
        );
        // The new content lives in a different notification_id —
        // easiest path is a full refetch.
        await refreshCurrentView();
      } catch (err) {
        showToast("err", (err as Error).message);
      } finally {
        setImprovingKey(null);
      }
    },
    [scoringKeyFor, showToast, refreshCurrentView],
  );

  // ----- Phase 11 — one-click publish -----------------------------------
  // Fetch which channels have a working publisher configured so the
  // "🚀 一键发布" button only appears where it's actually actionable.
  useEffect(() => {
    fetchPublishChannels()
      .then((res) => {
        setPublishChannels({
          configured: res.configured.map((c) => c.channel),
          unconfigured: res.unconfigured.map((c) => c.channel),
        });
      })
      .catch(() => {
        // Best-effort — if the endpoint is down we just hide the
        // publish buttons on every channel.
      });
  }, []);

  const handlePublish = useCallback(
    async (oppId: number, channel: ChannelKey, notificationId: number) => {
      const key = scoringKeyFor(oppId, channel);
      setPublishingKey(key);
      try {
        const res = await publishNotification(notificationId, {
          mark_published: true,
        });
        if (res.success) {
          showToast(
            "ok",
            `${CHANNEL_LABELS[channel]} 已发布 · ${res.external_id ?? "已记录"}`,
          );
          await refreshCurrentView();
        } else if (res.skipped) {
          showToast(
            "err",
            `${CHANNEL_LABELS[channel]} 平台未配置凭据: ${res.error ?? ""}`,
          );
        } else {
          showToast("err", `${CHANNEL_LABELS[channel]} 发布失败: ${res.error}`);
        }
      } catch (err) {
        showToast("err", (err as Error).message);
      } finally {
        setPublishingKey(null);
      }
    },
    [scoringKeyFor, showToast, refreshCurrentView],
  );

  // ----- tab strip (Phase 8) ---------------------------------------------
  const handleChannelTab = useCallback(
    async (ch: ChannelKey | "all") => {
      setActiveChannel(ch);
      setRefreshing(true);
      try {
        const fresh = await fetchContentCenter(
          onlyQualified,
          limit,
          ch === "all" ? undefined : ch,
        );
        setItems(fresh.items);
      } catch (err) {
        showToast("err", (err as Error).message);
      } finally {
        setRefreshing(false);
      }
    },
    [onlyQualified, limit, showToast],
  );

  const handleRefresh = useCallback(async () => {
    setRefreshing(true);
    try {
      const fresh = await fetchContentCenter(
        onlyQualified,
        limit,
        activeChannel === "all" ? undefined : activeChannel,
      );
      setItems(fresh.items);
      showToast("ok", "已刷新");
    } catch (err) {
      showToast("err", (err as Error).message);
    } finally {
      setRefreshing(false);
    }
  }, [onlyQualified, limit, activeChannel, showToast]);

  // ----- bulk select + export (Phase 8) ----------------------------------
  const selectionKey = useCallback(
    (oppId: number, channel: ChannelKey) => `${oppId}-${channel}`,
    [],
  );

  const isChecked = useCallback(
    (oppId: number, channel: ChannelKey) => selected.has(selectionKey(oppId, channel)),
    [selected, selectionKey],
  );

  const toggleChecked = useCallback(
    (oppId: number, channel: ChannelKey, value: boolean) => {
      setSelected((prev) => {
        const next = new Set(prev);
        const k = selectionKey(oppId, channel);
        if (value) next.add(k);
        else next.delete(k);
        return next;
      });
    },
    [selectionKey],
  );

  const handleExport = useCallback(
    async (format: ExportFormat) => {
      setExporting(true);
      try {
        // If anything is selected, export just those opportunities (across
        // all selected channels). Otherwise, export the full current
        // channel view.
        let opportunity_ids: number[] | undefined;
        let channels: ChannelKey[] | undefined;
        if (selected.size > 0) {
          const oppIdSet = new Set<number>();
          const chSet = new Set<ChannelKey>();
          for (const k of selected) {
            const [oid, ch] = k.split("-") as [string, ChannelKey];
            oppIdSet.add(parseInt(oid, 10));
            chSet.add(ch);
          }
          opportunity_ids = [...oppIdSet];
          channels = [...chSet];
        } else if (activeChannel !== "all") {
          channels = [activeChannel];
        }

        const result = await exportContent({
          only_qualified: onlyQualified,
          limit,
          ...(opportunity_ids ? { opportunity_ids } : {}),
          ...(channels ? { channels } : {}),
          format,
        });

        if (result.format === "csv") {
          triggerBrowserDownload(result.filename, result.body, "text/csv");
        } else if (result.format === "json") {
          triggerBrowserDownload(
            result.filename,
            JSON.stringify(result.data, null, 2),
            "application/json",
          );
        } else {
          triggerBrowserDownload(
            result.filename,
            JSON.stringify(result.data, null, 2),
            "application/json",
          );
        }
        showToast(
          "ok",
          format === "bundle"
            ? "Bundle 已下载 — 文件可拖入公众号编辑器"
            : `已导出 ${format.toUpperCase()}`,
        );
      } catch (err) {
        showToast("err", (err as Error).message);
      } finally {
        setExporting(false);
      }
    },
    [selected, activeChannel, onlyQualified, limit, showToast],
  );

  const handleRegenerate = useCallback(
    async (oppId: number) => {
      setBusyId(oppId);
      try {
        const result = await regenerateContent(oppId, {
          // Append mode by default — operators lose history on replace.
          delete_previous: false,
        });
        showToast(
          "ok",
          `已重跑 ${result.regenerated_count} 个渠道:${result.generators.join(
            ", ",
          )}`,
        );
        // Re-fetch this opp's row so the new content shows up.
        const fresh = await fetchContentCenter(
          onlyQualified,
          limit,
          activeChannel === "all" ? undefined : activeChannel,
        );
        setItems(fresh.items);
      } catch (err) {
        showToast("err", (err as Error).message);
      } finally {
        setBusyId(null);
      }
    },
    [onlyQualified, limit, activeChannel, showToast],
  );

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
      {/* Tab strip + global actions */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div
          className="flex flex-wrap items-center gap-1 rounded-lg border border-border bg-muted/30 p-1"
          data-testid="content-center-tabs"
        >
          <TabButton
            active={activeChannel === "all"}
            onClick={() => handleChannelTab("all")}
            testId="tab-all"
          >
            全部
          </TabButton>
          {CHANNEL_ORDER.map((ch) => (
            <TabButton
              key={ch}
              active={activeChannel === ch}
              onClick={() => handleChannelTab(ch)}
              testId={`tab-${ch}`}
            >
              {CHANNEL_LABELS[ch]}
            </TabButton>
          ))}
        </div>
        <div className="flex items-center gap-2">
          <p className="text-sm text-muted-foreground" data-testid="content-center-summary">
            {items.length} 个机会 · {totalChannels} 条已生成内容
            {selected.size > 0 && ` · 已选 ${selected.size} 条`}
          </p>
          <button
            onClick={handleRefresh}
            disabled={refreshing}
            className="rounded-md border border-border px-3 py-1.5 text-sm hover:bg-muted disabled:opacity-50"
            data-testid="content-center-refresh"
          >
            {refreshing ? "刷新中…" : "刷新"}
          </button>
          <div className="relative">
            <details className="group">
              <summary
                className="cursor-pointer rounded-md border border-border px-3 py-1.5 text-sm hover:bg-muted"
                data-testid="content-center-export"
              >
                {exporting ? "导出中…" : "导出 ▾"}
              </summary>
              <div className="absolute right-0 z-10 mt-1 w-44 rounded-md border border-border bg-card p-1 text-sm shadow-lg">
                {(["csv", "json", "bundle"] as ExportFormat[]).map((fmt) => (
                  <button
                    key={fmt}
                    onClick={() => handleExport(fmt)}
                    disabled={exporting}
                    className="block w-full rounded px-2 py-1.5 text-left text-xs hover:bg-muted disabled:opacity-50"
                    data-testid={`export-${fmt}`}
                  >
                    {fmt === "csv"
                      ? "CSV — Excel 友好"
                      : fmt === "json"
                      ? "JSON — 二次处理"
                      : "Bundle — 拖入公众号编辑器"}
                  </button>
                ))}
              </div>
            </details>
          </div>
        </div>
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
            先到 n8n 跑一遍完整流程,或在终端调:
            <code className="ml-1 rounded bg-muted px-1">
              POST /api/internal/content/generate
            </code>
          </p>
        </div>
      ) : (
        <ul className="space-y-8" data-testid="content-center-list">
          {items.map((item) => {
            const opp = item.opportunity;
            const cp = opp.channel_published ?? {};
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
                      onClick={() => handleRegenerate(opp.id)}
                      disabled={busyId === opp.id}
                      className="rounded-md border border-border px-3 py-1.5 text-xs font-semibold hover:bg-muted disabled:cursor-not-allowed disabled:opacity-40"
                      data-testid={`regenerate-${opp.id}`}
                      title="重新跑全部生成器(append 模式 — 历史保留)"
                    >
                      {busyId === opp.id ? "重跑中…" : "重跑"}
                    </button>
                    <button
                      onClick={() => handleMarkPublished(opp.id)}
                      disabled={busyId === opp.id || opp.content_status === "published"}
                      className="rounded-md bg-accent px-3 py-1.5 text-xs font-semibold text-accent-foreground hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-40"
                      data-testid={`mark-published-${opp.id}`}
                      title="4 个渠道全部标记发布"
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
                    const pieceKey = scoringKeyFor(opp.id, channel);
                    const publisherConfigured = publishChannels.configured.includes(
                      channel,
                    );
                    return (
                      <ContentPieceCard
                        key={channel}
                        channel={channel}
                        label={CHANNEL_LABELS[channel]}
                        piece={piece}
                        opportunityId={opp.id}
                        checked={isChecked(opp.id, channel)}
                        onCheck={(v) => toggleChecked(opp.id, channel, v)}
                        published={Boolean(cp[channel])}
                        onMarkPublished={() => handleMarkChannelPublished(opp.id, channel)}
                        marking={busyChannelKey === `${opp.id}-${channel}`}
                        onEdit={
                          piece ? () => openEditor(piece) : undefined
                        }
                        onViewHistory={() => openHistory(opp.id, channel)}
                        onScore={
                          piece
                            ? () => handleScore(opp.id, channel, piece.notification_id)
                            : undefined
                        }
                        scoring={scoringKey === pieceKey}
                        onAutoImprove={
                          piece
                            ? () => handleAutoImprove(opp.id, channel, piece.notification_id)
                            : undefined
                        }
                        autoImproving={improvingKey === pieceKey}
                        onPublish={
                          piece && publisherConfigured
                            ? () => handlePublish(opp.id, channel, piece.notification_id)
                            : undefined
                        }
                        publishing={publishingKey === pieceKey}
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

      <ContentEditor
        open={editing !== null}
        onClose={closeEditor}
        piece={editing ?? EMPTY_PIECE}
        onSaved={handleEditSaved}
        onError={(msg) => showToast("err", msg)}
      />

      {historyFor && (
        <ContentVersionHistory
          open
          onClose={closeHistory}
          opportunityId={historyFor.opportunityId}
          channel={historyFor.channel}
          channelLabel={CHANNEL_LABELS[historyFor.channel]}
        />
      )}
    </div>
  );
}

// Used as a stable placeholder when the editor is closed — keeps the
// editor's useEffect re-sync from firing on every render.
const EMPTY_PIECE: ContentPiece = {
  notification_id: 0,
  channel: "",
  title: "",
  body: "",
  metadata: {},
  generator: "",
  format: "markdown",
  created_at: null,
};

// ---------------------------------------------------------------------------
// Local helpers
// ---------------------------------------------------------------------------
function TabButton({
  active,
  onClick,
  children,
  testId,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
  testId: string;
}) {
  return (
    <button
      onClick={onClick}
      data-testid={testId}
      className={
        "rounded-md px-3 py-1 text-xs font-medium transition " +
        (active
          ? "bg-accent text-accent-foreground"
          : "text-muted-foreground hover:bg-muted")
      }
    >
      {children}
    </button>
  );
}

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
