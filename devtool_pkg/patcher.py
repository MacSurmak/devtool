"""Transactional all-or-nothing patch parser, in-memory staging, and git manager."""

from pathlib import Path

from devtool_pkg.constants import (
    AI_DIR_NAME,
    CLOSE_MARK,
    COMMIT_PATTERN,
    CREATE_PATTERN,
    DEFAULT_DUMPIGNORE_TEMPLATE,
    DEFAULT_GITIGNORE_TEMPLATE,
    DELETE_PATTERN,
    HEADER_CREATE_PATTERN,
    HEADER_DELETE_PATTERN,
    HEADER_SEARCH_PATTERN,
    OPEN_MARK,
    PATCH_PLACEHOLDER_TEXT,
    REPLACE_PATTERN,
    SEP_MARK,
    SUBPROCESS_TIMEOUT_SEC,
)
from devtool_pkg.errors import ConfigurationError, PatchRejectedError
from devtool_pkg.models import (
    CreateOp,
    DeleteOp,
    DevtoolConfig,
    OperationStatus,
    OrderedPatchOp,
    ParsedPatch,
    ReplaceOp,
    RuleViolation,
)
from devtool_pkg.runner import (
    get_current_branch,
    get_project_root,
    get_workspace_paths,
    is_git_repo,
    load_config,
    resolve_path,
    run_git,
    run_process,
    save_config,
)
from devtool_pkg.validator import (
    format_violation_report,
    validate_commit_message,
    validate_file_content,
    validate_text_block_for_omissions,
)


def parse_patch_text(patch_text: str) -> ParsedPatch:
    """Parse raw devtool patch text and collect all structural violations."""
    violations: list[RuleViolation] = []
    first_block_idx = patch_text.find(OPEN_MARK)
    if first_block_idx == -1:
        violations.append(
            RuleViolation(
                file_path="<PATCH>",
                line_number=1,
                rule_id="PATCH-NO-BLOCKS",
                law_reference="Section III.2 (Monolithic Block Structure)",
                code_snippet="<missing devtool markers>",
                message=f"No devtool instruction blocks ({OPEN_MARK}) found in patch file.",
            )
        )
        return ParsedPatch(
            commit_message="",
            creates=(),
            deletes=(),
            replaces=(),
            ordered_ops=(),
            structural_violations=tuple(violations),
        )

    first_chunk = patch_text[first_block_idx:]
    commit_match = COMMIT_PATTERN.match(first_chunk)
    commit_msg = ""
    if commit_match is None:
        violations.append(
            RuleViolation(
                file_path="<PATCH:COMMIT>",
                line_number=1,
                rule_id="PATCH-COMMIT-MISSING",
                law_reference="Section III.2 (Mandatory First COMMIT Block)",
                code_snippet=first_chunk.splitlines()[0] if first_chunk else "",
                message=f"The very first block in a patch must be {OPEN_MARK} COMMIT ... {CLOSE_MARK} COMMIT.",
            )
        )
    else:
        commit_msg = commit_match.group(1).strip()
        violations.extend(validate_commit_message(commit_msg))

    raw_ordered: list[OrderedPatchOp] = []

    deletes: list[DeleteOp] = []
    for match in DELETE_PATTERN.finditer(patch_text):
        del_op = DeleteOp(rel_path=match.group(1).strip())
        deletes.append(del_op)
        raw_ordered.append(
            OrderedPatchOp(
                position=match.start(),
                op_type="DELETE",
                rel_path=del_op.rel_path,
                create_op=None,
                delete_op=del_op,
                replace_op=None,
            )
        )

    creates: list[CreateOp] = []
    for match in CREATE_PATTERN.finditer(patch_text):
        f_name = match.group(1).strip()
        content = match.group(2)
        end_tag = match.group(3).strip()
        create_op = CreateOp(rel_path=f_name, content=content, closing_tag=end_tag)
        creates.append(create_op)
        raw_ordered.append(
            OrderedPatchOp(
                position=match.start(),
                op_type="CREATE",
                rel_path=f_name,
                create_op=create_op,
                delete_op=None,
                replace_op=None,
            )
        )
        if end_tag != "CREATE":
            violations.append(
                RuleViolation(
                    file_path=f_name,
                    line_number=1,
                    rule_id="PATCH-BROKEN-TAGS",
                    law_reference="Section III.2 (CREATE Block Closing Tag)",
                    code_snippet=f"{CLOSE_MARK} {end_tag}",
                    message=f"CREATE block for '{f_name}' was closed with '{CLOSE_MARK} {end_tag}' instead of '{CLOSE_MARK} CREATE'.",
                )
            )

    replaces: list[ReplaceOp] = []
    for match in REPLACE_PATTERN.finditer(patch_text):
        f_name = match.group(1).strip()
        search_b = match.group(2)
        replace_b = match.group(3)
        rep_op = ReplaceOp(
            rel_path=f_name,
            search_block=search_b,
            replace_block=replace_b,
        )
        replaces.append(rep_op)
        raw_ordered.append(
            OrderedPatchOp(
                position=match.start(),
                op_type="REPLACE",
                rel_path=f_name,
                create_op=None,
                delete_op=None,
                replace_op=rep_op,
            )
        )
        stripped_search = search_b.strip()
        stripped_replace = replace_b.strip()
        if not stripped_search:
            violations.append(
                RuleViolation(
                    file_path=f_name,
                    line_number=1,
                    rule_id="PATCH-SEARCH-EMPTY",
                    law_reference="Section III.3 (Non-Empty SEARCH Block)",
                    code_snippet="<empty SEARCH>",
                    message=f"SEARCH block in '{f_name}' must not be empty.",
                )
            )
        elif stripped_search in stripped_replace:
            first_line_snippet = stripped_search.splitlines()[0]
            violations.append(
                RuleViolation(
                    file_path=f_name,
                    line_number=1,
                    rule_id="PATCH-NON-IDEMPOTENT-SEARCH",
                    law_reference="Section III.3 (Patch Idempotency & No Duplicate Application)",
                    code_snippet=first_line_snippet,
                    message=(
                        f"SEARCH block in '{f_name}' is a verbatim substring of REPLACE block. "
                        "Re-applying this patch would match again and duplicate code. "
                        "Include context on BOTH sides of the insertion or mutate the matched line itself."
                    ),
                )
            )

    all_search_headers = [
        h.strip() for h in HEADER_SEARCH_PATTERN.findall(patch_text)
    ]
    all_create_headers = [
        h.strip() for h in HEADER_CREATE_PATTERN.findall(patch_text)
    ]
    all_delete_headers = [
        h.strip() for h in HEADER_DELETE_PATTERN.findall(patch_text)
    ]

    parsed_replace_files = [op.rel_path for op in replaces]
    for header_path in set(all_search_headers):
        if all_search_headers.count(header_path) > parsed_replace_files.count(
            header_path
        ):
            violations.append(
                RuleViolation(
                    file_path=header_path,
                    line_number=1,
                    rule_id="PATCH-BROKEN-TAGS",
                    law_reference="Section III.2 (Malformed SEARCH/REPLACE Block)",
                    code_snippet=f"{OPEN_MARK} SEARCH: {header_path}",
                    message=f"Malformed SEARCH/REPLACE block for '{header_path}'. Check '{SEP_MARK}' and '{CLOSE_MARK} REPLACE' markers.",
                )
            )

    parsed_create_files = [op.rel_path for op in creates]
    for header_path in set(all_create_headers):
        if all_create_headers.count(header_path) > parsed_create_files.count(
            header_path
        ):
            violations.append(
                RuleViolation(
                    file_path=header_path,
                    line_number=1,
                    rule_id="PATCH-BROKEN-TAGS",
                    law_reference="Section III.2 (Malformed CREATE Block)",
                    code_snippet=f"{OPEN_MARK} CREATE: {header_path}",
                    message=f"Malformed CREATE block for '{header_path}'. Check closing '{CLOSE_MARK} CREATE' marker.",
                )
            )

    parsed_delete_files = [op.rel_path for op in deletes]
    for header_path in set(all_delete_headers):
        if all_delete_headers.count(header_path) > parsed_delete_files.count(
            header_path
        ):
            violations.append(
                RuleViolation(
                    file_path=header_path,
                    line_number=1,
                    rule_id="PATCH-BROKEN-TAGS",
                    law_reference="Section III.2 (Malformed DELETE Block)",
                    code_snippet=f"{OPEN_MARK} DELETE: {header_path}",
                    message=f"Malformed DELETE block for '{header_path}'. Check closing '{CLOSE_MARK} DELETE' marker.",
                )
            )

    if not creates and not deletes and not replaces:
        violations.append(
            RuleViolation(
                file_path="<PATCH>",
                line_number=1,
                rule_id="PATCH-NO-OPERATIONS",
                law_reference="Section III.2 (Patch Operations)",
                code_snippet="<empty operations>",
                message="Patch contains no valid CREATE, DELETE, or SEARCH/REPLACE operations.",
            )
        )

    sorted_ops = tuple(sorted(raw_ordered, key=lambda item: item.position))
    return ParsedPatch(
        commit_message=commit_msg,
        creates=tuple(creates),
        deletes=tuple(deletes),
        replaces=tuple(replaces),
        ordered_ops=sorted_ops,
        structural_violations=tuple(violations),
    )


def apply_patch(
    patch_path: str | None,
    force_push: bool,
) -> None:
    """Validate and atomically apply a devtool patch in all-or-nothing mode."""
    ws = get_workspace_paths()
    has_git = is_git_repo(ws.root)

    if has_git:
        status_res = run_git(
            args=["status", "--porcelain"],
            cwd=ws.root,
            require_success=True,
        )
        if status_res.stdout.strip():
            print("\n⚠️  Uncommitted changes detected in repository:\n")
            short_status = run_git(
                args=["status", "-s"],
                cwd=ws.root,
                require_success=True,
            ).stdout
            print(short_status)
            user_msg = input(
                "📝 Enter commit message for your local changes (or press Enter to abort): "
            ).strip()
            if not user_msg:
                raise ConfigurationError(
                    "Apply aborted. Commit or stash your local changes before running devtool apply."
                )
            run_git(args=["add", "-A"], cwd=ws.root, require_success=True)
            run_git(
                args=["commit", "-m", user_msg],
                cwd=ws.root,
                require_success=True,
            )
            print(f'📦 Local changes committed: "{user_msg}"\n')

    target_file = Path(patch_path) if patch_path is not None else ws.default_patch
    if not target_file.exists():
        raise ConfigurationError(f"Patch file not found: {target_file}")

    patch_text = target_file.read_text(encoding="utf-8").replace("\r\n", "\n")
    parsed = parse_patch_text(patch_text)

    all_violations: list[RuleViolation] = list(parsed.structural_violations)
    staged_files: dict[str, str] = {}
    deleted_paths: set[str] = set()
    preliminary_statuses: list[tuple[str, str, bool, str]] = []

    for item in parsed.ordered_ops:
        rel_p = item.rel_path
        if item.op_type == "DELETE" and item.delete_op is not None:
            target = resolve_path(rel_p, ws.root)
            if not target.exists():
                all_violations.append(
                    RuleViolation(
                        file_path=rel_p,
                        line_number=1,
                        rule_id="PATCH-TARGET-MISSING",
                        law_reference="Section III.3 (No Blind Patches)",
                        code_snippet=f"DELETE: {rel_p}",
                        message=f"Cannot delete '{rel_p}': file does not exist on disk.",
                    )
                )
                preliminary_statuses.append(
                    ("DELETE", rel_p, False, "file does not exist")
                )
            else:
                deleted_paths.add(rel_p)
                if rel_p in staged_files:
                    del staged_files[rel_p]
                preliminary_statuses.append(("DELETE", rel_p, True, "target found"))

        elif item.op_type == "CREATE" and item.create_op is not None:
            content = item.create_op.content
            if content and not content.endswith("\n"):
                content += "\n"
            staged_files[rel_p] = content
            if rel_p in deleted_paths:
                deleted_paths.remove(rel_p)
            if item.create_op.closing_tag != "CREATE":
                preliminary_statuses.append(
                    (
                        "CREATE",
                        rel_p,
                        False,
                        f"invalid closing tag '{CLOSE_MARK} {item.create_op.closing_tag}'",
                    )
                )
            else:
                preliminary_statuses.append(("CREATE", rel_p, True, "staged in memory"))

        elif item.op_type == "REPLACE" and item.replace_op is not None:
            rep_op = item.replace_op
            if rel_p not in staged_files:
                target = resolve_path(rel_p, ws.root)
                if not target.exists() or rel_p in deleted_paths:
                    all_violations.append(
                        RuleViolation(
                            file_path=rel_p,
                            line_number=1,
                            rule_id="PATCH-TARGET-MISSING",
                            law_reference="Section III.3 (No Blind Patches)",
                            code_snippet=f"SEARCH: {rel_p}",
                            message=f"Target file '{rel_p}' does not exist for SEARCH/REPLACE.",
                        )
                    )
                    preliminary_statuses.append(
                        ("REPLACE", rel_p, False, "file does not exist")
                    )
                    continue
                staged_files[rel_p] = target.read_text(encoding="utf-8").replace(
                    "\r\n", "\n"
                )

            current_text = staged_files[rel_p]
            occurrences = current_text.count(rep_op.search_block)
            first_search_line = (
                rep_op.search_block.strip().splitlines()[0]
                if rep_op.search_block.strip()
                else "<empty search>"
            )

            if occurrences == 0:
                all_violations.append(
                    RuleViolation(
                        file_path=rel_p,
                        line_number=1,
                        rule_id="PATCH-SEARCH-NOT-FOUND",
                        law_reference="Section III.3 (Exact SEARCH Match Requirement)",
                        code_snippet=first_search_line,
                        message=f"Exact SEARCH block was not found in '{rel_p}'.",
                    )
                )
                preliminary_statuses.append(
                    ("REPLACE", rel_p, False, "exact SEARCH block not found")
                )
            elif occurrences > 1:
                all_violations.append(
                    RuleViolation(
                        file_path=rel_p,
                        line_number=1,
                        rule_id="PATCH-SEARCH-AMBIGUOUS",
                        law_reference="Section III.3 (Absolute Uniqueness of SEARCH)",
                        code_snippet=first_search_line,
                        message=(
                            f"SEARCH block matches {occurrences} locations in '{rel_p}'. "
                            "Provide more surrounding context so it matches exactly once."
                        ),
                    )
                )
                preliminary_statuses.append(
                    (
                        "REPLACE",
                        rel_p,
                        False,
                        f"ambiguous SEARCH block ({occurrences} matches)",
                    )
                )
            else:
                updated_text = current_text.replace(
                    rep_op.search_block, rep_op.replace_block, 1
                )
                if updated_text and not updated_text.endswith("\n"):
                    updated_text += "\n"
                staged_files[rel_p] = updated_text
                preliminary_statuses.append(
                    ("REPLACE", rel_p, True, "100% exact match")
                )

    per_file_violation_counts: dict[str, int] = {}
    for v in all_violations:
        base_file = v.file_path.split(" (")[0]
        per_file_violation_counts[base_file] = (
            per_file_violation_counts[base_file] + 1
            if base_file in per_file_violation_counts
            else 1
        )

    for rel_p, final_content in staged_files.items():
        file_violations = validate_file_content(rel_p, final_content)
        all_violations.extend(file_violations)
        if file_violations:
            per_file_violation_counts[rel_p] = (
                per_file_violation_counts[rel_p] + len(file_violations)
                if rel_p in per_file_violation_counts
                else len(file_violations)
            )

    op_totals_per_file: dict[tuple[str, str], int] = {}
    for op_type, rel_p, _, _ in preliminary_statuses:
        key = (op_type, rel_p)
        op_totals_per_file[key] = (
            op_totals_per_file[key] + 1 if key in op_totals_per_file else 1
        )

    op_seen_counts: dict[tuple[str, str], int] = {}
    final_statuses: list[OperationStatus] = []
    for op_type, rel_p, stage_ok, stage_detail in preliminary_statuses:
        key = (op_type, rel_p)
        current_idx = op_seen_counts[key] + 1 if key in op_seen_counts else 1
        op_seen_counts[key] = current_idx
        label_type = (
            f"{op_type} #{current_idx}"
            if op_totals_per_file[key] > 1
            else op_type
        )

        v_count = (
            per_file_violation_counts[rel_p]
            if rel_p in per_file_violation_counts
            else 0
        )
        if not stage_ok:
            final_statuses.append(
                OperationStatus(
                    op_type=label_type,
                    rel_path=rel_p,
                    passed=False,
                    detail=stage_detail,
                )
            )
        elif v_count > 0:
            final_statuses.append(
                OperationStatus(
                    op_type=label_type,
                    rel_path=rel_p,
                    passed=False,
                    detail=f"{stage_detail}, file violations: {v_count}",
                )
            )
        else:
            final_statuses.append(
                OperationStatus(
                    op_type=label_type,
                    rel_path=rel_p,
                    passed=True,
                    detail=f"{stage_detail}, 0 violations",
                )
            )

    if all_violations:
        report_text = format_violation_report(all_violations, final_statuses)
        ws.ai_dir.mkdir(parents=True, exist_ok=True)
        ws.validation_report.write_text(report_text + "\n", encoding="utf-8")
        raise PatchRejectedError(
            f"{report_text}\n\n📄 Full rejection report saved to: {ws.validation_report}"
        )

    print("\n🔍 Patch validation & staging summary (in patch order):")
    print("─" * 76)
    for st in final_statuses:
        icon = "✅" if st.passed else "❌"
        print(f"  {icon} {st.op_type:<11} {st.rel_path} ({st.detail})")
    print("─" * 76)

    for rel_p in sorted(deleted_paths):
        target = resolve_path(rel_p, ws.root)
        if target.exists():
            target.unlink()
            print(f"🗑️  Deleted: {rel_p}")

    for rel_p in sorted(staged_files.keys()):
        dest = resolve_path(rel_p, ws.root)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(staged_files[rel_p], encoding="utf-8")
        print(f"✅ Applied: {rel_p}")

    if patch_path is None and ws.default_patch.exists():
        ws.default_patch.write_text(PATCH_PLACEHOLDER_TEXT, encoding="utf-8")

    if ws.validation_report.exists():
        ws.validation_report.unlink()

    if has_git:
        run_git(args=["add", "-A"], cwd=ws.root, require_success=True)
        staged = run_git(
            args=["diff", "--cached", "--name-only"],
            cwd=ws.root,
            require_success=True,
        ).stdout.strip()
        if not staged:
            print("\nℹ️  No effective file changes to commit.")
            return

        run_git(
            args=["commit", "-m", parsed.commit_message],
            cwd=ws.root,
            require_success=True,
        )
        print(f'📦 Committed: "{parsed.commit_message}"')

        if force_push:
            branch = get_current_branch(ws.root)
            print(f"🚀 Pushing changes to remote (branch {branch})...")
            run_git(
                args=["push", "-u", "origin", branch],
                cwd=ws.root,
                require_success=True,
            )
            print("✅ Changes pushed to remote.")
    else:
        print("✅ All files validated and written to disk (Git not enabled).")


def validate_existing_files(targets: list[str]) -> None:
    """Run the universal architectural validator against existing workspace files."""
    ws = get_workspace_paths()
    search_targets = targets if targets else ["."]
    files_to_check: list[Path] = []

    for t_str in search_targets:
        resolved = resolve_path(t_str, ws.root)
        if not resolved.exists():
            raise ConfigurationError(f"Path does not exist: {t_str}")
        if resolved.is_file():
            files_to_check.append(resolved)
        elif resolved.is_dir():
            for py_file in sorted(resolved.iterdir()):
                if py_file.is_file() and py_file.suffix in (".py", ".pyi"):
                    files_to_check.append(py_file)
                elif py_file.is_dir() and not py_file.name.startswith("."):
                    for sub_root, _, sub_files in py_file.walk():
                        for sf in sub_files:
                            cand = sub_root / sf
                            if cand.suffix in (".py", ".pyi"):
                                files_to_check.append(cand)

    all_violations: list[RuleViolation] = []
    for fp in sorted(set(files_to_check)):
        rel_str = str(fp.relative_to(ws.root))
        content = fp.read_text(encoding="utf-8", errors="replace")
        all_violations.extend(validate_file_content(rel_str, content))

    if all_violations:
        report_text = format_violation_report(all_violations, [])
        ws.ai_dir.mkdir(parents=True, exist_ok=True)
        ws.validation_report.write_text(report_text + "\n", encoding="utf-8")
        raise PatchRejectedError(
            f"{report_text}\n\n📄 Full report saved to: {ws.validation_report}"
        )

    print(
        f"✅ Architectural validation passed: {len(set(files_to_check))} file(s) verified with 0 violations."
    )


def git_rollback() -> None:
    """Roll back the most recent Git commit interactively."""
    root = get_project_root()
    if not is_git_repo(root):
        raise ConfigurationError("Current project is not a Git repository.")

    branch = get_current_branch(root)
    last_commit = run_git(
        args=["log", "-1", "--pretty=%B"],
        cwd=root,
        require_success=True,
    ).stdout.strip()
    confirm = (
        input(f'⚠️  Roll back last commit "{last_commit}"? (y/N): ')
        .strip()
        .lower()
    )
    if confirm != "y":
        print("Rollback cancelled.")
        return

    is_pushed = False
    remotes = run_git(
        args=["remote"],
        cwd=root,
        require_success=False,
    ).stdout.split()
    if "origin" in remotes:
        check_remote = run_git(
            args=["merge-base", "--is-ancestor", "HEAD", f"origin/{branch}"],
            cwd=root,
            require_success=False,
        )
        if check_remote.returncode == 0:
            is_pushed = True

    run_git(args=["reset", "--hard", "HEAD~1"], cwd=root, require_success=True)
    run_git(args=["clean", "-fd"], cwd=root, require_success=True)
    print(f'✨ Commit "{last_commit}" removed.')

    if is_pushed:
        remote_confirm = (
            input(f"Roll back branch '{branch}' on remote via force-push? (y/N): ")
            .strip()
            .lower()
        )
        if remote_confirm == "y":
            run_git(
                args=["push", "--force-with-lease", "origin", branch],
                cwd=root,
                require_success=True,
            )
            print(f"🚀 Remote branch '{branch}' rolled back.")


def manage_branches(branch_name: str | None) -> None:
    """List branches, switch to an existing branch, or create a new branch."""
    root = get_project_root()
    if not is_git_repo(root):
        raise ConfigurationError("Current project is not a Git repository.")

    if branch_name is None:
        current = get_current_branch(root)
        branches = run_git(
            args=["branch", "--list"],
            cwd=root,
            require_success=True,
        ).stdout
        print(f"🌿 Current branch: {current}\n{branches}")
        return

    res = run_git(
        args=["branch", "--list"],
        cwd=root,
        require_success=True,
    ).stdout.splitlines()
    existing_branches = [b.strip().lstrip("* ") for b in res]

    if branch_name in existing_branches:
        run_git(args=["switch", branch_name], cwd=root, require_success=True)
        print(f"🔀 Switched to branch: {branch_name}")
    else:
        run_git(
            args=["switch", "-c", branch_name],
            cwd=root,
            require_success=True,
        )
        print(f"🌱 Created and switched to new branch: {branch_name}")


def run_black(target: str) -> None:
    """Format target path with Black and commit if inside a clean Git repository."""
    root = get_project_root()
    has_git = is_git_repo(root)

    if has_git:
        status_res = run_git(
            args=["status", "--porcelain"],
            cwd=root,
            require_success=True,
        )
        if status_res.stdout.strip():
            raise ConfigurationError(
                "Uncommitted changes detected. Commit them before running Black formatter."
            )

    print(f"🎨 Running Black formatter on: {target}")
    run_process(
        cmd=["black", target],
        cwd=root,
        require_success=True,
        timeout_sec=SUBPROCESS_TIMEOUT_SEC,
    )

    if has_git:
        status_res = run_git(
            args=["status", "--porcelain"],
            cwd=root,
            require_success=True,
        )
        if not status_res.stdout.strip():
            print("ℹ️  No changes (code is already formatted).")
            return

        msg = "Format code with Black"
        run_git(args=["add", "-A"], cwd=root, require_success=True)
        run_git(args=["commit", "-m", msg], cwd=root, require_success=True)
        print(f'📦 Committed: "{msg}"')


def init_project(
    init_git: bool,
    remote_url: str | None,
) -> None:
    """Initialize .ai workspace directory, dumpignore, and optional Git setup."""
    ws = get_workspace_paths()
    ws.ai_dir.mkdir(exist_ok=True)

    print(f"🚀 Initializing AI DevTool in: {ws.root}")
    has_git = is_git_repo(ws.root)
    setup_git = True if init_git else (remote_url is not None)

    if setup_git and not has_git:
        init_res = run_git(
            args=["init", "-b", "main"],
            cwd=ws.root,
            require_success=False,
        )
        if init_res.returncode != 0:
            run_git(args=["init"], cwd=ws.root, require_success=True)
            run_git(
                args=["branch", "-M", "main"],
                cwd=ws.root,
                require_success=True,
            )
        has_git = True

    if has_git:
        gitignore = ws.root / ".gitignore"
        if not gitignore.exists():
            gitignore.write_text(DEFAULT_GITIGNORE_TEMPLATE, encoding="utf-8")
        else:
            content = gitignore.read_text(encoding="utf-8")
            if f"{AI_DIR_NAME}/" not in content and AI_DIR_NAME not in content:
                with open(gitignore, "a", encoding="utf-8") as handle:
                    handle.write(f"\n# AI DevTool workdir\n{AI_DIR_NAME}/\n")

    if remote_url is not None and has_git:
        remotes = run_git(
            args=["remote"],
            cwd=ws.root,
            require_success=False,
        ).stdout.split()
        if "origin" in remotes:
            run_git(
                args=["remote", "set-url", "origin", remote_url],
                cwd=ws.root,
                require_success=True,
            )
        else:
            run_git(
                args=["remote", "add", "origin", remote_url],
                cwd=ws.root,
                require_success=True,
            )

    existing_cfg = load_config(ws.config_file)
    final_remote = remote_url if remote_url is not None else existing_cfg.remote_url
    save_config(
        ws.config_file,
        DevtoolConfig(use_git=has_git, remote_url=final_remote),
    )

    if not ws.default_patch.exists():
        ws.default_patch.write_text(PATCH_PLACEHOLDER_TEXT, encoding="utf-8")

    if not ws.dumpignore_file.exists():
        ws.dumpignore_file.write_text(
            DEFAULT_DUMPIGNORE_TEMPLATE, encoding="utf-8"
        )

    print("✨ Workspace initialized.")
