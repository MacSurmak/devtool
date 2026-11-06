"""Language-aware signature and structural skeleton extractors for project views."""

import ast
import json
import re
from pathlib import Path
from typing import Final

from devtool_pkg.constants import (
    MAX_BASH_SIGNATURES_LIMIT,
    MAX_CLASS_FIELDS_LIMIT,
    MAX_CONFIG_KEYS_LIMIT,
    MAX_MARKDOWN_HEADERS_LIMIT,
    MAX_SIGNATURES_LIMIT,
    SIGNATURE_REST_MAX_LEN,
)
from devtool_pkg.errors import ConfigurationError

JS_TS_SIG_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"^\s*(export\s+)?(async\s+)?(function|class|interface|type)\s+([A-Za-z0-9_$]+)(.*?)(?:\{|=|$)",
    re.MULTILINE,
)

GO_RUST_SIG_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"^\s*(pub\s+)?(func|fn|type|struct|impl)\s+([A-Za-z0-9_]+)(.*?)(?:\{|$)",
    re.MULTILINE,
)

BASH_SIG_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"^\s*(function\s+)?([A-Za-z0-9_-]+)\s*\(\)\s*\{",
    re.MULTILINE,
)


def extract_python_signatures(content: str) -> list[str]:
    """Extract imports, typed constants, class fields, methods, and functions from Python code."""
    try:
        tree = ast.parse(content)
    except SyntaxError as exc:
        raise ConfigurationError(
            f"Failed to parse Python AST for signature extraction at line {exc.lineno}: {exc.msg}"
        ) from exc

    sigs: list[str] = []
    imported_modules: list[str] = []

    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported_modules.append(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported_modules.append(node.module)

    if imported_modules:
        unique_mods = list(dict.fromkeys(imported_modules))
        shown_mods = ", ".join(unique_mods)
        sigs.append(f"[imports: {shown_mods}]")

    for node in tree.body:
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            var_name = node.target.id
            ann_str = ast.unparse(node.annotation)
            sigs.append(f"const {var_name}: {ann_str}")

        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id.isupper():
                    sigs.append(f"const {target.id}")

        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            async_prefix = "async " if isinstance(node, ast.AsyncFunctionDef) else ""
            args_str = ast.unparse(node.args)
            ret_str = (
                f" -> {ast.unparse(node.returns)}"
                if node.returns is not None
                else ""
            )
            sigs.append(f"{async_prefix}def {node.name}({args_str}){ret_str}")

        elif isinstance(node, ast.ClassDef):
            decorators = [
                f"@{ast.unparse(dec)}" for dec in node.decorator_list
            ]
            dec_prefix = f"{' '.join(decorators)} " if decorators else ""
            bases_str = (
                f"({', '.join(ast.unparse(b) for b in node.bases)})"
                if node.bases
                else ""
            )
            sigs.append(f"{dec_prefix}class {node.name}{bases_str}:")

            field_count = 0
            for sub in node.body:
                if (
                    isinstance(sub, ast.AnnAssign)
                    and isinstance(sub.target, ast.Name)
                    and field_count < MAX_CLASS_FIELDS_LIMIT
                ):
                    f_name = sub.target.id
                    f_type = ast.unparse(sub.annotation)
                    sigs.append(f"  {f_name}: {f_type}")
                    field_count += 1
                elif (
                    isinstance(sub, ast.Assign)
                    and len(sub.targets) == 1
                    and field_count < MAX_CLASS_FIELDS_LIMIT
                ):
                    target_node = sub.targets[0]
                    if isinstance(target_node, ast.Name):
                        if isinstance(sub.value, ast.Call):
                            func_label = ast.unparse(sub.value.func)
                            sigs.append(f"  {target_node.id} = {func_label}(...)")
                            field_count += 1
                        elif isinstance(sub.value, ast.Constant):
                            sigs.append(
                                f"  {target_node.id} = {ast.unparse(sub.value)}"
                            )
                            field_count += 1
                elif isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    m_async = (
                        "async " if isinstance(sub, ast.AsyncFunctionDef) else ""
                    )
                    m_args = ast.unparse(sub.args)
                    m_ret = (
                        f" -> {ast.unparse(sub.returns)}"
                        if sub.returns is not None
                        else ""
                    )
                    sigs.append(f"  {m_async}def {sub.name}({m_args}){m_ret}")

    return sigs[:MAX_SIGNATURES_LIMIT]


def extract_nextflow_signatures(content: str) -> list[str]:
    """Extract process, workflow, and include declarations from Nextflow files."""
    sigs: list[str] = []
    for line in content.splitlines():
        line_s = line.strip()
        if line_s.startswith("process ") and "{" in line_s:
            parts = line_s.split()
            if len(parts) >= 2:
                name = parts[1].replace("{", "").strip()
                sigs.append(f"process {name}")
        elif line_s.startswith("workflow ") and "{" in line_s:
            parts = line_s.split()
            if len(parts) >= 2:
                name = parts[1].replace("{", "").strip()
                sigs.append(f"workflow {name}")
        elif line_s.startswith("include ") and "{" in line_s:
            parts = line_s.split("from")
            if len(parts) == 2:
                inc = (
                    parts[0]
                    .replace("include", "")
                    .replace("{", "")
                    .replace("}", "")
                    .strip()
                )
                from_path = parts[1].strip().strip("'\"")
                sigs.append(f"include: {inc} from '{from_path}'")
    return sigs[:MAX_SIGNATURES_LIMIT]


def extract_js_ts_signatures(content: str) -> list[str]:
    """Extract function, class, interface, and type declarations from JS/TS files."""
    sigs: list[str] = []
    for match in JS_TS_SIG_PATTERN.finditer(content):
        _, async_kw, kind, name, rest = match.groups()
        async_str = "async " if async_kw else ""
        rest_clean = rest.strip()[:SIGNATURE_REST_MAX_LEN]
        sigs.append(f"{async_str}{kind} {name}{rest_clean}")
    return sigs[:MAX_SIGNATURES_LIMIT]


def extract_go_rust_signatures(content: str) -> list[str]:
    """Extract function, struct, and impl declarations from Go/Rust files."""
    sigs: list[str] = []
    for match in GO_RUST_SIG_PATTERN.finditer(content):
        pub, kind, name, rest = match.groups()
        pub_str = "pub " if pub else ""
        sigs.append(
            f"{pub_str}{kind} {name}{rest.strip()[:SIGNATURE_REST_MAX_LEN]}"
        )
    return sigs[:MAX_SIGNATURES_LIMIT]


def extract_config_signatures(path: Path, content: str) -> list[str]:
    """Extract top-level keys or section headers from YAML, JSON, and TOML files."""
    ext = path.suffix.lower()
    if ext in (".yaml", ".yml"):
        keys: list[str] = []
        for line in content.splitlines():
            if (
                line
                and not line.startswith(" ")
                and not line.startswith("#")
                and ":" in line
            ):
                key_name = line.split(":")[0].strip()
                if key_name:
                    keys.append(key_name)
        if keys:
            return [f"keys: [{', '.join(keys[:MAX_CONFIG_KEYS_LIMIT])}]"]
    elif ext == ".json":
        try:
            parsed: dict[str, str | int | float | bool | None] | list[str] = (
                json.loads(content)
            )
        except json.JSONDecodeError as exc:
            raise ConfigurationError(
                f"Invalid JSON in '{path}': {exc.msg} at line {exc.lineno}"
            ) from exc
        if isinstance(parsed, dict):
            str_keys = [
                str(k) for k in list(parsed.keys())[:MAX_CONFIG_KEYS_LIMIT]
            ]
            return [f"keys: [{', '.join(str_keys)}]"]
    elif ext == ".toml":
        sections = [
            ln.strip()
            for ln in content.splitlines()
            if ln.strip().startswith("[") and ln.strip().endswith("]")
        ]
        if sections:
            return sections[:MAX_CONFIG_KEYS_LIMIT]
    return []


def extract_markdown_signatures(content: str) -> list[str]:
    """Extract heading lines from Markdown/reStructuredText files."""
    headers: list[str] = []
    for line in content.splitlines():
        if line.startswith("#"):
            headers.append(line.strip())
    return headers[:MAX_MARKDOWN_HEADERS_LIMIT]


def extract_bash_signatures(content: str) -> list[str]:
    """Extract shell function definitions from Bash/Zsh scripts."""
    sigs: list[str] = []
    for match in BASH_SIG_PATTERN.finditer(content):
        sigs.append(f"function {match.group(2)}()")
    return sigs[:MAX_BASH_SIGNATURES_LIMIT]


def extract_file_signatures(path: Path) -> list[str]:
    """Dispatch signature extraction based on file extension or shebang."""
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise ConfigurationError(
            f"Failed to read file '{path}' for signature extraction: {exc}"
        ) from exc

    ext = path.suffix.lower()
    lines = content.splitlines()
    first_line = lines[0].strip() if lines else ""

    if ext in (".py", ".pyi") or (
        first_line.startswith("#!") and "python" in first_line
    ):
        return extract_python_signatures(content)
    if ext in (".sh", ".bash", ".zsh") or (
        first_line.startswith("#!")
        and any(sh in first_line for sh in ("bash", "sh", "zsh"))
    ):
        return extract_bash_signatures(content)
    if ext == ".nf":
        return extract_nextflow_signatures(content)
    if ext in (".js", ".jsx", ".ts", ".tsx") or (
        first_line.startswith("#!") and "node" in first_line
    ):
        return extract_js_ts_signatures(content)
    if ext in (".go", ".rs"):
        return extract_go_rust_signatures(content)
    if ext in (".yaml", ".yml", ".json", ".toml"):
        return extract_config_signatures(path, content)
    if ext in (".md", ".rst"):
        return extract_markdown_signatures(content)

    return []
