"""Command-line interface parser and command dispatcher for devtool."""

import argparse
import sys

from devtool_pkg.constants import DEFAULT_DIFF_COMMIT, DEFAULT_TARGET_DIR
from devtool_pkg.dumper import dump_project, export_diff, export_tree
from devtool_pkg.errors import DevtoolError
from devtool_pkg.patcher import (
    apply_patch,
    git_rollback,
    init_project,
    manage_branches,
    run_black,
    validate_existing_files,
)


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser with all subcommands."""
    parser = argparse.ArgumentParser(
        prog="devtool",
        description="AI DevTool - Strict Architectural Patch & Context Manager",
    )
    subparsers = parser.add_subparsers(dest="command")

    init_p = subparsers.add_parser(
        "init", help="Initialize .ai workspace in the project"
    )
    init_p.add_argument(
        "--git", action="store_true", help="Initialize local Git repository"
    )
    init_p.add_argument("--remote", help="Remote Git repository URL")

    dump_p = subparsers.add_parser(
        "dump", help="Build project skeleton or targeted file dump for LLM"
    )
    dump_p.add_argument(
        "targets",
        nargs="*",
        help="Specific files or folders to dump. Omit to build compact skeleton.",
    )
    dump_p.add_argument(
        "-o", "--output", help="Output file path (default: .ai/dump.txt)"
    )
    dump_p.add_argument(
        "--full",
        action="store_true",
        help="Dump all project files in full (legacy mode)",
    )
    dump_p.add_argument(
        "--since",
        help="Dump files modified since a specific commit (e.g. HEAD~3)",
    )

    diff_p = subparsers.add_parser(
        "diff", help="Export git diff since a specific commit"
    )
    diff_p.add_argument(
        "commit",
        nargs="?",
        default=DEFAULT_DIFF_COMMIT,
        help="Base commit for diff (default: HEAD~1)",
    )
    diff_p.add_argument(
        "-o", "--output", help="Output file path (default: .ai/diff.txt)"
    )
    diff_p.add_argument(
        "--stdout", action="store_true", help="Print diff to stdout"
    )

    apply_p = subparsers.add_parser(
        "apply",
        help="Validate and atomically apply an LLM patch (all-or-nothing)",
    )
    apply_p.add_argument(
        "file", nargs="?", help="Path to patch file (default: .ai/patch.txt)"
    )
    apply_p.add_argument(
        "--push",
        action="store_true",
        help="Push commit to remote origin after applying",
    )

    check_p = subparsers.add_parser(
        "check",
        help="Run strict architectural validation on existing files",
    )
    check_p.add_argument(
        "targets",
        nargs="*",
        help="Files or directories to validate (default: current project)",
    )

    branch_p = subparsers.add_parser(
        "branch", help="List, switch, or create a Git branch"
    )
    branch_p.add_argument(
        "name", nargs="?", help="Branch name (omit to list branches)"
    )

    black_p = subparsers.add_parser(
        "black", help="Format code with Black and commit"
    )
    black_p.add_argument(
        "target",
        nargs="?",
        default=DEFAULT_TARGET_DIR,
        help="File or directory to format (default: '.')",
    )

    tree_p = subparsers.add_parser(
        "tree", help="Export directory skeleton tree with signatures"
    )
    tree_p.add_argument(
        "target",
        nargs="?",
        default=DEFAULT_TARGET_DIR,
        help="Target directory (default: '.')",
    )
    tree_p.add_argument(
        "-o", "--output", help="Output file path (default: .ai/dump.txt)"
    )
    tree_p.add_argument(
        "--stdout", action="store_true", help="Print tree to stdout"
    )

    subparsers.add_parser("rollback", help="Roll back the last Git commit")
    return parser


def main() -> None:
    """CLI entrypoint with fail-fast domain error reporting."""
    parser = build_parser()
    args = parser.parse_args()

    try:
        if args.command == "init":
            init_project(
                init_git=bool(args.git),
                remote_url=args.remote,
            )
        elif args.command == "dump":
            dump_project(
                output_path=args.output,
                targets=list(args.targets),
                full_dump=bool(args.full),
                since_commit=args.since,
            )
        elif args.command == "diff":
            export_diff(
                commit=str(args.commit),
                output_path=args.output,
                to_stdout=bool(args.stdout),
            )
        elif args.command == "apply":
            apply_patch(
                patch_path=args.file,
                force_push=bool(args.push),
            )
        elif args.command == "check":
            validate_existing_files(targets=list(args.targets))
        elif args.command == "branch":
            manage_branches(branch_name=args.name)
        elif args.command == "black":
            run_black(target=str(args.target))
        elif args.command == "rollback":
            git_rollback()
        elif args.command == "tree":
            export_tree(
                target=str(args.target),
                output_path=args.output,
                to_stdout=bool(args.stdout),
            )
        else:
            parser.print_help()
    except DevtoolError as exc:
        print(f"\n{exc}\n", file=sys.stderr)
        sys.exit(1)
