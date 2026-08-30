"use client";

/**
 * Phase 22 — sole-operator subscription console.
 *
 * Server seeds `initialItems` + `initialFilters`; the client owns the
 * filter URL state and re-fetches on filter change. Extend / Cancel
 * mutations open modals, then refresh the table. Mirrors
 * `ActivationCodesPanel`'s layout — same stat header, same filter
 * bar, same audit deep link per row.
 */

import { useCallback, useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";

import {
  cancelSubscription,
  extendSubscription,
  fetchSubscriptions,
  type SubscriptionListParams,
} from "@/lib/api";
import {
  PLAN_CODES,
  SUBSCRIPTION_STATUSES,
  auditDeepLink,
  statusChipClass,
} from "@/lib/adminCrud";
import { formatShortDate } from "@/lib/contentOpportunities";
import { formatRelativeTime } from "@/lib/utils";
import type {
  Subscription,
  SubscriptionListResponse,
  SubscriptionStatusValue,
} from "@/types";

const STATUS_LABELS: Record<SubscriptionStatusValue, string> = {
  active: "活跃",
  expired: "已过期",
  suspended: "已暂停",
  cancelled: "已取消",
};

export interface SubscriptionsPanelProps {
  initial: SubscriptionListResponse;
  initialFilters: {
    status?: string;
    plan?: string;
    feishu_open_id?: string;
  };
}

interface FormState {
  status: string;
  plan: string;
  feishu_open_id: string;
}

function filterToForm(f: SubscriptionsPanelProps["initialFilters"]): FormState {
  return {
    status: f.status ?? "",
    plan: f.plan ?? "",
    feishu_open_id: f.feishu_open_id ?? "",
  };
}

function formToParams(form: FormState): SubscriptionListParams {
  return {
    status: form.status || undefined,
    plan: form.plan || undefined,
    limit: 1000,
  };
}

export function SubscriptionsPanel({
  initial,
  initialFilters,
}: SubscriptionsPanelProps) {
  const router = useRouter();
  const searchParams = useSearchParams();

  const [items, setItems] = useState<Subscription[]>(initial.items);
  const [count, setCount] = useState<number>(initial.count);
  const [form, setForm] = useState<FormState>(filterToForm(initialFilters));
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [toast, setToast] = useState<{ kind: "ok" | "err"; text: string } | null>(
    null,
  );

  const [extendTarget, setExtendTarget] = useState<Subscription | null>(null);
  const [cancelTarget, setCancelTarget] = useState<Subscription | null>(null);

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
      setOrDel("status", next.status);
      setOrDel("plan", next.plan);
      setOrDel("feishu_open_id", next.feishu_open_id);
      const qs = params.toString();
      router.replace(
        qs ? `/admin/subscriptions?${qs}` : "/admin/subscriptions",
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
        const base = await fetchSubscriptions(formToParams(next));
        let filtered = base.items;
        if (next.feishu_open_id) {
          const needle = next.feishu_open_id.toLowerCase();
          filtered = filtered.filter(
            (it) =>
              (it.feishu_open_id ?? "").toLowerCase().includes(needle),
          );
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
    const blank: FormState = { status: "", plan: "", feishu_open_id: "" };
    setForm(blank);
    pushUrl(blank);
    void refresh(blank);
  }, [pushUrl, refresh]);

  const onExtended = useCallback(
    (resp: Subscription) => {
      setExtendTarget(null);
      showToast(
        "ok",
        `✅ 订阅 #${resp.id} 已延期,新到期:${formatShortDate(resp.expires_at)}`,
      );
      void refresh(form);
    },
    [refresh, form, showToast],
  );

  const onCancelled = useCallback(
    (resp: Subscription) => {
      setCancelTarget(null);
      showToast("ok", `✅ 订阅 #${resp.id} 已取消`);
      void refresh(form);
    },
    [refresh, form, showToast],
  );

  const counts = items.reduce<Record<SubscriptionStatusValue, number>>(
    (acc, it) => {
      acc[it.status] = (acc[it.status] ?? 0) + 1;
      return acc;
    },
    { active: 0, expired: 0, suspended: 0, cancelled: 0 },
  );

  useEffect(() => {
    if (typeof document === "undefined") return;
    const root = document.querySelector(
      "[data-testid='subscriptions-panel']",
    );
    if (!root) return;
    root.setAttribute("data-form-status", form.status);
    root.setAttribute("data-form-plan", form.plan);
    root.setAttribute("data-form-feishu_open_id", form.feishu_open_id);
  }, [form]);

  return (
    <div className="space-y-8" data-testid="subscriptions-panel" data-count={count}>
      {/* Stat cards -------------------------------------------------------*/}
      <section
        className="grid gap-4 md:grid-cols-5"
        data-testid="subscriptions-stats"
      >
        <StatCard label="总订阅" value={String(count)} testid="stat-total" />
        <StatCard label={STATUS_LABELS.active} value={String(counts.active)} testid="stat-active" />
        <StatCard label={STATUS_LABELS.expired} value={String(counts.expired)} testid="stat-expired" />
        <StatCard label={STATUS_LABELS.suspended} value={String(counts.suspended)} testid="stat-suspended" />
        <StatCard label={STATUS_LABELS.cancelled} value={String(counts.cancelled)} testid="stat-cancelled" />
      </section>

      {/* Filters ---------------------------------------------------------*/}
      <section
        className="rounded-xl border border-border bg-card/40 p-4"
        data-testid="subscriptions-filter-bar"
      >
        <div className="grid gap-3 md:grid-cols-3">
          <div>
            <label className="text-xs uppercase tracking-wide text-muted-foreground">
              status
            </label>
            <div className="mt-1 flex flex-wrap gap-2">
              <ChipFilter
                label="全部"
                value=""
                current={form.status}
                onClick={(v) => applyFilters({ ...form, status: v })}
                testid="chip-status-all"
              />
              {SUBSCRIPTION_STATUSES.map((opt) => (
                <ChipFilter
                  key={opt}
                  label={STATUS_LABELS[opt]}
                  value={opt}
                  current={form.status}
                  onClick={(v) => applyFilters({ ...form, status: v })}
                  testid={`chip-status-${opt}`}
                />
              ))}
            </div>
          </div>
          <div>
            <label className="text-xs uppercase tracking-wide text-muted-foreground">
              plan
            </label>
            <select
              value={form.plan}
              onChange={(e) => applyFilters({ ...form, plan: e.target.value })}
              className="mt-1 w-full rounded-md border border-border bg-background px-2 py-1.5 text-sm"
              data-testid="select-plan"
            >
              <option value="">(all)</option>
              {PLAN_CODES.map((p) => (
                <option key={p} value={p}>
                  {p}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label className="text-xs uppercase tracking-wide text-muted-foreground">
              feishu_open_id
            </label>
            <input
              type="text"
              value={form.feishu_open_id}
              onChange={(e) =>
                setForm({ ...form, feishu_open_id: e.target.value })
              }
              placeholder="例如 ou_xyz"
              className="mt-1 w-full rounded-md border border-border bg-background px-2 py-1.5 text-sm"
              data-testid="input-feishu_open_id"
            />
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
          data-testid="subscriptions-error"
        >
          {error}
        </p>
      )}

      {/* Table ------------------------------------------------------------*/}
      <section
        className="overflow-x-auto rounded-xl border border-border bg-card/40"
        data-testid="subscriptions-table-wrap"
      >
        <table className="min-w-full text-sm">
          <thead className="bg-muted/40 text-xs uppercase tracking-wide text-muted-foreground">
            <tr>
              <th className="px-3 py-2 text-left">ID</th>
              <th className="px-3 py-2 text-left">feishu_open_id</th>
              <th className="px-3 py-2 text-left">plan</th>
              <th className="px-3 py-2 text-left">status</th>
              <th className="px-3 py-2 text-left">expires</th>
              <th className="px-3 py-2 text-left">source</th>
              <th className="px-3 py-2 text-right">操作</th>
            </tr>
          </thead>
          <tbody>
            {items.length === 0 ? (
              <tr>
                <td
                  colSpan={7}
                  className="px-3 py-8 text-center text-muted-foreground"
                  data-testid="subscriptions-empty"
                >
                  当前过滤下没有订阅记录。
                </td>
              </tr>
            ) : (
              items.map((it) => (
                <tr
                  key={it.id}
                  className="border-t border-border hover:bg-muted/30"
                  data-testid={`sub-row-${it.id}`}
                >
                  <td className="px-3 py-2 font-mono text-xs">#{it.id}</td>
                  <td className="px-3 py-2 font-mono text-[10px] text-muted-foreground">
                    {it.feishu_open_id ?? "—"}
                  </td>
                  <td className="px-3 py-2 font-mono text-xs">{it.plan}</td>
                  <td className="px-3 py-2">
                    <span
                      className={`rounded-full px-2 py-0.5 text-[10px] font-mono uppercase tracking-wider ${statusChipClass("subscription", it.status)}`}
                      data-testid={`sub-status-${it.id}`}
                    >
                      {STATUS_LABELS[it.status]}
                    </span>
                  </td>
                  <td className="px-3 py-2 font-mono text-xs">
                    <div>{formatShortDate(it.expires_at)}</div>
                    <div className="text-[10px] text-muted-foreground">
                      {formatRelativeTime(it.expires_at)}
                    </div>
                  </td>
                  <td className="px-3 py-2 font-mono text-[10px] text-muted-foreground">
                    {it.source_channel ?? "—"}
                  </td>
                  <td className="px-3 py-2 text-right">
                    <div className="flex justify-end gap-1">
                      <a
                        href={auditDeepLink("subscription", it.id)}
                        className="rounded-md border border-border bg-background px-2 py-0.5 text-xs hover:bg-muted"
                        data-testid={`sub-audit-${it.id}`}
                      >
                        📋
                      </a>
                      <button
                        type="button"
                        onClick={() => setExtendTarget(it)}
                        disabled={loading}
                        className="rounded-md border border-accent/40 bg-accent/10 px-2 py-0.5 text-xs text-accent hover:bg-accent/20 disabled:opacity-50"
                        data-testid={`sub-extend-${it.id}`}
                      >
                        +N 天
                      </button>
                      {it.status !== "cancelled" && (
                        <button
                          type="button"
                          onClick={() => setCancelTarget(it)}
                          disabled={loading}
                          className="rounded-md border border-danger/40 bg-danger/10 px-2 py-0.5 text-xs text-danger hover:bg-danger/20 disabled:opacity-50"
                          data-testid={`sub-cancel-${it.id}`}
                        >
                          取消
                        </button>
                      )}
                    </div>
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </section>

      {extendTarget && (
        <ExtendSubscriptionModal
          target={extendTarget}
          onClose={() => setExtendTarget(null)}
          onExtended={onExtended}
          onError={(msg) => showToast("err", msg)}
        />
      )}

      {cancelTarget && (
        <ConfirmCancelModal
          target={cancelTarget}
          onClose={() => setCancelTarget(null)}
          onConfirm={async () => {
            try {
              const resp = await cancelSubscription(cancelTarget.id);
              onCancelled(resp);
            } catch (e) {
              showToast("err", (e as Error).message);
            }
          }}
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
          data-testid="subscriptions-toast"
        >
          {toast.text}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Modals
// ---------------------------------------------------------------------------
function ExtendSubscriptionModal({
  target,
  onClose,
  onExtended,
  onError,
}: {
  target: Subscription;
  onClose: () => void;
  onExtended: (resp: Subscription) => void;
  onError: (msg: string) => void;
}) {
  const [days, setDays] = useState<string>("30");
  const [busy, setBusy] = useState(false);
  const daysNum = Number.parseInt(days, 10);
  const submitDisabled =
    busy || !Number.isFinite(daysNum) || daysNum < 1 || daysNum > 3650;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
      data-testid="extend-modal"
      role="dialog"
      aria-modal="true"
    >
      <form
        onSubmit={async (e) => {
          e.preventDefault();
          if (submitDisabled) return;
          setBusy(true);
          try {
            const resp = await extendSubscription(target.id, {
              days: daysNum,
            });
            onExtended(resp);
          } catch (err) {
            onError((err as Error).message);
          } finally {
            setBusy(false);
          }
        }}
        className="w-full max-w-md rounded-xl border border-border bg-background p-6 shadow-2xl"
        data-testid="extend-form"
      >
        <header className="mb-4">
          <h2 className="text-lg font-semibold">
            延期订阅 #{target.id}
          </h2>
          <p className="mt-1 text-xs text-muted-foreground">
            当前到期:{formatShortDate(target.expires_at)} · plan={target.plan} ·
            status={target.status}
          </p>
          <p className="mt-1 text-xs text-muted-foreground">
            后端会取 (now, expires_at) 较晚者作为基准 +N 天,并把 status 设为 active。
          </p>
        </header>
        <div className="text-sm">
          <label className="text-xs text-muted-foreground">days (1..3650)</label>
          <input
            type="number"
            min={1}
            max={3650}
            value={days}
            onChange={(e) => setDays(e.target.value)}
            className="mt-1 w-full rounded-md border border-border bg-background px-2 py-1.5"
            data-testid="extend-days"
          />
        </div>
        <footer className="mt-6 flex justify-end gap-2">
          <button
            type="button"
            onClick={onClose}
            disabled={busy}
            className="rounded-md border border-border bg-card/40 px-3 py-1.5 text-sm hover:bg-muted disabled:opacity-50"
            data-testid="extend-cancel"
          >
            取消
          </button>
          <button
            type="submit"
            disabled={submitDisabled}
            className="rounded-md bg-accent px-3 py-1.5 text-sm font-medium text-accent-foreground hover:opacity-90 disabled:opacity-50"
            data-testid="extend-submit"
          >
            {busy ? "处理中…" : `延期 ${daysNum > 0 ? daysNum : ""} 天`}
          </button>
        </footer>
      </form>
    </div>
  );
}

function ConfirmCancelModal({
  target,
  onClose,
  onConfirm,
}: {
  target: Subscription;
  onClose: () => void;
  onConfirm: () => Promise<void>;
}) {
  const [busy, setBusy] = useState(false);
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
      data-testid="cancel-modal"
      role="dialog"
      aria-modal="true"
    >
      <form
        onSubmit={async (e) => {
          e.preventDefault();
          if (busy) return;
          setBusy(true);
          await onConfirm();
          setBusy(false);
        }}
        className="w-full max-w-md rounded-xl border border-border bg-background p-6 shadow-2xl"
        data-testid="cancel-form"
      >
        <header className="mb-4">
          <h2 className="text-lg font-semibold">取消订阅 #{target.id}?</h2>
          <p className="mt-1 text-xs text-muted-foreground">
            用户: {target.feishu_open_id ?? "—"} · plan={target.plan}
          </p>
          <p className="mt-1 text-xs text-muted-foreground">
            取消后 status=cancelled,后端会写一行 audit。
          </p>
        </header>
        <footer className="mt-6 flex justify-end gap-2">
          <button
            type="button"
            onClick={onClose}
            disabled={busy}
            className="rounded-md border border-border bg-card/40 px-3 py-1.5 text-sm hover:bg-muted disabled:opacity-50"
            data-testid="cancel-modal-cancel"
          >
            取消
          </button>
          <button
            type="submit"
            disabled={busy}
            className="rounded-md bg-danger px-3 py-1.5 text-sm font-medium text-white hover:opacity-90 disabled:opacity-50"
            data-testid="cancel-submit"
          >
            {busy ? "处理中…" : "确认取消"}
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
