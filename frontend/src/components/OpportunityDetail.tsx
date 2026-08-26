import { fetchOpportunity } from "@/lib/api";
import { formatScore, recommendationLabel } from "@/lib/utils";

export async function OpportunityDetail({ id }: { id: string }) {
  try {
    const opp = await fetchOpportunity(id);
    const rec = recommendationLabel(opp.recommendation);
    return (
      <article className="glass rounded-xl p-8">
        <div className="flex items-start justify-between gap-6">
          <div>
            {opp.category && <span className="chip-accent">{opp.category}</span>}
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
            <span className={`mt-3 ${rec.cls}`}>{rec.label}</span>
          </div>
        </div>

        <section className="mt-8 grid gap-6 md:grid-cols-2">
          <Field label="Why now?" value="(filled by deep research in Phase 7)" />
          <Field label="Customer pain" value="(filled by deep research in Phase 7)" />
          <Field label="Competitors" value="(filled by deep research in Phase 7)" />
          <Field label="China Gap" value={formatScore(opp.china_gap)} />
          <Field label="Execution Feasibility" value={formatScore(opp.execution_score)} />
          <Field label="Monetization" value={opp.monetization ?? "unknown"} />
        </section>
      </article>
    );
  } catch (err) {
    return (
      <p className="rounded-md border border-danger/40 bg-danger/10 p-4 text-sm">
        Could not load opportunity: {(err as Error).message}
      </p>
    );
  }
}

function Field({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md border border-border bg-muted/30 p-4">
      <div className="text-xs uppercase tracking-wider text-muted-foreground">{label}</div>
      <div className="mt-1 text-sm">{value}</div>
    </div>
  );
}