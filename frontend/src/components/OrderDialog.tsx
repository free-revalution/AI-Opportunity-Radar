"use client";

/**
 * OrderDialog — Phase 4 (v2.0) commercial-order capture modal.
 *
 * Launched from the Content Center's "标记已售出" button. Collects the
 * three fields the spec calls out — customer / payment / delivery
 * status — and a small set of metadata (channel, notes). On submit it
 * fires `onSubmit(order)` which the parent wires to either the
 * Content Center's mark-sold-with-order endpoint or a direct
 * /api/internal/orders POST.
 */

import { useEffect, useId, useState } from "react";

import type { OrderChannel, OrderCreatePayload } from "@/types";

const CHANNELS: Array<{ value: OrderChannel; label: string }> = [
  { value: "xianyu", label: "闲鱼" },
  { value: "xiaohongshu", label: "小红书" },
  { value: "wechat", label: "微信" },
  { value: "wechat_article", label: "公众号" },
  { value: "feishu", label: "飞书群" },
  { value: "direct", label: "直接联系" },
  { value: "other", label: "其他" },
];

const PAYMENT_METHODS = ["wechat", "alipay", "xianyu_guarantee", "bank", "other"] as const;

export interface OrderDialogProps {
  open: boolean;
  onClose: () => void;
  onSubmit: (order: OrderCreatePayload) => Promise<void> | void;
  busy?: boolean;
  opportunityTitle: string;
  defaultAmount?: number;
}

export function OrderDialog({
  open,
  onClose,
  onSubmit,
  busy = false,
  opportunityTitle,
  defaultAmount = 49,
}: OrderDialogProps) {
  const formId = useId();
  const [customerName, setCustomerName] = useState("");
  const [customerContact, setCustomerContact] = useState("");
  const [amountCny, setAmountCny] = useState<string>(String(defaultAmount));
  const [channel, setChannel] = useState<OrderChannel>("xianyu");
  const [paymentMethod, setPaymentMethod] = useState<string>("wechat");
  const [paymentReference, setPaymentReference] = useState("");
  const [notes, setNotes] = useState("");
  const [submitError, setSubmitError] = useState<string | null>(null);

  // Reset on open.
  useEffect(() => {
    if (open) {
      setCustomerName("");
      setCustomerContact("");
      setAmountCny(String(defaultAmount));
      setChannel("xianyu");
      setPaymentMethod("wechat");
      setPaymentReference("");
      setNotes("");
      setSubmitError(null);
    }
  }, [open, defaultAmount]);

  if (!open) return null;

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setSubmitError(null);
    const amount = Number.parseFloat(amountCny);
    if (!customerName.trim()) {
      setSubmitError("客户姓名必填");
      return;
    }
    if (!Number.isFinite(amount) || amount <= 0) {
      setSubmitError("金额必须大于 0");
      return;
    }
    const order: OrderCreatePayload = {
      customer_name: customerName.trim(),
      customer_contact: customerContact.trim() || null,
      amount_cny: amount,
      channel,
      payment_method: paymentMethod || null,
      payment_reference: paymentReference.trim() || null,
      notes: notes.trim() || null,
      mark_opportunity_sold: true,
    };
    try {
      await onSubmit(order);
    } catch (err) {
      setSubmitError((err as Error).message);
    }
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
      data-testid="order-dialog-backdrop"
      role="dialog"
      aria-modal="true"
      aria-labelledby={`${formId}-title`}
    >
      <form
        onSubmit={handleSubmit}
        className="w-full max-w-md rounded-xl border border-border bg-background p-6 shadow-2xl"
        data-testid="order-dialog"
      >
        <header className="mb-4">
          <h2 id={`${formId}-title`} className="text-lg font-semibold">
            记录销售
          </h2>
          <p className="mt-1 text-xs text-muted-foreground">{opportunityTitle}</p>
        </header>

        <div className="space-y-3">
          <Field label="客户姓名 *" htmlFor={`${formId}-customer`}>
            <input
              id={`${formId}-customer`}
              type="text"
              value={customerName}
              onChange={(e) => setCustomerName(e.target.value)}
              required
              className="w-full rounded-md border border-border bg-background px-3 py-1.5 text-sm"
              data-testid="order-customer-name"
              placeholder="如:张三 / 微信号"
            />
          </Field>
          <Field label="联系方式(可选)" htmlFor={`${formId}-contact`}>
            <input
              id={`${formId}-contact`}
              type="text"
              value={customerContact}
              onChange={(e) => setCustomerContact(e.target.value)}
              className="w-full rounded-md border border-border bg-background px-3 py-1.5 text-sm"
              data-testid="order-customer-contact"
              placeholder="微信号 / 手机号 / 邮箱"
            />
          </Field>
          <div className="grid grid-cols-2 gap-3">
            <Field label="金额 (CNY) *" htmlFor={`${formId}-amount`}>
              <input
                id={`${formId}-amount`}
                type="number"
                step="0.01"
                min="0"
                value={amountCny}
                onChange={(e) => setAmountCny(e.target.value)}
                required
                className="w-full rounded-md border border-border bg-background px-3 py-1.5 text-sm"
                data-testid="order-amount"
              />
            </Field>
            <Field label="销售渠道 *" htmlFor={`${formId}-channel`}>
              <select
                id={`${formId}-channel`}
                value={channel}
                onChange={(e) => setChannel(e.target.value as OrderChannel)}
                className="w-full rounded-md border border-border bg-background px-3 py-1.5 text-sm"
                data-testid="order-channel"
              >
                {CHANNELS.map((c) => (
                  <option key={c.value} value={c.value}>
                    {c.label}
                  </option>
                ))}
              </select>
            </Field>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <Field label="支付方式" htmlFor={`${formId}-payment-method`}>
              <select
                id={`${formId}-payment-method`}
                value={paymentMethod}
                onChange={(e) => setPaymentMethod(e.target.value)}
                className="w-full rounded-md border border-border bg-background px-3 py-1.5 text-sm"
                data-testid="order-payment-method"
              >
                {PAYMENT_METHODS.map((p) => (
                  <option key={p} value={p}>
                    {p}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="支付凭证号" htmlFor={`${formId}-payment-ref`}>
              <input
                id={`${formId}-payment-ref`}
                type="text"
                value={paymentReference}
                onChange={(e) => setPaymentReference(e.target.value)}
                className="w-full rounded-md border border-border bg-background px-3 py-1.5 text-sm"
                data-testid="order-payment-ref"
                placeholder="交易号 / 闲鱼订单号"
              />
            </Field>
          </div>
          <Field label="备注" htmlFor={`${formId}-notes`}>
            <textarea
              id={`${formId}-notes`}
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              rows={2}
              className="w-full rounded-md border border-border bg-background px-3 py-1.5 text-sm"
              data-testid="order-notes"
              placeholder="如:客户特别要求 PDF 版"
            />
          </Field>

          {submitError && (
            <p
              className="rounded-md border border-danger/40 bg-danger/10 p-2 text-xs text-danger"
              data-testid="order-error"
            >
              {submitError}
            </p>
          )}
        </div>

        <footer className="mt-6 flex justify-end gap-2">
          <button
            type="button"
            onClick={onClose}
            className="rounded-md border border-border px-3 py-1.5 text-sm hover:bg-muted"
            data-testid="order-cancel"
          >
            取消
          </button>
          <button
            type="submit"
            disabled={busy}
            className="rounded-md bg-accent px-3 py-1.5 text-sm font-semibold text-accent-foreground hover:opacity-90 disabled:opacity-40"
            data-testid="order-submit"
          >
            {busy ? "提交中…" : "确认记录"}
          </button>
        </footer>
      </form>
    </div>
  );
}

function Field({
  label,
  htmlFor,
  children,
}: {
  label: string;
  htmlFor: string;
  children: React.ReactNode;
}) {
  return (
    <label htmlFor={htmlFor} className="block text-xs text-muted-foreground">
      <span className="mb-1 block font-medium">{label}</span>
      {children}
    </label>
  );
}
