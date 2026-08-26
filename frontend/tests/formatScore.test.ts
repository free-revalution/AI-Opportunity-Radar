import { formatScore } from "@/lib/utils";
import { describe, expect, it } from "vitest";

describe("formatScore", () => {
  it("formats integer scores", () => {
    expect(formatScore(85)).toBe("85/100");
  });
  it("rounds fractional scores", () => {
    expect(formatScore(85.4)).toBe("85/100");
    expect(formatScore(85.6)).toBe("86/100");
  });
  it("handles missing input", () => {
    expect(formatScore(undefined)).toBe("—");
    expect(formatScore(null)).toBe("—");
    expect(formatScore(Number.NaN)).toBe("—");
  });
});