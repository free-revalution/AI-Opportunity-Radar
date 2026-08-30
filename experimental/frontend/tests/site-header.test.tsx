import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

// Stub the next/link module so it renders a plain anchor in tests.
vi.mock("next/link", () => ({
  default: ({ href, children }: { href: string; children: React.ReactNode }) => (
    <a href={href}>{children}</a>
  ),
}));

// Stub the api module so we can drive the health pill state from tests.
const fetchHealth = vi.fn();
vi.mock("@/lib/api", () => ({
  fetchHealth: () => fetchHealth(),
}));

import { SiteHeader } from "@/components/SiteHeader";

describe("SiteHeader", () => {
  it("renders the navigation links", async () => {
    fetchHealth.mockResolvedValueOnce({
      status: "healthy",
      service: "ai-opportunity-radar",
      version: "0.1.0",
      components: {},
    });
    const ui = await SiteHeader();
    render(ui);
    expect(screen.getByText("Dashboard")).toBeInTheDocument();
    expect(screen.getByText("Opportunities")).toBeInTheDocument();
    expect(screen.getByText("Settings")).toBeInTheDocument();
  });

  it("renders a healthy pill when the backend is healthy", async () => {
    fetchHealth.mockResolvedValueOnce({
      status: "healthy",
      service: "x",
      version: "0",
      components: {},
    });
    render(await SiteHeader());
    const pill = screen.getByTestId("health-pill");
    expect(pill).toHaveTextContent("healthy");
    expect(pill).toHaveClass("chip-success");
  });

  it("renders a degraded pill in the warning tone", async () => {
    fetchHealth.mockResolvedValueOnce({
      status: "degraded",
      service: "x",
      version: "0",
      components: {},
    });
    render(await SiteHeader());
    const pill = screen.getByTestId("health-pill");
    expect(pill).toHaveTextContent("degraded");
    expect(pill).toHaveClass("chip-warning");
  });

  it("renders a down pill when the backend reports down", async () => {
    fetchHealth.mockResolvedValueOnce({
      status: "down",
      service: "x",
      version: "0",
      components: {},
    });
    render(await SiteHeader());
    expect(screen.getByTestId("health-pill")).toHaveTextContent("down");
  });

  it("renders an offline pill when the backend is unreachable", async () => {
    fetchHealth.mockRejectedValueOnce(new Error("network down"));
    render(await SiteHeader());
    expect(screen.getByTestId("health-pill")).toHaveTextContent("offline");
  });
});
