import { AdminGuard } from "@/components/AdminGuard";

/**
 * Phase 18 — admin layout. Wraps every `/admin/*` page in the
 * client-side auth guard (sessionStorage-backed X-Radar-Webhook).
 */
export default function AdminLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return <AdminGuard>{children}</AdminGuard>;
}