import { fetchContentOpportunity } from "@/lib/api";
import { ContentOpportunityDetail } from "@/components/ContentOpportunityDetail";
import type { ContentOpportunity } from "@/types";

/**
 * Phase 18 — admin Content Opportunity detail page.
 *
 * Server component: fetches one row by id, hands it to the client-side
 * detail panel. The 404 path is split into its own banner so the
 * operator gets a clear "not found" instead of a generic error.
 */
export default async function ContentOpportunityDetailPage({
  params,
}: {
  params?: { id?: string };
}) {
  const idStr = params?.id ?? "";
  const idNum = Number.parseInt(idStr, 10);
  if (!Number.isFinite(idNum)) {
    return (
      <main
        className="container py-10"
        data-testid="co-detail-invalid-id"
      >
        <p className="rounded-md border border-danger/40 bg-danger/10 p-4 text-sm">
          无效的 ID:{idStr || "(空)"}
        </p>
      </main>
    );
  }

  let co: ContentOpportunity | null = null;
  let notFound = false;
  let errored = false;
  try {
    co = await fetchContentOpportunity(idNum);
  } catch (err) {
    const msg = (err as Error).message;
    notFound = msg.includes("404") || msg.includes("not found");
    errored = !notFound;
  }

  return (
    <main className="container py-10" data-testid="co-detail-page">
      {co ? (
        <ContentOpportunityDetail initial={co} />
      ) : notFound ? (
        <p
          className="rounded-md border border-danger/40 bg-danger/10 p-4 text-sm"
          data-testid="co-detail-not-found"
        >
          未找到 ID #{idNum} 的 ContentOpportunity(可能已被删除)。
        </p>
      ) : (
        <p
          className="rounded-md border border-danger/40 bg-danger/10 p-4 text-sm"
          data-testid="co-detail-error"
        >
          加载失败:后端不可达或 webhook secret 无效。检查 docker compose +{" "}
          sessionStorage。
        </p>
      )}
      {errored && (
        <p className="sr-only" data-testid="co-detail-errored-marker">
          errored
        </p>
      )}
    </main>
  );
}