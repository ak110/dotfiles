"""コードと依存定義が、読込表の`dependency:<名前>`の依存を実際に使うかを判定する。

読込表は技術資料を「そのライブラリを使う実コードまたは依存定義の操作」で案内する。
名前が文字列リテラル、コメント、テストデータに現れるだけのファイルは使用に数えず、
読込記述（import、require、CSSの`@import`、HTMLの`src`など）と依存定義の項目だけを判定材料にする。
"""

from __future__ import annotations

import json
import pathlib
import re
import tomllib

DEPENDENCY_FILES = frozenset(
    {
        "pyproject.toml",
        "package.json",
        "package-lock.json",
        "pnpm-lock.yaml",
        "uv.lock",
        "Cargo.toml",
        "Cargo.lock",
    }
)

CODE_SUFFIXES = frozenset(
    {
        ".py",
        ".js",
        ".jsx",
        ".mjs",
        ".cjs",
        ".ts",
        ".tsx",
        ".mts",
        ".cts",
        ".html",
        ".htm",
        ".css",
        ".svelte",
        ".rs",
        ".cs",
        ".sh",
        ".bash",
        ".ps1",
        ".psm1",
        ".psd1",
        ".cmd",
        ".bat",
    }
)

# 読込記述は言語ごとの書式だけを抽出する。行頭から始まる記述に限ることで、文字列やコメントの中の名前を使用に数えない。
# 別の言語の書式（Pythonの文字列に書いた`<script src>`など）は、その言語のファイルでだけ判定する。
_PYTHON_IMPORT = re.compile(r"^[ \t]*(?:from[ \t]+([\w.]+)[ \t]+import\b|import[ \t]+([\w., \t]+))", re.MULTILINE)
_SCRIPT_IMPORTS = (
    re.compile(r"^[ \t]*(?:import|export)\b[^'\"`;]*?['\"]([^'\"\n]+)['\"]", re.MULTILINE),
    re.compile(r"\brequire\(\s*['\"]([^'\"\n]+)['\"]\s*\)"),
    re.compile(r"\bimport\(\s*['\"]([^'\"\n]+)['\"]\s*\)"),
)
_CSS_IMPORTS = (re.compile(r"^[ \t]*@(?:import|plugin|reference)[ \t]+(?:url\()?['\"]?([^'\"\s);]+)", re.MULTILINE),)
_MARKUP_SOURCES = (re.compile(r"<(?:script|link)\b[^>]*?\b(?:src|href)\s*=\s*['\"]([^'\"]+)['\"]", re.IGNORECASE),)
_SCRIPT_SUFFIXES = frozenset({".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".mts", ".cts"})
_MARKUP_SUFFIXES = frozenset({".html", ".htm", ".svelte"})
_REQUIREMENT_NAME = re.compile(r"^\s*([A-Za-z0-9@][A-Za-z0-9._/@-]*)")
_PNPM_KEY = re.compile(r"^[ \t]+['\"]?/?(@?[A-Za-z0-9][\w.-]*(?:/[\w.-]+)?)(?:@[^'\":\s]*)?['\"]?:", re.MULTILINE)


def is_target(path: pathlib.Path) -> bool:
    """判定対象のコードまたは依存定義のファイルかを返す。chezmoiの`.tmpl`は外した名前で判定する。"""
    name = path.name.removesuffix(".tmpl")
    return pathlib.Path(name).suffix in CODE_SUFFIXES or name in DEPENDENCY_FILES


def uses(path: pathlib.Path, text: str, dependency: str) -> bool:
    """`path`の本文`text`が`dependency`を読み込むか、依存として宣言しているかを返す。

    パッケージ名は宣言値そのものと、宣言値を接頭辞とする派生名（`drizzle-orm`、`@playwright/test`、
    `@sveltejs/kit`など）を対象にする。`.svelte`ファイルは拡張子そのものをsvelteの使用とする。
    """
    name = path.name.removesuffix(".tmpl")
    if name in DEPENDENCY_FILES:
        return any(_names(package, dependency) for package in _declared_packages(name, text))
    suffix = pathlib.Path(name).suffix
    if suffix == ".svelte" and dependency == "svelte":
        return True
    return any(_names(specifier, dependency) for specifier in _loaded_specifiers(suffix, text))


def _loaded_specifiers(suffix: str, text: str):
    if suffix == ".py":
        for match in _PYTHON_IMPORT.finditer(text):
            if match[1]:
                yield match[1]
            else:
                yield from (part.split()[0] for part in match[2].split(",") if part.strip())
        return
    patterns: tuple[re.Pattern[str], ...] = ()
    if suffix in _SCRIPT_SUFFIXES or suffix in _MARKUP_SUFFIXES:
        patterns += _SCRIPT_IMPORTS
    if suffix == ".css" or suffix in _MARKUP_SUFFIXES:
        patterns += _CSS_IMPORTS
    if suffix in _MARKUP_SUFFIXES:
        patterns += _MARKUP_SOURCES
    for pattern in patterns:
        for match in pattern.finditer(text):
            yield match[1]


def _names(specifier: str, dependency: str) -> bool:
    """読込記述の対象または依存の項目名が、宣言値の名前か派生名かを返す。"""
    value = specifier.strip().lower()
    if value.startswith((".", "/")) and "://" not in value:
        return False
    if "://" in value:
        # CDNのURLは、パス中のパッケージ名の区切り（`alpinejs@3`など）で判定する。
        return any(segment.startswith(dependency) for segment in value.split("://", 1)[1].split("/")[1:])
    head = value.removeprefix("@").replace(".", "/").split("/", 1)[0]
    return head.startswith(dependency)


def _declared_packages(name: str, text: str) -> list[str]:
    """依存定義のファイルが依存として宣言する項目名を返す。解析できない本文は項目なしとして扱う。"""
    try:
        if name == "pyproject.toml":
            return _pyproject_packages(tomllib.loads(text))
        if name in {"uv.lock", "Cargo.lock"}:
            return [str(package.get("name", "")) for package in tomllib.loads(text).get("package", [])]
        if name == "Cargo.toml":
            return _cargo_packages(tomllib.loads(text))
        if name == "package.json":
            data = json.loads(text)
            return [
                key
                for field in ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies")
                for key in (data.get(field) or {})
            ]
        if name == "package-lock.json":
            data = json.loads(text)
            return [key.rsplit("node_modules/", 1)[-1] for key in (data.get("packages") or {}) if key] + list(
                data.get("dependencies") or {}
            )
    except tomllib.TOMLDecodeError, json.JSONDecodeError, AttributeError, TypeError:
        return []
    # pnpm-lock.yamlは依存のキーを行頭の字下げ付きキーとして持つ。YAMLの解析器へ依存しない。
    return _PNPM_KEY.findall(text)


def _pyproject_packages(data: dict) -> list[str]:
    project = data.get("project") or {}
    requirements = list(project.get("dependencies") or [])
    for group in (project.get("optional-dependencies") or {}).values():
        requirements.extend(group)
    for group in (data.get("dependency-groups") or {}).values():
        requirements.extend(item for item in group if isinstance(item, str))
    requirements.extend((data.get("tool", {}).get("uv") or {}).get("dev-dependencies") or [])
    return [match[1] for item in requirements if isinstance(item, str) and (match := _REQUIREMENT_NAME.match(item))]


def _cargo_packages(data: dict) -> list[str]:
    tables = [data, data.get("workspace") or {}, *(data.get("target") or {}).values()]
    return [
        key
        for table in tables
        for field in ("dependencies", "dev-dependencies", "build-dependencies")
        for key in (table.get(field) or {})
    ]
