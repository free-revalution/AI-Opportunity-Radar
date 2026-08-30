import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// ---- Mock next/link so it renders a plain anchor --------------------------
vi.mock("next/link", () => ({
  default: ({ href, children }: { href: string; children: React.ReactNode }) => (
    <a href={href}>{children}</a>
  ),
}));

// Stub window.location.href so the 🛡️ 待复核 button click can be observed
// without actually navigating jsdom.
const originalLocation = window.location;
beforeEach(() => {
  Object.defineProperty(window, "location", {
    value: { ...originalLocation, href: "" },
    writable: true,
  });
});
afterEach(() => {
  Object.defineProperty(window, "location", {
    value: originalLocation,
    writable: true,
  });
});

import { DashboardPanel } from "@/components/DashboardPanel";
import type {
  DashboardActivityItem,
  DashboardResponse,
} from "@/types";

// ---- Fixtures -------------------------------------------------------------
function makeActivity(
  overrides: Partial<DashboardActivityItem> = {},
): DashboardActivityItem {
  return {
    id: 1,
    actor_type: "webhook",
    actor_id: "webhook",
    action: "content_opportunity_transition",
    resource_type: "content_opportunity",
    resource_id: "42",
    result: "success",
    metadata_json: { from: "draft", to: "approved" },
    created_at: "2026-08-30T14:35:00Z",
    ...overrides,
  };
}

function makeDashboard(
  overrides: Partial<DashboardResponse> = {},
): DashboardResponse {
  return {
    generated_at: "2026-08-30T14:40:00Z",
    content_opportunities: {
      total: 10,
      by_status: {
        draft: 4,
        approved: 2,
        published: 3,
        rejected: 1,
        archived: 0,
      },
      blocked_review_queue: 2,
      recent_7d_count: 7,
      new_today: 1,
    },
    signals: {
      total: 200,
      by_status: {
        discovered: 40,
        validating: 20,
        verified: 100,
        analyzing: 15,
        published: 0,
        expired: 20,
        rejected: 5,
      },
      recent_7d_count: 50,
      new_today: 5,
      verified_count: 100,
    },
    recent_activity: [],
    ...overrides,
  };
}

describe("DashboardPanel", () => {
  it("renders all 5 content-opportunity stat cards + the review-queue chip", () => {
    render(<DashboardPanel initial={makeDashboard()} />);
    expect(screen.getByTestId("admin-dashboard-panel")).toBeInTheDocument();
    expect(screen.getByTestId("stat-co-draft")).toHaveTextContent("4");
    expect(screen.getByTestId("stat-co-approved")).toHaveTextContent("2");
    expect(screen.getByTestId("stat-co-published")).toHaveTextContent("3");
    expect(screen.getByTestId("stat-co-rejected")).toHaveTextContent("1");
    expect(screen.getByTestId("stat-co-review-queue")).toHaveTextContent("2");
  });

  it("stat card links carry the right pre-filtered query string", () => {
    render(<DashboardPanel initial={makeDashboard()} />);
    expect(screen.getByTestId("stat-co-draft").getAttribute("href")).toBe(
      "/admin/content-opportunities?status=draft",
    );
    expect(screen.getByTestId("stat-co-approved").getAttribute("href")).toBe(
      "/admin/content-opportunities?status=approved",
    );
    expect(screen.getByTestId("stat-co-rejected").getAttribute("href")).toBe(
      "/admin/content-opportunities?status=rejected",
    );
    expect(screen.getByTestId("stat-co-published").getAttribute("href")).toBe(
      "/admin/content-opportunities?status=published",
    );
  });

  it("the 🛡️ 待复核 button routes to ?status=draft&compliance_blocked=true", () => {
    render(<DashboardPanel initial={makeDashboard()} />);
    fireEvent.click(screen.getByTestId("stat-co-review-queue"));
    expect(window.location.href).toBe(
      "/admin/content-opportunities?status=draft&compliance_blocked=true",
    );
  });

  it("renders the signal health stat cards with correct values", () => {
    render(<DashboardPanel initial={makeDashboard()} />);
    expect(screen.getByTestId("stat-sig-verified")).toHaveTextContent("100");
    expect(screen.getByTestId("stat-sig-recent")).toHaveTextContent("50");
    expect(screen.getByTestId("stat-sig-discovered")).toHaveTextContent("40");
    expect(screen.getByTestId("stat-sig-rejected")).toHaveTextContent("5");
  });

  it("renders the empty-state when there is no activity", () => {
    render(<DashboardPanel initial={makeDashboard({ recent_activity: [] })} />);
    expect(screen.getByTestId("activity-empty")).toBeInTheDocument();
  });

  it("renders one feed row per activity item with transition + reason + target link", () => {
    const activity = [
      makeActivity({
        id: 1,
        resource_id: "42",
        metadata_json: { from: "draft", to: "approved" },
      }),
      makeActivity({
        id: 2,
        resource_id: "7",
        metadata_json: {
          from: "draft",
          to: "rejected",
          reason: "包含违禁词",
        },
      }),
    ];
    render(
      <DashboardPanel initial={makeDashboard({ recent_activity: activity })} />,
    );
    expect(screen.getByTestId("activity-feed")).toBeInTheDocument();
    expect(screen.getByTestId("activity-row-1")).toBeInTheDocument();
    expect(screen.getByTestId("activity-row-2")).toBeInTheDocument();

    // Row 1 — no reason, target link to /admin/content-opportunities/42.
    expect(screen.getByTestId("activity-row-1")).toHaveTextContent("draft → approved");
    const target1 = screen.getByTestId("activity-target-1");
    expect(target1).toHaveTextContent("#42");
    expect(target1.getAttribute("href")).toBe(
      "/admin/content-opportunities/42",
    );
    expect(screen.queryByTestId("activity-reason-1")).toBeNull();

    // Row 2 — has reason.
    expect(screen.getByTestId("activity-row-2")).toHaveTextContent("draft → rejected");
    expect(screen.getByTestId("activity-reason-2")).toHaveTextContent("包含违禁词");
    expect(screen.getByTestId("activity-target-2").getAttribute("href")).toBe(
      "/admin/content-opportunities/7",
    );
  });

  it("renders actor_type chip and both absolute + relative time labels", () => {
    render(
      <DashboardPanel
        initial={makeDashboard({
          recent_activity: [
            makeActivity({ id: 9, created_at: "2026-08-30T14:35:00Z" }),
          ],
        })}
      />,
    );
    expect(screen.getByTestId("activity-actor-9")).toHaveTextContent("webhook");
    // Relative time label is non-empty (formatted by formatRelativeTime).
    expect(screen.getByTestId("activity-relative-9").textContent).not.toBe("");
    expect(screen.getByTestId("activity-absolute-9").textContent).not.toBe("");
  });
});