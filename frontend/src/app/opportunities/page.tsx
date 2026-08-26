import { OpportunitiesSection } from "@/components/OpportunitiesSection";

export default function OpportunitiesPage() {
  return (
    <main className="container py-10">
      <header>
        <h1 className="text-3xl font-semibold">All Opportunities</h1>
        <p className="text-sm text-muted-foreground">Sorted by total score, descending.</p>
      </header>
      <OpportunitiesSection showAll />
    </main>
  );
}