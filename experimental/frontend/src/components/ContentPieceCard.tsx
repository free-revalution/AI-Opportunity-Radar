"use client";

/**
 * ContentPieceCard — one channel per opportunity in the Content Center.
 *
 * Renders the body text with channel-specific formatting (markdown vs
 * JSON) and a Copy button that writes to the clipboard. When the
 * piece is missing (`undefined`), shows a friendly placeholder.
 *
 * Phase 8 (v2.0) additions:
 *   • `checked` / `onCheck` — bulk-select checkbox (for batch export).
 *   • `published` / `onMarkPublished` — per-channel ✓/○ badge +
 *     single-channel mark_published button.
 *
 * Phase 9 (v2.0) additions:
 *   • `onEdit` — opens ContentEditor modal so operator can tweak the body
 *     without re-running the LLM.
 *   • `onViewHistory` — opens ContentVersionHistory drawer.
 */

import { useCallback, useState } from "react";

import type { ContentPiece } from "@/types";

import { QualityBadge } from "./QualityBadge";

export function ContentPieceCard({
  channel,
  label,
  piece,
  opportunityId,
  checked = false,
  onCheck,
  published = false,
  onMarkPublished,
  marking = false,
  onEdit,
  onViewHistory,
  onScore,
  scoring = false,
  onAutoImprove,
  autoImproving = false,
  onPublish,
  publishing = false,
}: {
  channel: string;
  label: string;
  piece: ContentPiece | undefined;
  opportunityId: number;
  checked?: boolean;
  onCheck?: (checked: boolean) => void;
  /** True if this channel has been marked published on the opp. */
  published?: boolean;
  /** Optional callback — when provided, a "标记已发布" button appears. */
  onMarkPublished?: () => void;
  /** Disable the publish button while a request is in flight. */
  marking?: boolean;
  /** Phase 9 — open the ContentEditor modal for this piece. */
  onEdit?: () => void;
  /** Phase 9 — open the ContentVersionHistory drawer for this channel. */
  onViewHistory?: () => void;
  /** Phase 10 — call /content/{id}/quality on demand. */
  onScore?: () => void;
  /** Phase 10 — disable while the scorer is in flight. */
  scoring?: boolean;
  /** Phase 10 — call /content/{id}/auto_improve (only rendered when score < threshold). */
  onAutoImprove?: () => void;
  /** Phase 10 — disable while auto_improve is in flight. */
  autoImproving?: boolean;
  /** Phase 11 — call /content/{id}/publish for one-click platform publish. */
  onPublish?: () => void;
  /** Phase 11 — disable while publish is in flight. */
  publishing?: boolean;
}) {
  const [copied, setCopied] = useState(false);

  const handleCopy = useCallback(async () => {
    if (!piece) return;
    const text =
      piece.format === "json"
        ? JSON.stringify(piece.body, null, 2)
        : String(piece.body);
    try {
      if (navigator?.clipboard?.writeText) {
        await navigator.clipboard.writeText(text);
      } else {
        // Fallback for environments without the clipboard API.
        const ta = document.createElement("textarea");
        ta.value = text;
        document.body.appendChild(ta);
        ta.select();
        document.execCommand("copy");
        document.body.removeChild(ta);
      }
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      setCopied(false);
    }
  }, [piece]);

  // ---- empty state -----------------------------------------------------
  if (!piece) {
    return (
      <div
        className="rounded-lg border border-dashed border-border bg-muted/20 p-4 text-xs text-muted-foreground"
        data-testid={`piece-${channel}-${opportunityId}-missing`}
      >
        <div className="flex items-center justify-between gap-2">
          <div className="flex items-center gap-2">
            {onCheck && (
              <input
                type="checkbox"
                checked={checked}
                onChange={(e) => onCheck(e.target.checked)}
                aria-label={`选择 ${label}`}
                className="h-3 w-3"
                data-testid={`select-${channel}-${opportunityId}`}
              />
            )}
            <span className="font-semibold uppercase tracking-wider">
              {label}
            </span>
          </div>
          {published !== undefined && (
            <ChannelStatusDot published={published} />
          )}
        </div>
        <p className="mt-3">尚未生成。先到 n8n 触发内容生产。</p>
      </div>
    );
  }

  // ---- populated state -------------------------------------------------
  return (
    <div
      className="rounded-lg border border-border bg-card/40 p-4 text-xs"
      data-testid={`piece-${channel}-${opportunityId}`}
    >
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          {onCheck && (
            <input
              type="checkbox"
              checked={checked}
              onChange={(e) => onCheck(e.target.checked)}
              aria-label={`选择 ${label}`}
              className="h-3 w-3"
              data-testid={`select-${channel}-${opportunityId}`}
            />
          )}
          <span className="font-semibold uppercase tracking-wider">{label}</span>
          {published !== undefined && (
            <ChannelStatusDot published={published} />
          )}
          {(onScore || piece?.quality_score) && (
            <QualityBadge
              score={piece?.quality_score ?? null}
              loading={scoring}
              onScore={onScore}
              onAutoImprove={onAutoImprove}
              busy={autoImproving}
            />
          )}
        </div>
        <div className="flex items-center gap-1">
          <button
            onClick={handleCopy}
            className="rounded border border-border px-2 py-0.5 text-[10px] font-medium hover:bg-muted"
            data-testid={`copy-${channel}-${opportunityId}`}
            aria-label={`复制 ${label} 内容`}
          >
            {copied ? "已复制 ✓" : "复制"}
          </button>
          {onEdit && (
            <button
              onClick={onEdit}
              className="rounded border border-border px-2 py-0.5 text-[10px] font-medium hover:bg-muted"
              data-testid={`edit-${channel}-${opportunityId}`}
              aria-label={`编辑 ${label} 内容`}
              title="编辑 → 保存为新版本"
            >
              编辑
            </button>
          )}
          {onPublish && (
            <button
              onClick={onPublish}
              disabled={publishing}
              className="rounded border border-accent/60 bg-accent/10 px-2 py-0.5 text-[10px] font-medium text-accent-foreground hover:bg-accent/20 disabled:cursor-not-allowed disabled:opacity-40"
              data-testid={`publish-${channel}-${opportunityId}`}
              aria-label={`一键发布 ${label}`}
              title="一键发布到对应平台(未配置凭据时为友好提示)"
            >
              {publishing ? "发布中…" : "🚀 一键发布"}
            </button>
          )}
        </div>
      </div>
      {piece.title && (
        <p className="mt-2 text-sm font-semibold text-foreground">
          {piece.title}
        </p>
      )}
      <pre className="mt-2 max-h-48 overflow-auto whitespace-pre-wrap break-words rounded bg-background/60 p-2 font-mono text-[11px] leading-relaxed">
        {piece.format === "json"
          ? JSON.stringify(piece.body, null, 2)
          : String(piece.body)}
      </pre>
      {(onMarkPublished || onViewHistory) && (
        <div className="mt-2 flex items-center justify-between">
          {onViewHistory && (
            <button
              onClick={onViewHistory}
              className="text-[10px] text-muted-foreground underline-offset-2 hover:text-foreground hover:underline"
              data-testid={`view-history-${channel}-${opportunityId}`}
            >
              查看历史
            </button>
          )}
          {!onViewHistory && <span />}
          {onMarkPublished && (
            <button
              onClick={onMarkPublished}
              disabled={marking || published}
              className="rounded border border-border px-2 py-0.5 text-[10px] font-medium hover:bg-muted disabled:cursor-not-allowed disabled:opacity-40"
              data-testid={`mark-published-${channel}-${opportunityId}`}
            >
              {published ? "已发布 ✓" : marking ? "标记中…" : "标记已发布"}
            </button>
          )}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Local helpers
// ---------------------------------------------------------------------------
function ChannelStatusDot({ published }: { published: boolean }) {
  return (
    <span
      title={published ? "已发布到此渠道" : "尚未发布"}
      aria-label={published ? "已发布" : "未发布"}
      data-testid={`status-dot-${published ? "on" : "off"}`}
      className={
        "inline-block h-2 w-2 rounded-full " +
        (published ? "bg-emerald-500" : "bg-zinc-500")
      }
    />
  );
}
