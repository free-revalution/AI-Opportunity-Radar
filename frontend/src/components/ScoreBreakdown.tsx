import type { Opportunity } from "@/types";

import {
  SUB_SCORE_LABELS,
  cn,
  formatScore,
  scoreBarWidth,
} from "@/lib/utils";

interface ScoreBreakdownProps {
  opportunity: Pick<
    Opportunity,
    | "trend_score"
    | "demand_score"
    | "monetization_score"
    | "competition_gap_score"
    | "china_gap_score"
    | "execution_score"
  >;
}

/**
 * Horizontal bars for the six weighted sub-scores that drive the
 * README §12 scoring formula. Each row shows the band, the weight
 * hint, the value, and a coloured fill so the operator can spot the
 * weakest dimension at a glance.
 */
export function ScoreBreakdown({ opportunity }: ScoreBreakdownProps) {
  return (
    <section
      className="space-y-3"
      aria-label="Score breakdown"
      data-testid="score-breakdown"
    >
      <h2 className="text-sm font-semibold uppercase tracking-wider text-muted-foreground">
        Score breakdown
      </h2>
      <ul className="space-y-2">
        {SUB_SCORE_LABELS.map(({ key, label, hint }) => {
          const value = opportunity[key];
          const width = scoreBarWidth(value);
          const tone =
            width >= 70
              ? "bg-success"
              : width >= 55
                ? "bg-accent"
                : width >= 35
                  ? "bg-warning"
                  : "bg-danger";
          return (
            <li
              key={key}
              className="grid grid-cols-[10rem_1fr_auto] items-center gap-3 text-sm"
              data-testid={`score-row-${key}`}
            >
              <div>
                <div className="font-medium">{label}</div>
                <div className="text-xs text-muted-foreground">weight {hint}</div>
              </div>
              <div
                className="relative h-2 overflow-hidden rounded-full bg-muted/40"
                role="progressbar"
                aria-valuenow={Math.round(width)}
                aria-valuemin={0}
                aria-valuemax={100}
                aria-label={`${label} ${formatScore(value)}`}
              >
                <div
                  className={cn("h-full rounded-full transition-all", tone)}
                  style={{ width: `${width}%` }}
                />
              </div>
              <div className="w-14 text-right font-mono text-xs tabular-nums">
                {formatScore(value)}
              </div>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
