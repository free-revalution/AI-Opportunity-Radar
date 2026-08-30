"use client";

/**
 * OnDemandPanel — Phase 5 (v2.0) operator UI for accepting a customer
 * URL or topic and generating a deep-research report inline.
 *
 * Server-side fetches `fetchOnDemandRecent()` and hands the initial
 * list to this client component, which owns the URL/topic form, the
 * optional customer/order fields, the inline report viewer, and the
 * refresh interaction.
 */

import { useCallback, useState } from "react";

import {
  createOnDemandResearch,
  fetchOnDemandDetail,
  fetchOnDemandRecent,
} from "@/lib/api";
import type {
  OnDemandCreatePayload,
  OnDemandCreateResponse,
  OnDemandDetailResponse,
  OnDemandListResponse,
  Recommendation,
  ResearchReportData,
} from "@/types";

const RECOMMENDATION_LABELS: Record<Recommendation, string> = {
  strongly_recommend: "强烈推荐",
  recommend: "推荐",
  watch: "继续观察",
  not_recommended: "不推荐",
  insufficient_data: "信息不足",
};

export interface OnDemandPanelProps {
  initialList: OnDemandListResponse;
}

type SeedMode = "url" | "topic";

export function OnDemandPanel({ initialList }: OnDemandPanelProps) {
  const [seedMode, setSeedMode] = useState<SeedMode>("url");
  const [url, setUrl] = useState("");
  const [topic, setTopic] = useState("");
  const [attachOrder, setAttachOrder] = useState(false);
  const [customerName, setCustomerName] = useState("");
  const [customerContact, setCustomerContact] = useState("");
  const [amountCny, setAmountCny] = useState("299");
  const [channel, setChannel] = useState<"wechat" | "xianyu" | "xiaohongshu" | "direct" | "other">("wechat");
  const [paymentMethod, setPaymentMethod] = useState("wechat");
  const [notes, setNotes] = useState("");

  const [submitting, setSubmitting] = useState(false);
  const [errorText, setErrorText] = useState<string | null>(null);
  const [report, setReport] = useState<OnDemandCreateResponse | null>(null);
  const [detail, setDetail] = useState<OnDemandDetailResponse | null>(null);
  const [list, setList] = useState<OnDemandListResponse>(initialList);
  const [refreshing, setRefreshing] = useState(false);

  const showToast = useCallback((kind: "ok" | "err", text: string) => {
    if (typeof window === "undefined") return;
    window.setTimeout(() => undefined, 2500);
    const el = document.createElement("div");
    el.textContent = text;
    el.className =
      "fixed bottom-6 left-1/2 -translate-x-1/2 rounded-md px-4 py-2 text-sm shadow-lg " +
      (kind === "ok" ? "bg-emerald-600 text-white" : "bg-red-600 text-white");
    document.body.appendChild(el);
    setTimeout(() => el.remove(), 2500);
  }, []);

  const refresh = useCallback(async () => {
    setRefreshing(true);
    try {
      const fresh = await fetchOnDemandRecent(20);
      setList(fresh);
    } catch (err) {
      showToast("err", (err as Error).message);
    } finally {
      setRefreshing(false);
    }
  }, [showToast]);

  const submit = useCallback(async () => {
    setErrorText(null);
    setReport(null);
    setDetail(null);

    const trimmedSeed = seedMode === "url" ? url.trim() : topic.trim();
    if (!trimmedSeed) {
      setErrorText(seedMode === "url" ? "请输入 URL" : "请输入主题");
      return;
    }
    if (attachOrder) {
      if (!customerName.trim()) {
        setErrorText("填写客户信息时,客户姓名必填");
        return;
      }
      const amt = Number(amountCny);
      if (!Number.isFinite(amt) || amt < 0) {
        setErrorText("金额必须是 ≥ 0 的数字");
        return;
      }
    }

    const payload: OnDemandCreatePayload = seedMode === "url"
      ? { url: trimmedSeed }
      : { topic: trimmedSeed };
    if (attachOrder) {
      payload.customer_name = customerName.trim();
      if (customerContact.trim()) payload.customer_contact = customerContact.trim();
      payload.amount_cny = Number(amountCny);
      payload.channel = channel;
      if (paymentMethod.trim()) payload.payment_method = paymentMethod.trim();
      if (notes.trim()) payload.notes = notes.trim();
    }

    setSubmitting(true);
    try {
      const created = await createOnDemandResearch(payload);
      setReport(created);
      // Fetch the full report inline.
      try {
        const full = await fetchOnDemandDetail(created.job_id);
        setDetail(full);
      } catch (err) {
        showToast("err", `已生成概要,但详情加载失败: ${(err as Error).message}`);
      }
      // Reset + refresh the list.
      setUrl("");
      setTopic("");
      setCustomerName("");
      setCustomerContact("");
      setNotes("");
      const fresh = await fetchOnDemandRecent(20);
      setList(fresh);
      showToast("ok", `报告已生成 · job #${created.job_id}`);
    } catch (err) {
      setErrorText((err as Error).message);
    } finally {
      setSubmitting(false);
    }
  }, [
    amountCny,
    attachOrder,
    channel,
    customerContact,
    customerName,
    notes,
    paymentMethod,
    seedMode,
    showToast,
    topic,
    url,
  ]);

  return (
    <div className="space-y-10" data-testid="on-demand-panel">
      {/* Form card */}
      <section
        className="rounded-xl border border-border bg-card/40 p-6"
        data-testid="on-demand-form"
      >
        <h2 className="text-lg font-semibold">生成按需深度研究报告</h2>
        <p className="mt-1 text-xs text-muted-foreground">
          输入一个公开 URL 或一个主题,系统会用与日常流水线相同的抓取+LLM
          流程生成一份七段式研究报告。¥299-¥999/份的售前场景。
        </p>

        {/* Seed-mode toggle */}
        <div className="mt-4 flex gap-2" data-testid="seed-toggle">
          <button
            type="button"
            onClick={() => setSeedMode("url")}
            className={
              "rounded-md border px-3 py-1 text-xs " +
              (seedMode === "url"
                ? "border-accent bg-accent/20 text-accent"
                : "border-border hover:bg-muted")
            }
            data-testid="seed-toggle-url"
          >
            公开 URL
          </button>
          <button
            type="button"
            onClick={() => setSeedMode("topic")}
            className={
              "rounded-md border px-3 py-1 text-xs " +
              (seedMode === "topic"
                ? "border-accent bg-accent/20 text-accent"
                : "border-border hover:bg-muted")
            }
            data-testid="seed-toggle-topic"
          >
            主题
          </button>
        </div>

        {seedMode === "url" ? (
          <input
            type="url"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            placeholder="https://example.com/product-page"
            className="mt-3 w-full rounded-md border border-border bg-background px-3 py-2 text-sm"
            data-testid="seed-url-input"
          />
        ) : (
          <input
            type="text"
            value={topic}
            onChange={(e) => setTopic(e.target.value)}
            placeholder="例如:AI 法律合同审核"
            className="mt-3 w-full rounded-md border border-border bg-background px-3 py-2 text-sm"
            data-testid="seed-topic-input"
          />
        )}

        {/* Attach-order toggle */}
        <label className="mt-4 flex items-center gap-2 text-xs text-muted-foreground">
          <input
            type="checkbox"
            checked={attachOrder}
            onChange={(e) => setAttachOrder(e.target.checked)}
            data-testid="attach-order-toggle"
          />
          同时记录一笔订单(客户付费后,直接生成报告+登记订单)
        </label>

        {attachOrder && (
          <div
            className="mt-3 grid gap-3 md:grid-cols-3"
            data-testid="order-fields"
          >
            <Field label="客户姓名 *" testid="order-customer-name">
              <input
                type="text"
                value={customerName}
                onChange={(e) => setCustomerName(e.target.value)}
                className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm"
                data-testid="order-customer-name-input"
              />
            </Field>
            <Field label="联系方式" testid="order-customer-contact">
              <input
                type="text"
                value={customerContact}
                onChange={(e) => setCustomerContact(e.target.value)}
                placeholder="wechat:xxx 或邮箱"
                className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm"
                data-testid="order-customer-contact-input"
              />
            </Field>
            <Field label="金额 (CNY) *" testid="order-amount">
              <input
                type="number"
                value={amountCny}
                onChange={(e) => setAmountCny(e.target.value)}
                min={0}
                step="0.01"
                className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm"
                data-testid="order-amount-input"
              />
            </Field>
            <Field label="销售渠道" testid="order-channel">
              <select
                value={channel}
                onChange={(e) =>
                  setChannel(
                    e.target.value as
                      | "wechat"
                      | "xianyu"
                      | "xiaohongshu"
                      | "direct"
                      | "other",
                  )
                }
                className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm"
                data-testid="order-channel-select"
              >
                <option value="wechat">微信</option>
                <option value="xianyu">闲鱼</option>
                <option value="xiaohongshu">小红书</option>
                <option value="direct">直接联系</option>
                <option value="other">其他</option>
              </select>
            </Field>
            <Field label="支付方式" testid="order-payment-method">
              <input
                type="text"
                value={paymentMethod}
                onChange={(e) => setPaymentMethod(e.target.value)}
                placeholder="wechat / alipay / …"
                className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm"
                data-testid="order-payment-method-input"
              />
            </Field>
            <Field label="备注" testid="order-notes">
              <input
                type="text"
                value={notes}
                onChange={(e) => setNotes(e.target.value)}
                className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm"
                data-testid="order-notes-input"
              />
            </Field>
          </div>
        )}

        {errorText && (
          <p
            className="mt-3 rounded-md border border-danger/40 bg-danger/10 p-3 text-xs"
            data-testid="on-demand-error"
          >
            {errorText}
          </p>
        )}

        <div className="mt-4 flex items-center gap-3">
          <button
            type="button"
            onClick={submit}
            disabled={submitting}
            className="rounded-md bg-accent px-4 py-2 text-sm font-medium text-accent-foreground hover:opacity-90 disabled:opacity-50"
            data-testid="on-demand-submit"
          >
            {submitting ? "生成中…" : "生成报告"}
          </button>
          <span className="text-[10px] text-muted-foreground">
            按需生成的报告会出现在下方&ldquo;最近任务&rdquo;列表。
          </span>
        </div>
      </section>

      {/* Inline report viewer */}
      {(report || detail) && (
        <section
          className="rounded-xl border border-accent/40 bg-accent/5 p-6"
          data-testid="on-demand-result"
        >
          {report && (
            <div className="flex items-center gap-2 text-sm">
              <span className="chip-accent">已生成</span>
              <span className="font-mono text-xs text-muted-foreground">
                job #{report.job_id}
              </span>
              {report.order_id && (
                <span className="chip-success" data-testid="result-order-chip">
                  订单 #{report.order_id} 已登记
                </span>
              )}
            </div>
          )}
          <ReportViewer detail={detail} />
        </section>
      )}

      {/* Recent list */}
      <section className="space-y-3">
        <div className="flex items-center gap-3">
          <h2 className="text-lg font-semibold">最近任务</h2>
          <button
            type="button"
            onClick={refresh}
            disabled={refreshing}
            className="ml-auto rounded-md border border-border px-3 py-1 text-xs hover:bg-muted disabled:opacity-50"
            data-testid="on-demand-refresh"
          >
            {refreshing ? "刷新中…" : "刷新"}
          </button>
        </div>

        {list.items.length === 0 ? (
          <div
            className="rounded-xl border border-dashed border-border p-12 text-center text-sm text-muted-foreground"
            data-testid="on-demand-empty"
          >
            还没有按需研究报告。提交上面的表单开始第一份。
          </div>
        ) : (
          <div className="overflow-x-auto rounded-xl border border-border">
            <table className="w-full text-xs" data-testid="on-demand-table">
              <thead className="bg-muted/30 text-left">
                <tr>
                  <th className="px-3 py-2 font-medium">Job</th>
                  <th className="px-3 py-2 font-medium">种子</th>
                  <th className="px-3 py-2 font-medium">建议</th>
                  <th className="px-3 py-2 font-medium">置信度</th>
                  <th className="px-3 py-2 font-medium">来源</th>
                  <th className="px-3 py-2 font-medium">状态</th>
                  <th className="px-3 py-2 font-medium">完成时间</th>
                </tr>
              </thead>
              <tbody>
                {list.items.map((item) => (
                  <tr
                    key={item.job_id}
                    className="border-t border-border"
                    data-testid={`on-demand-row-${item.job_id}`}
                  >
                    <td className="px-3 py-2 font-mono">#{item.job_id}</td>
                    <td className="px-3 py-2">
                      <div className="font-medium">
                        {item.seed_url ?? item.seed_topic ?? "—"}
                      </div>
                      <div className="text-[10px] text-muted-foreground">
                        opp #{item.opportunity_id}
                      </div>
                    </td>
                    <td className="px-3 py-2">
                      {item.recommendation
                        ? RECOMMENDATION_LABELS[item.recommendation] ??
                          item.recommendation
                        : "—"}
                    </td>
                    <td className="px-3 py-2 font-mono">
                      {(item.confidence * 100).toFixed(0)}%
                    </td>
                    <td className="px-3 py-2 font-mono">{item.sources_count}</td>
                    <td className="px-3 py-2">
                      <span
                        className={
                          "rounded-full px-2 py-0.5 " +
                          statusClass(item.status)
                        }
                      >
                        {item.status}
                      </span>
                    </td>
                    <td className="px-3 py-2 text-[10px] text-muted-foreground">
                      {formatDate(item.completed_at)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}

// ---------------------------------------------------------------------------
function Field({
  label,
  testid,
  children,
}: {
  label: string;
  testid: string;
  children: React.ReactNode;
}) {
  return (
    <label className="space-y-1 text-xs text-muted-foreground" data-testid={testid}>
      <span>{label}</span>
      {children}
    </label>
  );
}

function ReportViewer({ detail }: { detail: OnDemandDetailResponse | null }) {
  if (!detail) {
    return (
      <p className="mt-3 text-xs text-muted-foreground" data-testid="report-loading">
        加载报告详情…
      </p>
    );
  }
  const r: ResearchReportData | null = detail.report;
  if (!r) {
    return (
      <p
        className="mt-3 rounded-md border border-warning/40 bg-warning/10 p-3 text-xs"
        data-testid="report-missing"
      >
        研究任务完成,但报告内容尚未写入。检查 LLM 提供方日志。
      </p>
    );
  }
  return (
    <div className="mt-4 space-y-4" data-testid="report-viewer">
      <header>
        <h3 className="text-lg font-semibold">{detail.opportunity_title}</h3>
        {detail.seed_url && (
          <div className="mt-1 text-[10px] text-muted-foreground">
            种子 URL:
            <a
              href={detail.seed_url}
              className="ml-1 text-accent hover:underline"
              target="_blank"
              rel="noopener noreferrer"
            >
              {detail.seed_url}
            </a>
          </div>
        )}
        <div className="mt-2 flex flex-wrap items-center gap-2 text-xs">
          {detail.recommendation && (
            <span
              className={
                "rounded-full px-2 py-0.5 " +
                recommendationClass(detail.recommendation)
              }
              data-testid="report-recommendation"
            >
              {RECOMMENDATION_LABELS[detail.recommendation] ??
                detail.recommendation}
            </span>
          )}
          <span className="font-mono text-muted-foreground">
            置信度 {(detail.confidence * 100).toFixed(0)}%
          </span>
          <span className="font-mono text-muted-foreground">
            来源 {detail.sources_count}
          </span>
        </div>
      </header>

      <ReportSection title="执行摘要" testid="report-section-exec" body={r.executive_summary ?? ""} />
      <ReportSection title="市场分析" testid="report-section-market" body={r.market_analysis ?? ""} />
      <ReportSection title="竞争分析" testid="report-section-competition" body={r.competition_analysis ?? ""} />
      <ReportSection title="中国市场分析" testid="report-section-china" body={r.china_analysis ?? ""} />
      <ReportSection title="变现分析" testid="report-section-monetization" body={r.monetization_analysis ?? ""} />
      <ReportSection title="MVP 分析" testid="report-section-mvp" body={r.mvp_analysis ?? ""} />
      <ReportSection title="风险分析" testid="report-section-risk" body={r.risk_analysis ?? ""} />

      {r.sources && r.sources.length > 0 && (
        <div
          className="rounded-md border border-border bg-card/40 p-3"
          data-testid="report-sources"
        >
          <h4 className="text-xs font-semibold">参考资料 ({r.sources.length})</h4>
          <ul className="mt-2 space-y-1 text-[11px]">
            {r.sources.map((s, i) => (
              <li key={`${s.url}-${i}`} className="break-all">
                <a
                  href={s.url}
                  className="text-accent hover:underline"
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  {s.title || s.url}
                </a>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

function ReportSection({
  title,
  testid,
  body,
}: {
  title: string;
  testid: string;
  body: string;
}) {
  return (
    <div
      className="rounded-md border border-border bg-card/40 p-3"
      data-testid={testid}
    >
      <h4 className="text-xs font-semibold text-muted-foreground">{title}</h4>
      <p className="mt-2 whitespace-pre-wrap text-sm leading-relaxed">{body}</p>
    </div>
  );
}

function statusClass(status: string): string {
  switch (status) {
    case "completed":
      return "bg-emerald-500/20 text-emerald-300";
    case "running":
    case "pending":
      return "bg-blue-500/20 text-blue-300";
    case "failed":
    case "cancelled":
      return "bg-red-500/20 text-red-300";
    default:
      return "bg-muted text-muted-foreground";
  }
}

function recommendationClass(r: string): string {
  switch (r) {
    case "strongly_recommend":
    case "recommend":
      return "bg-emerald-500/20 text-emerald-300";
    case "watch":
      return "bg-amber-500/20 text-amber-300";
    case "not_recommended":
      return "bg-red-500/20 text-red-300";
    default:
      return "bg-muted text-muted-foreground";
  }
}

function formatDate(iso: string | null | undefined): string {
  if (!iso) return "";
  try {
    const d = new Date(iso);
    return `${d.getMonth() + 1}/${d.getDate()} ${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
  } catch {
    return iso;
  }
}