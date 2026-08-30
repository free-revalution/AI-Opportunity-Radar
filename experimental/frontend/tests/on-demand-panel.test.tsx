import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// ---- Mock the api module ---------------------------------------------------
const createOnDemandResearch = vi.fn();
const fetchOnDemandRecent = vi.fn();
const fetchOnDemandDetail = vi.fn();

vi.mock("@/lib/api", () => ({
  createOnDemandResearch: (...args: unknown[]) =>
    (createOnDemandResearch as (...args: unknown[]) => unknown)(...args),
  fetchOnDemandRecent: (...args: unknown[]) =>
    (fetchOnDemandRecent as (...args: unknown[]) => unknown)(...args),
  fetchOnDemandDetail: (...args: unknown[]) =>
    (fetchOnDemandDetail as (...args: unknown[]) => unknown)(...args),
}));

import { OnDemandPanel } from "@/components/OnDemandPanel";
import type {
  OnDemandCreateResponse,
  OnDemandDetailResponse,
  OnDemandListResponse,
} from "@/types";

// ---- Fixtures --------------------------------------------------------------
const EMPTY_LIST: OnDemandListResponse = {
  generated_at: "2026-08-27T10:00:00Z",
  items: [],
  total: 0,
};

function makeCreateResponse(
  overrides: Partial<OnDemandCreateResponse> = {},
): OnDemandCreateResponse {
  return {
    opportunity_id: 100,
    opportunity_title: "https://example.com/ai-product",
    opportunity_slug: "on-demand-abc",
    job_id: 7,
    status: "completed",
    recommendation: "recommend",
    confidence: 0.78,
    sources_count: 3,
    executive_summary: "mock executive summary",
    order_id: null,
    ...overrides,
  };
}

function makeDetailResponse(
  overrides: Partial<OnDemandDetailResponse> = {},
): OnDemandDetailResponse {
  return {
    job_id: 7,
    opportunity_id: 100,
    opportunity_title: "https://example.com/ai-product",
    status: "completed",
    recommendation: "recommend",
    confidence: 0.78,
    sources_count: 3,
    error: null,
    started_at: "2026-08-27T10:00:00Z",
    completed_at: "2026-08-27T10:01:00Z",
    seed_url: "https://example.com/ai-product",
    seed_topic: null,
    report: {
      id: "r1",
      opportunity_id: "100",
      executive_summary: "mock exec",
      market_analysis: "mock market",
      competition_analysis: "mock competition",
      china_analysis: "mock china",
      monetization_analysis: "mock monetization",
      mvp_analysis: "mock mvp",
      risk_analysis: "mock risk",
      recommendation: "recommend",
      confidence: 0.78,
      sources: [{ url: "https://example.com", title: "example" }],
    },
    ...overrides,
  };
}

// ---- Tests -----------------------------------------------------------------
describe("OnDemandPanel", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  it("renders the empty state when no on-demand jobs exist", () => {
    render(<OnDemandPanel initialList={EMPTY_LIST} />);
    expect(screen.getByTestId("on-demand-empty")).toBeInTheDocument();
  });

  it("toggles between URL and topic seed inputs", () => {
    render(<OnDemandPanel initialList={EMPTY_LIST} />);

    // URL mode is the default.
    expect(screen.getByTestId("seed-url-input")).toBeInTheDocument();
    expect(screen.queryByTestId("seed-topic-input")).toBeNull();

    // Click topic toggle → URL input hides, topic input appears.
    fireEvent.click(screen.getByTestId("seed-toggle-topic"));
    expect(screen.getByTestId("seed-topic-input")).toBeInTheDocument();
    expect(screen.queryByTestId("seed-url-input")).toBeNull();

    // Back to URL.
    fireEvent.click(screen.getByTestId("seed-toggle-url"));
    expect(screen.getByTestId("seed-url-input")).toBeInTheDocument();
    expect(screen.queryByTestId("seed-topic-input")).toBeNull();
  });

  it("hides the order fields until the attach-order checkbox is checked", () => {
    render(<OnDemandPanel initialList={EMPTY_LIST} />);
    expect(screen.queryByTestId("order-fields")).toBeNull();

    fireEvent.click(screen.getByTestId("attach-order-toggle"));
    expect(screen.getByTestId("order-fields")).toBeInTheDocument();
    expect(screen.getByTestId("order-customer-name-input")).toBeInTheDocument();
    expect(screen.getByTestId("order-amount-input")).toBeInTheDocument();
  });

  it("blocks submission with a friendly error when seed is empty", async () => {
    render(<OnDemandPanel initialList={EMPTY_LIST} />);
    fireEvent.click(screen.getByTestId("on-demand-submit"));
    await waitFor(() =>
      expect(screen.getByTestId("on-demand-error")).toHaveTextContent(
        "请输入 URL",
      ),
    );
    expect(createOnDemandResearch).not.toHaveBeenCalled();
  });

  it("blocks submission with order fields when customer name is empty", async () => {
    render(<OnDemandPanel initialList={EMPTY_LIST} />);
    fireEvent.change(screen.getByTestId("seed-url-input"), {
      target: { value: "https://example.com" },
    });
    fireEvent.click(screen.getByTestId("attach-order-toggle"));
    fireEvent.click(screen.getByTestId("on-demand-submit"));
    await waitFor(() =>
      expect(screen.getByTestId("on-demand-error")).toHaveTextContent("客户姓名"),
    );
    expect(createOnDemandResearch).not.toHaveBeenCalled();
  });

  it("submits a URL-only request and renders the inline report", async () => {
    const created = makeCreateResponse();
    const detail = makeDetailResponse();
    createOnDemandResearch.mockResolvedValueOnce(created);
    fetchOnDemandDetail.mockResolvedValueOnce(detail);
    fetchOnDemandRecent.mockResolvedValueOnce({
      generated_at: "2026-08-27T10:02:00Z",
      items: [
        {
          job_id: 7,
          opportunity_id: 100,
          status: "completed",
          recommendation: "recommend",
          confidence: 0.78,
          sources_count: 3,
          error: null,
          seed_url: "https://example.com/ai-product",
          seed_topic: null,
          executive_summary: "mock exec",
          started_at: "2026-08-27T10:00:00Z",
          completed_at: "2026-08-27T10:01:00Z",
        },
      ],
      total: 1,
    });

    render(<OnDemandPanel initialList={EMPTY_LIST} />);
    fireEvent.change(screen.getByTestId("seed-url-input"), {
      target: { value: "https://example.com/ai-product" },
    });
    fireEvent.click(screen.getByTestId("on-demand-submit"));

    await waitFor(() => expect(createOnDemandResearch).toHaveBeenCalledTimes(1));
    expect(createOnDemandResearch).toHaveBeenCalledWith({
      url: "https://example.com/ai-product",
    });

    await waitFor(() =>
      expect(screen.getByTestId("report-viewer")).toBeInTheDocument(),
    );
    expect(screen.getByTestId("report-recommendation")).toHaveTextContent(
      "推荐",
    );
    expect(screen.getByTestId("report-section-exec")).toHaveTextContent(
      "mock exec",
    );
    expect(screen.getByTestId("report-section-market")).toHaveTextContent(
      "mock market",
    );
    expect(screen.getByTestId("report-sources")).toBeInTheDocument();
    // Recent list refresh fired.
    expect(fetchOnDemandRecent).toHaveBeenCalled();
  });

  it("submits a topic-only request when topic mode is active", async () => {
    createOnDemandResearch.mockResolvedValueOnce(
      makeCreateResponse({
        seed_topic: "AI 法律合同审核",
        opportunity_title: "AI 法律合同审核",
      }),
    );
    fetchOnDemandDetail.mockResolvedValueOnce(
      makeDetailResponse({
        seed_url: null,
        seed_topic: "AI 法律合同审核",
        opportunity_title: "AI 法律合同审核",
        report: {
          ...makeDetailResponse().report!,
        },
      }),
    );
    fetchOnDemandRecent.mockResolvedValueOnce(EMPTY_LIST);

    render(<OnDemandPanel initialList={EMPTY_LIST} />);
    fireEvent.click(screen.getByTestId("seed-toggle-topic"));
    fireEvent.change(screen.getByTestId("seed-topic-input"), {
      target: { value: "AI 法律合同审核" },
    });
    fireEvent.click(screen.getByTestId("on-demand-submit"));

    await waitFor(() => expect(createOnDemandResearch).toHaveBeenCalledTimes(1));
    expect(createOnDemandResearch).toHaveBeenCalledWith({
      topic: "AI 法律合同审核",
    });
  });

  it("sends the order fields when the attach-order toggle is on", async () => {
    createOnDemandResearch.mockResolvedValueOnce(
      makeCreateResponse({ order_id: 88 }),
    );
    fetchOnDemandDetail.mockResolvedValueOnce(makeDetailResponse());
    fetchOnDemandRecent.mockResolvedValueOnce(EMPTY_LIST);

    render(<OnDemandPanel initialList={EMPTY_LIST} />);
    fireEvent.change(screen.getByTestId("seed-url-input"), {
      target: { value: "https://example.com/p" },
    });
    fireEvent.click(screen.getByTestId("attach-order-toggle"));
    fireEvent.change(screen.getByTestId("order-customer-name-input"), {
      target: { value: "李四" },
    });
    fireEvent.change(screen.getByTestId("order-customer-contact-input"), {
      target: { value: "wechat:lisi" },
    });
    fireEvent.change(screen.getByTestId("order-amount-input"), {
      target: { value: "299" },
    });
    fireEvent.change(screen.getByTestId("order-notes-input"), {
      target: { value: "first paid" },
    });
    fireEvent.click(screen.getByTestId("on-demand-submit"));

    await waitFor(() =>
      expect(createOnDemandResearch).toHaveBeenCalledWith(
        expect.objectContaining({
          url: "https://example.com/p",
          customer_name: "李四",
          customer_contact: "wechat:lisi",
          amount_cny: 299,
          channel: "wechat",
          notes: "first paid",
        }),
      ),
    );
    await waitFor(() =>
      expect(screen.getByTestId("result-order-chip")).toHaveTextContent(
        "订单 #88",
      ),
    );
  });

  it("renders one row per item in the recent list", () => {
    render(
      <OnDemandPanel
        initialList={{
          generated_at: "2026-08-27T10:00:00Z",
          total: 2,
          items: [
            {
              job_id: 1,
              opportunity_id: 11,
              status: "completed",
              recommendation: "recommend",
              confidence: 0.9,
              sources_count: 4,
              error: null,
              seed_url: "https://a.example",
              seed_topic: null,
              executive_summary: "summary a",
              started_at: "2026-08-27T09:00:00Z",
              completed_at: "2026-08-27T09:01:00Z",
            },
            {
              job_id: 2,
              opportunity_id: 12,
              status: "running",
              recommendation: null,
              confidence: 0,
              sources_count: 0,
              error: null,
              seed_url: null,
              seed_topic: "topic b",
              executive_summary: null,
              started_at: "2026-08-27T09:02:00Z",
              completed_at: null,
            },
          ],
        }}
      />,
    );
    expect(screen.getByTestId("on-demand-row-1")).toBeInTheDocument();
    expect(screen.getByTestId("on-demand-row-2")).toBeInTheDocument();
    expect(screen.queryByTestId("on-demand-empty")).toBeNull();
  });

  it("calls fetchOnDemandRecent when the refresh button is clicked", async () => {
    fetchOnDemandRecent.mockResolvedValueOnce(EMPTY_LIST);

    render(<OnDemandPanel initialList={EMPTY_LIST} />);
    fireEvent.click(screen.getByTestId("on-demand-refresh"));

    await waitFor(() => expect(fetchOnDemandRecent).toHaveBeenCalledWith(20));
  });

  it("surfaces an inline error when createOnDemandResearch throws", async () => {
    createOnDemandResearch.mockRejectedValueOnce(
      new Error("API 422 ValidationError"),
    );

    render(<OnDemandPanel initialList={EMPTY_LIST} />);
    fireEvent.change(screen.getByTestId("seed-url-input"), {
      target: { value: "https://example.com/x" },
    });
    fireEvent.click(screen.getByTestId("on-demand-submit"));

    await waitFor(() =>
      expect(screen.getByTestId("on-demand-error")).toHaveTextContent(
        "API 422",
      ),
    );
    expect(screen.queryByTestId("report-viewer")).toBeNull();
  });
});