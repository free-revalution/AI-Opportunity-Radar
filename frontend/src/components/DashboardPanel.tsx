"use client";

/**
 * Phase 19 — admin dashboard panel (sole-operator landing page).
 *
 * 5+2 stat cards with pre-filtered jump links + recent activity feed
 * sourced from AuditLog `content_opportunity_transition` rows. Refresh
 * is by browser reload (Phase 19 simplification) — Phase 20 will add
 * a ↻ button + useEffect invalidate.
 */

import { useState } from "react";

import {
  activityReason,
  formatActivityTransition,
  formatShortDate,
} from "@/lib/contentOpportunities";
import type { DashboardResponse } from "@/types";
import { formatRelativeTime } from "@/lib/utils";

export interface DashboardPanelProps {
  initial: DashboardResponse;
}

export function DashboardPanel({ initial }: DashboardPanelProps) {
  const [data] = useState<DashboardResponse>(initial);
  const co = data.content_opportunities;
  const sig = data.signals;
  const activity = data.recent_activity;

  return (
    <div className="space-y-8" data-testid="admin-dashboard-panel">
      {/* ContentOpportunity stats */}
      <section>
        <h2 className="mb-3 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
          ContentOpportunity 状态机
        </h2>
        <div
          className="grid gap-4 md:grid-cols-5"
          data-testid="stat-co-cards"
        >
          <StatLink
            label="草稿"
            value={co.by_status.draft ?? 0}
            href="/admin/content-opportunities?status=draft"
            testid="stat-co-draft"
          />
          <StatLink
            label="已批准"
            value={co.by_status.approved ?? 0}
            href="/admin/content-opportunities?status=approved"
            testid="stat-co-approved"
          />
          <StatLink
            label="已发布"
            value={co.by_status.published ?? 0}
            href="/admin/content-opportunities?status=published"
            testid="stat-co-published"
          />
          <StatLink
            label="已驳回"
            value={co.by_status.rejected ?? 0}
            href="/admin/content-opportunities?status=rejected"
            testid="stat-co-rejected"
          />
          <button
            type="button"
            onClick={() => {
              window.location.href =
                "/admin/content-opportunities?status=draft&compliance_blocked=true";
            }}
            className="rounded-xl border border-warning/40 bg-warning/10 p-4 text-left hover:bg-warning/20"
            data-testid="stat-co-review-queue"
            title="点击查看合规拦截的草稿"
          >
            <div className="text-xs text-warning">🛡️ 待复核</div>
            <div className="mt-2 text-2xl font-semibold text-warning">
              {co.blocked_review_queue}
            </div>
          </button>
        </div>
        <p
          className="mt-2 text-xs text-muted-foreground"
          data-testid="stat-co-meta"
        >
          总计 {co.total} 条 · 今日新建 {co.new_today} · 最近 7 天{" "}
          {co.recent_7d_count}
        </p>
      </section>

      {/* Signal stats */}
      <section>
        <h2 className="mb-3 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
          Signal 健康度
        </h2>
        <div
          className="grid gap-4 md:grid-cols-4"
          data-testid="stat-sig-cards"
        >
          <StatLink
            label="verified"
            value={sig.by_status.verified ?? 0}
            href="/admin/signals?status=verified"
            testid="stat-sig-verified"
          />
          <StatLink
            label="recent 7d"
            value={sig.recent_7d_count}
            href="/admin/signals"
            testid="stat-sig-recent"
          />
          <StatLink
            label="discovered"
            value={sig.by_status.discovered ?? 0}
            href="/admin/signals?status=discovered"
            testid="stat-sig-discovered"
          />
          <StatLink
            label="rejected"
            value={sig.by_status.rejected ?? 0}
            href="/admin/signals?status=rejected"
            testid="stat-sig-rejected"
          />
        </div>
        <p
          className="mt-2 text-xs text-muted-foreground"
          data-testid="stat-sig-meta"
        >
          总计 {sig.total} 条 · 今日新建 {sig.new_today}
        </p>
      </section>

      {/* Recent activity feed */}
      <section data-testid="activity-section">
        <h2 className="mb-3 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
          最近活动(content_opportunity_transition)
        </h2>
        {activity.length === 0 ? (
          <p
            className="rounded-xl border border-dashed border-border p-8 text-center text-sm text-muted-foreground"
            data-testid="activity-empty"
          >
            还没有任何状态转换记录 —— 在飞书 <code className="mx-1 rounded bg-muted px-1">/content &lt;id&gt;</code>{" "}
            触发第一条,或在 /admin/content-opportunities 点「批准 / 驳回 / 发布」。
          </p>
        ) : (
          <ol className="space-y-2" data-testid="activity-feed">
            {activity.map((it) => {
              const reason = activityReason(it.metadata_json);
              const transition = formatActivityTransition(it.metadata_json);
              const created = it.created_at;
              const targetUrl = it.resource_id
                ? `/admin/content-opportunities/${it.resource_id}`
                : null;
              return (
                <li
                  key={it.id}
                  className="flex flex-wrap items-center gap-3 rounded-lg border border-border bg-card/40 px-4 py-2.5 text-sm"
                  data-testid={`activity-row-${it.id}`}
                >
                  <span
                    className="rounded-full bg-muted/40 px-2 py-0.5 text-[10px] font-mono uppercase tracking-wider text-muted-foreground"
                    data-testid={`activity-actor-${it.id}`}
                  >
                    {it.actor_type}
                  </span>
                  <span className="font-mono text-base font-semibold">
                    {transition}
                  </span>
                  {targetUrl ? (
                    <a
                      href={targetUrl}
                      className="font-mono text-xs text-accent hover:underline"
                      data-testid={`activity-target-${it.id}`}
                    >
                      #{it.resource_id} →
                    </a>
                  ) : (
                    <span className="font-mono text-xs text-muted-foreground">
                      (无目标)
                    </span>
                  )}
                  {reason && (
                    <span
                      className="text-xs italic text-muted-foreground"
                      data-testid={`activity-reason-${it.id}`}
                    >
                      「{reason}」
                    </span>
                  )}
                  <span className="ml-auto text-[10px] text-muted-foreground">
                    <span data-testid={`activity-relative-${it.id}`}>
                      {formatRelativeTime(created)}
                    </span>
                    <span className="mx-1">·</span>
                    <span data-testid={`activity-absolute-${it.id}`}>
                      {formatShortDate(created)}
                    </span>
                  </span>
                </li>
              );
            })}
          </ol>
        )}
      </section>

      {/* Quick links */}
      <section
        className="flex flex-wrap gap-3 border-t border-border pt-6"
        data-testid="quick-links"
      >
        <a
          href="/admin/content-opportunities"
          className="rounded-md border border-border bg-card/40 px-4 py-2 text-sm hover:bg-muted"
          data-testid="quick-link-co"
        >
          → 全列表:ContentOpportunities
        </a>
        <a
          href="/admin/signals"
          className="rounded-md border border-border bg-card/40 px-4 py-2 text-sm hover:bg-muted"
          data-testid="quick-link-signals"
        >
          → 全列表:Signals
        </a>
      </section>
    </div>
  );
}

// ---------------------------------------------------------------------------
type StatCardProps = {
  label: string;
  value: number;
  testid: string;
};

function StatCard({ label, value, testid }: StatCardProps) {
  const display = value > 0 ? String(value) : "—";
  return (
    <div
      className="rounded-xl border border-border bg-card/40 p-4"
      data-testid={testid}
    >
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className="mt-2 text-2xl font-semibold">{display}</div>
    </div>
  );
}

function StatLink({
  label,
  value,
  href,
  testid,
}: StatCardProps & { href: string }) {
  const display = value > 0 ? String(value) : "—";
  return (
    <a
      href={href}
      className="block rounded-xl border border-border bg-card/40 p-4 hover:bg-muted/40"
      data-testid={testid}
    >
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className="mt-2 text-2xl font-semibold">{display}</div>
    </a>
  );
}