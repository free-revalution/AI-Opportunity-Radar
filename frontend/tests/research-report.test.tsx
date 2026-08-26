import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

const fetchResearch = vi.fn();
vi.mock("@/lib/api", () => ({
  fetchResearch: (id: string) => fetchResearch(id),
}));

import { ResearchReport } from "@/components/ResearchReport";

const FULL_REPORT = {
  id: "r-1",
  opportunity_id: "op-1",
  executive_summary: "Strong demand from indie devs.",
  market_analysis: "$2B TAM, growing 18% YoY.",
  competition_analysis: "Two entrenched incumbents.",
  china_analysis: "Weak local presence.",
  monetization_analysis: "$19/mo SaaS is the band.",
  mvp_analysis: "Ship in 30 days with 1 engineer.",
  risk_analysis: "Regulatory risk is the top concern.",
  recommendation: "recommend" as const,
  confidence: 0.78,
  sources: [
    { url: "https://example.com/a", title: "Market report A" },
    { url: "https://example.com/b", title: "" },
  ],
};

describe("ResearchReport", () => {
  it("renders all seven sections", async () => {
    fetchResearch.mockResolvedValueOnce(FULL_REPORT);
    render(await ResearchReport({ id: "op-1" }));
    expect(screen.getByTestId("research-report")).toBeInTheDocument();
    expect(screen.getByTestId("research-section-executive-summary")).toBeInTheDocument();
    expect(screen.getByTestId("research-section-market-analysis")).toBeInTheDocument();
    expect(screen.getByTestId("research-section-competition")).toBeInTheDocument();
    expect(screen.getByTestId("research-section-china-market")).toBeInTheDocument();
    expect(screen.getByTestId("research-section-monetization")).toBeInTheDocument();
    expect(screen.getByTestId("research-section-mvp-plan")).toBeInTheDocument();
    expect(screen.getByTestId("research-section-risk-analysis")).toBeInTheDocument();
  });

  it("renders the recommendation chip with the correct tone", async () => {
    fetchResearch.mockResolvedValueOnce(FULL_REPORT);
    render(await ResearchReport({ id: "op-1" }));
    // The header should contain the recommendation label
    expect(screen.getByText(/Recommended/)).toBeInTheDocument();
  });

  it("renders the confidence bar at the expected percentage", async () => {
    fetchResearch.mockResolvedValueOnce(FULL_REPORT);
    render(await ResearchReport({ id: "op-1" }));
    expect(screen.getByText("78%")).toBeInTheDocument();
  });

  it("renders the sources list when present", async () => {
    fetchResearch.mockResolvedValueOnce(FULL_REPORT);
    render(await ResearchReport({ id: "op-1" }));
    const sources = screen.getByTestId("research-sources");
    expect(sources).toHaveTextContent("Sources (2)");
    const anchors = sources.querySelectorAll("a");
    expect(anchors.length).toBe(2);
    expect(anchors[0]?.getAttribute("href")).toBe("https://example.com/a");
  });

  it("renders the pending fallback when the API returns pending=true", async () => {
    fetchResearch.mockResolvedValueOnce({
      ...FULL_REPORT,
      executive_summary: "",
      market_analysis: "",
      competition_analysis: "",
      china_analysis: "",
      monetization_analysis: "",
      mvp_analysis: "",
      risk_analysis: "",
      sources: [],
      pending: true,
    });
    render(await ResearchReport({ id: "op-2" }));
    expect(screen.getByTestId("research-pending")).toBeInTheDocument();
    expect(screen.getByText(/Research pending/)).toBeInTheDocument();
  });

  it("shows an error message when the fetch throws", async () => {
    fetchResearch.mockRejectedValueOnce(new Error("HTTP 502"));
    render(await ResearchReport({ id: "op-3" }));
    expect(
      screen.getByText(/Research report unavailable: HTTP 502/),
    ).toBeInTheDocument();
  });
});
