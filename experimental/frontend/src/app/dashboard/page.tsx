import { NotificationHistory } from "@/components/NotificationHistory";
import { OpportunitiesSection } from "@/components/OpportunitiesSection";

export default function DashboardPage() {
  return (
    <main className="container space-y-10 py-10">
      <header className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-semibold">Today&apos;s Opportunities</h1>
          <p className="text-sm text-muted-foreground">
            Auto-curated from GitHub, Reddit, Hacker News, Product Hunt, RSS.
          </p>
        </div>
        <span className="chip-accent">live</span>
      </header>

      <OpportunitiesSection />

      <NotificationHistory />
    </main>
  );
}
