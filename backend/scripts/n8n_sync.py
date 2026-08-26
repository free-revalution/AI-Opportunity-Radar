#!/usr/bin/env python3
"""Phase 10 — push the local `n8n/workflows/*.json` files to a running n8n.

Usage:
    python -m scripts.n8n_sync                # sync, leave workflows as-is
    python -m scripts.n8n_sync --activate    # also flip them active on n8n
    python -m scripts.n8n_sync --dry-run      # validate JSON, do not push
    python -m scripts.n8n_sync --dir PATH    # override the workflows dir
    python -m scripts.n8n_sync --url URL --api-key KEY
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Allow `python scripts/n8n_sync.py` from a checked-out repo.
REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from app.services.n8n import (  # noqa: E402  (import after path tweak)
    N8nClient,
    N8nError,
    load_workflow_file,
    summarise,
    sync_workflows_dir,
)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="n8n_sync",
        description="Sync n8n/workflows/*.json into a running n8n instance.",
    )
    parser.add_argument(
        "--dir",
        default=str(REPO_ROOT / "n8n" / "workflows"),
        help="directory of workflow JSON files",
    )
    parser.add_argument("--url", help="override N8N_BASE_URL")
    parser.add_argument("--api-key", help="override N8N_API_KEY")
    parser.add_argument(
        "--activate",
        action="store_true",
        help="activate each workflow after a successful create/update",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="parse every workflow JSON and report validation issues; "
        "do not call n8n",
    )
    return parser


def _validate_only(directory: Path) -> int:
    files = sorted(directory.glob("*.json"))
    if not files:
        print(f"no workflow files found in {directory}", file=sys.stderr)
        return 2
    failed = 0
    for path in files:
        try:
            payload = load_workflow_file(path)
        except ValueError as exc:
            print(f"INVALID  {path.name}  {exc}")
            failed += 1
            continue
        node_count = len(payload.get("nodes", []))
        print(f"OK       {path.name}  ({node_count} nodes, name={payload['name']!r})")
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    directory = Path(args.dir).resolve()
    if not directory.exists():
        print(f"workflows directory not found: {directory}", file=sys.stderr)
        return 2

    if args.dry_run:
        return _validate_only(directory)

    try:
        client = N8nClient(base_url=args.url or "", api_key=args.api_key or "")
    except N8nError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2

    try:
        summaries = sync_workflows_dir(
            directory, activate=args.activate, client=client
        )
    except N8nError as exc:
        print(f"sync failed: {exc}", file=sys.stderr)
        return 1

    print(summarise(summaries))
    if args.activate:
        print("\nAll workflows activated.")
    if any(s.action == "failed" for s in summaries):
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
