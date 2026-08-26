import { SettingsForm } from "@/components/SettingsForm";

export default function SettingsPage() {
  return (
    <main className="container max-w-2xl py-10">
      <h1 className="text-3xl font-semibold">Settings</h1>
      <p className="text-sm text-muted-foreground">
        User accounts land in V1. This page is a placeholder so the navigation works today.
      </p>
      <SettingsForm />
    </main>
  );
}