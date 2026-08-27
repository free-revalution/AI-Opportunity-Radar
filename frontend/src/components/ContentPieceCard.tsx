"use client";

/**
 * ContentPieceCard — one channel per opportunity in the Content Center.
 *
 * Renders the body text with channel-specific formatting (markdown vs
 * JSON) and a Copy button that writes to the clipboard. When the
 * piece is missing (`undefined`), shows a friendly placeholder.
 */

import { useCallback, useState } from "react";

import type { ContentPiece } from "@/types";

export function ContentPieceCard({
  channel,
  label,
  piece,
  opportunityId,
}: {
  channel: string;
  label: string;
  piece: ContentPiece | undefined;
  opportunityId: number;
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

  if (!piece) {
    return (
      <div
        className="rounded-lg border border-dashed border-border bg-muted/20 p-4 text-xs text-muted-foreground"
        data-testid={`piece-${channel}-${opportunityId}-missing`}
      >
        <div className="flex items-center justify-between">
          <span className="font-semibold uppercase tracking-wider">{label}</span>
        </div>
        <p className="mt-3">尚未生成。先到 n8n 触发内容生产。</p>
      </div>
    );
  }

  return (
    <div
      className="rounded-lg border border-border bg-card/40 p-4 text-xs"
      data-testid={`piece-${channel}-${opportunityId}`}
    >
      <div className="flex items-center justify-between gap-2">
        <span className="font-semibold uppercase tracking-wider">{label}</span>
        <button
          onClick={handleCopy}
          className="rounded border border-border px-2 py-0.5 text-[10px] font-medium hover:bg-muted"
          data-testid={`copy-${channel}-${opportunityId}`}
          aria-label={`复制 ${label} 内容`}
        >
          {copied ? "已复制 ✓" : "复制"}
        </button>
      </div>
      {piece.title && (
        <p className="mt-2 text-sm font-semibold text-foreground">{piece.title}</p>
      )}
      <pre className="mt-2 max-h-48 overflow-auto whitespace-pre-wrap break-words rounded bg-background/60 p-2 font-mono text-[11px] leading-relaxed">
        {piece.format === "json"
          ? JSON.stringify(piece.body, null, 2)
          : String(piece.body)}
      </pre>
    </div>
  );
}