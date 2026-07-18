#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Claude Code PreToolUseフック: dotfiles個人環境専用チェック集。

汎用的なチェック（mojibake検出・PowerShell LF-only検出など）は`agent-toolkit`
プラグインが担当する。本スクリプトはdotfiles個人環境前提に依存する汎用性の
低いチェックをまとめる。

統合しているチェック:

1. `~/.claude/`配下への直接編集警告（warn、非ブロック）
2. PowerShellスクリプトの必須ディレクティブ欠落ブロック（block、Writeのみ）
3. 個人用/ローカル専用ファイル言及検出（warn、非ブロック）
4. agent-toolkit配布物へのdotfiles固有名混入検出（block + warn）
5. `agent-toolkit/`配下編集時の`agent-toolkit-edit`スキル未起動警告（warn、非ブロック）
6. 計画ファイル（`~/.claude/plans/*.md`）の`agent-toolkit/`編集を伴う変更でのbump宣言欠落警告（warn、非ブロック）
7. 計画ファイルの`## 変更内容`配下の`agent-toolkit/`パスを示すH3配下diffブロック+行へのdotfiles固有名混入検出（block）
8. `Bash`経由の`atk tb add`コマンド文字列への縮退フレーズ混入検出（block）

各チェックの詳細仕様は対応する実装関数のdocstringを参照する。
検査対象はチェックごとに異なる。
`Write`/`Edit`/`MultiEdit`向けチェック（1〜7）は「新規に書き込まれる側」（`content`/`new_string`）のみを対象とする。
`Bash`向けチェック（8）は`command`文字列を対象とする。
予期せぬ例外はexit codeを0として通過させる（フックが破損して編集できなくなる事故を避けるため）。
メッセージは英語で記述する（ユーザーの日本語思考コンテキストへのノイズ混入を避けるため）。

LLM宛て出力は`agent-toolkit/scripts/_message_format.llm_notice`経由で整形する。
プレフィックス／サフィックス規約と出力先フィールド（`reason`・`additionalContext`）の詳細は
`_message_format`モジュールのdocstringを参照する。
参照経路は`Path(__file__).resolve().parent.parent / "agent-toolkit" / "scripts"`を
`sys.path`に追加して解決する。プラグイン無効化時もファイル自体は存在しimportは成立する。
"""

import json
import pathlib
import re
import sys
import tomllib
import traceback

# agent-toolkit のメッセージ整形ヘルパーを sys.path 経由で再利用する。
# plugin が無効化されていても dotfiles リポジトリ上にファイルが存在し続けるため import は成立する。
sys.path.insert(
    0,
    str(pathlib.Path(__file__).resolve().parent.parent / "agent-toolkit" / "scripts"),
)
from _message_format import llm_notice as _llm_notice_base  # noqa: E402  # pylint: disable=wrong-import-position,import-error
from _plan_file import is_plan_file  # noqa: E402  # pylint: disable=wrong-import-position,import-error
from _session_state import read_state  # noqa: E402  # pylint: disable=wrong-import-position,import-error
from pretooluse import _match_scope_escalation  # noqa: E402  # pylint: disable=wrong-import-position,import-error

# このスクリプトの hook 識別子。プレフィックス `[auto-generated: dotfiles/claude_hook_pretooluse]` に展開される。
_HOOK_ID = "dotfiles/claude_hook_pretooluse"

_CLAUDE_LOCAL_MD = "CLAUDE.local.md"


def _llm_notice(body: str, *, tag: str = "") -> str:
    """コーディングエージェント宛てメッセージを標準プレフィックス / サフィックス付きで整形する。

    `tag` に `warn` 等を渡すとプレフィックスに並置する (`[auto-generated: ...][warn]`)。
    """
    return _llm_notice_base(body, _HOOK_ID, tag=tag)


def main() -> int:
    """エントリポイント。exit code を返す（0 または 2）。"""
    try:
        payload = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, ValueError):
        # 想定外入力ではフックを無効化する（実処理の破損を避ける安全側の判定）。
        return 0

    tool_name = payload.get("tool_name", "")
    tool_input = payload.get("tool_input") or {}
    if not isinstance(tool_input, dict):
        return 0

    if tool_name == "Bash":
        command_raw = tool_input.get("command")
        command = command_raw if isinstance(command_raw, str) else ""
        block_msg = _check_bash_atk_fb_tbd_add_scope_escalation(command)
        if block_msg is not None:
            print(_llm_notice(block_msg), file=sys.stderr)
            return 2
        return 0

    fields = _collect_new_fields(tool_name, tool_input)
    if fields is None:
        return 0

    file_path_raw = tool_input.get("file_path")
    file_path = file_path_raw if isinstance(file_path_raw, str) else ""
    session_id_raw = payload.get("session_id", "")
    session_id = session_id_raw if isinstance(session_id_raw, str) else ""
    dotfiles_root = pathlib.Path(__file__).resolve().parent.parent

    # --- block 系 check（最初の違反で exit 2）---
    if _check_ps1_directives(tool_name, fields, file_path):
        return 2
    dotfiles_block, dotfiles_warn = _check_dotfiles_specific_names(tool_name, fields, file_path)
    if dotfiles_block is not None:
        print(_llm_notice(dotfiles_block), file=sys.stderr)
        return 2
    plan_diff_block = _check_plan_file_dotfiles_specific_names(tool_name, fields, file_path)
    if plan_diff_block is not None:
        print(_llm_notice(plan_diff_block), file=sys.stderr)
        return 2

    # --- warn 系 check ---
    warnings: list[str] = []
    home_claude_warning = _home_claude_edit_warning(tool_name, file_path)
    if home_claude_warning is not None:
        warnings.append(home_claude_warning)
    personal_warning = _personal_file_mentions_warning(tool_name, fields, file_path)
    if personal_warning is not None:
        warnings.append(personal_warning)
    if dotfiles_warn is not None:
        warnings.append(dotfiles_warn)
    skill_warning = _agent_toolkit_edit_skill_warning(tool_name, file_path, session_id, dotfiles_root)
    if skill_warning is not None:
        warnings.append(skill_warning)
    plan_bump_warning = _plan_file_bump_declaration_warning(tool_name, fields, file_path)
    if plan_bump_warning is not None:
        warnings.append(plan_bump_warning)

    if warnings:
        # 組み込みの ask ルール（`.claude/` 配下の確認ダイアログ等）は本フックの allow では
        # 上書きできない。確認ダイアログの抑制が必要な経路は PermissionRequest フック
        # （`agent-toolkit/scripts/permissionrequest.py`）で別途処理する。
        print(
            json.dumps(
                {
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "permissionDecision": "allow",
                        "additionalContext": _llm_notice(" | ".join(warnings), tag="warn"),
                    }
                },
                ensure_ascii=False,
            )
        )

    return 0


def _collect_new_fields(tool_name: str, tool_input: dict) -> list[tuple[str, str]] | None:
    """対象ツールの「新規書き込みフィールド」を（field 名, 値）のリストで返す。

    対象外ツールの場合は None を返す。値が文字列でないものはスキップする。
    """
    if tool_name == "Write":
        value = tool_input.get("content")
        return [("content", value)] if isinstance(value, str) else []
    if tool_name == "Edit":
        value = tool_input.get("new_string")
        return [("new_string", value)] if isinstance(value, str) else []
    if tool_name == "MultiEdit":
        edits = tool_input.get("edits") or []
        if not isinstance(edits, list):
            return []
        result: list[tuple[str, str]] = []
        for index, edit in enumerate(edits):
            if not isinstance(edit, dict):
                continue
            new_string = edit.get("new_string")
            if isinstance(new_string, str):
                result.append((f"edits[{index}].new_string", new_string))
        return result
    return None


# --- PowerShell 必須ディレクティブ check (block) ---

# 冒頭付近に必須のディレクティブ。両方が揃わなければブロックする。
# 行頭厳格マッチ（インデント不可）にすることで「コメント内に文字列が含まれるだけ」や
# 「関数/条件ブロック内に書かれている（＝スクリプト全体には適用されない）」ケースをブロックする。
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


def _check_ps1_directives(tool_name: str, fields: list[tuple[str, str]], file_path: str) -> bool:
    """PowerShell スクリプトの冒頭ディレクティブ欠落を検出したら True を返す。

    Edit / MultiEdit の `new_string` はファイル先頭を含まないことが多いため Write のみを対象とする。
    LF/CRLF 改行のチェックは `agent-toolkit` プラグイン側で実施しているため重複させない。
    """
    if tool_name != "Write" or not _is_ps1(file_path):
        return False
    for field, value in fields:
        # BOM（U+FEFF）は chezmoi テンプレートで使われることがあるため除去してから判定する。
        normalized = value.lstrip("﻿")
        head = "\n".join(normalized.splitlines()[:_PS1_DIRECTIVES_HEAD_LINES])
        missing = [label for pattern, label in _PS1_REQUIRED_DIRECTIVES if pattern.search(head) is None]
        if missing:
            print(
                _llm_notice(
                    f"{tool_name}.{field}: missing required PowerShell directives: "
                    f"{', '.join(missing)}. For Windows PowerShell 5.1 compatibility, add "
                    f"`Set-StrictMode -Version Latest` and `$ErrorActionPreference = 'Stop'` "
                    f"near the top (within first {_PS1_DIRECTIVES_HEAD_LINES} lines, at line start)."
                    f" Target: {file_path}"
                ),
                file=sys.stderr,
            )
            return True
    return False


# --- Bash: atk tb add 縮退フレーズ check (block) ---


def _check_bash_atk_fb_tbd_add_scope_escalation(command: str) -> str | None:
    """`atk tb add`実行時にコマンド文字列へ縮退フレーズが含まれる場合にブロック用メッセージを返す。

    `_match_scope_escalation`は`agent-toolkit/scripts/pretooluse.py`定義の
    `_SCOPE_ESCALATION_PHRASES`を再利用する（sys.path経由import）。
    検査対象はコマンド文字列全体とする。
    """
    if "atk" not in command:
        return None
    if "tb add" not in command:
        return None
    match_result = _match_scope_escalation(command)
    if match_result is None:
        return None
    category, _matched = match_result
    return (
        f"blocked: `atk tb add` includes a scope-escalation phrase (category: {category})."
        " See agent-toolkit/rules/01-agent.md session-split prohibition section."
    )


# --- ~/.claude/ 配下の直接編集 check (warn) ---

# 警告対象外のサブツリー（Claude Code のランタイム領域 / プラン作業領域）。
# 配布対象（rules/ や agents/）は含めない。
_HOME_CLAUDE_ALLOWED_DIRS: frozenset[str] = frozenset(
    {
        "plans",  # plan mode が書き込む計画ファイル
        "scratchpad",  # 一時作業ファイル領域 (chezmoi 管理外)
        "projects",  # Claude Code のセッション履歴
        "todos",  # TodoWrite ストレージ
        "shell-snapshots",  # シェル スナップショット
        "ide",  # IDE 連携キャッシュ
        "statsig",  # Statsig SDK のキャッシュ
    }
)

# 警告対象外のファイル名（Claude Code 自身が書き換える非 chezmoi 管理ファイル）。
_HOME_CLAUDE_ALLOWED_NAMES: frozenset[str] = frozenset(
    {
        "settings.json",  # Claude Code ランタイム設定 (autoMode 等を自身が書き換える)
    }
)

# 警告対象外のファイル名部分一致（`*.local.*` 系ローカル設定）。
_HOME_CLAUDE_ALLOWED_NAME_SUBSTRING = ".local."


def _home_claude_edit_warning(tool_name: str, file_path: str) -> str | None:
    """`~/.claude/` 配下への直接編集の警告メッセージを返す (該当しなければ None)。

    chezmoi の配布先のため、配布対象ファイルを編集すると次回 `chezmoi apply` で
    上書きされる。配布元 (`.chezmoi-source/dot_claude/`) を編集すべき。
    ただし `settings.json` など非 chezmoi 管理ファイルを機械的に判別するのは
    難しく、誤判定でブロックすると作業が止まるため、警告のみ表示し判断はコーディングエージェントに委ねる。
    """
    if not file_path:
        return None
    try:
        target = pathlib.Path(file_path).expanduser()
        # 相対パスでは ~/.claude 配下か判定できないためスキップする。
        # （resolve すると CWD 基準で解決され誤検出になり得るので resolve 前に判定する）
        if not target.is_absolute():
            return None
        # `.` / `..`・シンボリックリンクを解消して字句比較の迂回を防ぐ。
        # strict=False で存在しないパスでも例外を送出しない。
        target = target.resolve(strict=False)
        home_claude = (pathlib.Path.home() / ".claude").resolve(strict=False)
    except (ValueError, OSError):
        return None
    try:
        rel = target.relative_to(home_claude)
    except ValueError:
        return None
    parts = rel.parts
    if not parts:
        # `~/.claude` そのもの（実際にはディレクトリ）は対象外。
        return None
    if parts[0] in _HOME_CLAUDE_ALLOWED_DIRS:
        return None
    if rel.name in _HOME_CLAUDE_ALLOWED_NAMES:
        return None
    if _HOME_CLAUDE_ALLOWED_NAME_SUBSTRING in rel.name:
        return None
    return (
        f"{tool_name} targets ~/.claude/ ({file_path})."
        " If this file is distributed via chezmoi, edit `.chezmoi-source/dot_claude/` instead"
        " (direct edits will be overwritten on next `chezmoi apply`)."
        " Proceed only if the target is a non-chezmoi runtime/config file."
    )


# --- 個人用 / ローカル専用ファイル言及 check (warn) ---

# ファイル名に連続アンダースコア（3〜7 文字）を含むトークンを検出する。
# 個人用メモの慣習として使われるファイル名パターン。
# 8 文字以上の連続アンダースコアは区切り線等の装飾用途とみなし対象外とする。
# `[^\W_]`（= [a-zA-Z0-9]）でアンダースコア列の前後を非アンダースコアの word 文字に
# 限定し、`(?!_)` で列が 7 文字を超えないことを保証する。
# `\b` でword境界に固定することで、トークンの内側の部分マッチを避ける。
_TRIPLE_UNDERSCORE_PATTERN = re.compile(r"\b\w*[^\W_]_{3,7}(?!_)[^\W_]\w*\b")

# 3〜7 文字の連続アンダースコアを検出するパターン（ファイル名判定用）。
_SHORT_UNDERSCORE_RUN = re.compile(r"(?<!_)_{3,7}(?!_)")


def _is_claude_local_md(file_path: str) -> bool:
    """ファイルパス自体が CLAUDE.local.md かを判定する (言及チェック除外用)。"""
    if not file_path:
        return False
    # パス区切りを正規化してファイル名を取得
    name = file_path.replace("\\", "/").rsplit("/", 1)[-1]
    return name == _CLAUDE_LOCAL_MD


def _has_triple_underscore_filename(file_path: str) -> bool:
    """ファイル名自体に 3〜7 文字の連続アンダースコアが含まれるかを判定する (言及チェック除外用)。"""
    if not file_path:
        return False
    name = file_path.replace("\\", "/").rsplit("/", 1)[-1]
    return bool(_SHORT_UNDERSCORE_RUN.search(name))


def _is_under_claude_plans(file_path: str) -> bool:
    """書き込み先パスが ``~/.claude/plans/`` 配下かを判定する (言及チェック除外用)。

    plans/ 配下は版管理外の計画ファイル領域で、版管理経由でのファイル名漏洩リスクが
    存在しないため警告を抑止する設計とする。
    """
    if not file_path:
        return False
    try:
        resolved = pathlib.Path(file_path).expanduser().resolve(strict=False)
    except (OSError, RuntimeError):
        return False
    plans_dir = (pathlib.Path.home() / ".claude" / "plans").resolve(strict=False)
    try:
        resolved.relative_to(plans_dir)
        return True
    except ValueError:
        return False


def _personal_file_mentions_warning(tool_name: str, fields: list[tuple[str, str]], file_path: str) -> str | None:
    """個人用 / ローカル専用ファイルの言及の警告メッセージを返す (該当しなければ None)。

    対象は `CLAUDE.local.md` と、ファイル名に `___` を含むトークン。
    対象ファイル自身の編集および書き込み先が ``~/.claude/plans/`` 配下の場合は警告をスキップする。
    文脈依存の判断はコーディングエージェントに委ね、hook は緩い警告のみを表示してブロックはしない。
    """
    if _is_under_claude_plans(file_path):
        return None
    messages: list[str] = []
    if not _is_claude_local_md(file_path):
        for field, value in fields:
            if _CLAUDE_LOCAL_MD in value:
                messages.append(f"'{_CLAUDE_LOCAL_MD}' in {tool_name}.{field}")
                break
    if not _has_triple_underscore_filename(file_path):
        for field, value in fields:
            match = _TRIPLE_UNDERSCORE_PATTERN.search(value)
            if match:
                messages.append(f"'{match.group()}' (filename-like token containing '___') in {tool_name}.{field}")
                break
    if not messages:
        return None
    return (
        "detected possible mention(s) of local-only personal file(s): "
        + "; ".join(messages)
        + ". Such files are typically gitignored personal memos."
        " Referencing them from version-controlled files is often unintentional"
        " and risks leaking the filename via ignore-lists or stale references."
        " On the other hand, recommending end users to create their own local memo"
        " file (e.g., in distributed docs/skills) is legitimate."
        " Judge the context and keep the mention only if it is intentional."
    )


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
    block 対象は配布先の利用者にとって意味不明な参照となるため exit 2 で停止する。
    warn 対象 (`pyfltr` / `pytilpack`) は OSS として正規参照される場合があるため通知のみ。

    `(block_message, warn_message)` を返す。該当なしの側は None。
    """
    if not file_path:
        return None, None
    dotfiles_root = pathlib.Path(__file__).resolve().parent.parent
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
            " Replace with generalized wording (personal skill names, pytools commands, scripts,"
            " and personal project names like glatasks/gv/lc/smpr leak repository internals)."
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


# --- agent-toolkit-edit スキル未起動警告 check (warn) ---


def _agent_toolkit_edit_skill_warning(
    tool_name: str,
    file_path: str,
    session_id: str,
    dotfiles_root: pathlib.Path,
) -> str | None:
    """`agent-toolkit/` 配下編集時の `agent-toolkit-edit` スキル未起動警告を返す。

    `agent-toolkit-edit` スキルは bump 種別判定・行数規定・編集手順を提供する。
    PostToolUse (`claude_hook_posttooluse.py`) が当該スキル呼び出しを観測し
    セッション状態の `agent_toolkit_edit_skill_invoked` を真にする。
    """
    if tool_name not in {"Write", "Edit", "MultiEdit"}:
        return None
    if not session_id:
        return None
    if not _is_in_agent_toolkit_distribution(file_path, dotfiles_root):
        return None
    state = read_state(session_id)
    if state.get("agent_toolkit_edit_skill_invoked", False):
        return None
    return (
        "editing files under `agent-toolkit/` without invoking the"
        " `agent-toolkit-edit` skill first."
        " Invoke the skill to load bump policy, 200-line guideline,"
        " and editing workflow before proceeding."
    )


# H3見出し行に出現する最初のバッククォート1個のインラインコード値を抽出する。
# `.*?` で非貪欲マッチし、複数のインラインコードを含むH3でも先頭の値を採用する。
_H3_INLINE_CODE = re.compile(r"^### .*?`([^`]+)`")
# diff言語指定フェンスの開始（3個以上の連続バッククォート）。
_DIFF_FENCE_OPEN = re.compile(r"^(`{3,})diff\s*$")
# 終了フェンス候補（連続バッククォート行）。
_FENCE_CLOSE = re.compile(r"^`+\s*$")


def _extract_plan_file_diff_plus_lines(changes_section: str) -> list[str]:
    """計画ファイルの`## 変更内容`配下から`agent-toolkit/`パス指示H3配下のdiff +行を抽出する。

    - `###`で始まるH3行に出現するバッククォート1個のインラインコード値を現在の対象パスとして記憶する
    - 対象パスに`agent-toolkit/`を含む場合に限り、配下の`diff`言語指定フェンスブロック内へ入る
    - diffフェンスは3個以上の連続バッククォートで開く
    - 終了フェンスは開始フェンスと同じ長さ以上の連続バッククォートで判定する
    - 先頭1文字が`+`かつ先頭2文字が`++`でない行のみを返す
    """
    result: list[str] = []
    current_path = ""
    in_diff = False
    fence_len = 0

    for line in changes_section.splitlines():
        if in_diff:
            if _FENCE_CLOSE.match(line) and len(line.rstrip()) >= fence_len:
                in_diff = False
                fence_len = 0
            elif line.startswith("+") and not line.startswith("++"):
                result.append(line)
        else:
            m_h3 = _H3_INLINE_CODE.match(line)
            if m_h3:
                current_path = m_h3.group(1)
                continue
            if "agent-toolkit/" in current_path:
                m_fence = _DIFF_FENCE_OPEN.match(line)
                if m_fence:
                    in_diff = True
                    fence_len = len(m_fence.group(1))

    return result


def _check_plan_file_dotfiles_specific_names(tool_name: str, fields: list[tuple[str, str]], file_path: str) -> str | None:
    r"""計画ファイル`## 変更内容`配下のdiff +行へのdotfiles固有名混入を検出する。

    対象は`Write`のみ。対象パスは`is_plan_file`の判定に従う。
    `## 変更内容`配下の`agent-toolkit/`パスを示すH3見出し配下のdiffブロック+行を検査し、
    block名集合の名前が`\b<name>\b`でマッチする場合にブロックメッセージを返す。
    """
    if tool_name != "Write":
        return None
    if not is_plan_file(file_path):
        return None
    dotfiles_root = pathlib.Path(__file__).resolve().parent.parent
    block_names, _warn_names = _build_dotfiles_specific_names(dotfiles_root)
    for _field, value in fields:
        sections = _split_markdown_h2_sections(value)
        changes = sections.get("変更内容", "")
        plus_lines = _extract_plan_file_diff_plus_lines(changes)
        if not plus_lines:
            continue
        combined = "\n".join(plus_lines)
        hits: list[str] = []
        for name in sorted(block_names):
            if re.search(rf"\b{re.escape(name)}\b", combined):
                hits.append(name)
        if hits:
            return (
                "plan file diff (+lines) under `## 変更内容` references"
                " dotfiles-specific identifiers in agent-toolkit/ paths."
                f" Hits: {'; '.join(hits)}."
                " Replace with generalized wording before adding to the plan."
                " These names (personal skill names, pytools commands, scripts,"
                " personal project names) leak repository internals into the distribution."
                f" Target: {file_path}"
            )
    return None


def _plan_file_bump_declaration_warning(tool_name: str, fields: list[tuple[str, str]], file_path: str) -> str | None:
    """計画ファイル Write 時の agent-toolkit/ 編集に対する bump 宣言欠落の警告メッセージを返す。

    対象は Write のみ。Edit / MultiEdit の new_string では計画ファイル本文全域を
    取得できないため判定対象外とする。
    対象パスは `is_plan_file` の判定に従い、`.review.md` / `.codex.log` /
    サブディレクトリ配下は対象外とする。
    判定対象セクションは `## 変更内容` と `## 実行方法`。
    """
    if tool_name != "Write":
        return None
    if not is_plan_file(file_path):
        return None
    for _field, value in fields:
        sections = _split_markdown_h2_sections(value)
        changes = sections.get("変更内容", "")
        plan = sections.get("実行方法", "")
        if "agent-toolkit/" not in changes:
            continue
        if "agent_toolkit_bump.py" in plan:
            continue
        if "bump不要" in plan:
            continue
        return (
            "plan file references `agent-toolkit/` paths under `## 変更内容` but"
            " `## 実行方法` is missing both an `agent_toolkit_bump.py` invocation"
            " and an explicit `bump不要` declaration."
            " Per the `agent-toolkit-edit` skill (plan mode handling), include"
            " `scripts/agent_toolkit_bump.py {patch|minor|major}` before the"
            " verification step, or state `bump不要` in the body when no bump applies."
        )
    return None


def _split_markdown_h2_sections(text: str) -> dict[str, str]:
    """Markdown 本文を `## ` 見出しで分割し見出し名→本文の dict を返す。

    `### ` 以下の深い見出しは本文側に含める（`## ` で始まる行は 3 文字目が空白で
    `### ` を排除済みのため追加条件は不要）。
    同名見出しが複数ある場合は後の値で上書きする。
    最初の `## ` 見出しより前のコンテンツ（`# タイトル` 行等）は dict に含めず破棄する。
    """
    sections: dict[str, str] = {}
    current_name: str | None = None
    current_lines: list[str] = []
    for line in text.splitlines():
        if line.startswith("## "):
            if current_name is not None:
                sections[current_name] = "\n".join(current_lines)
            current_name = line[3:].strip()
            current_lines = []
        else:
            current_lines.append(line)
    if current_name is not None:
        sections[current_name] = "\n".join(current_lines)
    return sections


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


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:  # noqa: BLE001 -- フックが破損して編集できなくなる事故を避けるため広範に捕捉
        # 予期せぬ例外は安全側として通過させる。デバッグのためスタックトレースは stderr に出力する。
        traceback.print_exc()
        sys.exit(0)
