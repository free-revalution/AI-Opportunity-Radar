"""Tests for `backend/scripts/backup_postgres.sh` + `restore_postgres.sh`.

We never invoke it for real (CI may not have Docker). Each test only
inspects the script's `--help` and `--dry-run` output.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
BACKUP_SCRIPT = SCRIPTS_DIR / "backup_postgres.sh"
RESTORE_SCRIPT = SCRIPTS_DIR / "restore_postgres.sh"


def _run(args: list[str], env_extra: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    if env_extra:
        env.update(env_extra)
    return subprocess.run(  # noqa: S603 - test-only, args are script paths under our control
        ["bash", str(SCRIPTS_DIR / args[1]), *args[2:]],  # noqa: S607 - bash interpreter for portability
        capture_output=True,
        text=True,
        env=env,
        timeout=10,
    )


def test_backup_script_exists_and_is_executable() -> None:
    assert BACKUP_SCRIPT.exists()
    import stat

    mode = BACKUP_SCRIPT.stat().st_mode
    assert mode & stat.S_IXUSR, "backup script must be user-executable"


def test_restore_script_exists_and_is_executable() -> None:
    assert RESTORE_SCRIPT.exists()
    import stat

    mode = RESTORE_SCRIPT.stat().st_mode
    assert mode & stat.S_IXUSR, "restore script must be user-executable"


def test_backup_script_help_returns_zero() -> None:
    proc = _run(["bash", str(BACKUP_SCRIPT), "--help"])
    assert proc.returncode == 0
    assert "backup_postgres.sh" in proc.stdout
    assert "--dry-run" in proc.stdout


def test_restore_script_help_returns_zero() -> None:
    proc = _run(["bash", str(RESTORE_SCRIPT), "--help"])
    assert proc.returncode == 0
    assert "restore_postgres.sh" in proc.stdout
    assert "--dry-run" in proc.stdout


def test_backup_dry_run_prints_docker_exec_pg_dump() -> None:
    proc = _run(["bash", str(BACKUP_SCRIPT), "--dry-run"])
    assert proc.returncode == 0
    assert "DRY-RUN" in proc.stdout
    assert "docker" in proc.stdout
    assert "pg_dump" in proc.stdout
    # The default container name appears in the printed command.
    assert "radar-postgres" in proc.stdout


def test_restore_dry_run_prints_cat_and_psql(tmp_path: Path) -> None:
    sql_file = tmp_path / "dump.sql"
    sql_file.write_text("-- stub\n")
    proc = _run(["bash", str(RESTORE_SCRIPT), "--file", str(sql_file), "--dry-run"])
    assert proc.returncode == 0
    assert "DRY-RUN" in proc.stdout
    assert "docker" in proc.stdout
    assert "psql" in proc.stdout
