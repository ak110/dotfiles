r"""PreToolUse統合フックのうち、編集内容とユーザー向け本文について警告するか判定する。"""

from __future__ import annotations

import pathlib
import re

from agent_toolkit._hooks import (
    tool_input as _hook_tool_input,
)
from agent_toolkit._hooks.notice import _WARN_TAG
from agent_toolkit._hooks.pretooluse.notices import _llm_notice
from agent_toolkit._plan.locations import is_plan_adjunct_file, is_plan_component_file

# U+FFFD（REPLACEMENT CHARACTER）: UTF-8デコード失敗時の代替文字
_REPLACEMENT_CHAR = "\ufffd"


def _is_plan_file_or_adjunct(file_path: str) -> bool:
    """計画ファイル（メイン）・計画ファイル（バグ）の場合に真を返す。"""
    return is_plan_component_file(file_path) or is_plan_adjunct_file(file_path)


def _materialize_cached(
    operation: _hook_tool_input.EditOperation,
    index: int,
    images: dict[int, _hook_tool_input.MaterializedEdit | None],
) -> _hook_tool_input.MaterializedEdit | None:
    """操作単位の変更前後像を必要になった時点で1回だけ具体化する。"""
    if index not in images:
        images[index] = _hook_tool_input.materialize(operation)
    return images[index]


_TRAILING_TOOL_BOUNDARY_RE = re.compile(r"</content>\s*</invoke>\s*\Z")


def _is_trailing_tool_boundary_target(file_path: str) -> bool:
    """Pythonまたは計画Markdownであればツール境界タグを判定する対象に含める。"""
    suffix = pathlib.PurePath(file_path).suffix.lower()
    return suffix == ".py" or (suffix == ".md" and _is_plan_file_or_adjunct(file_path))


def _collect_edit_operation_warnings(
    tool_name: str,
    operation: _hook_tool_input.EditOperation,
    index: int,
    images: dict[int, _hook_tool_input.MaterializedEdit | None],
) -> list[str]:
    """1操作分の警告本文を順に集める。

    いずれも後続の編集で復元できる結果を対象とするため、`agent-toolkit:writing-standards`の
    `references/claude-hooks-block-warn.md`「遮断と警告の選択」により警告で返す。
    """
    fields = [(fragment.label, fragment.after) for fragment in operation.fragments]
    display_path = operation.display_path
    boundary_warning = None
    if _is_trailing_tool_boundary_target(operation.path):
        image = _materialize_cached(operation, index, images)
        if image is not None and _TRAILING_TOOL_BOUNDARY_RE.search(image.after_image) is not None:
            boundary_warning = _llm_notice(
                f"warn: `{display_path}`の末尾にツール境界タグ`</content>`と`</invoke>`が混入している。",
                tag=_WARN_TAG,
                fix="末尾のツール境界タグを削除する。",
                removable_cause=True,
            )
    candidates = [
        _warn_mojibake(tool_name, fields),
        # PowerShellの改行要件はpatch断片のLF表現から判定できないため、Claudeの`Write`だけへ適用する。
        _check_ps1_eol(tool_name, fields, display_path) if tool_name == "Write" and _is_ps1(display_path) else None,
        *(_check_lockfiles(tool_name, path) for path in operation.display_paths),
        boundary_warning,
        _check_manifest(tool_name, fields, display_path),
    ]
    warnings = [warning for warning in candidates if warning is not None]
    return warnings


def _detect_mojibake(tool_name: str, fields: list[tuple[str, str]]) -> tuple[str, str] | None:
    """U+FFFD（mojibake）を検出して本文と解消手段を返す。"""
    for field, value in fields:
        position = value.find(_REPLACEMENT_CHAR)
        if position == -1:
            continue
        start = max(0, position - 10)
        end = min(len(value), position + 11)
        sample = value[start:end]
        return (
            f"`{tool_name}.{field}`にU+FFFD（文字化け）を検出した。文脈: {sample!r}",
            "U+FFFDを意図した文字へ置き換えて再実行する。",
        )
    return None


def _warn_mojibake(tool_name: str, fields: list[tuple[str, str]]) -> str | None:
    """文字化けを警告する。

    第1段の判定では、編集対象もユーザーへ提示する本文も再編集で是正できるため復元可能として扱う。
    ユーザーへ提示する本文はユーザー自身が誤りを読み取れるため、遮断せず警告で返す。
    """
    detected = _detect_mojibake(tool_name, fields)
    if detected is None:
        return None
    body, fix = detected
    return _llm_notice(body, tag=_WARN_TAG, fix=fix, removable_cause=True)


def _is_ps1(file_path: str) -> bool:
    """`.ps1` / `.ps1.tmpl`の場合に真を返す。"""
    lowered = file_path.lower()
    return lowered.endswith(".ps1") or lowered.endswith(".ps1.tmpl")


def _check_ps1_eol(tool_name: str, fields: list[tuple[str, str]], file_path: str) -> str | None:
    """BOMなしのLF-only書き込みと改行規約の不一致を検出して警告本文を返す。

    書き込んだファイルは再作成で復元できるため、警告で返す。
    """
    for field, value in fields:
        if "\n" not in value:
            continue
        if "\r\n" in value:
            continue
        return _llm_notice(
            f"`{tool_name}.{field}`に`LF`だけの内容を検出した。"
            "この書き込みでは`UTF-8`の`BOM`が失われて日本語が文字化けし、"
            f"`.gitattributes`の`*.ps1 text eol=crlf`規約とも一致しない。対象: {file_path}",
            tag=_WARN_TAG,
            fix=(
                "`agent-toolkit:writing-standards`の`references/encoding.md`「書込ツールの改行・BOM保全」に従い、"
                "既存ファイルには`Edit`ツールを使い、新規ファイルは`Bash`で`UTF-8`の`BOM`と`CRLF`改行を指定して書き込む。"
            ),
            removable_cause=True,
        )
    return None


# --- lockfile / 生成物ディレクトリcheck ---

# （label, regex, hint）のタプル。regexはfile_path全体に対するマッチ。
_LOCKFILE_RULES: tuple[tuple[str, re.Pattern[str], str], ...] = (
    ("uv.lock", re.compile(r"(^|/)uv\.lock$"), "依存の追加には`uv add`、削除には`uv remove`を使う。"),
    (
        "pnpm-lock.yaml",
        re.compile(r"(^|/)pnpm-lock\.yaml$"),
        "依存の追加には`pnpm add`、削除には`pnpm remove`を使う。",
    ),
    ("package-lock.json", re.compile(r"(^|/)package-lock\.json$"), "依存の追加には`npm install <pkg>`を使う。"),
    ("yarn.lock", re.compile(r"(^|/)yarn\.lock$"), "依存の追加には`yarn add`を使う。"),
    ("Cargo.lock", re.compile(r"(^|/)Cargo\.lock$"), "依存の追加には`cargo add`を使う。"),
    ("mise.lock", re.compile(r"(^|/)mise\.lock$"), "ツール管理には`mise use`・`mise install`を使う。"),
    (
        ".venv/",
        re.compile(r"(^|/)\.venv/"),
        "仮想環境のファイルを直接編集せず、`uv`などで再構築する。",
    ),
    (
        "node_modules/",
        re.compile(r"(^|/)node_modules/"),
        "`node_modules`は生成ディレクトリであるため、直接編集しない。",
    ),
)


def _check_lockfiles(tool_name: str, file_path: str) -> str | None:
    """lockfileや生成物ディレクトリへの直接編集を検出して警告本文を返す。

    手編集した内容はパッケージ管理ツールの再生成で復元できるため、警告で返す。
    """
    if not file_path:
        return None
    normalized = file_path.replace("\\", "/")
    for label, pattern, hint in _LOCKFILE_RULES:
        if pattern.search(normalized):
            fix = "このパスを直接編集せず、パッケージ管理ツールで再生成する。" if label in {".venv/", "node_modules/"} else hint
            return _llm_notice(
                f"{tool_name}による{label}の直接編集を検出した。対象: {file_path}",
                tag=_WARN_TAG,
                fix=fix,
                removable_cause=True,
            )
    return None


# --- manifest手編集check (warn) ---

_MANIFEST_RULES: tuple[tuple[str, re.Pattern[str], str], ...] = (
    (
        "pyproject.toml",
        re.compile(r"(^|/)pyproject\.toml$"),
        (
            "`[project.dependencies]`・`[project.optional-dependencies]`の編集は、"
            "`uv.lock`を同期させるため`uv add`・`uv remove`を使う。"
            "`[tool.*]`と版数の編集はそのまま進めてよい。"
        ),
    ),
    (
        "package.json",
        re.compile(r"(^|/)package\.json$"),
        (
            "依存の編集は、`pnpm-lock.yaml`を同期させるため`pnpm add`・`pnpm remove`を使う。"
            "`scripts`とメタデータの編集はそのまま進めてよい。"
        ),
    ),
)


_DEPENDENCY_SECTION_RE = re.compile(r"dependenc", re.IGNORECASE)
"""manifestの依存の節へ触れる編集断片を判別する語。

`pyproject.toml`の`[project.dependencies]`・`[project.optional-dependencies]`・`dependencies = [`と、
`package.json`の`dependencies`・`devDependencies`などをまとめて捉える。
`[tool.*]`と版数だけを変える編集はこの語を含まないため、警告の対象から外れる。
"""


def _check_manifest(tool_name: str, fields: list[tuple[str, str]], file_path: str) -> str | None:
    """manifestの依存の節への手編集を検出したら警告本文を返す（warnのみ、exit codeは変えない）。

    lockfileとの同期が失われるのは依存の節を変える編集に限るため、その節へ触れない編集では通知しない。
    """
    if not file_path:
        return None
    normalized = file_path.replace("\\", "/")
    for label, pattern, hint in _MANIFEST_RULES:
        if not pattern.search(normalized):
            continue
        if not any(_DEPENDENCY_SECTION_RE.search(value) for _, value in fields):
            return None
        return _llm_notice(
            f"`{tool_name}`で`{label}`の依存の節を編集しようとしている。",
            tag=_WARN_TAG,
            fix=hint,
            removable_cause=True,
        )
    return None
