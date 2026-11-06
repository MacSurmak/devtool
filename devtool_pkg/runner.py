"""Deterministic subprocess and Git execution wrappers."""

import json
import os
import subprocess
from pathlib import Path

from devtool_pkg.constants import (
    AI_DIR_NAME,
    CONFIG_NAME,
    DEFAULT_BRANCH_NAME,
    DEFAULT_DIFF_NAME,
    DEFAULT_DUMP_NAME,
    DEFAULT_PATCH_NAME,
    DUMPIGNORE_NAME,
    SUBPROCESS_TIMEOUT_SEC,
    VALIDATION_REPORT_NAME,
)
from devtool_pkg.errors import CommandExecutionError, ConfigurationError
from devtool_pkg.models import CommandResult, DevtoolConfig, WorkspacePaths


def run_process(
    cmd: list[str],
    cwd: Path,
    require_success: bool,
    timeout_sec: float,
) -> CommandResult:
    """Execute an external command with explicit environment and timeout."""
    env_copy: dict[str, str] = dict(os.environ)
    try:
        completed = subprocess.run(
            cmd,
            cwd=str(cwd),
            env=env_copy,
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            check=False,
        )
    except FileNotFoundError as exc:
        raise CommandExecutionError(
            f"Required executable not found: '{cmd[0]}'."
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise CommandExecutionError(
            f"Command timed out after {timeout_sec}s: {' '.join(cmd)}"
        ) from exc

    if require_success and completed.returncode != 0:
        err_details = completed.stderr.strip()
        if not err_details:
            err_details = completed.stdout.strip()
        raise CommandExecutionError(
            f"Command failed (exit {completed.returncode}): `{' '.join(cmd)}`\n{err_details}"
        )

    return CommandResult(
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )


def run_git(
    args: list[str],
    cwd: Path,
    require_success: bool,
) -> CommandResult:
    """Execute a git command in the specified directory."""
    return run_process(
        cmd=["git"] + args,
        cwd=cwd,
        require_success=require_success,
        timeout_sec=SUBPROCESS_TIMEOUT_SEC,
    )


def is_git_repo(root: Path) -> bool:
    """Return True if the directory is inside a Git working tree."""
    res = run_git(
        args=["rev-parse", "--is-inside-work-tree"],
        cwd=root,
        require_success=False,
    )
    return res.returncode == 0


def get_current_branch(root: Path) -> str:
    """Return the active Git branch name."""
    res = run_git(
        args=["rev-parse", "--abbrev-ref", "HEAD"],
        cwd=root,
        require_success=False,
    )
    if res.returncode == 0 and res.stdout.strip():
        return res.stdout.strip()
    return DEFAULT_BRANCH_NAME


def get_project_root() -> Path:
    """Resolve the top-level Git repository root or current working directory."""
    cwd = Path.cwd()
    res = run_git(
        args=["rev-parse", "--show-toplevel"],
        cwd=cwd,
        require_success=False,
    )
    if res.returncode == 0 and res.stdout.strip():
        return Path(res.stdout.strip())
    return cwd


def get_workspace_paths() -> WorkspacePaths:
    """Construct canonical workspace paths for the current project."""
    root = get_project_root()
    ai_dir = root / AI_DIR_NAME
    return WorkspacePaths(
        root=root,
        ai_dir=ai_dir,
        default_dump=ai_dir / DEFAULT_DUMP_NAME,
        default_patch=ai_dir / DEFAULT_PATCH_NAME,
        default_diff=ai_dir / DEFAULT_DIFF_NAME,
        validation_report=ai_dir / VALIDATION_REPORT_NAME,
        dumpignore_file=ai_dir / DUMPIGNORE_NAME,
        config_file=ai_dir / CONFIG_NAME,
    )


def resolve_path(path_str: str, root: Path) -> Path:
    """Resolve a user-supplied path string relative to the project root."""
    candidate = Path(path_str.strip()).expanduser()
    if candidate.is_absolute():
        return candidate
    return root / candidate


def load_config(config_file: Path) -> DevtoolConfig:
    """Load and validate devtool configuration from disk."""
    if not config_file.exists():
        return DevtoolConfig(use_git=False, remote_url=None)

    try:
        raw_text = config_file.read_text(encoding="utf-8")
        raw_data: dict[str, str | bool | None] = json.loads(raw_text)
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigurationError(
            f"Corrupted devtool configuration file at {config_file}: {exc}"
        ) from exc

    use_git_val = bool(raw_data["use_git"]) if "use_git" in raw_data else False
    remote_val: str | None = None
    if "remote" in raw_data and isinstance(raw_data["remote"], str):
        remote_val = raw_data["remote"]

    return DevtoolConfig(use_git=use_git_val, remote_url=remote_val)


def save_config(config_file: Path, cfg: DevtoolConfig) -> None:
    """Persist devtool configuration atomically to disk."""
    config_file.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, str | bool] = {"use_git": cfg.use_git}
    if cfg.remote_url is not None:
        payload["remote"] = cfg.remote_url
    config_file.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
