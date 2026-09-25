"""Claude Code PreToolUseフック: dotfiles個人環境専用チェック集。

汎用的なチェック（mojibake検出・PowerShell LF-only検出など）は`agent-toolkit`
プラグインが担当する。本スクリプトはdotfiles個人環境前提に依存する汎用性の
低いチェックをまとめる。

統合しているチェック:

1. PowerShellスクリプトの必須ディレクティブ欠落警告（warn、Writeのみ）
2. agent-toolkit配布物へのdotfiles固有名混入検出（warn）

本フックが扱う検査は、いずれも書き込んだファイルの再編集で結果を復元できるため遮断を用いない。
判定基準は`agent-toolkit:writing-standards`の`references/claude-hooks.md`「遮断・警告フックの成立条件」が定める。
各チェックの詳細仕様は対応する実装関数のdocstringを参照する。
検査対象は「新規に書き込まれる側」（`content`/`new_string`）のみとする。
本フックはPreToolUse登録matcherが`Write|Edit|MultiEdit`のみのため、`Bash`ツール呼び出し時は起動しない。
予期せぬ例外の処理は共通エントリポイント（`pytools/claude_hook/__init__.py`）が担う。
メッセージは英語で記述する（ユーザーの日本語思考コンテキストへのノイズ混入を避けるため）。

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

# このスクリプトのhook識別子。`agent-toolkit-auto-inserted`要素の`source`属性に展開される。
_HOOK_ID = "dotfiles/claude_hook_pretooluse"


_llm_notice = _notice_formatter(_HOOK_ID)


def main(payload_text: str) -> int:
    """エントリポイント。exit code を返す（常に0）。"""
    try:
        payload = json.loads(payload_text)
    except (json.JSONDecodeError, ValueError):
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
    # `agent-toolkit:writing-standards`の`references/claude-hooks.md`
    # 「遮断・警告フックの成立条件」の第1段が復元できる結果へ警告を求めるため、遮断を用いない。
    warnings: list[str] = []
    ps1_directives_warning = _check_ps1_directives(tool_name, fields, file_path)
    if ps1_directives_warning is not None:
        warnings.append(ps1_directives_warning)
    dotfiles_detected, dotfiles_warn = _check_dotfiles_specific_names(tool_name, fields, file_path)
    if dotfiles_detected is not None:
        warnings.append(
            f"{dotfiles_detected} Replace the identifiers with generalized wording before editing the distribution file again."
        )
    if dotfiles_warn is not None:
        warnings.append(dotfiles_warn)
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
                            " | ".join(warnings),
                            tag="warn",
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


def _is_ps1(file_path: str) -> bool:
    """対象拡張子か判定する（`.ps1` / `.ps1.tmpl`）。"""
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
            return (
                f"{tool_name}.{field}: missing required PowerShell directives: {', '.join(missing)}. Target: {file_path}"
                " For Windows PowerShell 5.1 compatibility, add `Set-StrictMode -Version Latest`"
                " and `$ErrorActionPreference = 'Stop'` near the top"
                f" (within first {_PS1_DIRECTIVES_HEAD_LINES} lines, at line start)."
            )
    return None


# --- agent-toolkit 配布物への dotfiles 固有名混入 check (block + warn) ---

# 個人プロジェクト名の固定リスト。
# ファイルシステムから機械的に取得できないため明示的に持つ。
# OSS として紹介する想定がある pyfltr / pytilpack は warn にとどめ、
# それ以外（個人非公開・特定プラットフォーム専用）は block する。
_PERSONAL_PROJECTS_BLOCK: frozenset[str] = frozenset({"glatasks", "gv", "lc", "smpr"})
_PERSONAL_PROJECTS_WARN: frozenset[str] = frozenset({"pyfltr", "pytilpack"})
# 配布物文面で参照する外部 CLI 名の許容リスト。
# 配布物側が `command -v` 等で存在検査を行い、CLI 不在時に安全にフォールバックする
# 分岐構造を取る場合に限り登録する。block / warn のいずれからも除外され、
# 配布物文面 (`agent-toolkit/` 配下) への記述が許可される。
# 追加時は本ファイルのテスト群 (`_PERSONAL_PROJECTS_BLOCK` との非衝突など) を確認する。
_EXTERNAL_CLI_ALLOWED: frozenset[str] = frozenset({"atk"})


def _check_dotfiles_specific_names(
    tool_name: str, fields: list[tuple[str, str]], file_path: str
) -> tuple[str | None, str | None]:
    """agent-toolkit 配布物への dotfiles 固有名混入を検出する。

    対象範囲は `agent-toolkit/` 配下。
    block 対象は配布先のエンドユーザーにとって意味不明な参照となるため exit 2 で停止する。
    warn 対象 (`pyfltr` / `pytilpack`) は OSS として正規参照される場合があるため通知のみ。

    `(block_message, warn_message)` を返す。該当なしの側は None。
    """
    if not file_path:
        return None, None
    dotfiles_root = pathlib.Path(__file__).resolve().parents[2]
    if not _is_in_agent_toolkit_distribution(file_path, dotfiles_root):
        return None, None
    block_names, warn_names = _build_dotfiles_specific_names(dotfiles_root)
    block_hits = _collect_word_hits(tool_name, fields, block_names)
    warn_hits = _collect_word_hits(tool_name, fields, warn_names)
    block_msg: str | None = None
    if block_hits:
        block_msg = (
            "agent-toolkit distribution must not contain dotfiles-specific identifiers."
            f" Hits: {'; '.join(block_hits)}."
            " Personal skill names, pytools commands, scripts, and personal project names"
            " like glatasks/gv/lc/smpr leak repository internals."
            f" Target: {file_path}"
        )
    warn_msg: str | None = None
    if warn_hits:
        warn_msg = (
            "agent-toolkit distribution references possibly dotfiles-related projects: "
            + "; ".join(warn_hits)
            + ". These names are personal projects but commonly referenced as OSS."
            " Verify the reference is intentional and accurate."
            f" Target: {file_path}"
        )
    return block_msg, warn_msg


def _is_in_agent_toolkit_distribution(file_path: str, dotfiles_root: pathlib.Path) -> bool:
    """対象ファイルが agent-toolkit 配布範囲のいずれかに含まれるかを判定する。

    相対パスでは判定不能なためスキップする（resolve は CWD 基準で解決され誤検出になり得る）。
    """
    try:
        target = pathlib.Path(file_path).expanduser()
        if not target.is_absolute():
            return False
        target = target.resolve(strict=False)
    except (ValueError, OSError):
        return False
    for base in _agent_toolkit_distribution_roots(dotfiles_root):
        try:
            target.relative_to(base.resolve(strict=False))
            return True
        except (ValueError, OSError):
            continue
    return False


def _agent_toolkit_distribution_roots(dotfiles_root: pathlib.Path) -> tuple[pathlib.Path, ...]:
    """マーケットプレイス経由で配布されるディレクトリの一覧を返す。"""
    return (dotfiles_root / "agent-toolkit",)


def _build_dotfiles_specific_names(dotfiles_root: pathlib.Path) -> tuple[frozenset[str], frozenset[str]]:
    """Dotfiles 固有名 (block 対象 / warn 対象) を返す。

    block 対象は次の 5 カテゴリの動的取得結果と固定の個人プロジェクト名の和集合。
    各カテゴリは対象ディレクトリ未存在時に空集合を返す。
    """
    block: set[str] = set()
    block |= _list_subdirs(dotfiles_root / ".chezmoi-source" / "dot_claude" / "skills")
    block |= _list_subdirs(dotfiles_root / ".claude" / "skills")
    block |= _list_pyproject_scripts(dotfiles_root / "pyproject.toml")
    block |= _list_pytools_modules(dotfiles_root / "pytools")
    block |= _list_scripts_modules(dotfiles_root / "scripts")
    block |= _PERSONAL_PROJECTS_BLOCK
    # warn 対象が誤って block に混入した場合は warn を優先する（保守的措置）。
    block -= _PERSONAL_PROJECTS_WARN
    # 存在検査付きで参照することを許容する外部 CLI 名は block・warn のいずれからも除外する。
    block -= _EXTERNAL_CLI_ALLOWED
    return frozenset(block), _PERSONAL_PROJECTS_WARN - _EXTERNAL_CLI_ALLOWED


def _list_subdirs(path: pathlib.Path) -> set[str]:
    """ディレクトリ直下のサブディレクトリ名を返す。未存在なら空集合。"""
    if not path.is_dir():
        return set()
    return {child.name for child in path.iterdir() if child.is_dir()}


def _list_pyproject_scripts(path: pathlib.Path) -> set[str]:
    """`pyproject.toml` の `[project.scripts]` キー名を返す。読み込み失敗時は空集合。"""
    if not path.is_file():
        return set()
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, tomllib.TOMLDecodeError):
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


def _list_scripts_modules(path: pathlib.Path) -> set[str]:
    """`scripts/` 直下のスクリプト名（拡張子除去）を返す。`*_test.py` は除外。"""
    if not path.is_dir():
        return set()
    names: set[str] = set()
    for child in list(path.glob("*.py")) + list(path.glob("*.sh")):
        if child.suffix == ".py" and child.name.endswith("_test.py"):
            continue
        names.add(child.stem)
    return names


def _collect_word_hits(tool_name: str, fields: list[tuple[str, str]], names: frozenset[str]) -> list[str]:
    """単語境界マッチで各フィールドから検出された名前を `'name' in field` 形式で列挙する。

    同名が複数フィールドで出ても 1 件だけ記録する。
    """
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
