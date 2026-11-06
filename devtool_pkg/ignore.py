"""Dumpignore rule loading and deterministic path filtering."""

from pathlib import Path, PurePath

from devtool_pkg.constants import DEFAULT_DUMPIGNORE_TEMPLATE


def load_dumpignore_rules(dumpignore_path: Path) -> tuple[str, ...]:
    """Load active ignore patterns from .ai/dumpignore, creating default if missing."""
    if not dumpignore_path.exists():
        dumpignore_path.parent.mkdir(parents=True, exist_ok=True)
        dumpignore_path.write_text(DEFAULT_DUMPIGNORE_TEMPLATE, encoding="utf-8")

    rules: list[str] = []
    for raw_line in dumpignore_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        rules.append(line)
    return tuple(rules)


def _match_pattern(target: str, pattern: str) -> bool:
    """Check if a target path or segment matches a wildcard pattern."""
    return PurePath(target).match(pattern)


def should_ignore(rel_path: Path, is_dir: bool, rules: tuple[str, ...]) -> bool:
    """Return True if the relative path matches any dumpignore rule."""
    path_str = str(rel_path).replace("\\", "/")
    name = rel_path.name

    for raw_rule in rules:
        rule = raw_rule.replace("\\", "/")
        if rule.endswith("/"):
            clean_rule = rule.rstrip("/")
            if is_dir:
                if (
                    _match_pattern(name, clean_rule)
                    or _match_pattern(path_str, clean_rule)
                    or _match_pattern(path_str, f"*/{clean_rule}")
                ):
                    return True
            else:
                parent_parts = rel_path.parts[:-1]
                if any(_match_pattern(part, clean_rule) for part in parent_parts):
                    return True
        else:
            if (
                _match_pattern(name, rule)
                or _match_pattern(path_str, rule)
                or _match_pattern(path_str, f"*/{rule}")
            ):
                return True
    return False
