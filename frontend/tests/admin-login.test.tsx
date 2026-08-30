import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// ---- Mock next/navigation ------------------------------------------------
const replace = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace }),
  usePathname: () => "/admin/login",
}));

import AdminLoginPage from "@/app/admin/login/page";
import { clearWebhookSecret, getWebhookSecret } from "@/lib/auth";

beforeEach(() => {
  replace.mockReset();
  clearWebhookSecret();
});

afterEach(() => {
  clearWebhookSecret();
});

describe("AdminLoginPage", () => {
  it("renders the title, secret input, and submit button", () => {
    render(<AdminLoginPage />);
    expect(screen.getByTestId("admin-login-page")).toBeInTheDocument();
    expect(screen.getByTestId("admin-login-secret")).toBeInTheDocument();
    expect(screen.getByTestId("admin-login-submit")).toBeInTheDocument();
  });

  it("submit button is disabled while the input is empty", () => {
    render(<AdminLoginPage />);
    const btn = screen.getByTestId("admin-login-submit") as HTMLButtonElement;
    expect(btn.disabled).toBe(true);
  });

  it("storing the secret then submitting routes to /admin/content-opportunities", async () => {
    render(<AdminLoginPage />);
    fireEvent.input(screen.getByTestId("admin-login-secret"), {
      target: { value: "the-shared-secret" },
    });
    fireEvent.click(screen.getByTestId("admin-login-submit"));

    await waitFor(() =>
      expect(replace).toHaveBeenCalledWith("/admin/content-opportunities"),
    );
    // sessionStorage now holds the trimmed secret.
    expect(getWebhookSecret()).toBe("the-shared-secret");
  });

  it("trims whitespace before storing", async () => {
    render(<AdminLoginPage />);
    fireEvent.input(screen.getByTestId("admin-login-secret"), {
      target: { value: "   spaced-secret   " },
    });
    fireEvent.click(screen.getByTestId("admin-login-submit"));

    await waitFor(() =>
      expect(replace).toHaveBeenCalledWith("/admin/content-opportunities"),
    );
    expect(getWebhookSecret()).toBe("spaced-secret");
  });
});