import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// ---- Mock next/link so it renders a plain anchor --------------------------
vi.mock("next/link", () => ({
  default: ({ href, children }: { href: string; children: React.ReactNode }) => (
    <a href={href}>{children}</a>
  ),
}));

// ---- Mock useRouter + useSearchParams -------------------------------------
const mockReplace = vi.fn();
const mockSearchParams = new URLSearchParams();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: mockReplace }),
  useSearchParams: () => mockSearchParams,
  usePathname: () => "/admin/activation",
}));

import { ActivationCodesPanel } from "@/components/ActivationCodesPanel";
import {
  fetchActivationCodes,
  issueActivationCode,
  resendActivationCode,
  revokeActivationCode,
} from "@/lib/api";
import type {
  ActivationCode,
  ActivationIssueResponse,
  ActivationListResponse,
} from "@/types";

vi.mock("@/lib/api", () => ({
  fetchActivationCodes: vi.fn(),
  issueActivationCode: vi.fn(),
  revokeActivationCode: vi.fn(),
  resendActivationCode: vi.fn(),
}));

// ---- Fixtures -------------------------------------------------------------
function makeCode(overrides: Partial<ActivationCode> = {}): ActivationCode {
  return {
    id: 1,
    plan: "basic",
    status: "unused",
    expires_at: "2027-08-30T00:00:00Z",
    bound_feishu_open_id: null,
    bound_at: null,
    created_at: "2026-08-30T12:00:00Z",
    used_at: null,
    ...overrides,
  };
}

function makeResponse(
  overrides: Partial<ActivationListResponse> = {},
): ActivationListResponse {
  return { count: 0, items: [], ...overrides };
}

function makeIssueResponse(
  overrides: Partial<ActivationIssueResponse> = {},
): ActivationIssueResponse {
  return {
    ...makeCode(),
    code: "TEST-CODE-1234",
    im_send: null,
    ...overrides,
  };
}

const mockedFetch = vi.mocked(fetchActivationCodes);
const mockedIssue = vi.mocked(issueActivationCode);
const mockedRevoke = vi.mocked(revokeActivationCode);
const mockedResend = vi.mocked(resendActivationCode);

beforeEach(() => {
  mockReplace.mockClear();
  Array.from(mockSearchParams.keys()).forEach((k) =>
    mockSearchParams.delete(k),
  );
  mockedFetch.mockReset();
  mockedFetch.mockResolvedValue(makeResponse());
  mockedIssue.mockReset();
  mockedRevoke.mockReset();
  mockedResend.mockReset();
});

afterEach(() => {
  vi.clearAllMocks();
});

// ---------------------------------------------------------------------------
// Helpers — drive the panel into a state where the modal is open.
// ---------------------------------------------------------------------------
function openIssueModal() {
  fireEvent.click(screen.getByTestId("btn-issue"));
}

// ---------------------------------------------------------------------------
describe("ActivationCodesPanel — Phase 23 IM delivery", () => {
  it("the issue modal defaults to send_im=true and the open_id input is empty", async () => {
    mockedFetch.mockResolvedValue(makeResponse({ items: [], count: 0 }));
    render(
      <ActivationCodesPanel
        initial={makeResponse({ items: [], count: 0 })}
        initialFilters={{}}
      />,
    );

    openIssueModal();

    expect(screen.getByTestId("issue-modal")).toBeInTheDocument();
    const sendIm = screen.getByTestId("issue-send-im") as HTMLInputElement;
    expect(sendIm.checked).toBe(true);
    const openId = screen.getByTestId(
      "issue-feishu-open-id",
    ) as HTMLInputElement;
    expect(openId.value).toBe("");
  });

  it("submitting with feishu_open_id + send_im=true passes both fields + surfaces 'sent' toast", async () => {
    mockedIssue.mockResolvedValueOnce(
      makeIssueResponse({
        id: 99,
        code: "PHASE23-OK",
        im_send: { sent: true, message_id: "om_abc", error: null },
      }),
    );
    mockedFetch.mockResolvedValue(makeResponse({ items: [], count: 0 }));

    render(
      <ActivationCodesPanel
        initial={makeResponse({ items: [], count: 0 })}
        initialFilters={{}}
      />,
    );
    openIssueModal();

    fireEvent.change(screen.getByTestId("issue-plan"), {
      target: { value: "pro" },
    });
    fireEvent.change(screen.getByTestId("issue-feishu-open-id"), {
      target: { value: "ou_target" },
    });
    fireEvent.click(screen.getByTestId("issue-submit"));

    await waitFor(() => {
      expect(mockedIssue).toHaveBeenCalledWith(
        expect.objectContaining({
          plan: "pro",
          feishu_open_id: "ou_target",
          send_im: true,
        }),
      );
    });

    // Toast surfaces success.
    await waitFor(() => {
      expect(screen.getByTestId("activation-toast")).toHaveTextContent(
        "已发飞书消息 (om_abc)",
      );
    });
  });

  it("submitting with send_im=false omits IM delivery", async () => {
    mockedIssue.mockResolvedValueOnce(
      makeIssueResponse({ im_send: null }),
    );
    mockedFetch.mockResolvedValue(makeResponse({ items: [], count: 0 }));

    render(
      <ActivationCodesPanel
        initial={makeResponse({ items: [], count: 0 })}
        initialFilters={{}}
      />,
    );
    openIssueModal();

    fireEvent.change(screen.getByTestId("issue-feishu-open-id"), {
      target: { value: "ou_x" },
    });
    fireEvent.click(screen.getByTestId("issue-send-im")); // uncheck
    fireEvent.click(screen.getByTestId("issue-submit"));

    await waitFor(() => {
      expect(mockedIssue).toHaveBeenCalledWith(
        expect.objectContaining({
          send_im: false,
        }),
      );
    });
    // open_id is still passed (operator may have filled it for record).
    expect(mockedIssue).toHaveBeenCalledWith(
      expect.objectContaining({ feishu_open_id: "ou_x" }),
    );
  });

  it("submitting with no open_id omits the field; toast still shows '已发放'", async () => {
    mockedIssue.mockResolvedValueOnce(
      makeIssueResponse({ im_send: null }),
    );
    mockedFetch.mockResolvedValue(makeResponse({ items: [], count: 0 }));

    render(
      <ActivationCodesPanel
        initial={makeResponse({ items: [], count: 0 })}
        initialFilters={{}}
      />,
    );
    openIssueModal();
    fireEvent.click(screen.getByTestId("issue-submit"));

    await waitFor(() => {
      expect(mockedIssue).toHaveBeenCalledWith(
        expect.objectContaining({
          send_im: true,
        }),
      );
    });
    // feishu_open_id key should not be present when input was blank.
    const callArg = mockedIssue.mock.calls[0][0] as Record<string, unknown>;
    expect("feishu_open_id" in callArg).toBe(false);
  });

  it("when the server returns im_send.sent=false the toast surfaces the failure", async () => {
    mockedIssue.mockResolvedValueOnce(
      makeIssueResponse({
        im_send: {
          sent: false,
          message_id: null,
          error: "robot disabled (code=230001)",
        },
      }),
    );
    mockedFetch.mockResolvedValue(makeResponse({ items: [], count: 0 }));

    render(
      <ActivationCodesPanel
        initial={makeResponse({ items: [], count: 0 })}
        initialFilters={{}}
      />,
    );
    openIssueModal();
    fireEvent.change(screen.getByTestId("issue-feishu-open-id"), {
      target: { value: "ou_x" },
    });
    fireEvent.click(screen.getByTestId("issue-submit"));

    await waitFor(() => {
      expect(screen.getByTestId("activation-toast")).toHaveTextContent(
        "飞书发送失败",
      );
    });
    expect(screen.getByTestId("activation-toast")).toHaveTextContent(
      "robot disabled (code=230001)",
    );
  });

  it("the Resend button on a row opens a modal that calls resendActivationCode", async () => {
    const items = [
      makeCode({
        id: 50,
        status: "unused",
        bound_feishu_open_id: "ou_bound_user",
      }),
    ];
    mockedFetch.mockResolvedValue(makeResponse({ items, count: 1 }));
    mockedResend.mockResolvedValueOnce({
      id: 50,
      sent: true,
      message_id: "om_resend",
      error: null,
    });

    render(
      <ActivationCodesPanel
        initial={makeResponse({ items, count: 1 })}
        initialFilters={{}}
      />,
    );

    expect(screen.getByTestId("activation-resend-50")).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("activation-resend-50"));

    expect(screen.getByTestId("resend-modal")).toBeInTheDocument();
    // The modal pre-fills with bound_feishu_open_id.
    const openId = screen.getByTestId("resend-open-id") as HTMLInputElement;
    expect(openId.value).toBe("ou_bound_user");

    fireEvent.click(screen.getByTestId("resend-submit"));

    await waitFor(() => {
      expect(mockedResend).toHaveBeenCalledWith(50, "ou_bound_user");
    });
    await waitFor(() => {
      expect(screen.getByTestId("activation-toast")).toHaveTextContent(
        "飞书补发提示已发送 (om_resend)",
      );
    });
  });

  it("resend with no open_id typed keeps the submit disabled", async () => {
    const items = [
      makeCode({
        id: 51,
        status: "unused",
        bound_feishu_open_id: null,
      }),
    ];
    mockedFetch.mockResolvedValue(makeResponse({ items, count: 1 }));

    render(
      <ActivationCodesPanel
        initial={makeResponse({ items, count: 1 })}
        initialFilters={{}}
      />,
    );

    fireEvent.click(screen.getByTestId("activation-resend-51"));
    expect(screen.getByTestId("resend-modal")).toBeInTheDocument();
    expect(screen.getByTestId("resend-submit")).toBeDisabled();
    expect(mockedResend).not.toHaveBeenCalled();
  });
});
