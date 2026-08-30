import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ScoreBreakdown } from "@/components/ScoreBreakdown";
import type { Opportunity } from "@/types";

const baseOpp: Pick<
  Opportunity,
  | "trend_score"
  | "demand_score"
  | "monetization_score"
  | "competition_gap_score"
  | "china_gap_score"
  | "execution_score"
> = {
  trend_score: 90,
  demand_score: 70,
  monetization_score: 60,
  competition_gap_score: 40,
  china_gap_score: 20,
  execution_score: 10,
};

describe("ScoreBreakdown", () => {
  it("renders one row per sub-score with the correct weight hint", () => {
    render(<ScoreBreakdown opportunity={baseOpp} />);
    expect(screen.getByTestId("score-row-trend_score")).toBeInTheDocument();
    expect(screen.getByTestId("score-row-demand_score")).toBeInTheDocument();
    expect(screen.getByTestId("score-row-monetization_score")).toBeInTheDocument();
    expect(
      screen.getByTestId("score-row-competition_gap_score"),
    ).toBeInTheDocument();
    expect(screen.getByTestId("score-row-china_gap_score")).toBeInTheDocument();
    expect(screen.getByTestId("score-row-execution_score")).toBeInTheDocument();
  });

  it("renders the human-readable label and weight", () => {
    render(<ScoreBreakdown opportunity={baseOpp} />);
    expect(screen.getByText("Trend Velocity")).toBeInTheDocument();
    expect(screen.getByText("Demand")).toBeInTheDocument();
    // Trend weight is 0.20 per the README scoring formula.
    expect(screen.getAllByText("weight 0.20").length).toBeGreaterThanOrEqual(3);
  });

  it("formats each row value as N/100", () => {
    render(<ScoreBreakdown opportunity={baseOpp} />);
    expect(screen.getAllByText("90/100").length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText("10/100").length).toBeGreaterThanOrEqual(1);
  });

  it("renders an aria progressbar for accessibility", () => {
    render(<ScoreBreakdown opportunity={baseOpp} />);
    const bars = screen.getAllByRole("progressbar");
    expect(bars.length).toBe(6);
    expect(bars[0]?.getAttribute("aria-valuenow")).toBe("90");
    expect(bars[5]?.getAttribute("aria-valuenow")).toBe("10");
  });

  it("handles nullish sub-scores without crashing", () => {
    render(
      <ScoreBreakdown
        opportunity={{
          trend_score: undefined,
          demand_score: null,
          monetization_score: Number.NaN,
          competition_gap_score: undefined,
          china_gap_score: undefined,
          execution_score: undefined,
        }}
      />,
    );
    // 6 rows must still render
    expect(screen.getAllByRole("progressbar").length).toBe(6);
    expect(screen.getAllByText("—").length).toBeGreaterThanOrEqual(1);
  });
});
