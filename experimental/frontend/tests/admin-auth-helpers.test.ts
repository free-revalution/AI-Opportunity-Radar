import { afterEach, beforeEach, describe, expect, it } from "vitest";

import {
  clearWebhookSecret,
  getWebhookSecret,
  setWebhookSecret,
} from "@/lib/auth";

const KEY = "radar.webhookSecret";

describe("lib/auth sessionStorage helpers", () => {
  beforeEach(() => {
    if (typeof window !== "undefined") {
      window.sessionStorage.clear();
    }
  });

  afterEach(() => {
    if (typeof window !== "undefined") {
      window.sessionStorage.clear();
    }
  });

  it("getWebhookSecret returns undefined when nothing is stored", () => {
    expect(getWebhookSecret()).toBeUndefined();
  });

  it("setWebhookSecret then getWebhookSecret returns the same value", () => {
    setWebhookSecret("s3cret-value");
    expect(getWebhookSecret()).toBe("s3cret-value");
    expect(window.sessionStorage.getItem(KEY)).toBe("s3cret-value");
  });

  it("clearWebhookSecret removes the stored value", () => {
    setWebhookSecret("temp");
    clearWebhookSecret();
    expect(getWebhookSecret()).toBeUndefined();
    expect(window.sessionStorage.getItem(KEY)).toBeNull();
  });

  it("treats an empty string as not set", () => {
    window.sessionStorage.setItem(KEY, "");
    expect(getWebhookSecret()).toBeUndefined();
  });
});