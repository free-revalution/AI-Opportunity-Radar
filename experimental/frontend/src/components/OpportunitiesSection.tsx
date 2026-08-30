import { OpportunityCard } from "./OpportunityCard";
import { fetchOpportunities } from "@/lib/api";

export async function OpportunitiesSection({ showAll = false }: { showAll?: boolean }) {
  const data = await fetchOpportunities();
  const items = showAll ? data.items : data.items.slice(0, 5);

  if (items.length === 0) {
    return (
      <p className="mt-8 rounded-md border border-border bg-muted/30 p-6 text-sm text-muted-foreground">
        No opportunities yet — the daily discovery pipeline has not produced any signals.
      </p>
    );
  }

  return (
    <div className="mt-8 grid gap-4 md:grid-cols-2 xl:grid-cols-3">
      {items.map((opp) => (
        <OpportunityCard key={opp.id} opportunity={opp} />
      ))}
    </div>
  );
}