"use client";

/**
 * Phase 18 — Content Center review queue (admin).
 *
 * Mirrors `OrdersPanel` shape: server hands `initialItems` +
 * `initialTotal`; the client owns the filter URL state and re-fetches
 * when the user changes it. Compliance review queue is a URL shortcut
 * (`?status=draft&compliance_blocked=true`) — same component, no
 * separate page.
 */

import { useCallback, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";

import {
  fetchContentOpportunities,
  type ContentOpportunityListParams,
} from "@/lib/api";
import {
  NEXT_STATUS_MAP,
  STATUS_LABELS,
  formatRiskScore,
  formatShortDate,
  statusChipClass,
  truncate,
} from "@/lib/contentOpportunities";
import type { ContentOpportunity } from "@/types";

const STATUS_OPTIONS: Array<{ value: string; label: string }> = [
  { value: "", label: "全部" },
  { value: "draft", label: "草稿" },
  { value: "approved", label: "已批准" },
  { value: "published", label: "已发布" },
  { value: "rejected", label: "已驳回" },
  { value: "archived", label: "已归档" },
];

const COMPLIANCE_OPTIONS: Array<{ value: string; label: string }> = [
  { value: "", label: "全部" },
  { value: "true", label: "🛡️ 仅显示合规拦截" },
  { value: "false", label: "仅显示合规通过" },
];

export interface ContentOpportunitiesPanelProps {
  initialItems: ContentOpportunity[];
  initialTotal: number;
  initialStatusFilter: string;
  initialComplianceFilter: string;
  initialSignalId: number | null;
  /** Unfiltered list of all rows for stat cards (status breakdown). */
  initialAllItems: ContentOpportunity[];
}

export function ContentOpportunitiesPanel({
  initialItems,
  initialTotal,
  initialStatusFilter,
  initialComplianceFilter,
  initialSignalId,
  initialAllItems,
}: ContentOpportunitiesPanelProps) {
  const router = useRouter();
  const searchParams = useSearchParams();

  const [items, setItems] = useState<ContentOpportunity[]>(initialItems);
  const [total, setTotal] = useState<number>(initialTotal);
  const [allItems, setAllItems] =
    useState<ContentOpportunity[]>(initialAllItems);
  const [status, setStatus] = useState<string>(initialStatusFilter);
  const [compliance, setCompliance] = useState<string>(initialComplianceFilter);
  const [signalId, setSignalId] = useState<string>(
    initialSignalId !== null ? String(initialSignalId) : "",
  );
  const [busy, setBusy] = useState(false);
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

  const pushUrl = useCallback(
    (next: {
      status?: string;
      compliance?: string;
      signal_id?: string;
    }) => {
      const params = new URLSearchParams(searchParams.toString());
      const setOrDel = (k: string, v: string | undefined) => {
        if (v && v.length > 0) params.set(k, v);
        else params.delete(k);
      };
      setOrDel("status", next.status);
      setOrDel("compliance_blocked", next.compliance);
      setOrDel("signal_id", next.signal_id);
      const qs = params.toString();
      router.push(qs ? `/admin/content-opportunities?${qs}` : "/admin/content-opportunities");
    },
    [router, searchParams],
  );

  const refresh = useCallback(
    async (overrides: Partial<ContentOpportunityListParams> = {}) => {
      setBusy(true);
      try {
        const params: ContentOpportunityListParams = {
          limit: 50,
          offset: 0,
          ...overrides,
        };
        if (params.status === undefined) {
          params.status = status || undefined;
        }
        if (params.compliance_blocked === undefined) {
          params.compliance_blocked =
            compliance === "true"
              ? true
              : compliance === "false"
                ? false
                : undefined;
        }
        if (params.signal_id === undefined) {
          const n = signalId ? Number.parseInt(signalId, 10) : NaN;
          if (Number.isFinite(n)) params.signal_id = n;
        }
        const [filtered, all] = await Promise.all([
          fetchContentOpportunities(params),
          // Pull unfiltered (limit=200) for the stat cards.
          fetchContentOpportunities({ limit: 200 }),
        ]);
        setItems(filtered.items);
        setTotal(filtered.total);
        setAllItems(all.items);
      } catch (err) {
        showToast("err", (err as Error).message);
      } finally {
        setBusy(false);
      }
    },
    [status, compliance, signalId, showToast],
  );

  const onStatusChange = useCallback(
    (v: string) => {
      setStatus(v);
      pushUrl({ status: v });
      refresh({ status: v || undefined });
    },
    [pushUrl, refresh],
  );

  const onComplianceChange = useCallback(
    (v: string) => {
      setCompliance(v);
      pushUrl({ compliance: v });
      refresh({
        compliance_blocked:
          v === "true" ? true : v === "false" ? false : undefined,
      });
    },
    [pushUrl, refresh],
  );

  const onSignalIdSubmit = useCallback(
    (e: React.FormEvent) => {
      e.preventDefault();
      const v = signalId.trim();
      pushUrl({ signal_id: v });
      const n = v ? Number.parseInt(v, 10) : NaN;
      refresh({ signal_id: Number.isFinite(n) ? n : undefined });
    },
    [signalId, pushUrl, refresh],
  );

  const onReset = useCallback(async () => {
    setStatus("");
    setCompliance("");
    setSignalId("");
    router.push("/admin/content-opportunities");
    setBusy(true);
    try {
      const [filtered, all] = await Promise.all([
        fetchContentOpportunities({
          limit: 50,
          offset: 0,
          status: undefined,
          compliance_blocked: undefined,
          signal_id: undefined,
        }),
        fetchContentOpportunities({ limit: 200 }),
      ]);
      setItems(filtered.items);
      setTotal(filtered.total);
      setAllItems(all.items);
    } catch (err) {
      showToast("err", (err as Error).message);
    } finally {
      setBusy(false);
    }
  }, [router, showToast]);

  // Stat card computations off the unfiltered list.
  const counts = allItems.reduce<Record<string, number>>(
    (acc, it) => {
      acc[it.status] = (acc[it.status] ?? 0) + 1;
      if (it.compliance_blocked && it.status === "draft") {
        acc.review_queue = (acc.review_queue ?? 0) + 1;
      }
      return acc;
    },
    { review_queue: 0 },
  );

  return (
    <div className="space-y-8" data-testid="content-opportunities-panel">
      {/* Stat cards */}
      <section
        className="grid gap-4 md:grid-cols-5"
        data-testid="content-opportunities-stats"
      >
        <StatCard label="草稿" value={String(counts.draft ?? 0)} testid="stat-draft" />
        <StatCard label="已批准" value={String(counts.approved ?? 0)} testid="stat-approved" />
        <StatCard label="已发布" value={String(counts.published ?? 0)} testid="stat-published" />
        <StatCard label="已驳回" value={String(counts.rejected ?? 0)} testid="stat-rejected" />
        <button
          type="button"
          onClick={() => {
            setStatus("draft");
            setCompliance("true");
            pushUrl({ status: "draft", compliance: "true" });
            refresh({ status: "draft", compliance_blocked: true });
          }}
          className="rounded-xl border border-warning/40 bg-warning/10 p-4 text-left hover:bg-warning/20"
          data-testid="stat-review-queue"
          title="点击查看合规拦截的草稿"
        >
          <div className="text-xs text-warning">🛡️ 待复核</div>
          <div className="mt-2 text-2xl font-semibold text-warning">
            {counts.review_queue ?? 0}
          </div>
        </button>
      </section>

      {/* Filters */}
      <section className="flex flex-wrap items-center gap-3">
        <label className="text-xs text-muted-foreground">
          状态
          <select
            value={status}
            onChange={(e) => onStatusChange(e.target.value)}
            className="ml-1 rounded-md border border-border bg-background px-2 py-1 text-xs"
            data-testid="filter-status"
          >
            {STATUS_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>
        </label>
        <label className="text-xs text-muted-foreground">
          合规
          <select
            value={compliance}
            onChange={(e) => onComplianceChange(e.target.value)}
            className="ml-1 rounded-md border border-border bg-background px-2 py-1 text-xs"
            data-testid="filter-compliance"
          >
            {COMPLIANCE_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>
        </label>
        <form onSubmit={onSignalIdSubmit} className="flex items-center gap-1 text-xs">
          <label className="text-muted-foreground">signal_id</label>
          <input
            type="number"
            value={signalId}
            onChange={(e) => setSignalId(e.target.value)}
            className="w-24 rounded-md border border-border bg-background px-2 py-1 text-xs"
            data-testid="filter-signal-id"
            min={1}
          />
          <button
            type="submit"
            className="rounded-md border border-border px-2 py-1 hover:bg-muted"
            data-testid="filter-signal-id-apply"
          >
            应用
          </button>
        </form>
        <button
          onClick={onReset}
          className="rounded-md border border-border px-2 py-1 text-xs hover:bg-muted"
          data-testid="filter-reset"
        >
          重置
        </button>
        {busy && (
          <span className="text-xs text-muted-foreground" data-testid="busy-indicator">
            刷新中…
          </span>
        )}
      </section>

      {/* Table */}
      {items.length === 0 ? (
        <div
          className="rounded-xl border border-dashed border-border p-12 text-center text-sm text-muted-foreground"
          data-testid="content-opportunities-empty"
        >
          当前过滤下没有 ContentOpportunity。
          <br />
          <span className="text-xs">
            它们由飞书 <code className="mx-1 rounded bg-muted px-1">/content &lt;id&gt;</code>{" "}
            命令触发后写入数据库。
          </span>
        </div>
      ) : (
        <div className="overflow-x-auto rounded-xl border border-border">
          <table className="w-full text-xs" data-testid="content-opportunities-table">
            <thead className="bg-muted/30 text-left">
              <tr>
                <th className="px-3 py-2 font-medium">ID</th>
                <th className="px-3 py-2 font-medium">signal</th>
                <th className="px-3 py-2 font-medium">platform</th>
                <th className="px-3 py-2 font-medium">tone</th>
                <th className="px-3 py-2 font-medium">hook</th>
                <th className="px-3 py-2 font-medium">score</th>
                <th className="px-3 py-2 font-medium">状态</th>
                <th className="px-3 py-2 font-medium">合规</th>
                <th className="px-3 py-2 font-medium">时间</th>
                <th className="px-3 py-2 font-medium"></th>
              </tr>
            </thead>
            <tbody>
              {items.map((it) => (
                <tr
                  key={it.id}
                  className="border-t border-border hover:bg-muted/20"
                  data-testid={`co-row-${it.id}`}
                >
                  <td className="px-3 py-2 font-mono">#{it.id}</td>
                  <td className="px-3 py-2 font-mono">{it.signal_id}</td>
                  <td className="px-3 py-2">{it.platform}</td>
                  <td className="px-3 py-2">{it.tone ?? "—"}</td>
                  <td className="px-3 py-2 max-w-xs" title={it.hook ?? ""}>
                    {truncate(it.hook, 40)}
                  </td>
                  <td className="px-3 py-2 font-mono">
                    {it.content_score.toFixed(0)}
                  </td>
                  <td className="px-3 py-2">
                    <span
                      className={
                        "rounded-full px-2 py-0.5 " + statusChipClass(it.status)
                      }
                      data-testid={`co-status-${it.id}`}
                    >
                      {STATUS_LABELS[it.status]}
                    </span>
                  </td>
                  <td className="px-3 py-2">
                    {it.compliance_blocked ? (
                      <span
                        className="rounded-full bg-red-500/20 px-2 py-0.5 text-red-300"
                        title={`risk ${formatRiskScore(it.compliance_risk_score)} · ${it.compliance_risk_types.join(", ") || "no specific type"}`}
                        data-testid={`co-compliance-blocked-${it.id}`}
                      >
                        🛡️ 拦截
                      </span>
                    ) : (
                      <span
                        className="rounded-full bg-emerald-500/20 px-2 py-0.5 text-emerald-300"
                        data-testid={`co-compliance-ok-${it.id}`}
                      >
                        ✓ 通过
                      </span>
                    )}
                  </td>
                  <td className="px-3 py-2 text-[10px] text-muted-foreground">
                    {formatShortDate(it.created_at)}
                  </td>
                  <td className="px-3 py-2">
                    <a
                      href={`/admin/content-opportunities/${it.id}`}
                      className="text-accent hover:underline"
                      data-testid={`co-detail-link-${it.id}`}
                    >
                      详情 →
                    </a>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {total > items.length && (
            <div
              className="border-t border-border px-3 py-2 text-[10px] text-muted-foreground"
              data-testid="content-opportunities-overflow"
            >
              显示前 {items.length} 条,共 {total} 条
            </div>
          )}
        </div>
      )}

      {/* Toast */}
      {toast && (
        <div
          role="status"
          className={
            "fixed bottom-6 left-1/2 -translate-x-1/2 rounded-md px-4 py-2 text-sm shadow-lg " +
            (toast.kind === "ok"
              ? "bg-emerald-600 text-white"
              : "bg-red-600 text-white")
          }
          data-testid="content-opportunities-toast"
        >
          {toast.text}
        </div>
      )}
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