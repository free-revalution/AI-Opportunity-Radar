"use client";

/**
 * ContentEditor — Phase 9 modal for editing a single ContentPiece.
 *
 * Operators use this when LLM output is "80% right" — they tweak wording,
 * tighten the hook, fix a number, or add a missing CTA — without having
 * to re-run the generator (which is slow + non-deterministic).
 *
 * Each save creates a NEW Notification row pointing back at the source
 * via `payload.edited_from_notification_id`. The original is left
 * untouched — operators can audit / rollback via the version history.
 *
 * The modal stays simple: title field + body textarea + optional note.
 * Metadata merges with source (the backend does the merge too, but
 * we surface a small key/value editor for the most common fields).
 */

import { useEffect, useState } from "react";

import type { ContentEditRequest, ContentPiece } from "@/types";
import { editContent } from "@/lib/api";

export interface ContentEditorProps {
  open: boolean;
  onClose: () => void;
  piece: ContentPiece;
  /** Called after a successful save — parent should refetch the card row. */
  onSaved: (newNotificationId: number) => void;
  /** Show a transient error toast on failure. */
  onError?: (message: string) => void;
}

export function ContentEditor({
  open,
  onClose,
  piece,
  onSaved,
  onError,
}: ContentEditorProps) {
  const [title, setTitle] = useState(piece.title ?? "");
  const [body, setBody] = useState(
    piece.format === "json"
      ? JSON.stringify(piece.body, null, 2)
      : String(piece.body ?? ""),
  );
  const [editNote, setEditNote] = useState("");
  const [saving, setSaving] = useState(false);

  // Reset fields when the source piece changes (e.g. operator clicks
  // "edit" on a different card while the modal is open).
  useEffect(() => {
    if (!open) return;
    setTitle(piece.title ?? "");
    setBody(
      piece.format === "json"
        ? JSON.stringify(piece.body, null, 2)
        : String(piece.body ?? ""),
    );
    setEditNote("");
  }, [piece, open]);

  if (!open) return null;

  const charCount = body.length;

  const handleSave = async () => {
    if (saving) return;
    // Local validation — backend will 422 anyway, but better UX if we
    // catch empty submits early.
    const trimmedTitle = title.trim();
    const trimmedBody = body.trim();
    if (!trimmedTitle && !trimmedBody) {
      onError?.("标题和正文不能同时为空");
      return;
    }
    const payload: ContentEditRequest = {
      body: trimmedBody || undefined,
      title: trimmedTitle || undefined,
      edit_note: editNote.trim() || undefined,
    };
    setSaving(true);
    try {
      const result = await editContent(piece.notification_id, payload);
      onSaved(result.notification_id);
      onClose();
    } catch (err) {
      onError?.((err as Error).message);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
      role="dialog"
      aria-modal="true"
      aria-label="编辑内容"
      data-testid={`content-editor-${piece.notification_id}`}
    >
      <div className="flex max-h-[90vh] w-full max-w-3xl flex-col rounded-xl border border-border bg-card shadow-2xl">
        <header className="flex items-center justify-between border-b border-border px-5 py-3">
          <div>
            <h3 className="text-base font-semibold">编辑内容</h3>
            <p className="mt-0.5 text-xs text-muted-foreground">
              渠道:{piece.channel} · 格式:{piece.format} · 来源通知 #{piece.notification_id}
            </p>
          </div>
          <button
            onClick={onClose}
            disabled={saving}
            className="rounded border border-border px-2 py-1 text-xs hover:bg-muted disabled:opacity-40"
            data-testid="content-editor-close"
            aria-label="关闭"
          >
            ✕
          </button>
        </header>

        <div className="flex-1 space-y-4 overflow-auto px-5 py-4">
          <label className="block">
            <span className="text-xs font-medium text-muted-foreground">
              标题
            </span>
            <input
              type="text"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              disabled={saving}
              data-testid="content-editor-title"
              className="mt-1 w-full rounded-md border border-border bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-accent"
              maxLength={200}
            />
          </label>

          <label className="block">
            <span className="flex items-center justify-between text-xs font-medium text-muted-foreground">
              <span>正文</span>
              <span data-testid="content-editor-char-count" className="font-mono">
                {charCount} 字
              </span>
            </span>
            <textarea
              value={body}
              onChange={(e) => setBody(e.target.value)}
              disabled={saving}
              data-testid="content-editor-body"
              className="mt-1 h-80 w-full rounded-md border border-border bg-background px-3 py-2 font-mono text-xs leading-relaxed focus:outline-none focus:ring-2 focus:ring-accent"
            />
          </label>

          <label className="block">
            <span className="text-xs font-medium text-muted-foreground">
              修改备注(可选,记录到审计追踪)
            </span>
            <input
              type="text"
              value={editNote}
              onChange={(e) => setEditNote(e.target.value)}
              disabled={saving}
              data-testid="content-editor-note"
              placeholder="例如:把数字钩子从 14 天改成 21 天,加了一个 CTA"
              className="mt-1 w-full rounded-md border border-border bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-accent"
              maxLength={200}
            />
          </label>

          <p className="rounded-md border border-dashed border-border bg-muted/30 px-3 py-2 text-[11px] leading-relaxed text-muted-foreground">
            保存后会在版本历史里产生一条新版本,原版本不会被改动。
            所有历史版本可在右侧「查看历史」里回看。
          </p>
        </div>

        <footer className="flex items-center justify-end gap-2 border-t border-border px-5 py-3">
          <button
            onClick={onClose}
            disabled={saving}
            className="rounded-md border border-border px-3 py-1.5 text-sm hover:bg-muted disabled:opacity-40"
          >
            取消
          </button>
          <button
            onClick={handleSave}
            disabled={saving}
            className="rounded-md bg-accent px-4 py-1.5 text-sm font-semibold text-accent-foreground hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-40"
            data-testid="content-editor-save"
          >
            {saving ? "保存中…" : "保存为新版本"}
          </button>
        </footer>
      </div>
    </div>
  );
}