import { fetchOpportunity } from "@/lib/api";
import { recommendationLabel } from "@/lib/utils";

import { ResearchReport } from "@/components/ResearchReport";
import { ScoreBreakdown } from "@/components/ScoreBreakdown";

export async function OpportunityDetail({ id }: { id: string }) {
  let opp: Awaited<ReturnType<typeof fetchOpportunity>> | null = null;
  let error: string | null = null;
  try {
    opp = await fetchOpportunity(id);
  } catch (err) {
    error = (err as Error).message;
  }

  if (error || !opp) {
    return (
      <p
        className="rounded-md border border-danger/40 bg-danger/10 p-4 text-sm"
        data-testid="opportunity-error"
      >
        Could not load opportunity: {error ?? "not found"}
      </p>
    );
  }

  const rec = recommendationLabel(opp.recommendation);

  return (
    <div className="space-y-8" data-testid="opportunity-detail">
      <article className="glass rounded-xl p-8">
        <div className="flex items-start justify-between gap-6">
          <div>
            {opp.category && (
              <span className="chip-accent">{opp.category}</span>
            )}
            <h1 className="mt-2 text-4xl font-semibold">{opp.title}</h1>
            {opp.summary && (
              <p className="mt-3 max-w-2xl text-muted-foreground">{opp.summary}</p>
            )}
          </div>
          <div className="text-right">
            <div className="text-6xl font-bold text-accent tabular-nums">
              {Math.round(opp.score)}
            </div>
            <div className="text-sm text-muted-foreground">total score / 100</div>
            <span className={`mt-3 inline-block chip ${rec.cls}`}>
              <span aria-hidden>{rec.emoji}</span> {rec.label}
            </span>
            {opp.status && (
              <div className="mt-2 text-xs uppercase tracking-wider text-muted-foreground">
                {opp.status.replace(/_/g, " ")}
              </div>
            )}
          </div>
        </div>
      </article>

      <article className="glass rounded-xl p-8">
        <ScoreBreakdown opportunity={opp} />
      </article>

      <ResearchReport id={id} />
    </div>
  );
}
