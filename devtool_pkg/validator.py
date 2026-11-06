"""Universal AST, token, and text architectural law validator."""

import ast
import re
import tokenize
from io import BytesIO
from pathlib import Path
from typing import Final

from devtool_pkg.models import OperationStatus, RuleViolation

CYRILLIC_PATTERN: Final[re.Pattern[str]] = re.compile(r"[а-яА-ЯёЁ]")
SUPPRESSION_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"#\s*(type:\s*ignore|noqa|pragma:\s*no\s*cover|pylint:\s*disable|ruff:\s*noqa)",
    re.IGNORECASE,
)
TODO_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"(?:#|//|/\*)\s*\b(TODO|FIXME|XXX|HACK)\b",
    re.IGNORECASE,
)
LAZY_OMISSION_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"(?:#|//|/\*)\s*(\.\.\.|remaining\s+code|existing\s+code|rest\s+of|остальной\s+код|без\s+изменений)",
    re.IGNORECASE,
)

BANNED_BROAD_EXCEPTIONS: Final[frozenset[str]] = frozenset(
    {"Exception", "BaseException"}
)
BANNED_TYPE_IDENTIFIERS: Final[frozenset[str]] = frozenset({"Any", "object"})
BARE_COLLECTION_TYPES: Final[frozenset[str]] = frozenset(
    {"dict", "list", "tuple", "set", "frozenset", "Dict", "List", "Tuple", "Set", "FrozenSet"}
)
BANNED_FS_HEURISTIC_MODULES: Final[frozenset[str]] = frozenset({"glob", "fnmatch"})
BANNED_FS_HEURISTIC_METHODS: Final[frozenset[str]] = frozenset(
    {"glob", "rglob", "getmtime", "getctime"}
)
BANNED_FALLBACK_METHODS: Final[frozenset[str]] = frozenset({"get", "setdefault", "getenv"})


def _extract_line(lines: list[str], lineno: int) -> str:
    """Safely extract a 1-indexed source line for violation reporting."""
    if 1 <= lineno <= len(lines):
        return lines[lineno - 1].strip()
    return "<unknown line>"


def validate_commit_message(commit_msg: str) -> list[RuleViolation]:
    """Validate commit message language and formatting rules."""
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
    """Scan comment lines in patch blocks for lazy omissions, placeholders, and suppressions."""
    violations: list[RuleViolation] = []
    lines = text.splitlines()

    for idx, raw_line in enumerate(lines, start=1):
        stripped = raw_line.strip()
        if not (
            stripped.startswith("#")
            or stripped.startswith("//")
            or stripped.startswith("/*")
        ):
            continue

        if LAZY_OMISSION_PATTERN.search(stripped):
            violations.append(
                RuleViolation(
                    file_path=f"{file_path} ({block_label})",
                    line_number=idx,
                    rule_id="PATCH-LAZY-OMISSION",
                    law_reference="Section III.3 & Law I.8 (Zero Placeholders & No Omissions)",
                    code_snippet=stripped,
                    message="Lazy code omission or ellipsis comment detected. Every block must be complete.",
                )
            )
        if TODO_PATTERN.search(stripped):
            violations.append(
                RuleViolation(
                    file_path=f"{file_path} ({block_label})",
                    line_number=idx,
                    rule_id="NO-TODO-PLACEHOLDERS",
                    law_reference="Law I.8 (Zero Stubs & Zero Placeholders)",
                    code_snippet=stripped,
                    message="Unfinished code markers (TODO/FIXME/XXX/HACK) are strictly prohibited.",
                )
            )
        if SUPPRESSION_PATTERN.search(stripped):
            violations.append(
                RuleViolation(
                    file_path=f"{file_path} ({block_label})",
                    line_number=idx,
                    rule_id="NO-LINTER-SUPPRESSION",
                    law_reference="Law I.9 (Strict Type Safety & Zero Suppression)",
                    code_snippet=stripped,
                    message="Suppressing type checkers or linters via inline comments is prohibited.",
                )
            )
    return violations


class _PythonASTValidator(ast.NodeVisitor):
    """Deep AST visitor enforcing all universal architectural laws on Python files."""

    def __init__(self, file_path: str, source: str) -> None:
        self._file_path: str = file_path
        self._source: str = source
        self._lines: list[str] = source.splitlines()
        self.violations: list[RuleViolation] = []
        self._scope_depth: int = 0

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

    def validate_tokens(self) -> None:
        """Check actual Python comment tokens for Cyrillic, TODOs, omissions, and suppressions."""
        tokens = tokenize.tokenize(
            BytesIO(self._source.encode("utf-8")).readline
        )
        for tok_type, tok_str, (srow, _), _, _ in tokens:
            if tok_type == tokenize.COMMENT:
                if CYRILLIC_PATTERN.search(tok_str):
                    self._add(
                        lineno=srow,
                        rule_id="COMMENT-ENGLISH-ONLY",
                        law_ref="Section III.2 (English Code Comments)",
                        message="Code comments must be written strictly in English.",
                    )
                if LAZY_OMISSION_PATTERN.search(tok_str):
                    self._add(
                        lineno=srow,
                        rule_id="PATCH-LAZY-OMISSION",
                        law_ref="Section III.3 & Law I.8 (Zero Placeholders & No Omissions)",
                        message="Lazy code omission or ellipsis comment detected.",
                    )
                if TODO_PATTERN.search(tok_str):
                    self._add(
                        lineno=srow,
                        rule_id="NO-TODO-PLACEHOLDERS",
                        law_ref="Law I.8 (Zero Stubs & Zero Placeholders)",
                        message="Unfinished code markers (TODO/FIXME/XXX/HACK) are strictly prohibited.",
                    )
                if SUPPRESSION_PATTERN.search(tok_str):
                    self._add(
                        lineno=srow,
                        rule_id="NO-LINTER-SUPPRESSION",
                        law_ref="Law I.9 (Strict Type Safety & Zero Suppression)",
                        message="Suppressing type checkers or linters via inline comments is prohibited.",
                    )

    def visit_Module(self, node: ast.Module) -> None:
        """Check module-level statements for mutable global state."""
        for stmt in node.body:
            if isinstance(stmt, ast.Assign):
                for target in stmt.targets:
                    if isinstance(target, ast.Name) and target.id != "__all__":
                        self._add(
                            lineno=stmt.lineno,
                            rule_id="STRICT-TYPE-ANNOTATIONS",
                            law_ref="Law I.9 (Total Static Typing)",
                            message=(
                                f"Module-level variable '{target.id}' must have an explicit "
                                "type annotation (preferably Final[...])."
                            ),
                        )
            elif isinstance(stmt, ast.AnnAssign):
                if isinstance(stmt.target, ast.Name) and stmt.value is not None:
                    if isinstance(stmt.value, (ast.List, ast.Dict, ast.Set)):
                        self._add(
                            lineno=stmt.lineno,
                            rule_id="NO-GLOBAL-MUTABLE-STATE",
                            law_ref="Law I.5 (Pure State & Immutability)",
                            message=(
                                f"Mutable global collection '{stmt.target.id}' is prohibited at module level. "
                                "Use immutable tuple or frozenset."
                            ),
                        )
        self.generic_visit(node)

    def visit_Import(self, node: ast.Import) -> None:
        """Enforce top-level imports only and ban filesystem heuristic modules."""
        if self._scope_depth > 0:
            self._add(
                lineno=node.lineno,
                rule_id="STRICT-DAG-NO-LAZY-IMPORT",
                law_ref="Law I.6 (Strict DAG & No Runtime Imports)",
                message="Runtime imports inside functions, classes, or blocks are strictly prohibited.",
            )
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
        """Enforce top-level imports and ban Any or globbing imports."""
        if self._scope_depth > 0:
            self._add(
                lineno=node.lineno,
                rule_id="STRICT-DAG-NO-LAZY-IMPORT",
                law_ref="Law I.6 (Strict DAG & No Runtime Imports)",
                message="Runtime imports inside functions, classes, or blocks are strictly prohibited.",
            )
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

    def _validate_function_node(
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

        if node.returns is None:
            self._add(
                lineno=node.lineno,
                rule_id="STRICT-TYPE-ANNOTATIONS",
                law_ref="Law I.9 (Total Static Typing)",
                message=f"Function '{node.name}' is missing a return type annotation.",
            )
        else:
            self._validate_type_annotation(node.returns)

        all_args: list[ast.arg] = (
            list(node.args.posonlyargs)
            + list(node.args.args)
            + list(node.args.kwonlyargs)
        )
        if node.args.vararg is not None:
            all_args.append(node.args.vararg)
        if node.args.kwarg is not None:
            all_args.append(node.args.kwarg)

        for arg in all_args:
            if arg.arg in ("self", "cls"):
                continue
            if arg.annotation is None:
                self._add(
                    lineno=node.lineno,
                    rule_id="STRICT-TYPE-ANNOTATIONS",
                    law_ref="Law I.9 (Total Static Typing)",
                    message=f"Parameter '{arg.arg}' in function '{node.name}' is missing a type annotation.",
                )
            else:
                self._validate_type_annotation(arg.annotation)

        self._scope_depth += 1
        self.generic_visit(node)
        self._scope_depth -= 1

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        """Validate synchronous function definitions."""
        self._validate_function_node(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        """Validate asynchronous function definitions."""
        self._validate_function_node(node)

    def _validate_type_annotation(self, annotation: ast.AST) -> None:
        """Recursively verify that a type annotation contains no Any, object, or bare generics."""
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
        """Enforce frozen=True on dataclasses and check class body."""
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

        self._scope_depth += 1
        self.generic_visit(node)
        self._scope_depth -= 1

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

    def visit_Try(self, node: ast.Try) -> None:
        """Track scope depth inside try blocks and validate handlers."""
        self._scope_depth += 1
        self.generic_visit(node)
        self._scope_depth -= 1

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        """Enforce Fail-Fast exception handling rules."""
        if node.type is None:
            self._add(
                lineno=node.lineno,
                rule_id="FAIL-FAST-BARE-EXCEPT",
                law_ref="Law I.2 (Zero Silent Failures & No Bare Except)",
                message="Bare 'except:' clause is strictly prohibited. Catch specific domain/runtime exceptions.",
            )
        elif isinstance(node.type, ast.Name) and node.type.id in BANNED_BROAD_EXCEPTIONS:
            self._add(
                lineno=node.lineno,
                rule_id="FAIL-FAST-BARE-EXCEPT",
                law_ref="Law I.2 (Zero Silent Failures & No Broad Exception)",
                message=f"Catching broad '{node.type.id}' is strictly prohibited.",
            )
        elif isinstance(node.type, ast.Tuple):
            for elt in node.type.elts:
                if isinstance(elt, ast.Name) and elt.id in BANNED_BROAD_EXCEPTIONS:
                    self._add(
                        lineno=node.lineno,
                        rule_id="FAIL-FAST-BARE-EXCEPT",
                        law_ref="Law I.2 (Zero Silent Failures & No Broad Exception)",
                        message=f"Catching broad '{elt.id}' inside exception tuple is prohibited.",
                    )

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
        """Validate function and method calls for fallbacks, NaN, globbing, and subprocess safety."""
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
            if attr_name in BANNED_FALLBACK_METHODS:
                self._add(
                    lineno=node.lineno,
                    rule_id="NO-FALLBACK-CALLS",
                    law_ref="Law I.3 (No Hidden Defaults or Fallbacks)",
                    message=(
                        f"Method '.{attr_name}()' is prohibited because it introduces implicit fallback/default behavior. "
                        "Use direct key indexing with explicit validation."
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
                if attr_name == "run" and "check" not in kw_map:
                    self._add(
                        lineno=node.lineno,
                        rule_id="SUBPROCESS-ISOLATION",
                        law_ref="Law I.2 & I.7 (Explicit Subprocess Exit Code Policy)",
                        message="subprocess.run() must explicitly specify 'check=...'",
                    )

        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        """Ban NaN attributes, st_mtime, and cross-object private attribute access."""
        if node.attr.lower() == "nan" or node.attr == "NA":
            self._add(
                lineno=node.lineno,
                rule_id="FAIL-FAST-NO-NAN",
                law_ref="Law I.2 (No NaN Substitution)",
                message=f"Accessing '{node.attr}' (NaN/NA constant) is strictly prohibited.",
            )
        if node.attr in ("st_mtime", "st_ctime", "st_mtime_ns"):
            self._add(
                lineno=node.lineno,
                rule_id="NO-FS-GLOB-HEURISTICS",
                law_ref="Law I.6 (No mtime Scanning)",
                message=f"Reading '{node.attr}' for filesystem state heuristics is prohibited.",
            )
        if node.attr.startswith("_") and not node.attr.startswith("__"):
            is_self_or_cls = isinstance(node.value, ast.Name) and node.value.id in (
                "self",
                "cls",
            )
            if not is_self_or_cls:
                self._add(
                    lineno=node.lineno,
                    rule_id="NO-CROSS-MODULE-PRIVATE-ACCESS",
                    law_ref="Law I.9 (Public API Boundary Contract)",
                    message=(
                        f"Accessing private attribute/method '{node.attr}' on an external object/module is prohibited."
                    ),
                )
        self.generic_visit(node)

    def _check_or_fallback_expr(self, expr: ast.AST) -> None:
        """Flag value-fallback usage of boolean 'or' outside conditional tests."""
        if isinstance(expr, ast.BoolOp) and isinstance(expr.op, ast.Or):
            self._add(
                lineno=expr.lineno,
                rule_id="NO-FALLBACK-OR",
                law_ref="Law I.3 (No Implicit 'or' Fallbacks)",
                message=(
                    "Using 'x or fallback' as a value expression is prohibited. "
                    "Use explicit conditional branches and strict validation."
                ),
            )

    def visit_Assign(self, node: ast.Assign) -> None:
        """Check assignments for 'or' fallback patterns."""
        self._check_or_fallback_expr(node.value)
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        """Check annotated assignments for types and 'or' fallback patterns."""
        self._validate_type_annotation(node.annotation)
        if node.value is not None:
            self._check_or_fallback_expr(node.value)
        self.generic_visit(node)

    def visit_Return(self, node: ast.Return) -> None:
        """Check return expressions for 'or' fallback patterns and NotImplemented."""
        if node.value is not None:
            self._check_or_fallback_expr(node.value)
            if isinstance(node.value, ast.Name) and node.value.id == "NotImplemented":
                self._add(
                    lineno=node.lineno,
                    rule_id="ZERO-STUBS-NOT-IMPLEMENTED",
                    law_ref="Law I.8 (Zero Stubs)",
                    message="Returning 'NotImplemented' is prohibited.",
                )
        self.generic_visit(node)


def validate_file_content(rel_path: str, content: str) -> list[RuleViolation]:
    """Run all universal text and language-specific architectural checks on a file."""
    violations: list[RuleViolation] = []

    suffix = Path(rel_path).suffix.lower()
    first_line = content.splitlines()[0].strip() if content.splitlines() else ""
    is_python = (
        True
        if suffix in (".py", ".pyi")
        else (first_line.startswith("#!") and "python" in first_line)
    )

    if is_python:
        compiled_code = compile(
            content,
            filename=rel_path,
            mode="exec",
            flags=ast.PyCF_ONLY_AST,
        )
        tree: ast.Module = compiled_code
        ast_validator = _PythonASTValidator(file_path=rel_path, source=content)
        ast_validator.validate_tokens()
        ast_validator.visit(tree)
        violations.extend(ast_validator.violations)
    else:
        violations.extend(validate_text_block_for_omissions(rel_path, "FILE", content))

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
            out.append(f"  {icon} {st.op_type:<8} {st.rel_path} ({st.detail})")
        out.append(sep_light)

    out.append("")

    for file_path in sorted(grouped.keys()):
        file_violations = sorted(grouped[file_path], key=lambda item: item.line_number)
        out.append(f"File: {file_path} (Violations: {len(file_violations)})")
        out.append(sep_light)
        for v in file_violations:
            out.append(f"  ❌ Line {v.line_number:<4} | [{v.rule_id}] {v.law_reference}")
            out.append(f"     Code:    {v.code_snippet}")
            out.append(f"     Reason:  {v.message}")
            out.append("")

    out.append(sep_heavy)
    out.append(
        "ACTION REQUIRED: Eradicate ALL root causes listed above and provide a complete, compliant devtool patch."
    )
    out.append(sep_heavy)
    return "\n".join(out)
