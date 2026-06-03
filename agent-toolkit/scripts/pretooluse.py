#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
# pylint: disable=too-many-lines  # ハンドラ網羅のためチェック実装が多く、分割するとモジュール間の依存関係が複雑化するため許容する
r"""Claude Code plugin agent-toolkit: PreToolUse統合フック。

任意ツールの実行前に以下のチェックを順に実行する。
block系checkは1プロセスで直列実行し、最初の違反でexit 2する。
warn種別のcheckはstderrまたはstdoutに警告を表示しつつ処理を継続する。
auto-fix種別のcheckは`updatedInput`でツール入力を自動書き換えする。

統合しているチェック:

任意ツール:

- メインエージェント応答の日本語文字比率が閾値未満の場合の警告/ブロック (warn/block)
- plan modeで最初のツール呼び出しがplan-modeスキル以外の場合の警告 (warn)

mcp__codex__codex:

- `sandbox`パラメーターの`danger-full-access`自動修正 (auto-fix)

Bash:

- git amend / rebase直前に`git log`未確認のブロック (block)
- 非Pythonプロジェクトでの`uv run python <path>`形式起動のブロック (block)
- `git commit`未検証警告 (warn)
- `git log --decorate`の自動付与 (auto-fix)
- `codex exec`の未決事項念押し (warn)

Write / Edit / MultiEdit:

- 文字化け（U+FFFD）検出 (block)
- `.ps1` / `.ps1.tmpl`へのLF-only書き込み検出 (block)
- lockfile / 生成物ディレクトリの直接編集 (block)
- シークレット / 鍵ファイルの直接編集 (block)
- manifestファイルの手編集 (warn)
- ホームディレクトリの絶対パス混入 (warn)
- 口語的な日本語表現の混入 (warn)

各チェックの詳細仕様（対象パターン・エラー文言・例外条件）は対応する実装関数のdocstringを参照する。
block系checkの検査対象は「新規に書き込まれる側」（`content` / `new_string`）のみ。
`old_string`は既存内容の修正・削除を妨げないため検査しない。
"""

import json
import pathlib
import re
import shlex
import subprocess
import sys
import traceback

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import _colloquial_check  # noqa: E402  # pylint: disable=wrong-import-position,import-error
import _response_language_check  # noqa: E402  # pylint: disable=wrong-import-position,import-error
from _bash_command_parser import (  # noqa: E402  # pylint: disable=wrong-import-position,import-error
    extract_git_events,
)
from _message_format import llm_notice as _llm_notice_base  # noqa: E402  # pylint: disable=wrong-import-position,import-error
from _session_state import read_state, update_state  # noqa: E402  # pylint: disable=wrong-import-position,import-error

# U+FFFD（REPLACEMENT CHARACTER）: UTF-8デコード失敗時の代替文字
_REPLACEMENT_CHAR = "\ufffd"

# このスクリプトの hook 識別子。
_HOOK_ID = "agent-toolkit/pretooluse"


def _llm_notice(body: str, *, tag: str = "") -> str:
    """コーディングエージェント宛てメッセージを標準プレフィックス/サフィックス付きで整形する。"""
    return _llm_notice_base(body, _HOOK_ID, tag=tag)


def _language_notice(body: str) -> str:
    """言語警告専用の整形ヘルパー。

    共通サフィックスの関連性評価を促す英語文が英語化を助長し
    警告効果を弱めるため、プレフィックスのみ付与してサフィックスを省く。
    """
    return f"[auto-generated: {_HOOK_ID}][warn] {body}"


def main() -> int:
    """エントリポイント。

    exit code契約:

    - exit 0: 通過（違反なし / スキップ対象ツール / 想定外入力 / warnのみ）
    - exit 2: block違反検出（stderrに理由を出力）

    予期せぬ例外は0にフォールバックする（pluginのhookが破損して編集できなくなる事故を避けるため）。
    """
    try:
        payload = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, ValueError):
        # 想定外入力ではフックを無効化（実処理の破損を避ける安全側の判定）
        return 0

    tool_name = payload.get("tool_name", "")
    tool_input = payload.get("tool_input") or {}
    if not isinstance(tool_input, dict):
        return 0
    session_id_raw = payload.get("session_id", "")
    session_id = session_id_raw if isinstance(session_id_raw, str) else ""
    permission_mode_raw = payload.get("permission_mode", "")
    permission_mode = permission_mode_raw if isinstance(permission_mode_raw, str) else ""

    # 直前メインエージェント応答の日本語比率警告（任意ツール）。
    # 他warn系checkがJSONを返す場合はadditionalContextの末尾へ追記し、それ以外は単独でJSON出力する。
    exit_code, language_warning_body = _handle_language_check(payload, session_id)
    if exit_code == 2:
        return 2

    def emit_json(result: dict) -> None:
        nonlocal language_warning_body
        if language_warning_body is not None:
            _append_additional_context(result, _language_notice(language_warning_body))
            language_warning_body = None
        print(json.dumps(result, ensure_ascii=False))

    def flush_pending_language_warning() -> None:
        nonlocal language_warning_body
        if language_warning_body is None:
            return
        body = language_warning_body
        language_warning_body = None
        print(
            json.dumps(
                {
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "permissionDecision": "allow",
                        "additionalContext": _language_notice(body),
                    },
                },
                ensure_ascii=False,
            ),
        )

    # plan modeで最初のツール呼び出しがplan-modeスキル以外なら警告（任意ツール）
    plan_mode_result = _check_plan_mode_skill_first(tool_name, tool_input, permission_mode, session_id)
    if plan_mode_result is not None:
        emit_json(plan_mode_result)
        # 1セッション1回限りの警告のため、同フックでの後続checkは省略する
        return 0

    # mcp__codex__codex: sandbox自動修正
    if tool_name == "mcp__codex__codex":
        result = _check_codex_mcp_sandbox(tool_input)
        if result is not None:
            emit_json(result)
            return 0
        flush_pending_language_warning()
        return 0

    # Bashは専用ハンドラ
    if tool_name == "Bash":
        command = tool_input.get("command")
        if not isinstance(command, str):
            flush_pending_language_warning()
            return 0
        cwd_raw = payload.get("cwd", "")
        cwd = cwd_raw if isinstance(cwd_raw, str) else ""
        # git amend / rebaseは直前にgit logを確認していなければブロック
        if _check_bash_amend_rebase_without_log(command, session_id, cwd):
            return 2
        # uv run python <path>形式の起動は非Pythonプロジェクトでブロック
        if _check_bash_uv_run_python(command, cwd):
            return 2
        # git commit未検証警告
        result = _check_bash_git_commit(command, session_id, cwd)
        if result is not None:
            emit_json(result)
            return 0
        # git log --decorate自動付与
        result = _check_bash_git_log_decorate(command, tool_input)
        if result is not None:
            emit_json(result)
            return 0
        # codex exec未決事項の念押し
        result = _check_bash_codex_exec(command)
        if result is not None:
            emit_json(result)
            return 0
        flush_pending_language_warning()
        return 0

    # Write/Edit/MultiEdit以外は全スキップ
    fields = _collect_new_fields(tool_name, tool_input)
    if fields is None:
        flush_pending_language_warning()
        return 0

    file_path_raw = tool_input.get("file_path")
    file_path = file_path_raw if isinstance(file_path_raw, str) else ""

    # --- block系check（最初の違反でexit 2）---
    if _check_mojibake(tool_name, fields):
        return 2
    # Edit/MultiEditは内部的にCRLFを透過的に維持するためチェック不要。
    # WriteのみLFで書き込むためEOLチェックを実行する。
    if tool_name == "Write" and _is_ps1(file_path) and _check_ps1_eol(tool_name, fields, file_path):
        return 2
    if _check_lockfiles(tool_name, file_path):
        return 2
    if _check_secrets(tool_name, file_path):
        return 2

    # --- warn系check（stderrに警告のみ、exit codeは0のまま）---
    _check_manifest(tool_name, file_path)
    _check_home_path(tool_name, fields, file_path)
    _check_colloquial(tool_name, fields, file_path)

    flush_pending_language_warning()
    return 0


def _handle_language_check(payload: dict, session_id: str) -> tuple[int | None, str | None]:
    """直前メインエージェント応答の言語検査を実行し、セッション状態でエスカレーションを管理する。

    Returns:
        (exit code, 警告本文)のタプル。
        exit code 2: ブロック（stderrに出力済み）。
        exit code None + 本文あり: 警告（呼び出し側でadditionalContextに追記）。
        exit code None + 本文None: 対象外。

    セッション状態キー:
    - english_warning_count: 連続英語ターンのカウンタ（int）
    - english_warning_msg_id: 前回検出時のmessage ID（str）

    エスカレーションロジック:
    - WARN: message IDが前回と異なればカウンタ+1、同一なら据え置き。カウンタ≧2でブロック
    - PASS: カウンタを0にリセット
    - SKIP: カウンタ変更なし
    - ブロック後はカウンタを1に設定する（日本語に切り替わるまで毎ターンブロックを継続）
    """
    transcript_path = payload.get("transcript_path", "")
    if not isinstance(transcript_path, str) or not transcript_path:
        return (None, None)
    if payload.get("isSidechain") is True:
        return (None, None)

    outcome, body, msg_id = _response_language_check.detailed_check(transcript_path)

    if outcome is _response_language_check.CheckOutcome.SKIP:
        return (None, None)

    if outcome is _response_language_check.CheckOutcome.PASS:
        if session_id:

            def _reset_count(current: dict) -> dict | None:
                if current.get("english_warning_count", 0) == 0:
                    return None
                current["english_warning_count"] = 0
                return current

            update_state(session_id, _reset_count)
        return (None, None)

    # WARN
    if not session_id:
        return (None, body)

    # update_stateがOSErrorで失敗した場合、_incrementは実行されずcountは初期値0のまま残る。
    # この場合はブロックしない方向（安全側）にフォールバックする。
    count = 0

    def _increment(current: dict) -> dict | None:
        nonlocal count
        prev_id = current.get("english_warning_msg_id", "")
        prev_count = current.get("english_warning_count", 0)
        if msg_id and prev_id == msg_id:
            count = prev_count
            return None
        count = prev_count + 1
        current["english_warning_count"] = count
        current["english_warning_msg_id"] = msg_id
        return current

    update_state(session_id, _increment)

    if count >= 2:

        def _set_threshold(current: dict) -> dict | None:
            current["english_warning_count"] = 1
            return current

        update_state(session_id, _set_threshold)
        print(_language_notice(_response_language_check.BLOCK_BODY), file=sys.stderr)
        return (2, None)

    return (None, body)


def _append_additional_context(result: dict, suffix: str) -> None:
    """既存JSON結果の`hookSpecificOutput.additionalContext`末尾へ警告本文を追記する。

    `hookSpecificOutput`が無い・`additionalContext`が文字列でない場合は新規に設定する。
    既存内容との境界には空行を出力する。
    """
    hook_specific = result.get("hookSpecificOutput")
    if not isinstance(hook_specific, dict):
        hook_specific = {"hookEventName": "PreToolUse", "permissionDecision": "allow"}
        result["hookSpecificOutput"] = hook_specific
    existing = hook_specific.get("additionalContext")
    if isinstance(existing, str) and existing:
        hook_specific["additionalContext"] = f"{existing}\n\n{suffix}"
    else:
        hook_specific["additionalContext"] = suffix


def _collect_new_fields(tool_name: str, tool_input: dict) -> list[tuple[str, str]] | None:
    """対象ツールの「新規書き込みフィールド」を（field名, 値）のリストで返す。

    対象外ツールの場合はNoneを返す。文字列でない値はスキップする。
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


def _check_mojibake(tool_name: str, fields: list[tuple[str, str]]) -> bool:
    """U+FFFD（mojibake）を検出したらTrueを返す。"""
    for field, value in fields:
        position = value.find(_REPLACEMENT_CHAR)
        if position == -1:
            continue
        start = max(0, position - 10)
        end = min(len(value), position + 11)
        sample = value[start:end]
        print(
            _llm_notice(f"blocked: U+FFFD (mojibake) detected in {tool_name}.{field}. Context: {sample!r}"),
            file=sys.stderr,
        )
        return True
    return False


def _is_ps1(file_path: str) -> bool:
    """`.ps1` / `.ps1.tmpl`の場合に真を返す。"""
    lowered = file_path.lower()
    return lowered.endswith(".ps1") or lowered.endswith(".ps1.tmpl")


def _check_ps1_eol(tool_name: str, fields: list[tuple[str, str]], file_path: str) -> bool:
    """PowerShellスクリプトへのLF-only書き込みを検出したらTrueを返す。"""
    for field, value in fields:
        if "\n" not in value:
            continue
        if "\r\n" in value:
            continue
        print(
            _llm_notice(
                f"blocked: LF-only content detected in {tool_name}.{field}."
                f" PowerShell 5.1 cannot parse .ps1 files with LF line endings; CRLF is required."
                f" Use the Edit tool for existing files (it preserves CRLF transparently)."
                f" For new files, write via Bash with a UTF-8 BOM and CRLF line endings"
                f" (e.g., printf '\\xEF\\xBB\\xBF' > file.ps1 && ... | sed 's/$/\\r/' >> file.ps1)."
                f" Target: {file_path}"
            ),
            file=sys.stderr,
        )
        return True
    return False


# --- lockfile / 生成物ディレクトリcheck ---

# （label, regex, hint）のタプル。regexはfile_path全体に対するマッチ。
_LOCKFILE_RULES: tuple[tuple[str, re.Pattern[str], str], ...] = (
    ("uv.lock", re.compile(r"(^|/)uv\.lock$"), "Use `uv add` to add dependencies and `uv remove` to remove them."),
    (
        "pnpm-lock.yaml",
        re.compile(r"(^|/)pnpm-lock\.yaml$"),
        "Use `pnpm add` to add dependencies and `pnpm remove` to remove them.",
    ),
    ("package-lock.json", re.compile(r"(^|/)package-lock\.json$"), "Use `npm install <pkg>` to add dependencies."),
    ("yarn.lock", re.compile(r"(^|/)yarn\.lock$"), "Use `yarn add` to add dependencies."),
    ("Cargo.lock", re.compile(r"(^|/)Cargo\.lock$"), "Use `cargo add` to add dependencies."),
    ("mise.lock", re.compile(r"(^|/)mise\.lock$"), "Use `mise use` / `mise install` for tool management."),
    (
        ".venv/",
        re.compile(r"(^|/)\.venv/"),
        "Do not edit virtual environment files directly; rebuild with uv or similar.",
    ),
    (
        "node_modules/",
        re.compile(r"(^|/)node_modules/"),
        "node_modules is a generated directory; do not edit it directly.",
    ),
)


def _check_lockfiles(tool_name: str, file_path: str) -> bool:
    """lockfileや生成物ディレクトリへの直接編集を検出した場合に真を返す。"""
    if not file_path:
        return False
    normalized = file_path.replace("\\", "/")
    for label, pattern, hint in _LOCKFILE_RULES:
        if pattern.search(normalized):
            print(
                _llm_notice(f"blocked: direct edit of {label} is prohibited by {tool_name}. {hint} Target: {file_path}"),
                file=sys.stderr,
            )
            return True
    return False


# --- シークレット / 鍵ファイルcheck ---

_SECRETS_PATTERN = re.compile(
    r"(^|/)("
    r"\.env(\..+)?"
    r"|\.encrypt_key"
    r"|\.secret_key"
    r"|github_action(\.pub)?"
    r"|[^/]+\.(pem|key)"
    r")$"
)

_SECRETS_EXEMPT_SUFFIXES: tuple[str, ...] = (".example", ".sample", "-example", "-sample")


def _check_secrets(tool_name: str, file_path: str) -> bool:
    """シークレット / 鍵ファイルへの直接編集を検出した場合に真を返す。"""
    if not file_path:
        return False
    normalized = file_path.replace("\\", "/")
    if normalized.endswith(_SECRETS_EXEMPT_SUFFIXES):
        return False
    if _SECRETS_PATTERN.search(normalized):
        print(
            _llm_notice(
                f"blocked: direct edit of secret / key files is prohibited by {tool_name}."
                f" Accidental edits can cause service outages or data leaks. Target: {file_path}"
            ),
            file=sys.stderr,
        )
        return True
    return False


# --- manifest手編集check (warn) ---

_MANIFEST_RULES: tuple[tuple[str, re.Pattern[str], str], ...] = (
    (
        "pyproject.toml",
        re.compile(r"(^|/)pyproject\.toml$"),
        (
            "For [project.dependencies] / [project.optional-dependencies],"
            " use `uv add` / `uv remove` (to keep uv.lock in sync)."
            " For [tool.*] or version edits, proceed as-is."
        ),
    ),
    (
        "package.json",
        re.compile(r"(^|/)package\.json$"),
        (
            "For dependency edits, use `pnpm add` / `pnpm remove`"
            " (to keep pnpm-lock.yaml in sync). For scripts or metadata edits, proceed as-is."
        ),
    ),
)


def _check_manifest(tool_name: str, file_path: str) -> bool:
    """manifest手編集を検出したら警告を表示して真を返す（warnのみ、exit codeは変えない）。"""
    if not file_path:
        return False
    normalized = file_path.replace("\\", "/")
    for label, pattern, hint in _MANIFEST_RULES:
        if pattern.search(normalized):
            print(
                _llm_notice(
                    f"editing {label} via {tool_name}. {hint}",
                    tag="warn",
                ),
                file=sys.stderr,
            )
            return True
    return False


# --- ホームディレクトリパス混入check (warn) ---

# 混入を許容するファイル末尾パターン（ローカル設定やログなど）
_HOME_PATH_SKIP_SUFFIXES: tuple[str, ...] = (
    ".local.md",
    ".local.json",
    ".local.yaml",
    ".local.yml",
    ".local.toml",
    ".jsonl",
    ".log",
)


def _check_home_path(tool_name: str, fields: list[tuple[str, str]], file_path: str) -> bool:
    """ホームディレクトリの絶対パス混入を検出したら警告を表示して真を返す。

    リポジトリ管理ファイルに`/home/user/...`のような環境依存パスが書き込まれると
    他環境での再現性が失われるため警告する。警告のみでeditは継続（warn）。
    """
    home_str = str(pathlib.Path.home())
    # ルートなど極端に短いパスは誤検出を避けてスキップ。
    if len(home_str) < 3:
        return False

    normalized_path = file_path.replace("\\", "/")
    if normalized_path.endswith(_HOME_PATH_SKIP_SUFFIXES):
        return False
    if normalized_path.endswith("/CLAUDE.local.md") or normalized_path == "CLAUDE.local.md":
        return False
    if normalized_path.endswith("/.claude/settings.local.json"):
        return False

    # POSIX正規化された両表記で検査（WindowsからPOSIX風パスが混入するケースに対応）
    candidates = {home_str, home_str.replace("\\", "/")}

    for field, value in fields:
        for home in candidates:
            position = value.find(home)
            if position == -1:
                continue
            start = max(0, position - 20)
            end = min(len(value), position + len(home) + 20)
            sample = value[start:end]
            print(
                _llm_notice(
                    f"home directory absolute path ({home}) detected in {tool_name}.{field}."
                    f" In version-controlled files, use `~`, `$HOME`, or `pathlib.Path.home()`"
                    f" instead to avoid environment-dependent paths."
                    f" Context: {sample!r}",
                    tag="warn",
                ),
                file=sys.stderr,
            )
            return True
    return False


# --- 口語表現混入check (warn) ---

# モジュールロード時に1回だけコンパイルする。
# 検出語そのものをコーディングエージェントのコンテキストへ持ち込まないよう、
# 本ファイルからパターンの実体を文字列で参照しない。
_COLLOQUIAL_DENY_PATTERNS = _colloquial_check.load_patterns(_colloquial_check.DENY_PATH)
_COLLOQUIAL_ALLOW_PATTERNS = _colloquial_check.load_patterns(_colloquial_check.ALLOW_PATH)


def _check_colloquial(tool_name: str, fields: list[tuple[str, str]], file_path: str) -> bool:
    """口語的な日本語表現の混入を検出して警告する（warn）。

    検出した語そのものは出力に含めない（コーディングエージェントのコンテキスト汚染防止）。
    allowlistに一致する部分を先に除去してからdenylistを適用し、
    複合動詞・複合名詞などの標準用語が誤検出されることを抑える。
    """
    for field, value in fields:
        if not value:
            continue
        if _colloquial_check.first_hit(value, _COLLOQUIAL_DENY_PATTERNS, _COLLOQUIAL_ALLOW_PATTERNS):
            print(
                _llm_notice(
                    f"colloquial Japanese expressions detected in {tool_name}.{field}."
                    f" Rewrite using formal written-style expressions"
                    f" (standard technical terminology, dictionary form,"
                    f" no metaphorical verbs) per agent.md 「言語表現」 chapter."
                    f" Target: {file_path}",
                    tag="warn",
                ),
                file=sys.stderr,
            )
            return True
    return False


# --- plan mode中の最初のツール呼び出しがplan-modeスキル以外の場合の警告（warn）---

# Skillツールの`skill`引数として許容するplan-modeスキル名。
# ユーザーが手動で短縮名を渡すケースに備えてフルネームと短縮名の両方を許容する。
# posttooluse.pyの同名定数と同期しておく。
_PLAN_MODE_SKILL_NAMES = frozenset({"agent-toolkit:plan-mode", "plan-mode"})


def _check_plan_mode_skill_first(
    tool_name: str,
    tool_input: dict,
    permission_mode: str,
    session_id: str,
) -> dict | None:
    """Plan mode中で最初のツール呼び出しがplan-modeスキル以外の場合に警告を返す。

    判定条件:

    - `permission_mode == "plan"`
    - セッション状態の`plan_mode_skill_invoked`が偽
    - セッション状態の`plan_mode_warning_emitted`が偽（1セッション1回のみ）

    例外: 当該呼び出しがSkillツールかつスキル名が`_PLAN_MODE_SKILL_NAMES`
    に含まれる場合は警告しない。

    警告発火時は`plan_mode_warning_emitted`を真にして以後の発火を抑制する。
    """
    if permission_mode != "plan":
        return None
    if not session_id:
        return None
    state = read_state(session_id)
    if state.get("plan_mode_skill_invoked", False):
        return None
    if state.get("plan_mode_warning_emitted", False):
        return None
    if tool_name == "Skill":
        skill_name = tool_input.get("skill")
        if isinstance(skill_name, str) and skill_name in _PLAN_MODE_SKILL_NAMES:
            return None

    def _set_warning(current: dict) -> dict | None:
        if current.get("plan_mode_warning_emitted", False):
            return None
        current["plan_mode_warning_emitted"] = True
        return current

    update_state(session_id, _set_warning)
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "allow",
            "additionalContext": _llm_notice(
                "in plan mode, but the first tool call is not the plan-mode skill."
                " Invoke `agent-toolkit:plan-mode` skill first to load the latest section"
                " structure and review process guidance before drafting the plan.",
                tag="warn",
            ),
        },
    }


# --- Bash: heredoc内のパターンを除外するヘルパー ---


def _likely_real_command(command: str, pos: int) -> bool:
    """マッチ位置がシェルコマンド文脈にあるかヒューリスティックで判定する。

    heredoc（`<<`）がマッチ位置より前にある場合、マッチはリテラル文字列の
    一部である可能性が高いため偽を返す。
    `python3 -c` / `cat <<`等でファイル内容を書き込むケースの誤検出を防ぐ。
    """
    prefix = command[:pos]
    return "<<" not in prefix


# --- Bash: 関連定数（git commit検出）---

_GIT_COMMIT_PATTERN = re.compile(r"\bgit\s+commit\b")


# --- Bash: git amend / rebaseをlog未確認でブロック ---


def _check_bash_amend_rebase_without_log(command: str, session_id: str, cwd: str) -> bool:
    """Git commit --amend / git rebaseをgit log未確認で実行しようとした場合にブロックする。

    amend / rebaseは既存コミットを書き換えるため、直前にgit log --decorateで
    コミット状態（特にプッシュ済みかどうか）を確認する必要がある。
    ファイル編集・commit・rebase・push・Stopが介在すると確認状態をリセットする。
    ユーザーが裏でpushしている可能性があるためリセット対象に含める。

    `git_log_checked`はcwd別に管理する辞書`{cwd: True}`形式を採用する。
    旧形式のbool値（`True` / `False`）はcwd空文字列環境向けの後方互換として
    そのまま参照する。
    判定は`extract_git_events`の結果を消費し、各git呼び出しの実効cwd
    （`cd`・`pushd`・`git -C`の影響を反映）ごとに行う。
    """
    targets: list[tuple[str, str]] = []
    for event in extract_git_events(command, cwd):
        if event.subcommand == "commit" and "--amend" in event.subcommand_args:
            targets.append((event.cwd, "git commit --amend"))
        elif event.subcommand == "rebase":
            targets.append((event.cwd, "git rebase"))
    if not targets:
        return False
    state = read_state(session_id)
    log_state = state.get("git_log_checked", False)
    for event_cwd, op in targets:
        if isinstance(log_state, dict):
            if event_cwd and log_state.get(event_cwd, False):
                continue
        elif log_state:
            continue
        print(
            _llm_notice(
                f"blocked: {op}."
                f" Run `git log --oneline --decorate` first to confirm commit state before amend/rebase"
                f" (especially, do NOT amend/rebase commits that have already been pushed)."
            ),
            file=sys.stderr,
        )
        return True
    return False


# --- Bash: uv run python <path>形式の起動ブロック ---

# 副作用の理由:
# cwdのpyproject.tomlが[tool.uv]のみで[project]セクションを持たない場合、
# `uv run python <path>`はcwdをプロジェクト解決対象として扱い`.venv`と
# `uv.lock`を生成する（uvの仕様）。
# エージェントがPEP 723スクリプトを誤って`uv run python <path>`形式で起動する
# 事故を予防的にblockする。
#
# 判定の優先順位:
#
# 1. `uv run`と`python`の間（uv run自身のオプション位置）に`--script`または
#    `--no-project`が現れる場合は許容する（cwdの依存解決を行わないため副作用なし）。
# 2. cwd変更経路（Bashの`cd` / `pushd`先行・`uv --directory` / `uv --project`）
#    が無く、cwdのpyproject.tomlが[project]セクションを持つPythonプロジェクト
#    の場合は許容する（`uv run python -c '...'`等の正規利用を妨げない）。
# 3. それ以外はblockする。
#
# cwd変更経路を伴う場合はpayload上のcwdを判定根拠に採用できないため、Python
# プロジェクト判定をスキップしてblock側に倒す（副作用の有無を確実に判定できない
# ため安全側の挙動とする）。
# 環境変数経由のcwd / project切り替え（UV_WORKING_DIR / UV_PROJECT）は
# 利用頻度が低く実装コストに見合わないため対応スコープ外とする。

_UV_RUN_PYTHON_BLOCK_MSG = (
    "blocked: `uv run python <path>` style invocation."
    " In a non-Python project (pyproject.toml without a [project] section, or absent),"
    " uv treats the cwd as a project and generates `.venv` and `uv.lock` as a side effect."
    " Alternatives:"
    " (1) for a PEP 723 script, use `uv run --script <path>` or invoke the executable shebang directly;"
    " (2) to skip cwd project resolution, use `uv run --no-project python ...`;"
    " (3) inside a Python project, `cd` to the project root before running."
)

_ENV_ASSIGN_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z_0-9]*=")
_PYTHON_TOKEN_PATTERN = re.compile(r"^python[0-9.]*(?:\.exe)?$", re.IGNORECASE)
_PYPROJECT_PROJECT_SECTION_PATTERN = re.compile(r"(?m)^\[project(?:\.[\w\-]+)?\]\s*$")


def _check_bash_uv_run_python(command: str, cwd: str) -> bool:
    """`uv run python <path>`形式の起動を非Pythonプロジェクトでブロックする。

    判定詳細は本関数の冒頭コメントを参照する。真を返すとblock（exit 2）。
    """
    # heredocを含むコマンドは本文中のリテラル混入で誤検出する余地があるため通過させる。
    if "<<" in command:
        return False
    segments = _split_bash_segments(command)
    cwd_changed_before = False
    for segment in segments:
        try:
            tokens = shlex.split(segment, posix=True)
        except ValueError:
            return False
        info = _parse_uv_run_python(tokens)
        if info is not None:
            has_script_or_no_project, directory_or_project_overridden = info
            if not has_script_or_no_project and (
                directory_or_project_overridden or cwd_changed_before or not _cwd_is_python_project(cwd)
            ):
                print(_llm_notice(_UV_RUN_PYTHON_BLOCK_MSG), file=sys.stderr)
                return True
        if _segment_changes_cwd(tokens):
            cwd_changed_before = True
    return False


def _split_bash_segments(command: str) -> list[str]:
    """Bashコマンドを`;` / `&&` / `||` / `|` / `&`で分割する。

    クォート（`'` / `"`）内のメタ文字は分割対象外とする。
    バックスラッシュエスケープやheredocは厳密に扱わないため、heredocを含む
    コマンドは呼び出し側で除外する想定。
    """
    segments: list[str] = []
    buf: list[str] = []
    in_single = False
    in_double = False
    i = 0
    while i < len(command):
        c = command[i]
        if in_single:
            buf.append(c)
            if c == "'":
                in_single = False
            i += 1
            continue
        if in_double:
            buf.append(c)
            if c == '"':
                in_double = False
            i += 1
            continue
        if c == "'":
            in_single = True
            buf.append(c)
            i += 1
            continue
        if c == '"':
            in_double = True
            buf.append(c)
            i += 1
            continue
        if c in ("&", "|") and i + 1 < len(command) and command[i + 1] == c:
            segments.append("".join(buf))
            buf = []
            i += 2
            continue
        if c in (";", "&", "|"):
            segments.append("".join(buf))
            buf = []
            i += 1
            continue
        buf.append(c)
        i += 1
    if buf:
        segments.append("".join(buf))
    return [s.strip() for s in segments if s.strip()]


def _skip_env_assignments(tokens: list[str], start: int) -> int:
    """先頭の`KEY=VALUE`形式の環境変数代入をスキップした次の位置を返す。"""
    i = start
    while i < len(tokens) and _ENV_ASSIGN_PATTERN.match(tokens[i]):
        i += 1
    return i


def _segment_changes_cwd(tokens: list[str]) -> bool:
    """セグメント先頭のコマンドが`cd` / `pushd` / `popd`の場合に真を返す。"""
    i = _skip_env_assignments(tokens, 0)
    if i >= len(tokens):
        return False
    return tokens[i] in ("cd", "pushd", "popd")


def _is_python_token(token: str) -> bool:
    """`python` / `python3` / `python3.12`などの実行ファイル名トークンの場合に真を返す。"""
    return _PYTHON_TOKEN_PATTERN.match(token) is not None


def _parse_uv_run_python(tokens: list[str]) -> tuple[bool, bool] | None:
    """`uv [...] run [...] python`構造をtokensから検出する。

    構造を検出した場合は`(has_script_or_no_project, directory_or_project_overridden)`を返す。
    対象構造でなければNoneを返す。
    `--script` / `--no-project`は`uv`トークンと`python`トークンの間に
    出現する場合のみ「uv runのオプション」として扱う（`python`以降に書かれた
    場合は`python`の引数として解釈されるため対象外）。
    """
    i = _skip_env_assignments(tokens, 0)
    if i >= len(tokens) or tokens[i] != "uv":
        return None
    uv_idx = i
    python_idx: int | None = None
    for j in range(uv_idx + 1, len(tokens)):
        if _is_python_token(tokens[j]):
            python_idx = j
            break
    if python_idx is None:
        return None
    has_run_between = any(tokens[j] == "run" for j in range(uv_idx + 1, python_idx))
    if not has_run_between:
        return None
    has_script_or_no_project = False
    directory_or_project_overridden = False
    for tok in tokens[uv_idx + 1 : python_idx]:
        if tok in ("--script", "--no-project"):
            has_script_or_no_project = True
        elif tok in ("--directory", "--project") or tok.startswith("--directory=") or tok.startswith("--project="):
            directory_or_project_overridden = True
    return has_script_or_no_project, directory_or_project_overridden


def _cwd_is_python_project(cwd: str) -> bool:
    """cwdの`pyproject.toml`が`[project]`セクションを持つ場合に真を返す。

    `pyproject.toml`不在・読み込み失敗・`[project]`セクション欠如の場合は偽を返す。
    """
    if not cwd:
        return False
    try:
        text = (pathlib.Path(cwd) / "pyproject.toml").read_text(encoding="utf-8", errors="replace")
    except (OSError, ValueError):
        return False
    return _PYPROJECT_PROJECT_SECTION_PATTERN.search(text) is not None


# --- Bash: git commit未検証警告 ---


_GIT_COMMIT_INCLUDE_WORKTREE_PATTERN = re.compile(r"(?:^|\s)(?:-\w*a\w*|--all)\b")


def _is_docs_only_commit(command: str, cwd: str) -> bool:
    """コミット対象のファイルが全てMarkdownの場合に真を返す。

    docs-only変更では手動テストを省略しpre-commit側のtextlint / markdownlintに
    委ねる運用を想定しており、その場合に未検証警告を抑制する。

    `git commit -a` / `--all`等のコマンドでは作業ツリー側の変更も対象となるため、
    stagedとworking treeを切り分けて判定する。
    `cwd`不在やgit呼び出し失敗時は偽を返して警告を継続する。
    """
    if not cwd:
        return False
    match = _GIT_COMMIT_PATTERN.search(command)
    if match is None:
        return False
    tail = command[match.end() :]
    for delimiter in (";", "|", "&&"):
        pos = tail.find(delimiter)
        if pos != -1:
            tail = tail[:pos]
    include_working_tree = _GIT_COMMIT_INCLUDE_WORKTREE_PATTERN.search(tail) is not None
    args = ["git", "diff", "--name-only", "HEAD"] if include_working_tree else ["git", "diff", "--cached", "--name-only"]
    try:
        result = subprocess.run(args, capture_output=True, text=True, check=False, cwd=cwd, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return False
    if result.returncode != 0:
        return False
    files = [line for line in result.stdout.splitlines() if line.strip()]
    if not files:
        return False
    return all(path.lower().endswith(".md") for path in files)


def _check_bash_git_commit(command: str, session_id: str, cwd: str) -> dict | None:
    """テスト未実行のままgit commitする場合に警告JSONを返す。

    テスト実行済み（stateの`test_executed`が真）の場合はスキップする。
    状態ファイル不在時は`test_executed` = falseとして扱い警告を表示する。
    コミット対象が全てMarkdownファイルの場合はpre-commit側に検証を委ねる運用を想定してスキップする。
    """
    match = _GIT_COMMIT_PATTERN.search(command)
    if match is None or not _likely_real_command(command, match.start()):
        return None
    state = read_state(session_id)
    if state.get("test_executed", False):
        return None
    if _is_docs_only_commit(command, cwd):
        return None
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "allow",
            "additionalContext": _llm_notice(
                "committing without running tests. Follow the verify-then-commit procedure in agent.md and run tests first.",
                tag="warn",
            ),
        },
    }


# --- Bash: git log --decorate自動付与 ---

_GIT_LOG_INSERT_REGEX = re.compile(r"\bgit\s+log\b")


def _check_bash_git_log_decorate(command: str, tool_input: dict) -> dict | None:
    r"""Git logに--decorateがない場合、自動で挿入したupdatedInputを返す。

    `extract_git_events`の結果から`subcommand == "log"`かつ`subcommand_args`に
    `--decorate`を含まない最初のイベントを対象とする。
    コマンド本文上の挿入位置は同順に並ぶ`git\\s+log`マッチから取得する。
    heredoc内のリテラル一致は`_likely_real_command`で除外する。
    """
    log_events = [event for event in extract_git_events(command, "") if event.subcommand == "log"]
    target_index = next(
        (i for i, event in enumerate(log_events) if "--decorate" not in event.subcommand_args),
        None,
    )
    if target_index is None:
        return None
    matches = [m for m in _GIT_LOG_INSERT_REGEX.finditer(command) if _likely_real_command(command, m.start())]
    if target_index >= len(matches):
        return None
    match = matches[target_index]
    updated_command = command[: match.end()] + " --decorate" + command[match.end() :]
    updated_input = dict(tool_input)
    updated_input["command"] = updated_command
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "allow",
            "updatedInput": updated_input,
        },
        "systemMessage": "[agent-toolkit] git logに--decorateを自動的に挿入しました。",
    }


# --- Bash: codex exec未決事項の念押し ---

_CODEX_EXEC_PATTERN = re.compile(r"\bcodex\s+exec\b")
_CODEX_RESUME_PATTERN_PRE = re.compile(r"\bcodex\s+exec\s+resume\b")


def _check_bash_codex_exec(command: str) -> dict | None:
    """Codex exec（resume以外）を検出した場合に未決事項確認の念押しメッセージを返す。"""
    exec_match = _CODEX_EXEC_PATTERN.search(command)
    if exec_match is None or not _likely_real_command(command, exec_match.start()):
        return None
    if _CODEX_RESUME_PATTERN_PRE.search(command):
        return None
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "allow",
            "additionalContext": _llm_notice(
                "submitting plan file to codex review."
                " Pre-submission check: are there any decisions made by assumption"
                " rather than user confirmation?"
                " Resolve any open questions with the user before proceeding."
            ),
        },
    }


# --- mcp__codex__codex: sandbox自動修正 ---


def _check_codex_mcp_sandbox(tool_input: dict) -> dict | None:
    """Codex MCP呼び出しのsandboxがdanger-full-accessでなければ自動修正する。"""
    sandbox = tool_input.get("sandbox")
    if sandbox == "danger-full-access":
        return None
    updated_input = dict(tool_input)
    updated_input["sandbox"] = "danger-full-access"
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "allow",
            "updatedInput": updated_input,
        },
        "systemMessage": "[agent-toolkit] codex MCPのsandboxをdanger-full-accessに自動修正しました。",
    }


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:  # noqa: BLE001 -- pluginが破損して編集できなくなる事故を避けるため広範に捕捉
        # 予期せぬ例外は安全側として通過させる。デバッグのためスタックトレースはstderrに出力する。
        traceback.print_exc()
        sys.exit(0)
