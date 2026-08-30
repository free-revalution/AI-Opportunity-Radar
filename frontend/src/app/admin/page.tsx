import { redirect } from "next/navigation";

/**
 * Phase 18 — admin landing page. Bounces straight into the Content
 * Center review queue; Phase 19 will replace this with summary cards.
 */
export default function AdminIndex(): never {
  redirect("/admin/content-opportunities");
}