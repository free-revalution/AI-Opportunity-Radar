import { fetchResearch } from "@/lib/api";
import {
  cn,
  formatRelativeTime,
  recommendationLabel,
  scoreBarWidth,
} from "@/lib/utils";

/**
 * Phase 7 ResearchReport viewer — renders all seven sections returned by
 * the deep-research engine. When no report exists yet the API returns a
 * `pending: true` fallback and we surface a friendly CTA.
 */
export async function ResearchReport({ id }: { id: string }) {
  let report: Awaited<ReturnType<typeof fetchResearch>> | null = null;
  let error: string | null = null;
  try {
    report = await fetchResearch(id);
  } catch (err) {
    error = (err as Error).message;
  }

  if (error) {
    return (
      <p className="rounded-md border border-danger/40 bg-danger/10 p-4 text-sm">
        Research report unavailable: {error}
      </p>
    );
  }

  if (!report) {
    return (
      <p className="rounded-md border border-border bg-muted/30 p-4 text-sm text-muted-foreground">
        Research report not found.
      </p>
    );
  }

  const rec = recommendationLabel(report.recommendation);
  const confidencePct = scoreBarWidth(
    typeof report.confidence === "number" ? report.confidence * 100 : 0,
  );

  return (
    <article className="glass space-y-8 rounded-xl p-8" data-testid="research-report">
      <header className="flex flex-wrap items-start justify-between gap-6">
        <div>
          <span className="chip-accent">Deep Research</span>
          <h1 className="mt-3 text-3xl font-semibold">
            Opportunity #{report.opportunity_id}
          </h1>
          <p className="mt-1 text-xs text-muted-foreground">
            Report ID {report.id}
          </p>
        </div>
        <div className="text-right">
          <span className={cn("chip", rec.cls)}>
            <span aria-hidden>{rec.emoji}</span> {rec.label}
          </span>
          <div className="mt-3 w-44">
            <div className="flex items-center justify-between text-xs text-muted-foreground">
              <span>Confidence</span>
              <span className="font-mono tabular-nums">
                {Math.round(confidencePct)}%
              </span>
            </div>
            <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-muted/40">
              <div
                className={cn(
                  "h-full rounded-full transition-all",
                  confidencePct >= 70
                    ? "bg-success"
                    : confidencePct >= 40
                      ? "bg-accent"
                      : "bg-warning",
                )}
                style={{ width: `${confidencePct}%` }}
              />
            </div>
          </div>
        </div>
      </header>

      {report.pending ? (
        <PendingFallback opportunityId={report.opportunity_id} />
      ) : (
        <div className="grid gap-8 md:grid-cols-2">
          <Section title="Executive Summary" body={report.executive_summary} />
          <Section title="Market Analysis" body={report.market_analysis} />
          <Section title="Competition" body={report.competition_analysis} />
          <Section title="China Market" body={report.china_analysis} />
          <Section title="Monetization" body={report.monetization_analysis} />
          <Section title="MVP Plan" body={report.mvp_analysis} />
          <Section
            title="Risk Analysis"
            body={report.risk_analysis}
            tone="danger"
          />
          {report.sources && report.sources.length > 0 && (
            <Sources items={report.sources} />
          )}
        </div>
      )}
    </article>
  );
}

function Section({
  title,
  body,
  tone,
}: {
  title: string;
  body?: string;
  tone?: "danger";
}) {
  const trimmed = (body ?? "").trim();
  return (
    <section
      className="rounded-md border border-border bg-muted/30 p-5"
      data-testid={`research-section-${slug(title)}`}
    >
      <h2 className="text-sm font-semibold uppercase tracking-wider text-muted-foreground">
        {title}
      </h2>
      {trimmed ? (
        <p
          className={cn(
            "mt-3 whitespace-pre-line text-sm leading-relaxed",
            tone === "danger" && "text-warning",
          )}
        >
          {trimmed}
        </p>
      ) : (
        <p className="mt-3 text-sm italic text-muted-foreground">
          Insufficient data.
        </p>
      )}
    </section>
  );
}

function Sources({
  items,
}: {
  items: NonNullable<Awaited<ReturnType<typeof fetchResearch>>["sources"]>;
}) {
  return (
    <section
      className="rounded-md border border-border bg-muted/30 p-5 md:col-span-2"
      data-testid="research-sources"
    >
      <h2 className="text-sm font-semibold uppercase tracking-wider text-muted-foreground">
        Sources ({items.length})
      </h2>
      <ul className="mt-3 space-y-1 text-sm">
        {items.map((s) => (
          <li key={s.url} className="truncate">
            <a
              href={s.url}
              target="_blank"
              rel="noopener noreferrer"
              className="text-accent hover:underline"
            >
              {s.title || s.url}
            </a>
            {s.title && (
              <span className="ml-2 text-xs text-muted-foreground">{s.url}</span>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}

function PendingFallback({ opportunityId }: { opportunityId: string }) {
  return (
    <div
      className="rounded-md border border-warning/40 bg-warning/5 p-6 text-sm"
      data-testid="research-pending"
    >
      <p className="font-medium text-warning">Research pending.</p>
      <p className="mt-2 text-muted-foreground">
        The deep-research worker has not produced a report for opportunity{" "}
        <span className="font-mono">{opportunityId}</span> yet. Trigger it from
        the dashboard or wait for the next scheduled run.
      </p>
    </div>
  );
}

function slug(s: string): string {
  return s.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/(^-|-$)/g, "");
}

// Suppress unused import warning for `formatRelativeTime` — kept on hand
// for future "last updated" rendering without re-importing.
void formatRelativeTime;
