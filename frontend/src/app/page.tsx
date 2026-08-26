import Link from "next/link";
import { OpportunitiesSection } from "@/components/OpportunitiesSection";

export default function LandingPage() {
  return (
    <main className="container py-16">
      <section className="max-w-3xl">
        <span className="chip-accent">AI Opportunity Radar</span>
        <h1 className="mt-4 text-5xl font-bold leading-tight tracking-tight">
          Discover the next AI business
          <br />
          <span className="text-accent">before everyone else.</span>
        </h1>
        <p className="mt-6 text-lg text-muted-foreground">
          We monitor global product launches, GitHub stars, Reddit chatter and AI blogs every day,
          score every signal, and surface the 5-10 opportunities worth your attention.
        </p>
        <div className="mt-8 flex gap-3">
          <Link
            href="/dashboard"
            className="rounded-md bg-accent px-4 py-2 text-sm font-semibold text-accent-foreground hover:opacity-90"
          >
            Open dashboard
          </Link>
          <Link
            href="/opportunities"
            className="rounded-md border border-border px-4 py-2 text-sm font-semibold hover:bg-muted"
          >
            Browse opportunities
          </Link>
        </div>
      </section>

      <section className="mt-16">
        <h2 className="text-2xl font-semibold">Today's top picks (live data from the backend)</h2>
        <p className="mt-2 text-sm text-muted-foreground">
          The list below is fetched server-side from the FastAPI backend so it stays in sync with
          every signal the pipeline produces.
        </p>
        <OpportunitiesSection />
      </section>
    </main>
  );
}