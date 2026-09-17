# ruff: noqa: F401,F821,I001
# pylint: disable=unused-import,used-before-assignment,wrong-import-order
r"""Claude Code plugin agent-toolkit: PreToolUse統合フック。

任意ツールの実行前に以下のチェックを順に実行する。
block系checkは1プロセスで直列実行し、最初の違反でexit 2する。
warn種別のcheckはstdoutの`hookSpecificOutput.additionalContext`へ警告を載せつつ処理を継続する
（exit 0で終了したフックのstderrはコーディングエージェントへ届かないため）。
auto-fix種別のcheckは`updatedInput`でツール入力を自動書き換えする。
関連チェック項目は初回で一括開示する（反復サイクル防止のため）。

統合しているチェック:

任意ツール:

- メインエージェント応答の日本語文字比率が閾値未満の場合の警告 (warn)
- ユーザーが直接読む質問本文・計画本文の文字化け、他言語文字、口語表現の検査 (block)
- 質問本文・選択肢が指す`atk`サブコマンドの公開契約が未観測の場合の検査 (block)
- plan-modeスキル未起動のままのplan file編集（Write/Edit/MultiEdit）の警告 (warn)
- plan-modeスキル起動後、計画ファイル未作成のままagent-toolkit配下の直接編集連続の警告 (warn)

固定見出し（新形式と旧形式の互換別名）と固定表の構造、素材表・要求表・素材参照、
計画メタ情報の4項目と記法、計画単位のエージェント提案詳細表（5項目）を含む
フェンス整合、参照実在は
`agent-toolkit/skills/plan-mode/scripts/check_plan_file.py`が担うため
本フックでは扱わない。

mcp__plugin_agent-toolkit_agents_server__start / start_explore / start_write / start_shell / send_message / kill:

- 委譲先へ渡す絶対`cwd`と`send_message`・`kill`のprompt/sessionの検査 (block)
- 全チェック通過時の強制承認 (auto-approve)

list:

- 状態が変化していない再取得を再実行窓付きで遮断 (block)

Bash:

- 多段シェルへのコード文字列、heredocと後段制御演算子の併用、`.env`内容出力の遮断 (block)
- 単純な明示パスの不存在と`atk`未対応オプションの警告 (warn)
- 単純な`git grep`後方オプションの受理位置への移動 (auto-fix)
- 350行を超える通常ファイルの静的に確定できる全文取得の遮断 (block)
- 長い固定`sleep`の後に別コマンドを連結する前景待機の検出 (warn/block)
- 高容量のユーザー領域を無限定に再帰検索する実行位置の検出 (warn)
- 検証コマンド又は保存本文を返すコマンドの出力を`tail`・`head`で切り詰める指定の補正又は遮断 (auto-fix/block)
- 切り詰め直後の`$?`が検証コマンドの終了状態を隠す指定の検出 (warn)
- パターン一致によるプロセス終了（`pkill`・`killall`等）の遮断 (block)
- git amend / rebase直前に`git log`未確認のブロック (block)
- git push実行時のamend後dirty状態のブロック (block)
- 非Pythonプロジェクトでの`uv run python <path>`形式起動の補正又は警告 (auto-fix/warn)
- 除外設定を持たない単純な再帰`grep`の`rg`への補正 (auto-fix)
- `git commit`未検証警告 (warn)
- `agent-toolkit/`配下のコミット時のversion bump漏れ警告 (warn)
- `git log --decorate`の自動付与 (auto-fix)
- `codex exec`の未決事項念押し (warn)
- 一括ステージ実行時の自セッション編集対象外ファイル警告 (warn)

Skill:

- `agent-toolkit:plan-mode`起動時の計画単位の状態リセット (side-effect)

TaskStop:

- 停滞検知完了記録又は自セッション起動記録との対象一致による通過と、それ以外の遮断 (block)

Read / Write / Edit / MultiEdit / apply_patch:

- 文字化け（U+FFFD）検出 (warn。ユーザーが直接読む本文はblock)
- `.ps1` / `.ps1.tmpl`へのLF-only書き込み検出 (warn)
- lockfile / 生成物ディレクトリの直接編集 (warn)
- `.env`系のReadとシークレット・鍵ファイルの直接編集 (block)
- manifestファイルの手編集 (warn)
- ホームディレクトリの絶対パス混入 (warn)
- 口語的な日本語表現の混入 (warn)
- 「Xを根拠にYしない」「Xを理由にYしない」形式のメタ規範文言の増加 (warn)
- .md規範文書のWrite/Edit/MultiEditでfrontmatter同期注記の本体該当語句の実在検証warn (warn)
- 日本語を含む書き込み文字列へのハングル・キリル文字の混入 (warn。ユーザーが直接読む本文はblock)
- .md規範文書の本文中にある他ファイルの節参照の実在検証 (warn)
- 複数断片の全境界が現在内容へ一意に解決できるかの検査 (warn)
- `atk`の公開契約の正本を取得した範囲にある`atk`サブコマンド経路の観測記録 (side-effect)

各チェックの詳細仕様（対象パターン・エラー文言・例外条件）は対応する実装関数のdocstringを参照する。
block系checkの検査対象は「新規に書き込まれる側」（変更後断片）を基本とする。
変更前断片は既存内容の修正・削除を妨げないため単独では検査対象としない。

ホスト差の扱い:

- 編集入力は`_hook_tool_input`が共通の操作記録へ正規化し、検査本体はホストを区別しない
- 非空文字列の`turn_id`をCodex判定の正本とし、payload読込直後に一度だけ判定する
- Bashの終了コードを取得できないCodexでは、成功状態を前提とするamend・rebase、push、commitの各検査を実行しない
- 外部ファイル解決を伴うfrontmatter同期注記・本文節参照の検査と、PowerShellの改行検査はClaude入力へ限定する
- 警告は1つの`hookSpecificOutput.additionalContext`へ結合し、遮断は最初の違反をexit 2とstderrで返す
"""

from __future__ import annotations

import datetime
import importlib
import json
import os
import pathlib
import re
import shlex
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING

from pyfltr.colloquial import check as _colloquial_check  # noqa: E402  # pylint: disable=wrong-import-position

from agent_toolkit._common.file_lock import (  # noqa: E402  # pylint: disable=wrong-import-position,import-error
    locked_rotate_and_append as _locked_rotate_and_append,
)
from agent_toolkit._git import status as _git_status  # noqa: E402  # pylint: disable=wrong-import-position,import-error

# pylint: disable=wrong-import-position
from agent_toolkit._hooks import (
    bash_command_parser as _bash_command_parser,  # noqa: E402  # pylint: disable=wrong-import-position,import-error
)
from agent_toolkit._hooks import (
    response_language_check as _response_language_check,  # noqa: E402  # pylint: disable=wrong-import-position,import-error
)
from agent_toolkit._hooks import (
    scratchpad_path as _scratchpad_path,  # noqa: E402  # pylint: disable=wrong-import-position,import-error
)
from agent_toolkit._hooks import (
    tool_input as _hook_tool_input,  # noqa: E402  # pylint: disable=wrong-import-position,import-error
)
from agent_toolkit._hooks import transcript as _transcript  # noqa: E402  # pylint: disable=wrong-import-position,import-error
from agent_toolkit._hooks.bash_command_parser import (  # noqa: E402  # pylint: disable=wrong-import-position,import-error
    _GLOBAL_OPTIONS_WITH_VALUE,
    _GLOBAL_OPTIONS_WITHOUT_VALUE,
    CwdResolution,
    GitEvent,
    extract_git_events,
    resolve_cwd_change,
    resolve_execution_segment,
    split_bash_segments,
)

# pylint: disable-next=wrong-import-position,import-error
from agent_toolkit._hooks.notice import _WARN_TAG, set_warning_session_id  # noqa: E402

# pylint: disable-next=wrong-import-position,import-error
from agent_toolkit._hooks.notice import block_formatter as _block_notice_formatter  # noqa: E402
from agent_toolkit._hooks.notice import formatter as _notice_formatter  # noqa: E402
from agent_toolkit._hooks.session_state import (  # noqa: E402  # pylint: disable=wrong-import-position,import-error
    bash_failure_gate_is_active,
    read_state,
    update_state,
)
from agent_toolkit._plan import structure as _plan_format  # noqa: E402  # pylint: disable=wrong-import-position,import-error
from agent_toolkit._plan.locations import (  # noqa: E402  # pylint: disable=wrong-import-position,import-error
    is_plan_adjunct_file,
    is_plan_component_file,
    is_plan_handoff_file,
)

if TYPE_CHECKING:
    from agent_toolkit._hooks.pretooluse.agent_checks import (
        _AGENTS_SERVER_KILL_TOOLS,
        _AGENTS_SERVER_LIST_TOOLS,
        _AGENTS_SERVER_SEND_TOOLS,
        _AGENTS_SERVER_START_TOOLS,
        _AGENTS_SERVER_TOOL_NAMES,
        _PLAN_MODE_SKILL_NAMES,
        _check_agents_server_continuation_input,
        _check_agents_server_cwd,
        _check_agents_server_list_repeat,
        _check_generic_agent_preference,
        _check_sendmessage_agent_type_recipient,
        _check_task_stop,
        _check_webfetch_verbatim_request,
        _handle_language_check,
        _record_iss_sidechain_probe,
        _reset_plan_mode_state,
    )
    from agent_toolkit._hooks.pretooluse.content_checks import (
        _check_direct_agent_toolkit_edits_after_plan_mode,
        _check_edit_boundary_resolution,
        _check_edit_operation_blocks,
        _check_plan_mode_skill_first,
        _check_secret_read,
        _collect_edit_operation_warnings,
        _warn_foreign_script_mixin,
        _warn_mojibake,
        check_user_facing_typo,
        record_atk_help_paths_from_read,
    )
    from agent_toolkit._hooks.pretooluse.git_checks import (
        _check_bash_agent_toolkit_version_bump,
        _check_bash_amend_rebase_without_log,
        _check_bash_bulk_stage_with_unedited_files,
        _check_bash_git_commit,
        _check_bash_git_log_decorate,
        _check_bash_git_push_after_amend_with_dirty_status,
    )
    from agent_toolkit._hooks.pretooluse.large_reads import check_large_bash_read, check_large_read
    from agent_toolkit._hooks.pretooluse.notices import _llm_notice
    from agent_toolkit._hooks.pretooluse.shell_checks import (
        _autofix_bash_command,
        _check_bash_codex_exec,
        _check_bash_atk_help_observation,
        _check_bash_atk_options,
        _check_bash_external_command_options,
        _check_bash_explicit_path_exists,
        _check_bash_git_grep_pattern_type,
        _check_bash_option_terminator_missing,
        _check_bash_redirect_parent_exists,
        _check_bash_rg_multiline_pattern,
        _check_bash_unknown_atk_subcommand,
        _check_bash_unquoted_shell_metacharacter,
        _check_bash_unresolved_git_object,
        _check_bash_help_with_execution,
        _check_bash_heredoc_chain,
        _check_bash_env_full_read,
        _check_bash_missing_path_operand_loss,
        _check_bash_nested_code_string,
        _check_bash_output_status_after_truncation,
        _check_bash_output_truncation,
        _check_bash_process_kill_by_pattern,
        _check_bash_python_code_string,
        _check_bash_recursive_grep_without_exclusion,
        _check_bash_recursive_home_search,
        _check_bash_sleep_poll_pattern,
        _check_bash_unbounded_home_traversal,
        _check_bash_unbounded_root_traversal,
        _check_bash_uv_run_python,
    )

_ExecutionSegment = _bash_command_parser.ExecutionSegment
_extract_execution_pipelines = _bash_command_parser.extract_execution_pipelines
_extract_execution_segments = _bash_command_parser.extract_execution_segments
_has_uv_terminal_option = _bash_command_parser.has_uv_terminal_option
_is_python_token = _bash_command_parser.is_python_token

# U+FFFD（REPLACEMENT CHARACTER）: UTF-8デコード失敗時の代替文字
_REPLACEMENT_CHAR = "\ufffd"


def _is_plan_file_or_adjunct(file_path: str) -> bool:
    """計画ファイル（メイン）・計画ファイル（バグ）の場合に真を返す。"""
    return is_plan_component_file(file_path) or is_plan_adjunct_file(file_path)


def _is_claude_job_file(file_path: str) -> bool:
    """Claude Codeが生成するセッション作業領域配下の場合に真を返す。"""
    try:
        target = pathlib.Path(file_path).expanduser().resolve(strict=False)
        jobs = (pathlib.Path.home() / ".claude" / "jobs").resolve(strict=False)
        return target.is_relative_to(jobs) and target != jobs
    except (OSError, ValueError):
        return False


# 日本語の文字（ひらがな・カタカナ・CJK統合漢字）。
_JAPANESE_SCRIPT_RE = re.compile(r"[぀-ゟ゠-ヿ一-鿿]")
# 日本語文中への混入を検出する他言語の文字。
# ハングル字母（U+1100-U+11FF）・ハングル互換字母（U+3130-U+318F）・ハングル音節（U+AC00-U+D7A3）・
# 半角ハングル（U+FFA0-U+FFDC）・キリル文字（U+0400-U+04FF）・キリル補助（U+0500-U+052F）を対象とする。
_FOREIGN_SCRIPT_RE = re.compile("[\u1100-\u11ff\u3130-\u318f\uac00-\ud7a3\uffa0-\uffdc\u0400-\u04ff\u0500-\u052f]")

_USER_FACING_TEXT_TOOL_NAMES: frozenset[str] = frozenset({"AskUserQuestion", "ExitPlanMode"})


def main(payload_text: str) -> int:
    """エントリポイント。

    exit code契約:

    - exit 0: 通過（違反なし / スキップ対象ツール / 想定外入力 / warnのみ）
    - exit 2: block違反検出（stderrに理由を出力）

    予期せぬ例外は0にフォールバックする（pluginのhookが破損して編集できなくなる事故を避けるため）。
    """
    try:
        payload = json.loads(payload_text)
    except (json.JSONDecodeError, ValueError):
        # 想定外入力ではフックを無効化（実処理の破損を避ける安全側の判定）
        return 0

    tool_name = payload.get("tool_name", "")
    tool_input = payload.get("tool_input") or {}
    if not isinstance(tool_input, dict):
        return 0
    session_id_raw = payload.get("session_id", "")
    session_id = session_id_raw if isinstance(session_id_raw, str) else ""
    set_warning_session_id(session_id)
    cwd_raw = payload.get("cwd", "")
    cwd = cwd_raw if isinstance(cwd_raw, str) else ""
    # ホスト判定はpayload読込直後に一度だけ行い、以降の検査選択と入力アダプターへ同じ値を渡す。
    is_codex = _hook_tool_input.is_codex_payload(payload)

    # 直前メインエージェント応答の日本語比率警告（任意ツール）。
    # 他warn系checkがJSONを返す場合はadditionalContextの末尾へ追記し、それ以外は単独でJSON出力する。
    # transcriptを安定インターフェースとして扱えないCodexでは実行しない。
    language_warning_body = None if is_codex else _handle_language_check(payload, session_id)

    # 出力を保留する通知の一覧。exit 0のstderrはコーディングエージェントへ届かないため、
    # 通常はstdoutの`hookSpecificOutput.additionalContext`へ結合して出力する。
    # 遮断で終える場合はJSONを出力しないため、`exit_with`がstderrへ出力して消費する。
    pending_notices: list[str] = []
    if language_warning_body is not None:
        pending_notices.append(_llm_notice(language_warning_body, tag=_WARN_TAG, removable_cause=True))

    def emit_json(result: dict) -> None:
        for notice in pending_notices:
            _append_additional_context(result, notice)
        pending_notices.clear()
        print(json.dumps(result, ensure_ascii=False))

    def flush_pending_notices() -> None:
        if not pending_notices:
            return
        emit_json({"hookSpecificOutput": {"hookEventName": "PreToolUse"}})

    def exit_with(code: int) -> int:
        """終了コードを返す直前に、遮断時だけ保留通知をstderrへ出力して消費する。

        exit 2ではstdoutの構造化JSONが評価されず、保留通知を`additionalContext`で渡せない。
        出力しないまま返すと、検査側のカウンタと最終パスだけが更新されるため、
        同じ入力を再試行しても通知が再生成されず失われる。
        exit 2のstderrはコーディングエージェントへ届くため、遮断理由と同じ経路で出力する。
        """
        if code == 2 and pending_notices:
            print("\n".join(pending_notices), file=sys.stderr)
            pending_notices.clear()
        return code

    # plan mode下でplan-modeスキル未起動のままplan fileを編集しようとした場合は警告（降格）。
    # 呼び出し元はplan-modeの直接委譲手順で計画確定前に警告を解消・検収する
    plan_mode_notice = _check_plan_mode_skill_first(tool_name, tool_input, session_id)
    if plan_mode_notice is not None:
        pending_notices.append(plan_mode_notice)

    # plan-modeスキル起動後、計画ファイル未作成のままagent-toolkit配下の直接編集連続を警告
    _, direct_edit_notice = _check_direct_agent_toolkit_edits_after_plan_mode(tool_name, tool_input, session_id)
    if direct_edit_notice is not None:
        pending_notices.append(direct_edit_notice)

    # plan file編集前の必須リファレンス未読の場合は警告（降格）

    # 編集中はパス契約だけを補助し、意味と構造の検査は確定前の計画検査とレビューへ委ねる。

    if tool_name in _USER_FACING_TEXT_TOOL_NAMES:
        return exit_with(_handle_user_facing_text_tool(tool_name, tool_input, emit_json, flush_pending_notices))

    # Skill: plan-mode起動時は計画単位の状態をリセットする。
    if tool_name == "Skill":
        skill_name = tool_input.get("skill")
        if isinstance(skill_name, str) and skill_name in _PLAN_MODE_SKILL_NAMES:
            _reset_plan_mode_state(session_id)
        flush_pending_notices()
        return 0

    if tool_name in _AGENTS_SERVER_LIST_TOOLS:
        if _check_agents_server_list_repeat(session_id):
            return exit_with(2)
        flush_pending_notices()
        return 0

    if tool_name in _AGENTS_SERVER_TOOL_NAMES:
        return exit_with(_handle_agents_server_tool(payload, tool_name, tool_input, session_id, emit_json))

    if tool_name == "Bash":
        return exit_with(
            _handle_bash_tool(
                payload,
                tool_input,
                session_id,
                emit_json,
                flush_pending_notices,
                is_codex=is_codex,
            )
        )

    if tool_name == "TaskStop":
        if _check_task_stop(session_id, tool_input):
            return exit_with(2)
        flush_pending_notices()
        return 0

    if tool_name == "WebFetch":
        notice = _check_webfetch_verbatim_request(tool_input)
        if notice is not None:
            pending_notices.append(notice)
        flush_pending_notices()
        return 0

    if tool_name == "SendMessage":
        notice = _check_sendmessage_agent_type_recipient(tool_input)
        if notice is not None:
            pending_notices.append(notice)
        flush_pending_notices()
        return 0

    if tool_name in {"Agent", "Task"}:
        notice = _check_generic_agent_preference(tool_input)
        if notice is not None:
            pending_notices.append(notice)
        flush_pending_notices()
        return 0

    if tool_name == "Read":
        file_path = tool_input.get("file_path", "")
        if isinstance(file_path, str) and _check_secret_read(file_path):
            return exit_with(2)
        large_read_fix = check_large_read(tool_input, cwd)
        if large_read_fix is not None:
            corrected_input, large_read_notice = large_read_fix
            record_atk_help_paths_from_read(corrected_input, cwd, session_id)
            emit_json(
                {
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "permissionDecision": "allow",
                        "updatedInput": corrected_input,
                        "additionalContext": "\n".join([*pending_notices, large_read_notice]),
                    }
                }
            )
            return 0
        record_atk_help_paths_from_read(tool_input, cwd, session_id)
        flush_pending_notices()
        return 0

    return exit_with(_handle_edit_tool(tool_name, tool_input, cwd, emit_json, flush_pending_notices, is_codex=is_codex))


def _handle_agents_server_tool(
    payload: dict,
    tool_name: str,
    tool_input: dict,
    session_id: str,
    emit_json: Callable[[dict], None],
) -> int:
    """agents_serverの開始点・観測点を分離して検査する。"""
    _record_iss_sidechain_probe(session_id, tool_name, payload)
    if tool_name in _AGENTS_SERVER_START_TOOLS:
        if _check_agents_server_cwd(tool_input):
            return 2
    elif tool_name in _AGENTS_SERVER_SEND_TOOLS | _AGENTS_SERVER_KILL_TOOLS and _check_agents_server_continuation_input(
        session_id, tool_input, tool_name
    ):
        return 2
    emit_json({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "allow"}})
    return 0


def _handle_bash_tool(
    payload: dict,
    tool_input: dict,
    session_id: str,
    emit_json: Callable[[dict], None],
    flush_warning: Callable[[], None],
    *,
    is_codex: bool,
) -> int:
    """Bashコマンドの遮断・警告・引数補正を処理する。

    Bashの終了コードを取得できないCodexでは、成功状態の生産者が存在しない検査
    （amend・rebase前の`git log`確認、push前のdirty検査、commit前の検証確認）を実行しない。
    現在の入力とcwdだけで判定する検査、PreToolUse自身が記録するsleep poll検査、
    成功した編集が記録する`session_edited_files`を使う一括stage警告は両ホストで共有する。
    """
    command = tool_input.get("command")
    if not isinstance(command, str):
        flush_warning()
        return 0
    cwd_raw = payload.get("cwd", "")
    cwd = cwd_raw if isinstance(cwd_raw, str) else ""
    warnings: list[str] = []
    if bash_failure_gate_is_active(session_id):
        # 通した場合の結果は同じ失敗の反復に限り、作業ツリーへ副作用を残さないため警告で返す。
        warnings.append(
            _llm_notice(
                "同じ終了コードによるBash失敗が連続している。\n"
                "対処: 原因調査と次のコマンド実行をagents_serverのstart_shellへ分離する。",
                tag=_WARN_TAG,
                removable_cause=True,
            )
        )
    large_read_notice = check_large_bash_read(command, cwd)
    if large_read_notice is not None:
        print(large_read_notice, file=sys.stderr)
        return 2
    sleep_poll_result = _check_bash_sleep_poll_pattern(command, session_id, bool(tool_input.get("run_in_background")))
    if sleep_poll_result == "block":
        return 2
    if sleep_poll_result is not None:
        warnings.append(sleep_poll_result)
    if _check_bash_missing_path_operand_loss(command, cwd) == "block":
        return 2
    auto_fix = _autofix_bash_command(command, cwd, session_id)
    if auto_fix is not None:
        command, auto_fix_notice = auto_fix
        tool_input = dict(tool_input)
        tool_input["command"] = command
        warnings.append(auto_fix_notice)
    if (
        (not is_codex and _check_bash_amend_rebase_without_log(command, session_id, cwd))
        or (not is_codex and _check_bash_git_push_after_amend_with_dirty_status(command, session_id, cwd))
        or _check_bash_process_kill_by_pattern(command)
    ):
        return 2
    truncation_result = _check_bash_output_truncation(command, session_id)
    if truncation_result == "block":
        return 2
    if (
        _check_bash_nested_code_string(command)
        or _check_bash_python_code_string(command)
        or _check_bash_heredoc_chain(command)
        or _check_bash_env_full_read(command)
    ):
        return 2
    recursive_grep_result = _check_bash_recursive_grep_without_exclusion(command, cwd)
    if recursive_grep_result == "block":
        return 2
    if _check_bash_unbounded_root_traversal(command) == "block":
        return 2
    git_grep_pattern_type_result = _check_bash_git_grep_pattern_type(command)
    if git_grep_pattern_type_result == "block":
        return 2
    for warning in (
        _check_bash_bulk_stage_with_unedited_files(command, session_id, cwd),
        truncation_result,
        _check_bash_output_status_after_truncation(command),
        _check_bash_recursive_home_search(command),
        _check_bash_unbounded_home_traversal(command),
        recursive_grep_result,
        _check_bash_atk_help_observation(command, session_id),
        _check_bash_external_command_options(command, session_id),
        _check_bash_uv_run_python(command, cwd),
        _check_bash_help_with_execution(command),
        _check_bash_explicit_path_exists(command, cwd),
        _check_bash_atk_options(command),
        _check_bash_unknown_atk_subcommand(command),
        git_grep_pattern_type_result,
        _check_bash_unquoted_shell_metacharacter(command),
        _check_bash_unresolved_git_object(command, cwd),
        _check_bash_rg_multiline_pattern(command),
        _check_bash_option_terminator_missing(command, cwd),
        _check_bash_redirect_parent_exists(command, cwd),
        None if is_codex else _check_bash_git_commit(command, session_id, cwd),
        _check_bash_agent_toolkit_version_bump(command, cwd),
        _check_bash_codex_exec(command),
    ):
        if warning is not None:
            warnings.append(warning)
    result = _check_bash_git_log_decorate(command, tool_input)
    if result is not None:
        if warnings:
            _append_additional_context(result, "\n".join(warnings))
        emit_json(result)
        return 0
    if auto_fix is not None:
        emit_json(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "allow",
                    "updatedInput": tool_input,
                    "additionalContext": "\n".join(warnings),
                }
            }
        )
        return 0
    if warnings:
        emit_json({"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": "\n".join(warnings)}})
    else:
        flush_warning()
    return 0


def _user_facing_text_fields(tool_name: str, tool_input: dict) -> list[tuple[str, str]]:
    """ユーザーが直接読むツール入力から文字列フィールドを順序どおり抽出する。"""
    if tool_name == "ExitPlanMode":
        plan = tool_input.get("plan")
        return [("plan", plan)] if isinstance(plan, str) else []
    questions = tool_input.get("questions")
    if not isinstance(questions, list):
        return []
    fields: list[tuple[str, str]] = []
    for question_index, question in enumerate(questions):
        if not isinstance(question, dict):
            continue
        for name in ("question", "header"):
            value = question.get(name)
            if isinstance(value, str):
                fields.append((f"questions[{question_index}].{name}", value))
        options = question.get("options")
        if not isinstance(options, list):
            continue
        for option_index, option in enumerate(options):
            if not isinstance(option, dict):
                continue
            for name in ("label", "description"):
                value = option.get(name)
                if isinstance(value, str):
                    fields.append((f"questions[{question_index}].options[{option_index}].{name}", value))
    return fields


def _handle_user_facing_text_tool(
    tool_name: str,
    tool_input: dict,
    emit_json: Callable[[dict], None],
    flush_warning: Callable[[], None],
) -> int:
    """質問・計画本文へ言語品質検査と誤字検査を適用し、いずれも警告として返す。

    ユーザーへ直接到達する本文を対象とする検査は、当該本文をユーザー自身が読んで誤りを指摘できるため、
    第1段の復元できない結果に当たらない。遮断すると当該ターンの入力と作業を失わせたうえで
    同じ確認の再発行を要するため、文字化け、日本語以外の文字の混入及び誤字のいずれも警告で返す。
    判定の根拠は`agent-toolkit:writing-standards`の`references/claude-hooks.md`
    「遮断・警告フックの成立条件」が定める。
    """
    warnings: list[str] = []
    fields = _user_facing_text_fields(tool_name, tool_input)
    for warning in (
        _warn_mojibake(tool_name, fields),
        _warn_foreign_script_mixin(tool_name, fields),
        check_user_facing_typo(tool_name, fields),
    ):
        if warning is not None:
            warnings.append(warning)
    if not warnings:
        flush_warning()
    else:
        emit_json({"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": "\n".join(warnings)}})
    return 0


def _handle_edit_tool(
    tool_name: str,
    tool_input: dict,
    cwd: str,
    emit_json: Callable[[dict], None],
    flush_warning: Callable[[], None],
    *,
    is_codex: bool,
) -> int:
    """共通編集単位ごとに遮断検査と警告検査を処理する。

    ClaudeのWrite・Edit・MultiEditとCodexの`apply_patch`を`_hook_tool_input`が
    同一の操作記録へ変換するため、検査本体はホストを区別しない。
    複数対象・複数検査の警告は1つの`additionalContext`へ結合し、遮断は最初の違反で返す。
    """
    operations = _hook_tool_input.parse_operations(tool_name, tool_input, cwd)
    if operations is None:
        flush_warning()
        return 0
    images: dict[int, _hook_tool_input.MaterializedEdit | None] = {}
    for operation in operations:
        if _check_edit_operation_blocks(tool_name, operation):
            return 2
    warnings: list[str] = []
    boundary_warning = _check_edit_boundary_resolution(tool_name, operations)
    if boundary_warning is not None:
        warnings.append(boundary_warning)
    for index, operation in enumerate(operations):
        warnings.extend(_collect_edit_operation_warnings(tool_name, operation, index, images, is_codex=is_codex))
    if warnings:
        emit_json(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "additionalContext": "\n\n".join(warnings),
                },
            }
        )
    else:
        flush_warning()
    return 0


def _materialize_cached(
    operation: _hook_tool_input.EditOperation,
    index: int,
    images: dict[int, _hook_tool_input.MaterializedEdit | None],
) -> _hook_tool_input.MaterializedEdit | None:
    """操作単位の変更前後像を必要になった時点で1回だけ具体化する。"""
    if index not in images:
        images[index] = _hook_tool_input.materialize(operation)
    return images[index]


def _append_additional_context(result: dict, suffix: str) -> None:
    """既存JSON結果の`hookSpecificOutput.additionalContext`末尾へ警告本文を追記する。

    `hookSpecificOutput`が無い・`additionalContext`が文字列でない場合は新規に設定する。
    既存内容との境界には空行を出力する。
    """
    hook_specific = result.get("hookSpecificOutput")
    if not isinstance(hook_specific, dict):
        hook_specific = {"hookEventName": "PreToolUse"}
        result["hookSpecificOutput"] = hook_specific
    existing = hook_specific.get("additionalContext")
    if isinstance(existing, str) and existing:
        hook_specific["additionalContext"] = f"{existing}\n\n{suffix}"
    else:
        hook_specific["additionalContext"] = suffix
