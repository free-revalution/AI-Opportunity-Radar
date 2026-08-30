"use client";

/**
 * Phase 18 — admin login page. Sole-operator console: prompt for the
 * shared webhook secret, store it in sessionStorage, then redirect to
 * the Content Center review queue.
 *
 * We don't pre-validate the secret against a backend probe (the /api/* we
 * hit from the browser is read-only and would 401 the same way a wrong
 * secret would). The next API call will toast the failure if wrong.
 */

import { useState } from "react";
import { useRouter } from "next/navigation";

import { setWebhookSecret } from "@/lib/auth";

export default function AdminLoginPage() {
  const router = useRouter();
  const [secret, setSecret] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const trimmed = secret.trim();
  const canSubmit = trimmed.length > 0 && !submitting;

  const onSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!canSubmit) return;
    setSubmitting(true);
    setError(null);
    try {
      setWebhookSecret(trimmed);
      router.replace("/admin/content-opportunities");
    } catch (err) {
      setError((err as Error).message);
      setSubmitting(false);
    }
  };

  return (
    <main
      className="container max-w-md py-16"
      data-testid="admin-login-page"
    >
      <div className="rounded-xl border border-border bg-card/40 p-8">
        <header className="mb-6">
          <span className="chip-accent">v2.0 · Admin</span>
          <h1 className="mt-3 text-2xl font-semibold">管理员登录</h1>
          <p className="mt-2 text-xs text-muted-foreground">
            输入与后端 <code className="rounded bg-muted px-1">RADAR_WEBHOOK_SECRET</code> /{" "}
            <code className="rounded bg-muted px-1">APP_SECRET_KEY</code> 相同的 secret。
            Secret 仅存在当前浏览器 sessionStorage,关闭浏览器即失效。
          </p>
        </header>

        <form onSubmit={onSubmit} className="space-y-4">
          <label
            htmlFor="admin-login-secret"
            className="block text-xs text-muted-foreground"
          >
            <span className="mb-1 block font-medium">Webhook Secret</span>
            <input
              id="admin-login-secret"
              type="password"
              value={secret}
              onChange={(e) => setSecret(e.target.value)}
              autoFocus
              required
              autoComplete="off"
              className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm"
              data-testid="admin-login-secret"
            />
          </label>

          {error && (
            <p
              className="rounded-md border border-danger/40 bg-danger/10 p-2 text-xs text-danger"
              data-testid="admin-login-error"
            >
              {error}
            </p>
          )}

          <button
            type="submit"
            disabled={!canSubmit}
            className="w-full rounded-md bg-accent px-3 py-2 text-sm font-semibold text-accent-foreground hover:opacity-90 disabled:opacity-40"
            data-testid="admin-login-submit"
          >
            {submitting ? "进入中…" : "进入管理控制台"}
          </button>
        </form>
      </div>
    </main>
  );
}