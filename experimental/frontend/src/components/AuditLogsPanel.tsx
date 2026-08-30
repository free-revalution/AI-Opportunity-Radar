"use client";

/**
 * Phase 20 — sole-operator audit log viewer.
 *
 * Server component seeds the initial page from URL searchParams; the
 * client panel owns filter state and re-fetches when filters change.
 * URL is the source of truth — mirrors the ContentOpportunitiesPanel
 * pattern so browser back/forward + reload work.
 *
 * Each row can expand an inline metadata drawer so the operator can
 * inspect raw AuditLog.metadata_json without leaving the table.
 */

import { useCallback, useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";

import { fetchAuditLogs } from "@/lib/api";
import {
  ACTION_OPTIONS,
  ACTOR_TYPE_OPTIONS,
  RESULT_OPTIONS,
  actorTypeClass,
  formatActionMetadata,
  resourceLink,
  resultClass,
} from "@/lib/auditLogs";
import { formatShortDate } from "@/lib/contentOpportunities";
import { formatRelativeTime } from "@/lib/utils";
import type {
  AuditLogFilters,
  AuditLogItem,
  AuditLogsResponse,
} from "@/types";

const PAGE_SIZE = 50;

export interface AuditLogsPanelProps {
  initial: AuditLogsResponse;
  initialFilters: AuditLogFilters;
}

// ---------------------------------------------------------------------------
// Helpers — copy the URL-filter set into the local form state.
// ---------------------------------------------------------------------------
function filterToForm(filters: AuditLogFilters): FormState {
  return {
    actor_type: filters.actor_type ?? "",
    actor_id: filters.actor_id ?? "",
    action: filters.action ?? "",
    result: filters.result ?? "",
    resource_type: filters.resource_type ?? "",
    resource_id: filters.resource_id ?? "",
    since: filters.since ?? "",
    until: filters.until ?? "",
  };
}

function formToFilters(form: FormState, offset: number): AuditLogFilters {
  const set = (v: string): string | undefined =>
    v && v.length > 0 ? v : undefined;
  return {
    actor_type: set(form.actor_type),
    actor_id: set(form.actor_id),
    action: set(form.action),
    result: set(form.result),
    resource_type: set(form.resource_type),
    resource_id: set(form.resource_id),
    since: set(form.since),
    until: set(form.until),
    limit: PAGE_SIZE,
    offset,
  };
}

interface FormState {
  actor_type: string;
  actor_id: string;
  action: string;
  result: string;
  resource_type: string;
  resource_id: string;
  since: string;
  until: string;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------
export function AuditLogsPanel({
  initial,
  initialFilters,
}: AuditLogsPanelProps) {
  const router = useRouter();
  const searchParams = useSearchParams();

  const [data, setData] = useState<AuditLogsResponse>(initial);
  const [form, setForm] = useState<FormState>(filterToForm(initialFilters));
  const [offset, setOffset] = useState<number>(initial.offset);
  const [loading, setLoading] = useState(false);
  const [expandedId, setExpandedId] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);

  const total = data.total;
  const items = data.items;
  const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const pageIndex = Math.floor(offset / PAGE_SIZE) + 1;

  /** Push the current filter form to the URL — keeps back/forward
   *  navigable and bookmarkable. */
  const pushUrl = useCallback(
    (nextForm: FormState, nextOffset: number) => {
      const params = new URLSearchParams(searchParams.toString());
      const setOrDel = (k: string, v: string | undefined) => {
        if (v && v.length > 0) params.set(k, v);
        else params.delete(k);
      };
      setOrDel("actor_type", nextForm.actor_type);
      setOrDel("actor_id", nextForm.actor_id);
      setOrDel("action", nextForm.action);
      setOrDel("result", nextForm.result);
      setOrDel("resource_type", nextForm.resource_type);
      setOrDel("resource_id", nextForm.resource_id);
      setOrDel("since", nextForm.since);
      setOrDel("until", nextForm.until);
      if (nextOffset > 0) params.set("offset", String(nextOffset));
      else params.delete("offset");
      const qs = params.toString();
      router.replace(qs ? `/admin/audit-logs?${qs}` : "/admin/audit-logs", {
        scroll: false,
      });
    },
    [router, searchParams],
  );

  const runFetch = useCallback(
    async (nextForm: FormState, nextOffset: number) => {
      setLoading(true);
      setError(null);
      try {
        const filters = formToFilters(nextForm, nextOffset);
        const resp = await fetchAuditLogs(filters);
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
      setExpandedId(null);
      pushUrl(nextForm, 0);
      void runFetch(nextForm, 0);
    },
    [pushUrl, runFetch],
  );

  const onReset = useCallback(() => {
    const blank: FormState = {
      actor_type: "",
      actor_id: "",
      action: "",
      result: "",
      resource_type: "",
      resource_id: "",
      since: "",
      until: "",
    };
    setForm(blank);
    setOffset(0);
    setExpandedId(null);
    pushUrl(blank, 0);
    void runFetch(blank, 0);
  }, [pushUrl, runFetch]);

  const onPagePrev = useCallback(() => {
    const nextOffset = Math.max(0, offset - PAGE_SIZE);
    setExpandedId(null);
    pushUrl(form, nextOffset);
    void runFetch(form, nextOffset);
  }, [offset, form, pushUrl, runFetch]);

  const onPageNext = useCallback(() => {
    const nextOffset = offset + PAGE_SIZE;
    setExpandedId(null);
    pushUrl(form, nextOffset);
    void runFetch(form, nextOffset);
  }, [offset, form, pushUrl, runFetch]);

  const toggleExpanded = useCallback((id: number) => {
    setExpandedId((prev) => (prev === id ? null : id));
  }, []);

  // -----------------------------------------------------------------------
  // Form field handlers — local state only; URL + fetch trigger via
  // "应用筛选" button (so rapid typing doesn't spam the backend).
  // -----------------------------------------------------------------------
  const setField = (key: keyof FormState, value: string) =>
    setForm((prev) => ({ ...prev, [key]: value }));

  // Expose the form values to the test environment so they can read them
  // off `data-*` attrs (see AuditLogsPanel test).
  useEffect(() => {
    if (typeof document === "undefined") return;
    const root = document.querySelector("[data-testid='audit-logs-panel']");
    if (!root) return;
    root.setAttribute("data-form-actor_type", form.actor_type);
    root.setAttribute("data-form-action", form.action);
    root.setAttribute("data-form-result", form.result);
    root.setAttribute("data-offset", String(offset));
  }, [form, offset]);

  return (
    <div
      className="space-y-6"
      data-testid="audit-logs-panel"
      data-total={total}
    >
      {/* Filter bar -------------------------------------------------------*/}
      <section
        className="rounded-xl border border-border bg-card/40 p-4"
        data-testid="audit-filter-bar"
      >
        <div className="grid gap-3 md:grid-cols-3">
          {/* actor_type chips */}
          <div>
            <label className="text-xs uppercase tracking-wide text-muted-foreground">
              actor_type
            </label>
            <div className="mt-1 flex flex-wrap gap-2">
              <ChipFilter
                label="全部"
                value=""
                current={form.actor_type}
                onClick={(v) => applyFilters({ ...form, actor_type: v })}
                testid="chip-actor_type-all"
              />
              {ACTOR_TYPE_OPTIONS.map((opt) => (
                <ChipFilter
                  key={opt}
                  label={opt}
                  value={opt}
                  current={form.actor_type}
                  onClick={(v) => applyFilters({ ...form, actor_type: v })}
                  testid={`chip-actor_type-${opt}`}
                />
              ))}
            </div>
          </div>

          {/* result chips */}
          <div>
            <label className="text-xs uppercase tracking-wide text-muted-foreground">
              result
            </label>
            <div className="mt-1 flex flex-wrap gap-2">
              <ChipFilter
                label="全部"
                value=""
                current={form.result}
                onClick={(v) => applyFilters({ ...form, result: v })}
                testid="chip-result-all"
              />
              {RESULT_OPTIONS.map((opt) => (
                <ChipFilter
                  key={opt}
                  label={opt}
                  value={opt}
                  current={form.result}
                  onClick={(v) => applyFilters({ ...form, result: v })}
                  testid={`chip-result-${opt}`}
                />
              ))}
            </div>
          </div>

          {/* action select */}
          <div>
            <label className="text-xs uppercase tracking-wide text-muted-foreground">
              action
            </label>
            <select
              value={form.action}
              onChange={(e) => applyFilters({ ...form, action: e.target.value })}
              className="mt-1 w-full rounded-md border border-border bg-background px-2 py-1.5 text-sm"
              data-testid="select-action"
            >
              <option value="">(all)</option>
              {ACTION_OPTIONS.map((opt) => (
                <option key={opt} value={opt}>
                  {opt}
                </option>
              ))}
            </select>
          </div>
        </div>

        <div className="mt-3 grid gap-3 md:grid-cols-4">
          <TextInput
            label="actor_id"
            value={form.actor_id}
            onChange={(v) => setField("actor_id", v)}
            placeholder="e.g. ou_xyz"
            testid="input-actor_id"
          />
          <TextInput
            label="resource_type"
            value={form.resource_type}
            onChange={(v) => setField("resource_type", v)}
            placeholder="e.g. content_opportunity"
            testid="input-resource_type"
          />
          <TextInput
            label="resource_id"
            value={form.resource_id}
            onChange={(v) => setField("resource_id", v)}
            placeholder="e.g. 42"
            testid="input-resource_id"
          />
          <div className="flex items-end gap-2">
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

        <div className="mt-3 grid gap-3 md:grid-cols-2">
          <TextInput
            label="since (ISO)"
            value={form.since}
            onChange={(v) => setField("since", v)}
            placeholder="2026-08-01T00:00:00Z"
            testid="input-since"
          />
          <TextInput
            label="until (ISO)"
            value={form.until}
            onChange={(v) => setField("until", v)}
            placeholder="2026-08-31T23:59:59Z"
            testid="input-until"
          />
        </div>
      </section>

      {error && (
        <p
          className="rounded-md border border-danger/40 bg-danger/10 p-3 text-sm"
          data-testid="audit-logs-error"
        >
          {error}
        </p>
      )}

      {/* Table ------------------------------------------------------------*/}
      <section
        className="overflow-x-auto rounded-xl border border-border bg-card/40"
        data-testid="audit-table-wrap"
      >
        <table className="min-w-full text-sm">
          <thead className="bg-muted/40 text-xs uppercase tracking-wide text-muted-foreground">
            <tr>
              <th className="px-3 py-2 text-left">时间</th>
              <th className="px-3 py-2 text-left">actor</th>
              <th className="px-3 py-2 text-left">action</th>
              <th className="px-3 py-2 text-left">result</th>
              <th className="px-3 py-2 text-left">resource</th>
              <th className="px-3 py-2 text-left">metadata</th>
              <th className="px-3 py-2 text-right" />
            </tr>
          </thead>
          <tbody>
            {items.length === 0 ? (
              <tr>
                <td
                  colSpan={7}
                  className="px-3 py-8 text-center text-muted-foreground"
                  data-testid="audit-empty"
                >
                  暂无审计日志 — 调整筛选条件或等待后台活动。
                </td>
              </tr>
            ) : (
              items.map((it) => (
                <AuditRow
                  key={it.id}
                  item={it}
                  expanded={expandedId === it.id}
                  onToggle={() => toggleExpanded(it.id)}
                />
              ))
            )}
          </tbody>
        </table>
      </section>

      {/* Pagination -------------------------------------------------------*/}
      <section
        className="flex flex-wrap items-center justify-between gap-3"
        data-testid="audit-pagination"
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
    </div>
  );
}

// ---------------------------------------------------------------------------
// Row sub-component — keeps the parent JSX from sprawling.
// ---------------------------------------------------------------------------
function AuditRow({
  item,
  expanded,
  onToggle,
}: {
  item: AuditLogItem;
  expanded: boolean;
  onToggle: () => void;
}) {
  const link = resourceLink(item.resource_type, item.resource_id);
  return (
    <>
      <tr
        className="border-t border-border hover:bg-muted/30"
        data-testid={`audit-row-${item.id}`}
      >
        <td className="px-3 py-2 font-mono text-xs">
          <div className="text-foreground">
            {formatShortDate(item.created_at)}
          </div>
          <div className="text-[10px] text-muted-foreground">
            {formatRelativeTime(item.created_at)}
          </div>
        </td>
        <td className="px-3 py-2">
          <span
            className={`rounded-full px-2 py-0.5 text-[10px] font-mono uppercase tracking-wider ${actorTypeClass(item.actor_type)}`}
            data-testid={`audit-actor-${item.id}`}
          >
            {item.actor_type}
          </span>
          {item.actor_id && (
            <div className="mt-1 font-mono text-[10px] text-muted-foreground">
              {item.actor_id}
            </div>
          )}
        </td>
        <td className="px-3 py-2 font-mono text-xs">{item.action}</td>
        <td className="px-3 py-2">
          <span
            className={`rounded-full px-2 py-0.5 text-[10px] font-mono uppercase tracking-wider ${resultClass(item.result)}`}
            data-testid={`audit-result-${item.id}`}
          >
            {item.result}
          </span>
        </td>
        <td className="px-3 py-2 text-xs">
          {link.href ? (
            <a
              href={link.href}
              className="font-mono text-accent hover:underline"
              data-testid={`audit-target-${item.id}`}
            >
              {link.label}
            </a>
          ) : (
            <span className="font-mono text-muted-foreground">
              {link.label}
            </span>
          )}
        </td>
        <td className="px-3 py-2 font-mono text-xs">
          {formatActionMetadata(item.action, item.metadata_json)}
        </td>
        <td className="px-3 py-2 text-right">
          <button
            type="button"
            onClick={onToggle}
            className="rounded-md border border-border bg-background px-2 py-0.5 text-xs hover:bg-muted"
            data-testid={`audit-toggle-${item.id}`}
            aria-label={expanded ? "Hide metadata" : "Show metadata"}
          >
            {expanded ? "收起" : "···"}
          </button>
        </td>
      </tr>
      {expanded && (
        <tr
          className="border-t border-border bg-muted/30"
          data-testid={`audit-meta-${item.id}`}
        >
          <td colSpan={7} className="px-3 py-3">
            <pre
              className="max-h-64 overflow-auto rounded-md bg-background p-3 text-[11px] leading-relaxed"
              data-testid={`audit-meta-pre-${item.id}`}
            >
              {JSON.stringify(item.metadata_json ?? {}, null, 2)}
            </pre>
          </td>
        </tr>
      )}
    </>
  );
}

// ---------------------------------------------------------------------------
// Tiny UI atoms — kept inline to avoid inflating `components/ui`.
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
      className={`rounded-full border px-3 py-1 text-xs transition ${
        active
          ? "border-accent bg-accent text-accent-foreground"
          : "border-border bg-card/40 text-muted-foreground hover:bg-muted"
      }`}
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
