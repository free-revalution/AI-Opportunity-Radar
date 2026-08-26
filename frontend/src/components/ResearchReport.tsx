import { fetchResearch } from "@/lib/api";
import { formatScore, recommendationLabel } from "@/lib/utils";

export async function ResearchReport({ id }: { id: string }) {
  try {
    const report = await fetchResearch(id);
    const rec = recommendationLabel(report.recommendation);
    return (
      <article className="glass rounded-xl p-8">
        <header className="flex items-start justify-between gap-6">
          <div>
            <span className="chip-accent">Deep Research</span>
            <h1 className="mt-2 text-3xl font-semibold">{report.opportunity_id}</h1>
          </div>
          <div className="text-right">
            <span className={rec.cls}>{rec.label}</span>
            <div className="mt-2 text-sm text-muted-foreground">
              Confidence: {formatScore(report.confidence)}
            </div>
          </div>
        </header>

        {report.executive_summary && (
          <section className="mt-8">
            <h2 className="text-lg font-semibold">Executive Summary</h2>
            <p className="mt-2 text-muted-foreground">{report.executive_summary}</p>
          </section>
        )}

        {report.problem && (
          <section className="mt-6">
            <h2 className="text-lg font-semibold">Problem</h2>
            <p className="mt-2 text-muted-foreground">{report.problem}</p>
          </section>
        )}

        {report.competitors && report.competitors.length > 0 && (
          <section className="mt-6">
            <h2 className="text-lg font-semibold">Competitors</h2>
            <ul className="mt-2 space-y-2 text-sm">
              {report.competitors.map((c) => (
                <li key={c.name} className="rounded-md border border-border bg-muted/30 p-3">
                  <div className="font-medium">{c.name}</div>
                  {c.price && <div className="text-muted-foreground">Price: {c.price}</div>}
                  {c.weakness && <div className="text-muted-foreground">Weakness: {c.weakness}</div>}
                </li>
              ))}
            </ul>
          </section>
        )}
      </article>
    );
  } catch (err) {
    return (
      <p className="rounded-md border border-danger/40 bg-danger/10 p-4 text-sm">
        Research report unavailable: {(err as Error).message}
      </p>
    );
  }
}