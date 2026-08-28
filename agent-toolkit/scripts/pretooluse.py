r"""Claude Code plugin agent-toolkit: PreToolUse統合フック。

任意ツールの実行前に以下のチェックを順に実行する。
block系checkは1プロセスで直列実行し、最初の違反でexit 2する。
warn種別のcheckはstdoutの`hookSpecificOutput.additionalContext`へ警告を載せつつ処理を継続する
（exit 0で終了したフックのstderrはコーディングエージェントへ届かないため）。
auto-fix種別のcheckは`updatedInput`でツール入力を自動書き換えする。
関連チェック項目は初回で一括開示する（反復サイクル防止のため）。

統合しているチェック:

任意ツール:

- メインエージェント応答の日本語文字比率が閾値未満の場合の警告/ブロック (warn/block)
- plan-modeスキル未起動のままのplan file編集（Write/Edit/MultiEdit）の警告 (warn)
- plan-modeスキル起動後、計画ファイル未作成のままagent-toolkit配下の直接編集連続のブロック (warn/block)

固定見出し（新形式と旧形式の互換別名）と固定表の構造、素材表・要求表・素材参照、
計画メタ情報の4項目と記法、計画単位のエージェント判断表（5項目）を含む
フェンス整合、参照実在は
`agent-toolkit/skills/plan-mode/scripts/check_plan_file.py`が担うため
本フックでは扱わない。

mcp__plugin_agent-toolkit_agents_server__start / send_message / kill:

- 委譲先へ渡す絶対`cwd`と`send_message`・`kill`のprompt/sessionの検査 (block)
- 全チェック通過時の強制承認 (auto-approve)

wait:

- 既存sessionの観測として通過 (pass-through)

Bash:

- 長い固定`sleep`の後に別コマンドを連結する前景待機の検出 (warn/block)
- 高容量のユーザー領域を無限定に再帰検索する実行位置の検出 (warn)
- パターン一致によるプロセス終了（`pkill`・`killall`等）の遮断 (block)
- git amend / rebase直前に`git log`未確認のブロック (block)
- git push実行時のamend後dirty状態のブロック (block)
- 非Pythonプロジェクトでの`uv run python <path>`形式起動のブロック (block)
- `git commit`未検証警告 (warn)
- `agent-toolkit/`配下のコミット時のversion bump漏れ警告 (warn)
- `git log --decorate`の自動付与 (auto-fix)
- `codex exec`の未決事項念押し (warn)
- 一括ステージ実行時の自セッション編集対象外ファイル警告 (warn)

Skill:

- `agent-toolkit:plan-mode`起動時の計画単位の状態リセット (side-effect)

Agent / Task:

- `plan-impl-executor`起動時、起動プロンプトが指す実在計画パスの記録 (side-effect)
- `_TRACKED_SUBAGENT_TYPES`対象種別起動時の`_process_loop_log`への起動時刻記録 (side-effect)
- 定義済み既定モデルを持ちoverride運用の定めが無いサブエージェントへの`model`引数指定のブロック (block)

TaskStop:

- 初回呼び出しのブロックと、直近ブロックから一定時間内の再実行の通過 (block)

Write / Edit / MultiEdit / apply_patch:

- 文字化け（U+FFFD）検出 (block)
- `.ps1` / `.ps1.tmpl`へのLF-only書き込み検出 (block)
- lockfile / 生成物ディレクトリの直接編集 (block)
- シークレット / 鍵ファイルの直接編集 (block)
- manifestファイルの手編集 (warn)
- ホームディレクトリの絶対パス混入 (warn)
- 口語的な日本語表現の混入 (warn)
- 「Xを根拠にYしない」「Xを理由にYしない」形式のメタ規範文言の増加 (warn)
- .md規範文書のWrite/Edit/MultiEditでfrontmatter同期注記の本体該当語句の実在検証warn (warn)
- 日本語を含む書き込み文字列へのハングル・キリル文字の混入 (block)
- .md規範文書の本文中にある他ファイルの節参照の実在検証 (warn)

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

import dataclasses
import datetime
import json
import pathlib
import re
import shlex
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Sequence

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import _git_status  # noqa: E402  # pylint: disable=wrong-import-position,import-error
import _hook_tool_input  # noqa: E402  # pylint: disable=wrong-import-position,import-error
import _plan_format  # noqa: E402  # pylint: disable=wrong-import-position,import-error
import _process_loop_log  # noqa: E402  # pylint: disable=wrong-import-position,import-error
import _response_language_check  # noqa: E402  # pylint: disable=wrong-import-position,import-error
import _scratchpad_path  # noqa: E402  # pylint: disable=wrong-import-position,import-error
from _bash_command_parser import (  # noqa: E402  # pylint: disable=wrong-import-position,import-error
    CwdResolution,
    GitEvent,
    extract_git_events,
    resolve_cwd_change,
    split_bash_segments,
)
from _file_lock import (  # noqa: E402  # pylint: disable=wrong-import-position,import-error
    locked_rotate_and_append as _locked_rotate_and_append,
)

# pylint: disable-next=wrong-import-position,import-error
from _hook_notice import block_formatter as _block_notice_formatter  # noqa: E402

# pylint: disable-next=wrong-import-position,import-error
from _hook_notice import formatter as _notice_formatter  # noqa: E402
from _plan_file import (  # noqa: E402  # pylint: disable=wrong-import-position,import-error
    is_plan_adjunct_file,
    is_plan_component_file,
)
from _session_state import read_state, update_state  # noqa: E402  # pylint: disable=wrong-import-position,import-error

# pylint: disable=wrong-import-position,import-error
from _tracked_subagent_types import TRACKED_SUBAGENT_TYPES as _TRACKED_SUBAGENT_TYPES  # noqa: E402

# pylint: enable=wrong-import-position,import-error
from pyfltr.colloquial import check as _colloquial_check  # noqa: E402  # pylint: disable=wrong-import-position

# U+FFFD（REPLACEMENT CHARACTER）: UTF-8デコード失敗時の代替文字
_REPLACEMENT_CHAR = "\ufffd"


def _is_plan_file_or_adjunct(file_path: str) -> bool:
    """計画本体・実装詳細またはバグ調査付属ファイルの場合に真を返す。"""
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

# このスクリプトの hook 識別子。
_HOOK_ID = "agent-toolkit/pretooluse"


_llm_notice = _notice_formatter(_HOOK_ID)
_block_notice = _block_notice_formatter(_HOOK_ID)


def _language_notice(body: str) -> str:
    """言語警告専用の整形ヘルパー。

    共通サフィックスの関連性評価を促す英語文が英語化を助長し
    警告効果を弱めるため、プレフィックスのみ付与してサフィックスを省く。
    """
    return f"[auto-generated: {_HOOK_ID}][warn] {body}"


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
    cwd_raw = payload.get("cwd", "")
    cwd = cwd_raw if isinstance(cwd_raw, str) else ""
    # ホスト判定はpayload読込直後に一度だけ行い、以降の検査選択と入力アダプターへ同じ値を渡す。
    is_codex = _hook_tool_input.is_codex_payload(payload)

    # 直前メインエージェント応答の日本語比率警告（任意ツール）。
    # 他warn系checkがJSONを返す場合はadditionalContextの末尾へ追記し、それ以外は単独でJSON出力する。
    # transcriptを安定インターフェースとして扱えないCodexでは実行しない。
    exit_code, language_warning_body = (None, None) if is_codex else _handle_language_check(payload, session_id)
    if exit_code == 2:
        return 2

    # 出力を保留する通知の一覧。exit 0のstderrはコーディングエージェントへ届かないため、
    # 通常はstdoutの`hookSpecificOutput.additionalContext`へ結合して出力する。
    # 遮断で終える場合はJSONを出力しないため、`exit_with`がstderrへ出力して消費する。
    pending_notices: list[str] = []
    if language_warning_body is not None:
        pending_notices.append(_language_notice(language_warning_body))

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

    # plan-modeスキル起動後、計画ファイル未作成のままagent-toolkit配下の直接編集連続をブロック
    blocked, direct_edit_notice = _check_direct_agent_toolkit_edits_after_plan_mode(tool_name, tool_input, session_id)
    if blocked:
        return exit_with(2)
    if direct_edit_notice is not None:
        pending_notices.append(direct_edit_notice)

    # plan file編集前の必須リファレンス未読の場合は警告（降格）

    # 編集中はパス契約だけを補助し、意味と構造の検査は確定前の計画検査とレビューへ委ねる。

    if tool_name == "ExitPlanMode":
        flush_pending_notices()
        return 0

    # Skill: plan-mode起動時は計画単位の状態をリセットする。
    if tool_name == "Skill":
        skill_name = tool_input.get("skill")
        if isinstance(skill_name, str) and skill_name in _PLAN_MODE_SKILL_NAMES:
            _reset_plan_mode_state(session_id)
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
        if _check_task_stop(session_id):
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

    # Readは変更を伴わないため、個別の事前検査を行わない。
    if tool_name == "Read":
        flush_pending_notices()
        return 0

    if tool_name in ("Agent", "Task"):
        return exit_with(_handle_agent_tool(tool_input, flush_pending_notices))

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
    sleep_poll_result = _check_bash_sleep_poll_pattern(command, session_id, bool(tool_input.get("run_in_background")))
    if sleep_poll_result == "block":
        return 2
    if sleep_poll_result is not None:
        warnings.append(sleep_poll_result)
    if (
        (not is_codex and _check_bash_amend_rebase_without_log(command, session_id, cwd))
        or (not is_codex and _check_bash_git_push_after_amend_with_dirty_status(command, session_id, cwd))
        or _check_bash_uv_run_python(command, cwd)
        or _check_bash_process_kill_by_pattern(command)
    ):
        return 2
    for warning in (
        _check_bash_bulk_stage_with_unedited_files(command, session_id, cwd),
        _check_bash_output_truncation(command),
        _check_bash_output_status_after_truncation(command),
        _check_bash_recursive_home_search(command),
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
    if warnings:
        emit_json({"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": "\n".join(warnings)}})
    else:
        flush_warning()
    return 0


def _handle_agent_tool(
    tool_input: dict,
    flush_warning: Callable[[], None],
) -> int:
    """Agent・Task起動の委譲契約と観測ログを処理する。"""
    subagent_type = tool_input.get("subagent_type")
    if isinstance(subagent_type, str) and _check_subagent_model_override(subagent_type, tool_input):
        return 2
    if isinstance(subagent_type, str) and subagent_type in _TRACKED_SUBAGENT_TYPES:
        _process_loop_log.append("subagent_start", type=subagent_type)
    flush_warning()
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


def _check_edit_operation_blocks(
    tool_name: str,
    operation: _hook_tool_input.EditOperation,
) -> bool:
    """1操作分の遮断検査を実行する。"""
    fields = [(fragment.label, fragment.after) for fragment in operation.fragments]
    display_path = operation.display_path
    if (
        _check_mojibake(tool_name, fields)
        or _check_foreign_script_mixin(tool_name, fields)
        # PowerShellの改行要件はpatch断片のLF表現から判定できないため、Claudeの`Write`だけへ適用する。
        or (tool_name == "Write" and _is_ps1(display_path) and _check_ps1_eol(tool_name, fields, display_path))
    ):
        return True
    return any(_check_lockfiles(tool_name, path) or _check_secrets(tool_name, path) for path in operation.display_paths)


def _collect_edit_operation_warnings(
    tool_name: str,
    operation: _hook_tool_input.EditOperation,
    index: int,
    images: dict[int, _hook_tool_input.MaterializedEdit | None],
    *,
    is_codex: bool,
) -> list[str]:
    """1操作分の警告本文を順に集める。"""
    fields = [(fragment.label, fragment.after) for fragment in operation.fragments]
    display_path = operation.display_path
    warnings = [
        warning
        for warning in (
            _check_manifest(tool_name, display_path),
            _check_home_path(tool_name, fields, display_path),
            _check_colloquial(tool_name, fields, operation.path),
            _check_style_negation(tool_name, operation, display_path),
        )
        if warning is not None
    ]
    if is_codex:
        # 同一patch内で追加・移動する参照先を実ファイルだけで解決できないため、
        # 外部ファイル解決を伴う2検査はClaude入力へ限定する。
        return warnings
    image = _materialize_cached(operation, index, images)
    content = image.after_image if image is not None else None
    if content is None:
        return warnings
    warnings.extend(
        warning for warning in (_check_body_section_reference_exists(tool_name, content, display_path),) if warning is not None
    )
    return warnings


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
        hook_specific = {"hookEventName": "PreToolUse"}
        result["hookSpecificOutput"] = hook_specific
    existing = hook_specific.get("additionalContext")
    if isinstance(existing, str) and existing:
        hook_specific["additionalContext"] = f"{existing}\n\n{suffix}"
    else:
        hook_specific["additionalContext"] = suffix


def _check_foreign_script_mixin(tool_name: str, fields: list[tuple[str, str]]) -> bool:
    """日本語を含む文字列へのハングル・キリル文字の混入を検出したらTrueを返す。

    日本語（ひらがな・カタカナ・漢字）を含まない文字列は対象外とする。
    多言語の文字列を意図的に扱う場面での誤検出を避けるためである。
    文脈の提示には`ascii()`を用いる。`repr()`は非ASCII文字をそのまま出力するため、
    メッセージ自体が検出対象文字を含むことになる。
    """
    for field, value in fields:
        if _JAPANESE_SCRIPT_RE.search(value) is None:
            continue
        match = _FOREIGN_SCRIPT_RE.search(value)
        if match is None:
            continue
        start = max(0, match.start() - 10)
        end = min(len(value), match.end() + 10)
        print(
            _block_notice(
                f"blocked: non-Japanese script (Hangul/Cyrillic) mixed into Japanese text"
                f" in {tool_name}.{field}. Context: {ascii(value[start:end])}.",
                fix="Replace it with the intended Japanese characters.",
            ),
            file=sys.stderr,
        )
        return True
    return False


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
            _block_notice(
                f"blocked: U+FFFD (mojibake) detected in {tool_name}.{field}. Context: {sample!r}",
                fix="Replace the U+FFFD character with the intended character and retry.",
            ),
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
            _block_notice(
                f"blocked: LF-only content detected in {tool_name}.{field}."
                f" PowerShell 5.1 cannot parse .ps1 files with LF line endings; CRLF is required."
                f" Target: {file_path}",
                fix=(
                    "Use the Edit tool for existing files (it preserves CRLF transparently)."
                    " For new files, write via Bash with a UTF-8 BOM and CRLF line endings"
                    " (e.g., printf '\\xEF\\xBB\\xBF' > file.ps1 && ... | sed 's/$/\\r/' >> file.ps1)."
                ),
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
            fix = (
                "Do not edit this path; regenerate it with the package manager instead."
                if label in {".venv/", "node_modules/"}
                else hint
            )
            print(
                _block_notice(
                    f"blocked: direct edit of {label} is prohibited by {tool_name}. Target: {file_path}",
                    fix=fix,
                ),
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

# `.env`・`.env.<接尾辞>`だけを対象とする案内付加用のパターン。
# 遮断対象と戻り値は`_SECRETS_PATTERN`が決めるため、本パターンは案内の有無だけを分ける。
_ENV_FILE_PATTERN = re.compile(r"(^|/)\.env(\..+)?$")

# `.env`系の遮断時だけ添える代替経路の案内。
# 対象を`.env`系へ限定するのは、鍵・証明書へBash経由の改変経路を案内しないためである。
_ENV_FILE_GUIDANCE = (
    " To make a git worktree runnable, copy the original with `cp` via Bash."
    " To add, change, or remove a value for a quick check, append or edit lines via Bash"
    " (`echo ... >>`, `sed -i`) instead of rewriting the file through an edit tool."
)


def _check_secrets(tool_name: str, file_path: str) -> bool:
    """シークレット / 鍵ファイルへの直接編集を検出した場合に真を返す。

    遮断対象が`.env`・`.env.<接尾辞>`の場合だけ、遮断理由の直後へ
    `cp`による複製とBash経由の値操作という代替経路の案内を添える。
    鍵・証明書などの他の対象では遮断理由だけを表示する。
    """
    if not file_path:
        return False
    normalized = file_path.replace("\\", "/")
    if normalized.endswith(_SECRETS_EXEMPT_SUFFIXES):
        return False
    if _SECRETS_PATTERN.search(normalized):
        guidance = (
            _ENV_FILE_GUIDANCE.strip()
            if _ENV_FILE_PATTERN.search(normalized)
            else "Do not edit key or certificate files; abandon this edit."
        )
        print(
            _block_notice(
                f"blocked: direct edit of secret / key files is prohibited by {tool_name}."
                f" Accidental edits can cause service outages or data leaks. Target: {file_path}",
                fix=guidance,
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


def _check_manifest(tool_name: str, file_path: str) -> str | None:
    """manifest手編集を検出したら警告本文を返す（warnのみ、exit codeは変えない）。"""
    if not file_path:
        return None
    normalized = file_path.replace("\\", "/")
    for label, pattern, hint in _MANIFEST_RULES:
        if pattern.search(normalized):
            return _llm_notice(f"editing {label} via {tool_name}. {hint}", tag="warn")
    return None


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


def _unmanaged_path(resolved_path: pathlib.Path) -> bool | None:
    """対象パスの最近接既存親からルートまでのGit管理マーカーを調べる。"""
    try:
        existing_parent = resolved_path if resolved_path.is_dir() else resolved_path.parent
        while not existing_parent.exists():
            existing_parent = existing_parent.parent
        for parent in (existing_parent, *existing_parent.parents):
            try:
                (parent / ".git").lstat()
            except FileNotFoundError:
                continue
            except OSError:
                return None
            return False
        return True
    except (OSError, ValueError):
        return None


def _check_home_path(tool_name: str, fields: list[tuple[str, str]], file_path: str) -> str | None:
    """ホームディレクトリの絶対パス混入を検出したら警告本文を返す。

    リポジトリ管理ファイルに`/home/user/...`のような環境依存パスが書き込まれると
    他環境での再現性が失われるため警告する。警告のみでeditは継続（warn）。
    Git管理外の作業文書であり、正確な絶対パスを記録する計画ファイルは対象外とする。
    対象パスの最近接既存親からルートまでにGit管理マーカーがないと確定できた文書は対象外とする。
    マーカーの確認不能時は既存検査を継続する。
    """
    if _is_plan_file_or_adjunct(file_path) or _is_claude_job_file(file_path):
        return None

    try:
        resolved_path = pathlib.Path(file_path).resolve()
        if _unmanaged_path(resolved_path) is True:
            return None
    except (OSError, ValueError):
        pass

    home_str = str(pathlib.Path.home())
    # ルートなど極端に短いパスは誤検出を避けてスキップ。
    if len(home_str) < 3:
        return None

    normalized_path = file_path.replace("\\", "/")
    if normalized_path.endswith(_HOME_PATH_SKIP_SUFFIXES):
        return None
    if normalized_path.endswith("/CLAUDE.local.md") or normalized_path == "CLAUDE.local.md":
        return None
    if normalized_path.endswith("/.claude/settings.local.json"):
        return None

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
            return _llm_notice(
                f"home directory absolute path ({home}) detected in {tool_name}.{field}."
                f" In version-controlled files, use `~`, `$HOME`, or `pathlib.Path.home()`"
                f" instead to avoid environment-dependent paths."
                f" Context: {sample!r}",
                tag="warn",
            )
    return None


# --- 口語表現混入check (warn) ---

# モジュールロード時に1回だけコンパイルする。
# 検出語そのものをコーディングエージェントのコンテキストへ持ち込まないよう、
# 本ファイルからパターンの実体を文字列で参照しない。
_COLLOQUIAL_DENY_PATTERNS = _colloquial_check.load_patterns(_colloquial_check.DENY_PATH)
_COLLOQUIAL_ALLOW_PATTERNS = _colloquial_check.load_patterns(_colloquial_check.ALLOW_PATH)

_COLLOQUIAL_MAX_LISTED_MATCHES = 5
"""口語表現検査の通知へ列挙する一致位置の上限。超過分は総件数だけを示す。"""
_MANAGED_TEMP_MARKER = ".agent-toolkit-managed-temp.json"


def _is_in_managed_temp(file_path: str) -> bool:
    """Git作業ツリー境界より内側に管理対象一時領域のマーカーがある場合に真を返す。"""
    try:
        current = pathlib.Path(file_path).expanduser().resolve(strict=False)
        current = current if current.is_dir() else current.parent
        for directory in (current, *current.parents):
            if (directory / _MANAGED_TEMP_MARKER).is_file():
                return True
            if (directory / ".git").exists():
                return False
        return False
    except (OSError, ValueError):
        return False


def _check_colloquial(tool_name: str, fields: list[tuple[str, str]], file_path: str) -> str | None:
    """口語的な日本語表現の混入を検出して警告本文を返す（warn）。

    検出語・行抜粋・置換候補は出力せず、総件数と先頭`_COLLOQUIAL_MAX_LISTED_MATCHES`件までの
    位置（行・列）だけを示す（コーディングエージェントのコンテキスト汚染防止）。
    上限を超える一致は総件数だけで示す。
    allowlistに一致する部分を先に除去してからdenylistを適用し、
    複合動詞・複合名詞などの標準用語が誤検出されることを抑える。
    """
    # 計画ファイルは起草中の素材に口語表現が含まれることがあり、専用の計画検査と
    # writing-standardsの除外規定が適用されるため、この警告だけを対象外とする。
    if _is_plan_file_or_adjunct(file_path) or _is_in_managed_temp(file_path):
        return None
    for field, value in fields:
        if not value:
            continue
        hits = _colloquial_check.scan_text(value, _COLLOQUIAL_DENY_PATTERNS, _COLLOQUIAL_ALLOW_PATTERNS)
        if hits:
            listed = "; ".join(
                f"line {line_no}, column {column}" for line_no, column, *_ in hits[:_COLLOQUIAL_MAX_LISTED_MATCHES]
            )
            return _llm_notice(
                f"colloquial Japanese expressions detected in {tool_name}.{field}."
                f" Matches: {len(hits)} ({listed})."
                f" Rewrite the whole sentence containing the detected expression"
                f" using formal written-style expressions"
                f" (standard technical terminology, dictionary form,"
                f" no metaphorical verbs) per agent-toolkit/rules/01-agent.md '日本語' section."
                f" Do not just swap the detected word for a synonym; restructure the sentence."
                f" Target: {file_path}",
                tag="warn",
            )
    return None


# --- 「Xを根拠にYしない」形式の増加検出 (warn, FB10) ---

# `agent-toolkit/rules/01-agent.md`「日本語」節が指摘する誤読リスクのある禁止規定形式。
# 「Xでなければ`Y`してよい」と誤読される可能性があるため、全称否定形への書き換えを推奨する。
_STYLE_NEGATION_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"([^、\s]{1,20})を根拠に([^、\s]{1,20})しない"),
    re.compile(r"([^、\s]{1,20})を理由に([^、\s]{1,20})しない"),
)


def _is_style_negation_target_doc(file_path: str) -> bool:
    """対象ドキュメント（コーディングエージェント向け文書判定対象と同一の判定基準）への編集かを判定する。"""
    return _plan_format.is_agent_doc_target_file(file_path)


def _count_style_negation_matches(text: str) -> int:
    """`_STYLE_NEGATION_PATTERNS`の総マッチ件数を返す。"""
    return sum(len(pattern.findall(text)) for pattern in _STYLE_NEGATION_PATTERNS)


def _check_style_negation(tool_name: str, operation: _hook_tool_input.EditOperation, file_path: str) -> str | None:
    """『Xを根拠にYしない』『Xを理由にYしない』形式の増加を検出したら警告本文を返す（warn）。

    全文を書き込む操作（Claudeの`Write`、Codex patchの`*** Add File:`）は変更後全文の
    マッチ件数が1件以上であれば警告する。断片単位の操作（ClaudeのEdit・MultiEdit、
    Codex patchの`*** Update File:`）は断片ごとに変更前後の件数を比較し、増加時のみ警告する
    （既存文字列の保持時は件数同数で誤検出しない）。
    """
    if not _is_style_negation_target_doc(file_path):
        return None
    if operation.is_whole_write:
        increased = _count_style_negation_matches(operation.whole_after_text or "") > 0
    else:
        increased = any(
            _count_style_negation_matches(fragment.after) > _count_style_negation_matches(fragment.before)
            for fragment in operation.fragments
        )
    if not increased:
        return None
    return _llm_notice(
        f"detected an increase in meta-norm phrases of the form '`X`を根拠に`Y`しない' / '`X`を理由に`Y`しない'"
        f" via {tool_name}. Target: {file_path}."
        " Such phrasing risks being misread as 'if not X, then it is fine to Y'."
        " Consider rewriting to the universal-negation form"
        " ('いかなる理由（例: X）があっても`Y`しない')."
        " See agent-toolkit/rules/01-agent.md '日本語' section.",
        tag="warn",
    )


# frontmatter区間（`^---$`〜`^---$`）の抽出用。
_FRONTMATTER_BLOCK_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---(?:\r?\n|\Z)", re.DOTALL)


def _resolve_referenced_path(file_path: str, referenced: str) -> pathlib.Path | None:
    """`file_path`の祖先ディレクトリを起点に`referenced`（相対パス）の実ファイルを探索する。

    frontmatterの同期注記は同一ディレクトリまたは近隣ディレクトリの兄弟ファイルを
    裸ファイル名（例: `plan-impl-executor.md`）で参照する形式が実運用で使われるため、
    `.git`を持つ祖先（リポジトリルート）を発見しても即確定とせず、以下の順に実在確認する。

    1. `file_path`の各祖先ディレクトリ（近い順。同一ディレクトリの兄弟ファイル参照に対応）
    2. リポジトリルート配下の`agent-toolkit/agents/`・`agent-toolkit/rules/`・
       `agent-toolkit/skills/`（近隣ディレクトリの参照に対応。`.git`祖先が見つかった場合のみ）

    いずれの経路でも実在しない場合は`None`を返す。
    """
    start = pathlib.Path(file_path).resolve().parent
    ancestors = (start, *start.parents)
    search_roots: list[pathlib.Path] = list(ancestors)

    repo_root: pathlib.Path | None = None
    for candidate in ancestors:
        if (candidate / ".git").exists():
            repo_root = candidate
            break
    if repo_root is not None:
        search_roots.extend(
            repo_root / neighbor for neighbor in ("agent-toolkit/agents", "agent-toolkit/rules", "agent-toolkit/skills")
        )

    for candidate in search_roots:
        resolved = candidate / referenced
        if resolved.exists():
            return resolved
    return None


# --- .md規範文書の本文中にある節参照の実在検証check (warn) ---

_BODY_SECTION_REFERENCE_RE = re.compile(r"`([^`\n]+\.md)`「([^」\n]+)」[節項]")


def _check_body_section_reference_exists(tool_name: str, content: str, file_path: str) -> str | None:
    """規範文書の本文中にある他ファイルの節参照の実在を検査して警告本文を返す（warn）。

    本checkは本文（frontmatter区間を除く）を走査する。
    参照先ファイル名が複数のパスへ一致する場合は照合せず、一意に解決できない旨を警告する。
    """
    # 対象ファイル判定: `agent-toolkit/rules/`・`agent-toolkit/skills/`・`agent-toolkit/agents/`配下の`.md`
    if not file_path:
        return None
    normalized = file_path.replace("\\", "/")
    is_target = any(
        pattern.search(normalized)
        for pattern in (
            re.compile(r"(^|/)agent-toolkit/rules/[^/]+\.md$"),
            re.compile(r"(^|/)agent-toolkit/skills/(?:(?!.*/references/).)+/[^/]+\.md$"),
            re.compile(r"(^|/)agent-toolkit/agents/[^/]+\.md$"),
        )
    )
    if not is_target:
        return None

    # frontmatter区間を除いた本文のみを走査対象とする。
    frontmatter_match = _FRONTMATTER_BLOCK_RE.match(content)
    self_body = content[frontmatter_match.end() :] if frontmatter_match is not None else content

    # 本文から節参照を抽出
    references = _BODY_SECTION_REFERENCE_RE.findall(self_body)
    if not references:
        return None

    reasons: list[str] = []
    for file_name, section_name in references:
        # ファイル参照の解決: 相対解決を第一とし、リポジトリ内で一意に定まる場合のみ照合
        resolved = _resolve_referenced_path(file_path, file_name)
        if resolved is None:
            reasons.append(f"referenced file path does not exist: {file_name}")
            continue

        # 参照先ファイルを読み込み、節名を照合
        try:
            referenced_body = resolved.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            reasons.append(f"failed to read referenced file: {file_name}")
            continue

        # 見出し一致（`^#+\s*<節名>$`）または部分文字列一致で照合
        search_corpus = referenced_body
        heading_pattern = re.compile(rf"^#+\s*{re.escape(section_name)}\s*$", re.MULTILINE)
        if heading_pattern.search(search_corpus) is None and section_name not in search_corpus:
            reasons.append(f"section name does not exist: `{file_name}` '{section_name}'")

    if not reasons:
        return None
    return _llm_notice(
        "the section reference in the body of the normative document may not exist"
        f" ({tool_name}, target: {file_path}): {'; '.join(reasons)}."
        " Verify that the reference matches the target file and section name.",
        tag="warn",
    )


# Claude CodeとCodexが生成するagents_serverの完全修飾MCP tool名。
_AGENTS_SERVER_NAMESPACES = (
    "mcp__plugin_agent-toolkit_agents_server__",
    "mcp__agents_server__",
)
_AGENTS_SERVER_START_TOOLS = frozenset(f"{namespace}start" for namespace in _AGENTS_SERVER_NAMESPACES)
_AGENTS_SERVER_WAIT_TOOLS = frozenset(f"{namespace}wait" for namespace in _AGENTS_SERVER_NAMESPACES)
_AGENTS_SERVER_SEND_TOOLS = frozenset(f"{namespace}send_message" for namespace in _AGENTS_SERVER_NAMESPACES)
_AGENTS_SERVER_KILL_TOOLS = frozenset(f"{namespace}kill" for namespace in _AGENTS_SERVER_NAMESPACES)
_AGENTS_SERVER_TOOL_NAMES = (
    _AGENTS_SERVER_START_TOOLS | _AGENTS_SERVER_WAIT_TOOLS | _AGENTS_SERVER_SEND_TOOLS | _AGENTS_SERVER_KILL_TOOLS
)
_AGENTS_SERVER_SESSION_CWD_KEY = "agents_server_cwd_by_session"


# --- plan mode中のplan file編集をplan-modeスキル未起動の場合にブロック ---

_PLAN_FILE_EDIT_TOOLS = frozenset({"Write", "Edit", "MultiEdit"})


def _check_plan_mode_skill_first(
    tool_name: str,
    tool_input: dict,
    session_id: str,
) -> str | None:
    """plan-modeスキル未起動のままplan fileを編集しようとした場合に警告する。

    判定条件:

    - `session_id`が空でない（空ならセッション状態を取得できず判定不能のためスキップ）
    - セッション状態の`plan_mode_skill_invoked`が偽
    - `tool_name`が`Write` / `Edit` / `MultiEdit`のいずれか
    - 対象の`file_path`が`~/.claude/plans/`直下の計画本体・実装詳細・バグ調査付属ファイル

    `permission_mode`の値に依らず適用する（plan mode外でも計画ファイル編集時には同様に違反が起こり得るため）。
    サブエージェント経由の呼び出しでも同一の判定が働く
    （本checkは`isSidechain`を参照せず、`permission_mode`とセッション状態のみで判定するため）。
    計画ファイル編集に至るまでは警告を表示しない
    （`process-feedbacks`等の他スキル呼び出し・通常のRead・Bash操作は素通りする）。
    既存計画へのEdit・MultiEditで、一意かつ最後の`## 進捗ログ`見出し行までの接頭部が
    編集後も不変である場合は、受領側の正規操作として警告しない。
    ファイル又は入力を解釈できない場合は警告を維持する。
    警告のみでツール呼び出しは継続する（block降格）。
    呼び出し元はplan-modeの直接委譲手順で計画確定前に警告を解消・検収する。
    違反を検出した場合は通知本文を返し、呼び出し元が`additionalContext`へ結合する。
    違反が無い場合はNoneを返す。
    """
    if not session_id:
        return None
    if tool_name not in _PLAN_FILE_EDIT_TOOLS:
        return None
    file_path_raw = tool_input.get("file_path")
    if not isinstance(file_path_raw, str) or not _is_plan_file_or_adjunct(file_path_raw):
        return None
    state = read_state(session_id)
    if state.get("plan_mode_skill_invoked", False):
        return None
    if tool_name in {"Edit", "MultiEdit"} and _is_progress_log_only_edit(tool_name, tool_input, file_path_raw):
        return None
    return _llm_notice(
        "warning: editing a plan file without invoking `agent-toolkit:plan-mode` skill first."
        " If you are authoring the plan yourself, invoke the skill and restart from"
        " Phase 1 (Initial Understanding)"
        " before continuing the plan file edit."
        " If you are reviewing a delegated plan and only correcting values uniquely"
        " determined by the artifact and evidence, continue without restarting plan-mode"
        " after recording each correction and its evidence in `## 変更履歴`."
        " Resolve and verify this warning through the plan-mode direct delegation workflow"
        " before finalizing the plan.",
        tag="warn",
    )


def _is_progress_log_only_edit(tool_name: str, tool_input: dict, file_path: str) -> bool:
    """既存計画の進捗ログ節だけを変更するEdit又はMultiEditであるかを返す。"""
    try:
        existing = pathlib.Path(file_path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False

    edited = _apply_edits_to_content(tool_name, tool_input, existing)
    if edited is None:
        return False
    existing_prefix = _progress_log_heading_prefix(existing)
    edited_prefix = _progress_log_heading_prefix(edited)
    return existing_prefix is not None and existing_prefix == edited_prefix


def _progress_log_heading_prefix(content: str) -> str | None:
    """一意かつ最後の進捗ログH2見出し行までの接頭部を返す。"""
    headings = _plan_format.extract_headings(content)
    progress_index = _plan_format.find_heading_index(headings, 2, _plan_format.PLAN_H2_PROGRESS)
    if progress_index is None:
        return None
    h2_headings = [heading for heading in headings if heading.level == 2]
    if sum(heading.text in _plan_format.h2_aliases(_plan_format.PLAN_H2_PROGRESS) for heading in h2_headings) != 1:
        return None
    progress_heading = headings[progress_index]
    if not h2_headings or h2_headings[-1] != progress_heading:
        return None
    return "".join(content.splitlines(keepends=True)[: progress_heading.lineno])


# --- plan-modeスキル起動後、計画ファイル未作成のままagent-toolkit配下の直接編集連続をブロック ---


# `_check_direct_agent_toolkit_edits_after_plan_mode`専用の配布先追加パターン。
# 原本パス（`agent-toolkit/rules/`・`agent-toolkit/skills/.../SKILL.md`・
# `agent-toolkit/skills/.../references/`・`agent-toolkit/agents/`）は
# `_plan_format.is_agent_doc_target_file`のSSOTを再利用して判定するため本定数へ列挙しない。
# 本定数は原本パスから配布された実在経路のみを追加対象として保持する。
#
# 実在する配布経路は次の2系統である。
#
# - `agent-toolkit/rules/` → `~/.claude/rules/agent-toolkit/`
#   （`pytools/_internal/sync_agent_toolkit_rules.py`によるcopy sync）
# - `~/.claude/plugins/cache/<owner>-<repo>/agent-toolkit/`
#   （Claude Codeのプラグインマーケットプレイス経由の配布展開先）
#
# `.claude/skills/agent-toolkit*/`および`.chezmoi-source/dot_claude/`配下への
# agent-toolkit経由の配布経路は存在しないため本定数の対象に含めない。
# `AGENTS.md`・`CLAUDE.md`のbasename一致（`_plan_format.AGENT_DOC_TARGET_BASENAMES`）は
# プロジェクトごとの文書へ波及するため本checkの対象からは除外する
# （本checkはagent-toolkit本体への連続直接編集の抑止を目的とし、
# プロジェクトごとの`AGENTS.md`・`CLAUDE.md`編集は本目的の対象外）。
_DIRECT_AGENT_TOOLKIT_DISTRIBUTION_PATTERNS: tuple[re.Pattern[str], ...] = (
    # `agent-toolkit/rules/` → `~/.claude/rules/agent-toolkit/`
    # （`pytools/_internal/sync_agent_toolkit_rules.py`によるcopy sync）
    re.compile(r"(^|/)\.claude/rules/agent-toolkit/.+\.md$"),
    # `~/.claude/plugins/cache/<owner>-<repo>/agent-toolkit/`
    # （Claude Codeのプラグインマーケットプレイス経由の配布展開先）
    re.compile(r"(^|/)\.claude/plugins/cache/[^/]+/agent-toolkit/.+\.md$"),
)

# `_is_direct_agent_toolkit_edit_target`専用の除外パターン。
# `_plan_format.AGENT_DOC_TARGET_PATTERNS`はプロジェクト直下の`.claude/rules/`・`.claude/skills/`配下も
# コーディングエージェント向け文書として判定するが、本checkはagent-toolkit本体への連続直接編集の抑止を
# 目的とするため、プロジェクトごとの規範文書は対象から外す。
# `.claude/rules/agent-toolkit/`はagent-toolkitの配布先であるため、配布経路側で引き続き対象とする。
_PROJECT_LOCAL_AGENT_DOC_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(^|/)\.claude/rules/(?!agent-toolkit/).+\.md$"),
    re.compile(r"(^|/)\.claude/skills/.+\.md$"),
)


def _is_direct_agent_toolkit_edit_target(file_path: str) -> bool:
    """`_check_direct_agent_toolkit_edits_after_plan_mode`の対象パス判定。

    原本パスは`_plan_format.is_agent_doc_target_file`のSSOTを再利用して判定する
    （`agent-toolkit/rules/`・`agent-toolkit/skills/.../SKILL.md`・
    `agent-toolkit/skills/.../references/`・`agent-toolkit/agents/`・
    `.chezmoi-source/dot_claude/rules/`を含む）。
    加えて、実在する配布経路（`~/.claude/rules/agent-toolkit/`・
    `~/.claude/plugins/cache/*/agent-toolkit/`）を
    `_DIRECT_AGENT_TOOLKIT_DISTRIBUTION_PATTERNS`で追加照合する。
    `AGENTS.md`・`CLAUDE.md`のbasename一致とプロジェクト直下の`.claude/rules/`・
    `.claude/skills/`配下は、プロジェクトごとの文書へ波及するため本checkの対象外とする。
    """
    if not isinstance(file_path, str) or not file_path:
        return False
    normalized = file_path.replace("\\", "/")
    # basename一致（AGENTS.md/CLAUDE.md）はプロジェクト文書波及のため除外する。
    if pathlib.Path(normalized).name in _plan_format.AGENT_DOC_TARGET_BASENAMES:
        return False
    # プロジェクト直下の`.claude/rules/`・`.claude/skills/`配下も同じ理由で除外する。
    if any(pat.search(normalized) for pat in _PROJECT_LOCAL_AGENT_DOC_PATTERNS):
        return False
    if _plan_format.is_agent_doc_target_file(file_path):
        return True
    return any(pat.search(normalized) for pat in _DIRECT_AGENT_TOOLKIT_DISTRIBUTION_PATTERNS)


def _check_direct_agent_toolkit_edits_after_plan_mode(
    tool_name: str,
    tool_input: dict,
    session_id: str,
) -> tuple[bool, str | None]:
    """plan-modeスキル起動後、計画ファイル未作成のまま`agent-toolkit`配下の直接編集連続を検知する。

    判定条件:

    - `session_id`が空でない
    - `tool_name`が`Write` / `Edit` / `MultiEdit`のいずれか
    - セッション状態の`plan_mode_skill_invoked`が真
    - セッション状態の`plan_file_written`が偽

    連続判定は`last_agent_toolkit_edit_path`と対象パスを比較し、
    直前と異なるパスのときのみ`direct_agent_toolkit_edit_count`をincrementする。
    `~/.claude/plans/`配下の計画本体・実装詳細・バグ調査付属ファイルへのWrite/Edit時は
    `plan_file_written`を真にしてカウンタをリセットする。
    対象外パスへの編集時もカウンタをリセットする。
    カウンタ2件目でwarn（`additionalContext`へ載せる通知本文を返して進行を継続）、
    3件目以上でblock（stderr出力＋第1要素にTrueを返してツール呼び出しを中断）する。
    block時は`direct_agent_toolkit_edit_count`と`last_agent_toolkit_edit_path`を更新しない。
    block後にコーディングエージェントが同一パスを再試行した場合、
    直前パス一致条件によるカウンタ加算スキップで素通りする回避を防ぐため、
    カウンタは加算直前の値のまま保持し、再試行時に再度加算されblockが継続する。

    Returns:
        （block判定, 通知本文またはNone）のタプル。
    """
    if not session_id:
        return False, None
    if tool_name not in _PLAN_FILE_EDIT_TOOLS:
        return False, None
    file_path_raw = tool_input.get("file_path")
    if not isinstance(file_path_raw, str) or not file_path_raw:
        return False, None
    state = read_state(session_id)
    if not state.get("plan_mode_skill_invoked", False):
        return False, None

    # 計画本体・実装詳細・バグ調査付属ファイルの編集時は`plan_file_written`を真にしカウンタをリセットする。
    if _is_plan_file_or_adjunct(file_path_raw):

        def _mark_plan_written(current: dict) -> dict | None:
            changed = False
            if not current.get("plan_file_written", False):
                current["plan_file_written"] = True
                changed = True
            if current.get("direct_agent_toolkit_edit_count", 0) != 0:
                current["direct_agent_toolkit_edit_count"] = 0
                changed = True
            if current.get("last_agent_toolkit_edit_path") is not None:
                current["last_agent_toolkit_edit_path"] = None
                changed = True
            return current if changed else None

        update_state(session_id, _mark_plan_written)
        return False, None

    # 計画ファイルが既に作成済みの場合は本checkの対象外。
    if state.get("plan_file_written", False):
        return False, None

    # 対象外パスへの編集ならカウンタをリセットして通過。
    if not _is_direct_agent_toolkit_edit_target(file_path_raw):

        def _reset_counter(current: dict) -> dict | None:
            if current.get("direct_agent_toolkit_edit_count", 0) == 0 and current.get("last_agent_toolkit_edit_path") is None:
                return None
            current["direct_agent_toolkit_edit_count"] = 0
            current["last_agent_toolkit_edit_path"] = None
            return current

        update_state(session_id, _reset_counter)
        return False, None

    # 直前と同一パスの場合はincrementしない（連続判定は異なるファイルに対する編集を対象とする）。
    last_path = state.get("last_agent_toolkit_edit_path")
    if isinstance(last_path, str) and last_path == file_path_raw:
        return False, None

    # 並列edit時のlost update回避のため、都度ロック内で加算する。
    # `_mark_plan_written`・`_reset_counter`と同様、`update_state`のmutator内で
    # 現在値を再取得してから+1する。呼び出し元へは結果値を`captured`辞書経由で返す。
    captured: dict[str, int] = {"count": 0}

    def _increment(current: dict) -> dict | None:
        count = int(current.get("direct_agent_toolkit_edit_count", 0) or 0) + 1
        captured["count"] = count
        if count >= 3:
            # block時はstate更新をスキップする。
            # 直前パスとカウンタを更新してしまうと、コーディングエージェントが
            # 同一パスを再試行した際に「直前と同一パス」条件で
            # `_increment`到達前にreturn Falseとなりblockが素通りする。
            # 更新をスキップすることで再試行時も再度3件目としてblockが継続する。
            return None
        current["direct_agent_toolkit_edit_count"] = count
        current["last_agent_toolkit_edit_path"] = file_path_raw
        return current

    update_state(session_id, _increment)
    new_count = captured["count"]

    if new_count >= 3:
        print(
            _block_notice(
                f"blocked: after invoking the plan-mode skill, {new_count} consecutive Write/Edit/MultiEdit"
                f" operations targeted files under agent-toolkit/ without first creating a plan file.",
                fix="Create a plan file under `~/.claude/plans/` before editing any file under agent-toolkit/.",
            ),
            file=sys.stderr,
        )
        return True, None
    if new_count == 2:
        return False, _llm_notice(
            f"warn: after invoking the plan-mode skill, {new_count} consecutive Write/Edit/MultiEdit"
            f" operations targeted files under agent-toolkit/ without first creating a plan file."
            " The next such edit will be blocked."
            " Create a plan file under `~/.claude/plans/` first.",
            tag="warn",
        )
    return False, None


def _apply_edits_to_content(tool_name: str, tool_input: dict, existing: str) -> str | None:
    """Edit又はMultiEditを既存内容へ適用した文字列を返す。"""
    if tool_name == "Edit":
        old_string = tool_input.get("old_string")
        new_string = tool_input.get("new_string")
        if not isinstance(old_string, str) or not isinstance(new_string, str):
            return None
        if not existing:
            return new_string
        if bool(tool_input.get("replace_all")):
            return existing.replace(old_string, new_string)
        return existing.replace(old_string, new_string, 1)

    if tool_name == "MultiEdit":
        edits = tool_input.get("edits") or []
        if not isinstance(edits, list):
            return None
        result = existing
        for edit in edits:
            if not isinstance(edit, dict):
                continue
            old_string = edit.get("old_string")
            new_string = edit.get("new_string")
            if not isinstance(old_string, str) or not isinstance(new_string, str):
                continue
            if bool(edit.get("replace_all")):
                result = result.replace(old_string, new_string)
            else:
                result = result.replace(old_string, new_string, 1)
        return result

    return None


# --- 計画単位の状態管理 ---

# Skillツールの`skill`引数として許容するplan-modeスキル名。
# posttooluse.pyの`_PLAN_MODE_SKILL_NAMES`と対応させる。
_PLAN_MODE_SKILL_NAMES: frozenset[str] = frozenset({"agent-toolkit:plan-mode", "plan-mode"})
# Agent/Taskツールの`subagent_type`引数として許容するplan-impl-executor識別子。
# フルネームと短縮名の両方を許容する。
_PLAN_IMPL_EXECUTOR_SUBAGENT_TYPES: frozenset[str] = frozenset({"agent-toolkit:plan-impl-executor", "plan-impl-executor"})
_FEEDBACKS_PLANNER_SUBAGENT_TYPES: frozenset[str] = frozenset({"agent-toolkit:feedbacks-planner", "feedbacks-planner"})
_PLAN_REVIEW_EXECUTOR_SUBAGENT_TYPES: frozenset[str] = frozenset({"agent-toolkit:plan-review-executor", "plan-review-executor"})

# `model`引数指定を一律禁止する対象。調整役は定義済みモデルを使う委譲窓口として動く。
_MODEL_OVERRIDE_FORBIDDEN_SUBAGENT_TYPES: frozenset[str] = (
    _PLAN_IMPL_EXECUTOR_SUBAGENT_TYPES | _FEEDBACKS_PLANNER_SUBAGENT_TYPES | _PLAN_REVIEW_EXECUTOR_SUBAGENT_TYPES
)
_WEBFETCH_VERBATIM_RE = re.compile(
    r"(?:全文|原文|そのまま|逐語|引用|verbatim|word[ -]for[ -]word)",
    re.IGNORECASE,
)


def _check_webfetch_verbatim_request(tool_input: dict) -> str | None:
    """WebFetchへ逐語再現を要求する入力を検出して警告する。"""
    prompt = tool_input.get("prompt")
    if not isinstance(prompt, str) or _WEBFETCH_VERBATIM_RE.search(prompt) is None:
        return None
    return _llm_notice(
        "WebFetch uses a summarization model and is not evidence for verbatim quotation."
        " Save the raw content from the same URL in an agent-toolkit managed temporary directory,"
        " then quote only the relevant passage from the saved raw content.",
        tag="warn",
    )


def _check_sendmessage_agent_type_recipient(tool_input: dict) -> str | None:
    """SendMessageの宛先にエージェント種別名を指定した場合に警告する。"""
    recipient = tool_input.get("to")
    if not isinstance(recipient, str) or ":" not in recipient:
        return None
    return _llm_notice(
        "An agent type name is not a reachable SendMessage recipient."
        " Return the normal completion report through the tool result once;"
        " send an immediate notification only to the caller identifier supplied by the runtime.",
        tag="warn",
    )


def _check_subagent_model_override(subagent_type: str, tool_input: dict) -> bool:
    """定義済みモデルを使う委譲調整役への`model`引数指定を一律ブロックする。

    `plan-impl-executor`は定義済みモデルを使う委譲窓口として動くため、呼び出しごとの上書きを許容しない。
    """
    if subagent_type not in _MODEL_OVERRIDE_FORBIDDEN_SUBAGENT_TYPES:
        return False
    if "model" not in tool_input:
        return False
    model = tool_input.get("model")
    print(
        _block_notice(
            f"blocked: explicit `model` argument (`{model!r}`) for subagent_type `{subagent_type}`.\n"
            "Why this gate exists: this subagent uses its frontmatter model;"
            " no per-call model override is defined.",
            fix="Omit the `model` parameter and let the agent definition's default apply.",
        ),
        file=sys.stderr,
    )
    return True


# --- TaskStop: 初回遮断と再実行窓 ---

_TASK_STOP_RETRY_WINDOW_SECONDS = 300


def _check_task_stop(session_id: str) -> bool:
    """`TaskStop`呼び出しを初回遮断し、再実行窓内なら通過させる。

    判定は状態キー`task_stop_blocked_at`（`float`。セッション単位で1つだけ持つ、
    直近の遮断時刻のPOSIX秒）を用いる。値が存在し現在時刻との差が
    `_TASK_STOP_RETRY_WINDOW_SECONDS`以下なら通過（偽を返す）し、それ以外は値を
    現在時刻へ更新して遮断（真を返す）する。停止対象の識別子（`tool_input`の
    `task_id`・`shell_id`）は読まない。

    ここで保存する時刻は再実行許可窓の判定にのみ用いる値であり、状態ファイル自体の
    回収期限（`_session_state.STALE_STATE_MAX_AGE_SECONDS`によるmtime基準の14日）とは
    別の寿命を持つ。
    """
    now = time.time()
    state = read_state(session_id)
    blocked_at = state.get("task_stop_blocked_at")
    if isinstance(blocked_at, (int, float)) and now - blocked_at <= _TASK_STOP_RETRY_WINDOW_SECONDS:
        return False

    def _mark_blocked(current: dict) -> dict | None:
        current["task_stop_blocked_at"] = now
        return current

    update_state(session_id, _mark_blocked)
    print(
        _block_notice(
            "blocked: TaskStop."
            " Only stop a background task on the user's explicit, immediate stop request,"
            " or after completing the stall-detection procedure;"
            " slow progress or perceived inefficiency alone is not a stop instruction."
            " If more than one interpretation of intent remains, confirm with AskUserQuestion before stopping."
            " After user intervention, send additional instructions to active delegates by default;"
            " stop only when the intervention invalidates the delegated scope or assumptions"
            " and continuing would produce an incorrect artifact, as specified by"
            " `agent-toolkit:delegation`「継続と新規起動」.",
            fix="If the basis for stopping is already confirmed, retry TaskStop within 5 minutes to proceed.",
        ),
        file=sys.stderr,
    )
    return True


def _reset_plan_mode_state(session_id: str) -> None:
    """plan-mode起動時に計画単位の状態をリセットする。"""
    if not session_id:
        return

    def _reset(current: dict) -> dict | None:
        changed = False
        if current.pop("current_plan_file_path", None) is not None:
            changed = True
        # 直接編集連続checkの状態も新計画へ持ち越さない。
        if current.get("plan_file_written", False):
            current["plan_file_written"] = False
            changed = True
        if current.get("direct_agent_toolkit_edit_count", 0) != 0:
            current["direct_agent_toolkit_edit_count"] = 0
            changed = True
        if current.get("last_agent_toolkit_edit_path") is not None:
            current["last_agent_toolkit_edit_path"] = None
            changed = True
        return current if changed else None

    update_state(session_id, _reset)


# --- Bash: heredoc内のパターンを除外するヘルパー ---


def _likely_real_command(command: str, pos: int) -> bool:
    """マッチ位置がシェルコマンド文脈にあるかヒューリスティックで判定する。

    heredoc（`<<`）がマッチ位置より前にある場合、マッチはリテラル文字列の
    一部である可能性が高いため偽を返す。
    `python3 -c` / `cat <<`等でファイル内容を書き込むケースの誤検出を防ぐ。
    """
    prefix = command[:pos]
    return "<<" not in prefix


# --- Bash: git amend / rebaseをlog未確認でブロック ---


def _check_bash_amend_rebase_without_log(command: str, session_id: str, cwd: str) -> bool:
    """Git commit --amend / git rebaseをgit log未確認で実行しようとした場合にブロックする。

    amend / rebaseは既存コミットを書き換えるため、直前にgit log --decorateで
    コミット状態（特にプッシュ済みかどうか）を確認する必要がある。
    リセット条件は対象コミットの親子関係が変化する操作（commit・rebase・reset）に限定する
    （`posttooluse.py` `_GIT_LOG_RESET_SUBCOMMANDS`が単一のリセット判定箇所）。
    ファイル編集・push・Stopの介在ではリセットしない
    （push・Stopはコミット木を書き換えないため再確認を強制する必要がない）。

    `git_log_checked`はcwd別に管理する辞書`{cwd: True}`形式を採用する。
    旧形式のbool値（`True` / `False`）はcwd空文字列環境向けの後方互換として
    そのまま参照する。
    判定は`extract_git_events`の結果を消費し、各git呼び出しの実効cwd
    （`cd`・`pushd`・`git -C`の影響を反映）ごとに行う。
    実効cwdがscratchpad配下（`_scratchpad_path.is_scratchpad_path`）で、かつ当該cwdの
    `git remote`（`_git_status.run_git_lines`）が空リストの場合だけ、当該イベントを
    検査対象から外す。取得失敗（`None`）または非空リストの場合は検査を適用する
    （取得失敗を除外の根拠にしない）。
    """
    targets: list[tuple[GitEvent, str]] = []
    for event in extract_git_events(command, cwd):
        if event.subcommand == "commit" and "--amend" in event.subcommand_args:
            targets.append((event, "git commit --amend"))
        elif event.subcommand == "rebase":
            targets.append((event, "git rebase"))
    if not targets:
        return False
    unresolved = next(((event, op) for event, op in targets if not event.cwd_resolved), None)
    if unresolved is not None:
        event, op = unresolved
        if event.unresolved_expression is not None:
            reason = (
                f"blocked: {op}. The working directory expression"
                f" {event.unresolved_expression!r} cannot be resolved statically."
            )
            fix = (
                "Run `git -C <absolute path> log --oneline --decorate` first, then retry the history rewrite"
                " with `git -C <absolute path>`."
            )
        else:
            reason = f"blocked: {op}. The command changes its working directory through an unresolved shell expression."
            fix = (
                "Run `git log --oneline --decorate` from the target repository first,"
                " then retry with a statically resolvable working directory."
            )
        print(
            _block_notice(
                reason,
                fix=fix,
            ),
            file=sys.stderr,
        )
        return True
    state = read_state(session_id)
    log_state = state.get("git_log_checked", False)
    for event, op in targets:
        event_cwd = event.cwd
        if event_cwd and _scratchpad_path.is_scratchpad_path(pathlib.Path(event_cwd)):
            remotes = _git_status.run_git_lines(["git", "remote"], event_cwd)
            if remotes == []:
                continue
        if isinstance(log_state, dict):
            if event_cwd and log_state.get(event_cwd, False):
                continue
        elif log_state:
            continue
        print(
            _block_notice(
                f"blocked: {op}. Commit state must be confirmed before amend/rebase.",
                fix=(
                    "Run `git log --oneline --decorate` first to confirm commit state before amend/rebase"
                    " (especially, do NOT amend/rebase commits that have already been pushed)."
                    " A `git log` in the same Bash command does not satisfy this check;"
                    " run it in a preceding Bash call against the same effective working directory."
                ),
            ),
            file=sys.stderr,
        )
        return True
    return False


# --- Bash: git push 前 amend後dirty状態のブロック ---


def _check_bash_git_push_after_amend_with_dirty_status(command: str, session_id: str, cwd: str) -> bool:
    """Git push 実行前に amend直後の未コミット差分残置を機械的にブロックする。

    posttooluse側で git commit --amend / --fixup 成功時に設定した
    cwd別の`amend_pending_status_check`フラグがTrueで、かつ現在の作業ツリーに追跡ファイル
    未コミット差分がある場合、pushをブロックして目視・機械両面での確認を促す。
    フラグが立っていないか差分がない場合はブロックしない。
    差分なし時は該当cwdのフラグも解除して通過させるが、解除対象は実送出pushに限定する
    （`git push --dry-run`など送出しないpush系サブコマンドではdirty時blockを実施しclean時は解除せず状態を保つ）。
    差分検出は共有ヘルパー`_git_status.has_tracked_dirty`（`git -C <cwd> status --porcelain`実行）を使い、
    未追跡ファイル（`??`行）を除いた出力行が1件以上あればdirtyと判定する。
    cwd解析は既存の`extract_git_events(command, cwd)`ヘルパーで`git -C <path>`および
    `cd <path> && git push`両形式に対応する（cwd別辞書の実効cwd参照を統一）。
    """
    push_events = [event for event in extract_git_events(command, cwd) if event.subcommand == "push"]
    if not push_events:
        return False
    state = read_state(session_id)
    flags = state.get(_git_status.AMEND_PENDING_FLAG_KEY)
    if not isinstance(flags, dict):
        return False
    for event in push_events:
        if not event.cwd_resolved:
            if any(value is True for value in flags.values()):
                if event.unresolved_expression is not None:
                    reason = (
                        "blocked: git push after an amend/fixup could not resolve the working directory expression"
                        f" {event.unresolved_expression!r}."
                    )
                    fix = "Retry the push as `git -C <absolute path> push ...` after confirming the target repository."
                else:
                    reason = "blocked: git push after an amend/fixup could not resolve its working directory."
                    fix = "Review the amend state and retry with a statically resolvable working directory."
                print(
                    _block_notice(
                        reason,
                        fix=fix,
                    ),
                    file=sys.stderr,
                )
                return True
            continue
        if not flags.get(event.cwd, False):
            continue
        dirty = _git_status.has_tracked_dirty(event.cwd)
        if dirty is None:
            continue
        if dirty:
            print(
                _block_notice(
                    f"blocked: git push after `git commit --amend` / `--fixup` with uncommitted tracked changes"
                    f" in {event.cwd}.",
                    fix=(
                        "Run `git status` to review, then either `git add` + `git commit --amend`"
                        " (or `--fixup=<sha>`) to fold the residual diff into the amended commit,"
                        " or create a follow-up commit before pushing."
                    ),
                ),
                file=sys.stderr,
            )
            return True
        if _git_status.git_push_is_real_send(event.subcommand_args):
            event_cwd = event.cwd

            def _reset(current: dict, target_cwd: str = event_cwd) -> dict | None:
                current_flags = current.get(_git_status.AMEND_PENDING_FLAG_KEY)
                if not isinstance(current_flags, dict) or not current_flags.get(target_cwd, False):
                    return None
                current_flags[target_cwd] = False
                current[_git_status.AMEND_PENDING_FLAG_KEY] = current_flags
                return current

            update_state(session_id, _reset)
    return False


# --- Bash: 一括ステージ実行時の未編集ファイル警告 ---


def _has_a_flag(args: list[str]) -> bool:
    """`git commit`の`-a`フラグ検出。`--all`、または短フラグクラスタ内の`a`を検出する。

    `-am`・`-amx`等の連結ショートフラグにも一致する。
    簡略化: 値付きフラグ（`-S<key-id>`等でクラスタ内に`a`が現れる形）は誤検出しうる。
    `git commit`の`-S<value>`は`-S <value>`形式でも受け付けるため、
    実運用ではまず短フラグクラスタに値が続かないため許容する。
    見直し契機: 誤警告報告が発生した場合。
    """
    for tok in args:
        if tok == "--all":
            return True
        if tok.startswith("-") and not tok.startswith("--") and "a" in tok[1:]:
            return True
    return False


def _detect_bulk_stage_mode(event: GitEvent) -> str | None:
    """一括ステージ操作の検出。該当時はモード名を返す。

    - `git add -A` / `git add --all` / `git add .`: `include_untracked`
    - `git add -u` / `git add --update`: `tracked_only`
    - `git commit -a` / `git commit --all` / `git commit -am`等: `tracked_only`
    """
    args = event.subcommand_args
    if event.subcommand == "add":
        for tok in args:
            if tok in ("-A", "--all", "."):
                return "include_untracked"
        for tok in args:
            if tok in ("-u", "--update"):
                return "tracked_only"
        return None
    if event.subcommand == "commit":
        if _has_a_flag(args):
            return "tracked_only"
        return None
    return None


def _parse_git_status_short(stdout: str, mode: str) -> set[str]:
    """`git status --short`出力から変更ファイルの相対パス集合を返す。

    `mode == "tracked_only"`のときは`??`（未追跡）行を除外する。
    リネーム行`R  old -> new`は新パスを採用する。
    簡略化: クォート付きパス（`core.quotepath`有効時のUnicodeエスケープ等）は
    先頭・末尾のダブルクォート除去のみで内部のエスケープは非対応。
    見直し契機: エスケープを含むパスで誤検出報告が発生した場合。
    """
    files: set[str] = set()
    for line in stdout.splitlines():
        if len(line) < 4:
            continue
        prefix = line[:2]
        if mode == "tracked_only" and prefix == "??":
            continue
        path = line[3:].strip()
        if " -> " in path:
            path = path.split(" -> ", 1)[1].strip()
        if path.startswith('"') and path.endswith('"') and len(path) >= 2:
            path = path[1:-1]
        if path:
            files.add(path)
    return files


def _normalize_to_relative(path: str, cwd: str) -> str:
    """絶対パスを`cwd`起点の相対パスへ正規化する。相対パスは`pathlib.Path`のみ適用する。"""
    if not path:
        return path
    p = pathlib.Path(path)
    if p.is_absolute() and cwd:
        try:
            return str(p.relative_to(pathlib.Path(cwd), walk_up=True))
        except ValueError:
            return str(p)
    return str(p)


def _check_bash_bulk_stage_with_unedited_files(
    command: str,
    session_id: str,
    payload_cwd: str,
) -> str | None:
    """一括ステージ実行時に自セッション未編集の変更が含まれる場合の警告文を返す。

    `git add -A/--all/.` は未追跡を含む集合、`git add -u/--update` と
    `git commit -a/--all/-am`等は追跡済みのみを対象として作業ツリー変更を判定する。
    セッション状態の`session_edited_files`集合との差集合が空でない場合、
    個別ファイル指定への切替を促すwarnをhookSpecificOutputで返す。
    """
    for event in extract_git_events(command, payload_cwd):
        mode = _detect_bulk_stage_mode(event)
        if mode is None:
            continue
        if not event.cwd_resolved:
            continue
        effective_cwd = event.cwd
        if not effective_cwd:
            continue
        try:
            proc = subprocess.run(
                ["git", "status", "--short"],
                cwd=effective_cwd,
                capture_output=True,
                text=True,
                check=False,
            )
        except (OSError, FileNotFoundError):
            continue
        if proc.returncode != 0:
            continue
        changed = _parse_git_status_short(proc.stdout, mode)
        if not changed:
            continue
        state = read_state(session_id)
        edited_raw = state.get("session_edited_files", []) or []
        edited: set[str] = set()
        for entry in edited_raw:
            if isinstance(entry, str) and entry:
                edited.add(_normalize_to_relative(entry, effective_cwd))
        changed_norm = {_normalize_to_relative(p, effective_cwd) for p in changed}
        unedited = changed_norm - edited
        if not unedited:
            continue
        sample = sorted(unedited)[:5]
        return _llm_notice(
            "warn: bulk staging includes files with no recorded edit by the file edit tools"
            " in this session. Files changed by shell commands or generators are not recorded,"
            " so confirm ownership before staging."
            f" Candidates: {sample}."
            " Consider switching to per-file staging (`git add <file>`).",
            tag="warn",
        )
    return None


# --- Bash: uv run python <path>形式の起動ブロック ---

# 副作用の理由:
# cwd又はその祖先で最初に見つかるpyproject.tomlが[tool.uv]のみで
# [project]セクションを持たない場合、`uv run python <path>`は当該ディレクトリを
# プロジェクト解決対象として扱い`.venv`と`uv.lock`を生成する（uvの仕様）。
# エージェントがPEP 723スクリプトを誤って`uv run python <path>`形式で起動する
# 事故を予防的にblockする。
#
# 判定の優先順位:
#
# 1. `uv run`と`python`の間（uv run自身のオプション位置）に`--script`または
#    `--no-project`が現れる場合は許容する（cwdの依存解決を行わないため副作用なし）。
# 2. cwd変更経路（Bashの`cd` / `pushd`先行・`uv --directory` / `uv --project`）
#    の実効cwdが解決済みで、cwd又はその祖先で最初に見つかるpyproject.tomlが
#    [project]セクションを持つPythonプロジェクトの場合は許容する
#    （`uv run python -c '...'`等の正規利用を妨げない）。
# 3. それ以外はblockする。
#
# cwd変更経路の引数にシェル展開が含まれる場合は、実効cwdとPythonプロジェクトの
# 種別を静的に確定できないため、プロジェクト判定を行わずblock側に倒す。
# 環境変数経由のcwd / project切り替え（UV_WORKING_DIR / UV_PROJECT）は
# 利用頻度が低く実装コストに見合わないため対応スコープ外とする。

_UV_RUN_PYTHON_BLOCK_MSG = (
    "blocked: `uv run python` invocation without `--script` or `--no-project`"
    " before the `python` token"
    " (applies regardless of whether a path or `-c` follows `python`)."
    " In a non-Python project (pyproject.toml without a [project] section, or absent),"
    " uv treats the cwd as a project and generates `.venv` and `uv.lock` as a side effect."
    " The invocation cannot safely continue without an explicit project-independent form."
)

_UV_RUN_PYTHON_FIX = (
    "For a PEP 723 script, use `uv run --script <path>` or invoke the executable shebang directly;"
    " to skip cwd project resolution, use `uv run --no-project python ...`;"
    " otherwise run it from a directory where the first `pyproject.toml` found in the cwd or its ancestors"
    " has a `[project]` section. A statically resolvable `cd` target is evaluated as the effective working directory."
    " An unresolved shell expansion in a cwd change blocks this invocation because the project type cannot be confirmed."
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
    segments = split_bash_segments(command)
    current_cwd = CwdResolution(cwd, bool(cwd))
    for segment in segments:
        try:
            tokens = shlex.split(segment, posix=True)
        except ValueError:
            return False
        cwd_change = resolve_cwd_change(tokens, current_cwd)
        if cwd_change is not None:
            current_cwd = cwd_change
            continue
        info = _parse_uv_run_python(tokens)
        if info is not None:
            has_script_or_no_project, directory_or_project_overridden = info
            if not has_script_or_no_project and (
                directory_or_project_overridden or not current_cwd.resolved or not _cwd_in_python_project(current_cwd.path)
            ):
                print(_block_notice(_UV_RUN_PYTHON_BLOCK_MSG, fix=_UV_RUN_PYTHON_FIX), file=sys.stderr)
                return True
    return False


def _skip_env_assignments(tokens: list[str], start: int) -> int:
    """先頭の`KEY=VALUE`形式の環境変数代入をスキップした次の位置を返す。"""
    i = start
    while i < len(tokens) and _ENV_ASSIGN_PATTERN.match(tokens[i]):
        i += 1
    return i


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


def _cwd_in_python_project(cwd: str) -> bool:
    """cwdから祖先方向へ最初に見つかる`pyproject.toml`が`[project]`を持つ場合に真を返す。

    uvのプロジェクト解決と同じ探索順序に合わせる。直近の`pyproject.toml`が`[project]`を
    欠く場合、uvは当該ディレクトリへ`.venv`と`uv.lock`を生成するため偽を返す。
    祖先まで見つからない場合と読み込みに失敗した場合も偽を返す。
    """
    if not cwd:
        return False
    cwd_path = pathlib.Path(cwd)
    for directory in (cwd_path, *cwd_path.parents):
        try:
            text = (directory / "pyproject.toml").read_text(encoding="utf-8", errors="replace")
        except FileNotFoundError:
            continue
        except (OSError, ValueError):
            return False
        return _PYPROJECT_PROJECT_SECTION_PATTERN.search(text) is not None
    return False


# --- Bash: 実行位置のトークン列抽出（助言用検査の共通入口）---

# 本ヘルパーの消費主体は助言用の3検査（出力切り詰め・`codex exec`・agent-toolkit版更新漏れ）に限る。
# 遮断を伴う`_check_bash_process_kill_by_pattern`は、コマンド置換・サブシェル・オプション終端まで
# 解決できる解析を用意できるまで現行のコマンド文字列全体への一致判定を維持し、本ヘルパーを使わない
# （解析の不足で既存の保護を外さないため）。

_EXEC_PREFIX_WITH_ENV_ASSIGNMENTS: frozenset[str] = frozenset({"sudo", "env"})
"""続く`KEY=VALUE`形式の代入を走査対象から除く実行前置語。

`-`始まりトークンが続く場合は、引数を取るか否かが実装・版により異なり値の境界を確定できないため、
当該区間を実行位置未確定として扱う。
"""

_EXEC_PREFIX_WITHOUT_OPTIONS: frozenset[str] = frozenset({"command", "nohup", "uvx", "xargs"})
"""次のトークンを実行位置候補とする実行前置語。

`-`始まりトークンが続く場合は`_EXEC_PREFIX_WITH_ENV_ASSIGNMENTS`と同じ理由で実行位置未確定とする。
"""

_TIMEOUT_DURATION_RE = re.compile(r"^\d+(?:\.\d+)?[smhd]?$")

_SHELL_TOKENS: frozenset[str] = frozenset({"sh", "bash"})

_UV_TERMINAL_OPTIONS: frozenset[str] = frozenset({"--help", "-h", "--version", "-V"})
"""後続の指定を実行しない終端オプション。走査中のコマンド自身を実行位置として確定する。"""

_UV_GLOBAL_OPTIONS_WITH_VALUE: frozenset[str] = frozenset(
    {
        "--allow-insecure-host",
        "--cache-dir",
        "--color",
        "--config-file",
        "--directory",
        "--project",
    }
)
"""`uv --help`（uv 0.12.3）の出力から機械抽出した、値を1つ取るグローバルオプション。

長形と短縮形の双方を保持する。`sudo`・`env`・`xargs`・`timeout`が表を持たず`-`始まりトークンで
一律に実行位置未確定へ倒すのに対し、`uv`だけがオプション表を持つのは、導入版の`--help`出力から
オプション全体を一次資料として取得できるためである。
表に無い`-`始まりトークンは意味を確定できないため当該区間を実行位置未確定として扱う。
uvの新版でオプションが増減した場合は、`uv --help`と`uv run --help`の出力から本表と関連3表を再作成する。
"""

_UV_GLOBAL_OPTIONS_WITHOUT_VALUE: frozenset[str] = frozenset(
    {
        "--managed-python",
        "--no-cache",
        "--no-config",
        "--no-managed-python",
        "--no-progress",
        "--no-python-downloads",
        "--offline",
        "--quiet",
        "--system-certs",
        "--verbose",
        "-n",
        "-q",
        "-v",
    }
)
"""`uv --help`の出力から機械抽出した、値を取らないグローバルオプション。取得元は`_UV_GLOBAL_OPTIONS_WITH_VALUE`参照。"""

_UV_RUN_OPTIONS_WITH_VALUE: frozenset[str] = frozenset(
    {
        "--allow-insecure-host",
        "--cache-dir",
        "--color",
        "--config-file",
        "--config-setting",
        "--config-settings-package",
        "--default-index",
        "--directory",
        "--env-file",
        "--exclude-newer",
        "--exclude-newer-package",
        "--extra",
        "--extra-index-url",
        "--find-links",
        "--fork-strategy",
        "--group",
        "--index",
        "--index-strategy",
        "--index-url",
        "--keyring-provider",
        "--link-mode",
        "--no-binary-package",
        "--no-build-isolation-package",
        "--no-build-package",
        "--no-editable-package",
        "--no-extra",
        "--no-group",
        "--no-sources-package",
        "--only-group",
        "--package",
        "--prerelease",
        "--prerelease-package",
        "--project",
        "--python",
        "--python-platform",
        "--refresh-package",
        "--reinstall-package",
        "--resolution",
        "--upgrade-group",
        "--upgrade-package",
        "--with",
        "--with-editable",
        "--with-requirements",
        "-C",
        "-P",
        "-f",
        "-i",
        "-p",
        "-w",
    }
)
"""`uv run --help`の出力から機械抽出した、値を1つ取る`run`オプション。取得元は`_UV_GLOBAL_OPTIONS_WITH_VALUE`参照。"""

_UV_RUN_OPTIONS_WITHOUT_VALUE: frozenset[str] = frozenset(
    {
        "--active",
        "--all-extras",
        "--all-groups",
        "--all-packages",
        "--compile-bytecode",
        "--exact",
        "--frozen",
        "--gui-script",
        "--isolated",
        "--locked",
        "--managed-python",
        "--module",
        "--no-binary",
        "--no-build",
        "--no-build-isolation",
        "--no-cache",
        "--no-config",
        "--no-default-groups",
        "--no-dev",
        "--no-editable",
        "--no-env-file",
        "--no-index",
        "--no-managed-python",
        "--no-progress",
        "--no-project",
        "--no-python-downloads",
        "--no-sources",
        "--no-sync",
        "--offline",
        "--only-dev",
        "--quiet",
        "--refresh",
        "--reinstall",
        "--script",
        "--system-certs",
        "--upgrade",
        "--verbose",
        "-U",
        "-m",
        "-n",
        "-q",
        "-s",
        "-v",
    }
)
"""`uv run --help`の出力から機械抽出した、値を取らない`run`オプション。取得元は`_UV_GLOBAL_OPTIONS_WITH_VALUE`参照。"""


_PIPE_SEPARATORS: frozenset[str] = frozenset({"|", "|&"})
"""前後の区間を同一パイプラインへ属させる区切り演算子。

`;`・`&&`・`||`・`&`は前段の標準出力を後段へ渡さないため、別のパイプラインの開始として扱う。
"""


@dataclasses.dataclass(frozen=True)
class _ExecutionSegment:
    """Bashコマンドの1区間について、実行位置以降のトークン列と実行位置の確定可否を表す。

    `resolved`が偽の区間では`tokens`を空とし、助言用検査は当該区間で検出しない。
    `is_agent_toolkit_script`はagent-toolkit配下の配布検査スクリプトを表す。
    """

    tokens: tuple[str, ...]
    resolved: bool
    is_agent_toolkit_script: bool = False


def _split_bash_pipelines(command: str) -> list[list[str]]:
    """`split_bash_segments`の分割結果を、パイプラインごとの区間列へまとめて返す。

    分割そのものは`split_bash_segments`を正本とし、本関数は境界の分類とまとめ直しだけを行う。
    各区間の元コマンド内の位置を先頭から順に求め、区間の間に残る文字列（空白と区切り演算子だけからなる）で
    同一パイプラインの継続かを判定する。位置を求められない場合は継続とみなさない。
    """
    pipelines: list[list[str]] = []
    segments = split_bash_segments(command)
    position = 0
    for index, segment in enumerate(segments):
        start = command.find(segment, position)
        separator_text = command[position:start] if start >= 0 else ""
        separator = separator_text.strip()
        previous = segments[index - 1] if index > 0 else ""
        if pipelines and _is_redirection_continuation(previous, separator_text, segment):
            pipelines[-1][-1] += separator_text + segment
            position = (start if start >= 0 else position) + len(segment)
            continue
        if index == 0 or not _is_pipeline_continuation(previous, separator, segment):
            pipelines.append([])
        pipelines[-1].append(segment)
        position = (start if start >= 0 else position) + len(segment)
    return pipelines


def _is_pipeline_continuation(previous: str, separator: str, following: str) -> bool:
    """区間の境界が同一パイプラインの継続であるかを、前後の区間と区切り文字列から判定する。"""
    if separator in _PIPE_SEPARATORS:
        return True
    # `2>&1`・`&>log`等のリダイレクトに含まれる`&`は、`split_bash_segments`が区切りとして分割するが
    # コマンドの終端ではないため継続として扱う（前段の出力は後段のパイプへ渡る）。
    return separator == "&" and (previous.endswith((">", "<")) or following.startswith(">"))


def _is_redirection_continuation(previous: str, separator: str, following: str) -> bool:
    """`split_bash_segments`が分割したリダイレクト断片の続きであるかを返す。"""
    if separator.strip() != "&":
        return False
    if previous.endswith((">", "<")):
        return True
    return following.startswith(">") and separator.endswith("&")


def _extract_execution_pipelines(command: str, *, expand_shell: bool = True) -> list[list[_ExecutionSegment]]:
    """Bashコマンドをパイプライン単位へ分割し、各パイプラインの区間列を実行順で返す。

    1つのパイプラインは`|`だけで連結された一続きの区間列であり、前段の標準出力が後段へ渡る。
    `;`・`&&`・`||`・`&`は出力を渡さないため別のパイプラインとして分ける。
    前段の出力が後段へ渡るか否かを要件とする検査は、同じパイプライン内の前後関係だけを見ればよい。

    区間分割は`split_bash_segments`（`;`・`&&`・`||`・`|`・`&`で分割し、クォート内のメタ文字を除く）、
    トークン化は`shlex.split(segment, posix=True)`を使う。
    実行前置語（`sudo`・`env`・`uv run`等）を解決した後の実行位置が`sh -c`・`bash -c`
    （`-lc`等の結合形を含む）である場合、続く文字列引数を1段だけ同じ手順で展開する。
    2段以上の入れ子は展開せず実行位置未確定とする。
    展開結果が1つのパイプラインへ収まる場合は、上流・下流とも呼び出し元のパイプラインへ連結する。
    内側が`;`・`&&`等により複数の文へ分かれる場合、上流と下流を非対称に扱う。

    - 下流（`sh -c '...' | 後続`）は連結する。内側の各文は同じ標準出力を継承し実行順に書き込むため、
      どの文の出力も後続へ渡る。内側の各文それぞれの末尾へ後続の区間列を複製して連結する
    - 上流（`前段 | sh -c '...'`）は連結しない。渡された標準入力をどの文が消費するかは
      実行時の消費順に依存し、静的なトークン列の解析では確定できないため、当該区間を実行位置未確定とする

    本ヘルパーはコマンド置換・サブシェル・`--`によるオプション終端・前置語の値境界を解決しない。
    この解析水準で成立するのは、実行を止めない助言用の判定に限る。
    """
    pipelines: list[list[_ExecutionSegment]] = []
    for raw_pipeline in _split_bash_pipelines(command):
        pipelines.extend(_resolve_pipeline(raw_pipeline, expand_shell=expand_shell))
    return [pipeline for pipeline in pipelines if pipeline]


def _resolve_pipeline(raw_segments: Sequence[str], *, expand_shell: bool) -> list[list[_ExecutionSegment]]:
    """1つのパイプラインの区間列を解決する。

    戻り値の先頭は当該パイプライン自身であり、2件目以降は`sh -c`展開により生じた独立したパイプラインとする。
    展開の接続規則は`_extract_execution_pipelines`のdocstringが定める。
    """
    current: list[_ExecutionSegment] = []
    for index, raw_segment in enumerate(raw_segments):
        try:
            tokens = shlex.split(raw_segment, posix=True)
        except ValueError:
            current.append(_ExecutionSegment((), False))
            continue
        segment = _resolve_execution_segment(tokens)
        shell_argument = _shell_c_argument(segment.tokens) if segment.resolved else None
        if shell_argument is None:
            current.append(segment)
            continue
        if not expand_shell:
            current.append(_ExecutionSegment((), False))
            continue
        inner = _extract_execution_pipelines(shell_argument, expand_shell=False)
        if len(inner) <= 1:
            current.extend(inner[0] if inner else ())
            continue
        # 内側が複数の文へ分かれる場合、上流は接続せず当該区間を実行位置未確定とする。
        # 下流の区間列は内側の各文へ複製して連結し、それぞれを独立したパイプラインとする。
        current.append(_ExecutionSegment((), False))
        rest = _resolve_pipeline(raw_segments[index + 1 :], expand_shell=expand_shell)
        downstream = rest[0] if rest else []
        return [current, *(inner_pipeline + downstream for inner_pipeline in inner), *rest[1:]]
    return [current]


def _extract_execution_segments(command: str) -> list[_ExecutionSegment]:
    """Bashコマンドの全区間を実行順の一次元列で返す。

    パイプラインの区切りを要件としない検査（実行位置の一致だけを判定する検査）が使う。
    """
    return [segment for pipeline in _extract_execution_pipelines(command) for segment in pipeline]


def _shell_c_argument(tokens: Sequence[str]) -> str | None:
    """実行位置以降のトークン列が`sh -c`・`bash -c`形式であれば、実行するコマンド文字列を返す。

    該当しない場合はNoneを返す。判定対象は実行前置語を解決した後のトークン列であり、
    `sudo sh -c '...'`のように前置語と組み合わせた形も展開対象となる。
    """
    if not tokens or tokens[0] not in _SHELL_TOKENS:
        return None
    for position in range(1, len(tokens)):
        token = tokens[position]
        if not token.startswith("-") or token.startswith("--"):
            return None
        if "c" in token[1:]:
            return tokens[position + 1] if position + 1 < len(tokens) else None
    return None


def _resolve_execution_segment(tokens: list[str]) -> _ExecutionSegment:
    """トークン列の実行位置を求め、実行位置以降のトークン列と確定可否を返す。

    先頭の`KEY=VALUE`形式の環境変数代入の次の位置から、既知の実行前置語を順に走査対象から除く。
    除いた後の位置が存在しない場合、または当該トークンが`-`で始まる場合は、
    前置語の引数境界を確定できていないため実行位置未確定とする。
    """
    index = _skip_env_assignments(tokens, 0)
    is_agent_toolkit_script = False
    while index < len(tokens):
        token = tokens[index]
        if token in _EXEC_PREFIX_WITH_ENV_ASSIGNMENTS:
            index = _skip_env_assignments(tokens, index + 1)
            continue
        if token in _EXEC_PREFIX_WITHOUT_OPTIONS:
            index += 1
            continue
        if token == "timeout":
            index += 1
            if index < len(tokens) and _TIMEOUT_DURATION_RE.match(tokens[index]):
                index += 1
            continue
        if token == "uv":
            uv_index = _resolve_uv_execution_index(tokens, index)
            if uv_index is None:
                return _ExecutionSegment((), False)
            if uv_index == index:
                break
            is_agent_toolkit_script = _is_agent_toolkit_script_invocation(tokens, index, uv_index)
            index = uv_index
            continue
        if _is_python_token(token) and index + 1 < len(tokens) and tokens[index + 1] == "-m":
            index += 2
            continue
        break
    if index >= len(tokens) or tokens[index].startswith("-"):
        return _ExecutionSegment((), False)
    return _ExecutionSegment(tuple(tokens[index:]), True, is_agent_toolkit_script)


def _resolve_uv_execution_index(tokens: list[str], uv_index: int) -> int | None:
    """`uv`トークンの位置から実行位置の添字を求める。実行位置未確定の場合はNoneを返す。

    `uv`のグローバル区間と`run`区間へ同じ優先順位の走査（`_scan_uv_options`）を適用する。
    終端オプションを含む区間と`run`以外のサブコマンドは、`uv`自身を実行位置として確定する
    （検証コマンド・`codex exec`のいずれとも一致しないため検出対象にならない）。
    """
    index, state = _scan_uv_options(tokens, uv_index + 1, _UV_GLOBAL_OPTIONS_WITH_VALUE, _UV_GLOBAL_OPTIONS_WITHOUT_VALUE)
    if state == "terminal":
        return uv_index
    if state != "reached":
        return None
    if tokens[index] != "run":
        return uv_index
    index, state = _scan_uv_options(tokens, index + 1, _UV_RUN_OPTIONS_WITH_VALUE, _UV_RUN_OPTIONS_WITHOUT_VALUE)
    if state == "terminal":
        return uv_index
    if state != "reached":
        return None
    return index


def _is_agent_toolkit_script_invocation(tokens: Sequence[str], uv_index: int, execution_index: int) -> bool:
    """`uv run --script`のagent-toolkit配下Pythonスクリプトを識別する。"""
    index, state = _scan_uv_options(list(tokens), uv_index + 1, _UV_GLOBAL_OPTIONS_WITH_VALUE, _UV_GLOBAL_OPTIONS_WITHOUT_VALUE)
    if state != "reached" or index >= len(tokens) or tokens[index] != "run":
        return False
    run_index = index
    index, state = _scan_uv_options(list(tokens), run_index + 1, _UV_RUN_OPTIONS_WITH_VALUE, _UV_RUN_OPTIONS_WITHOUT_VALUE)
    if state != "reached" or index != execution_index:
        return False
    script_path = tokens[execution_index]
    normalized = script_path.replace("\\", "/")
    components = tuple(part for part in normalized.split("/") if part)
    run_options = tokens[run_index + 1 : execution_index]
    return (
        any(option in run_options for option in ("--script", "-s"))
        and "agent-toolkit" in components
        and normalized.endswith(".py")
    )


def _scan_uv_options(
    tokens: list[str],
    start: int,
    with_value: frozenset[str],
    without_value: frozenset[str],
) -> tuple[int, str]:
    """`uv`のオプション列を走査し、到達位置と走査結果の状態を返す。

    解析の前提は「意味を確定できる構文だけを受理する」ことであり、個別のオプション名を事象ごとに追加しない。
    各トークンは次の5状態のいずれか1つへ排他的に定まる。判定はこの優先順位で行い、
    先に一致した状態で確定して以降の状態を評価しない。

    1. 終端状態: 終端オプション。当該区間は後続の指定を実行しないため走査を終える（状態`terminal`）
    2. 値あり状態: 値ありオプション表と完全一致する。トークンと続く1トークンを走査対象から除く。
       `--name=value`形式は`--name`が同表と完全一致する場合に1トークンだけを除く
    3. 値なし状態: 値なしオプション表と完全一致する。トークン1つを除く
    4. 非オプション状態: `-`で始まらない。当該トークンを走査の到達点とする（状態`reached`）
    5. 未分類状態: 上記のいずれにも当たらない（表に無い長形、2文字以上の結合短縮形、表に無い短縮形など）。
       区間全体を実行位置未確定とする（状態`unresolved`）

    5状態は排他かつ網羅であり、優先順位が固定されているため同じトークンが2つの状態へ当たることはない。
    値なしオプション表に`--help`・`-h`が含まれていても、終端状態を最優先で判定するため状態1で確定する。
    新しいオプションや未知の記法が現れても個別の規則追加を要さず状態5へ倒れ、助言用検査は非検出となる。
    """
    index = start
    while index < len(tokens):
        token = tokens[index]
        if token in _UV_TERMINAL_OPTIONS:
            return index, "terminal"
        if token in with_value:
            index += 2
            continue
        name, separator, _ = token.partition("=")
        if separator and name in with_value:
            index += 1
            continue
        if token in without_value:
            index += 1
            continue
        if not token.startswith("-"):
            return index, "reached"
        return index, "unresolved"
    return index, "unresolved"


# --- Bash: 固定sleep後に処理が続く前景待機の検出 ---

_SLEEP_COMMAND = "sleep"
_LONG_SLEEP_SECONDS = 30
"""前景の固定待機として扱う`sleep`の秒数。

観測した固定待機は420秒から570秒であり、この範囲を後続コマンドの種類によらず検出する。
一方で短い待機は処理の一部として用いられるため、既存の通過検体`sleep 5`を含む範囲は
読み取り専用の状態確認コマンドが続く場合だけを検出対象とする。
"""
_LOOP_KEYWORDS = frozenset({"until", "while", "for"})
_LOOP_END_KEYWORD = "done"
_POLL_COMMAND_PREFIXES = (
    ("ls",),
    ("cat",),
    ("git", "status"),
    ("gh", "run", "view"),
    ("gh", "run", "watch"),
    ("ps",),
    ("atk", "mq", "list"),
    ("atk", "mq", "show"),
    ("systemctl", "status"),
    ("systemctl", "is-active"),
)
_CURL_COMMAND = ("curl",)
_CURL_DATA_SHORT_OPTIONS = ("-d", "-F", "-T")
_CURL_DATA_LONG_OPTIONS = (
    "--data",
    "--data-raw",
    "--data-binary",
    "--data-ascii",
    "--data-urlencode",
    "--form",
    "--form-string",
    "--upload-file",
    "--json",
)
_CURL_METHOD_SHORT_OPTION = "-X"
_CURL_METHOD_LONG_OPTION = "--request"
_CURL_READ_ONLY_METHODS = frozenset({"GET", "HEAD"})
_CURL_NEXT_OPTIONS = ("--next", "-:")


def _split_curl_operations(args: Sequence[str]) -> list[list[str]]:
    """`--next`・独立トークンの`-:`で区切られた操作単位へトークン列を分割する。

    簡略化: `-:`が他の短縮オプションと結合した形は区切りとして検出しない,
    既知の限界: curlの短縮オプションクラスタを完全には解析しない,
    見直し契機: 結合形を使う書込みcurlの見逃しを実測した場合
    """
    operations: list[list[str]] = [[]]
    for arg in args:
        if arg in _CURL_NEXT_OPTIONS:
            operations.append([])
            continue
        operations[-1].append(arg)
    return operations


def _curl_args_have_write_indicator(args: Sequence[str]) -> bool:
    """curlの引数列に書込みを示す操作が1件以上含まれるかを判定する。"""
    return any(_curl_operation_has_write_indicator(operation) for operation in _split_curl_operations(args))


def _curl_operation_has_write_indicator(args: Sequence[str]) -> bool:
    """`--next`で区切った1操作にデータ送信または書込みHTTPメソッドがあるかを判定する。"""
    for arg in args:
        if any(arg == option or arg.startswith(option) for option in _CURL_DATA_SHORT_OPTIONS):
            return True
        if any(arg == option or arg.startswith(f"{option}=") for option in _CURL_DATA_LONG_OPTIONS):
            return True

    last_method: str | None = None
    for index, arg in enumerate(args):
        method = _extract_curl_method_value(args, index, arg)
        if method is not None:
            last_method = method
    return last_method is not None and last_method.upper() not in _CURL_READ_ONLY_METHODS


def _extract_curl_method_value(args: Sequence[str], index: int, arg: str) -> str | None:
    """`-X`・`--request`のHTTPメソッド値を連結形・分離形・`=`結合形から抽出する。"""
    if arg in (_CURL_METHOD_SHORT_OPTION, _CURL_METHOD_LONG_OPTION):
        return args[index + 1] if index + 1 < len(args) else ""
    if arg.startswith(_CURL_METHOD_SHORT_OPTION) and len(arg) > len(_CURL_METHOD_SHORT_OPTION):
        return arg[len(_CURL_METHOD_SHORT_OPTION) :]
    if arg.startswith(f"{_CURL_METHOD_LONG_OPTION}="):
        return arg[len(_CURL_METHOD_LONG_OPTION) + 1 :]
    return None


_WORD_BOUNDARY_CONTROL_CHARS = ("&", "|")
"""単体で単語境界となるBash制御演算子。このうち`;`・`&&`は別途区切りとして処理する。

`&`・`|`単体は本関数の分割対象ではないが、直後に空白無しで`#`が続く場合はコメント開始として
認識する必要がある（例: `sleep 0&#comment`は`&`直後がコメント開始）。
`(`・`)`は含めない。コマンド置換`$(...)`・算術展開`$((...))`・プロセス置換
`<(...)`・`>(...)`等の閉じ括弧は制御演算子ではなく式構文の一部であり、
直後の文字は同一単語の続きとなる（単語境界にならない）。サブシェルを開閉する
制御演算子としての単体`(`・`)`と展開構文中の`(`・`)`を区別する構文解析は本関数の
対象外とし、閉じ括弧を無条件に単語境界とすることによる誤検出を避けるため対象から除く。
"""


_SERIAL_SHELL_SEPARATORS: frozenset[str] = frozenset({";", "&&"})
_STATUS_SHELL_SEPARATORS: frozenset[str] = frozenset({";", "&&", "||", "&"})


def _split_serial_shell_commands(
    command: str,
    *,
    separators: frozenset[str] = _SERIAL_SHELL_SEPARATORS,
) -> list[str]:
    """指定したクォート外のシェル演算子でBash入力を直列コマンドへ分割する。

    クォート外の`#`（Bashコメント開始）から行末までをスキップし、
    コメント内の演算子を区切りとして誤検出しない。
    """
    segments: list[str] = []
    buffer: list[str] = []
    quote: str | None = None
    escaped = False
    # 現在位置が新しい単語を開始し得る位置か（行頭・エスケープなし空白直後・制御演算子直後）。
    # エスケープされた空白は単語を区切らないため、直前文字の生の空白判定だけでは
    # `foo\ #literal`のような字面をコメント開始と誤認する。
    word_boundary = True
    index = 0
    while index < len(command):
        char = command[index]
        if escaped:
            buffer.append(char)
            escaped = False
            word_boundary = False
            index += 1
            continue
        if char == "\\" and quote != "'" and command.startswith("\\\n", index):
            # 行継続（バックスラッシュ改行）はBash仕様上、入力から完全に除去され前後を
            # 単純連結する。バッファへは何も追加せず、単語境界の状態も変化させない
            # （行継続前の空白直後であれば、継続後も引き続き単語境界のままとなる）
            index += 2
            continue
        if char == "\\" and quote != "'":
            buffer.append(char)
            escaped = True
            word_boundary = False
            index += 1
            continue
        if quote is not None:
            buffer.append(char)
            if char == quote:
                quote = None
            word_boundary = False
            index += 1
            continue
        if char in {"'", '"'}:
            quote = char
            buffer.append(char)
            word_boundary = False
            index += 1
            continue
        # クォート外の`#`を検出した場合、単語先頭位置に限り行末までをスキップする
        # （Bash仕様では`#`は単語の先頭にある場合だけコメント開始）
        if char == "#" and word_boundary:
            newline_index = command.find("\n", index)
            if newline_index < 0:
                # 改行が無い場合はそのまま終了
                break
            # 改行のみを次の処理へ渡す
            index = newline_index
            word_boundary = True
            continue
        if char == "\n":
            buffer.append(char)
            word_boundary = True
            index += 1
            continue
        if char in (" ", "\t"):
            buffer.append(char)
            word_boundary = True
            index += 1
            continue
        separator_length = 0
        if command.startswith("&&", index) and "&&" in separators or command.startswith("||", index) and "||" in separators:
            separator_length = 2
        elif (
            char == ";"
            and ";" in separators
            or (
                char == "&"
                and "&" in separators
                and not command.startswith("&>", index)
                and not (index > 0 and command[index - 1] in "<>|")
            )
        ):
            separator_length = 1
        if separator_length:
            segments.append("".join(buffer).strip())
            buffer = []
            word_boundary = True
            index += separator_length
            continue
        if char in _WORD_BOUNDARY_CONTROL_CHARS:
            buffer.append(char)
            word_boundary = True
            index += 1
            continue
        buffer.append(char)
        word_boundary = False
        index += 1
    segments.append("".join(buffer).strip())
    return [segment for segment in segments if segment]


def _is_long_fixed_sleep(command: str) -> bool:
    """コマンドが閾値以上の数値リテラルを与える`sleep`単体であるかを判定する。"""
    args = _command_tokens(command)
    if args is None:
        return False
    if len(args) != 2 or args[0] != _SLEEP_COMMAND:
        return False
    try:
        seconds = float(args[1])
    except ValueError:
        return False
    return seconds >= _LONG_SLEEP_SECONDS


def _command_tokens(command: str) -> list[str] | None:
    """制御構文の接頭予約語を除いたコマンドトークンを返す。"""
    try:
        args = shlex.split(command, posix=True)
    except ValueError:
        return None
    while args and args[0] in {"do", "then", "else"}:
        args = args[1:]
    return args


def _command_tokens_with_quotes(command: str) -> list[str] | None:
    """クォートを保持したコマンドトークンを返す。"""
    try:
        args = shlex.split(command, posix=False)
    except ValueError:
        return None
    while args and args[0] in {"do", "then", "else"}:
        args = args[1:]
    return args


def _first_token(command: str) -> str | None:
    """コマンドの先頭トークンを返す（分割できない場合と空の場合はNone）。"""
    try:
        args = shlex.split(command, posix=True)
    except ValueError:
        return None
    return args[0] if args else None


def _starts_loop_keyword(command: str) -> bool:
    """コマンドの先頭トークンが条件ループの予約語であるかを判定する。"""
    tokens = _command_tokens(command) or []
    return bool(tokens) and tokens[0] in _LOOP_KEYWORDS


def _loop_scope_flags(segments: list[str]) -> list[bool]:
    """各セグメントがループ予約語から対応する`done`までの範囲に属するかを返す。

    ループを開くセグメントと対応する`done`のセグメント自身も範囲に含める。
    `done`が現れないまま入力が終わる場合は、末尾までを当該ループの範囲として扱う。
    """
    flags: list[bool] = []
    depth = 0
    for segment in segments:
        if _starts_loop_keyword(segment):
            depth += 1
            flags.append(True)
            continue
        flags.append(depth > 0)
        tokens = _command_tokens(segment) or []
        if depth > 0 and tokens and tokens[0] == _LOOP_END_KEYWORD:
            depth -= 1
    return flags


def _polling_loop_body_flags(segments: list[str]) -> list[bool]:
    """入れ子でなく早期離脱を持たない単純ポーリングループの本体範囲を返す。"""
    flags = [False] * len(segments)
    start: int | None = None
    eligible = False
    nested = False
    has_early_exit = False
    depth = 0
    for index, segment in enumerate(segments):
        tokens = _command_tokens(segment) or []
        first = tokens[0] if tokens else None
        if first in _LOOP_KEYWORDS:
            if depth == 0:
                start = index
                eligible = first == "for" or (first == "while" and tokens[1:] in (["true"], [":"]))
                nested = False
                has_early_exit = False
            else:
                nested = True
            depth += 1
            continue
        if depth == 0:
            continue
        if first == _LOOP_END_KEYWORD:
            depth -= 1
            if depth == 0:
                if start is not None and eligible and not nested and not has_early_exit:
                    flags[start + 1 : index] = [True] * (index - start - 1)
                start = None
            continue
        if first in {"break", "exit", "return"}:
            has_early_exit = True
    if depth == 1 and start is not None and eligible and not nested and not has_early_exit:
        flags[start + 1 :] = [True] * (len(segments) - start - 1)
    return flags


def _is_sleep_poll_pair(left: str, right: str, *, previous: str | None = None) -> bool:
    """隣接する2コマンドがsleepと読み取り専用状態確認の組であるかを判定する。"""
    left_args = _command_tokens(left)
    right_args = _command_tokens(right)
    if left_args is None or right_args is None:
        return False
    if not left_args or left_args[0] != _SLEEP_COMMAND or not right_args:
        return False
    if previous is not None:
        previous_args = _command_tokens(previous) or []
        if previous_args and previous_args[0] == "kill" and right_args[0] == "ps" and "-p" in right_args[1:]:
            return False
    if any(tuple(right_args[: len(prefix)]) == prefix for prefix in _POLL_COMMAND_PREFIXES):
        return True
    return tuple(right_args[:1]) == _CURL_COMMAND and not _curl_args_have_write_indicator(right_args[1:])


def _has_foreground_sleep_wait(segments: list[str]) -> bool:
    """ループ本体の外にある`sleep`が検出条件を満たすかを判定する。

    除外の要否は`sleep`候補自身が属する範囲だけで決める。
    直後のセグメントは検出条件の判定にだけ用い、その所属は除外条件へ混ぜない。
    """
    in_loop_body = _loop_scope_flags(segments)
    polling_loop_body = _polling_loop_body_flags(segments)
    return any(
        (polling_loop_body[index] or not in_loop_body[index])
        and (
            _is_long_fixed_sleep(segments[index])
            or _is_sleep_poll_pair(
                segments[index],
                segments[index + 1],
                previous=segments[index - 1] if index > 0 else None,
            )
        )
        for index in range(len(segments) - 1)
    )


def _check_bash_sleep_poll_pattern(
    command: str,
    session_id: str,
    run_in_background: bool,
) -> str | None:
    """固定sleep後に処理が続く前景待機を初回warn、同一セッション内再検出でblockする。

    検出条件は、閾値以上の`sleep`の直後に任意のコマンドが続く形と、
    閾値未満の`sleep`の直後に読み取り専用の状態確認コマンドが続く形の2つとする。
    前者は待機後に続くコマンドの種類に依存しないため、状態確認コマンド名の追随保守を要しない。
    条件成立で抜けるループ（`until`・条件付き`while`）の本体は検出対象から除く。
    入れ子でない`for`・`while true`・`while :`の本体は、早期離脱が無い場合だけ検出対象とする。
    入れ子ループは検出対象から除く。
    当該範囲の外にある`sleep`は、同一のBash呼び出しにループが含まれる場合も通常どおり判定する。

    簡略化: クォート外の`;`・`&&`直列連結だけを検出する,
    既知の限界: サブシェルで包んだ状態確認は検出しない,
    見直し契機: サブシェル包みの反復ポーリングを実測した場合
    """
    if run_in_background:
        return None
    if not _has_foreground_sleep_wait(_split_serial_shell_commands(command)):
        return None

    already_detected = False

    def _record_detection(state: dict) -> dict | None:
        nonlocal already_detected
        already_detected = bool(state.get("sleep_poll_detected"))
        if already_detected:
            return None
        state["sleep_poll_detected"] = True
        return state

    update_state(session_id, _record_detection)
    guidance = (
        "Receive the completion notification, use a background job's machine-readable completion marker,\n"
        "or observe delegated work with `atk watch`, then end the turn with a waiting status."
    )
    if already_detected:
        print(
            _block_notice(
                "block: foreground sleep followed by another command was detected again in this session.",
                fix=guidance,
            ),
            file=sys.stderr,
        )
        return "block"
    return _llm_notice(
        f"warn: foreground sleep followed by another command may cause repeated polling.\n{guidance}",
        tag="warn",
    )


# --- Bash: パターン一致によるプロセス終了の検出 ---

_PROCESS_KILL_BY_PATTERN_RE = re.compile(r"(?<![\w-])(pkill|killall)(?![\w-])")


def _check_bash_process_kill_by_pattern(command: str) -> bool:
    """`pkill`・`killall`等パターン指定によるプロセス終了をブロックする。

    対象の所有権を確認できないパターン一致の一括終了は事故の危険があるため禁止する。
    自身が起動して識別子（PID）を確認したプロセスに対する`kill <PID>`形式は対象外とする。
    """
    if not _PROCESS_KILL_BY_PATTERN_RE.search(command):
        return False
    print(
        _block_notice(
            "blocked: pattern-based process termination (pkill/killall) is prohibited because"
            " process ownership cannot be verified.",
            fix="Use `kill <PID>` for a process you started and identified by PID instead.",
        ),
        file=sys.stderr,
    )
    return True


# --- Bash: 検証コマンド出力の切り詰め検出 ---

_VERIFICATION_COMMAND_PREFIXES: tuple[tuple[str, ...], ...] = (
    ("pyfltr",),
    ("pytest",),
    ("cargo", "test"),
    ("dotnet", "test"),
    ("npm", "test"),
    ("npm", "run", "test"),
    ("vitest",),
)
_OUTPUT_TRUNCATION_COMMANDS: frozenset[str] = frozenset({"head", "tail"})
_OUTPUT_FULL_SAVE_COMMAND = "tee"
_SHELL_REDIRECTION_PATTERN = re.compile(r"^(?:\d+)?(?:&>>|&>|<<<|<<|>>|<>|>&|<&|>\||>|<)")
_TEE_NON_FILE_OPERAND_PATTERN = re.compile(r"^(?:/dev/null|/dev/(?:stdin|stdout|stderr|tty)|/dev/fd/\d+|/proc/self/fd/\d+)/?$")
_MAKE_ASSIGNMENT_PATTERN = re.compile(r"^[^=\s]+?\s*(?:::=|:=|\?=|\+=|!=|=)")
_MAKE_OPTIONS_WITH_VALUE: frozenset[str] = frozenset(
    {
        "-C",
        "-E",
        "-f",
        "-I",
        "-o",
        "-W",
        "--directory",
        "--file",
        "--makefile",
        "--include-dir",
        "--old-file",
        "--assume-old",
        "--what-if",
        "--new-file",
        "--assume-new",
        "--eval",
    }
)
"""`make --help`で値を必須とする短長オプション（別名を含む）。

長形の`--name=value`形式は、走査側がオプション名と値を分離して判定する。
"""
_MAKE_LONG_OPTIONS: frozenset[str] = frozenset(
    {
        "--always-make",
        "--assume-new",
        "--assume-old",
        "--check-symlink-times",
        "--debug",
        "--directory",
        "--dry-run",
        "--environment-overrides",
        "--eval",
        "--file",
        "--help",
        "--ignore-errors",
        "--include-dir",
        "--jobs",
        "--just-print",
        "--keep-going",
        "--load-average",
        "--makefile",
        "--max-load",
        "--new-file",
        "--no-builtin-rules",
        "--no-builtin-variables",
        "--no-keep-going",
        "--no-print-directory",
        "--no-silent",
        "--old-file",
        "--output-sync",
        "--print-data-base",
        "--print-directory",
        "--question",
        "--quiet",
        "--recon",
        "--silent",
        "--stop",
        "--touch",
        "--trace",
        "--version",
        "--warn-undefined-variables",
        "--what-if",
    }
)
"""`make --help`に現れる長形オプション。GNU Makeの一意な省略解決に使う。"""


def _segment_starts_with(segment: _ExecutionSegment, prefix: tuple[str, ...]) -> bool:
    """区間の実行位置以降のトークン列が指定の接頭トークン列で始まるかを返す。"""
    return segment.resolved and segment.tokens[: len(prefix)] == prefix


def _tee_operand_is_non_regular_file(token: str) -> bool:
    """`tee`のoperandが既知の特殊出力先または既存の非通常ファイルかを返す。"""
    normalized = token.rstrip("/")
    if _TEE_NON_FILE_OPERAND_PATTERN.fullmatch(normalized):
        return True
    path = pathlib.Path(normalized)
    if not path.is_absolute():
        return False
    try:
        return path.exists() and not path.is_file()
    except OSError:
        return False


def _tee_saves_to_file(segment: _ExecutionSegment) -> bool:
    """`tee`区間に標準出力を保存するファイル引数があるかを返す。"""
    if not _segment_starts_with(segment, (_OUTPUT_FULL_SAVE_COMMAND,)):
        return False
    option_terminator = False
    tokens = segment.tokens[1:]
    index = 0
    while index < len(tokens):
        token = tokens[index]
        redirect_match = _SHELL_REDIRECTION_PATTERN.match(token)
        if redirect_match is not None:
            index += 1
            if redirect_match.end() == len(token):
                index += 1
            continue
        if _tee_operand_is_non_regular_file(token):
            index += 1
            continue
        if option_terminator:
            return True
        if token == "--":
            option_terminator = True
        elif token.startswith("-") and token != "-":
            pass
        else:
            return True
        index += 1
    return False


def _make_option_requires_value(token: str) -> bool:
    """`make`の短長オプションが別トークンの必須値を取るかを返す。"""
    if token in _MAKE_OPTIONS_WITH_VALUE:
        return True
    if not token.startswith("--") or "=" in token:
        return False
    matches = tuple(option for option in _MAKE_LONG_OPTIONS if option.startswith(token))
    return len(matches) == 1 and matches[0] in _MAKE_OPTIONS_WITH_VALUE


def _make_targets(segment: _ExecutionSegment) -> tuple[str, ...]:
    """`make`区間からオプションと変数代入を除いたターゲット名を返す。"""
    if not _segment_starts_with(segment, ("make",)):
        return ()
    targets: list[str] = []
    option_terminator = False
    tokens = iter(segment.tokens[1:])
    for token in tokens:
        if _MAKE_ASSIGNMENT_PATTERN.match(token):
            continue
        if option_terminator:
            targets.append(token)
            continue
        if token == "--":
            option_terminator = True
            continue
        if token.startswith("-"):
            if _make_option_requires_value(token):
                next(tokens, None)
            continue
        targets.append(token)
    return tuple(targets)


def _segment_is_verification(segment: _ExecutionSegment) -> bool:
    """区間が検証コマンドの実行位置から始まるかを返す。"""
    return (
        any(_segment_starts_with(segment, prefix) for prefix in _VERIFICATION_COMMAND_PREFIXES)
        or segment.is_agent_toolkit_script
        or any(target.lower().find(keyword) >= 0 for target in _make_targets(segment) for keyword in ("test", "check", "lint"))
    )


def _pipeline_truncates_verification_output(pipeline: Sequence[_ExecutionSegment]) -> bool:
    """1つのパイプライン内で、検証コマンドの出力が全量保存されないまま切り詰められるかを判定する。

    検証コマンドより後方で最初に現れる`tail`・`head`を切り詰めの発生点とし、
    その手前に`tee`が無い場合に真を返す。`tee`で全量を先に保存してから抽出する形は対象外とし、
    切り詰めた後に`tee`で保存する形は保存内容が既に切り詰め後であるため対象とする。
    同一パイプラインに検証コマンドが複数ある場合は、いずれか1件でも該当すれば真を返す。
    """
    for index, segment in enumerate(pipeline):
        if not _segment_is_verification(segment):
            continue
        following = pipeline[index + 1 :]
        truncation_index = next(
            (
                position
                for position, item in enumerate(following)
                if item.resolved and item.tokens[0] in _OUTPUT_TRUNCATION_COMMANDS
            ),
            None,
        )
        if truncation_index is None:
            continue
        if any(_tee_saves_to_file(item) for item in following[:truncation_index]):
            continue
        return True
    return False


def _check_bash_output_truncation(command: str) -> str | None:
    """検証コマンドの出力を`tail`・`head`で切り詰める指定を検出し、全量保存を促す警告を返す。

    実行自体は止めない。全量をファイルへ保存してから必要部分を抽出する形を促す。
    全パイプラインの全検証コマンド区間を対象とし、1件でも切り詰めに該当すれば1回だけ警告する。
    `;`・`&&`・`||`・`&`で連結した後続コマンドは検証コマンドの出力を受け取らないため対象外とする。
    判定は`_extract_execution_pipelines`が返す実行位置で行うため、検証ツール名を検索語・引数として
    含むだけの読み取り操作は検出しない。実行位置を確定できない区間と、実行位置以外で起動される
    検証コマンドも検出しない（助言であり非検出側の誤差の実害が小さいため）。
    """
    if not any(_pipeline_truncates_verification_output(pipeline) for pipeline in _extract_execution_pipelines(command)):
        return None
    return _llm_notice(
        "warn: verification command output is piped through `tail`/`head`, truncating it."
        " Save the full output first (e.g. `tee /tmp/<name>.log`) and extract from the saved"
        " file instead of truncating the live output.",
        tag="warn",
    )


def _contains_unquoted_status_expansion(token: str) -> bool:
    """トークンに単一引用符で保護されていない`$?`があるかを返す。"""
    quote: str | None = None
    escaped = False
    index = 0
    while index < len(token):
        char = token[index]
        if escaped:
            escaped = False
            index += 1
            continue
        if quote == "'":
            if char == "'":
                quote = None
            index += 1
            continue
        if quote == '"':
            if char == "\\":
                escaped = True
            elif char == '"':
                quote = None
            elif token.startswith("$?", index):
                return True
            index += 1
            continue
        if char == "\\":
            escaped = True
        elif char in {"'", '"'}:
            quote = char
        elif token.startswith("$?", index):
            return True
        index += 1
    return False


def _status_report_follows_truncation(command: str) -> bool:
    """直後のserial commandが検証コマンドの終了状態を利用する形かを返す。"""
    tokens = _command_tokens_with_quotes(command)
    if not tokens:
        return False
    if any("PIPESTATUS" in token for token in tokens):
        return False
    return any(_contains_unquoted_status_expansion(token) for token in tokens)


def _check_bash_output_status_after_truncation(command: str) -> str | None:
    """切り詰め直後の`$?`報告が検証コマンドの状態を隠す場合に診断を返す。"""
    serial_commands = _split_serial_shell_commands(command, separators=_STATUS_SHELL_SEPARATORS)
    for index, serial_command in enumerate(serial_commands[:-1]):
        if not any(
            _pipeline_truncates_verification_output(pipeline) for pipeline in _extract_execution_pipelines(serial_command)
        ):
            continue
        if _status_report_follows_truncation(serial_commands[index + 1]):
            return _llm_notice(
                "warn: `$?` after a truncating verification pipeline reports the status of `head`/`tail`,"
                " not the verification command. Preserve the verification status before truncating output.",
                tag="warn",
            )
    return None


def _is_high_capacity_home_target(token: str) -> bool:
    """高容量のユーザー領域を表す検索対象かを返す。"""
    normalized = token.rstrip("/")
    home = pathlib.Path.home()
    targets = {
        str(home),
        str(home / ".local"),
        str(home / ".npm"),
        str(home / ".codex"),
        "~",
        "~/.local",
        "~/.npm",
        "~/.codex",
        "$HOME",
        "$HOME/.local",
        "$HOME/.npm",
        "$HOME/.codex",
        "${HOME}",
        "${HOME}/.local",
        "${HOME}/.npm",
        "${HOME}/.codex",
    }
    return normalized in targets


def _pipeline_has_recursive_home_search(tokens: Sequence[str]) -> bool:
    """オプションの無い単純な再帰検索が高容量領域だけを対象とするかを返す。"""
    if len(tokens) < 3:
        return False
    if tokens[0] == "rg":
        arguments = tokens[1:]
    elif tokens[0] == "grep" and tokens[1] in {"-r", "-R"}:
        arguments = tokens[2:]
    else:
        return False
    if len(arguments) < 2 or any(token.startswith("-") for token in arguments):
        return False
    paths = arguments[1:]
    return all(_is_high_capacity_home_target(path) for path in paths)


def _check_bash_recursive_home_search(command: str) -> str | None:
    """高容量のユーザー領域を無限定に再帰検索する実行位置へ警告を返す。"""
    if not any(
        segment.resolved and _pipeline_has_recursive_home_search(segment.tokens)
        for pipeline in _extract_execution_pipelines(command)
        for segment in pipeline
    ):
        return None
    return _llm_notice(
        "warn: recursive search targets a high-capacity user directory. "
        "Limit the path to a repository or a narrower subdirectory before running `rg`/recursive `grep`.",
        tag="warn",
    )


# --- Bash: git commit未検証警告 ---


_GIT_COMMIT_INCLUDE_WORKTREE_FLAGS: frozenset[str] = frozenset({"--all"})


def _commit_event_includes_worktree(event: GitEvent) -> bool:
    """`GitEvent`の`subcommand_args`から`-a` / `--all`相当の指定を検出する。

    短縮オプション結合（`-am`等）にも対応するため、`-`始まりで`a`を含むトークンを対象とする。
    `--`以降（pathspec区切り）は対象外とする。
    """
    for token in event.subcommand_args:
        if token == "--":
            break
        if token in _GIT_COMMIT_INCLUDE_WORKTREE_FLAGS:
            return True
        if token.startswith("-") and not token.startswith("--") and "a" in token[1:]:
            return True
    return False


def _is_docs_only_commit(event: GitEvent, cwd: str) -> bool:
    """コミット対象のファイルが全てMarkdownの場合に真を返す。

    docs-only変更では手動テストを省略しpre-commit側のtextlint / markdownlintに
    委ねる運用を想定しており、その場合に未検証警告を抑制する。

    `git commit -a` / `--all`等のコマンドでは作業ツリー側の変更も対象となるため、
    stagedとworking treeを切り分けて判定する。
    `cwd`不在やgit呼び出し失敗時は偽を返して警告を継続する。
    """
    if not cwd:
        return False
    include_working_tree = _commit_event_includes_worktree(event)
    args = ["git", "diff", "--name-only", "HEAD"] if include_working_tree else ["git", "diff", "--cached", "--name-only"]
    files = _git_status.run_git_lines(args, cwd)
    if not files:
        return False
    return all(path.lower().endswith(".md") for path in files)


def _check_bash_git_commit(command: str, session_id: str, cwd: str) -> str | None:
    """テスト未実行のままgit commitする場合に警告文を返す。

    テスト実行済み（stateの`test_executed`が真）の場合はスキップする。
    状態ファイル不在時は`test_executed` = falseとして扱い警告を表示する。
    コミット対象が全てMarkdownファイルの場合はpre-commit側に検証を委ねる運用を想定してスキップする。
    `git`コマンドの検出はシェルトークン解析（`extract_git_events`）に基づき、各セグメントの先頭
    トークンが`git`である場合のみサブコマンドを認識する。単純な部分文字列一致と異なり、
    `grep`の検索パターン文字列等クォート内に現れる`git commit`は先頭トークンに現れないため
    誤反応しない（`_check_bash_amend_rebase_without_log`等と同一の検出方式）。
    ヒアドキュメント本文中の記述は`extract_git_events`の既知の限界として本checkでも扱わない
    （`_bash_command_parser.split_bash_segments`のdocstring参照）。
    実効cwdがscratchpad配下（`_scratchpad_path.is_scratchpad_path`）で、かつ当該cwdの
    `git remote`（`_git_status.run_git_lines`）が空リストのイベントは検査対象から外す。
    取得失敗（`None`）または非空リストの場合は検査を適用する（取得失敗を除外の根拠にしない）。
    """
    commit_events = [e for e in extract_git_events(command, cwd) if e.subcommand == "commit"]
    if not commit_events:
        return None
    commit_events = [
        event
        for event in commit_events
        if not (
            event.cwd_resolved
            and _scratchpad_path.is_scratchpad_path(pathlib.Path(event.cwd))
            and _git_status.run_git_lines(["git", "remote"], event.cwd) == []
        )
    ]
    if not commit_events:
        return None
    state = read_state(session_id)
    if state.get("test_executed", False):
        return None
    if any(not event.cwd_resolved for event in commit_events):
        return _llm_notice(
            "committing without running tests. Follow the verify-then-commit procedure in 01-agent.md and run tests first.",
            tag="warn",
        )
    commit_event = commit_events[0]
    if _is_docs_only_commit(commit_event, commit_event.cwd):
        return None
    return _llm_notice(
        "committing without running tests. Follow the verify-then-commit procedure in 01-agent.md and run tests first.",
        tag="warn",
    )


# --- Bash: agent-toolkit/配下のversion bump漏れ警告 ---

_AGENT_TOOLKIT_PREFIX = "agent-toolkit/"
_AGENT_TOOLKIT_PLUGIN_MANIFEST = _plan_format.PLUGIN_MANIFEST_PATH
_AGENT_TOOLKIT_TEST_SUFFIX = "_test.py"
_AGENT_TOOLKIT_SCRIPTS_PREFIX = "agent-toolkit/scripts/"


def _check_bash_agent_toolkit_version_bump(command: str, cwd: str) -> str | None:
    """agent-toolkit/配下の変更をコミットする際にversion bump漏れを警告する。

    判定:

    1. `extract_git_events`が`commit`サブコマンドを1件以上返した場合のみ動作する
       （`_check_bash_git_commit`と同じ検出方式であり、`git commit`という文字列を引数として
       含むだけの読み取り操作では動作しない）
    2. ステージ済みファイルに`agent-toolkit/`配下を含まない、または
       `agent-toolkit/scripts/*_test.py`のみの場合は警告しない
    3. ステージ済み差分に`agent-toolkit/.claude-plugin/plugin.json`を
       含む場合は警告しない
    4. 未プッシュ範囲（`@{u}..HEAD`）に`agent-toolkit/.claude-plugin/plugin.json`
       を変更したコミットがある場合は警告しない。`@{u}`が解決できない場合
       （上流未設定・追跡先削除済みの`gone`状態）は、構成済みリモートの既定ブランチ
       （`refs/remotes/<remote>/HEAD`）との比較へフォールバックする。フォールバックも
       解決できない場合は警告側へ倒す
    5. 上記いずれにも該当しない場合、warn JSONを返す
    """
    commit_events = [event for event in extract_git_events(command, cwd) if event.subcommand == "commit"]
    if not commit_events or any(not event.cwd_resolved for event in commit_events):
        return None
    effective_cwd = commit_events[0].cwd
    if not effective_cwd:
        return None

    staged = _git_status.run_git_lines(["git", "diff", "--cached", "--name-only"], effective_cwd)
    if staged is None or not staged:
        return None
    agent_toolkit_files = [p for p in staged if p.startswith(_AGENT_TOOLKIT_PREFIX)]
    if not agent_toolkit_files:
        return None
    non_test_files = [
        p
        for p in agent_toolkit_files
        if not (p.startswith(_AGENT_TOOLKIT_SCRIPTS_PREFIX) and p.endswith(_AGENT_TOOLKIT_TEST_SUFFIX))
    ]
    if not non_test_files:
        return None
    if _AGENT_TOOLKIT_PLUGIN_MANIFEST in staged:
        return None

    unpushed = _git_status.run_git_lines(
        ["git", "rev-list", "@{u}..HEAD", "--", _AGENT_TOOLKIT_PLUGIN_MANIFEST],
        effective_cwd,
    )
    if unpushed is None:
        default_branch = _git_status.resolve_default_branch(effective_cwd)
        if default_branch is not None:
            unpushed = _git_status.run_git_lines(
                ["git", "rev-list", f"{default_branch}..HEAD", "--", _AGENT_TOOLKIT_PLUGIN_MANIFEST],
                effective_cwd,
            )
    if unpushed:
        return None

    return _llm_notice(
        "agent-toolkit/ files are staged but"
        " `agent-toolkit/.claude-plugin/plugin.json` `version` is unchanged"
        " in this commit and the unpushed range."
        " If user-facing behavior changes (hook script, skill, agent definition,"
        " rule file, etc.), bump the `version` field in plugin.json"
        " (and keep `.claude-plugin/marketplace.json` in sync) before committing.",
        tag="warn",
    )


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
    }


# --- Bash: codex exec未決事項の念押し ---

_CODEX_EXEC_PREFIX: tuple[str, ...] = ("codex", "exec")
_CODEX_EXEC_RESUME_PREFIX: tuple[str, ...] = ("codex", "exec", "resume")


def _check_bash_codex_exec(command: str) -> str | None:
    """Codex exec（resume以外）を検出した場合に未決事項確認の警告文を返す。

    判定は`_extract_execution_segments`が返す実行位置で行い、実行位置のトークン列が`codex exec`で
    始まる区間を対象とする。当該区間が`codex exec resume`である場合は除外する。
    `codex exec`という文字列を引数として含むだけの読み取り操作は検出しない。
    実行位置を確定できない区間と、実行位置以外で起動される`codex exec`も検出しない
    （助言であり非検出側の誤差の実害が小さいため）。
    """
    for segment in _extract_execution_segments(command):
        if not _segment_starts_with(segment, _CODEX_EXEC_PREFIX):
            continue
        if _segment_starts_with(segment, _CODEX_EXEC_RESUME_PREFIX):
            continue
        break
    else:
        return None
    return _llm_notice(
        "running codex exec."
        " If this run submits a plan file for review, check whether any decisions"
        " were made by assumption rather than user confirmation,"
        " and resolve open questions with the user before proceeding."
    )


# --- Codex App Server: isSidechainプローブ ---


def _record_iss_sidechain_probe(
    session_id: str,
    tool_name: str,
    payload: dict,
) -> None:
    """多重ネスト構成でのisSidechain実値採取用のデバッグログ記録。

    暫定機構: fb7 (20260719-074241-001.md) の実サンプル採取が目的。
    十分なサンプルが集まり代替判定機構が実装された時点で本ヘルパーは削除する。
    ログ出力先はtempfile.gettempdir()起点でsession_id単位に分離する
    （_stop_gate.pyの_stop_log_path先例に揃える）。
    ローテーションと追記は`_file_lock.locked_rotate_and_append`へ委譲する。
    """
    try:
        log_dir = pathlib.Path(tempfile.gettempdir())
        safe_session_id = re.sub(r"[^A-Za-z0-9_-]", "_", session_id or "unknown")
        log_path = log_dir / f"claude-agent-toolkit-issidechain-{safe_session_id}.log"
        state = read_state(session_id) if session_id else {}
        entry = {
            "timestamp": datetime.datetime.now(datetime.UTC).isoformat(),
            "session_id": session_id,
            "tool_name": tool_name,
            "isSidechain": payload.get("isSidechain"),
            "transcript_path": payload.get("transcript_path"),
            "cwd": payload.get("cwd"),
            "current_plan_file_path": state.get("current_plan_file_path") if isinstance(state, dict) else None,
        }
        _locked_rotate_and_append(log_path, json.dumps(entry, ensure_ascii=False) + "\n", 1_000_000)
    except OSError:
        pass


# --- agents_server: 開始点の絶対cwd検査 ---


def _check_agents_server_cwd(tool_input: dict) -> bool:
    """`start.cwd`が非空の絶対パスでない呼び出しを検出する。"""
    cwd = tool_input.get("cwd")
    if isinstance(cwd, str) and cwd.strip() != "" and pathlib.PurePath(cwd).is_absolute():
        return False
    specified = tool_input.get("cwd")
    actual = f"`{specified}`" if isinstance(specified, str) and specified != "" else "unspecified"
    print(
        _block_notice(
            f"blocked: agents_server start requires a non-empty absolute cwd parameter (got {actual})."
            " Without it, Codex resolves the working directory from the App Server"
            " process rather than the requested worktree.",
            fix="Retry with cwd set to the absolute path of the target working directory.",
        ),
        file=sys.stderr,
    )
    return True


def _check_agents_server_continuation_input(session_id: str, tool_input: dict, tool_name: str) -> bool:
    """`send_message`・`kill`の入力と保存済みcwdを検査する。"""
    display_name = tool_name.rsplit("__", 1)[-1]
    if tool_name in _AGENTS_SERVER_SEND_TOOLS:
        prompt = tool_input.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip():
            print(
                _block_notice(
                    f"blocked: {display_name} requires a non-empty prompt.",
                    fix="Retry with a non-empty prompt.",
                ),
                file=sys.stderr,
            )
            return True
    remote_session_id = tool_input.get("session_id")
    if not isinstance(remote_session_id, str) or not remote_session_id:
        print(
            _block_notice(
                f"blocked: {display_name} requires a non-empty session_id.",
                fix="Use the session_id returned by codex_start, or start a new session with codex_start.",
            ),
            file=sys.stderr,
        )
        return True
    state = read_state(session_id)
    cwd_map = state.get(_AGENTS_SERVER_SESSION_CWD_KEY)
    if not isinstance(cwd_map, dict) or not isinstance(cwd_map.get(remote_session_id), str):
        print(
            _block_notice(
                f"blocked: {display_name} cannot continue because session_id has no stored absolute cwd.",
                fix="Do not continue this session; start a new one with agents_server start using an absolute cwd.",
            ),
            file=sys.stderr,
        )
        return True
    return False
