"""Canonical configuration constants and compiled patterns for devtool."""

import re
from typing import Final

AI_DIR_NAME: Final[str] = ".ai"
DEFAULT_DUMP_NAME: Final[str] = "dump.txt"
DEFAULT_PATCH_NAME: Final[str] = "patch.txt"
DEFAULT_DIFF_NAME: Final[str] = "diff.txt"
VALIDATION_REPORT_NAME: Final[str] = "validation_report.txt"
DUMPIGNORE_NAME: Final[str] = "dumpignore"
CONFIG_NAME: Final[str] = "config.json"

DEFAULT_TARGET_DIR: Final[str] = "."
DEFAULT_DIFF_COMMIT: Final[str] = "HEAD~1"
DEFAULT_BRANCH_NAME: Final[str] = "main"
SUBPROCESS_TIMEOUT_SEC: Final[float] = 60.0
BINARY_CHECK_CHUNK_SIZE: Final[int] = 1024
CHARS_PER_TOKEN_ESTIMATE: Final[int] = 4

MAX_SIGNATURES_LIMIT: Final[int] = 250
MAX_CLASS_FIELDS_LIMIT: Final[int] = 60
MAX_CALLS_SUMMARY_LIMIT: Final[int] = 8
MAX_CONFIG_KEYS_LIMIT: Final[int] = 20
MAX_MARKDOWN_HEADERS_LIMIT: Final[int] = 20
MAX_BASH_SIGNATURES_LIMIT: Final[int] = 30
SIGNATURE_REST_MAX_LEN: Final[int] = 40

RUFF_SELECT_RULES: Final[str] = (
    "F,E722,BLE001,S110,S112,PLC0415,FIX001,FIX002,FIX003,FIX004,"
    "PGH003,PGH004,RUF003,ANN001,ANN002,ANN003,ANN201,ANN202,ANN204,"
    "SLF001,PLW1510,B006,B007,B008,TRY201,TRY302"
)

FILE_WIDE_FATAL_RULES: Final[frozenset[str]] = frozenset(
    {
        "RUFF-SYNTAX",
        "RUFF-F821",
        "RUFF-F822",
        "RUFF-F823",
        "RUFF-F406",
        "RUFF-F701",
        "RUFF-F702",
        "RUFF-F704",
        "RUFF-F706",
    }
)

OPEN_MARK: Final[str] = "<" * 7
SEP_MARK: Final[str] = "=" * 7
CLOSE_MARK: Final[str] = ">" * 7

PATCH_PLACEHOLDER_TEXT: Final[str] = (
    "# Paste LLM response here and run: devtool apply\n"
)

DEFAULT_GITIGNORE_TEMPLATE: Final[str] = """# Dependencies & Virtual Environments
node_modules/
.venv/
venv/
env/
vendor/

# Byte-compiled / cache files
__pycache__/
*.py[cod]
*$py.class
.cache/
.pytest_cache/
.mypy_cache/
.ruff_cache/
.coverage
htmlcov/

# Build & output artifacts
dist/
build/
out/
target/
bin/
obj/
*.egg-info/

# IDEs & Editors
.idea/
.vscode/
*.swp
*.swo

# Environment & secrets
.env
.env.local
*.pem
*.key

# Logs & OS Files
*.log
.DS_Store
Thumbs.db

# AI Workdir
.ai/
"""

DEFAULT_DUMPIGNORE_TEMPLATE: Final[str] = """# ========================================================
# Rules for excluding files and directories from AI dump
# ========================================================

.git/
.ai/

node_modules/
.venv/
venv/
env/
vendor/

__pycache__/
*.py[cod]
*$py.class
.cache/
.pytest_cache/
.mypy_cache/
.coverage
htmlcov/

dist/
build/
out/
target/
obj/
*.egg-info/

package-lock.json
yarn.lock
pnpm-lock.yaml
Cargo.lock
poetry.lock

.idea/
.vscode/
*.swp
*.swo

.env*
*.pem
*.key

*.log
.DS_Store
Thumbs.db
"""

TEXT_EXTENSIONS: Final[frozenset[str]] = frozenset(
    {
        ".py",
        ".pyi",
        ".js",
        ".jsx",
        ".ts",
        ".tsx",
        ".html",
        ".htm",
        ".css",
        ".scss",
        ".sass",
        ".less",
        ".json",
        ".jsonc",
        ".yaml",
        ".yml",
        ".toml",
        ".xml",
        ".md",
        ".rst",
        ".txt",
        ".c",
        ".cpp",
        ".cc",
        ".cxx",
        ".h",
        ".hpp",
        ".hh",
        ".hxx",
        ".cs",
        ".java",
        ".kt",
        ".kts",
        ".go",
        ".rs",
        ".php",
        ".rb",
        ".sh",
        ".bash",
        ".zsh",
        ".bat",
        ".cmd",
        ".ps1",
        ".sql",
        ".dockerfile",
        ".env.example",
        ".gitignore",
        ".gitattributes",
        ".ini",
        ".cfg",
        ".conf",
        ".proto",
        ".graphql",
        ".gql",
        ".vue",
        ".svelte",
        ".lua",
        ".zig",
        ".dart",
        ".swift",
        ".scala",
        ".r",
        ".m",
        ".nf",
        ".config",
    }
)

COMMIT_PATTERN: Final[re.Pattern[str]] = re.compile(
    rf"^(?<!<){OPEN_MARK}(?!<)[ \t]*COMMIT[ \t]*\n(.*?)\n(?<!>){CLOSE_MARK}(?!>)[ \t]*COMMIT",
    re.DOTALL,
)

CREATE_PATTERN: Final[re.Pattern[str]] = re.compile(
    rf"^(?<!<){OPEN_MARK}(?!<)[ \t]*CREATE:[ \t]*([^\n\r]+?)[ \t]*\n"
    rf"(.*?)"
    rf"(?:\n|^)(?<!>){CLOSE_MARK}(?!>)[ \t]*(CREATE|REPLACE)[ \t]*",
    re.DOTALL | re.MULTILINE,
)

DELETE_PATTERN: Final[re.Pattern[str]] = re.compile(
    rf"^(?<!<){OPEN_MARK}(?!<)[ \t]*DELETE:[ \t]*([^\n\r]+?)[ \t]*\n"
    rf"(?:\n|^)(?<!>){CLOSE_MARK}(?!>)[ \t]*DELETE[ \t]*",
    re.MULTILINE,
)

REPLACE_PATTERN: Final[re.Pattern[str]] = re.compile(
    rf"^(?<!<){OPEN_MARK}(?!<)[ \t]*SEARCH:[ \t]*([^\n\r]+?)[ \t]*\n"
    rf"((?:(?!\n(?<!<){OPEN_MARK}(?!<)|\n(?<!=){SEP_MARK}(?!=)[ \t]*\n|\n(?<!>){CLOSE_MARK}(?!>)).)*?)\n"
    rf"(?<!=){SEP_MARK}(?!=)[ \t]*\n"
    rf"((?:(?!\n(?<!<){OPEN_MARK}(?!<)|\n(?<!>){CLOSE_MARK}(?!>)).)*?)"
    rf"(?:\n|^)(?<!>){CLOSE_MARK}(?!>)[ \t]*REPLACE[ \t]*",
    re.DOTALL | re.MULTILINE,
)

HEADER_SEARCH_PATTERN: Final[re.Pattern[str]] = re.compile(
    rf"^(?<!<){OPEN_MARK}(?!<)[ \t]*SEARCH:[ \t]*([^\n\r]+)",
    re.MULTILINE,
)

HEADER_CREATE_PATTERN: Final[re.Pattern[str]] = re.compile(
    rf"^(?<!<){OPEN_MARK}(?!<)[ \t]*CREATE:[ \t]*([^\n\r]+)",
    re.MULTILINE,
)

HEADER_DELETE_PATTERN: Final[re.Pattern[str]] = re.compile(
    rf"^(?<!<){OPEN_MARK}(?!<)[ \t]*DELETE:[ \t]*([^\n\r]+)",
    re.MULTILINE,
)
