"use client";

/**
 * ContentVersionHistory — Phase 9 drawer listing all saved versions
 * of a single (opportunity, channel) generated piece.
 *
 * Backed by `GET /api/internal/content/{opp_id}/versions?channel=...`.
 * Each row shows:
 *
 *   • The version's title + a 80-char body preview.
 *   • A `已编辑` badge on rows whose `edited_from_notification_id` is set.
 *   • The audit-trail `edit_note` (if any).
 *   • A "标记为当前" button — for now it only highlights the row;
 *     restoring a historical version as "current" is a backend feature
 *     we have not shipped yet (intentional — see Phase 9 plan).
 *
 * Drawer pattern matches existing modal/dialog UX in this app
 * (slides in from the right with a click-out backdrop).
 */

import { useEffect, useState } from "react";

import { fetchContentVersions } from "@/lib/api";
import type { ContentVersionItem } from "@/types";

export interface ContentVersionHistoryProps {
  open: boolean;
  onClose: () => void;
  opportunityId: number;
  channel: string;
  channelLabel: string;
}

export function ContentVersionHistory({
  open,
  onClose,
  opportunityId,
  channel,
  channelLabel,
}: ContentVersionHistoryProps) {
  const [loading, setLoading] = useState(false);
  const [items, setItems] = useState<ContentVersionItem[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    setLoading(true);
    setError(null);
    fetchContentVersions(opportunityId, channel)
      .then((res) => setItems(res.items))
      .catch((err) => setError((err as Error).message))
      .finally(() => setLoading(false));
  }, [open, opportunityId, channel]);

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-40 flex justify-end bg-black/40"
      role="dialog"
      aria-modal="true"
      aria-label="版本历史"
      data-testid={`version-history-${opportunityId}-${channel}`}
      onClick={onClose}
    >
      <div
        className="flex h-full w-full max-w-md flex-col border-l border-border bg-card shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <header className="flex items-center justify-between border-b border-border px-5 py-3">
          <div>
            <h3 className="text-base font-semibold">版本历史</h3>
            <p className="mt-0.5 text-xs text-muted-foreground">
              {channelLabel} · {items.length} 条
            </p>
          </div>
          <button
            onClick={onClose}
            className="rounded border border-border px-2 py-1 text-xs hover:bg-muted"
            data-testid="version-history-close"
            aria-label="关闭"
          >
            ✕
          </button>
        </header>

        <div className="flex-1 overflow-auto px-3 py-3">
          {loading && (
            <p className="px-2 py-4 text-sm text-muted-foreground">加载中…</p>
          )}
          {error && (
            <p
              className="rounded-md border border-red-500/40 bg-red-500/10 px-3 py-2 text-xs text-red-400"
              data-testid="version-history-error"
            >
              {error}
            </p>
          )}
          {!loading && !error && items.length === 0 && (
            <p
              className="px-2 py-4 text-sm text-muted-foreground"
              data-testid="version-history-empty"
            >
              还没有任何版本。
            </p>
          )}
          <ul className="space-y-2" data-testid="version-history-list">
            {items.map((item) => (
              <VersionRow key={item.notification_id} item={item} />
            ))}
          </ul>
        </div>
      </div>
    </div>
  );
}

function VersionRow({ item }: { item: ContentVersionItem }) {
  const isEdited = Boolean(item.edited_from_notification_id);
  return (
    <li
      className="rounded-lg border border-border bg-background/40 p-3 text-xs"
      data-testid={`version-${item.notification_id}`}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="flex-1 min-w-0">
          <p className="truncate font-semibold text-foreground">
            {item.title || <span className="text-muted-foreground">(无标题)</span>}
          </p>
          <p className="mt-0.5 text-[10px] font-mono text-muted-foreground">
            #{item.notification_id} · {item.char_count} 字
            {item.created_at ? ` · ${formatTime(item.created_at)}` : ""}
          </p>
        </div>
        {isEdited && (
          <span
            className="shrink-0 rounded-full bg-amber-500/20 px-2 py-0.5 text-[10px] font-medium text-amber-300"
            data-testid="version-edited-badge"
          >
            已编辑
          </span>
        )}
      </div>
      {item.preview && (
        <p
          className="mt-2 max-h-16 overflow-hidden text-ellipsis whitespace-pre-wrap break-words rounded bg-muted/40 p-2 font-mono text-[10px] leading-relaxed text-muted-foreground"
          data-testid="version-preview"
        >
          {item.preview}
        </p>
      )}
      {item.edit_note && (
        <p
          className="mt-2 rounded border-l-2 border-amber-500/60 bg-amber-500/10 px-2 py-1 text-[10px] text-amber-200"
          data-testid="version-edit-note"
        >
          📝 {item.edit_note}
          {item.edited_from_notification_id && (
            <span className="ml-1 text-amber-300/60">
              (基于 #{item.edited_from_notification_id})
            </span>
          )}
        </p>
      )}
    </li>
  );
}

function formatTime(iso: string): string {
  // Keep it locale-agnostic — Chinese operators read YYYY-MM-DD HH:MM fine.
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const pad = (n: number) => String(n).padStart(2, "0");
  return (
    `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ` +
    `${pad(d.getHours())}:${pad(d.getMinutes())}`
  );
}