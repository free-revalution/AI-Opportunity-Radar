"use client";

/**
 * QualityBadge — Phase 10 LLM-as-judge score visualisation.
 *
 * Three states:
 *   • Hidden    — no score persisted yet
 *   • Pass      — total >= threshold; green pill
 *   • Fail      — total < threshold OR any dim below floor; amber pill
 *
 * Click → tooltip with the 5 sub-scores + rationale. The button mode
 * (`onScore`) lets the parent wire a "score this now" action when no
 * score is persisted yet.
 */

import type { ContentQualityScore } from "@/types";

export interface QualityBadgeProps {
  score: ContentQualityScore | null | undefined;
  loading?: boolean;
  onScore?: () => void;
  onAutoImprove?: () => void;
  busy?: boolean;
}

export function QualityBadge({
  score,
  loading = false,
  onScore,
  onAutoImprove,
  busy = false,
}: QualityBadgeProps) {
  if (loading) {
    return (
      <span
        className="inline-flex items-center gap-1 rounded-full bg-muted/40 px-2 py-0.5 text-[10px] text-muted-foreground"
        data-testid="quality-badge-loading"
      >
        ⏳ 评分中…
      </span>
    );
  }

  if (!score) {
    if (!onScore) return null;
    return (
      <button
        onClick={onScore}
        disabled={busy}
        className="inline-flex items-center gap-1 rounded-full border border-dashed border-border px-2 py-0.5 text-[10px] text-muted-foreground hover:bg-muted disabled:opacity-40"
        data-testid="quality-badge-score-now"
        title="调 LLM 评一次分"
      >
        🎯 评分
      </button>
    );
  }

  const pass = !score.below_threshold;
  const bg = pass ? "bg-emerald-500/20 text-emerald-300" : "bg-amber-500/20 text-amber-300";
  return (
    <span
      className="group/quality relative inline-flex items-center gap-1"
      data-testid={`quality-badge-${pass ? "pass" : "fail"}`}
    >
      <span
        className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[10px] font-mono font-semibold ${bg}`}
        title={score.rationale}
      >
        {pass ? "✓" : "⚠"} {score.total.toFixed(1)}
      </span>
      {onAutoImprove && !pass && (
        <button
          onClick={onAutoImprove}
          disabled={busy}
          className="rounded border border-amber-500/60 px-1.5 py-0.5 text-[10px] text-amber-300 hover:bg-amber-500/10 disabled:opacity-40"
          data-testid="quality-badge-auto-improve"
          title="低于阈值 — 让 LLM 再跑一次"
        >
          {busy ? "重跑中…" : "自动重跑"}
        </button>
      )}
      <ScoreTooltip score={score} />
    </span>
  );
}

function ScoreTooltip({ score }: { score: ContentQualityScore }) {
  return (
    <span
      role="tooltip"
      className="pointer-events-none absolute right-0 top-full z-20 mt-1 hidden w-60 rounded-md border border-border bg-card p-2 text-[10px] leading-relaxed text-foreground shadow-lg group-hover/quality:block"
      data-testid="quality-badge-tooltip"
    >
      <p className="mb-1 font-semibold">
        LLM 评分 · 总分 {score.total.toFixed(2)} / 10
        {score.below_threshold
          ? " · 低于阈值"
          : " · 已达标"}
      </p>
      <ul className="space-y-0.5 font-mono text-[10px]">
        <li>开头钩子: {score.hook_strength.toFixed(1)}</li>
        <li>CTA 自然度: {score.cta_naturalness.toFixed(1)}</li>
        <li>数据准确性: {score.data_accuracy.toFixed(1)}</li>
        <li>字数合规: {score.char_count_compliance.toFixed(1)}</li>
        <li>平台风格: {score.platform_style_match.toFixed(1)}</li>
      </ul>
      {score.rationale && (
        <p className="mt-1 italic text-muted-foreground">&ldquo;{score.rationale}&rdquo;</p>
      )}
      <p className="mt-1 text-[9px] text-muted-foreground">
        阈值 {score.threshold_used.toFixed(1)} · 单维底线 {score.dimension_floor_used.toFixed(1)}
      </p>
    </span>
  );
}