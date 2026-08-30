"use client";

/**
 * Phase 18 — client-side auth gate for `/admin/*` routes.
 *
 * Server components can't read sessionStorage, so the secret can't be
 * checked on the server. Instead this guard mounts on the client, reads
 * the secret from sessionStorage, and redirects to /admin/login when
 * it's missing. `/admin/login` itself is in `PUBLIC_PATHS` so the
 * login form never bounces back to itself.
 */

import { useEffect } from "react";
import { usePathname, useRouter } from "next/navigation";

import { getWebhookSecret } from "@/lib/auth";

const PUBLIC_PATHS = new Set<string>(["/admin/login"]);

export interface AdminGuardProps {
  children: React.ReactNode;
}

export function AdminGuard({ children }: AdminGuardProps) {
  const router = useRouter();
  const pathname = usePathname();

  const isPublic = pathname ? PUBLIC_PATHS.has(pathname) : false;

  useEffect(() => {
    if (isPublic) return;
    if (!getWebhookSecret()) {
      router.replace("/admin/login");
    }
  }, [isPublic, pathname, router]);

  if (isPublic) return <>{children}</>;
  if (!getWebhookSecret()) {
    return (
      <p
        className="container py-10 text-sm text-muted-foreground"
        data-testid="admin-guard-redirecting"
      >
        Redirecting to login…
      </p>
    );
  }
  return <>{children}</>;
}