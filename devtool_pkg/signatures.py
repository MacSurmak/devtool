"""Language-aware signature and semantic call-graph extractors for project skeleton views."""

import ast
import json
import re
from pathlib import Path
from typing import Final

from devtool_pkg.constants import (
    MAX_BASH_SIGNATURES_LIMIT,
    MAX_CALLS_SUMMARY_LIMIT,
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

SHORT_IDENTIFIER_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"^[A-Za-z0-9_.:/-]{1,28}$"
)

NF_SCRIPT_PATH_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"(?:\$\{projectDir\}/|\$\{baseDir\}/)?([A-Za-z0-9_./-]+\.(?:py|sh|pl|R|nf))"
)

NF_STOPWORDS: Final[frozenset[str]] = frozenset(
    {
        "if",
        "then",
        "else",
        "elif",
        "fi",
        "for",
        "in",
        "do",
        "done",
        "while",
        "case",
        "esac",
        "echo",
        "cat",
        "cp",
        "mv",
        "rm",
        "mkdir",
        "cd",
        "pwd",
        "ls",
        "true",
        "false",
        "set",
        "export",
        "local",
        "def",
        "return",
        "error",
        "tuple",
        "val",
        "path",
        "file",
        "env",
        "stdout",
        "python",
        "python3",
        "bash",
        "sh",
        "run",
    }
)

IGNORED_CALL_NAMES: Final[frozenset[str]] = frozenset(
    {
        "Path",
        "PurePath",
        "Console",
        "Table",
        "Tree",
        "Progress",
        "TextColumn",
        "BarColumn",
        "ArgumentParser",
        "Column",
        "Field",
        "relationship",
        "ForeignKey",
        "UniqueConstraint",
        " Integer",
        "String",
        "Float",
        "Boolean",
        "DateTime",
        "Text",
        "Session",
        " select",
        "case",
        "func",
        "cast",
        "dataclass",
        "field",
        "frozenset",
        "defaultdict",
        "Counter",
    }
)


def _format_class_call_assign(call_node: ast.Call) -> str:
    """Format ORM Column, relationship, Field, or generic descriptor call concisely."""
    func_label = ast.unparse(call_node.func)
    parts: list[str] = []

    for arg in call_node.args:
        arg_str = ast.unparse(arg)
        if len(arg_str) <= 42:
            parts.append(arg_str)

    for kw in call_node.keywords:
        if kw.arg is None:
            continue
        if kw.arg == "primary_key" and isinstance(kw.value, ast.Constant):
            if kw.value.value is True:
                parts.append("PK")
        elif kw.arg == "unique" and isinstance(kw.value, ast.Constant):
            if kw.value.value is True:
                parts.append("UNIQUE")
        elif kw.arg == "index" and isinstance(kw.value, ast.Constant):
            if kw.value.value is True:
                parts.append("INDEX")
        elif kw.arg == "nullable" and isinstance(kw.value, ast.Constant):
            if kw.value.value is False:
                parts.append("NOT NULL")
        elif kw.arg in ("back_populates", "default", "secondary"):
            val_str = ast.unparse(kw.value)
            if len(val_str) <= 28:
                parts.append(f"{kw.arg}={val_str}")

    if not parts:
        return f"{func_label}()"
    return f"{func_label}({', '.join(parts)})"


def _extract_subproc_cmd_token(call_node: ast.Call) -> str | None:
    """Extract target binary or script name from a subprocess.run / Popen call."""
    if not call_node.args:
        return None
    first_arg = call_node.args[0]
    if isinstance(first_arg, (ast.List, ast.Tuple)) and first_arg.elts:
        for elt in first_arg.elts[:3]:
            if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                token = elt.value.strip()
                if token and not token.startswith("-") and token not in ("python", "python3", "bash", "sh", "run"):
                    return Path(token).name
            elif isinstance(elt, ast.JoinedStr):
                unparsed = ast.unparse(elt)
                match_py = NF_SCRIPT_PATH_PATTERN.search(unparsed)
                if match_py is not None:
                    return match_py.group(1)
    return None


def _extract_function_semantics(
    func_node: ast.FunctionDef | ast.AsyncFunctionDef,
    trackable_symbols: frozenset[str],
) -> str:
    """Scan function AST for calls to project/imported symbols and subprocess executions."""
    called_items: list[str] = []
    subproc_bins: list[str] = []

    for child in ast.walk(func_node):
        if not isinstance(child, ast.Call):
            continue

        if isinstance(child.func, ast.Name):
            fn_name = child.func.id
            if fn_name in trackable_symbols and fn_name != func_node.name:
                call_repr = fn_name
                if child.args and isinstance(child.args[0], ast.Constant):
                    first_val = child.args[0].value
                    if isinstance(first_val, str) and SHORT_IDENTIFIER_PATTERN.match(
                        first_val
                    ):
                        call_repr = f"{fn_name}('{first_val}')"
                if call_repr not in called_items:
                    called_items.append(call_repr)

        elif isinstance(child.func, ast.Attribute):
            if (
                isinstance(child.func.value, ast.Name)
                and child.func.value.id == "subprocess"
                and child.func.attr in ("run", "Popen", "call", "check_call", "check_output")
            ):
                tok = _extract_subproc_cmd_token(child)
                if tok is not None and tok not in subproc_bins:
                    subproc_bins.append(tok)
            elif (
                isinstance(child.func.value, ast.Name)
                and child.func.value.id == "shutil"
                and child.func.attr == "which"
                and child.args
                and isinstance(child.args[0], ast.Constant)
                and isinstance(child.args[0].value, str)
            ):
                bin_name = child.args[0].value.strip()
                if bin_name and bin_name not in subproc_bins:
                    subproc_bins.append(bin_name)

    tags: list[str] = []
    if called_items:
        shown_calls = ", ".join(called_items[:MAX_CALLS_SUMMARY_LIMIT])
        if len(called_items) > MAX_CALLS_SUMMARY_LIMIT:
            shown_calls += f", +{len(called_items) - MAX_CALLS_SUMMARY_LIMIT}"
        tags.append(f"calls: {shown_calls}")
    if subproc_bins:
        tags.append(f"exec: {', '.join(subproc_bins[:6])}")

    if not tags:
        return ""
    return f"  [{'; '.join(tags)}]"


def _format_decorators(decorators: list[ast.expr]) -> str:
    """Format function or class decorators compactly."""
    if not decorators:
        return ""
    dec_strs: list[str] = []
    for dec in decorators:
        raw_dec = ast.unparse(dec)
        if len(raw_dec) <= 36:
            dec_strs.append(f"@{raw_dec}")
        elif isinstance(dec, ast.Call):
            dec_strs.append(f"@{ast.unparse(dec.func)}(...)")
    if not dec_strs:
        return ""
    return " ".join(dec_strs) + " "


def extract_python_signatures(content: str) -> list[str]:
    """Extract imports, type aliases, constants, ORM/dataclass fields, methods, and call graphs."""
    try:
        tree = ast.parse(content)
    except SyntaxError as exc:
        raise ConfigurationError(
            f"Failed to parse Python AST for signature extraction at line {exc.lineno}: {exc.msg}"
        ) from exc

    sigs: list[str] = []
    imported_modules: list[str] = []
    trackable_symbols: set[str] = set()

    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported_modules.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module is not None:
                imported_modules.append(node.module)
            for alias in node.names:
                sym = alias.asname if alias.asname is not None else alias.name
                if sym not in IGNORED_CALL_NAMES and not sym.isupper():
                    trackable_symbols.add(sym)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            trackable_symbols.add(node.name)

    frozen_trackable = frozenset(trackable_symbols)

    if imported_modules:
        unique_mods = list(dict.fromkeys(imported_modules))
        shown_mods = ", ".join(unique_mods)
        sigs.append(f"[imports: {shown_mods}]")

    for node in tree.body:
        if hasattr(ast, "TypeAlias") and isinstance(node, ast.TypeAlias):
            alias_name = ast.unparse(node.name)
            alias_val = ast.unparse(node.value)
            sigs.append(f"type {alias_name} = {alias_val}")

        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            var_name = node.target.id
            ann_str = ast.unparse(node.annotation)
            sigs.append(f"const {var_name}: {ann_str}")

        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id.isupper():
                    sigs.append(f"const {target.id}")

        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            dec_prefix = _format_decorators(node.decorator_list)
            async_prefix = "async " if isinstance(node, ast.AsyncFunctionDef) else ""
            args_str = ast.unparse(node.args)
            ret_str = (
                f" -> {ast.unparse(node.returns)}"
                if node.returns is not None
                else ""
            )
            sem_tags = _extract_function_semantics(node, frozen_trackable)
            sigs.append(
                f"{dec_prefix}{async_prefix}def {node.name}({args_str}){ret_str}{sem_tags}"
            )

        elif isinstance(node, ast.ClassDef):
            dec_prefix = _format_decorators(node.decorator_list)
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
                            call_summary = _format_class_call_assign(sub.value)
                            sigs.append(f"  {target_node.id} = {call_summary}")
                            field_count += 1
                        elif isinstance(sub.value, ast.Constant):
                            sigs.append(
                                f"  {target_node.id} = {ast.unparse(sub.value)}"
                            )
                            field_count += 1
                elif isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    m_dec = _format_decorators(sub.decorator_list)
                    m_async = (
                        "async " if isinstance(sub, ast.AsyncFunctionDef) else ""
                    )
                    m_args = ast.unparse(sub.args)
                    m_ret = (
                        f" -> {ast.unparse(sub.returns)}"
                        if sub.returns is not None
                        else ""
                    )
                    m_sem = _extract_function_semantics(sub, frozen_trackable)
                    sigs.append(
                        f"  {m_dec}{m_async}def {sub.name}({m_args}){m_ret}{m_sem}"
                    )

    return sigs[:MAX_SIGNATURES_LIMIT]


def _extract_nf_process_execs(block_lines: list[str]) -> list[str]:
    """Extract executed scripts and leading command binaries from a Nextflow process body."""
    execs: list[str] = []
    in_script_block = False

    for raw_ln in block_lines:
        ln = raw_ln.strip()
        if ln.startswith('"""') or ln.startswith("'''"):
            in_script_block = not in_script_block
            ln_content = ln.strip("\"'").strip()
            if not ln_content:
                continue
        else:
            ln_content = ln

        for script_match in NF_SCRIPT_PATH_PATTERN.finditer(ln_content):
            rel_script = script_match.group(1)
            if rel_script not in execs:
                execs.append(rel_script)

        if in_script_block and ln_content and not ln_content.startswith("#"):
            first_tok = ln_content.split()[0].strip("\"'\\$")
            if (
                first_tok
                and first_tok not in NF_STOPWORDS
                and SHORT_IDENTIFIER_PATTERN.match(first_tok)
                and not first_tok.startswith("-")
                and "=" not in first_tok
            ):
                if first_tok not in execs:
                    execs.append(first_tok)

    return execs[:6]


def extract_nextflow_signatures(content: str) -> list[str]:
    """Extract includes, processes with executed scripts/binaries, and workflows with call chains."""
    sigs: list[str] = []
    lines = content.splitlines()
    known_callables: list[str] = []

    for line in lines:
        line_s = line.strip()
        if line_s.startswith("include ") and "{" in line_s:
            parts = line_s.split("from")
            if len(parts) == 2:
                inc_raw = (
                    parts[0]
                    .replace("include", "")
                    .replace("{", "")
                    .replace("}", "")
                    .strip()
                )
                from_path = parts[1].strip().strip("'\"")
                sigs.append(f"include: {inc_raw} from '{from_path}'")
                for item in inc_raw.split(";"):
                    item_clean = item.strip()
                    if not item_clean:
                        continue
                    if " as " in item_clean:
                        alias_name = item_clean.split(" as ")[-1].strip()
                        if alias_name:
                            known_callables.append(alias_name)
                    else:
                        known_callables.append(item_clean)
        elif line_s.startswith("process ") and "{" in line_s:
            p_parts = line_s.split()
            if len(p_parts) >= 2:
                p_name = p_parts[1].replace("{", "").strip()
                if p_name:
                    known_callables.append(p_name)

    idx = 0
    total_lines = len(lines)
    while idx < total_lines:
        line_s = lines[idx].strip()
        if (line_s.startswith("process ") or line_s.startswith("workflow")) and "{" in line_s:
            is_proc = line_s.startswith("process ")
            tokens = line_s.split()
            name = (
                tokens[1].replace("{", "").strip()
                if len(tokens) >= 2 and tokens[1] != "{"
                else "<entry>"
            )

            brace_depth = line_s.count("{") - line_s.count("}")
            body_lines: list[str] = []
            idx += 1
            while idx < total_lines and brace_depth > 0:
                cur_line = lines[idx]
                brace_depth += cur_line.count("{") - cur_line.count("}")
                body_lines.append(cur_line)
                idx += 1

            if is_proc:
                exec_list = _extract_nf_process_execs(body_lines)
                exec_suffix = (
                    f"  [exec: {', '.join(exec_list)}]" if exec_list else ""
                )
                sigs.append(f"process {name}{exec_suffix}")
            else:
                body_text = "\n".join(body_lines)
                called_procs: list[str] = []
                for cand in known_callables:
                    if re.search(rf"\b{re.escape(cand)}\s*\(", body_text):
                        if cand not in called_procs:
                            called_procs.append(cand)
                calls_suffix = (
                    f"  [calls: {' -> '.join(called_procs)}]"
                    if called_procs
                    else ""
                )
                sigs.append(f"workflow {name}{calls_suffix}")
            continue
        idx += 1

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
