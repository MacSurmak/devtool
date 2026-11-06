"""Immutable data transfer objects for devtool operations and validation."""

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class WorkspacePaths:
    """Resolved canonical paths for the current project workspace."""

    root: Path
    ai_dir: Path
    default_dump: Path
    default_patch: Path
    default_diff: Path
    validation_report: Path
    dumpignore_file: Path
    config_file: Path


@dataclass(frozen=True)
class DevtoolConfig:
    """Project-level devtool configuration state."""

    use_git: bool
    remote_url: str | None


@dataclass(frozen=True)
class CommandResult:
    """Result of an isolated external command execution."""

    returncode: int
    stdout: str
    stderr: str


@dataclass(frozen=True)
class CreateOp:
    """File creation operation parsed from a patch."""

    rel_path: str
    content: str
    closing_tag: str


@dataclass(frozen=True)
class DeleteOp:
    """File deletion operation parsed from a patch."""

    rel_path: str


@dataclass(frozen=True)
class ReplaceOp:
    """Exact block replacement operation parsed from a patch."""

    rel_path: str
    search_block: str
    replace_block: str


@dataclass(frozen=True)
class RuleViolation:
    """Single architectural or structural rule violation."""

    file_path: str
    line_number: int
    rule_id: str
    law_reference: str
    code_snippet: str
    message: str


@dataclass(frozen=True)
class ParsedPatch:
    """Parsed representation of a devtool patch block."""

    commit_message: str
    creates: tuple[CreateOp, ...]
    deletes: tuple[DeleteOp, ...]
    replaces: tuple[ReplaceOp, ...]
    structural_violations: tuple[RuleViolation, ...]
