"use client";

/**
 * Phase 23 — sole-operator IM delivery history viewer.
 *
 * Mirrors the AuditLogsPanel pattern: server component seeds the initial
 * page from URL searchParams; client panel owns filter state + re-fetch.
 * URL is the source of truth so back/forward + reload work.
 *
 * Filters:
 *   * `kind`    — payload.kind discriminator (activation_code_issued,
 *                  subscription_renewal_reminder, …)
 *   * `channel` — feishu / telegram
 *   * `since`   — ISO lower bound on created_at
 *
 * Each row can expand an inline payload drawer so the operator can
 * inspect the raw JSON without leaving the table. The deep-link button
 * (when present) jumps to the related resource (activation code,
 * subscription list) — single click to debug "did this user actually
 * get their code?".
 */

import { useCallback, useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";

import { fetchNotifications } from "@/lib/api";
import {
  NOTIFICATION_CHANNEL_CHIP,
  NOTIFICATION_KIND_CHIP,
  NOTIFICATION_KIND_LABELS,
  notificationDeepLink,
} from "@/lib/adminCrud";
import { formatRelativeTime } from "@/lib/utils";
import type { NotificationItem, NotificationListResponse } from "@/types";

const PAGE_SIZE = 50;

const KIND_OPTIONS = [
  "activation_code_issued",
  "activation_code_resend",
  "subscription_renewal_reminder",
] as const;
const CHANNEL_OPTIONS = ["feishu", "telegram"] as const;

export interface MessagesFilters {
  kind?: string;
  channel?: string;
  since?: string;
  limit?: number;
  offset?: number;
}

export interface MessagesPanelProps {
  initial: NotificationListResponse;
  initialFilters: MessagesFilters;
}

interface FormState {
  kind: string;
  channel: string;
  since: string;
}

function filterToForm(filters: MessagesFilters): FormState {
  return {
    kind: filters.kind ?? "",
    channel: filters.channel ?? "",
    since: filters.since ?? "",
  };
}

function formToFilters(form: FormState, offset: number): MessagesFilters {
  const set = (v: string): string | undefined =>
    v && v.length > 0 ? v : undefined;
  return {
    kind: set(form.kind),
    channel: set(form.channel),
    since: set(form.since),
    limit: PAGE_SIZE,
    offset,
  };
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------
export function MessagesPanel({ initial, initialFilters }: MessagesPanelProps) {
  const router = useRouter();
  const searchParams = useSearchParams();

  const [data, setData] = useState<NotificationListResponse>(initial);
  const [form, setForm] = useState<FormState>(filterToForm(initialFilters));
  const [offset, setOffset] = useState<number>(initial.offset);
  const [loading, setLoading] = useState(false);
  const [expandedId, setExpandedId] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);

  const total = data.total;
  const items = data.items;
  const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const pageIndex = Math.floor(offset / PAGE_SIZE) + 1;

  // Stat counts — derived from current page items. The backend doesn't
  // expose aggregate counters; the 4 cards mirror the dashboard's
  // approach (recent rows for context, total for the headline).
  const sentCount = items.filter((n) => !n.error).length;
  const failedCount = items.filter((n) => Boolean(n.error)).length;
  const codeIssuedCount = items.filter(
    (n) => n.kind === "activation_code_issued",
  ).length;
  const reminderCount = items.filter(
    (n) => n.kind === "subscription_renewal_reminder",
  ).length;

  const pushUrl = useCallback(
    (nextForm: FormState, nextOffset: number) => {
      const params = new URLSearchParams(searchParams.toString());
      const setOrDel = (k: string, v: string | undefined) => {
        if (v && v.length > 0) params.set(k, v);
        else params.delete(k);
      };
      setOrDel("kind", nextForm.kind);
      setOrDel("channel", nextForm.channel);
      setOrDel("since", nextForm.since);
      if (nextOffset > 0) params.set("offset", String(nextOffset));
      else params.delete("offset");
      const qs = params.toString();
      router.replace(qs ? `/admin/messages?${qs}` : "/admin/messages", {
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
        const resp = await fetchNotifications(filters);
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
    const blank: FormState = { kind: "", channel: "", since: "" };
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

  const setField = (key: keyof FormState, value: string) =>
    setForm((prev) => ({ ...prev, [key]: value }));

  // Expose form + offset on data-* attrs for tests + external observers.
  useEffect(() => {
    if (typeof document === "undefined") return;
    const root = document.querySelector("[data-testid='messages-panel']");
    if (!root) return;
    root.setAttribute("data-form-kind", form.kind);
    root.setAttribute("data-form-channel", form.channel);
    root.setAttribute("data-form-since", form.since);
    root.setAttribute("data-offset", String(offset));
  }, [form, offset]);

  return (
    <div className="space-y-6" data-testid="messages-panel" data-total={total}>
      {/* Stat cards -------------------------------------------------------*/}
      <section
        className="grid gap-3 md:grid-cols-4"
        data-testid="messages-stats"
      >
        <StatCard
          label="全部消息"
          value={total}
          testid="stat-total"
        />
        <StatCard
          label="激活码发放"
          value={codeIssuedCount}
          tone="blue"
          testid="stat-activation"
        />
        <StatCard
          label="续期提醒"
          value={reminderCount}
          tone="amber"
          testid="stat-reminder"
        />
        <StatCard
          label="失败 (本页)"
          value={failedCount}
          tone={failedCount > 0 ? "red" : "muted"}
          testid="stat-failed"
        />
      </section>

      {/* Filter bar -------------------------------------------------------*/}
      <section
        className="rounded-xl border border-border bg-card/40 p-4"
        data-testid="messages-filter-bar"
      >
        <div className="grid gap-3 md:grid-cols-2">
          <div>
            <label className="text-xs uppercase tracking-wide text-muted-foreground">
              kind
            </label>
            <div className="mt-1 flex flex-wrap gap-2">
              <ChipFilter
                label="全部"
                value=""
                current={form.kind}
                onClick={(v) => applyFilters({ ...form, kind: v })}
                testid="chip-kind-all"
              />
              {KIND_OPTIONS.map((opt) => (
                <ChipFilter
                  key={opt}
                  label={NOTIFICATION_KIND_LABELS[opt] ?? opt}
                  value={opt}
                  current={form.kind}
                  onClick={(v) => applyFilters({ ...form, kind: v })}
                  testid={`chip-kind-${opt}`}
                />
              ))}
            </div>
          </div>
          <div>
            <label className="text-xs uppercase tracking-wide text-muted-foreground">
              channel
            </label>
            <div className="mt-1 flex flex-wrap gap-2">
              <ChipFilter
                label="全部"
                value=""
                current={form.channel}
                onClick={(v) => applyFilters({ ...form, channel: v })}
                testid="chip-channel-all"
              />
              {CHANNEL_OPTIONS.map((opt) => (
                <ChipFilter
                  key={opt}
                  label={opt}
                  value={opt}
                  current={form.channel}
                  onClick={(v) => applyFilters({ ...form, channel: v })}
                  testid={`chip-channel-${opt}`}
                />
              ))}
            </div>
          </div>
        </div>
        <div className="mt-3 grid gap-3 md:grid-cols-3">
          <div>
            <label className="text-xs uppercase tracking-wide text-muted-foreground">
              since (ISO)
            </label>
            <input
              type="text"
              value={form.since}
              onChange={(e) => setField("since", e.target.value)}
              placeholder="2026-08-01T00:00:00Z"
              className="mt-1 w-full rounded-md border border-border bg-background px-2 py-1.5 text-sm"
              data-testid="input-since"
            />
          </div>
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
          <div className="flex items-end justify-end text-xs text-muted-foreground">
            本页 {items.length} 条 · 已送达 {sentCount} · 失败 {failedCount}
          </div>
        </div>
      </section>

      {error && (
        <p
          className="rounded-md border border-danger/40 bg-danger/10 p-3 text-sm"
          data-testid="messages-error"
        >
          {error}
        </p>
      )}

      {/* Table ------------------------------------------------------------*/}
      <section
        className="overflow-x-auto rounded-xl border border-border bg-card/40"
        data-testid="messages-table-wrap"
      >
        <table className="min-w-full text-sm">
          <thead className="bg-muted/40 text-xs uppercase tracking-wide text-muted-foreground">
            <tr>
              <th className="px-3 py-2 text-left">时间</th>
              <th className="px-3 py-2 text-left">channel</th>
              <th className="px-3 py-2 text-left">kind</th>
              <th className="px-3 py-2 text-left">payload 摘要</th>
              <th className="px-3 py-2 text-left">状态</th>
              <th className="px-3 py-2 text-right" />
            </tr>
          </thead>
          <tbody>
            {items.length === 0 ? (
              <tr>
                <td
                  colSpan={6}
                  className="px-3 py-8 text-center text-muted-foreground"
                  data-testid="messages-empty"
                >
                  暂无消息 — 调整筛选条件或等待后台活动。
                </td>
              </tr>
            ) : (
              items.map((it) => (
                <MessageRow
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
        data-testid="messages-pagination"
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
// Row sub-component
// ---------------------------------------------------------------------------
function MessageRow({
  item,
  expanded,
  onToggle,
}: {
  item: NotificationItem;
  expanded: boolean;
  onToggle: () => void;
}) {
  const kind = item.kind ?? "—";
  const kindLabel = NOTIFICATION_KIND_LABELS[kind] ?? kind;
  const kindChip = NOTIFICATION_KIND_CHIP[kind] ?? "bg-muted text-muted-foreground";
  const channelChip =
    NOTIFICATION_CHANNEL_CHIP[item.channel] ??
    "bg-muted text-muted-foreground";
  const link = notificationDeepLink(item.payload ?? {});
  const summary = _summaryFromPayload(item);

  return (
    <>
      <tr
        className={`border-t border-border hover:bg-muted/30 ${
          item.failed ? "bg-red-500/5" : ""
        }`}
        data-testid={`messages-row-${item.id}`}
      >
        <td className="px-3 py-2 font-mono text-xs">
          <div className="text-foreground">
            {_formatDate(item.created_at)}
          </div>
          <div className="text-[10px] text-muted-foreground">
            {formatRelativeTime(item.created_at)}
          </div>
        </td>
        <td className="px-3 py-2">
          <span
            className={`rounded-full px-2 py-0.5 text-[10px] font-mono uppercase tracking-wider ${channelChip}`}
            data-testid={`messages-channel-${item.id}`}
          >
            {item.channel}
          </span>
        </td>
        <td className="px-3 py-2">
          <span
            className={`rounded-full px-2 py-0.5 text-[10px] uppercase tracking-wider ${kindChip}`}
            data-testid={`messages-kind-${item.id}`}
          >
            {kindLabel}
          </span>
        </td>
        <td className="px-3 py-2 font-mono text-xs">{summary}</td>
        <td className="px-3 py-2">
          {item.failed ? (
            <span
              className="rounded-full bg-red-500/20 px-2 py-0.5 text-[10px] uppercase tracking-wider text-red-300"
              data-testid={`messages-status-${item.id}`}
            >
              FAILED
            </span>
          ) : (
            <span
              className="rounded-full bg-emerald-500/20 px-2 py-0.5 text-[10px] uppercase tracking-wider text-emerald-300"
              data-testid={`messages-status-${item.id}`}
            >
              sent
            </span>
          )}
        </td>
        <td className="px-3 py-2 text-right">
          <div className="flex justify-end gap-1">
            {link && (
              <a
                href={link}
                className="rounded-md border border-border bg-background px-2 py-0.5 text-xs text-accent hover:bg-muted"
                data-testid={`messages-link-${item.id}`}
              >
                打开资源
              </a>
            )}
            <button
              type="button"
              onClick={onToggle}
              className="rounded-md border border-border bg-background px-2 py-0.5 text-xs hover:bg-muted"
              data-testid={`messages-toggle-${item.id}`}
              aria-label={expanded ? "Hide payload" : "Show payload"}
            >
              {expanded ? "收起" : "···"}
            </button>
          </div>
        </td>
      </tr>
      {expanded && (
        <tr
          className="border-t border-border bg-muted/30"
          data-testid={`messages-meta-${item.id}`}
        >
          <td colSpan={6} className="px-3 py-3">
            <pre
              className="max-h-64 overflow-auto rounded-md bg-background p-3 text-[11px] leading-relaxed"
              data-testid={`messages-meta-pre-${item.id}`}
            >
              {JSON.stringify(item.payload ?? {}, null, 2)}
            </pre>
            {item.error && (
              <p
                className="mt-2 text-xs text-red-300"
                data-testid={`messages-error-text-${item.id}`}
              >
                error: {item.error}
              </p>
            )}
          </td>
        </tr>
      )}
    </>
  );
}

// ---------------------------------------------------------------------------
// UI atoms
// ---------------------------------------------------------------------------
function StatCard({
  label,
  value,
  tone = "muted",
  testid,
}: {
  label: string;
  value: number;
  tone?: "muted" | "blue" | "amber" | "red";
  testid: string;
}) {
  const toneClass =
    tone === "blue"
      ? "text-blue-300"
      : tone === "amber"
        ? "text-amber-300"
        : tone === "red"
          ? "text-red-300"
          : "text-foreground";
  return (
    <div
      className="rounded-xl border border-border bg-card/40 p-4"
      data-testid={testid}
    >
      <p className="text-xs uppercase tracking-wide text-muted-foreground">
        {label}
      </p>
      <p className={`mt-1 font-mono text-2xl ${toneClass}`}>{value}</p>
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

// ---------------------------------------------------------------------------
// Tiny pure helpers
// ---------------------------------------------------------------------------
function _formatDate(iso: string | null): string {
  if (!iso) return "—";
  try {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return iso;
    const pad = (n: number) => String(n).padStart(2, "0");
    return `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(
      d.getUTCDate(),
    )} ${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())} UTC`;
  } catch {
    return iso;
  }
}

function _summaryFromPayload(item: NotificationItem): string {
  const p = item.payload ?? {};
  if (item.kind === "activation_code_issued" || item.kind === "activation_code_resend") {
    const codeId = p.activation_code_id ?? "—";
    const plan = p.plan ?? "—";
    const preview = p.code_preview ?? "";
    return `#${codeId} · ${plan}${preview ? ` · ${preview}` : ""}`;
  }
  if (item.kind === "subscription_renewal_reminder") {
    const subId = p.subscription_id ?? "—";
    const plan = p.plan ?? "—";
    const days = p.days_until;
    return `#${subId} · ${plan}${
      typeof days === "number" ? ` · ${days}d until expiry` : ""
    }`;
  }
  // Fallback — stringify a couple of payload keys so the table isn't
  // a wall of "{...}".
  const keys = Object.keys(p).slice(0, 3);
  return keys.map((k) => `${k}=${String(p[k])}`).join(" · ") || "—";
}
