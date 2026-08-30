"use client";

/**
 * Phase 22 — sole-operator activation code console.
 *
 * Server seeds `initialItems` + `initialFilters`; the client owns the
 * filter URL state, re-fetches on filter change, and opens mutating
 * modals (Issue / Revoke). The plaintext issued code is shown ONCE —
 * a toast + a 30-second banner both carry it so the operator can copy
 * it before it disappears.
 *
 * Mirrors the URL-sync pattern from `AuditLogsPanel` and the
 * stat-card header from `ContentOpportunitiesPanel`.
 */

import { useCallback, useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";

import {
  fetchActivationCodes,
  issueActivationCode,
  resendActivationCode,
  revokeActivationCode,
  type ActivationListParams,
} from "@/lib/api";
import {
  ACTIVATION_STATUSES,
  PLAN_CODES,
  auditDeepLink,
  statusChipClass,
} from "@/lib/adminCrud";
import { formatShortDate } from "@/lib/contentOpportunities";
import { formatRelativeTime } from "@/lib/utils";
import type {
  ActivationCode,
  ActivationIssueResponse,
  ActivationStatusValue,
  ActivationListResponse,
} from "@/types";

const STATUS_LABELS: Record<ActivationStatusValue, string> = {
  unused: "未用",
  active: "已激活",
  expired: "已过期",
  revoked: "已撤销",
};

export interface ActivationCodesPanelProps {
  initial: ActivationListResponse;
  initialFilters: { status?: string; plan?: string; id?: string };
}

interface FormState {
  status: string;
  plan: string;
  id: string;
}

function filterToForm(f: ActivationCodesPanelProps["initialFilters"]): FormState {
  return {
    status: f.status ?? "",
    plan: f.plan ?? "",
    id: f.id ?? "",
  };
}

function formToParams(form: FormState): ActivationListParams {
  return {
    status: form.status || undefined,
    plan: form.plan || undefined,
    limit: 1000,
  };
}

export function ActivationCodesPanel({
  initial,
  initialFilters,
}: ActivationCodesPanelProps) {
  const router = useRouter();
  const searchParams = useSearchParams();

  const [items, setItems] = useState<ActivationCode[]>(initial.items);
  const [count, setCount] = useState<number>(initial.count);
  const [form, setForm] = useState<FormState>(filterToForm(initialFilters));
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [toast, setToast] = useState<{ kind: "ok" | "err"; text: string } | null>(
    null,
  );
  const [bannerCode, setBannerCode] = useState<{
    code: string;
    plan: string;
    expires_at: string | null;
  } | null>(null);

  const [issueOpen, setIssueOpen] = useState(false);
  const [revokeTarget, setRevokeTarget] = useState<ActivationCode | null>(null);
  // Phase 23 — resend modal target. Resend only works for codes that
  // are still redeemable (`unused` or `active`); backend rejects others
  // with 409. The modal asks for an `open_id` since plaintext recovery
  // is impossible — we can only IM a "please contact us" hint card.
  const [resendTarget, setResendTarget] = useState<ActivationCode | null>(null);

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
      setOrDel("id", next.id);
      const qs = params.toString();
      router.replace(qs ? `/admin/activation?${qs}` : "/admin/activation", {
        scroll: false,
      });
    },
    [router, searchParams],
  );

  const refresh = useCallback(
    async (next: FormState) => {
      setLoading(true);
      setError(null);
      try {
        // Server-side filters: status + plan only. The `id` text input
        // is a post-filter because the backend doesn't expose id-based
        // query.
        const base = await fetchActivationCodes(formToParams(next));
        let filteredItems = base.items;
        if (next.id) {
          const n = Number.parseInt(next.id, 10);
          if (Number.isFinite(n)) {
            filteredItems = filteredItems.filter((it) => it.id === n);
          } else {
            filteredItems = [];
          }
        }
        setItems(filteredItems);
        setCount(filteredItems.length);
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
    const blank: FormState = { status: "", plan: "", id: "" };
    setForm(blank);
    pushUrl(blank);
    void refresh(blank);
  }, [pushUrl, refresh]);

  const onIssued = useCallback(
    (resp: ActivationIssueResponse) => {
      setIssueOpen(false);
      setBannerCode({
        code: resp.code,
        plan: resp.plan,
        expires_at: resp.expires_at,
      });
      // Phase 23 — surface the auto-IM result in the toast so the
      // operator knows whether the customer received the code.
      const im = resp.im_send;
      if (im?.sent) {
        showToast(
          "ok",
          `✅ 激活码已发放 + 已发飞书消息 (${im.message_id ?? "—"})`,
        );
      } else if (im && !im.sent) {
        // Backend reports the IM failure but the issue itself succeeded —
        // treat the toast as err (red) so the operator's attention is
        // pulled to /admin/messages for the failure row.
        showToast(
          "err",
          `⚠️ 激活码已发放但飞书发送失败: ${im.error ?? "unknown error"}`,
        );
      } else {
        showToast("ok", `✅ 激活码已发放: ${resp.code} (${resp.plan})`);
      }
      window.setTimeout(() => setBannerCode(null), 30_000);
      void refresh(form);
    },
    [refresh, form, showToast],
  );

  const onRevoked = useCallback(
    (_resp: ActivationCode) => {
      setRevokeTarget(null);
      showToast("ok", `✅ 激活码 #${revokeTarget?.id} 已撤销`);
      void refresh(form);
    },
    [refresh, form, revokeTarget, showToast],
  );

  // Counts for the stat cards (computed off the full result set; no
  // filter applied since we always render all rows).
  const counts = items.reduce<Record<ActivationStatusValue, number>>(
    (acc, it) => {
      acc[it.status] = (acc[it.status] ?? 0) + 1;
      return acc;
    },
    { unused: 0, active: 0, expired: 0, revoked: 0 },
  );

  // Expose form state to tests via data-* attrs.
  useEffect(() => {
    if (typeof document === "undefined") return;
    const root = document.querySelector("[data-testid='activation-codes-panel']");
    if (!root) return;
    root.setAttribute("data-form-status", form.status);
    root.setAttribute("data-form-plan", form.plan);
    root.setAttribute("data-form-id", form.id);
  }, [form]);

  return (
    <div className="space-y-8" data-testid="activation-codes-panel" data-count={count}>
      {/* Newly-issued code banner — auto-dismisses after 30s. */}
      {bannerCode && (
        <div
          role="status"
          className="flex items-center justify-between gap-3 rounded-xl border border-success/40 bg-success/10 p-4"
          data-testid="activation-banner"
        >
          <div>
            <div className="text-xs uppercase tracking-wide text-success">
              🆕 激活码已生成 — 复制给用户,此 banner 30s 后消失
            </div>
            <div className="mt-2 font-mono text-2xl font-semibold tracking-wider text-foreground">
              {bannerCode.code}
            </div>
            <div className="mt-1 text-xs text-muted-foreground">
              plan={bannerCode.plan} · expires={bannerCode.expires_at ?? "—"}
            </div>
          </div>
          <button
            type="button"
            onClick={() => {
              void navigator.clipboard.writeText(bannerCode.code);
              showToast("ok", "✅ 已复制到剪贴板");
            }}
            className="rounded-md bg-accent px-3 py-1.5 text-sm font-medium text-accent-foreground hover:opacity-90"
            data-testid="activation-banner-copy"
          >
            📋 复制
          </button>
        </div>
      )}

      {/* Stat cards -------------------------------------------------------*/}
      <section
        className="grid gap-4 md:grid-cols-5"
        data-testid="activation-stats"
      >
        <StatCard label="总码数" value={String(count)} testid="stat-total" />
        <StatCard label={STATUS_LABELS.unused} value={String(counts.unused)} testid="stat-unused" />
        <StatCard label={STATUS_LABELS.active} value={String(counts.active)} testid="stat-active" />
        <StatCard label={STATUS_LABELS.expired} value={String(counts.expired)} testid="stat-expired" />
        <StatCard label={STATUS_LABELS.revoked} value={String(counts.revoked)} testid="stat-revoked" />
      </section>

      {/* Filters ---------------------------------------------------------*/}
      <section
        className="rounded-xl border border-border bg-card/40 p-4"
        data-testid="activation-filter-bar"
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
              {ACTIVATION_STATUSES.map((opt) => (
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
              id 搜索
            </label>
            <input
              type="number"
              value={form.id}
              onChange={(e) => setForm({ ...form, id: e.target.value })}
              placeholder="例如 7"
              className="mt-1 w-full rounded-md border border-border bg-background px-2 py-1.5 text-sm"
              data-testid="input-id"
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
          <button
            type="button"
            onClick={() => setIssueOpen(true)}
            className="ml-auto rounded-md bg-success px-3 py-1.5 text-sm font-medium text-white hover:opacity-90"
            data-testid="btn-issue"
          >
            + 发激活码
          </button>
        </div>
      </section>

      {error && (
        <p
          className="rounded-md border border-danger/40 bg-danger/10 p-3 text-sm"
          data-testid="activation-error"
        >
          {error}
        </p>
      )}

      {/* Table ------------------------------------------------------------*/}
      <section
        className="overflow-x-auto rounded-xl border border-border bg-card/40"
        data-testid="activation-table-wrap"
      >
        <table className="min-w-full text-sm">
          <thead className="bg-muted/40 text-xs uppercase tracking-wide text-muted-foreground">
            <tr>
              <th className="px-3 py-2 text-left">ID</th>
              <th className="px-3 py-2 text-left">plan</th>
              <th className="px-3 py-2 text-left">status</th>
              <th className="px-3 py-2 text-left">bound to</th>
              <th className="px-3 py-2 text-left">expires</th>
              <th className="px-3 py-2 text-left">created</th>
              <th className="px-3 py-2 text-right">操作</th>
            </tr>
          </thead>
          <tbody>
            {items.length === 0 ? (
              <tr>
                <td
                  colSpan={7}
                  className="px-3 py-8 text-center text-muted-foreground"
                  data-testid="activation-empty"
                >
                  还没有激活码 — 点「+ 发激活码」开始。
                </td>
              </tr>
            ) : (
              items.map((it) => (
                <tr
                  key={it.id}
                  className="border-t border-border hover:bg-muted/30"
                  data-testid={`activation-row-${it.id}`}
                >
                  <td className="px-3 py-2 font-mono text-xs">#{it.id}</td>
                  <td className="px-3 py-2 font-mono text-xs">{it.plan}</td>
                  <td className="px-3 py-2">
                    <span
                      className={`rounded-full px-2 py-0.5 text-[10px] font-mono uppercase tracking-wider ${statusChipClass("activation", it.status)}`}
                      data-testid={`activation-status-${it.id}`}
                    >
                      {STATUS_LABELS[it.status]}
                    </span>
                  </td>
                  <td className="px-3 py-2 font-mono text-[10px] text-muted-foreground">
                    {it.bound_feishu_open_id ?? "—"}
                  </td>
                  <td className="px-3 py-2 font-mono text-xs">
                    {formatShortDate(it.expires_at)}
                  </td>
                  <td className="px-3 py-2 font-mono text-xs">
                    <div>{formatShortDate(it.created_at)}</div>
                    <div className="text-[10px] text-muted-foreground">
                      {formatRelativeTime(it.created_at)}
                    </div>
                  </td>
                  <td className="px-3 py-2 text-right">
                    <div className="flex justify-end gap-1">
                      <a
                        href={auditDeepLink("activation_code", it.id)}
                        className="rounded-md border border-border bg-background px-2 py-0.5 text-xs hover:bg-muted"
                        data-testid={`activation-audit-${it.id}`}
                      >
                        📋
                      </a>
                      {it.status !== "revoked" && (
                        <button
                          type="button"
                          onClick={() => setRevokeTarget(it)}
                          disabled={loading}
                          className="rounded-md border border-danger/40 bg-danger/10 px-2 py-0.5 text-xs text-danger hover:bg-danger/20 disabled:opacity-50"
                          data-testid={`activation-revoke-${it.id}`}
                        >
                          撤销
                        </button>
                      )}
                      {/* Phase 23 — Resend only works for unused/active
                          codes. Plaintext is unrecoverable, so this IM's
                          a "please contact us" hint to the open_id. */}
                      {(it.status === "unused" || it.status === "active") && (
                        <button
                          type="button"
                          onClick={() => setResendTarget(it)}
                          disabled={loading}
                          className="rounded-md border border-accent/40 bg-accent/10 px-2 py-0.5 text-xs text-accent hover:bg-accent/20 disabled:opacity-50"
                          data-testid={`activation-resend-${it.id}`}
                        >
                          补发
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

      {/* Issue modal ------------------------------------------------------*/}
      {issueOpen && (
        <IssueActivationModal
          onClose={() => setIssueOpen(false)}
          onIssued={onIssued}
          onError={(msg) => showToast("err", msg)}
        />
      )}

      {/* Resend modal (Phase 23) ------------------------------------------*/}
      {resendTarget && (
        <ResendActivationModal
          target={resendTarget}
          defaultOpenId={resendTarget.bound_feishu_open_id ?? ""}
          onClose={() => setResendTarget(null)}
          onSent={(msgId) => {
            setResendTarget(null);
            showToast("ok", `✅ 飞书补发提示已发送 (${msgId ?? "—"})`);
          }}
          onError={(msg) => showToast("err", msg)}
        />
      )}

      {/* Revoke confirm modal ---------------------------------------------*/}
      {revokeTarget && (
        <ConfirmRevokeModal
          target={revokeTarget}
          onClose={() => setRevokeTarget(null)}
          onConfirm={async () => {
            try {
              const resp = await revokeActivationCode(revokeTarget.id);
              onRevoked(resp);
            } catch (e) {
              showToast("err", (e as Error).message);
            }
          }}
        />
      )}

      {/* Toast ------------------------------------------------------------*/}
      {toast && (
        <div
          role="status"
          className={
            "fixed bottom-6 left-1/2 -translate-x-1/2 rounded-md px-4 py-2 text-sm shadow-lg " +
            (toast.kind === "ok"
              ? "bg-emerald-600 text-white"
              : "bg-red-600 text-white")
          }
          data-testid="activation-toast"
        >
          {toast.text}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Modals — kept inline; mirrors RejectReasonModal pattern.
// ---------------------------------------------------------------------------
function IssueActivationModal({
  onClose,
  onIssued,
  onError,
}: {
  onClose: () => void;
  onIssued: (resp: ActivationIssueResponse) => void;
  onError: (msg: string) => void;
}) {
  const [plan, setPlan] = useState<string>("basic");
  const [ttlDays, setTtlDays] = useState<string>("365");
  // Phase 23 — IM delivery is opt-in but on by default. The operator
  // can disable it for hand-delivery, or supply an explicit open_id to
  // DM the code straight to the customer.
  const [feishuOpenId, setFeishuOpenId] = useState<string>("");
  const [sendIm, setSendIm] = useState<boolean>(true);
  const [busy, setBusy] = useState(false);
  const submitDisabled =
    busy ||
    !plan ||
    PLAN_CODES.indexOf(plan as (typeof PLAN_CODES)[number]) === -1 ||
    !Number.isFinite(Number.parseInt(ttlDays, 10));

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
      data-testid="issue-modal"
      role="dialog"
      aria-modal="true"
    >
      <form
        onSubmit={async (e) => {
          e.preventDefault();
          if (submitDisabled) return;
          setBusy(true);
          try {
            const trimmed = feishuOpenId.trim();
            const resp = await issueActivationCode({
              plan,
              ttl_days: Number.parseInt(ttlDays, 10),
              ...(trimmed ? { feishu_open_id: trimmed } : {}),
              send_im: sendIm,
            });
            onIssued(resp);
          } catch (err) {
            onError((err as Error).message);
          } finally {
            setBusy(false);
          }
        }}
        className="w-full max-w-md rounded-xl border border-border bg-background p-6 shadow-2xl"
        data-testid="issue-form"
      >
        <header className="mb-4">
          <h2 className="text-lg font-semibold">发激活码</h2>
          <p className="mt-1 text-xs text-muted-foreground">
            后端只会返回一次明文 — 必须立即复制给用户。
          </p>
        </header>
        <div className="space-y-3 text-sm">
          <div>
            <label className="text-xs text-muted-foreground">plan</label>
            <select
              value={plan}
              onChange={(e) => setPlan(e.target.value)}
              className="mt-1 w-full rounded-md border border-border bg-background px-2 py-1.5"
              data-testid="issue-plan"
            >
              {PLAN_CODES.map((p) => (
                <option key={p} value={p}>
                  {p}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label className="text-xs text-muted-foreground">
              ttl_days (1..3650)
            </label>
            <input
              type="number"
              min={1}
              max={3650}
              value={ttlDays}
              onChange={(e) => setTtlDays(e.target.value)}
              className="mt-1 w-full rounded-md border border-border bg-background px-2 py-1.5"
              data-testid="issue-ttl"
            />
          </div>
          <div>
            <label className="text-xs text-muted-foreground">
              Feishu open_id (可选 — 填了就直接 IM 给客户)
            </label>
            <input
              type="text"
              value={feishuOpenId}
              onChange={(e) => setFeishuOpenId(e.target.value)}
              placeholder="ou_xxxxxxxx"
              className="mt-1 w-full rounded-md border border-border bg-background px-2 py-1.5 font-mono text-xs"
              data-testid="issue-feishu-open-id"
            />
          </div>
          <label className="flex cursor-pointer items-center gap-2">
            <input
              type="checkbox"
              checked={sendIm}
              onChange={(e) => setSendIm(e.target.checked)}
              data-testid="issue-send-im"
            />
            <span className="text-xs">
              自动通过飞书发送激活码 (Phase 23)
            </span>
          </label>
        </div>
        <footer className="mt-6 flex justify-end gap-2">
          <button
            type="button"
            onClick={onClose}
            disabled={busy}
            className="rounded-md border border-border bg-card/40 px-3 py-1.5 text-sm hover:bg-muted disabled:opacity-50"
            data-testid="issue-cancel"
          >
            取消
          </button>
          <button
            type="submit"
            disabled={submitDisabled}
            className="rounded-md bg-success px-3 py-1.5 text-sm font-medium text-white hover:opacity-90 disabled:opacity-50"
            data-testid="issue-submit"
          >
            {busy ? "处理中…" : "确认发放"}
          </button>
        </footer>
      </form>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Phase 23 — Resend modal
// ---------------------------------------------------------------------------
function ResendActivationModal({
  target,
  defaultOpenId,
  onClose,
  onSent,
  onError,
}: {
  target: ActivationCode;
  defaultOpenId: string;
  onClose: () => void;
  onSent: (messageId: string | null) => void;
  onError: (msg: string) => void;
}) {
  const [openId, setOpenId] = useState<string>(defaultOpenId);
  const [busy, setBusy] = useState(false);
  const submitDisabled = busy || openId.trim().length === 0;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
      data-testid="resend-modal"
      role="dialog"
      aria-modal="true"
    >
      <form
        onSubmit={async (e) => {
          e.preventDefault();
          if (submitDisabled) return;
          setBusy(true);
          try {
            const resp = await resendActivationCode(target.id, openId.trim());
            if (resp.sent) {
              onSent(resp.message_id ?? null);
            } else {
              onError(`飞书发送失败: ${resp.error ?? "unknown error"}`);
            }
          } catch (err) {
            onError((err as Error).message);
          } finally {
            setBusy(false);
          }
        }}
        className="w-full max-w-md rounded-xl border border-border bg-background p-6 shadow-2xl"
        data-testid="resend-form"
      >
        <header className="mb-4">
          <h2 className="text-lg font-semibold">补发激活码 #{target.id}</h2>
          <p className="mt-1 text-xs text-muted-foreground">
            明文已被哈希丢弃,只能给客户发一条“请联系我们索取新激活码”的提示。
          </p>
        </header>
        <div className="space-y-3 text-sm">
          <div>
            <label className="text-xs text-muted-foreground">Feishu open_id</label>
            <input
              type="text"
              value={openId}
              onChange={(e) => setOpenId(e.target.value)}
              placeholder="ou_xxxxxxxx"
              className="mt-1 w-full rounded-md border border-border bg-background px-2 py-1.5 font-mono text-xs"
              data-testid="resend-open-id"
              autoFocus
            />
          </div>
        </div>
        <footer className="mt-6 flex justify-end gap-2">
          <button
            type="button"
            onClick={onClose}
            disabled={busy}
            className="rounded-md border border-border bg-card/40 px-3 py-1.5 text-sm hover:bg-muted disabled:opacity-50"
            data-testid="resend-cancel"
          >
            取消
          </button>
          <button
            type="submit"
            disabled={submitDisabled}
            className="rounded-md bg-accent px-3 py-1.5 text-sm font-medium text-accent-foreground disabled:opacity-50"
            data-testid="resend-submit"
          >
            {busy ? "发送中…" : "发送飞书提示"}
          </button>
        </footer>
      </form>
    </div>
  );
}

function ConfirmRevokeModal({
  target,
  onClose,
  onConfirm,
}: {
  target: ActivationCode;
  onClose: () => void;
  onConfirm: () => Promise<void>;
}) {
  const [busy, setBusy] = useState(false);
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
      data-testid="revoke-modal"
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
        data-testid="revoke-form"
      >
        <header className="mb-4">
          <h2 className="text-lg font-semibold">撤销激活码 #{target.id}?</h2>
          <p className="mt-1 text-xs text-muted-foreground">
            撤销后无法继续用作激活 — 后端会写一行 audit。
          </p>
        </header>
        <footer className="mt-6 flex justify-end gap-2">
          <button
            type="button"
            onClick={onClose}
            disabled={busy}
            className="rounded-md border border-border bg-card/40 px-3 py-1.5 text-sm hover:bg-muted disabled:opacity-50"
            data-testid="revoke-cancel"
          >
            取消
          </button>
          <button
            type="submit"
            disabled={busy}
            className="rounded-md bg-danger px-3 py-1.5 text-sm font-medium text-white hover:opacity-90 disabled:opacity-50"
            data-testid="revoke-submit"
          >
            {busy ? "处理中…" : "确认撤销"}
          </button>
        </footer>
      </form>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Tiny atoms — same shape as AuditLogsPanel so styling stays consistent.
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
