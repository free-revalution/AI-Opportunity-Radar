"use client";

/**
 * Phase 24 — Compliance Engine operator surface (panel).
 *
 * Mirrors the AuditLogsPanel structure but specialised for
 * `GET /api/admin/compliance`:
 *   * risk_level chip group (all / low / medium / high / blocked)
 *   * risk_type chip group (all / pii / prompt_injection / ...)
 *   * since text input
 *   * table of ComplianceAuditItem rows + per-row Override button
 *   * pagination
 *   * override modal — requires reason ≥ 10 chars
 *
 * URL is the source of truth (browser back/forward + reload).
 */

import { useCallback, useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";

import {
  fetchComplianceAudits,
  overrideComplianceAudit,
} from "@/lib/api";
import {
  COMPLIANCE_RISK_LEVELS,
  COMPLIANCE_RISK_TYPES,
} from "@/types";
import type {
  ComplianceAuditFilters,
  ComplianceAuditItem,
  ComplianceAuditResponse,
  ComplianceRiskLevel,
  ComplianceRiskType,
} from "@/types";

import { formatShortDate } from "@/lib/contentOpportunities";
import { formatRelativeTime } from "@/lib/utils";

const PAGE_SIZE = 50;

export interface CompliancePanelProps {
  initial: ComplianceAuditResponse;
  initialFilters: ComplianceAuditFilters;
}

// ---------------------------------------------------------------------------
// Risk-level chip class helper — mirrors the adminCrud palette.
function riskLevelClass(level: ComplianceRiskLevel | null): string {
  switch (level) {
    case "low":
      return "bg-emerald-500/20 text-emerald-300";
    case "medium":
      return "bg-amber-500/20 text-amber-300";
    case "high":
      return "bg-orange-500/20 text-orange-300";
    case "blocked":
      return "bg-red-500/20 text-red-300";
    default:
      return "bg-muted text-muted-foreground";
  }
}

const RISK_TYPE_LABELS: Record<ComplianceRiskType, string> = {
  pii: "PII",
  prompt_injection: "提示注入",
  content_safety: "内容安全",
  copyright: "版权",
  source_policy: "源策略",
};

function formatRiskTypes(types: ComplianceRiskType[]): string {
  if (!types.length) return "—";
  return types.map((t) => RISK_TYPE_LABELS[t] ?? t).join(" · ");
}

function filterToForm(filters: ComplianceAuditFilters): FormState {
  return {
    risk_level: filters.risk_level ?? "",
    risk_type: filters.risk_type ?? "",
    resource_type: filters.resource_type ?? "",
    since: filters.since ?? "",
  };
}

function formToFilters(form: FormState, offset: number): ComplianceAuditFilters {
  const set = (v: string): string | undefined =>
    v && v.length > 0 ? v : undefined;
  return {
    risk_level: (set(form.risk_level) || "") as ComplianceAuditFilters["risk_level"],
    risk_type: (set(form.risk_type) || "") as ComplianceAuditFilters["risk_type"],
    resource_type: set(form.resource_type),
    since: set(form.since),
    limit: PAGE_SIZE,
    offset,
  };
}

interface FormState {
  risk_level: string;
  risk_type: string;
  resource_type: string;
  since: string;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------
export function CompliancePanel({
  initial,
  initialFilters,
}: CompliancePanelProps) {
  const router = useRouter();
  const searchParams = useSearchParams();

  const [data, setData] = useState<ComplianceAuditResponse>(initial);
  const [form, setForm] = useState<FormState>(filterToForm(initialFilters));
  const [offset, setOffset] = useState<number>(initial.offset);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [overrideTarget, setOverrideTarget] = useState<ComplianceAuditItem | null>(
    null,
  );
  const [overrideReason, setOverrideReason] = useState("");
  const [overrideError, setOverrideError] = useState<string | null>(null);
  const [overrideSubmitting, setOverrideSubmitting] = useState(false);
  const [overrideSuccess, setOverrideSuccess] = useState<string | null>(null);

  const total = data.total;
  const items = data.items;
  const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const pageIndex = Math.floor(offset / PAGE_SIZE) + 1;

  const pushUrl = useCallback(
    (nextForm: FormState, nextOffset: number) => {
      const params = new URLSearchParams(searchParams.toString());
      const setOrDel = (k: string, v: string | undefined) => {
        if (v && v.length > 0) params.set(k, v);
        else params.delete(k);
      };
      setOrDel("risk_level", nextForm.risk_level);
      setOrDel("risk_type", nextForm.risk_type);
      setOrDel("resource_type", nextForm.resource_type);
      setOrDel("since", nextForm.since);
      if (nextOffset > 0) params.set("offset", String(nextOffset));
      else params.delete("offset");
      const qs = params.toString();
      router.replace(
        qs ? `/admin/compliance?${qs}` : "/admin/compliance",
        { scroll: false },
      );
    },
    [router, searchParams],
  );

  const runFetch = useCallback(
    async (nextForm: FormState, nextOffset: number) => {
      setLoading(true);
      setError(null);
      try {
        const filters = formToFilters(nextForm, nextOffset);
        const resp = await fetchComplianceAudits(filters);
        setData(resp);
        setOffset(nextOffset);
      } catch (e) {
        setError(e instanceof Error ? e.message : "fetch failed");
      } finally {
        setLoading(false);
      }
    },
    [],
  );

  const applyFilters = useCallback(
    (nextForm: FormState) => {
      setForm(nextForm);
      pushUrl(nextForm, 0);
      void runFetch(nextForm, 0);
    },
    [pushUrl, runFetch],
  );

  const onReset = useCallback(() => {
    const blank: FormState = {
      risk_level: "",
      risk_type: "",
      resource_type: "",
      since: "",
    };
    setForm(blank);
    setOffset(0);
    pushUrl(blank, 0);
    void runFetch(blank, 0);
  }, [pushUrl, runFetch]);

  const onPagePrev = useCallback(() => {
    const nextOffset = Math.max(0, offset - PAGE_SIZE);
    pushUrl(form, nextOffset);
    void runFetch(form, nextOffset);
  }, [offset, form, pushUrl, runFetch]);

  const onPageNext = useCallback(() => {
    const nextOffset = offset + PAGE_SIZE;
    pushUrl(form, nextOffset);
    void runFetch(form, nextOffset);
  }, [offset, form, pushUrl, runFetch]);

  const setField = (key: keyof FormState, value: string) =>
    setForm((prev) => ({ ...prev, [key]: value }));

  // ---- Override modal handlers ------------------------------------------
  const openOverride = useCallback((item: ComplianceAuditItem) => {
    setOverrideTarget(item);
    setOverrideReason("");
    setOverrideError(null);
    setOverrideSuccess(null);
  }, []);

  const closeOverride = useCallback(() => {
    setOverrideTarget(null);
    setOverrideReason("");
    setOverrideError(null);
  }, []);

  const submitOverride = useCallback(async () => {
    if (!overrideTarget) return;
    if (overrideReason.trim().length < 10) {
      setOverrideError("原因至少需要 10 个字符（操作员审计要求）");
      return;
    }
    setOverrideSubmitting(true);
    setOverrideError(null);
    try {
      const resp = await overrideComplianceAudit(
        overrideTarget.id,
        overrideReason.trim(),
      );
      setOverrideSuccess(`已写入 override audit #${resp.override_audit_log_id}`);
      // Refresh listing so the row shows the overridden badge.
      await runFetch(form, offset);
      setTimeout(() => {
        closeOverride();
      }, 800);
    } catch (e) {
      setOverrideError(e instanceof Error ? e.message : "override failed");
    } finally {
      setOverrideSubmitting(false);
    }
  }, [overrideTarget, overrideReason, runFetch, form, offset, closeOverride]);

  useEffect(() => {
    if (typeof document === "undefined") return;
    const root = document.querySelector("[data-testid='compliance-panel']");
    if (!root) return;
    root.setAttribute("data-form-risk_level", form.risk_level);
    root.setAttribute("data-form-risk_type", form.risk_type);
    root.setAttribute("data-offset", String(offset));
  }, [form, offset]);

  return (
    <div
      className="space-y-6"
      data-testid="compliance-panel"
      data-total={total}
    >
      {/* Filter bar ----------------------------------------------------- */}
      <section
        className="rounded-xl border border-border bg-card/40 p-4"
        data-testid="compliance-filter-bar"
      >
        <div className="grid gap-3 md:grid-cols-2">
          {/* risk_level chips */}
          <div>
            <label className="text-xs uppercase tracking-wide text-muted-foreground">
              risk_level
            </label>
            <div className="mt-1 flex flex-wrap gap-2">
              <ChipFilter
                label="全部"
                value=""
                current={form.risk_level}
                onClick={(v) => applyFilters({ ...form, risk_level: v })}
                testid="chip-risk_level-all"
              />
              {COMPLIANCE_RISK_LEVELS.map((lvl) => (
                <ChipFilter
                  key={lvl}
                  label={lvl}
                  value={lvl}
                  current={form.risk_level}
                  onClick={(v) => applyFilters({ ...form, risk_level: v })}
                  testid={`chip-risk_level-${lvl}`}
                />
              ))}
            </div>
          </div>

          {/* risk_type chips */}
          <div>
            <label className="text-xs uppercase tracking-wide text-muted-foreground">
              risk_type
            </label>
            <div className="mt-1 flex flex-wrap gap-2">
              <ChipFilter
                label="全部"
                value=""
                current={form.risk_type}
                onClick={(v) => applyFilters({ ...form, risk_type: v })}
                testid="chip-risk_type-all"
              />
              {COMPLIANCE_RISK_TYPES.map((t) => (
                <ChipFilter
                  key={t}
                  label={RISK_TYPE_LABELS[t]}
                  value={t}
                  current={form.risk_type}
                  onClick={(v) => applyFilters({ ...form, risk_type: v })}
                  testid={`chip-risk_type-${t}`}
                />
              ))}
            </div>
          </div>
        </div>

        <div className="mt-3 grid gap-3 md:grid-cols-4">
          <TextInput
            label="resource_type"
            value={form.resource_type}
            onChange={(v) => setField("resource_type", v)}
            placeholder="feishu_message"
            testid="input-resource_type"
          />
          <TextInput
            label="since (ISO)"
            value={form.since}
            onChange={(v) => setField("since", v)}
            placeholder="2026-08-01T00:00:00Z"
            testid="input-since"
          />
          <div className="flex items-end gap-2 md:col-span-2">
            <button
              type="button"
              onClick={() => applyFilters(form)}
              disabled={loading}
              className="rounded-md bg-accent px-3 py-1.5 text-sm font-medium text-accent-foreground disabled:opacity-50"
              data-testid="btn-apply"
            >
              应用筛选
            </button>
            <button
              type="button"
              onClick={onReset}
              disabled={loading}
              className="rounded-md border border-border bg-card/40 px-3 py-1.5 text-sm hover:bg-muted disabled:opacity-50"
              data-testid="btn-reset"
            >
              ↻ Reset
            </button>
          </div>
        </div>
      </section>

      {error && (
        <p
          className="rounded-md border border-danger/40 bg-danger/10 p-3 text-sm"
          data-testid="compliance-error"
        >
          {error}
        </p>
      )}

      {/* Table ----------------------------------------------------------- */}
      <section
        className="overflow-x-auto rounded-xl border border-border bg-card/40"
        data-testid="compliance-table-wrap"
      >
        <table className="min-w-full text-sm">
          <thead className="bg-muted/40 text-xs uppercase tracking-wide text-muted-foreground">
            <tr>
              <th className="px-3 py-2 text-left">时间</th>
              <th className="px-3 py-2 text-left">risk</th>
              <th className="px-3 py-2 text-left">types</th>
              <th className="px-3 py-2 text-left">resource</th>
              <th className="px-3 py-2 text-left">reason</th>
              <th className="px-3 py-2 text-left">context</th>
              <th className="px-3 py-2 text-right" />
            </tr>
          </thead>
          <tbody>
            {items.length === 0 ? (
              <tr>
                <td
                  colSpan={7}
                  className="px-3 py-8 text-center text-muted-foreground"
                  data-testid="compliance-empty"
                >
                  暂无合规阻断记录 — 调整筛选条件或等待后台活动。
                </td>
              </tr>
            ) : (
              items.map((it) => (
                <ComplianceRow
                  key={it.id}
                  item={it}
                  onOverride={() => openOverride(it)}
                />
              ))
            )}
          </tbody>
        </table>
      </section>

      {/* Pagination ------------------------------------------------------ */}
      <section
        className="flex flex-wrap items-center justify-between gap-3"
        data-testid="compliance-pagination"
      >
        <p className="text-xs text-muted-foreground">
          共 <span className="font-mono text-foreground">{total}</span> 条 ·
          第 <span className="font-mono text-foreground">{pageIndex}</span> /
          <span className="font-mono text-foreground"> {pageCount}</span> 页
          (每页 {PAGE_SIZE})
        </p>
        <div className="flex gap-2">
          <button
            type="button"
            onClick={onPagePrev}
            disabled={loading || offset === 0}
            className="rounded-md border border-border bg-card/40 px-3 py-1.5 text-sm hover:bg-muted disabled:opacity-50"
            data-testid="btn-prev"
          >
            ← 上一页
          </button>
          <button
            type="button"
            onClick={onPageNext}
            disabled={loading || offset + PAGE_SIZE >= total}
            className="rounded-md border border-border bg-card/40 px-3 py-1.5 text-sm hover:bg-muted disabled:opacity-50"
            data-testid="btn-next"
          >
            下一页 →
          </button>
        </div>
      </section>

      {/* Override modal ------------------------------------------------- */}
      {overrideTarget && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/60"
          data-testid="override-modal"
          role="dialog"
        >
          <div className="w-full max-w-md rounded-xl border border-border bg-card p-6 shadow-xl">
            <h2 className="text-lg font-semibold">Override 合规阻断</h2>
            <p className="mt-2 text-sm text-muted-foreground">
              audit #{overrideTarget.id} · {overrideTarget.resource_type} ·{" "}
              <span
                className={`rounded-full px-2 py-0.5 text-[10px] font-mono uppercase tracking-wider ${riskLevelClass(overrideTarget.risk_level)}`}
              >
                {overrideTarget.risk_level ?? "—"}
              </span>
            </p>
            <p className="mt-1 text-xs text-muted-foreground">
              {overrideTarget.reason || "(no reason recorded)"}
            </p>

            <label
              htmlFor="override-reason"
              className="mt-4 block text-xs uppercase tracking-wide text-muted-foreground"
            >
              原因 (≥ 10 字符)
            </label>
            <textarea
              id="override-reason"
              value={overrideReason}
              onChange={(e) => setOverrideReason(e.target.value)}
              rows={3}
              className="mt-1 w-full rounded-md border border-border bg-background px-2 py-1.5 text-sm"
              data-testid="override-reason-input"
            />

            {overrideError && (
              <p
                className="mt-2 text-xs text-danger"
                data-testid="override-error"
              >
                {overrideError}
              </p>
            )}
            {overrideSuccess && (
              <p
                className="mt-2 text-xs text-emerald-300"
                data-testid="override-success"
              >
                {overrideSuccess}
              </p>
            )}

            <div className="mt-4 flex justify-end gap-2">
              <button
                type="button"
                onClick={closeOverride}
                disabled={overrideSubmitting}
                className="rounded-md border border-border bg-card/40 px-3 py-1.5 text-sm hover:bg-muted disabled:opacity-50"
                data-testid="override-cancel"
              >
                取消
              </button>
              <button
                type="button"
                onClick={submitOverride}
                disabled={
                  overrideSubmitting || overrideReason.trim().length < 10
                }
                className="rounded-md bg-accent px-3 py-1.5 text-sm font-medium text-accent-foreground disabled:opacity-50"
                data-testid="override-submit"
              >
                {overrideSubmitting ? "提交中…" : "提交 Override"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Row sub-component
// ---------------------------------------------------------------------------
function ComplianceRow({
  item,
  onOverride,
}: {
  item: ComplianceAuditItem;
  onOverride: () => void;
}) {
  return (
    <tr
      className="border-t border-border hover:bg-muted/30"
      data-testid={`compliance-row-${item.id}`}
    >
      <td className="px-3 py-2 font-mono text-xs">
        <div>{formatShortDate(item.created_at)}</div>
        <div className="text-[10px] text-muted-foreground">
          {formatRelativeTime(item.created_at)}
        </div>
      </td>
      <td className="px-3 py-2">
        <span
          className={`rounded-full px-2 py-0.5 text-[10px] font-mono uppercase tracking-wider ${riskLevelClass(item.risk_level)}`}
          data-testid={`compliance-risk-${item.id}`}
        >
          {item.risk_level ?? "—"}
        </span>
        {item.overridden && (
          <span
            className="ml-2 rounded-full bg-blue-500/20 px-2 py-0.5 text-[10px] font-mono uppercase tracking-wider text-blue-300"
            data-testid={`compliance-overridden-${item.id}`}
          >
            overridden
          </span>
        )}
      </td>
      <td className="px-3 py-2 text-xs">
        {formatRiskTypes(item.risk_types)}
      </td>
      <td className="px-3 py-2 text-xs">
        <div className="font-mono">{item.resource_type ?? "—"}</div>
        <div className="text-[10px] text-muted-foreground">
          {item.resource_id ?? ""}
        </div>
      </td>
      <td className="px-3 py-2 text-xs">{item.reason || "—"}</td>
      <td className="px-3 py-2 text-[10px] font-mono text-muted-foreground">
        {item.context}
      </td>
      <td className="px-3 py-2 text-right">
        <button
          type="button"
          onClick={onOverride}
          disabled={item.overridden}
          className="rounded-md border border-border bg-card/40 px-2 py-1 text-xs hover:bg-muted disabled:opacity-50"
          data-testid={`compliance-override-btn-${item.id}`}
        >
          {item.overridden ? "Overridden" : "Override"}
        </button>
      </td>
    </tr>
  );
}

// ---------------------------------------------------------------------------
// Tiny shared helpers (kept local so the panel is self-contained)
// ---------------------------------------------------------------------------
function ChipFilter({
  label,
  value,
  current,
  onClick,
  testid,
}: {
  label: string;
  value: string;
  current: string;
  onClick: (v: string) => void;
  testid: string;
}) {
  const active = current === value;
  return (
    <button
      type="button"
      onClick={() => onClick(value)}
      className={
        "rounded-full border px-2.5 py-0.5 text-xs " +
        (active
          ? "border-accent bg-accent text-accent-foreground"
          : "border-border bg-card/40 text-muted-foreground hover:bg-muted")
      }
      data-testid={testid}
      data-active={active ? "true" : "false"}
    >
      {label}
    </button>
  );
}

function TextInput({
  label,
  value,
  onChange,
  placeholder,
  testid,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
  testid: string;
}) {
  return (
    <div>
      <label className="text-xs uppercase tracking-wide text-muted-foreground">
        {label}
      </label>
      <input
        type="text"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        className="mt-1 w-full rounded-md border border-border bg-background px-2 py-1.5 text-sm"
        data-testid={testid}
      />
    </div>
  );
}
