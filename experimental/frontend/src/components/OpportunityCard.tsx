import Link from "next/link";
import type { Opportunity } from "@/types";
import { formatScore, recommendationLabel } from "@/lib/utils";

export function OpportunityCard({ opportunity }: { opportunity: Opportunity }) {
  const rec = recommendationLabel(opportunity.recommendation);
  return (
    <Link
      href={`/opportunities/${opportunity.id}`}
      className="glass group block rounded-xl p-5 transition hover:border-accent/60"
    >
      <div className="flex items-start justify-between">
        <div>
          {opportunity.category && (
            <span className="chip-accent">{opportunity.category}</span>
          )}
          <h3 className="mt-2 text-lg font-semibold group-hover:text-accent">
            {opportunity.title}
          </h3>
        </div>
        <div className="text-right">
          <div className="text-3xl font-bold tabular-nums text-accent">
            {Math.round(opportunity.score)}
          </div>
          <div className="text-xs text-muted-foreground">/ 100</div>
        </div>
      </div>
      {opportunity.summary && (
        <p className="mt-3 text-sm text-muted-foreground">{opportunity.summary}</p>
      )}
      <div className="mt-4 flex flex-wrap gap-2 text-xs">
        {opportunity.china_gap !== undefined && (
          <span className="chip-success">China Gap {formatScore(opportunity.china_gap)}</span>
        )}
        {opportunity.execution_score !== undefined && (
          <span className="chip-warning">Execution {formatScore(opportunity.execution_score)}</span>
        )}
        <span className={rec.cls}>{rec.label}</span>
      </div>
    </Link>
  );
}