"use client";

/**
 * Phase 18 — admin Signal browser.
 *
 * Read-only table of recent signals. Status + min-signal-score filters
 * + simple pagination (no detail page — signals are leaf nodes).
 */

import { useCallback, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";

import { fetchSignals, type SignalListParams } from "@/lib/api";
import { formatShortDate, truncate } from "@/lib/contentOpportunities";
import type {
  Signal,
  SignalLifecycleStatus,
} from "@/types";

const STATUS_OPTIONS: Array<{ value: string; label: string }> = [
  { value: "", label: "全部" },
  { value: "discovered", label: "已发现" },
  { value: "validating", label: "验证中" },
  { value: "verified", label: "已验证" },
  { value: "analyzing", label: "分析中" },
  { value: "published", label: "已发布" },
  { value: "expired", label: "已过期" },
  { value: "rejected", label: "已驳回" },
];

function statusChipClass(status: SignalLifecycleStatus | string): string {
  switch (status) {
    case "verified":
      return "bg-emerald-500/20 text-emerald-300";
    case "discovered":
      return "bg-blue-500/20 text-blue-300";
    case "validating":
    case "analyzing":
      return "bg-amber-500/20 text-amber-300";
    case "published":
      return "bg-accent/20 text-accent-foreground";
    case "expired":
      return "bg-zinc-500/20 text-zinc-400";
    case "rejected":
      return "bg-red-500/20 text-red-300";
    default:
      return "bg-zinc-500/20 text-zinc-300";
  }
}

export interface SignalsPanelProps {
  initialItems: Signal[];
  initialTotal: number;
  initialStatus: string;
  initialMinScore: number | null;
}

export function SignalsPanel({
  initialItems,
  initialTotal,
  initialStatus,
  initialMinScore,
}: SignalsPanelProps) {
  const router = useRouter();
  const searchParams = useSearchParams();

  const [items, setItems] = useState<Signal[]>(initialItems);
  const [total, setTotal] = useState<number>(initialTotal);
  const [status, setStatus] = useState<string>(initialStatus);
  const [minScore, setMinScore] = useState<string>(
    initialMinScore !== null ? String(initialMinScore) : "",
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
    (next: { status?: string; min_signal_score?: string }) => {
      const params = new URLSearchParams(searchParams.toString());
      const setOrDel = (k: string, v: string | undefined) => {
        if (v && v.length > 0) params.set(k, v);
        else params.delete(k);
      };
      setOrDel("status", next.status);
      setOrDel("min_signal_score", next.min_signal_score);
      const qs = params.toString();
      router.push(qs ? `/admin/signals?${qs}` : "/admin/signals");
    },
    [router, searchParams],
  );

  const refresh = useCallback(
    async (overrides: Partial<SignalListParams> = {}) => {
      setBusy(true);
      try {
        const params: SignalListParams = { limit: 50, offset: 0, ...overrides };
        if (params.status === undefined) params.status = status || undefined;
        if (params.min_signal_score === undefined) {
          const n = minScore ? Number.parseFloat(minScore) : NaN;
          if (Number.isFinite(n)) params.min_signal_score = n;
        }
        const data = await fetchSignals(params);
        setItems(data.items);
        setTotal(data.total);
      } catch (err) {
        showToast("err", (err as Error).message);
      } finally {
        setBusy(false);
      }
    },
    [status, minScore, showToast],
  );

  const onStatusChange = useCallback(
    (v: string) => {
      setStatus(v);
      pushUrl({ status: v });
      refresh({ status: v || undefined });
    },
    [pushUrl, refresh],
  );

  const onMinScoreSubmit = useCallback(
    (e: React.FormEvent) => {
      e.preventDefault();
      const v = minScore.trim();
      pushUrl({ min_signal_score: v });
      const n = v ? Number.parseFloat(v) : NaN;
      refresh({ min_signal_score: Number.isFinite(n) ? n : undefined });
    },
    [minScore, pushUrl, refresh],
  );

  const onReset = useCallback(async () => {
    setStatus("");
    setMinScore("");
    router.push("/admin/signals");
    setBusy(true);
    try {
      const data = await fetchSignals({
        limit: 50,
        offset: 0,
        status: undefined,
        min_signal_score: undefined,
      });
      setItems(data.items);
      setTotal(data.total);
    } catch (err) {
      showToast("err", (err as Error).message);
    } finally {
      setBusy(false);
    }
  }, [router, showToast]);

  return (
    <div className="space-y-6" data-testid="signals-panel">
      {/* Filters */}
      <section className="flex flex-wrap items-center gap-3">
        <label className="text-xs text-muted-foreground">
          状态
          <select
            value={status}
            onChange={(e) => onStatusChange(e.target.value)}
            className="ml-1 rounded-md border border-border bg-background px-2 py-1 text-xs"
            data-testid="signal-filter-status"
          >
            {STATUS_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>
        </label>
        <form onSubmit={onMinScoreSubmit} className="flex items-center gap-1 text-xs">
          <label className="text-muted-foreground">最低分</label>
          <input
            type="number"
            value={minScore}
            onChange={(e) => setMinScore(e.target.value)}
            className="w-20 rounded-md border border-border bg-background px-2 py-1 text-xs"
            data-testid="signal-filter-min-score"
            min={0}
            max={100}
            step={1}
          />
          <button
            type="submit"
            className="rounded-md border border-border px-2 py-1 hover:bg-muted"
            data-testid="signal-filter-apply"
          >
            应用
          </button>
        </form>
        <button
          onClick={onReset}
          className="rounded-md border border-border px-2 py-1 text-xs hover:bg-muted"
          data-testid="signal-filter-reset"
        >
          重置
        </button>
        {busy && (
          <span className="text-xs text-muted-foreground" data-testid="signal-busy">
            刷新中…
          </span>
        )}
      </section>

      {/* Table */}
      {items.length === 0 ? (
        <div
          className="rounded-xl border border-dashed border-border p-12 text-center text-sm text-muted-foreground"
          data-testid="signals-empty"
        >
          当前过滤下没有 Signal。
        </div>
      ) : (
        <div className="overflow-x-auto rounded-xl border border-border">
          <table className="w-full text-xs" data-testid="signals-table">
            <thead className="bg-muted/30 text-left">
              <tr>
                <th className="px-3 py-2 font-medium">ID</th>
                <th className="px-3 py-2 font-medium">raw_item</th>
                <th className="px-3 py-2 font-medium">type</th>
                <th className="px-3 py-2 font-medium">keyword</th>
                <th className="px-3 py-2 font-medium">title</th>
                <th className="px-3 py-2 font-medium">score</th>
                <th className="px-3 py-2 font-medium">confidence</th>
                <th className="px-3 py-2 font-medium">状态</th>
                <th className="px-3 py-2 font-medium">合规</th>
                <th className="px-3 py-2 font-medium">risk</th>
                <th className="px-3 py-2 font-medium">时间</th>
              </tr>
            </thead>
            <tbody>
              {items.map((s) => (
                <tr
                  key={s.id}
                  className="border-t border-border hover:bg-muted/20"
                  data-testid={`signal-row-${s.id}`}
                >
                  <td className="px-3 py-2 font-mono">#{s.id}</td>
                  <td className="px-3 py-2 font-mono">{s.raw_item_id}</td>
                  <td className="px-3 py-2">{s.signal_type ?? "—"}</td>
                  <td className="px-3 py-2">{s.keyword ?? "—"}</td>
                  <td
                    className="px-3 py-2 max-w-xs"
                    title={s.title ?? ""}
                  >
                    {truncate(s.title, 40)}
                  </td>
                  <td className="px-3 py-2 font-mono">
                    {s.signal_score.toFixed(0)}
                  </td>
                  <td className="px-3 py-2 font-mono">
                    {s.confidence_score.toFixed(0)}
                  </td>
                  <td className="px-3 py-2">
                    <span
                      className={
                        "rounded-full px-2 py-0.5 " + statusChipClass(s.status)
                      }
                      data-testid={`signal-status-${s.id}`}
                    >
                      {s.status}
                    </span>
                  </td>
                  <td className="px-3 py-2">{s.compliance_status ?? "—"}</td>
                  <td className="px-3 py-2 font-mono">
                    {s.risk_score.toFixed(2)}
                  </td>
                  <td className="px-3 py-2 text-[10px] text-muted-foreground">
                    {formatShortDate(s.created_at)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {total > items.length && (
            <div
              className="border-t border-border px-3 py-2 text-[10px] text-muted-foreground"
              data-testid="signals-overflow"
            >
              显示前 {items.length} 条,共 {total} 条
            </div>
          )}
        </div>
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
          data-testid="signals-toast"
        >
          {toast.text}
        </div>
      )}
    </div>
  );
}