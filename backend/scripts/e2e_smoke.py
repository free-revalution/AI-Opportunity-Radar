#!/usr/bin/env python3
"""Phase 35 PR-35-D follow-up — end-to-end smoke test for Data 表多目标广播。

Phase 35 把 ``FEISHU_BITABLE_DATA_APP_TOKEN`` 改成支持逗号分隔的多 token,
每次 ``bulk_insert_raw_items_unbounded`` 把每一行写到所有目标表。但单测
mock 了 Feishu 客户端,没法证明"真实生产里两张表都收到数据"。

这个脚本填这个口:

  1. 读 ``.env`` → 拿 ``RADAR_WEBHOOK_SECRET`` + ``FEISHU_BITABLE_DATA_APP_TOKEN``
  2. POST ``/api/internal/data_table/sync`` → 拿到 ``task_id``
  3. 轮询 ``/api/internal/task/{id}`` 直到 status ∈ {success, failed}
  4. 打印 result_summary 里每个 target 的 inserted / skipped / error

用法::

    make e2e-smoke                              # 走 http://localhost:8000
    python -m scripts.e2e_smoke --base-url http://staging:8000
    python -m scripts.e2e_smoke --poll-interval 2 --max-wait 60

退出码:
  0 = 全部 target success
  1 = 任一 target failed 或 status=failed
  2 = 网络错误 / task not found / 任何异常

跟 ``make n8n-validate`` / ``make n8n-sync`` 一样,不依赖外部 lib,
只走标准库 + httpx(项目本来就有)。
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Optional

# Allow `python scripts/e2e_smoke.py` from a checked-out repo.
REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "backend"))


# ---------------------------------------------------------------------------
# .env 解析 — 项目用 ``KEY=VALUE`` 简单格式,不需要 dotenv 依赖
# ---------------------------------------------------------------------------
def _read_env_file(path: Path) -> dict[str, str]:
    """读 .env 风格的 KEY=VALUE 文件,忽略空行和注释。

    不展开变量引用($RADAR_WEBHOOK_SECRET → 它的值)—— 直接给原值就行,
    require_admin 同时接受 ``APP_SECRET_KEY`` 和 ``RADAR_WEBHOOK_SECRET``。
    """
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        # 去引号(支持单/双/无引号)
        v = value.strip()
        if (v.startswith('"') and v.endswith('"')) or (
            v.startswith("'") and v.endswith("'")
        ):
            v = v[1:-1]
        out[key.strip()] = v
    return out


def _resolve_webhook_secret(env_file: Path) -> str:
    """优先 ``RADAR_WEBHOOK_SECRET``,其次 ``APP_SECRET_KEY``(二者等同)。

    这跟 ``app/api/deps.py::require_admin`` 一致 — n8n 用 RADAR_WEBHOOK_SECRET,
    老路径用 APP_SECRET_KEY,任意一个能匹配就算过。
    """
    env = _read_env_file(env_file)
    return (
        env.get("RADAR_WEBHOOK_SECRET")
        or env.get("APP_SECRET_KEY")
        or os.environ.get("RADAR_WEBHOOK_SECRET")
        or os.environ.get("APP_SECRET_KEY")
        or ""
    )


def _resolve_target_tokens(env_file: Path) -> list[str]:
    """读 ``FEISHU_BITABLE_DATA_APP_TOKEN`` 解析成 list(同 ``_parse_data_app_tokens``)。

    这里不调那个函数(避免 import 项目依赖);脚本的目标是"看到 .env 写啥"
    而不是 "按运行时 dedup 之后的真实目标" — 重复 token 写在这里也展示出来,
    给运营一个直观反馈。
    """
    env = _read_env_file(env_file)
    raw = env.get("FEISHU_BITABLE_DATA_APP_TOKEN") or os.environ.get(
        "FEISHU_BITABLE_DATA_APP_TOKEN", ""
    )
    raw = raw.strip()
    if not raw:
        return []
    tokens = [t.strip() for t in raw.split(",") if t.strip()]
    return tokens


# ---------------------------------------------------------------------------
# HTTP 客户端 — 复用 httpx(项目已有)
# ---------------------------------------------------------------------------
def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="e2e_smoke",
        description=(
            "Trigger POST /api/internal/data_table/sync and poll "
            "GET /api/internal/task/{id} until success/failed."
        ),
    )
    parser.add_argument(
        "--base-url",
        default=os.environ.get("APP_API_BASE_URL", "http://localhost:8000"),
        help="backend root URL (default: $APP_API_BASE_URL or localhost:8000)",
    )
    parser.add_argument(
        "--env-file",
        default=str(REPO_ROOT / ".env"),
        help=".env path with RADAR_WEBHOOK_SECRET + FEISHU_BITABLE_DATA_APP_TOKEN",
    )
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=500,
        help="DataTableSyncRequest.chunk_size (default: 500)",
    )
    parser.add_argument(
        "--since",
        default=None,
        help="ISO8601 lower bound for raw_items.fetched_at (default: all)",
    )
    parser.add_argument(
        "--trigger",
        default="e2e_smoke",
        help="string recorded in TaskRecord.trigger (default: e2e_smoke)",
    )
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=2.0,
        help="seconds between status polls (default: 2.0)",
    )
    parser.add_argument(
        "--max-wait",
        type=int,
        default=600,
        help="max seconds to poll before giving up (default: 600)",
    )
    return parser


def _post_sync(
    *,
    base_url: str,
    webhook_secret: str,
    chunk_size: int,
    since: Optional[str],
    trigger: str,
) -> dict[str, Any]:
    """POST /api/internal/data_table/sync → return response JSON。

    Raises:
        RuntimeError: 网络错误 / 401 / 任何非 2xx。
    """
    import httpx

    headers = {"X-Radar-Webhook": webhook_secret}
    payload: dict[str, Any] = {
        "chunk_size": chunk_size,
        "trigger": trigger,
    }
    if since:
        payload["since"] = since

    url = f"{base_url.rstrip('/')}/api/internal/data_table/sync"
    try:
        resp = httpx.post(url, headers=headers, json=payload, timeout=30.0)
    except httpx.HTTPError as exc:
        raise RuntimeError(f"POST {url} failed: {exc}") from exc

    if resp.status_code != 200:
        raise RuntimeError(
            f"POST {url} → HTTP {resp.status_code}: {resp.text[:300]}"
        )
    return resp.json()


def _get_task(
    *, base_url: str, webhook_secret: str, task_id: str
) -> dict[str, Any]:
    """GET /api/internal/task/{id} → return response JSON。

    Raises:
        RuntimeError: 网络错误或非 200。
    """
    import httpx

    headers = {"X-Radar-Webhook": webhook_secret}
    url = f"{base_url.rstrip('/')}/api/internal/task/{task_id}"
    try:
        resp = httpx.get(url, headers=headers, timeout=15.0)
    except httpx.HTTPError as exc:
        raise RuntimeError(f"GET {url} failed: {exc}") from exc

    if resp.status_code != 200:
        raise RuntimeError(
            f"GET {url} → HTTP {resp.status_code}: {resp.text[:300]}"
        )
    return resp.json()


def _format_status_line(record: dict[str, Any]) -> str:
    """把一次 poll 的 status 渲染成一行可读输出。"""
    status = record.get("status", "?")
    progress_ins = record.get("progress_inserted")
    progress_tot = record.get("progress_total")
    progress = ""
    if progress_ins is not None or progress_tot is not None:
        progress = f"  progress={progress_ins or 0}/{progress_tot or '?'}"

    # result_summary 在 success 时才有
    summary = record.get("result_summary") or {}
    targets = summary.get("targets") or []
    target_lines: list[str] = []
    for ts in targets:
        tok = ts.get("app_token") or "?"
        ins = ts.get("inserted", 0)
        skp = ts.get("skipped_duplicate", 0)
        err = ts.get("error")
        marker = "✓" if not err else "✗"
        target_lines.append(
            f"      {marker} {tok[:14]:<14} inserted={ins:<3} "
            f"skipped={skp:<3} {('error=' + err) if err else ''}"
        )

    return (
        f"  status={status}{progress}\n"
        + "\n".join(target_lines or ["      (no targets yet)"])
    )


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------
def main(argv: Optional[list[str]] = None) -> int:
    args = _build_parser().parse_args(argv)

    env_file = Path(args.env_file)
    target_tokens = _resolve_target_tokens(env_file)
    webhook_secret = _resolve_webhook_secret(env_file)

    print("=" * 72)
    print("AI Opportunity Radar — Phase 35 PR-35-D e2e smoke")
    print("=" * 72)
    print(f"  backend   : {args.base_url}")
    print(f"  env file  : {env_file} (exists={env_file.exists()})")
    print(f"  targets   : {len(target_tokens)} token(s)")
    for idx, t in enumerate(target_tokens, start=1):
        print(f"    {idx}. {t}")
    if not target_tokens:
        print("    (no FEISHU_BITABLE_DATA_APP_TOKEN — task will auto-create one)")
    has_secret = bool(webhook_secret)
    print(
        f"  webhook   : {'set (' + str(len(webhook_secret)) + ' chars)' if has_secret else 'EMPTY (relying on dev short-circuit)'}"
    )
    print()

    if not has_secret:
        print(
            "[warn] No RADAR_WEBHOOK_SECRET / APP_SECRET_KEY — only works against "
            "a dev backend with empty secrets. Set them in .env for production.",
            file=sys.stderr,
        )

    # 1) POST /data_table/sync
    print("[1/2] POST /api/internal/data_table/sync")
    try:
        sync_resp = _post_sync(
            base_url=args.base_url,
            webhook_secret=webhook_secret,
            chunk_size=args.chunk_size,
            since=args.since,
            trigger=args.trigger,
        )
    except RuntimeError as exc:
        print(f"  ✗ failed: {exc}", file=sys.stderr)
        return 2

    task_id = sync_resp.get("task_id")
    if not task_id:
        print(f"  ✗ no task_id in response: {sync_resp}", file=sys.stderr)
        return 2
    print(f"  ✓ task_id={task_id}  initial_status={sync_resp.get('status')!r}")

    # 2) Poll /task/{id} 直到 success / failed / 超时
    print()
    print(f"[2/2] Poll /api/internal/task/{task_id}")
    deadline = time.monotonic() + args.max_wait
    final: Optional[dict[str, Any]] = None
    last_status: Optional[str] = None
    while time.monotonic() < deadline:
        try:
            rec = _get_task(
                base_url=args.base_url,
                webhook_secret=webhook_secret,
                task_id=task_id,
            )
        except RuntimeError as exc:
            print(f"  ✗ poll failed: {exc}", file=sys.stderr)
            return 2

        status = rec.get("status")
        # 只在状态变化时打印,避免每 2 秒都重复同一行
        if status != last_status:
            print(_format_status_line(rec))
            last_status = status

        if status in ("success", "failed"):
            final = rec
            break
        time.sleep(args.poll_interval)
    else:
        print(
            f"  ✗ timeout after {args.max_wait}s "
            f"(last status={last_status!r})",
            file=sys.stderr,
        )
        return 2

    # 3) 评估结果
    assert final is not None
    status = final.get("status")
    summary = final.get("result_summary") or {}
    targets = summary.get("targets") or []
    inserted_rows = summary.get("inserted_rows", 0)
    inserted_unique = summary.get("inserted", 0)
    any_error = any(t.get("error") for t in targets)

    print()
    print("-" * 72)
    print(f"Final status : {status}")
    print(f"inserted     : {inserted_unique} (unique, ≥1 target)")
    print(f"inserted_rows: {inserted_rows} (broadcast sum)")
    print(f"targets      : {len(targets)}")
    for ts in targets:
        tok = ts.get("app_token") or "?"
        ins = ts.get("inserted", 0)
        skp = ts.get("skipped_duplicate", 0)
        err = ts.get("error")
        marker = "✓" if not err else "✗"
        print(f"  {marker} {tok:<22} +{ins} (skipped {skp})")
    if err := final.get("error"):
        print(f"task error   : {err}")
    print("-" * 72)

    if status == "failed" or any_error:
        print("FAIL — see errors above.", file=sys.stderr)
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
