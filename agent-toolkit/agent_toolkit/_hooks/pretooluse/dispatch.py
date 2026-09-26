# ruff: noqa: F401,F821,I001
# pylint: disable=unused-import,used-before-assignment,wrong-import-order
r"""Claude Code plugin agent-toolkit: PreToolUse統合フック。

任意ツールの実行前に以下のチェックを順に実行する。
block系checkは1プロセスで直列実行し、最初の違反でexit 2する。
warn種別のcheckはstdoutの`hookSpecificOutput.additionalContext`へ警告を載せつつ処理を継続する
（exit 0で終了したフックのstderrはコーディングエージェントへ届かないため）。
遮断は不可逆な操作とデータ破損を生む入力に限定し、後続の編集で復元できる結果は警告する。

統合しているチェック:

任意ツール:

- メインエージェント応答の日本語文字比率が閾値未満の場合の警告 (warn)

AskUserQuestion / ExitPlanMode:

- ユーザーが直接読む質問本文・計画本文の文字化けの警告 (warn)

mcp__plugin_agent-toolkit_agents_server__start / start_explore / start_write / start_shell / send_message / kill / list:

- `send_message`の`prompt`と`send_message`・`kill`の`session_id`の欠落はツール自身が拒否できるため警告 (warn)
- 対象sessionの保存済み`cwd`の欠落は所有を確認できないため遮断 (block)
- 全チェック通過時の強制承認 (auto-approve)

Bash:

- Codexで350行又は16KiBを超える通常ファイルの静的に確定できる全文取得の遮断 (block)
- パターン一致によるプロセス終了（`pkill`・`killall`等）の遮断 (block)
- 未完了の背景タスクが書き込む出力ファイルの読取の警告 (warn)

Skill:

- `agent-toolkit:plan-mode`起動時の前の計画ファイルのパスの消去 (side-effect)

TaskStop:

- 停滞検知完了記録又は自セッション起動記録との対象一致による通過と、それ以外の遮断 (block)

Write / Edit / MultiEdit / apply_patch:

- 文字化け（U+FFFD）検出 (warn)
- `.ps1` / `.ps1.tmpl`へのLF-only書き込み検出 (warn)
- lockfile / 生成物ディレクトリの直接編集 (warn)
- Pythonと計画Markdownの末尾へ混入したツール境界タグ (warn)
- manifestファイルの手編集 (warn)

各チェックの詳細仕様（対象パターン・エラー文言・例外条件）は対応する実装関数のdocstringを参照する。

ホスト差の扱い:

- 編集入力は`_hook_tool_input`が共通の操作記録へ正規化し、検査本体はホストを区別しない
- 非空文字列の`turn_id`をCodex判定の正本とし、payload読込直後に一度だけ判定する
- 大量読取の遮断はCodexだけへ適用し、PowerShellの改行検査はClaudeの`Write`へ限定する
- 警告は1つの`hookSpecificOutput.additionalContext`へ結合し、遮断は最初の違反をexit 2とstderrで返す
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from typing import TYPE_CHECKING


# pylint: disable=wrong-import-position
from agent_toolkit._hooks import (
    background_task_outputs as _background_task_outputs,  # noqa: E402  # pylint: disable=wrong-import-position,import-error
)
from agent_toolkit._hooks import (
    bash_command_parser as _bash_command_parser,  # noqa: E402  # pylint: disable=wrong-import-position,import-error
)
from agent_toolkit._hooks import (
    tool_input as _hook_tool_input,  # noqa: E402  # pylint: disable=wrong-import-position,import-error
)

# pylint: disable-next=wrong-import-position,import-error
from agent_toolkit._hooks.notice import _WARN_TAG, consume_warning_blocks, set_warning_session_id  # noqa: E402

from agent_toolkit._hooks.session_state import read_state  # noqa: E402  # pylint: disable=wrong-import-position,import-error
from agent_toolkit._hooks.pretooluse.warning_context import (  # noqa: E402  # pylint: disable=wrong-import-position,import-error
    format_warning_context,
)
from agent_toolkit._plan.locations import (  # noqa: E402  # pylint: disable=wrong-import-position,import-error
    is_plan_adjunct_file,
    is_plan_component_file,
)

if TYPE_CHECKING:
    from agent_toolkit._hooks.pretooluse.agent_checks import (
        _AGENTS_SERVER_KILL_TOOLS,
        _AGENTS_SERVER_SEND_TOOLS,
        _AGENTS_SERVER_TOOL_NAMES,
        _PLAN_MODE_SKILL_NAMES,
        _check_agents_server_continuation_input,
        _check_task_stop,
        _clear_current_plan_file_path,
        _advance_language_reinjection,
        _handle_language_check,
        _rules_context,
        _record_iss_sidechain_probe,
    )
    from agent_toolkit._hooks.pretooluse.content_checks import (
        _collect_edit_operation_warnings,
        _warn_mojibake,
    )
    from agent_toolkit._hooks.pretooluse.large_reads import (
        check_large_bash_read,
    )
    from agent_toolkit._hooks.pretooluse.notices import (
        _llm_notice,
    )
    from agent_toolkit._hooks.pretooluse.shell_checks import (
        _check_bash_process_kill_by_pattern,
        _warn_git_rev_parse_short_multiple,
    )

_ExecutionSegment = _bash_command_parser.ExecutionSegment
_extract_execution_segments = _bash_command_parser.extract_execution_segments

# U+FFFD（REPLACEMENT CHARACTER）: UTF-8デコード失敗時の代替文字
_REPLACEMENT_CHAR = "\ufffd"


def _is_plan_file_or_adjunct(file_path: str) -> bool:
    """計画ファイル（メイン）・計画ファイル（バグ）の場合に真を返す。"""
    return is_plan_component_file(file_path) or is_plan_adjunct_file(file_path)


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
    # Claude Codeのメインセッションでは一定間隔で日本語の応答指示を文脈の近くへ置き直す。
    if not is_codex and _advance_language_reinjection(payload, session_id):
        pending_notices.append(_llm_notice(_rules_context.RESPONSE_LANGUAGE_NOTICE))

    def emit_json(result: dict) -> None:
        hook_output = result.get("hookSpecificOutput")
        if isinstance(hook_output, dict):
            existing_context = hook_output.get("additionalContext")
            contexts: list[str] = []
            if isinstance(existing_context, str) and existing_context:
                contexts.append(existing_context)
            contexts.extend(pending_notices)
            if contexts:
                hook_output["additionalContext"] = format_warning_context(contexts)
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
        warning_blocks = consume_warning_blocks()
        if warning_blocks:
            if pending_notices:
                print("\n".join(pending_notices), file=sys.stderr)
                pending_notices.clear()
            print("\n".join(warning_blocks), file=sys.stderr)
            return 2
        if code == 2 and pending_notices:
            print("\n".join(pending_notices), file=sys.stderr)
            pending_notices.clear()
        return code

    if tool_name in _USER_FACING_TEXT_TOOL_NAMES:
        return exit_with(_handle_user_facing_text_tool(tool_name, tool_input, emit_json, flush_pending_notices))

    # Skill: plan-mode起動時は前の計画のパスを消去し、UserPromptSubmitが前の計画名を表示し続けないようにする。
    if tool_name == "Skill":
        skill_name = tool_input.get("skill")
        if isinstance(skill_name, str) and skill_name in _PLAN_MODE_SKILL_NAMES:
            _clear_current_plan_file_path(session_id)
        flush_pending_notices()
        return exit_with(0)

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
        return exit_with(0)

    return exit_with(_handle_edit_tool(tool_name, tool_input, cwd, emit_json, flush_pending_notices))


def _handle_agents_server_tool(
    payload: dict,
    tool_name: str,
    tool_input: dict,
    session_id: str,
    emit_json: Callable[[dict], None],
) -> int:
    """agents_serverの開始点・観測点を分離して検査する。"""
    _record_iss_sidechain_probe(session_id, tool_name, payload)
    if tool_name in _AGENTS_SERVER_SEND_TOOLS | _AGENTS_SERVER_KILL_TOOLS:
        continuation = _check_agents_server_continuation_input(session_id, tool_input, tool_name)
        if continuation is True:
            return 2
        if isinstance(continuation, str):
            emit_json(
                {
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "permissionDecision": "allow",
                        "additionalContext": continuation,
                    }
                }
            )
            return 0
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
    """Bashコマンドの遮断と警告を処理する。

    Codexの大量読取の遮断、パターン一致によるプロセス終了の遮断、
    未完了の背景タスクが書き込む出力ファイルの読取の警告を扱う。
    """
    command = tool_input.get("command")
    if not isinstance(command, str):
        flush_warning()
        return 0
    cwd_raw = payload.get("cwd", "")
    cwd = cwd_raw if isinstance(cwd_raw, str) else ""
    large_read_notice = check_large_bash_read(command, cwd, is_codex=is_codex)
    if large_read_notice is not None:
        print(large_read_notice, file=sys.stderr)
        return 2
    if _check_bash_process_kill_by_pattern(command):
        return 2
    warnings: list[str] = []
    rev_parse_warning = _warn_git_rev_parse_short_multiple(command)
    if rev_parse_warning is not None:
        warnings.append(rev_parse_warning)
    transcript_path = payload.get("transcript_path")
    if isinstance(transcript_path, str) and transcript_path:
        state = read_state(session_id)
        recorded_paths = state.get("background_task_output_paths")
        if isinstance(recorded_paths, dict):
            pending_ids = _background_task_outputs.pending_bash_task_ids(transcript_path, session_id)
            pending_paths = {
                path for task_id, path in recorded_paths.items() if task_id in pending_ids and isinstance(path, str)
            }
            if _background_task_outputs.command_reads_path(command, pending_paths):
                warnings.append(
                    _llm_notice(
                        "未完了の背景タスクが書き込む出力ファイルを読み取ろうとしている。\n"
                        "対処: 完了通知を唯一の再開契機とし、独立して実行する工程が無ければターンを終える。",
                        tag=_WARN_TAG,
                        removable_cause=True,
                        escalate_on_repeat=True,
                    )
                )
    if warnings:
        emit_json(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "additionalContext": format_warning_context(warnings),
                }
            }
        )
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
    """質問・計画本文へ文字化け検査を適用し、警告として返す。

    ユーザーへ直接到達する本文はユーザー自身が読んで誤りを指摘できるため、復元できない結果に当たらない。
    遮断すると当該ターンの入力と作業を失わせたうえで同じ確認の再発行を要するため、警告で返す。
    判定の根拠は`agent-toolkit:writing-standards`の`references/claude-hooks.md`
    「遮断・警告フックの成立条件」が定める。
    """
    warning = _warn_mojibake(tool_name, _user_facing_text_fields(tool_name, tool_input))
    if warning is None:
        flush_warning()
    else:
        emit_json(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "additionalContext": format_warning_context([warning]),
                }
            }
        )
    return 0


def _handle_edit_tool(
    tool_name: str,
    tool_input: dict,
    cwd: str,
    emit_json: Callable[[dict], None],
    flush_warning: Callable[[], None],
) -> int:
    """共通編集単位ごとに警告検査を処理する。

    ClaudeのWrite・Edit・MultiEditとCodexの`apply_patch`を`_hook_tool_input`が
    同一の操作記録へ変換するため、検査本体はホストを区別しない。
    複数対象・複数検査の警告は1つの`additionalContext`へ結合する。
    """
    operations = _hook_tool_input.parse_operations(tool_name, tool_input, cwd)
    if operations is None:
        flush_warning()
        return 0
    images: dict[int, _hook_tool_input.MaterializedEdit | None] = {}
    warnings: list[str] = []
    for index, operation in enumerate(operations):
        warnings.extend(_collect_edit_operation_warnings(tool_name, operation, index, images))
    if warnings:
        emit_json(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "additionalContext": format_warning_context(warnings),
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
