"use client";

/**
 * Phase 22 — sole-operator source compliance console.
 *
 * Server seeds `initialItems` + `initialFilters`; the client owns
 * filter URL state, re-fetches on change, and opens the Patch Compliance
 * modal. Same URL-sync + stat-card + audit-deep-link pattern as the
 * activation / subscriptions panels.
 */

import { useCallback, useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";

import {
  fetchSources,
  updateSourceCompliance,
  type SourceListParams,
} from "@/lib/api";
import {
  COMPLIANCE_LEVELS,
  COMPLIANCE_LEVEL_HINTS,
  auditDeepLink,
  statusChipClass,
} from "@/lib/adminCrud";
import { formatShortDate, truncate } from "@/lib/contentOpportunities";
import { formatRelativeTime } from "@/lib/utils";
import type {
  ComplianceLevelValue,
  Source,
  SourceListResponse,
} from "@/types";

export interface SourcesPanelProps {
  initial: SourceListResponse;
  initialFilters: {
    compliance_level?: string;
    enabled?: string;
  };
}

interface FormState {
  compliance_level: string;
  enabled: string;
}

function filterToForm(f: SourcesPanelProps["initialFilters"]): FormState {
  return {
    compliance_level: f.compliance_level ?? "",
    enabled: f.enabled ?? "",
  };
}

function formToParams(form: FormState): SourceListParams {
  return {
    compliance_level:
      form.compliance_level && form.compliance_level.length > 0
        ? form.compliance_level
        : undefined,
    limit: 1000,
  };
}

export function SourcesPanel({
  initial,
  initialFilters,
}: SourcesPanelProps) {
  const router = useRouter();
  const searchParams = useSearchParams();

  const [items, setItems] = useState<Source[]>(initial.items);
  const [count, setCount] = useState<number>(initial.count);
  const [form, setForm] = useState<FormState>(filterToForm(initialFilters));
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [toast, setToast] = useState<{ kind: "ok" | "err"; text: string } | null>(
    null,
  );

  const [patchTarget, setPatchTarget] = useState<Source | null>(null);

  const showToast = useCallback(
    (kind: "ok" | "err", text: string) => {
      setToast({ kind, text });
      window.setTimeout(() => setToast(null), 3000);
    },
    [],
  );

  const pushUrl = useCallback(
    (next: FormState) => {
      const params = new URLSearchParams(searchParams.toString());
      const setOrDel = (k: string, v: string) => {
        if (v && v.length > 0) params.set(k, v);
        else params.delete(k);
      };
      setOrDel("compliance_level", next.compliance_level);
      setOrDel("enabled", next.enabled);
      const qs = params.toString();
      router.replace(
        qs ? `/admin/sources?${qs}` : "/admin/sources",
        { scroll: false },
      );
    },
    [router, searchParams],
  );

  const refresh = useCallback(
    async (next: FormState) => {
      setLoading(true);
      setError(null);
      try {
        const base = await fetchSources(formToParams(next));
        let filtered = base.items;
        if (next.enabled === "enabled") {
          filtered = filtered.filter((it) => it.enabled);
        } else if (next.enabled === "disabled") {
          filtered = filtered.filter((it) => !it.enabled);
        }
        setItems(filtered);
        setCount(filtered.length);
      } catch (e) {
        setError(e instanceof Error ? e.message : "fetch failed");
      } finally {
        setLoading(false);
      }
    },
    [],
  );

  const applyFilters = useCallback(
    (next: FormState) => {
      setForm(next);
      pushUrl(next);
      void refresh(next);
    },
    [pushUrl, refresh],
  );

  const onReset = useCallback(() => {
    const blank: FormState = { compliance_level: "", enabled: "" };
    setForm(blank);
    pushUrl(blank);
    void refresh(blank);
  }, [pushUrl, refresh]);

  const onPatched = useCallback(
    (resp: Source) => {
      setPatchTarget(null);
      showToast("ok", `✅ ${resp.name} 合规级别 → ${resp.compliance_level}`);
      void refresh(form);
    },
    [refresh, form, showToast],
  );

  // Stat card counts (computed off the unfiltered list — keep all rows
  // visible so the operator can audit compliance posture at a glance).
  const counts = items.reduce<
    Record<ComplianceLevelValue | "enabled" | "disabled", number>
  >(
    (acc, it) => {
      acc[it.compliance_level] = (acc[it.compliance_level] ?? 0) + 1;
      if (it.enabled) acc.enabled = (acc.enabled ?? 0) + 1;
      else acc.disabled = (acc.disabled ?? 0) + 1;
      return acc;
    },
    {
      A: 0,
      B: 0,
      C: 0,
      D: 0,
      E: 0,
      enabled: 0,
      disabled: 0,
    },
  );

  useEffect(() => {
    if (typeof document === "undefined") return;
    const root = document.querySelector("[data-testid='sources-panel']");
    if (!root) return;
    root.setAttribute("data-form-compliance_level", form.compliance_level);
    root.setAttribute("data-form-enabled", form.enabled);
  }, [form]);

  return (
    <div className="space-y-8" data-testid="sources-panel" data-count={count}>
      {/* Stat cards -------------------------------------------------------*/}
      <section
        className="grid gap-4 md:grid-cols-7"
        data-testid="sources-stats"
      >
        <StatCard label="总 source" value={String(count)} testid="stat-total" />
        <StatCard label="已启用" value={String(counts.enabled)} testid="stat-enabled" />
        <StatCard label="已停用" value={String(counts.disabled)} testid="stat-disabled" />
        <StatCard label="Level A" value={String(counts.A)} testid="stat-A" />
        <StatCard label="Level B" value={String(counts.B)} testid="stat-B" />
        <StatCard label="Level C" value={String(counts.C)} testid="stat-C" />
        <StatCard label="Level D+E" value={String(counts.D + counts.E)} testid="stat-DE" />
      </section>

      {/* Filters ---------------------------------------------------------*/}
      <section
        className="rounded-xl border border-border bg-card/40 p-4"
        data-testid="sources-filter-bar"
      >
        <div className="grid gap-3 md:grid-cols-2">
          <div>
            <label className="text-xs uppercase tracking-wide text-muted-foreground">
              compliance_level
            </label>
            <div className="mt-1 flex flex-wrap gap-2">
              <ChipFilter
                label="全部"
                value=""
                current={form.compliance_level}
                onClick={(v) =>
                  applyFilters({ ...form, compliance_level: v })
                }
                testid="chip-level-all"
              />
              {COMPLIANCE_LEVELS.map((lv) => (
                <ChipFilter
                  key={lv}
                  label={`${lv} · ${COMPLIANCE_LEVEL_HINTS[lv]}`}
                  value={lv}
                  current={form.compliance_level}
                  onClick={(v) =>
                    applyFilters({ ...form, compliance_level: v })
                  }
                  testid={`chip-level-${lv}`}
                />
              ))}
            </div>
          </div>
          <div>
            <label className="text-xs uppercase tracking-wide text-muted-foreground">
              enabled
            </label>
            <select
              value={form.enabled}
              onChange={(e) => applyFilters({ ...form, enabled: e.target.value })}
              className="mt-1 w-full rounded-md border border-border bg-background px-2 py-1.5 text-sm"
              data-testid="select-enabled"
            >
              <option value="">(all)</option>
              <option value="enabled">已启用</option>
              <option value="disabled">已停用</option>
            </select>
          </div>
        </div>
        <div className="mt-3 flex items-center gap-2">
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
      </section>

      {error && (
        <p
          className="rounded-md border border-danger/40 bg-danger/10 p-3 text-sm"
          data-testid="sources-error"
        >
          {error}
        </p>
      )}

      {/* Table ------------------------------------------------------------*/}
      <section
        className="overflow-x-auto rounded-xl border border-border bg-card/40"
        data-testid="sources-table-wrap"
      >
        <table className="min-w-full text-sm">
          <thead className="bg-muted/40 text-xs uppercase tracking-wide text-muted-foreground">
            <tr>
              <th className="px-3 py-2 text-left">ID</th>
              <th className="px-3 py-2 text-left">name</th>
              <th className="px-3 py-2 text-left">type</th>
              <th className="px-3 py-2 text-left">url</th>
              <th className="px-3 py-2 text-left">enabled</th>
              <th className="px-3 py-2 text-left">compliance</th>
              <th className="px-3 py-2 text-left">last_check</th>
              <th className="px-3 py-2 text-right">操作</th>
            </tr>
          </thead>
          <tbody>
            {items.length === 0 ? (
              <tr>
                <td
                  colSpan={8}
                  className="px-3 py-8 text-center text-muted-foreground"
                  data-testid="sources-empty"
                >
                  当前过滤下没有 source。
                </td>
              </tr>
            ) : (
              items.map((it) => (
                <tr
                  key={it.id}
                  className="border-t border-border hover:bg-muted/30"
                  data-testid={`source-row-${it.id}`}
                >
                  <td className="px-3 py-2 font-mono text-xs">#{it.id}</td>
                  <td className="px-3 py-2 font-mono text-xs">{it.name}</td>
                  <td className="px-3 py-2 font-mono text-xs text-muted-foreground">
                    {it.type}
                  </td>
                  <td
                    className="px-3 py-2 font-mono text-xs text-muted-foreground"
                    title={it.url ?? ""}
                  >
                    {truncate(it.url, 32)}
                  </td>
                  <td className="px-3 py-2 text-xs">
                    {it.enabled ? (
                      <span className="rounded-full bg-emerald-500/20 px-2 py-0.5 text-emerald-300">
                        ✓ 启用
                      </span>
                    ) : (
                      <span className="rounded-full bg-zinc-700/40 px-2 py-0.5 text-zinc-400">
                        ○ 停用
                      </span>
                    )}
                  </td>
                  <td className="px-3 py-2">
                    <span
                      className={`rounded-full px-2 py-0.5 text-[10px] font-mono uppercase tracking-wider ${statusChipClass("compliance", it.compliance_level)}`}
                      title={COMPLIANCE_LEVEL_HINTS[it.compliance_level]}
                      data-testid={`source-level-${it.id}`}
                    >
                      {it.compliance_level}
                    </span>
                    {it.source_block_reason && (
                      <div
                        className="mt-1 text-[10px] text-muted-foreground"
                        title={it.source_block_reason}
                      >
                        {truncate(it.source_block_reason, 30)}
                      </div>
                    )}
                  </td>
                  <td className="px-3 py-2 font-mono text-xs">
                    <div>{formatShortDate(it.last_compliance_check)}</div>
                    <div className="text-[10px] text-muted-foreground">
                      {formatRelativeTime(it.last_compliance_check)}
                    </div>
                  </td>
                  <td className="px-3 py-2 text-right">
                    <div className="flex justify-end gap-1">
                      <a
                        href={auditDeepLink("source", it.id)}
                        className="rounded-md border border-border bg-background px-2 py-0.5 text-xs hover:bg-muted"
                        data-testid={`source-audit-${it.id}`}
                      >
                        📋
                      </a>
                      <button
                        type="button"
                        onClick={() => setPatchTarget(it)}
                        disabled={loading}
                        className="rounded-md border border-accent/40 bg-accent/10 px-2 py-0.5 text-xs text-accent hover:bg-accent/20 disabled:opacity-50"
                        data-testid={`source-patch-${it.id}`}
                      >
                        调合规
                      </button>
                    </div>
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </section>

      {patchTarget && (
        <PatchComplianceModal
          target={patchTarget}
          onClose={() => setPatchTarget(null)}
          onPatched={onPatched}
          onError={(msg) => showToast("err", msg)}
        />
      )}

      {toast && (
        <div
          role="status"
          className={
            "fixed bottom-6 left-1/2 -translate-x-1/2 rounded-md px-4 py-2 text-sm shadow-lg " +
            (toast.kind === "ok"
              ? "bg-emerald-600 text-white"
              : "bg-red-600 text-white")
          }
          data-testid="sources-toast"
        >
          {toast.text}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Patch Compliance modal — single-select chip group + 2 optional fields.
// ---------------------------------------------------------------------------
function PatchComplianceModal({
  target,
  onClose,
  onPatched,
  onError,
}: {
  target: Source;
  onClose: () => void;
  onPatched: (resp: Source) => void;
  onError: (msg: string) => void;
}) {
  const [level, setLevel] =
    useState<ComplianceLevelValue>(target.compliance_level);
  const [retention, setRetention] = useState<string>(
    target.retention_policy ?? "",
  );
  const [blockReason, setBlockReason] = useState<string>(
    target.source_block_reason ?? "",
  );
  const [busy, setBusy] = useState(false);

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
      data-testid="patch-modal"
      role="dialog"
      aria-modal="true"
    >
      <form
        onSubmit={async (e) => {
          e.preventDefault();
          if (busy) return;
          setBusy(true);
          try {
            const resp = await updateSourceCompliance(target.id, {
              compliance_level: level,
              retention_policy: retention.trim() || null,
              source_block_reason: blockReason.trim() || null,
            });
            onPatched(resp);
          } catch (err) {
            onError((err as Error).message);
          } finally {
            setBusy(false);
          }
        }}
        className="w-full max-w-md rounded-xl border border-border bg-background p-6 shadow-2xl"
        data-testid="patch-form"
      >
        <header className="mb-4">
          <h2 className="text-lg font-semibold">调整合规 — {target.name}</h2>
          <p className="mt-1 text-xs text-muted-foreground">
            当前 {target.compliance_level} · last_check{" "}
            {formatShortDate(target.last_compliance_check)}
          </p>
        </header>
        <div className="space-y-3 text-sm">
          <div>
            <label className="text-xs text-muted-foreground">
              compliance_level
            </label>
            <div className="mt-1 flex flex-wrap gap-2">
              {COMPLIANCE_LEVELS.map((lv) => (
                <button
                  key={lv}
                  type="button"
                  onClick={() => setLevel(lv)}
                  className={`rounded-full border px-3 py-1 text-xs transition ${
                    level === lv
                      ? "border-accent bg-accent text-accent-foreground"
                      : "border-border bg-card/40 text-muted-foreground hover:bg-muted"
                  }`}
                  data-testid={`patch-level-${lv}`}
                  data-active={level === lv ? "true" : "false"}
                >
                  {lv}
                </button>
              ))}
            </div>
            <p
              className="mt-1 text-[10px] text-muted-foreground"
              data-testid="patch-level-hint"
            >
              {COMPLIANCE_LEVEL_HINTS[level]}
            </p>
          </div>
          <div>
            <label className="text-xs text-muted-foreground">
              retention_policy (optional)
            </label>
            <input
              type="text"
              value={retention}
              onChange={(e) => setRetention(e.target.value)}
              placeholder="例如 30d"
              maxLength={64}
              className="mt-1 w-full rounded-md border border-border bg-background px-2 py-1.5"
              data-testid="patch-retention"
            />
          </div>
          <div>
            <label className="text-xs text-muted-foreground">
              source_block_reason (optional)
            </label>
            <input
              type="text"
              value={blockReason}
              onChange={(e) => setBlockReason(e.target.value)}
              placeholder="例如 付费墙 / 登录必须"
              maxLength={64}
              className="mt-1 w-full rounded-md border border-border bg-background px-2 py-1.5"
              data-testid="patch-block-reason"
            />
          </div>
        </div>
        <footer className="mt-6 flex justify-end gap-2">
          <button
            type="button"
            onClick={onClose}
            disabled={busy}
            className="rounded-md border border-border bg-card/40 px-3 py-1.5 text-sm hover:bg-muted disabled:opacity-50"
            data-testid="patch-cancel"
          >
            取消
          </button>
          <button
            type="submit"
            disabled={busy}
            className="rounded-md bg-accent px-3 py-1.5 text-sm font-medium text-accent-foreground hover:opacity-90 disabled:opacity-50"
            data-testid="patch-submit"
          >
            {busy ? "处理中…" : "确认"}
          </button>
        </footer>
      </form>
    </div>
  );
}

// ---------------------------------------------------------------------------
function StatCard({
  label,
  value,
  testid,
}: {
  label: string;
  value: string;
  testid: string;
}) {
  return (
    <div
      className="rounded-xl border border-border bg-card/40 p-4"
      data-testid={testid}
    >
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className="mt-2 text-2xl font-semibold">{value}</div>
    </div>
  );
}

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
