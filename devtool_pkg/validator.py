"""Industrial architectural validator powered by Ruff JSON engine and strict AST invariants."""

import ast
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Final

from devtool_pkg.constants import RUFF_SELECT_RULES, SUBPROCESS_TIMEOUT_SEC
from devtool_pkg.errors import ConfigurationError
from devtool_pkg.models import OperationStatus, RuleViolation

CYRILLIC_PATTERN: Final[re.Pattern[str]] = re.compile(r"[а-яА-ЯёЁ]")
LAZY_ELLIPSIS_COMMENT_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"^\s*(?:#|//|/\*)\s*(\.\.\.|remaining\s+code|existing\s+code|rest\s+of|остальной\s+код|без\s+изменений)",
    re.IGNORECASE,
)

BARE_COLLECTION_TYPES: Final[frozenset[str]] = frozenset(
    {
        "dict",
        "list",
        "tuple",
        "set",
        "frozenset",
        "Dict",
        "List",
        "Tuple",
        "Set",
        "FrozenSet",
    }
)
BANNED_TYPE_IDENTIFIERS: Final[frozenset[str]] = frozenset({"Any", "object"})
BANNED_FS_HEURISTIC_MODULES: Final[frozenset[str]] = frozenset({"glob", "fnmatch"})
BANNED_FS_HEURISTIC_METHODS: Final[frozenset[str]] = frozenset(
    {"glob", "rglob", "getmtime", "getctime"}
)


def _extract_line(lines: list[str], lineno: int) -> str:
    """Safely extract a 1-indexed source line for violation reporting."""
    if 1 <= lineno <= len(lines):
        return lines[lineno - 1].strip()
    return "<unknown line>"


def validate_commit_message(commit_msg: str) -> list[RuleViolation]:
    """Validate commit message language and single-sentence formatting rules."""
    violations: list[RuleViolation] = []
    cleaned = commit_msg.strip()
    if not cleaned:
        violations.append(
            RuleViolation(
                file_path="<PATCH:COMMIT>",
                line_number=1,
                rule_id="PATCH-COMMIT-EMPTY",
                law_reference="Section III.2 (Commit Message Format)",
                code_snippet="<empty>",
                message="Commit message must not be empty.",
            )
        )
        return violations

    if CYRILLIC_PATTERN.search(cleaned):
        violations.append(
            RuleViolation(
                file_path="<PATCH:COMMIT>",
                line_number=1,
                rule_id="PATCH-COMMIT-LANG",
                law_reference="Section III.3 (English Commit Requirement)",
                code_snippet=cleaned,
                message="Commit message must be written strictly in English (no Cyrillic characters).",
            )
        )

    non_empty_lines = [ln.strip() for ln in cleaned.splitlines() if ln.strip()]
    if len(non_empty_lines) > 1:
        violations.append(
            RuleViolation(
                file_path="<PATCH:COMMIT>",
                line_number=1,
                rule_id="PATCH-COMMIT-SINGLE-SENTENCE",
                law_reference="Section III.3 (Single Sentence Commit)",
                code_snippet=cleaned.splitlines()[0],
                message="Commit message must be a single concise sentence on one line.",
            )
        )
    return violations


def validate_text_block_for_omissions(
    file_path: str,
    block_label: str,
    text: str,
) -> list[RuleViolation]:
    """Scan lines in blocks for lazy code-omission comments (# ... remaining code ...)."""
    violations: list[RuleViolation] = []
    for idx, raw_line in enumerate(text.splitlines(), start=1):
        if LAZY_ELLIPSIS_COMMENT_PATTERN.search(raw_line):
            violations.append(
                RuleViolation(
                    file_path=f"{file_path} ({block_label})",
                    line_number=idx,
                    rule_id="PATCH-LAZY-OMISSION",
                    law_reference="Section III.3 & Law I.8 (Zero Placeholders & No Omissions)",
                    code_snippet=raw_line.strip(),
                    message="Lazy code omission or ellipsis comment detected. Every block must be complete.",
                )
            )
    return violations


def _run_ruff_json_check(rel_path: str, content: str) -> list[RuleViolation]:
    """Run isolated Ruff linter via stdin and convert JSON diagnostics into RuleViolations."""
    env_copy: dict[str, str] = dict(os.environ)
    cmd = [
        "ruff",
        "check",
        "--isolated",
        "--output-format=json",
        f"--select={RUFF_SELECT_RULES}",
        "--stdin-filename",
        rel_path,
        "-",
    ]
    try:
        completed = subprocess.run(
            cmd,
            input=content,
            env=env_copy,
            capture_output=True,
            text=True,
            timeout=SUBPROCESS_TIMEOUT_SEC,
            check=False,
        )
    except FileNotFoundError as exc:
        raise ConfigurationError(
            "Required industrial linter 'ruff' is not installed. Install it via: pip install ruff"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise ConfigurationError(
            f"Ruff validation timed out on '{rel_path}'."
        ) from exc

    raw_out = completed.stdout.strip()
    if not raw_out or raw_out == "[]":
        return []

    try:
        diagnostics: list[dict[str, str | int | dict[str, int]]] = json.loads(
            raw_out
        )
    except json.JSONDecodeError as exc:
        raise ConfigurationError(
            f"Failed to parse Ruff JSON output for '{rel_path}': {exc.msg}"
        ) from exc

    lines = content.splitlines()
    violations: list[RuleViolation] = []
    for item in diagnostics:
        code_val = str(item["code"]) if "code" in item and item["code"] is not None else "SYNTAX"
        msg_val = str(item["message"]) if "message" in item else "Linter violation"
        lineno = 1
        if "location" in item and isinstance(item["location"], dict):
            loc_dict = item["location"]
            if "row" in loc_dict and isinstance(loc_dict["row"], int):
                lineno = loc_dict["row"]

        violations.append(
            RuleViolation(
                file_path=rel_path,
                line_number=lineno,
                rule_id=f"RUFF-{code_val}",
                law_reference="Industrial Static Analysis (Ruff Engine)",
                code_snippet=_extract_line(lines, lineno),
                message=msg_val,
            )
        )
    return violations


class _StrictArchitecturalASTInvariants(ast.NodeVisitor):
    """AST visitor enforcing strictly those architectural invariants not covered by Ruff."""

    def __init__(self, file_path: str, source: str) -> None:
        self._file_path: str = file_path
        self._lines: list[str] = source.splitlines()
        self.violations: list[RuleViolation] = []

    def _add(
        self,
        lineno: int,
        rule_id: str,
        law_ref: str,
        message: str,
    ) -> None:
        self.violations.append(
            RuleViolation(
                file_path=self._file_path,
                line_number=lineno,
                rule_id=rule_id,
                law_reference=law_ref,
                code_snippet=_extract_line(self._lines, lineno),
                message=message,
            )
        )

    def visit_Module(self, node: ast.Module) -> None:
        """Enforce explicit type annotations and immutability on module-level variables."""
        for stmt in node.body:
            if isinstance(stmt, ast.Assign):
                for target in stmt.targets:
                    if isinstance(target, ast.Name) and target.id != "__all__":
                        self._add(
                            lineno=stmt.lineno,
                            rule_id="MODULE-VAR-UNTYPED",
                            law_ref="Law I.9 (Total Static Typing)",
                            message=(
                                f"Module-level variable '{target.id}' must have an explicit "
                                "type annotation (e.g., Final[...])."
                            ),
                        )
            elif isinstance(stmt, ast.AnnAssign):
                if isinstance(stmt.target, ast.Name) and stmt.value is not None:
                    if isinstance(stmt.value, (ast.List, ast.Dict, ast.Set)):
                        self._add(
                            lineno=stmt.lineno,
                            rule_id="NO-GLOBAL-MUTABLE-STATE",
                            law_ref="Law I.5 (Stateless Purity & Immutability)",
                            message=(
                                f"Mutable global collection '{stmt.target.id}' is prohibited at module level. "
                                "Use immutable tuple/frozenset or encapsulate state in a class/database."
                            ),
                        )
        self.generic_visit(node)

    def visit_Import(self, node: ast.Import) -> None:
        """Ban filesystem heuristic globbing modules."""
        for alias in node.names:
            root_mod = alias.name.split(".")[0]
            if root_mod in BANNED_FS_HEURISTIC_MODULES:
                self._add(
                    lineno=node.lineno,
                    rule_id="NO-FS-GLOB-HEURISTICS",
                    law_ref="Law I.4 (No Heuristic File Globbing)",
                    message=f"Importing heuristic file-matching module '{alias.name}' is prohibited.",
                )
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        """Ban filesystem heuristic globbing modules and Any imports."""
        if node.module is not None:
            root_mod = node.module.split(".")[0]
            if root_mod in BANNED_FS_HEURISTIC_MODULES:
                self._add(
                    lineno=node.lineno,
                    rule_id="NO-FS-GLOB-HEURISTICS",
                    law_ref="Law I.4 (No Heuristic File Globbing)",
                    message=f"Importing from '{node.module}' is prohibited.",
                )
        for alias in node.names:
            if alias.name in BANNED_TYPE_IDENTIFIERS:
                self._add(
                    lineno=node.lineno,
                    rule_id="NO-ANY-OR-OBJECT-TYPE",
                    law_ref="Law I.9 (Total Static Typing)",
                    message=f"Importing vague type '{alias.name}' is strictly prohibited.",
                )
        self.generic_visit(node)

    def _check_function_invariants(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
    ) -> None:
        if node.args.defaults or any(d is not None for d in node.args.kw_defaults):
            self._add(
                lineno=node.lineno,
                rule_id="NO-DEFAULT-ARGS",
                law_ref="Law I.3 (No Hidden Defaults in Signatures)",
                message=(
                    f"Function '{node.name}' defines default parameter values. "
                    "All parameters must be passed explicitly."
                ),
            )
        if node.returns is not None:
            self._validate_type_annotation(node.returns)

        all_args: list[ast.arg] = (
            list(node.args.posonlyargs)
            + list(node.args.args)
            + list(node.args.kwonlyargs)
        )
        for arg in all_args:
            if arg.annotation is not None:
                self._validate_type_annotation(arg.annotation)

        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        """Validate synchronous function invariants."""
        self._check_function_invariants(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        """Validate asynchronous function invariants."""
        self._check_function_invariants(node)

    def _validate_type_annotation(self, annotation: ast.AST) -> None:
        """Verify that a type annotation contains no Any, object, or unparameterized generics."""
        if isinstance(annotation, ast.Name):
            if annotation.id in BANNED_TYPE_IDENTIFIERS:
                self._add(
                    lineno=annotation.lineno,
                    rule_id="NO-ANY-OR-OBJECT-TYPE",
                    law_ref="Law I.9 (Total Static Typing)",
                    message=f"Use of vague type '{annotation.id}' in annotations is prohibited.",
                )
            elif annotation.id in BARE_COLLECTION_TYPES:
                self._add(
                    lineno=annotation.lineno,
                    rule_id="NO-UNTYPED-COLLECTIONS",
                    law_ref="Law I.9 (Total Static Typing)",
                    message=(
                        f"Unparameterized collection '{annotation.id}' is prohibited. "
                        f"Specify element types (e.g., {annotation.id.lower()}[...])."
                    ),
                )
            return

        if isinstance(annotation, ast.Subscript):
            if isinstance(annotation.value, ast.Name):
                if annotation.value.id in BANNED_TYPE_IDENTIFIERS:
                    self._add(
                        lineno=annotation.lineno,
                        rule_id="NO-ANY-OR-OBJECT-TYPE",
                        law_ref="Law I.9 (Total Static Typing)",
                        message=f"Use of '{annotation.value.id}' is prohibited.",
                    )
            self._validate_type_annotation(annotation.slice)
            return

        if isinstance(annotation, ast.BinOp) and isinstance(annotation.op, ast.BitOr):
            self._validate_type_annotation(annotation.left)
            self._validate_type_annotation(annotation.right)
            return

        if isinstance(annotation, ast.Tuple):
            for elt in annotation.elts:
                self._validate_type_annotation(elt)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        """Enforce frozen=True on all @dataclass definitions."""
        for dec in node.decorator_list:
            if isinstance(dec, ast.Name) and dec.id == "dataclass":
                self._add(
                    lineno=node.lineno,
                    rule_id="IMMUTABLE-DATACLASS",
                    law_ref="Law I.9 (Immutable Data Models)",
                    message=f"Class '{node.name}' uses @dataclass without frozen=True. Use @dataclass(frozen=True).",
                )
            elif (
                isinstance(dec, ast.Call)
                and isinstance(dec.func, ast.Name)
                and dec.func.id == "dataclass"
            ):
                has_frozen = any(
                    kw.arg == "frozen"
                    and isinstance(kw.value, ast.Constant)
                    and kw.value.value is True
                    for kw in dec.keywords
                )
                if not has_frozen:
                    self._add(
                        lineno=node.lineno,
                        rule_id="IMMUTABLE-DATACLASS",
                        law_ref="Law I.9 (Immutable Data Models)",
                        message=f"Class '{node.name}' @dataclass decorator must explicitly set frozen=True.",
                    )
        self.generic_visit(node)

    def visit_Global(self, node: ast.Global) -> None:
        """Ban global statement."""
        self._add(
            lineno=node.lineno,
            rule_id="NO-GLOBAL-MUTABLE-STATE",
            law_ref="Law I.5 (Stateless Purity & No Global State)",
            message="The 'global' statement is strictly prohibited.",
        )

    def visit_Nonlocal(self, node: ast.Nonlocal) -> None:
        """Ban nonlocal statement."""
        self._add(
            lineno=node.lineno,
            rule_id="NO-GLOBAL-MUTABLE-STATE",
            law_ref="Law I.5 (Stateless Purity & No Global State)",
            message="The 'nonlocal' statement is strictly prohibited.",
        )

    def visit_Pass(self, node: ast.Pass) -> None:
        """Ban pass statement everywhere."""
        self._add(
            lineno=node.lineno,
            rule_id="ZERO-STUBS-PASS",
            law_ref="Law I.8 (Zero Stubs & Zero Placeholders)",
            message="The 'pass' statement is strictly prohibited. Write complete logic or a docstring for empty classes.",
        )

    def visit_Expr(self, node: ast.Expr) -> None:
        """Ban ellipsis (...) used as a statement stub."""
        if isinstance(node.value, ast.Constant) and node.value.value is Ellipsis:
            self._add(
                lineno=node.lineno,
                rule_id="ZERO-STUBS-ELLIPSIS",
                law_ref="Law I.8 (Zero Stubs & Zero Placeholders)",
                message="Ellipsis '...' placeholder statement is strictly prohibited.",
            )
        self.generic_visit(node)

    def visit_Raise(self, node: ast.Raise) -> None:
        """Ban raising NotImplementedError."""
        if node.exc is not None:
            exc_name = ""
            if isinstance(node.exc, ast.Name):
                exc_name = node.exc.id
            elif isinstance(node.exc, ast.Call) and isinstance(node.exc.func, ast.Name):
                exc_name = node.exc.func.id
            if exc_name in ("NotImplementedError", "NotImplemented"):
                self._add(
                    lineno=node.lineno,
                    rule_id="ZERO-STUBS-NOT-IMPLEMENTED",
                    law_ref="Law I.8 (Zero Stubs & Zero Placeholders)",
                    message=f"Raising '{exc_name}' is prohibited. Deliver complete production-ready implementations.",
                )
        self.generic_visit(node)

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        """Require every except handler to re-raise or terminate execution."""
        has_raise_or_exit = False
        for child in ast.walk(node):
            if isinstance(child, ast.Raise):
                has_raise_or_exit = True
                break
            if isinstance(child, ast.Call):
                if isinstance(child.func, ast.Attribute) and child.func.attr == "exit":
                    has_raise_or_exit = True
                    break
                if isinstance(child.func, ast.Name) and child.func.id == "exit":
                    has_raise_or_exit = True
                    break

        if not has_raise_or_exit:
            self._add(
                lineno=node.lineno,
                rule_id="FAIL-FAST-SILENT-EXCEPT",
                law_ref="Law I.2 (Fail-Fast & Zero Silent Failures)",
                message=(
                    "Exception handler does not re-raise or terminate execution. "
                    "Swallowing exceptions or returning fallback values in except blocks is prohibited."
                ),
            )
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        """Check for fallback calls, NaN construction, filesystem globbing, and subprocess isolation."""
        if isinstance(node.func, ast.Name):
            if node.func.id == "getattr" and len(node.args) > 2:
                self._add(
                    lineno=node.lineno,
                    rule_id="NO-FALLBACK-CALLS",
                    law_ref="Law I.3 (No Hidden Defaults or Fallbacks)",
                    message="Calling getattr() with a 3rd default argument is prohibited.",
                )
            elif node.func.id == "float" and node.args:
                first_arg = node.args[0]
                if (
                    isinstance(first_arg, ast.Constant)
                    and isinstance(first_arg.value, str)
                    and first_arg.value.strip().lower() in ("nan", "+nan", "-nan")
                ):
                    self._add(
                        lineno=node.lineno,
                        rule_id="FAIL-FAST-NO-NAN",
                        law_ref="Law I.2 (No NaN Substitution)",
                        message="Constructing float('nan') is strictly prohibited.",
                    )

        elif isinstance(node.func, ast.Attribute):
            attr_name = node.func.attr
            if attr_name in ("setdefault", "getenv"):
                self._add(
                    lineno=node.lineno,
                    rule_id="NO-FALLBACK-CALLS",
                    law_ref="Law I.3 (No Hidden Defaults or Fallbacks)",
                    message=(
                        f"Method '.{attr_name}()' is prohibited because it introduces implicit fallback/default behavior."
                    ),
                )
            elif attr_name == "get":
                has_default_arg = True if len(node.args) >= 2 else any(
                    kw.arg == "default" for kw in node.keywords
                )
                is_os_environ = (
                    isinstance(node.func.value, ast.Attribute)
                    and node.func.value.attr == "environ"
                )
                if has_default_arg or is_os_environ:
                    self._add(
                        lineno=node.lineno,
                        rule_id="NO-FALLBACK-CALLS",
                        law_ref="Law I.3 (No Hidden Defaults or Fallbacks)",
                        message=(
                            "Calling '.get(key, default)' or 'os.environ.get()' is prohibited. "
                            "Validate required keys explicitly without fallback values."
                        ),
                    )
            elif attr_name in BANNED_FS_HEURISTIC_METHODS:
                self._add(
                    lineno=node.lineno,
                    rule_id="NO-FS-GLOB-HEURISTICS",
                    law_ref="Law I.4 & I.6 (No Filesystem Globbing or mtime Heuristics)",
                    message=f"Heuristic filesystem call '.{attr_name}()' is strictly prohibited.",
                )

            if (
                isinstance(node.func.value, ast.Name)
                and node.func.value.id == "subprocess"
                and attr_name in ("run", "Popen", "call", "check_output", "check_call")
            ):
                kw_map = {kw.arg for kw in node.keywords if kw.arg is not None}
                if "env" not in kw_map or "timeout" not in kw_map:
                    self._add(
                        lineno=node.lineno,
                        rule_id="SUBPROCESS-ISOLATION",
                        law_ref="Law I.7 (Runtime & Process Isolation)",
                        message=(
                            f"subprocess.{attr_name}() must explicitly specify both 'env=...' and 'timeout=...'"
                        ),
                    )

        self.generic_visit(node)

    def _check_or_fallback_expr(self, expr: ast.AST) -> None:
        """Flag value-fallback usage of 'or' when the fallback operand is a non-boolean literal or container."""
        if isinstance(expr, ast.BoolOp) and isinstance(expr.op, ast.Or):
            rightmost = expr.values[-1]
            is_fallback = False
            if isinstance(rightmost, (ast.List, ast.Dict, ast.Set, ast.Tuple)):
                is_fallback = True
            elif isinstance(rightmost, ast.Constant):
                if not isinstance(rightmost.value, bool) and rightmost.value is not None:
                    is_fallback = True
            if is_fallback:
                self._add(
                    lineno=expr.lineno,
                    rule_id="NO-FALLBACK-OR",
                    law_ref="Law I.3 (No Implicit 'or' Fallbacks)",
                    message=(
                        "Using 'x or <default_literal>' as a value fallback is prohibited. "
                        "Use explicit conditional validation."
                    ),
                )

    def visit_Assign(self, node: ast.Assign) -> None:
        """Check assignments for value-fallback 'or' expressions."""
        self._check_or_fallback_expr(node.value)
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        """Check annotated assignments for types and value-fallback 'or' expressions."""
        self._validate_type_annotation(node.annotation)
        if node.value is not None:
            self._check_or_fallback_expr(node.value)
        self.generic_visit(node)

    def visit_Return(self, node: ast.Return) -> None:
        """Check return statements for value-fallback 'or' expressions."""
        if node.value is not None:
            self._check_or_fallback_expr(node.value)
        self.generic_visit(node)


def validate_file_content(rel_path: str, content: str) -> list[RuleViolation]:
    """Run Ruff static analysis and strict AST architectural invariants on a file."""
    violations: list[RuleViolation] = []
    violations.extend(validate_text_block_for_omissions(rel_path, "FILE", content))

    suffix = Path(rel_path).suffix.lower()
    first_line = content.splitlines()[0].strip() if content.splitlines() else ""
    is_python = (
        True
        if suffix in (".py", ".pyi")
        else (first_line.startswith("#!") and "python" in first_line)
    )

    if is_python:
        ruff_violations = _run_ruff_json_check(rel_path, content)
        violations.extend(ruff_violations)

        has_syntax_err = any(v.rule_id == "RUFF-SYNTAX" for v in ruff_violations)
        if not has_syntax_err:
            compiled_code = compile(
                content,
                filename=rel_path,
                mode="exec",
                flags=ast.PyCF_ONLY_AST,
            )
            tree: ast.Module = compiled_code
            ast_checker = _StrictArchitecturalASTInvariants(
                file_path=rel_path,
                source=content,
            )
            ast_checker.visit(tree)
            violations.extend(ast_checker.violations)

    return violations


def format_violation_report(
    violations: list[RuleViolation],
    op_statuses: list[OperationStatus],
) -> str:
    """Format a comprehensive, copy-paste-ready rejection report with ordered block summary."""
    grouped: dict[str, list[RuleViolation]] = {}
    for v in violations:
        if v.file_path not in grouped:
            grouped[v.file_path] = []
        grouped[v.file_path].append(v)

    sep_heavy = "=" * 76
    sep_light = "-" * 76
    out: list[str] = [
        sep_heavy,
        f"🚨 PATCH REJECTED: ARCHITECTURAL & STRUCTURAL VIOLATIONS DETECTED (Total: {len(violations)})",
        "All-or-Nothing Rollback: Zero files on disk were modified.",
        sep_heavy,
    ]

    if op_statuses:
        out.append("")
        out.append("📋 Patch Operations Summary (in patch order):")
        out.append(sep_light)
        for st in op_statuses:
            icon = "✅" if st.passed else "❌"
            out.append(f"  {icon} {st.op_type:<11} {st.rel_path} ({st.detail})")
        out.append(sep_light)

    out.append("")

    for file_path in sorted(grouped.keys()):
        file_violations = sorted(
            grouped[file_path], key=lambda item: item.line_number
        )
        out.append(f"File: {file_path} (Violations: {len(file_violations)})")
        out.append(sep_light)
        for v in file_violations:
            out.append(
                f"  ❌ Line {v.line_number:<4} | [{v.rule_id}] {v.law_reference}"
            )
            out.append(f"     Code:    {v.code_snippet}")
            out.append(f"     Reason:  {v.message}")
            out.append("")

    out.append(sep_heavy)
    out.append(
        "ACTION REQUIRED: Eradicate ALL root causes listed above and provide a complete, compliant devtool patch."
    )
    out.append(sep_heavy)
    return "\n".join(out)
