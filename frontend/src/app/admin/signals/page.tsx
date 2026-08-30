import { fetchSignals } from "@/lib/api";
import { SignalsPanel } from "@/components/SignalsPanel";
import type { Signal } from "@/types";

/**
 * Phase 18 — admin Signal browser.
 *
 * Server component: reads searchParams (Next 14 sync prop), fetches
 * the filtered list, hands it to <SignalsPanel/> for filter / refresh
 * interactivity.
 */
export default async function SignalsPage({
  searchParams,
}: {
  searchParams?: {
    status?: string;
    min_signal_score?: string;
  };
}) {
  const status = searchParams?.status ?? "";
  const minStr = searchParams?.min_signal_score ?? "";
  const minNum = minStr ? Number.parseFloat(minStr) : NaN;

  let initialItems: Awaited<ReturnType<typeof fetchSignals>>["items"] = [];
  let initialTotal = 0;
  let errored = false;

  try {
    const data = await fetchSignals({
      limit: 50,
      offset: 0,
      status: status || undefined,
      min_signal_score: Number.isFinite(minNum) ? minNum : undefined,
    });
    initialItems = data.items;
    initialTotal = data.total;
  } catch {
    errored = true;
  }

  return (
    <main className="container py-10" data-testid="signals-page">
      <header className="mb-8">
        <span className="chip-accent">v2.0 · Signals · Admin</span>
        <h1 className="mt-3 text-3xl font-semibold">信号浏览器</h1>
        <p className="mt-2 max-w-2xl text-sm text-muted-foreground">
          浏览最近抓取的 Signal 行(原始抓取 → 信号识别后的产物)。
          按状态、最低分过滤;Phase 19 计划加「Signal → ContentOpportunity 一键创作」。
        </p>
      </header>

      {errored ? (
        <p
          className="rounded-md border border-danger/40 bg-danger/10 p-4 text-sm"
          data-testid="signals-error"
        >
          加载失败:后端不可达或 webhook secret 无效。检查 docker compose +
          sessionStorage。
        </p>
      ) : (
        <SignalsPanel
          initialItems={initialItems as Signal[]}
          initialTotal={initialTotal}
          initialStatus={status}
          initialMinScore={Number.isFinite(minNum) ? minNum : null}
        />
      )}
    </main>
  );
}