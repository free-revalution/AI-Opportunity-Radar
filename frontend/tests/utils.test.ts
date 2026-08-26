import { recommendationLabel } from "@/lib/utils";
import { describe, expect, it } from "vitest";

describe("recommendationLabel", () => {
  it("returns strongly_recommend for >=85", () => {
    expect(recommendationLabel("strongly_recommend").label).toContain("Strongly");
  });
  it("returns watch for 50-69", () => {
    expect(recommendationLabel("watch").label).toBe("Watch");
  });
  it("falls back to insufficient_data for unknown", () => {
    expect(recommendationLabel(undefined).label).toBe("Insufficient Data");
  });
});