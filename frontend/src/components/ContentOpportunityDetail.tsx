"use client";

/**
 * Phase 18 — Content Opportunity detail panel.
 *
 * Renders every column of the row + the rendered content payload
 * (hook / titles / materials / script / cta / risk_warning). State-
 * machine action bar mirrors `OrdersPanel`'s mutate-and-patch pattern.
 */

import { useCallback, useId, useState } from "react";

import {
  approveContentOpportunity,
  publishContentOpportunity,
  rejectContentOpportunity,
} from "@/lib/api";
import {
  NEXT_STATUS_MAP,
  STATUS_LABELS,
  formatRiskScore,
  formatShortDate,
  statusChipClass,
} from "@/lib/contentOpportunities";
import type {
  ContentOpportunity,
  ContentOpportunityRejectRequest,
  ContentOpportunityStatus,
} from "@/types";

export interface ContentOpportunityDetailProps {
  initial: ContentOpportunity;
}

export function ContentOpportunityDetail({
  initial,
}: ContentOpportunityDetailProps) {
  const [co, setCo] = useState<ContentOpportunity>(initial);
  const [busy, setBusy] = useState(false);
  const [rejectOpen, setRejectOpen] = useState(false);
  const [rejectReason, setRejectReason] = useState("");
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

  const doTransition = useCallback(
    async (action: () => Promise<ContentOpportunity>, label: string) => {
      setBusy(true);
      try {
        const updated = await action();
        setCo(updated);
        showToast("ok", `#${updated.id} → ${STATUS_LABELS[updated.status]}`);
      } catch (err) {
        showToast("err", (err as Error).message);
      } finally {
        setBusy(false);
      }
    },
    [showToast],
  );

  const nextOptions = NEXT_STATUS_MAP[co.status];

  return (
    <div className="space-y-8" data-testid="co-detail-panel">
      {/* Header */}
      <header className="space-y-2">
        <a
          href="/admin/content-opportunities"
          className="text-xs text-muted-foreground hover:text-accent"
          data-testid="co-back-link"
        >
          ← 返回列表
        </a>
        <div className="flex flex-wrap items-center gap-3">
          <h1 className="text-2xl font-semibold">#{co.id} · {co.platform}</h1>
          <span
            className={"rounded-full px-3 py-1 text-sm " + statusChipClass(co.status)}
            data-testid="co-detail-status"
          >
            {STATUS_LABELS[co.status]}
          </span>
          {co.compliance_blocked ? (
            <span
              className="rounded-full bg-red-500/20 px-3 py-1 text-sm text-red-300"
              data-testid="co-detail-blocked"
              title={co.compliance_risk_types.join(", ") || "blocked"}
            >
              🛡️ 合规拦截 {formatRiskScore(co.compliance_risk_score)}
            </span>
          ) : (
            <span
              className="rounded-full bg-emerald-500/20 px-3 py-1 text-sm text-emerald-300"
              data-testid="co-detail-clean"
            >
              ✓ 合规通过
            </span>
          )}
        </div>
      </header>

      {/* Meta + content side-by-side */}
      <section className="grid gap-6 lg:grid-cols-[2fr_3fr]">
        <div className="rounded-xl border border-border bg-card/40 p-4">
          <h2 className="mb-3 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
            元数据
          </h2>
          <dl className="space-y-2 text-sm">
            <Row k="signal_id" v={co.signal_id} />
            <Row k="audience" v={co.audience ?? "—"} />
            <Row k="niche" v={co.niche ?? "—"} />
            <Row k="tone" v={co.tone ?? "—"} />
            <Row k="content_score" v={co.content_score.toFixed(1)} />
            <Row k="recommended_length" v={co.recommended_length ?? "—"} />
            <Row
              k="risk_types"
              v={co.compliance_risk_types.length > 0 ? co.compliance_risk_types.join(", ") : "—"}
            />
            <Row k="created_at" v={formatShortDate(co.created_at)} />
            <Row k="updated_at" v={formatShortDate(co.updated_at)} />
          </dl>
        </div>

        <div className="space-y-4">
          {co.hook && (
            <div className="rounded-xl border border-border bg-card/40 p-5">
              <h2 className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                Hook
              </h2>
              <p className="text-lg font-semibold" data-testid="co-hook">
                {co.hook}
              </p>
            </div>
          )}
          {co.title_candidates && co.title_candidates.length > 0 && (
            <div className="rounded-xl border border-border bg-card/40 p-5">
              <h2 className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                标题候选
              </h2>
              <ul
                className="space-y-1 text-sm"
                data-testid="co-title-candidates"
              >
                {co.title_candidates.map((t, i) => (
                  <li key={i}>• {t}</li>
                ))}
              </ul>
            </div>
          )}
          {co.material_ideas && co.material_ideas.length > 0 && (
            <div className="rounded-xl border border-border bg-card/40 p-5">
              <h2 className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                素材点
              </h2>
              <ul
                className="space-y-1 text-sm"
                data-testid="co-material-ideas"
              >
                {co.material_ideas.map((m, i) => (
                  <li key={i}>• {m}</li>
                ))}
              </ul>
            </div>
          )}
          {co.script_outline && (
            <div className="rounded-xl border border-border bg-card/40 p-5">
              <h2 className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                脚本大纲
              </h2>
              <pre
                className="whitespace-pre-wrap text-sm leading-relaxed"
                data-testid="co-script-outline"
              >
                {co.script_outline}
              </pre>
            </div>
          )}
          {co.cta && (
            <div className="rounded-xl border border-border bg-card/40 p-5">
              <h2 className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                CTA
              </h2>
              <blockquote
                className="border-l-2 border-accent pl-3 text-sm italic"
                data-testid="co-cta"
              >
                {co.cta}
              </blockquote>
            </div>
          )}
          {co.risk_warning && (
            <div className="rounded-xl border border-warning/40 bg-warning/10 p-5">
              <h2 className="mb-2 text-xs font-semibold uppercase tracking-wide text-warning">
                ⚠️ 风险提示
              </h2>
              <p className="text-sm" data-testid="co-risk-warning">
                {co.risk_warning}
              </p>
            </div>
          )}
        </div>
      </section>

      {/* Action bar */}
      <section
        className="sticky bottom-0 -mx-4 flex flex-wrap gap-2 border-t border-border bg-background/80 px-4 py-3 backdrop-blur"
        data-testid="co-action-bar"
      >
        {nextOptions.includes("approved") && (
          <button
            onClick={() =>
              doTransition(
                () => approveContentOpportunity(co.id),
                "批准",
              )
            }
            disabled={busy}
            className="rounded-md bg-accent px-4 py-2 text-sm font-semibold text-accent-foreground hover:opacity-90 disabled:opacity-40"
            data-testid="co-approve"
          >
            {busy ? "提交中…" : "✓ 批准"}
          </button>
        )}
        {nextOptions.includes("published") && (
          <button
            onClick={() =>
              doTransition(
                () => publishContentOpportunity(co.id),
                "发布",
              )
            }
            disabled={busy}
            className="rounded-md bg-emerald-600 px-4 py-2 text-sm font-semibold text-white hover:opacity-90 disabled:opacity-40"
            data-testid="co-publish"
          >
            {busy ? "提交中…" : "🚀 发布"}
          </button>
        )}
        {nextOptions.includes("rejected") && (
          <button
            onClick={() => setRejectOpen(true)}
            disabled={busy}
            className="rounded-md border border-danger/40 bg-danger/10 px-4 py-2 text-sm font-semibold text-danger hover:bg-danger/20 disabled:opacity-40"
            data-testid="co-reject"
          >
            ✕ 驳回
          </button>
        )}
        {nextOptions.length === 0 && (
          <span
            className="text-sm text-muted-foreground"
            data-testid="co-no-actions"
          >
            终态({STATUS_LABELS[co.status]}),无可用操作。
          </span>
        )}
      </section>

      {/* Reject modal */}
      {rejectOpen && (
        <RejectReasonModal
          busy={busy}
          reason={rejectReason}
          onChange={setRejectReason}
          onClose={() => setRejectOpen(false)}
          onSubmit={async () => {
            const body: ContentOpportunityRejectRequest = {
              reason: rejectReason.trim() || null,
            };
            await doTransition(
              () => rejectContentOpportunity(co.id, body),
              "驳回",
            );
            setRejectOpen(false);
            setRejectReason("");
          }}
        />
      )}

      {/* Toast */}
      {toast && (
        <div
          role="status"
          className={
            "fixed bottom-20 left-1/2 -translate-x-1/2 rounded-md px-4 py-2 text-sm shadow-lg " +
            (toast.kind === "ok"
              ? "bg-emerald-600 text-white"
              : "bg-red-600 text-white")
          }
          data-testid="co-toast"
        >
          {toast.text}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
function Row({ k, v }: { k: string; v: React.ReactNode }) {
  return (
    <div className="flex justify-between gap-2 border-b border-border/50 pb-1.5 text-xs">
      <dt className="text-muted-foreground">{k}</dt>
      <dd className="font-mono text-right">{v}</dd>
    </div>
  );
}

// ---------------------------------------------------------------------------
function RejectReasonModal({
  busy,
  reason,
  onChange,
  onClose,
  onSubmit,
}: {
  busy: boolean;
  reason: string;
  onChange: (v: string) => void;
  onClose: () => void;
  onSubmit: () => Promise<void>;
}) {
  const formId = useId();
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
      data-testid="co-reject-modal"
      role="dialog"
      aria-modal="true"
      aria-labelledby={`${formId}-title`}
    >
      <form
        onSubmit={(e) => {
          e.preventDefault();
          void onSubmit();
        }}
        className="w-full max-w-md rounded-xl border border-border bg-background p-6 shadow-2xl"
        data-testid="co-reject-form"
      >
        <header className="mb-4">
          <h2
            id={`${formId}-title`}
            className="text-lg font-semibold"
          >
            驳回原因(可选,最多 255 字)
          </h2>
          <p className="mt-1 text-xs text-muted-foreground">
            状态机:任意 → rejected(终态)
          </p>
        </header>
        <textarea
          value={reason}
          onChange={(e) => onChange(e.target.value)}
          maxLength={255}
          rows={4}
          autoFocus
          className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm"
          data-testid="co-reject-reason"
          placeholder="如:含违禁关键词 / 数据未经核实"
        />
        <footer className="mt-4 flex justify-end gap-2">
          <button
            type="button"
            onClick={onClose}
            disabled={busy}
            className="rounded-md border border-border px-3 py-1.5 text-sm hover:bg-muted disabled:opacity-40"
            data-testid="co-reject-cancel"
          >
            取消
          </button>
          <button
            type="submit"
            disabled={busy}
            className="rounded-md bg-danger px-3 py-1.5 text-sm font-semibold text-white hover:opacity-90 disabled:opacity-40"
            data-testid="co-reject-confirm"
          >
            {busy ? "提交中…" : "确认驳回"}
          </button>
        </footer>
      </form>
    </div>
  );
}

// Re-export ContentOpportunityStatus so callers don't have to import
// from `@/types` if they only need the alias.
export type { ContentOpportunityStatus };