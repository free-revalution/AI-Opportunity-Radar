import { OpportunityDetail } from "@/components/OpportunityDetail";

export default async function OpportunityDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  return (
    <main className="container py-10">
      <OpportunityDetail id={id} />
    </main>
  );
}