"use client";

import { useState } from "react";

export function SettingsForm() {
  const [telegramChatId, setTelegramChatId] = useState("");
  const [submitted, setSubmitted] = useState(false);

  return (
    <form
      className="mt-8 space-y-4"
      onSubmit={(e) => {
        e.preventDefault();
        setSubmitted(true);
      }}
    >
      <label className="block">
        <span className="text-sm">Telegram Chat ID</span>
        <input
          type="text"
          value={telegramChatId}
          onChange={(e) => setTelegramChatId(e.target.value)}
          placeholder="123456789"
          className="mt-1 w-full rounded-md border border-border bg-muted/30 px-3 py-2 text-sm outline-none focus:border-accent"
        />
      </label>
      <button
        type="submit"
        className="rounded-md bg-accent px-4 py-2 text-sm font-semibold text-accent-foreground hover:opacity-90"
      >
        Save (UI only — backend wiring lands in V1)
      </button>
      {submitted && (
        <p className="text-xs text-muted-foreground">
          Saved locally. Persistence arrives in V1 once the user system ships.
        </p>
      )}
    </form>
  );
}