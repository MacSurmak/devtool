"""Project skeleton tree builder, targeted file dumper, and git diff exporter."""

import os
from pathlib import Path

from devtool_pkg.constants import (
    BINARY_CHECK_CHUNK_SIZE,
    CHARS_PER_TOKEN_ESTIMATE,
    CLOSE_MARK,
    OPEN_MARK,
    TEXT_EXTENSIONS,
)
from devtool_pkg.errors import ConfigurationError
from devtool_pkg.ignore import load_dumpignore_rules, should_ignore
from devtool_pkg.runner import (
    get_workspace_paths,
    is_git_repo,
    resolve_path,
    run_git,
)
from devtool_pkg.signatures import extract_file_signatures


def is_text_file(file_path: Path) -> bool:
    """Return True if the file is a recognized text format and contains no null bytes."""
    if file_path.suffix.lower() in TEXT_EXTENSIONS or file_path.name.startswith("."):
        return True
    try:
        with open(file_path, "rb") as handle:
            chunk = handle.read(BINARY_CHECK_CHUNK_SIZE)
            return b"\x00" not in chunk
    except OSError as exc:
        raise ConfigurationError(
            f"Failed to read file for binary inspection '{file_path}': {exc}"
        ) from exc


def build_skeleton_tree(
    dir_path: Path,
    root_path: Path,
    rules: tuple[str, ...],
    prefix: str,
) -> list[str]:
    """Recursively build a visual directory tree enriched with code signatures."""
    tree: list[str] = []
    try:
        entries = sorted(
            list(dir_path.iterdir()),
            key=lambda item: (item.is_file(), item.name.lower()),
        )
    except (PermissionError, OSError) as exc:
        raise ConfigurationError(
            f"Failed to list directory '{dir_path}': {exc}"
        ) from exc

    valid_entries: list[Path] = []
    for entry in entries:
        rel = entry.relative_to(root_path)
        if should_ignore(rel, entry.is_dir(), rules):
            continue
        valid_entries.append(entry)

    for idx, entry in enumerate(valid_entries):
        is_last = idx == len(valid_entries) - 1
        connector = "└── " if is_last else "├── "
        ext = "    " if is_last else "│   "

        if entry.is_dir():
            tree.append(f"{prefix}{connector}{entry.name}/")
            tree.extend(
                build_skeleton_tree(
                    dir_path=entry,
                    root_path=root_path,
                    rules=rules,
                    prefix=prefix + ext,
                )
            )
        else:
            tree.append(f"{prefix}{connector}{entry.name}")
            if is_text_file(entry):
                sigs = extract_file_signatures(entry)
                if sigs:
                    sig_prefix = prefix + ext
                    for s_idx, sig in enumerate(sigs):
                        s_last = s_idx == len(sigs) - 1
                        s_conn = "└── " if s_last else "├── "
                        tree.append(f"{sig_prefix}{s_conn}{sig}")
    return tree


def export_tree(
    target: str,
    output_path: str | None,
    to_stdout: bool,
) -> None:
    """Export project skeleton tree to a file or stdout."""
    ws = get_workspace_paths()
    rules = load_dumpignore_rules(ws.dumpignore_file)

    target_dir = resolve_path(target, ws.root)
    if not target_dir.exists():
        raise ConfigurationError(f"Target path does not exist: {target}")

    tree_lines = build_skeleton_tree(
        dir_path=target_dir,
        root_path=ws.root,
        rules=rules,
        prefix="",
    )
    scope_name = (
        ws.root.name
        if target_dir == ws.root
        else f"{ws.root.name}/{target_dir.relative_to(ws.root)}"
    )

    sep_line = "=" * 48
    tree_text = (
        f"{sep_line}\n"
        f" STRUCTURE: {scope_name}\n"
        f"{sep_line}\n\n"
        f"{scope_name}/\n" + "\n".join(tree_lines) + "\n"
    )

    if to_stdout:
        print(tree_text)
        return

    out_file = Path(output_path) if output_path is not None else ws.default_dump
    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_text(tree_text, encoding="utf-8")

    total_lines = len(tree_lines)
    est_tokens = len(tree_text) // CHARS_PER_TOKEN_ESTIMATE
    print(f"✅ Project tree saved to: {out_file}")
    print(f"📊 Lines: {total_lines} | Estimated size: ~{est_tokens:,} tokens")


def dump_skeleton(
    root: Path,
    out_file: Path,
    rules: tuple[str, ...],
) -> None:
    """Write compact project skeleton with signatures to the output dump file."""
    tree_lines = build_skeleton_tree(
        dir_path=root,
        root_path=root,
        rules=rules,
        prefix="",
    )
    sep_line = "=" * 48
    header = (
        f"{sep_line}\n"
        f" SKELETON: {root.name}\n"
        f"{sep_line}\n\n"
        f"{root.name}/\n" + "\n".join(tree_lines) + "\n"
    )
    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_text(header, encoding="utf-8")

    total_lines = len(tree_lines)
    est_tokens = len(header) // CHARS_PER_TOKEN_ESTIMATE
    print(f"✨ Project skeleton built: {out_file}")
    print(f"📊 Lines: {total_lines} | Estimated size: ~{est_tokens:,} tokens")
    print("💡 To dump full contents of specific files, run:")
    print("   devtool dump <path1> <path2> ...")


def _collect_directory_files(
    dir_path: Path,
    root: Path,
    rules: tuple[str, ...],
) -> set[Path]:
    """Deterministically walk a directory and collect non-ignored text files."""
    collected: set[Path] = set()
    for current_root, dirs, files in os.walk(dir_path):
        rel_root = Path(current_root).relative_to(root)
        dirs[:] = [
            d
            for d in dirs
            if not should_ignore(
                rel_root / d if str(rel_root) != "." else Path(d),
                True,
                rules,
            )
        ]
        for file_name in files:
            fp = Path(current_root) / file_name
            rel = fp.relative_to(root)
            if not should_ignore(rel, False, rules) and is_text_file(fp):
                collected.add(fp)
    return collected


def dump_targeted(
    root: Path,
    out_file: Path,
    target_paths: list[str],
    rules: tuple[str, ...],
) -> None:
    """Dump full contents of explicitly specified files or directories."""
    matched_files: set[Path] = set()

    for target_str in target_paths:
        if target_str == ".":
            matched_files.update(_collect_directory_files(root, root, rules))
            continue

        target = resolve_path(target_str, root)
        if not target.exists():
            print(f"⚠️  Path not found: {target_str}")
            continue

        if target.is_file():
            rel = target.relative_to(root)
            if not should_ignore(rel, False, rules) and is_text_file(target):
                matched_files.add(target)
        elif target.is_dir():
            matched_files.update(_collect_directory_files(target, root, rules))

    if not matched_files:
        raise ConfigurationError("No valid text files found for the specified paths.")

    sorted_files = sorted(list(matched_files))
    sep_line = "=" * 48
    out_lines: list[str] = [
        f"{sep_line}\n"
        f" TARGETED DUMP: {len(sorted_files)} file(s)\n"
        f"{sep_line}\n\n"
        "```devtool\n"
        f"{OPEN_MARK} COMMIT\n"
        f"Unroll {len(sorted_files)} file(s)\n"
        f"{CLOSE_MARK} COMMIT\n"
    ]

    for fp in sorted_files:
        rel = fp.relative_to(root)
        content = fp.read_text(encoding="utf-8", errors="replace")
        out_lines.append(f"\n{OPEN_MARK} CREATE: {rel}\n{content}")
        if not content.endswith("\n"):
            out_lines.append("\n")
        out_lines.append(f"{CLOSE_MARK} CREATE\n")

    out_lines.append("```")

    result_text = "".join(out_lines)
    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_text(result_text, encoding="utf-8")

    est_tokens = len(result_text) // CHARS_PER_TOKEN_ESTIMATE
    print(f"✅ Targeted dump ready: {out_file}")
    print(
        f"📦 Files: {len(sorted_files)} | Estimated size: ~{est_tokens:,} tokens (without tree)"
    )
    for f_item in sorted_files:
        print(f"   • {f_item.relative_to(root)}")


def dump_full_legacy(
    root: Path,
    out_file: Path,
    rules: tuple[str, ...],
) -> None:
    """Dump the entire project structure and all text files into a single archive."""
    tree_lines = build_skeleton_tree(
        dir_path=root,
        root_path=root,
        rules=rules,
        prefix="",
    )
    sep_line = "=" * 48
    out_lines: list[str] = [
        f"{sep_line}\n",
        f" STRUCTURE: {root.name}\n",
        f"{sep_line}\n\n",
        f"{root.name}/\n",
        "\n".join(tree_lines),
        "\n\n" + "=" * 50 + "\n\n",
        f"{OPEN_MARK} COMMIT\n",
        f"Restore project from dump: {root.name}\n",
        f"{CLOSE_MARK} COMMIT\n\n",
    ]

    all_files = sorted(list(_collect_directory_files(root, root, rules)))
    for fp in all_files:
        rel = fp.relative_to(root)
        content = fp.read_text(encoding="utf-8", errors="replace")
        out_lines.append(f"{OPEN_MARK} CREATE: {rel}\n{content}")
        if not content.endswith("\n"):
            out_lines.append("\n")
        out_lines.append(f"{CLOSE_MARK} CREATE\n\n")

    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_text("".join(out_lines), encoding="utf-8")
    print(f"✅ Full project dump ready: {out_file}")


def get_changed_files_since(commit: str, root: Path) -> list[str]:
    """Return list of file paths modified since the given git commit."""
    res = run_git(
        args=["diff", "--name-only", commit],
        cwd=root,
        require_success=False,
    )
    if res.returncode != 0:
        raise ConfigurationError(f"Commit '{commit}' not found in Git repository.")
    return [ln.strip() for ln in res.stdout.splitlines() if ln.strip()]


def export_diff(
    commit: str,
    output_path: str | None,
    to_stdout: bool,
) -> None:
    """Export git diff since a specific commit to a file or stdout."""
    ws = get_workspace_paths()
    if not is_git_repo(ws.root):
        raise ConfigurationError("Current project is not a Git repository.")

    ws.ai_dir.mkdir(exist_ok=True)
    out_file = Path(output_path) if output_path is not None else ws.default_diff

    res = run_git(
        args=["diff", commit],
        cwd=ws.root,
        require_success=False,
    )
    if res.returncode != 0:
        raise ConfigurationError(f"Failed to obtain git diff for '{commit}'.")

    diff_text = res.stdout
    if not diff_text.strip():
        print(f"ℹ️  Diff is empty: no changes since '{commit}'.")
        return

    if to_stdout:
        print(diff_text)
        return

    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_text(diff_text, encoding="utf-8")

    stat_res = run_git(
        args=["diff", "--stat", commit],
        cwd=ws.root,
        require_success=False,
    ).stdout.strip()
    total_lines = len(diff_text.splitlines())
    est_tokens = len(diff_text) // CHARS_PER_TOKEN_ESTIMATE

    print(f"✅ Diff since '{commit}' saved to: {out_file}")
    print(f"📊 Diff lines: {total_lines} | Estimated size: ~{est_tokens:,} tokens")
    if stat_res:
        print("\nChange summary:")
        for line in stat_res.splitlines()[-5:]:
            print(f"  {line}")


def dump_project(
    output_path: str | None,
    targets: list[str],
    full_dump: bool,
    since_commit: str | None,
) -> None:
    """Orchestrate project context dumping based on CLI flags."""
    ws = get_workspace_paths()
    ws.ai_dir.mkdir(exist_ok=True)
    out_file = Path(output_path) if output_path is not None else ws.default_dump
    rules = load_dumpignore_rules(ws.dumpignore_file)

    if since_commit is not None:
        if not is_git_repo(ws.root):
            raise ConfigurationError("The --since flag requires a Git repository.")
        changed_files = get_changed_files_since(since_commit, ws.root)
        if not changed_files:
            print(f"ℹ️  No changed files since '{since_commit}'.")
            return
        print(f"🔍 Changed files since '{since_commit}': {len(changed_files)}")
        dump_targeted(ws.root, out_file, changed_files, rules)
    elif full_dump:
        dump_full_legacy(ws.root, out_file, rules)
    elif targets:
        dump_targeted(ws.root, out_file, targets, rules)
    else:
        dump_skeleton(ws.root, out_file, rules)
