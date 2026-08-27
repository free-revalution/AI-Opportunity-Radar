import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// ---- Mock the api module ---------------------------------------------------
const fetchContentCenter = vi.fn();
const markContentPublished = vi.fn();
const markContentSold = vi.fn();

vi.mock("@/lib/api", () => ({
  fetchContentCenter: (...args: unknown[]) =>
    (fetchContentCenter as (...args: unknown[]) => unknown)(...args),
  markContentPublished: (...args: unknown[]) =>
    (markContentPublished as (...args: unknown[]) => unknown)(...args),
  markContentSold: (...args: unknown[]) =>
    (markContentSold as (...args: unknown[]) => unknown)(...args),
}));

import { ContentCenter } from "@/components/ContentCenter";
import type { ContentCenterItem } from "@/types";

// ---- Fixtures --------------------------------------------------------------
function makeItem(overrides: Partial<ContentCenterItem> = {}): ContentCenterItem {
  return {
    opportunity: {
      id: 1,
      title: "AI 法律合同审核",
      slug: "ai-legal",
      summary: "海外律师事务所在用 LLM 自动审核合同条款。",
      total_score: 87.5,
      content_status: "generated",
      commercial_status: "qualified",
      target_customer: "中型律所",
      market_size: "100M-500M USD",
      mvp_days: 14,
      difficulty: "medium",
      monetization_model: "SaaS 订阅 49 USD/月",
      china_gap: "中国律所市场分散,微信小程序是关键渠道",
    },
    content: {
      feishu: {
        notification_id: 101,
        channel: "feishu",
        title: "飞书日报",
        body: "# 今日AI商业机会\n\n## 法律合同审核...",
        metadata: {},
        generator: "daily_report",
        format: "markdown",
        created_at: "2026-08-27T10:00:00Z",
      },
      xianyu: {
        notification_id: 102,
        channel: "xianyu",
        title: "闲鱼商品",
        body: {
          title: "100 个海外 AI 创业机会",
          price: 49,
          selling_points: ["市场分析", "MVP 拆解", "中英双语"],
        },
        metadata: {},
        generator: "xianyu_product",
        format: "json",
        created_at: "2026-08-27T10:00:01Z",
      },
      xiaohongshu: {
        notification_id: 103,
        channel: "xiaohongshu",
        title: "小红书笔记",
        body: "国外一个 AI 项目月入 5 万美元,中国还没有人做",
        metadata: {},
        generator: "xiaohongshu_post",
        format: "markdown",
        created_at: "2026-08-27T10:00:02Z",
      },
    },
    ...overrides,
  };
}

// ---- clipboard mock --------------------------------------------------------
beforeEach(() => {
  Object.assign(navigator, {
    clipboard: { writeText: vi.fn().mockResolvedValue(undefined) },
  });
});

afterEach(() => {
  vi.clearAllMocks();
});

// ---- Tests -----------------------------------------------------------------
describe("ContentCenter", () => {
  it("renders the empty state when no opportunities are returned", () => {
    render(<ContentCenter initialItems={[]} onlyQualified limit={20} />);
    expect(screen.getByTestId("content-center-empty")).toBeInTheDocument();
  });

  it("renders one row per opportunity with all 4 channels", () => {
    const item = makeItem();
    render(<ContentCenter initialItems={[item]} onlyQualified limit={20} />);

    expect(screen.getByTestId("content-center-row-1")).toBeInTheDocument();
    expect(screen.getByText("AI 法律合同审核")).toBeInTheDocument();
    // 4 channel cards per row.
    expect(screen.getByTestId("piece-feishu-1")).toBeInTheDocument();
    expect(screen.getByTestId("piece-xianyu-1")).toBeInTheDocument();
    expect(screen.getByTestId("piece-xiaohongshu-1")).toBeInTheDocument();
    // wechat_article wasn't generated → placeholder.
    expect(
      screen.getByTestId("piece-wechat_article-1-missing"),
    ).toBeInTheDocument();
  });

  it("shows the content_status badge with the right label", () => {
    render(
      <ContentCenter
        initialItems={[
          makeItem({
            opportunity: { ...makeItem().opportunity, content_status: "published" },
          }),
        ]}
        onlyQualified
        limit={20}
      />,
    );
    const status = screen.getByTestId("status-1");
    expect(status).toHaveTextContent("已发布");
  });

  it("calls markContentPublished when the button is clicked", async () => {
    markContentPublished.mockResolvedValueOnce({
      opportunity_id: 1,
      content_status: "published",
      commercial_status: "qualified",
    });

    render(
      <ContentCenter
        initialItems={[makeItem()]}
        onlyQualified
        limit={20}
      />,
    );

    fireEvent.click(screen.getByTestId("mark-published-1"));

    await waitFor(() => expect(markContentPublished).toHaveBeenCalledWith(1));
  });

  it("calls markContentSold when the sold button is clicked", async () => {
    markContentSold.mockResolvedValueOnce({
      opportunity_id: 1,
      content_status: "sold",
      commercial_status: "promising",
    });

    render(
      <ContentCenter
        initialItems={[makeItem()]}
        onlyQualified
        limit={20}
      />,
    );

    fireEvent.click(screen.getByTestId("mark-sold-1"));

    await waitFor(() => expect(markContentSold).toHaveBeenCalledWith(1));
  });

  it("disables mark-published when status is already published", () => {
    render(
      <ContentCenter
        initialItems={[
          makeItem({
            opportunity: { ...makeItem().opportunity, content_status: "published" },
          }),
        ]}
        onlyQualified
        limit={20}
      />,
    );
    const btn = screen.getByTestId("mark-published-1") as HTMLButtonElement;
    expect(btn.disabled).toBe(true);
    expect(btn).toHaveTextContent("已发布 ✓");
  });

  it("calls the clipboard API when Copy is clicked", async () => {
    const writeText = navigator.clipboard.writeText as ReturnType<typeof vi.fn>;

    render(
      <ContentCenter
        initialItems={[makeItem()]}
        onlyQualified
        limit={20}
      />,
    );

    fireEvent.click(screen.getByTestId("copy-feishu-1"));
    await waitFor(() =>
      expect(writeText).toHaveBeenCalledWith(
        "# 今日AI商业机会\n\n## 法律合同审核...",
      ),
    );
  });

  it("renders a summary line with opportunity count + channel count", () => {
    render(
      <ContentCenter
        initialItems={[
          makeItem(),
          makeItem({
            opportunity: {
              ...makeItem().opportunity,
              id: 2,
              title: "AI 客服",
            },
          }),
        ]}
        onlyQualified
        limit={20}
      />,
    );
    // 2 opps × 3 channels each (feishu, xianyu, xiaohongshu) = 6 channels.
    expect(screen.getByText(/2 个机会/)).toBeInTheDocument();
    expect(screen.getByText(/6 条已生成内容/)).toBeInTheDocument();
  });
});