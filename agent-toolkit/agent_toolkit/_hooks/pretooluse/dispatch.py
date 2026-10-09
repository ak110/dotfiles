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
- 確認の前に読む2資料（`agent-toolkit:user-confirmation-and-report`）を読まないままメインが呼んだ
  `AskUserQuestion`の警告 (warn、昇格しない)

mcp__plugin_agent-toolkit_agents_server__start / send_message / kill / list:

- `start`の自由本文のmode（`delegate`・`explore`・`write`）の本文が`share/<役割名>.subagent.md`を指す起動の遮断 (block)
- `send_message`の`prompt`と`send_message`・`kill`の`session_id`の欠落はツール自身が拒否できるため警告 (warn)
- 対象sessionの保存済み`cwd`の欠落は所有を確認できないため遮断 (block)
- 全チェック通過時の強制承認 (auto-approve)

Agent / Task:

- `<役割名>.subagent.md`を指す本文が1行目の命令と宣言済みの入力以外の行を含む起動の遮断 (block)

Bash:

- Codexで48KiBを超える通常ファイルの静的に確定できる全文取得の遮断 (block)。通知は`atk read-file`による終端までの読取を示す
- 区切り語を引用しないheredocの本文にあるコマンド置換の遮断 (block)
- パターン一致によるプロセス終了（`pkill`・`killall`等）の遮断 (block)
- atkの出力のパイプとリダイレクト（常駐と追従の表示を除く）と、`atk agents wait`のシェル背景化の遮断 (block)
- 未完了のバックグラウンドタスクが書き込む出力ファイルの読取の警告 (warn)
- Codexでcommitのmessage.mdを読むとき、観測したモデルと推論量の帰属行を先行配送 (warn)
- 操作を起動の契機とするスキル（`agent-toolkit:search`・`agent-toolkit:bugfix`）が未起動の操作の、文脈ごとに1回の警告 (warn)

Grep / Glob:

- `agent-toolkit:search`が未起動のままの検索の、文脈ごとに1回の警告 (warn)

Skill:

- `agent-toolkit:plan-mode`起動時の前の計画ファイルのパスの消去 (side-effect)

TaskStop:

- 停滞検知完了記録または自セッション起動記録との対象一致による通過と、それ以外の遮断 (block)

Write / Edit / MultiEdit / apply_patch:

- 文字化け（U+FFFD）検出 (warn)
- `.ps1` / `.ps1.tmpl`へのLF-only書き込み検出 (warn)
- lockfile / 生成物ディレクトリの直接編集 (warn)
- Pythonと計画Markdownの末尾へ混入したツール境界タグ (warn)
- manifestファイルの手編集 (warn)
- `agent-toolkit:bugfix`が未起動のままの`## 原因分析`の見出し行の記述の、文脈ごとに1回の警告 (warn)

各チェックの詳細仕様（対象パターン・エラー文言・例外条件）は対応する実装関数のdocstringを参照する。

ホスト差の扱い:

- 編集入力は`_hook_tool_input`が共通の操作記録へ正規化し、各判定処理はホストを区別しない
- 非空文字列の`turn_id`からCodexかどうかを判定し、payload読込直後に一度だけ判定する
- 大量読取の遮断はCodexだけへ適用し、PowerShellの改行を確かめる処理はClaudeの`Write`へ限定する
- 警告は1つの`hookSpecificOutput.additionalContext`へ結合し、遮断は最初の違反をexit 2とstderrで返す
"""

from __future__ import annotations

import dataclasses
import json
import os
import pathlib
import sys

from agent_toolkit._common import host_homes as _host_homes
from agent_toolkit._common import response_language_check as _response_language_check
from agent_toolkit._common import transcript as _transcript
from agent_toolkit._common.runtime_identity import RuntimeIdentity, co_author_trailer, identity_observations
from agent_toolkit._common.session_state import read_state
from agent_toolkit._hooks import (
    background_task_outputs as _background_task_outputs,
)
from agent_toolkit._hooks import (
    message_format as _message_format,
)
from agent_toolkit._hooks import rules_context as _rules_context
from agent_toolkit._hooks import (
    tool_input as _hook_tool_input,
)
from agent_toolkit._hooks.host import is_codex_payload
from agent_toolkit._hooks.notice import _WARN_TAG, consume_warning_blocks, set_warning_session_id
from agent_toolkit._hooks.pretooluse.agent_checks import (
    _AGENTS_SERVER_KILL_TOOLS,
    _AGENTS_SERVER_SEND_TOOLS,
    _AGENTS_SERVER_TOOL_NAMES,
    _PLAN_MODE_SKILL_NAMES,
    _advance_language_reinjection,
    _check_agents_server_continuation_input,
    _check_task_stop,
    _clear_current_plan_file_path,
    _handle_language_check,
    _record_iss_sidechain_probe,
)
from agent_toolkit._hooks.pretooluse.confirmation_reads import unread_reference_warning
from agent_toolkit._hooks.pretooluse.content_checks import _collect_edit_operation_warnings, _warn_mojibake
from agent_toolkit._hooks.pretooluse.decision import Decision
from agent_toolkit._hooks.pretooluse.large_reads import bash_read_paths, check_large_bash_read
from agent_toolkit._hooks.pretooluse.notices import _HOOK_ID, _llm_notice
from agent_toolkit._hooks.pretooluse.operation_skills import operation_skill_warnings
from agent_toolkit._hooks.pretooluse.shell_checks import (
    _check_bash_atk_output_loss,
    _check_bash_option_after_terminator,
    _check_bash_process_kill_by_pattern,
    _check_bash_unquoted_heredoc_substitution,
    _git_commit_attribution_error,
    _git_commit_message_format_error,
    _warn_git_rev_parse_short_multiple,
    _warn_windows_drive_letter_path,
)
from agent_toolkit._hooks.pretooluse.task_document_launch import AGENT_TOOL_NAMES, check_task_document_launch
from agent_toolkit._hooks.pretooluse.warning_context import (
    format_warning_context,
)


def _settings_commit_attribution(path: pathlib.Path) -> tuple[bool, str | None]:
    """設定ファイルが`attribution.commit`を文字列として定義するかと、その値を返す。"""
    try:
        settings = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        return False, None
    if not isinstance(settings, dict):
        return False, None
    attribution = settings.get("attribution")
    if not isinstance(attribution, dict) or not isinstance(attribution.get("commit"), str):
        return False, None
    return True, attribution["commit"]


def _claude_commit_attribution_disabled(cwd: str) -> bool:
    """フックから観測できる設定範囲で、最上位の明示的な空文字設定を判定する。"""
    config_dir = _host_homes.claude_config_dir()
    project_raw = os.environ.get("CLAUDE_PROJECT_DIR") or cwd
    paths = [config_dir / "settings.json"]
    if project_raw:
        project_dir = pathlib.Path(project_raw)
        paths.extend((project_dir / ".claude" / "settings.json", project_dir / ".claude" / "settings.local.json"))
    effective: str | None = None
    defined = False
    for path in paths:
        current_defined, current = _settings_commit_attribution(path)
        if current_defined:
            defined = True
            effective = current
    return defined and effective == ""


_USER_FACING_TEXT_TOOL_NAMES: frozenset[str] = frozenset({"AskUserQuestion", "ExitPlanMode"})
_SEARCH_TOOL_NAMES: frozenset[str] = frozenset({"Grep", "Glob"})


def main(payload_text: str) -> int:
    """エントリポイント。

    exit code契約:

    - exit 0: 通過（違反なし / スキップ対象ツール / 想定外入力 / warnのみ）
    - exit 2: block違反検出（stderrに理由を出力）

    予期せぬ例外は0にフォールバックする（pluginのhookが破損して編集できなくなることを避けるため）。
    各判定は結果を`Decision`で返し、標準出力・標準エラーへの書き込みは本関数と`_emit`だけが行う。
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
    # ホスト判定はpayload読込直後に一度だけ行い、以降の判定処理の選択と入力アダプターへ同じ値を渡す。
    is_codex = is_codex_payload(payload)

    # 直前メインエージェント応答の日本語比率警告（任意ツール）。
    # 他warn系checkがJSONを返す場合はadditionalContextの末尾へ追記し、それ以外は単独でJSON出力する。
    # transcriptを安定インターフェースとして扱えないCodexでは実行しない。
    language_warning_body = None if is_codex else _handle_language_check(payload, session_id)

    # 出力を保留する通知の一覧。exit 0のstderrはコーディングエージェントへ届かないため、
    # 通常はstdoutの`hookSpecificOutput.additionalContext`へ結合して出力する。
    # 遮断で終える場合はJSONを出力しないため、`exit_with`がstderrへ出力して消費する。
    pending_notices: list[str] = []
    if language_warning_body is not None:
        pending_notices.append(
            _llm_notice(
                language_warning_body,
                tag=_WARN_TAG,
                fix=_response_language_check.WARNING_FIX,
                removable_cause=True,
            )
        )
    # Claude Codeのメインセッションでは一定間隔で日本語の応答指示を文脈の近くへ置き直す。
    if not is_codex and _advance_language_reinjection(payload, session_id):
        pending_notices.append(_message_format.llm_notice(_rules_context.RESPONSE_LANGUAGE_REINJECTION_NOTICE, _HOOK_ID))
    return _emit(_decide(payload, tool_name, tool_input, session_id, is_codex=is_codex), pending_notices)


def _decide(payload: dict, tool_name: str, tool_input: dict, session_id: str, *, is_codex: bool) -> Decision:
    """ツールの種類ごとの判定を実行し、その結果を返す。"""
    if tool_name in _USER_FACING_TEXT_TOOL_NAMES:
        return _decide_user_facing_text_tool(payload, tool_name, tool_input)
    # Skill: plan-mode起動時は前の計画のパスを消去し、UserPromptSubmitが前の計画名を表示し続けないようにする。
    if tool_name == "Skill":
        skill_name = tool_input.get("skill")
        if isinstance(skill_name, str) and skill_name in _PLAN_MODE_SKILL_NAMES:
            _clear_current_plan_file_path(session_id)
        return Decision()
    if tool_name in _AGENTS_SERVER_TOOL_NAMES:
        return _decide_agents_server_tool(payload, tool_name, tool_input, session_id)
    if tool_name in AGENT_TOOL_NAMES:
        return Decision(block=check_task_document_launch(tool_name, tool_input))
    if tool_name == "Bash":
        return _decide_bash_tool(payload, tool_input, session_id, is_codex=is_codex)
    if tool_name == "TaskStop":
        return Decision(block=_check_task_stop(session_id, tool_input))
    skill_warnings = tuple(operation_skill_warnings(payload, tool_name, tool_input, session_id, is_codex=is_codex))
    if tool_name in _SEARCH_TOOL_NAMES:
        return Decision(notices=skill_warnings)
    cwd_raw = payload.get("cwd", "")
    cwd = cwd_raw if isinstance(cwd_raw, str) else ""
    return dataclasses.replace(_decide_edit_tool(tool_name, tool_input, cwd), notices=skill_warnings)


def _emit(decision: Decision, pending_notices: list[str]) -> int:
    """判定の結果と保留の通知を標準出力・標準エラーへ書き、終了コードを返す。"""
    if decision.block is not None:
        print(decision.block, file=sys.stderr)
        return exit_with(2, pending_notices)
    pending_notices.extend(decision.notices)
    hook_output: dict[str, object] = {"hookEventName": "PreToolUse"}
    if decision.allow:
        hook_output["permissionDecision"] = "allow"
    contexts = [decision.context] if decision.context else []
    contexts.extend(pending_notices)
    pending_notices.clear()
    if contexts:
        hook_output["additionalContext"] = format_warning_context(contexts)
    if decision.allow or contexts:
        print(json.dumps({"hookSpecificOutput": hook_output}, ensure_ascii=False))
    return exit_with(0, pending_notices)


def exit_with(code: int, pending_notices: list[str]) -> int:
    """終了コードを返す直前に、遮断時だけ保留通知をstderrへ出力して消費する。

    exit 2ではstdoutの構造化JSONが評価されず、保留通知を`additionalContext`で渡せない。
    出力しないまま返すと、判定処理側のカウンタと最終パスだけが更新されるため、
    同じ入力を再試行しても通知が再生成されず失われる。
    exit 2のstderrはコーディングエージェントへ届くため、遮断理由と同じ出力先へ送る。
    反復した警告が遮断へ昇格した場合（`consume_warning_blocks`）も同じ出力先へ送り、exit 2で終える。
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


def _decide_agents_server_tool(payload: dict, tool_name: str, tool_input: dict, session_id: str) -> Decision:
    """agents_serverの開始点と観測点の条件をそれぞれ判定する。"""
    _record_iss_sidechain_probe(session_id, tool_name, payload)
    launch_block = check_task_document_launch(tool_name, tool_input)
    if launch_block is not None:
        return Decision(block=launch_block)
    if tool_name in _AGENTS_SERVER_SEND_TOOLS | _AGENTS_SERVER_KILL_TOOLS:
        return _check_agents_server_continuation_input(session_id, tool_input, tool_name)
    return Decision(allow=True)


def _decide_bash_tool(payload: dict, tool_input: dict, session_id: str, *, is_codex: bool) -> Decision:
    """Bashコマンドの遮断と警告を判定する。

    Codexの大量読取の遮断、引用符なしheredoc本文の置換の遮断、パターン一致によるプロセス終了の遮断、
    未起動のスキルの操作の警告、未完了のバックグラウンドタスクが書き込む出力ファイルの読取の警告を扱う。
    遮断の判定は警告の判定より前に行い、遮断した呼び出しでは警告の記録（文脈ごとに1回の警告など）を進めない。
    """
    command = tool_input.get("command")
    if not isinstance(command, str):
        return Decision()
    cwd_raw = payload.get("cwd", "")
    cwd = cwd_raw if isinstance(cwd_raw, str) else ""
    for block in (
        lambda: check_large_bash_read(command, cwd, is_codex=is_codex),
        lambda: _check_bash_unquoted_heredoc_substitution(command),
        lambda: _check_bash_process_kill_by_pattern(command),
        lambda: _check_bash_option_after_terminator(command),
        lambda: _check_bash_atk_output_loss(command),
        lambda: _warn_git_rev_parse_short_multiple(command),
        lambda: _git_commit_message_format_error(command, cwd=cwd),
        lambda: _git_commit_attribution_error(
            command,
            _hook_observed_identity(payload, is_codex=is_codex),
            attribution_disabled=not is_codex and _claude_commit_attribution_disabled(cwd),
            cwd=cwd,
        ),
    ):
        message = block()
        if message is not None:
            return Decision(block=message)
    warnings: list[str] = operation_skill_warnings(payload, "Bash", tool_input, session_id, is_codex=is_codex)
    drive_letter_path_warning = _warn_windows_drive_letter_path(command, is_codex=is_codex)
    if drive_letter_path_warning is not None:
        warnings.append(drive_letter_path_warning)
    transcript_path = payload.get("transcript_path")
    # 出力パスはtranscriptの起動記録だけが持つ。所有記録が空のセッションではtranscriptを読まない。
    if isinstance(transcript_path, str) and transcript_path and read_state(session_id).get("background_task_ids"):
        pending_paths = _background_task_outputs.pending_task_output_paths(transcript_path, session_id)
        if _background_task_outputs.command_reads_path(command, pending_paths):
            warnings.append(
                _llm_notice(
                    "未完了のバックグラウンドタスクが書き込む出力ファイルを読み取ろうとしている。",
                    tag=_WARN_TAG,
                    fix=(
                        "読み取った内容は途中経過であり、完了通知を受けた後に同じ出力ファイルを読み直してから結果として使う。"
                        "完了通知を唯一の再開契機とし、独立して実行する工程が無ければターンを終える。"
                    ),
                    removable_cause=True,
                    escalate_on_repeat=True,
                )
            )
    if is_codex and _reads_commit_message_rules(command, cwd):
        identity = _hook_observed_identity(payload, is_codex=True)
        if identity is not None and identity.source == "observed":
            warnings.append(
                _message_format.llm_notice(
                    "読取時点に観測したモデルと推論量の帰属行: "
                    + co_author_trailer(identity)
                    + "。明示指定と無効化の方針を先に確認する。通常commit直前にも現在の観測を確認する。",
                    _HOOK_ID,
                )
            )
    return Decision(context=format_warning_context(warnings) if warnings else None)


def _reads_commit_message_rules(command: str, cwd: str) -> bool:
    """静的に解決できる読取のファイル引数だけで帰属資料を識別する。"""
    for paths in bash_read_paths(command, cwd, include_partial=True):
        for path in paths:
            if path.as_posix().endswith("/skills/commit/references/message.md") and path.is_file():
                return True
    return False


def _hook_observed_identity(payload: dict, *, is_codex: bool) -> RuntimeIdentity | None:
    """Hook payloadとそのturnの実行主体の記録から、このturnの観測identityを返す。

    Claude Codeの推論量はhook入力の`effort.level`（ホストがturnへ適用した値）、モデルは記録の最後の観測値から取る。
    初出順に重複を除いた一覧の末尾は、turnの途中でモデルを切り替えて戻した記録で古い値を選ぶため使わない。
    hook入力が`agent_id`を持つ場合は、`transcript_path`がメインの記録を指すため、そのsubagentの記録
    （`<transcript_pathのディレクトリ>/<session_id>/subagents/agent-<agent_id>.jsonl`）を読む。
    記録を一意に解決できない場合は判定しない。Codexはhook入力の`model`と`reasoning_effort`を優先し、
    両方が無い場合は記録の`turn_context`の最後の観測値を使う。
    """
    runtime = "codex" if is_codex else "claude"
    model = payload.get("model")
    effort_input = payload.get("effort")
    effort = effort_input.get("level") if isinstance(effort_input, dict) else effort_input or payload.get("reasoning_effort")
    if isinstance(model, str) and model and isinstance(effort, str) and effort:
        return RuntimeIdentity(runtime, model, effort, "observed")
    record_path = _hook_record_path(payload)
    if record_path is None:
        return None
    lines = _transcript.read_transcript_lines(record_path)
    if lines is None:
        return None
    entries: list[dict] = []
    for raw in lines:
        if not raw.strip():
            continue
        try:
            entry = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(entry, dict):
            entries.append(entry)
    observations = identity_observations(entries, runtime)
    if not observations:
        return None
    latest = observations[-1][1]
    if not is_codex and isinstance(effort, str) and effort:
        return RuntimeIdentity(runtime, latest.model, effort, "observed")
    return latest


def _hook_record_path(payload: dict) -> str | None:
    """hook入力を発した実行主体の記録のパスを返す。subagentの記録を一意に解決できない場合は`None`を返す。"""
    transcript_path = payload.get("transcript_path")
    if not isinstance(transcript_path, str) or not transcript_path:
        return None
    agent_id = payload.get("agent_id")
    if agent_id is None:
        return transcript_path
    session_id = payload.get("session_id")
    if not isinstance(agent_id, str) or not agent_id or not isinstance(session_id, str) or not session_id:
        return None
    subagent_path = pathlib.Path(transcript_path).parent / session_id / "subagents" / f"agent-{agent_id}.jsonl"
    return str(subagent_path) if subagent_path.is_file() else None


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


def _decide_user_facing_text_tool(payload: dict, tool_name: str, tool_input: dict) -> Decision:
    """質問・計画本文の文字化けと、確認の前に読む資料の未読を確かめ、警告として返す。

    ユーザーへ直接到達する本文はユーザー自身が読んで誤りを指摘できるため、復元できない結果に当たらない。
    遮断するとそのターンの入力と作業を失い、同じ確認を再発行する必要があるため、警告で返す。
    判定の根拠は`agent-toolkit:writing-standards`の`references/claude-hooks-block-warn.md`
    「遮断と警告の選択」が定める。2つの判定は互いの結果に依存せず、成立した警告を同じ追加コンテキストへ並べる。
    """
    warnings = [
        warning
        for warning in (
            _warn_mojibake(tool_name, _user_facing_text_fields(tool_name, tool_input)),
            unread_reference_warning(payload, tool_name),
        )
        if warning is not None
    ]
    return Decision(context=format_warning_context(warnings) if warnings else None)


def _decide_edit_tool(tool_name: str, tool_input: dict, cwd: str) -> Decision:
    """共通編集単位ごとに警告が必要か判定する。

    ClaudeのWrite・Edit・MultiEditとCodexの`apply_patch`を`_hook_tool_input`が
    同一の操作記録へ変換するため、各判定処理はホストを区別しない。
    複数の対象と判定処理が返す警告は1つの`additionalContext`へ結合する。
    """
    operations = _hook_tool_input.parse_operations(tool_name, tool_input, cwd)
    if operations is None:
        return Decision()
    images: dict[int, _hook_tool_input.MaterializedEdit | None] = {}
    warnings: list[str] = []
    for index, operation in enumerate(operations):
        warnings.extend(_collect_edit_operation_warnings(tool_name, operation, index, images))
    return Decision(context=format_warning_context(warnings) if warnings else None)
