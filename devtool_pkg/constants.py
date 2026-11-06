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

MAX_SIGNATURES_LIMIT: Final[int] = 60
MAX_CLASS_FIELDS_LIMIT: Final[int] = 20
MAX_IMPORTS_SUMMARY_LIMIT: Final[int] = 12
MAX_CONFIG_KEYS_LIMIT: Final[int] = 15
MAX_MARKDOWN_HEADERS_LIMIT: Final[int] = 15
MAX_BASH_SIGNATURES_LIMIT: Final[int] = 20
SIGNATURE_REST_MAX_LEN: Final[int] = 40

RUFF_SELECT_RULES: Final[str] = (
    "F,E722,BLE001,S110,S112,PLC0415,FIX001,FIX002,FIX003,FIX004,"
    "PGH003,PGH004,RUF003,ANN001,ANN002,ANN003,ANN201,ANN202,ANN204,ANN401,"
    "SLF001,PLW1510,B006,B007,B008,TRY201,TRY302"
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
    rf"^{OPEN_MARK}\s*COMMIT\s*\n(.*?)\n{CLOSE_MARK}\s*COMMIT",
    re.DOTALL,
)

CREATE_PATTERN: Final[re.Pattern[str]] = re.compile(
    rf"^[ \t]*{OPEN_MARK}\s*CREATE:\s*([^\n\r]+?)[ \t]*\n"
    rf"(.*?)"
    rf"(?:\n|^)[ \t]*{CLOSE_MARK}+[ \t]*(CREATE|REPLACE)[ \t]*",
    re.DOTALL | re.MULTILINE,
)

DELETE_PATTERN: Final[re.Pattern[str]] = re.compile(
    rf"^[ \t]*{OPEN_MARK}\s*DELETE:\s*([^\n\r]+?)[ \t]*\n"
    rf"(?:\n|^)[ \t]*{CLOSE_MARK}+[ \t]*DELETE[ \t]*",
    re.MULTILINE,
)

REPLACE_PATTERN: Final[re.Pattern[str]] = re.compile(
    rf"^[ \t]*{OPEN_MARK}\s*SEARCH:\s*([^\n\r]+?)[ \t]*\n"
    rf"((?:(?!\n[ \t]*{OPEN_MARK}|\n[ \t]*{SEP_MARK}+[ \t]*\n|\n[ \t]*{CLOSE_MARK}+).)*?)\n"
    rf"[ \t]*{SEP_MARK}+[ \t]*\n"
    rf"((?:(?!\n[ \t]*{OPEN_MARK}|\n[ \t]*{CLOSE_MARK}+).)*?)"
    rf"(?:\n|^)[ \t]*{CLOSE_MARK}+[ \t]*REPLACE[ \t]*",
    re.DOTALL | re.MULTILINE,
)

HEADER_SEARCH_PATTERN: Final[re.Pattern[str]] = re.compile(
    rf"^[ \t]*{OPEN_MARK}\s*SEARCH:\s*([^\n\r]+)",
    re.MULTILINE,
)

HEADER_CREATE_PATTERN: Final[re.Pattern[str]] = re.compile(
    rf"^[ \t]*{OPEN_MARK}\s*CREATE:\s*([^\n\r]+)",
    re.MULTILINE,
)

HEADER_DELETE_PATTERN: Final[re.Pattern[str]] = re.compile(
    rf"^[ \t]*{OPEN_MARK}\s*DELETE:\s*([^\n\r]+)",
    re.MULTILINE,
)
