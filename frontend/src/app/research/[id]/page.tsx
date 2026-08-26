import { ResearchReport } from "@/components/ResearchReport";

export default async function ResearchPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  return (
    <main className="container py-10">
      <ResearchReport id={id} />
    </main>
  );
}