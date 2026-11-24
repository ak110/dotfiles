"""Claude Code PreToolUseフック: dotfiles個人環境専用チェック集。

汎用的なチェック（mojibake検出・PowerShell LF-only検出など）は`agent-toolkit`
プラグインが担当する。本スクリプトはdotfiles個人環境を前提とする汎用性の
低いチェックをまとめる。

統合しているチェック:

1. PowerShellスクリプトの必須ディレクティブ欠落警告（warn、Writeのみ）
2. agent-toolkit配布物へのdotfiles固有名混入検出（warn）

本フックが扱う検査は、いずれも書き込んだファイルの再編集で結果を復元できるため遮断を用いない。
判定基準は`agent-basics:writing-standards`の`references/claude-hooks-block-warn.md`「遮断と警告の選択」が定める。
各チェックの詳細仕様は対応する実装関数のdocstringを参照する。
検査対象は「新規に書き込まれる側」（`content`/`new_string`）のみとする。
本フックはPreToolUse登録matcherが`Write|Edit|MultiEdit`のみのため、`Bash`ツール呼び出し時は起動しない。
予期せぬ例外の処理は共通エントリポイント（`pytools/claude_hook/__init__.py`）が担う。
エージェント宛てのメッセージは日本語の事実と命令形の次の操作で記述する。

LLM宛て出力は`agent_toolkit._hooks.notice`の整形関数経由で整形する。
プレフィックス／サフィックス規約と出力先フィールド（`reason`・`additionalContext`）の詳細は
`_message_format`モジュールのdocstringを参照する。
agent-toolkitはpytoolsの依存パッケージとして通常のimportで解決する。
"""

import json
import pathlib
import re
import tomllib

from agent_toolkit._hooks.notice import (
    formatter as _notice_formatter,
)
from agent_toolkit._hooks.tool_input import new_content_fields

from pytools._internal import common

_HOOK_ID = "dotfiles/claude_hook_pretooluse"


_llm_notice = _notice_formatter(_HOOK_ID)


def main(payload_text: str) -> int:
    try:
        payload = json.loads(payload_text)
    except json.JSONDecodeError, ValueError:
        # 想定外入力ではフックを無効化する（実処理の破損を避ける安全側の判定）。
        return 0

    tool_name = payload.get("tool_name", "")
    tool_input = payload.get("tool_input") or {}
    if not isinstance(tool_input, dict):
        return 0

    fields = new_content_fields(tool_name, tool_input)
    if fields is None:
        return 0

    file_path_raw = tool_input.get("file_path")
    file_path = file_path_raw if isinstance(file_path_raw, str) else ""

    # --- warn 系 check ---
    # 本フックが扱う検査は、いずれも書き込んだファイルの再編集で結果を復元できる。
    # `agent-basics:writing-standards`の`references/claude-hooks-block-warn.md`
    # 「遮断と警告の選択」が復元できる結果へ警告を求めるため、遮断を用いない。
    # 本文と解消手段（warn通知の`fix`）の組を集める。
    warnings: list[tuple[str, str]] = []
    ps1_directives_warning = _check_ps1_directives(tool_name, fields, file_path)
    if ps1_directives_warning is not None:
        warnings.append((ps1_directives_warning, _PS1_DIRECTIVES_FIX))
    dotfiles_detected, dotfiles_warn = _check_dotfiles_specific_names(tool_name, fields, file_path)
    if dotfiles_detected is not None:
        warnings.append((dotfiles_detected, _DOTFILES_SPECIFIC_NAMES_FIX))
    if dotfiles_warn is not None:
        warnings.append((dotfiles_warn, _POSSIBLY_DOTFILES_NAMES_FIX))
    if warnings:
        # 組み込みの ask ルール（`.claude/` 配下の確認ダイアログ等）は本フックの allow では
        # 上書きできない。確認ダイアログの抑制が必要な経路は PermissionRequest フック
        # （`agent-toolkit/agent_toolkit/_hooks/permissionrequest.py`）で別途処理する。
        print(
            json.dumps(
                {
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "permissionDecision": "allow",
                        "additionalContext": _llm_notice(
                            " | ".join(body for body, _ in warnings),
                            tag="warn",
                            fix=" | ".join(fix for _, fix in warnings),
                            removable_cause=True,
                        ),
                    }
                },
                ensure_ascii=False,
            )
        )

    return 0


# --- PowerShell 必須ディレクティブ check (warn) ---

# 冒頭付近に必須のディレクティブ。両方が揃わなければ警告する。
# 行頭厳格マッチ（インデント不可）にすることで「コメント内に文字列が含まれるだけ」や
# 「関数/条件ブロック内に書かれている（＝スクリプト全体には適用されない）」ケースを検出する。
_PS1_REQUIRED_DIRECTIVES: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(r"^Set-StrictMode\s+-Version\s+Latest\b", re.MULTILINE),
        "Set-StrictMode -Version Latest",
    ),
    (
        re.compile(r"^\$ErrorActionPreference\s*=\s*'Stop'", re.MULTILINE),
        "$ErrorActionPreference = 'Stop'",
    ),
)

# 検査する先頭行数（コメントブロックを許容するため広めに取る）。
_PS1_DIRECTIVES_HEAD_LINES = 50

_PS1_DIRECTIVES_FIX = (
    "Windows PowerShell 5.1対応のため、`Set-StrictMode -Version Latest`と"
    f"`$ErrorActionPreference = 'Stop'`を先頭{_PS1_DIRECTIVES_HEAD_LINES}行以内の行頭へ加え、再度書き込め。"
)


def _is_ps1(file_path: str) -> bool:
    lowered = file_path.lower()
    return lowered.endswith(".ps1") or lowered.endswith(".ps1.tmpl")


def _check_ps1_directives(tool_name: str, fields: list[tuple[str, str]], file_path: str) -> str | None:
    """PowerShell スクリプトの冒頭ディレクティブ欠落を検出したら警告本文を返す。

    Edit / MultiEdit の `new_string` はファイル先頭を含まないことが多いため Write のみを対象とする。
    LF/CRLF 改行のチェックは `agent-toolkit` プラグイン側で実施しているため重複させない。
    """
    if tool_name != "Write" or not _is_ps1(file_path):
        return None
    for field, value in fields:
        # BOM（U+FEFF）は chezmoi テンプレートで使われることがあるため除去してから判定する。
        normalized = value.lstrip("﻿")
        head = "\n".join(normalized.splitlines()[:_PS1_DIRECTIVES_HEAD_LINES])
        missing = [label for pattern, label in _PS1_REQUIRED_DIRECTIVES if pattern.search(head) is None]
        if missing:
            return f"{tool_name}.{field}のPowerShell必須ディレクティブが欠けている: {', '.join(missing)}。対象: {file_path}"
    return None


_PERSONAL_PROJECTS_PRIVATE: frozenset[str] = frozenset({"glatasks", "gv", "lc", "smpr"})
_PERSONAL_PROJECTS_OSS: frozenset[str] = frozenset({"pyfltr", "pytilpack"})
# 配布物文面で参照する外部 CLI 名の許容リスト。
# 配布物側が `command -v` 等で存在検査を行い、CLI 不在時に安全にフォールバックする
# 分岐構造を取る場合に限り登録する。固有名と OSS 名のいずれからも除外され、
# 配布物文面 (`agent-toolkit/` 配下) への記述が許可される。
# 追加時は本ファイルのテスト群 (`_PERSONAL_PROJECTS_PRIVATE` との非衝突など) を確認する。
_EXTERNAL_CLI_ALLOWED: frozenset[str] = frozenset({"atk"})

_DOTFILES_SPECIFIC_NAMES_FIX = "検出した固有名を一般化した表現へ置き換え、同じ編集ツールで配布物を再編集してください。"
_POSSIBLY_DOTFILES_NAMES_FIX = (
    "検出した各参照が意図した正確なOSS参照か確かめてください。該当しなければ一般化した表現へ置き換えて再編集してください。"
)


def _check_dotfiles_specific_names(
    tool_name: str, fields: list[tuple[str, str]], file_path: str
) -> tuple[str | None, str | None]:
    if not file_path:
        return None, None
    dotfiles_root = common.find_dotfiles_root()
    if dotfiles_root is None or not _is_in_agent_toolkit_distribution(file_path, dotfiles_root):
        return None, None
    specific_names, oss_names = _build_dotfiles_specific_names(dotfiles_root)
    specific_hits = _collect_word_hits(tool_name, fields, specific_names)
    oss_hits = _collect_word_hits(tool_name, fields, oss_names)
    specific_msg: str | None = None
    if specific_hits:
        specific_msg = f"agent-toolkit配布物にdotfiles固有名を検出した: {'; '.join(specific_hits)}。対象: {file_path}"
    oss_msg: str | None = None
    if oss_hits:
        oss_msg = (
            "agent-toolkit配布物にOSSとして参照される個人プロジェクト名を検出した: "
            + "; ".join(oss_hits)
            + f"。対象: {file_path}"
        )
    return specific_msg, oss_msg


def _is_in_agent_toolkit_distribution(file_path: str, dotfiles_root: pathlib.Path) -> bool:
    try:
        target = pathlib.Path(file_path).expanduser()
        if not target.is_absolute():
            return False
        target = target.resolve(strict=False)
    except ValueError, OSError:
        return False
    for base in _agent_toolkit_distribution_roots(dotfiles_root):
        try:
            target.relative_to(base.resolve(strict=False))
            return True
        except ValueError, OSError:
            continue
    return False


def _agent_toolkit_distribution_roots(dotfiles_root: pathlib.Path) -> tuple[pathlib.Path, ...]:
    return (dotfiles_root / "agent-toolkit",)


def _build_dotfiles_specific_names(dotfiles_root: pathlib.Path) -> tuple[frozenset[str], frozenset[str]]:
    specific: set[str] = set()
    specific |= _list_subdirs(dotfiles_root / ".chezmoi-source" / "dot_claude" / "skills")
    specific |= _list_subdirs(dotfiles_root / ".claude" / "skills")
    specific |= _list_pyproject_scripts(dotfiles_root / "pyproject.toml")
    specific |= _list_pytools_modules(dotfiles_root / "pytools")
    specific |= _list_internal_modules(
        dotfiles_root / "pytools" / "_internal", dotfiles_root / "agent-toolkit" / "agent_toolkit"
    )
    specific |= _list_script_names(dotfiles_root / "scripts", dotfiles_root / "libexec")
    specific |= _PERSONAL_PROJECTS_PRIVATE

    specific -= _PERSONAL_PROJECTS_OSS
    # 存在検査付きで参照することを許容する外部 CLI 名は固有名・OSS 名のいずれからも除外する。
    specific -= _EXTERNAL_CLI_ALLOWED
    return frozenset(specific), _PERSONAL_PROJECTS_OSS - _EXTERNAL_CLI_ALLOWED


def _list_subdirs(path: pathlib.Path) -> set[str]:
    if not path.is_dir():
        return set()
    return {child.name for child in path.iterdir() if child.is_dir()}


def _list_pyproject_scripts(path: pathlib.Path) -> set[str]:
    """`pyproject.toml` の `[project.scripts]` キー名を返す。読み込み失敗時は空集合。"""
    if not path.is_file():
        return set()
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except OSError, ValueError, tomllib.TOMLDecodeError:
        return set()
    project = data.get("project")
    if not isinstance(project, dict):
        return set()
    scripts = project.get("scripts")
    if not isinstance(scripts, dict):
        return set()
    return {name for name in scripts if isinstance(name, str)}


def _list_pytools_modules(path: pathlib.Path) -> set[str]:
    """`pytools/` 直下のモジュール名（拡張子除去）を返す。`__init__.py` と `_internal/` は除外。"""
    if not path.is_dir():
        return set()
    return {child.stem for child in path.glob("*.py") if child.name != "__init__.py"}


def _list_internal_modules(path: pathlib.Path, agent_toolkit_package: pathlib.Path) -> set[str]:
    if not path.is_dir():
        return set()
    names = {
        child.stem
        for child in path.glob("*.py")
        if "_" in child.stem and not child.stem.startswith("_") and not child.stem.endswith("_test")
    }
    distributed = {child.stem for child in agent_toolkit_package.rglob("*.py")} if agent_toolkit_package.is_dir() else set()
    return names - distributed


def _list_script_names(*directories: pathlib.Path) -> set[str]:
    """開発用の`scripts/`とエンドユーザー環境で起動する`libexec/`の直下のスクリプト名（拡張子除去）を返す。

    `*_test.py`は除外し、存在しないディレクトリは空集合として扱う。
    """
    names: set[str] = set()
    for path in directories:
        if not path.is_dir():
            continue
        for child in list(path.glob("*.py")) + list(path.glob("*.sh")):
            if child.suffix == ".py" and child.name.endswith("_test.py"):
                continue
            names.add(child.stem)
    return names


def _collect_word_hits(tool_name: str, fields: list[tuple[str, str]], names: frozenset[str]) -> list[str]:
    hits: list[str] = []
    seen: set[str] = set()
    for field, value in fields:
        for name in sorted(names):
            if name in seen:
                continue
            if re.search(rf"\b{re.escape(name)}\b", value):
                hits.append(f"'{name}' in {tool_name}.{field}")
                seen.add(name)
    return hits
