import { fetchContentCenter } from "@/lib/api";

import { ContentCenter } from "@/components/ContentCenter";

/**
 * Content Center — operator console for the v2.0 generated sales copy.
 *
 * Server component: fetches the latest opportunity + content snapshot
 * and hands it to the client-side <ContentCenter/> for interactivity.
 */
export default async function ContentCenterPage({
  searchParams,
}: {
  searchParams?: { only_qualified?: string; limit?: string };
}) {
  const only_qualified = (searchParams?.only_qualified ?? "true") !== "false";
  const limit = Number.parseInt(searchParams?.limit ?? "20", 10) || 20;

  let items: Awaited<ReturnType<typeof fetchContentCenter>>["items"] = [];
  let errored = false;

  try {
    const data = await fetchContentCenter(only_qualified, limit);
    items = data.items;
  } catch {
    errored = true;
  }

  return (
    <main className="container py-10" data-testid="content-center-page">
      <header className="mb-8">
        <span className="chip-accent">v2.0 · Content Center</span>
        <h1 className="mt-3 text-3xl font-semibold">内容生产中心</h1>
        <p className="mt-2 max-w-2xl text-sm text-muted-foreground">
          系统为每个高分机会自动生成 4 个渠道的销售文案:飞书日报、闲鱼商品、小红书笔记、公众号长文。
          直接复制后到对应平台发布;发布后在这里点&ldquo;标记已发布&rdquo;,系统会自动把该机会计入已发布列表。
        </p>
      </header>

      {errored ? (
        <p
          className="rounded-md border border-danger/40 bg-danger/10 p-4 text-sm"
          data-testid="content-center-error"
        >
          加载失败:后端不可达。检查 docker compose 状态。
        </p>
      ) : (
        <ContentCenter
          initialItems={items}
          onlyQualified={only_qualified}
          limit={limit}
        />
      )}
    </main>
  );
}