import { describe, expect, it } from "vitest";

import {
  formatRelativeTime,
  recommendationFromScore,
  scoreBarWidth,
} from "@/lib/utils";

describe("scoreBarWidth", () => {
  it("clamps negative to 0", () => {
    expect(scoreBarWidth(-10)).toBe(0);
  });
  it("clamps above 100", () => {
    expect(scoreBarWidth(250)).toBe(100);
  });
  it("returns the value for in-range inputs", () => {
    expect(scoreBarWidth(72)).toBe(72);
  });
  it("treats nullish as 0", () => {
    expect(scoreBarWidth(undefined)).toBe(0);
    expect(scoreBarWidth(null)).toBe(0);
    expect(scoreBarWidth(Number.NaN)).toBe(0);
  });
});

describe("formatRelativeTime", () => {
  const now = Date.now();
  it("returns 'just now' for under a minute", () => {
    expect(formatRelativeTime(new Date(now - 30_000).toISOString())).toBe(
      "just now",
    );
  });
  it("returns minutes ago for under an hour", () => {
    expect(formatRelativeTime(new Date(now - 5 * 60_000).toISOString())).toBe(
      "5m ago",
    );
  });
  it("returns hours ago for under a day", () => {
    expect(formatRelativeTime(new Date(now - 3 * 60 * 60_000).toISOString())).toBe(
      "3h ago",
    );
  });
  it("returns days ago for anything older", () => {
    expect(
      formatRelativeTime(new Date(now - 2 * 24 * 60 * 60_000).toISOString()),
    ).toBe("2d ago");
  });
  it("returns the em-dash for invalid input", () => {
    expect(formatRelativeTime(undefined)).toBe("—");
    expect(formatRelativeTime("not-a-date")).toBe("—");
  });
});

describe("recommendationFromScore", () => {
  it("maps >=85 to strongly_recommend", () => {
    expect(recommendationFromScore(90).value).toBe("strongly_recommend");
  });
  it("maps >=70 to recommend", () => {
    expect(recommendationFromScore(72).value).toBe("recommend");
  });
  it("maps >=55 to watch", () => {
    expect(recommendationFromScore(60).value).toBe("watch");
  });
  it("maps >0 to not_recommended", () => {
    expect(recommendationFromScore(30).value).toBe("not_recommended");
  });
  it("maps 0 or null to insufficient_data", () => {
    expect(recommendationFromScore(0).value).toBe("insufficient_data");
    expect(recommendationFromScore(null).value).toBe("insufficient_data");
  });
});
