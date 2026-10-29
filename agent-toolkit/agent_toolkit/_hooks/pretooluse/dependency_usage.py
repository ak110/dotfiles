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

# 読込記述は言語ごとの書式だけを抽出し、別の言語の書式（Pythonの文字列に書いた`<script src>`など）は
# その言語のファイルでだけ判定する。コメントと文字列リテラルの中身は`_mask`で伏せてから正規表現を当てる。
_PYTHON_IMPORT = re.compile(
    r"^[ \t]*(?:lazy[ \t]+)?(?:from[ \t]+([\w.]+)[ \t]+import\b|import[ \t]+([\w., \t]+))", re.MULTILINE
)
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
        masked = _mask(text, line_comment="#", block_comments=(), quotes="'\"", triple=True)
        for match in _PYTHON_IMPORT.finditer(masked):
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
    if suffix == ".css":
        masked = _mask_css(text)
    elif suffix in _MARKUP_SUFFIXES:
        masked = _mask_markup(text)
    else:
        masked = _mask_script(text)
    for pattern in patterns:
        for match in pattern.finditer(masked):
            # 読込先は文字列の中身なので、伏せる前の本文の同じ位置から取り出す。
            yield text[match.start(1) : match.end(1)]


def _mask_css(text: str) -> str:
    return _mask(text, line_comment="", block_comments=(("/*", "*/"),), quotes="'\"")


def _mask_script(text: str) -> str:
    return _mask(text, line_comment="//", block_comments=(("/*", "*/"),), quotes="'\"`", script=True)


_ELEMENT = re.compile(r"<(script|style)\b[^>]*>(.*?)</\1\s*>", re.IGNORECASE | re.DOTALL)


def _mask_markup(text: str) -> str:
    """HTMLコメントを伏せ、script要素とstyle要素の中身だけをそれぞれの言語の規則で伏せる。

    要素の外の本文（`Don't`のようなアポストロフィなど）は文字列として扱わない。
    """
    masked = _mask(text, line_comment="", block_comments=(("<!--", "-->"),), quotes="")
    result = list(masked)
    for element in _ELEMENT.finditer(masked):
        body = text[element.start(2) : element.end(2)]
        inner = _mask_script(body) if element[1].lower() == "script" else _mask_css(body)
        result[element.start(2) : element.end(2)] = inner
    return "".join(result)


# この直後の`/`は除算ではなく正規表現リテラルの開始になる文字と予約語。
_REGEX_PRECEDING = frozenset("(,=:[!&|?{};+-*%<>~^")
_REGEX_KEYWORDS = frozenset({"return", "typeof", "case", "do", "else", "in", "of", "new", "delete", "void", "throw"})


def _mask(  # noqa: C901 -- 字句の種類ごとの分岐を1つの走査に保つ
    text: str,
    *,
    line_comment: str,
    block_comments: tuple[tuple[str, str], ...],
    quotes: str,
    triple: bool = False,
    script: bool = False,
) -> str:
    """コメントを空白へ、文字列リテラルの中身を`x`へ置き換えた同じ長さの本文を返す。

    改行と引用符は残すため、伏せた本文で一致した読込記述の位置を、元の本文の位置へそのまま対応付けられる。
    文字列やコメントの中に書かれた読込記述は、伏せた後の本文では読込記述の書式に一致しない。
    `script`ではJavaScriptのテンプレート文字列の`${...}`をコードとして走査し、正規表現リテラルの中身も伏せる。
    """
    result = list(text)
    length = len(text)

    def blank(start: int, end: int, fill: str) -> None:
        for position in range(start, min(end, length)):
            if result[position] != "\n":
                result[position] = fill

    def regex_allowed(index: int) -> bool:
        position = index - 1
        while position >= 0 and text[position] in " \t\r\n":
            position -= 1
        if position < 0 or text[position] in _REGEX_PRECEDING:
            return True
        word_end = position + 1
        while position >= 0 and (text[position].isalnum() or text[position] in "_$"):
            position -= 1
        return text[position + 1 : word_end] in _REGEX_KEYWORDS

    def skip_regex(index: int) -> int:
        position = index + 1
        in_class = False
        while position < length and text[position] != "\n":
            char = text[position]
            if char == "\\":
                position += 2
                continue
            if char == "[":
                in_class = True
            elif char == "]":
                in_class = False
            elif char == "/" and not in_class:
                blank(index + 1, position, "x")
                return position + 1
            position += 1
        return index + 1

    def skip_string(index: int) -> int:
        char = text[index]
        delimiter = char * 3 if triple and text.startswith(char * 3, index) else char
        position = segment = index + len(delimiter)
        while position < length and not text.startswith(delimiter, position):
            # 1文字の引用符による文字列は行をまたがない。テンプレート文字列と三重引用符はまたぐ。
            if text[position] == "\n" and len(delimiter) == 1 and char != "`":
                break
            if script and char == "`" and text.startswith("${", position):
                blank(segment, position, "x")
                position = scan(position + 2, closing="}") + 1
                segment = position
                continue
            position += 2 if text[position] == "\\" else 1
        blank(segment, position, "x")
        return position + len(delimiter) if text.startswith(delimiter, position) else position

    def scan(index: int, closing: str = "") -> int:
        depth = 0
        while index < length:
            char = text[index]
            if closing and char == closing and depth == 0:
                return index
            if closing and char == "{":
                depth += 1
            elif closing and char == "}":
                depth -= 1
            if line_comment and text.startswith(line_comment, index):
                end = text.find("\n", index)
                end = length if end < 0 else end
                blank(index, end, " ")
                index = end
                continue
            block = next(((start, stop) for start, stop in block_comments if text.startswith(start, index)), None)
            if block is not None:
                end = text.find(block[1], index + len(block[0]))
                end = length if end < 0 else end + len(block[1])
                blank(index, end, " ")
                index = end
                continue
            if script and char == "/" and regex_allowed(index):
                index = skip_regex(index)
                continue
            if quotes and char in quotes:
                index = skip_string(index)
                continue
            index += 1
        return index

    scan(0)
    return "".join(result)


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
