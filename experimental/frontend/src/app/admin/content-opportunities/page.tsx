import {
  fetchContentOpportunities,
  type ContentOpportunityListParams,
} from "@/lib/api";
import { ContentOpportunitiesPanel } from "@/components/ContentOpportunitiesPanel";

/**
 * Phase 18 — admin Content Center list page.
 *
 * Server component: reads searchParams (Next 14 sync prop), fetches
 * both the filtered and an unfiltered snapshot for the stat cards,
 * and hands everything to <ContentOpportunitiesPanel/>.
 */
export default async function ContentOpportunitiesPage({
  searchParams,
}: {
  searchParams?: {
    status?: string;
    compliance_blocked?: string;
    signal_id?: string;
  };
}) {
  const status = searchParams?.status ?? "";
  const compliance = searchParams?.compliance_blocked ?? "";
  const signalIdStr = searchParams?.signal_id ?? "";
  const signalIdNum = signalIdStr ? Number.parseInt(signalIdStr, 10) : NaN;

  const filteredParams: ContentOpportunityListParams = {
    limit: 50,
    offset: 0,
  };
  if (status) filteredParams.status = status;
  if (compliance === "true") filteredParams.compliance_blocked = true;
  if (compliance === "false") filteredParams.compliance_blocked = false;
  if (Number.isFinite(signalIdNum)) filteredParams.signal_id = signalIdNum;

  let initialItems: Awaited<
    ReturnType<typeof fetchContentOpportunities>
  >["items"] = [];
  let initialTotal = 0;
  let initialAllItems: Awaited<
    ReturnType<typeof fetchContentOpportunities>
  >["items"] = [];
  let errored = false;

  try {
    const [filtered, all] = await Promise.all([
      fetchContentOpportunities(filteredParams),
      fetchContentOpportunities({ limit: 200 }),
    ]);
    initialItems = filtered.items;
    initialTotal = filtered.total;
    initialAllItems = all.items;
  } catch {
    errored = true;
  }

  return (
    <main
      className="container py-10"
      data-testid="content-opportunities-page"
    >
      <header className="mb-8">
        <span className="chip-accent">v2.0 · Content Center · Admin</span>
        <h1 className="mt-3 text-3xl font-semibold">内容审稿台</h1>
        <p className="mt-2 max-w-2xl text-sm text-muted-foreground">
          审阅飞书 <code className="rounded bg-muted px-1">/content</code>{" "}
          命令产出的草稿。状态机:draft → approved → published;
          任意状态可 → rejected。🛡️ 标记的草稿触发了合规拦截,需要人工复核。
        </p>
      </header>

      {errored ? (
        <p
          className="rounded-md border border-danger/40 bg-danger/10 p-4 text-sm"
          data-testid="content-opportunities-error"
        >
          加载失败:后端不可达或 webhook secret 无效。检查 docker compose 状态
          + 浏览器 sessionStorage。
        </p>
      ) : (
        <ContentOpportunitiesPanel
          initialItems={initialItems}
          initialTotal={initialTotal}
          initialStatusFilter={status}
          initialComplianceFilter={compliance}
          initialSignalId={Number.isFinite(signalIdNum) ? signalIdNum : null}
          initialAllItems={initialAllItems}
        />
      )}
    </main>
  );
}