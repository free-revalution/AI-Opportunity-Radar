import { render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// ---- Mock next/navigation (with mutable pathname) -----------------------
const mockState = vi.hoisted(() => ({
  pathname: "/admin/content-opportunities",
  replace: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: mockState.replace }),
  usePathname: () => mockState.pathname,
}));

import { AdminGuard } from "@/components/AdminGuard";
import { clearWebhookSecret, setWebhookSecret } from "@/lib/auth";

beforeEach(() => {
  mockState.pathname = "/admin/content-opportunities";
  mockState.replace.mockReset();
  clearWebhookSecret();
});

afterEach(() => {
  clearWebhookSecret();
});

describe("AdminGuard", () => {
  it("redirects to /admin/login when no secret is set", () => {
    render(
      <AdminGuard>
        <p data-testid="secret-content">should not be visible</p>
      </AdminGuard>,
    );
    // First-paint skeleton.
    expect(screen.getByTestId("admin-guard-redirecting")).toBeInTheDocument();
    // useEffect fired → router.replace invoked.
    expect(mockState.replace).toHaveBeenCalledWith("/admin/login");
    // Children are NOT rendered in the no-secret branch.
    expect(screen.queryByTestId("secret-content")).toBeNull();
  });

  it("renders children when a secret is set", () => {
    setWebhookSecret("known-secret");
    render(
      <AdminGuard>
        <p data-testid="secret-content">hi operator</p>
      </AdminGuard>,
    );
    expect(screen.getByTestId("secret-content")).toBeInTheDocument();
    expect(screen.queryByTestId("admin-guard-redirecting")).toBeNull();
    expect(mockState.replace).not.toHaveBeenCalled();
  });

  it("/admin/login renders children even without a secret (allowlisted)", () => {
    mockState.pathname = "/admin/login";
    render(
      <AdminGuard>
        <p data-testid="login-content">login form</p>
      </AdminGuard>,
    );
    expect(screen.getByTestId("login-content")).toBeInTheDocument();
    expect(screen.queryByTestId("admin-guard-redirecting")).toBeNull();
    expect(mockState.replace).not.toHaveBeenCalled();
  });
});