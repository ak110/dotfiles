r"""Claude Code plugin agent-toolkit: PreToolUse統合フック。

任意ツールの実行前に以下のチェックを順に実行する。
block系checkは1プロセスで直列実行し、最初の違反でexit 2する。
warn種別のcheckはstderrまたはstdoutに警告を表示しつつ処理を継続する。
auto-fix種別のcheckは`updatedInput`でツール入力を自動書き換えする。
関連チェック項目は初回で一括開示する（反復サイクル防止のため）。

統合しているチェック:

任意ツール:

- メインエージェント応答の日本語文字比率が閾値未満の場合の警告/ブロック (warn/block)
- plan-modeスキル未起動のままのplan file編集（Write/Edit/MultiEdit）の警告 (warn)
- plan-modeスキル起動後、計画ファイル未作成のままagent-toolkit配下の直接編集連続のブロック (warn/block)
- plan fileのWrite/Edit/MultiEditで対象ファイル一覧に`agent-toolkit/`配下パスを含むが
  実装者向け領域に`agent_toolkit_bump.py`ステップが記載されていない場合の警告 (warn)
- plan fileのWrite/Edit/MultiEditで実装者向け領域にbump stepが記載されているが
  対象ファイル一覧にmanifest（`agent-toolkit/.claude-plugin/plugin.json`・
  `.claude-plugin/marketplace.json`）が含まれていない場合の警告 (warn)
- plan fileのWrite/Edit/MultiEditで対象ファイル一覧に絶対パスまたは親ディレクトリ参照を検出した場合の警告 (warn)

固定見出しと固定表の構造、逐語素材と原文参照、計画メタ情報の4項目と記法、
対象一覧の状態、フェンス整合、参照実在は
`agent-toolkit/skills/plan-mode/scripts/check_plan_file.py`が担うため
本フックでは扱わない。

mcp__codex__codex:

- メインセッションで`agent-toolkit:delegation`の起動記録が無いcodex MCP呼び出しのブロック (block)
- `sandbox`が`danger-full-access`以外（未指定を含む）の呼び出しのブロック (block)
- `approval-policy`の`never`固定 (auto-fix)
- 全チェック通過時の強制承認 (auto-approve)

mcp__codex__codex-reply:

- `agent-toolkit:delegation`起動後のcodex継続呼び出しの強制承認 (auto-approve)

Bash:

- `sleep`直後に読み取り専用の状態確認コマンドを連結するforeground待機の検出 (warn/block)
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
- `name`引数指定のブロック (block)

Write / Edit / MultiEdit:

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
- codex sandbox指定（`danger-full-access`）を含む行の削除・変更 (block)

各チェックの詳細仕様（対象パターン・エラー文言・例外条件）は対応する実装関数のdocstringを参照する。
block系checkの検査対象は「新規に書き込まれる側」（`content` / `new_string`）を基本とする。
`old_string`は既存内容の修正・削除を妨げないため単独では検査対象としない。
例外は`_check_danger_full_access_preserved`とする。同checkは保護対象文字列の「削除」自体を検出対象とするため、
`old_string`と`new_string`の出現数を比較する（`_check_style_negation`と同方式）。
"""

import datetime
import hashlib
import json
import pathlib
import re
import shlex
import subprocess
import sys
import tempfile
from collections.abc import Sequence

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import _git_status  # noqa: E402  # pylint: disable=wrong-import-position,import-error
import _plan_format  # noqa: E402  # pylint: disable=wrong-import-position,import-error
import _process_loop_log  # noqa: E402  # pylint: disable=wrong-import-position,import-error
import _response_language_check  # noqa: E402  # pylint: disable=wrong-import-position,import-error
from _bash_command_parser import (  # noqa: E402  # pylint: disable=wrong-import-position,import-error
    GitEvent,
    extract_git_events,
    split_bash_segments,
)
from _file_lock import rotate_if_needed as _rotate_if_needed  # noqa: E402  # pylint: disable=wrong-import-position,import-error
from _message_format import llm_notice as _llm_notice_base  # noqa: E402  # pylint: disable=wrong-import-position,import-error
from _plan_file import is_plan_file  # noqa: E402  # pylint: disable=wrong-import-position,import-error
from _session_state import read_state, update_state  # noqa: E402  # pylint: disable=wrong-import-position,import-error

# pylint: disable=wrong-import-position,import-error
from _tracked_subagent_types import TRACKED_SUBAGENT_TYPES as _TRACKED_SUBAGENT_TYPES  # noqa: E402
from _transcript_agent_id import (  # noqa: E402  # pylint: disable=wrong-import-position,import-error
    extract_transcript_agent_id as _extract_transcript_agent_id,
)

# pylint: enable=wrong-import-position,import-error
from pyfltr.colloquial import check as _colloquial_check  # noqa: E402  # pylint: disable=wrong-import-position

# U+FFFD（REPLACEMENT CHARACTER）: UTF-8デコード失敗時の代替文字
_REPLACEMENT_CHAR = "\ufffd"

# 日本語の文字（ひらがな・カタカナ・CJK統合漢字）。
_JAPANESE_SCRIPT_RE = re.compile(r"[぀-ゟ゠-ヿ一-鿿]")
# 日本語文中への混入を検出する他言語の文字。
# ハングル字母（U+1100-U+11FF）・ハングル互換字母（U+3130-U+318F）・ハングル音節（U+AC00-U+D7A3）・
# 半角ハングル（U+FFA0-U+FFDC）・キリル文字（U+0400-U+04FF）・キリル補助（U+0500-U+052F）を対象とする。
_FOREIGN_SCRIPT_RE = re.compile("[\u1100-\u11ff\u3130-\u318f\uac00-\ud7a3\uffa0-\uffdc\u0400-\u04ff\u0500-\u052f]")

# このスクリプトの hook 識別子。
_HOOK_ID = "agent-toolkit/pretooluse"


def _llm_notice(body: str, *, tag: str = "") -> str:
    """コーディングエージェント宛てメッセージを標準プレフィックス/サフィックス付きで整形する。"""
    return _llm_notice_base(body, _HOOK_ID, tag=tag)


def _print_warning_if_present(message: str | None) -> None:
    """警告降格したcheck関数の戻り値（違反メッセージまたはNone）をstderrへ出力する。

    旧block系checkの戻り値契約（違反メッセージ`str`またはNone）をそのまま流用しつつ、
    呼び出し元の制御フロー（exit 2）には使わない用途で使う。
    """
    if message:
        print(message, file=sys.stderr)


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
                        "additionalContext": _language_notice(body),
                    },
                },
                ensure_ascii=False,
            ),
        )

    # plan mode下でplan-modeスキル未起動のままplan fileを編集しようとした場合は警告（降格）。
    # 呼び出し元はplan-modeの直接委譲手順で計画確定前に警告を解消・検収する
    _check_plan_mode_skill_first(tool_name, tool_input, session_id)

    # plan-modeスキル起動後、計画ファイル未作成のままagent-toolkit配下の直接編集連続をブロック
    if _check_direct_agent_toolkit_edits_after_plan_mode(tool_name, tool_input, session_id):
        return 2

    # plan file編集前の必須リファレンス未読の場合は警告（降格）

    # 編集中はパス契約だけを補助し、意味と構造の検査は確定前の計画検査とレビューへ委ねる。
    _check_plan_file_target_file_paths_relative(tool_name, tool_input)

    if tool_name == "ExitPlanMode":
        flush_pending_language_warning()
        return 0

    # Skill: plan-mode起動時は計画単位の状態をリセット
    if tool_name == "Skill":
        skill_name = tool_input.get("skill")
        if isinstance(skill_name, str) and skill_name in _PLAN_MODE_SKILL_NAMES:
            _reset_plan_mode_state(session_id)
        flush_pending_language_warning()
        return 0

    # mcp__codex__codex: メインセッションのdelegation起動確認 + sandbox・cwd検査。
    if tool_name == "mcp__codex__codex":
        _record_iss_sidechain_probe(session_id, tool_name, payload)
        if payload.get("isSidechain") is not True:
            state = read_state(session_id)
            if _check_delegation_not_invoked(state, tool_name=tool_name):
                return 2
        if _check_codex_mcp_sandbox(tool_input):
            return 2
        if _check_codex_mcp_cwd(tool_input):
            return 2
        emit_json(_check_codex_mcp_execution(tool_input))
        _record_codex_remote_snapshot(session_id, tool_name, payload, tool_input)
        return 0

    # mcp__codex__codex-reply: メインセッションのdelegation起動確認 + 強制承認。
    if tool_name == "mcp__codex__codex-reply":
        _record_iss_sidechain_probe(session_id, tool_name, payload)
        if payload.get("isSidechain") is not True:
            state = read_state(session_id)
            if _check_delegation_not_invoked(state, tool_name=tool_name):
                return 2
        emit_json(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "allow",
                },
            }
        )
        _record_codex_remote_snapshot(session_id, tool_name, payload, tool_input)
        return 0

    # Bashは専用ハンドラ
    if tool_name == "Bash":
        command = tool_input.get("command")
        if not isinstance(command, str):
            flush_pending_language_warning()
            return 0
        cwd_raw = payload.get("cwd", "")
        cwd = cwd_raw if isinstance(cwd_raw, str) else ""
        # sleep直後の状態確認連結を検出（初回warn、セッション内再検出でblock）
        run_in_background = bool(tool_input.get("run_in_background"))
        sleep_poll_result = _check_bash_sleep_poll_pattern(command, session_id, run_in_background)
        if sleep_poll_result == "block":
            return 2
        _print_warning_if_present(sleep_poll_result)
        # git amend / rebaseは直前にgit logを確認していなければブロック
        if _check_bash_amend_rebase_without_log(command, session_id, cwd):
            return 2
        # git push実行前にamend後の未コミット差分残置を機械的にブロック
        if _check_bash_git_push_after_amend_with_dirty_status(command, session_id, cwd):
            return 2
        # 一括ステージ実行時にセッション未編集の変更が含まれる場合の警告
        result = _check_bash_bulk_stage_with_unedited_files(command, session_id, cwd)
        if result is not None:
            emit_json(result)
            return 0
        # uv run python <path>形式の起動は非Pythonプロジェクトでブロック
        if _check_bash_uv_run_python(command, cwd):
            return 2
        # パターン一致によるプロセス終了（pkill/killall）をブロック
        if _check_bash_process_kill_by_pattern(command):
            return 2
        # 検証コマンド出力のtail/head切り詰めを警告
        _print_warning_if_present(_check_bash_output_truncation(command))
        # git commit未検証警告
        result = _check_bash_git_commit(command, session_id, cwd)
        if result is not None:
            emit_json(result)
            return 0
        # agent-toolkit/配下のコミット時にversion bump漏れを警告
        result = _check_bash_agent_toolkit_version_bump(command, cwd)
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

    # Readは変更を伴わないため、個別の事前検査を行わない。
    if tool_name == "Read":
        flush_pending_language_warning()
        return 0

    # Agent/Task: process-loop観測用のサブエージェント起動時刻記録 (fb-1) +
    if tool_name in ("Agent", "Task"):
        # `name`指定は起動記録より前に遮断する（起動しない呼び出しの副作用を残さないため）。
        if _check_agent_name_parameter(tool_name, tool_input):
            return 2
        subagent_type = tool_input.get("subagent_type")
        if isinstance(subagent_type, str) and _check_subagent_model_override(subagent_type, tool_input):
            return 2
        if isinstance(subagent_type, str) and subagent_type in _PLAN_IMPL_EXECUTOR_SUBAGENT_TYPES:
            _record_plan_impl_executor_plan_path(session_id, tool_input)
        # ブロック検査を全通過した場合のみ、実際に起動する種別として開始時刻を記録する。
        # ブロック前に記録すると、起動しなかった種別の`subagent_start`だけが残り
        # `subagent_end`と対応しなくなるため（process-loopの所要時間分析が崩れる）。
        if isinstance(subagent_type, str) and subagent_type in _TRACKED_SUBAGENT_TYPES:
            _process_loop_log.append("subagent_start", type=subagent_type)
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
    if _check_foreign_script_mixin(tool_name, fields):
        return 2
    # Edit/MultiEditは内部的にCRLFを透過的に維持するためチェック不要。
    # WriteのみLFで書き込むためEOLチェックを実行する。
    if tool_name == "Write" and _is_ps1(file_path) and _check_ps1_eol(tool_name, fields, file_path):
        return 2
    if _check_lockfiles(tool_name, file_path):
        return 2
    if _check_secrets(tool_name, file_path):
        return 2
    if _check_danger_full_access_preserved(tool_name, tool_input, file_path):
        return 2

    # --- warn系check（stderrに警告のみ、exit codeは0のまま）---
    _check_manifest(tool_name, file_path)
    _check_home_path(tool_name, fields, file_path)
    _check_colloquial(tool_name, fields, file_path)
    _check_style_negation(tool_name, tool_input, file_path)
    _check_frontmatter_sync_note_body_exists(tool_name, tool_input, file_path)
    _check_body_section_reference_exists(tool_name, tool_input, file_path)

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
        hook_specific = {"hookEventName": "PreToolUse"}
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
            _llm_notice(
                f"blocked: non-Japanese script (Hangul/Cyrillic) mixed into Japanese text"
                f" in {tool_name}.{field}. Context: {ascii(value[start:end])}."
                f" Replace it with the intended Japanese characters."
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
                    f" no metaphorical verbs) per agent-toolkit/rules/01-agent.md '日本語' section."
                    f" Target: {file_path}",
                    tag="warn",
                ),
                file=sys.stderr,
            )
            return True
    return False


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


def _check_style_negation(tool_name: str, tool_input: dict, file_path: str) -> bool:
    """『Xを根拠にYしない』『Xを理由にYしない』形式の増加を検出したら警告を表示して真を返す（warn）。

    既存側と新規側の出現数を比較し、増加時のみ警告する
    （既存文字列の保持時は件数同数で誤検出しない）。Writeは`content`全文のマッチ件数が
    1件以上であれば警告する。
    """
    if not _is_style_negation_target_doc(file_path):
        return False
    increased = False
    if tool_name == "Write":
        content = tool_input.get("content")
        if isinstance(content, str):
            increased = _count_style_negation_matches(content) > 0
    elif tool_name == "Edit":
        old_string = tool_input.get("old_string") or ""
        new_string = tool_input.get("new_string")
        if isinstance(new_string, str):
            old_string = old_string if isinstance(old_string, str) else ""
            increased = _count_style_negation_matches(new_string) > _count_style_negation_matches(old_string)
    elif tool_name == "MultiEdit":
        edits = tool_input.get("edits") or []
        if isinstance(edits, list):
            for edit in edits:
                if not isinstance(edit, dict):
                    continue
                old_string = edit.get("old_string") or ""
                new_string = edit.get("new_string")
                if not isinstance(new_string, str):
                    continue
                old_string = old_string if isinstance(old_string, str) else ""
                if _count_style_negation_matches(new_string) > _count_style_negation_matches(old_string):
                    increased = True
                    break
    if not increased:
        return False
    print(
        _llm_notice(
            f"detected an increase in meta-norm phrases of the form '`X`を根拠に`Y`しない' / '`X`を理由に`Y`しない'"
            f" via {tool_name}. Target: {file_path}."
            " Such phrasing risks being misread as 'if not X, then it is fine to Y'."
            " Consider rewriting to the universal-negation form"
            " ('いかなる理由（例: X）があっても`Y`しない')."
            " See agent-toolkit/rules/01-agent.md '日本語' section.",
            tag="warn",
        ),
        file=sys.stderr,
    )
    return True


# --- frontmatter同期注記の本体該当語句の実在検証check (warn, feedback 2) ---

# 対象は`agent-toolkit/`・`.chezmoi-source/dot_claude/`配下の`.md`ファイル全般
# （`_plan_format.is_agent_doc_target_file`より対象範囲が広い専用判定）。
_FRONTMATTER_SYNC_TARGET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(^|/)agent-toolkit/.+\.md$"),
    re.compile(r"(^|/)\.chezmoi-source/dot_claude/.+\.md$"),
)

# frontmatter区間（`^---$`〜`^---$`）の抽出用。
_FRONTMATTER_BLOCK_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---(?:\r?\n|\Z)", re.DOTALL)

# 同期注記コメント行の判定トリガー。
# `# ...と意図的に重複させている` / `# ...と意図的に同期する` / `# 同期注記:`の3形式を検出する。
_SYNC_NOTE_TRIGGER_RE = re.compile(r"と意図的に(?:重複させている|同期する)|同期注記:")

# 注記本文からの参照ファイルパス抽出（`<name>.md`形式）。
_SYNC_NOTE_FILE_PATH_RE = re.compile(r"[\w.\-/]+\.md")

# 注記本文からの節名抽出。`「<節名>」節`形式とバッククォート囲み`<節名>節`形式の両方に対応する。
_SYNC_NOTE_SECTION_KAGI_RE = re.compile(r"「([^」]+)」節")
_SYNC_NOTE_SECTION_QUOTED_RE = re.compile(r"`([^`]+)`節")


def _is_frontmatter_sync_check_target(file_path: str) -> bool:
    """frontmatter同期注記検査の対象ファイルかを判定する。

    対象は`agent-toolkit/`・`.chezmoi-source/dot_claude/`配下の`.md`ファイル、
    および計画ファイル（`is_plan_file`が真のパス）。
    """
    if not file_path:
        return False
    normalized = file_path.replace("\\", "/")
    if any(p.search(normalized) is not None for p in _FRONTMATTER_SYNC_TARGET_PATTERNS):
        return True
    return is_plan_file(file_path)


def _extract_frontmatter_sync_notes(content: str) -> list[str]:
    """frontmatter区間から同期注記コメントブロックの本文一覧を抽出する。

    `#`始まり行が連続するコメントブロックを走査単位とし、ブロック内をさらに
    `_SYNC_NOTE_TRIGGER_RE`一致行を境界として複数の注記へ分離する
    （`_split_sync_note_block`参照）。トリガー語・参照先ファイルパスが別行に分かれる形式
    （1行目に参照先パス、後続行にトリガー語を含む宣言文）は同一注記として結合する一方、
    空行を置かず連続して書かれた独立した複数の同期注記宣言が1つの注記へ混在する事態を避ける。
    frontmatter未使用ファイル（先頭が`---`で始まらない）は空リストを返す
    （原文転記領域はfrontmatter区間の外側のため走査対象に含まれない）。
    """
    match = _FRONTMATTER_BLOCK_RE.match(content)
    if match is None:
        return []
    notes: list[str] = []
    current_block: list[str] = []
    for line in match.group(1).splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            current_block.append(stripped.lstrip("#").strip())
            continue
        notes.extend(_split_sync_note_block(current_block))
        current_block = []
    notes.extend(_split_sync_note_block(current_block))
    return notes


def _split_sync_note_block(block: list[str]) -> list[str]:
    """連続コメント行ブロックをトリガー行境界で複数の同期注記へ分離する。

    トリガー行（`_SYNC_NOTE_TRIGGER_RE`一致行）に到達するたびそこまでの蓄積行を1件の注記として確定し、
    次のトリガー行に向けて新たな蓄積を開始する。これにより「1行目に参照先パス、
    後続行にトリガー語を含む宣言文」形式は同一注記として結合されつつ、
    空行を置かず連続する独立した複数の同期注記宣言は別々の注記に分離される。
    最終トリガー行より後に続く行（後続の補足）はトリガーを含まないため、
    直前に確定した注記へ継続として統合する。ブロック全体にトリガー行が1つも無い場合は空リストを返す。
    """
    notes: list[list[str]] = []
    current: list[str] = []
    for body in block:
        current.append(body)
        if _SYNC_NOTE_TRIGGER_RE.search(body):
            notes.append(current)
            current = []
    if current:
        if notes:
            notes[-1].extend(current)
        else:
            return []
    return [" ".join(note) for note in notes]


def _extract_sync_note_references(note: str) -> tuple[list[str], list[str]]:
    """同期注記本文から参照ファイルパス一覧と節名一覧を抽出する。"""
    paths = _SYNC_NOTE_FILE_PATH_RE.findall(note)
    sections = _SYNC_NOTE_SECTION_KAGI_RE.findall(note) + _SYNC_NOTE_SECTION_QUOTED_RE.findall(note)
    return paths, sections


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


def _check_frontmatter_sync_note_body_exists(tool_name: str, tool_input: dict, file_path: str) -> bool:
    r"""frontmatter同期注記が指す本体側の該当語句の実在を検査して警告する（warn）。

    対象は`_is_frontmatter_sync_check_target`が真のファイル。
    frontmatter区間から`# ...と意図的に重複させている`・`# ...と意図的に同期する`・
    `# 同期注記:`形式のコメント行（同期注記）を抽出し、注記本文が参照するファイルパス
    （`<name>.md`形式）と節名（`「<節名>」節`または`` `<節名>`節 ``形式）の実在を照合する。

    - 参照ファイルパスがリポジトリ内に実在しない場合は警告する
    - 節名は、自ファイルの適用後本文（frontmatter区間を除く）と実在する参照ファイル本文を
      連結した対象に対し見出し一致（`^#+\s*<節名>$`）または部分文字列一致のいずれかで照合し、
      いずれも一致しない場合は警告する

    表記揺れ（同旨表現の同義語形式）による誤検出を許容するためblock化しない。
    """
    if not _is_frontmatter_sync_check_target(file_path):
        return False
    content = _materialize_post_edit_content(tool_name, tool_input, file_path)
    if content is None:
        return False
    notes = _extract_frontmatter_sync_notes(content)
    if not notes:
        return False

    # 節名照合の自ファイル側corpusはfrontmatter区間を除いた本文のみとする。
    # frontmatter内の同期注記コメント自体が対象の節名文字列を引用形式で含むため、
    # frontmatterを含めたまま照合すると常に自明一致（誤検出解消の形骸化）してしまう。
    frontmatter_match = _FRONTMATTER_BLOCK_RE.match(content)
    self_body = content[frontmatter_match.end() :] if frontmatter_match is not None else content

    reasons: list[str] = []
    for note in notes:
        paths, sections = _extract_sync_note_references(note)
        referenced_bodies: list[str] = []
        for path in paths:
            resolved = _resolve_referenced_path(file_path, path)
            if resolved is None:
                reasons.append(f"referenced file path does not exist: {path}")
                continue
            try:
                referenced_bodies.append(resolved.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError):
                reasons.append(f"failed to read referenced file: {path}")
        # 節名は自ファイル本文内で完結する場合（自己参照）と、他ファイル参照を伴う場合の双方があるため、
        # 自ファイル本文（frontmatter除く）と参照先ファイル本文の双方を照合対象に含める。
        search_corpus = "\n".join([self_body, *referenced_bodies])
        for section in sections:
            heading_pattern = re.compile(rf"^#+\s*{re.escape(section)}\s*$", re.MULTILINE)
            if heading_pattern.search(search_corpus) is None and section not in search_corpus:
                reasons.append(f"section name does not exist: {section}")

    if not reasons:
        return False
    print(
        _llm_notice(
            "the body-side identifier referenced by the frontmatter sync note may not exist"
            f" ({tool_name}, target: {file_path}): {'; '.join(reasons)}."
            " Verify that the sync note body matches the target file and section name.",
            tag="warn",
        ),
        file=sys.stderr,
    )
    return True


# --- .md規範文書の本文中にある節参照の実在検証check (warn) ---

_BODY_SECTION_REFERENCE_RE = re.compile(r"`([^`\n]+\.md)`「([^」\n]+)」[節項]")


def _check_body_section_reference_exists(tool_name: str, tool_input: dict, file_path: str) -> bool:
    """規範文書の本文中にある他ファイルの節参照の実在を検査して警告する（warn）。

    `_check_frontmatter_sync_note_body_exists`はfrontmatterコメント区間の同期注記のみを走査するため、
    本文中の参照は当該checkの対象外である。本checkは本文（frontmatter区間を除く）を走査する。
    参照先ファイル名が複数のパスへ一致する場合は照合せず、一意に解決できない旨を警告する。
    """
    # 対象ファイル判定: `agent-toolkit/rules/`・`agent-toolkit/skills/`・`agent-toolkit/agents/`配下の`.md`
    if not file_path:
        return False
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
        return False

    content = _materialize_post_edit_content(tool_name, tool_input, file_path)
    if content is None:
        return False

    # frontmatter区間を除いた本文のみを走査対象とする。
    frontmatter_match = _FRONTMATTER_BLOCK_RE.match(content)
    self_body = content[frontmatter_match.end() :] if frontmatter_match is not None else content

    # 本文から節参照を抽出
    references = _BODY_SECTION_REFERENCE_RE.findall(self_body)
    if not references:
        return False

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
        return False
    print(
        _llm_notice(
            "the section reference in the body of the normative document may not exist"
            f" ({tool_name}, target: {file_path}): {'; '.join(reasons)}."
            " Verify that the reference matches the target file and section name.",
            tag="warn",
        ),
        file=sys.stderr,
    )
    return True


# codex呼び出し前後のリモート参照スナップショットを記録する状態辞書のキー。
# `posttooluse.py`が同一キーで読み取り、比較後に削除する共有SSOT。
_CODEX_REMOTE_SNAPSHOT_KEY = "codex_remote_snapshot_by_key"

# キーごとの直近codex呼び出し対象cwdを保持する状態辞書のキー（永続、比較後も削除しない）。
# `mcp__codex__codex-reply`は`tool_input`へ`cwd`を持たないため、同一スレッド（同一key）の
# 直近`mcp__codex__codex`呼び出しで記録したcwdを引き継いで使う。
_CODEX_REMOTE_CWD_KEY = "codex_remote_cwd_by_key"


def _codex_thread_cwd_state_id(thread_id: str) -> str:
    """`threadId`単位の共有cwd状態ファイルに使う疑似セッションIDを返す。"""
    digest = hashlib.sha256(thread_id.encode()).hexdigest()
    return f"codex-thread-cwd-{digest}"


def _record_codex_remote_snapshot(session_id: str, tool_name: str, payload: dict, tool_input: dict) -> None:
    """codex呼び出し直前のリモート参照スナップショットを記録する。

    キーは`transcript_path`から抽出した`agentId`（サブエージェント経由の呼び出し時）を優先し、
    抽出できない場合（主セッション自身の直接呼び出し時）は`session_id`とする。

    比較対象のcwdはcodexが実際に実行される作業ディレクトリでなければならない。
    `mcp__codex__codex`は`tool_input["cwd"]`（`_check_codex_mcp_cwd`が絶対パス検証済み）を用いる。
    `payload["cwd"]`（呼び出し元セッション自身の作業ディレクトリ）は使わない。worktree内から
    起動したセッションでも本体リポジトリを指す場合があり、実行対象と異なり得るためである
    （`_check_codex_mcp_cwd`のdocstring参照）。
    `mcp__codex__codex-reply`は`tool_input`に`cwd`を持たないため、`threadId`に対応するcwdを
    `threadId`単位の共有状態から取得する。同一オーケストレーター内の旧記録との互換用に、
    共有状態から取得できない場合は同一キーの直近cwdを`_CODEX_REMOTE_CWD_KEY`から引き継ぐ。
    cwdを取得できない場合は比較対象が無いため記録をスキップする。
    """
    agent_id = _extract_transcript_agent_id(payload.get("transcript_path"))
    key = agent_id if agent_id is not None else f"session:{session_id}"
    if tool_name == "mcp__codex__codex":
        cwd_raw = tool_input.get("cwd")
    else:
        state = read_state(session_id)
        thread_id = tool_input.get("threadId") or tool_input.get("thread_id")
        cwd_map = state.get(_CODEX_REMOTE_CWD_KEY)
        thread_state = read_state(_codex_thread_cwd_state_id(thread_id)) if isinstance(thread_id, str) else {}
        cwd_raw = thread_state.get("cwd")
        if cwd_raw is None:
            cwd_raw = cwd_map.get(key) if isinstance(cwd_map, dict) else None
    if not isinstance(cwd_raw, str) or not cwd_raw:
        return
    snapshot = _git_status.snapshot_remote_refs(cwd_raw)

    def _mutator(state: dict) -> dict | None:
        entries = state.setdefault(_CODEX_REMOTE_SNAPSHOT_KEY, {})
        entries[key] = {"cwd": cwd_raw, "snapshot": snapshot}
        cwd_map = state.setdefault(_CODEX_REMOTE_CWD_KEY, {})
        cwd_map[key] = cwd_raw
        return state

    update_state(session_id, _mutator)


# --- codex sandbox指定（danger-full-access）の保護 (block, FB13) ---

_DANGER_FULL_ACCESS_PROTECTED_PATHS: tuple[str, ...] = (
    "agent-toolkit/agents/plan-impl-executor.md",
    "agent-toolkit/skills/delegation/SKILL.md",
    "agent-toolkit/skills/delegation/references/runtime-routing.md",
    "agent-toolkit/skills/agent-standards/references/claude-hooks.md",
    "agent-toolkit/scripts/pretooluse.py",
)
_DANGER_FULL_ACCESS_VALUE = "danger-full-access"
# sandbox指定記述（`sandbox`という語とその直後の値を1組で表す記述）を抽出する。
# JSON形式・日本語地の文のバッククォート形式の双方を対象とする。
_SANDBOX_ASSIGNMENT_RE = re.compile(r"[\"`]?sandbox[\"`]?\s*(?::|へ|は|に)\s*[\"`]([A-Za-z0-9_-]+)[\"`]")
# 行コメント。抽出対象から除く。
_COMMENT_LINE_RE = re.compile(r"^\s*(?:#|//|<!--)")


def _extract_sandbox_assignments(text: str) -> list[str]:
    """sandbox指定記述から値を抽出する。行コメント行は除外する。"""
    values: list[str] = []
    for line in text.split("\n"):
        if _COMMENT_LINE_RE.match(line):
            continue
        for match in _SANDBOX_ASSIGNMENT_RE.finditer(line):
            values.append(match.group(1))
    return values


def _check_danger_full_access_preserved(tool_name: str, tool_input: dict, file_path: str) -> bool:
    """`danger-full-access`を含む行の削除・変更を遮断する（block）。

    対象ファイルは`_DANGER_FULL_ACCESS_PROTECTED_PATHS`に列挙された以下5つ:
    - `agent-toolkit/agents/plan-impl-executor.md`
    - `agent-toolkit/skills/delegation/SKILL.md`
    - `agent-toolkit/skills/delegation/references/runtime-routing.md`
    - `agent-toolkit/skills/agent-standards/references/claude-hooks.md`
    - `agent-toolkit/scripts/pretooluse.py`

    判定は`danger-full-access`という文字列の出現数ではなく、`sandbox`へ値を結びつける記述
    （以下「sandbox指定記述」）を抽出して比較する。sandbox指定記述とは、`sandbox`という語とその直後の値を
    1組で表す記述であり、`"sandbox": "<値>"`・`` `sandbox`へ`<値>` ``・`` `sandbox`は`<値>` ``・
    `` `sandbox`: `<値>` ``の各形式を対象とする。行コメント（`#`・`//`・`<!--`で始まる行）は抽出対象から除く。
    次のいずれかに当たる場合に遮断する:
    - sandbox指定記述の総数が減る（設定そのものの削除）
    - 値が`danger-full-access`でないsandbox指定記述が1件でも新たに出現する（設定の弱体化）
    """
    if not file_path:
        return False
    normalized = file_path.replace("\\", "/")
    is_target = any(
        normalized.endswith(path) or normalized.endswith("/" + path) for path in _DANGER_FULL_ACCESS_PROTECTED_PATHS
    )
    if not is_target:
        return False

    # 新規記述と既存記述のsandbox指定記述値を比較
    if tool_name == "Write":
        new_content = tool_input.get("content")
        if not isinstance(new_content, str):
            return False
        old_content = ""
    elif tool_name == "Edit":
        old_string = tool_input.get("old_string") or ""
        new_string = tool_input.get("new_string")
        if not isinstance(new_string, str):
            return False
        try:
            old_content = pathlib.Path(file_path).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            old_content = ""
        new_content = old_content.replace(old_string, new_string, 1)
    elif tool_name == "MultiEdit":
        edits = tool_input.get("edits") or []
        if not isinstance(edits, list):
            return False
        try:
            old_content = pathlib.Path(file_path).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            old_content = ""
        new_content = old_content
        for edit in edits:
            if not isinstance(edit, dict):
                continue
            old_string = edit.get("old_string") or ""
            new_string = edit.get("new_string")
            if not isinstance(new_string, str):
                continue
            new_content = new_content.replace(old_string, new_string, 1)
    else:
        return False

    old_values = _extract_sandbox_assignments(old_content)
    new_values = _extract_sandbox_assignments(new_content)

    # 総数が減少したか判定
    if len(new_values) < len(old_values):
        print(
            _llm_notice(
                f"blocked: a codex sandbox assignment in {file_path} would be removed or set to a value"
                f" other than `danger-full-access`."
                f" read-only and workspace-write leave the codex process waiting for approval and never returning,"
                f" so the caller cannot detect completion. This value is fixed by design."
                f" Commit ad3d07f4 already reverted one attempt to weaken it."
                f" A security warning about this setting is not a reason to change, verify, or narrow it."
                f" To change it intentionally, disable this hook first and record the reason."
            ),
            file=sys.stderr,
        )
        return True

    # 値が変わっていないか判定
    for value in new_values:
        if value != _DANGER_FULL_ACCESS_VALUE and value not in old_values:
            print(
                _llm_notice(
                    f"blocked: a codex sandbox assignment in {file_path} would be removed or set to a value"
                    f" other than `danger-full-access`."
                    f" read-only and workspace-write leave the codex process waiting for approval and never returning,"
                    f" so the caller cannot detect completion. This value is fixed by design."
                    f" Commit ad3d07f4 already reverted one attempt to weaken it."
                    f" A security warning about this setting is not a reason to change, verify, or narrow it."
                    f" To change it intentionally, disable this hook first and record the reason."
                ),
                file=sys.stderr,
            )
            return True

    return False


# --- plan mode中のplan file編集をplan-modeスキル未起動の場合にブロック ---

_PLAN_FILE_EDIT_TOOLS = frozenset({"Write", "Edit", "MultiEdit"})


def _check_plan_mode_skill_first(
    tool_name: str,
    tool_input: dict,
    session_id: str,
) -> bool:
    """plan-modeスキル未起動のままplan fileを編集しようとした場合に警告する。

    判定条件:

    - `session_id`が空でない（空ならセッション状態を取得できず判定不能のためスキップ）
    - セッション状態の`plan_mode_skill_invoked`が偽
    - `tool_name`が`Write` / `Edit` / `MultiEdit`のいずれか
    - 対象の`file_path`が`~/.claude/plans/`直下の計画ファイル

    `permission_mode`の値に依らず適用する（plan mode外でも計画ファイル編集時には同様に違反が起こり得るため）。
    サブエージェント経由の呼び出しでも同一の判定が働く
    （本checkは`isSidechain`を参照せず、`permission_mode`とセッション状態のみで判定するため）。
    plan file編集に至るまでは警告を表示しない
    （`process-feedbacks`等の他スキル呼び出し・通常のRead・Bash操作は素通りする）。
    警告のみでツール呼び出しは継続する（block降格）。
    呼び出し元はplan-modeの直接委譲手順で計画確定前に警告を解消・検収する。
    戻り値は違反検出の有無を示す（呼び出し元は制御フローに使わない）。
    """
    if not session_id:
        return False
    if tool_name not in _PLAN_FILE_EDIT_TOOLS:
        return False
    file_path_raw = tool_input.get("file_path")
    if not isinstance(file_path_raw, str) or not is_plan_file(file_path_raw):
        return False
    state = read_state(session_id)
    if state.get("plan_mode_skill_invoked", False):
        return False
    print(
        _llm_notice(
            "warning: editing a plan file without invoking `agent-toolkit:plan-mode` skill first."
            " Invoke the skill and restart from Phase 1 (Initial Understanding)"
            " before continuing the plan file edit."
            " Resolve and verify this warning through the plan-mode direct delegation workflow"
            " before finalizing the plan.",
            tag="warn",
        ),
        file=sys.stderr,
    )
    return True


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
) -> bool:
    """plan-modeスキル起動後、計画ファイル未作成のまま`agent-toolkit`配下の直接編集連続を検知する。

    判定条件:

    - `session_id`が空でない
    - `tool_name`が`Write` / `Edit` / `MultiEdit`のいずれか
    - セッション状態の`plan_mode_skill_invoked`が真
    - セッション状態の`plan_file_written`が偽

    連続判定は`last_agent_toolkit_edit_path`と対象パスを比較し、
    直前と異なるパスのときのみ`direct_agent_toolkit_edit_count`をincrementする。
    `~/.claude/plans/`配下のWrite/Edit時は`plan_file_written`を真にしてカウンタをリセットする。
    対象外パスへの編集時もカウンタをリセットする。
    カウンタ2件目でwarn（stderr出力＋Falseを返して進行を継続）、
    3件目以上でblock（stderr出力＋Trueを返してツール呼び出しを中断）する。
    block時は`direct_agent_toolkit_edit_count`と`last_agent_toolkit_edit_path`を更新しない。
    block後にコーディングエージェントが同一パスを再試行した場合、
    直前パス一致条件によるカウンタ加算スキップで素通りする回避を防ぐため、
    カウンタは加算直前の値のまま保持し、再試行時に再度加算されblockが継続する。
    warn／blockの2段階はstderr出力のtagで区別し、
    ハンドラの戻り値は既存の`_check_plan_mode_skill_first`等と同じくbool型とする。
    """
    if not session_id:
        return False
    if tool_name not in _PLAN_FILE_EDIT_TOOLS:
        return False
    file_path_raw = tool_input.get("file_path")
    if not isinstance(file_path_raw, str) or not file_path_raw:
        return False
    state = read_state(session_id)
    if not state.get("plan_mode_skill_invoked", False):
        return False

    # 計画ファイル編集時は`plan_file_written`を真にしカウンタをリセットする。
    if is_plan_file(file_path_raw):

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
        return False

    # 計画ファイルが既に作成済みの場合は本checkの対象外。
    if state.get("plan_file_written", False):
        return False

    # 対象外パスへの編集ならカウンタをリセットして通過。
    if not _is_direct_agent_toolkit_edit_target(file_path_raw):

        def _reset_counter(current: dict) -> dict | None:
            if current.get("direct_agent_toolkit_edit_count", 0) == 0 and current.get("last_agent_toolkit_edit_path") is None:
                return None
            current["direct_agent_toolkit_edit_count"] = 0
            current["last_agent_toolkit_edit_path"] = None
            return current

        update_state(session_id, _reset_counter)
        return False

    # 直前と同一パスの場合はincrementしない（連続判定は異なるファイルに対する編集を対象とする）。
    last_path = state.get("last_agent_toolkit_edit_path")
    if isinstance(last_path, str) and last_path == file_path_raw:
        return False

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
            _llm_notice(
                f"blocked: after invoking the plan-mode skill, {new_count} consecutive Write/Edit/MultiEdit"
                f" operations targeted files under agent-toolkit/ without first creating a plan file."
                " Create a plan file under `~/.claude/plans/` before editing any file under agent-toolkit/.",
                tag="block",
            ),
            file=sys.stderr,
        )
        return True
    if new_count == 2:
        print(
            _llm_notice(
                f"warn: after invoking the plan-mode skill, {new_count} consecutive Write/Edit/MultiEdit"
                f" operations targeted files under agent-toolkit/ without first creating a plan file."
                " The next such edit will be blocked."
                " Create a plan file under `~/.claude/plans/` first.",
                tag="warn",
            ),
            file=sys.stderr,
        )
    return False


def _materialize_post_edit_content(tool_name: str, tool_input: dict, file_path: str) -> str | None:
    """Write/Edit/MultiEditを適用した後のファイル内容を構築する。"""
    if tool_name == "Write":
        content = tool_input.get("content")
        return content if isinstance(content, str) else None

    try:
        existing = pathlib.Path(file_path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        existing = ""

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


_PLAN_IMPL_EXECUTOR_VERIFIED_PLAN_PATH_KEY = "plan_impl_executor_verified_plan_path"


# --- 計画単位の状態管理 ---

# Skillツールの`skill`引数として許容するplan-modeスキル名。
# posttooluse.pyの`_PLAN_MODE_SKILL_NAMES`と対応させる。
_PLAN_MODE_SKILL_NAMES: frozenset[str] = frozenset({"agent-toolkit:plan-mode", "plan-mode"})
# Agent/Taskツールの`subagent_type`引数として許容するplan-impl-executor識別子。
# フルネームと短縮名の両方を許容する。
_PLAN_IMPL_EXECUTOR_SUBAGENT_TYPES: frozenset[str] = frozenset({"agent-toolkit:plan-impl-executor", "plan-impl-executor"})

# `model`引数指定を一律禁止する対象。executorは定義済みモデルを使う委譲窓口として動く。
_MODEL_OVERRIDE_FORBIDDEN_SUBAGENT_TYPES: frozenset[str] = _PLAN_IMPL_EXECUTOR_SUBAGENT_TYPES


def _check_plan_file_target_file_paths_relative(tool_name: str, tool_input: dict) -> None:
    """計画ファイルの対象ファイル一覧に絶対パスまたは親ディレクトリ参照がある場合に警告する。

    判定は`_plan_format.find_invalid_target_file_paths`へ委譲する（SSOT）。
    違反時はwarn降格の`_llm_notice`を`stderr`へ出力し、ブロックは採用しない。
    """
    if tool_name not in _PLAN_FILE_EDIT_TOOLS:
        return
    file_path_raw = tool_input.get("file_path")
    if not isinstance(file_path_raw, str) or not is_plan_file(file_path_raw):
        return
    content = _materialize_post_edit_content(tool_name, tool_input, file_path_raw)
    if content is None:
        return
    invalid = _plan_format.find_invalid_target_file_paths(content)
    if not invalid:
        return
    joined = ", ".join(f"`{p}`" for p in invalid)
    print(
        _llm_notice(
            f"plan file {file_path_raw}: entries containing absolute paths or parent-directory references were"
            f" detected under `### 対象ファイル一覧` in the implementer-facing section: {joined}."
            f" Rewrite them as full paths relative to the project root"
            f" (see `skills/plan-mode/SKILL.md` '計画ファイルの完成条件' section).",
            tag="warn",
        ),
        file=sys.stderr,
    )


def _check_agent_name_parameter(tool_name: str, tool_input: dict) -> bool:
    """AgentまたはTask起動時の`name`引数指定を値によらずブロックする。

    `name`付きbackground起動は完了通知が本来の起動元へ配送されず停滞するため、
    `agent-toolkit/rules/99-claude-code.md`「サブエージェント実装」節が`name`の指定を厳守規定として禁じる。
    キーの存在のみで判定し、空文字列・`None`を含め値の内容は問わない。
    """
    if "name" not in tool_input:
        return False
    print(
        _llm_notice(
            f"blocked: the `name` parameter is not allowed for {tool_name}"
            f" (given: {tool_input.get('name')!r}).\n"
            "Why this gate exists: a named background launch does not deliver its completion"
            " notification to the actual launcher, which leaves the launcher waiting indefinitely.\n"
            "Normal fix: omit both `name` and `run_in_background`."
            " Omitting them does not fix the launch mode; determine the actual completion-report"
            " delivery route from the execution result. Place independent launches side by side"
            " in a single response to run them in parallel.\n"
            "See agent-toolkit/rules/99-claude-code.md 'サブエージェント実装' section.",
            tag="block",
        ),
        file=sys.stderr,
    )
    return True


def _check_subagent_model_override(subagent_type: str, tool_input: dict) -> bool:
    """`plan-impl-executor`への`model`引数指定を一律ブロックする。

    executorは定義済みモデルを使う委譲窓口として動くため、呼び出しごとの上書きを許容しない。
    """
    if subagent_type not in _MODEL_OVERRIDE_FORBIDDEN_SUBAGENT_TYPES:
        return False
    if "model" not in tool_input:
        return False
    model = tool_input.get("model")
    print(
        _llm_notice(
            f"blocked: explicit `model` argument (`{model!r}`) for subagent_type `{subagent_type}`.\n"
            "Why this gate exists: this subagent uses its frontmatter model and delegates"
            " actual work through `agent-toolkit:delegation`; no per-call model override is defined.\n"
            "Normal fix: omit the `model` parameter and let the agent definition's default"
            " apply.",
            tag="block",
        ),
        file=sys.stderr,
    )
    return True


_PLAN_FILE_PATH_IN_BACKTICK_RE = re.compile(r"`([^`\n]*\.claude[/\\]plans[/\\][^`\n]+\.md)`")
_PLAN_FILE_PATH_IN_PROMPT_RE = re.compile(r"[^\s`]*\.claude[/\\]plans[/\\][^\s`]+\.md")


def _extract_referenced_plan_file_path(prompt: str) -> str | None:
    r"""起動プロンプト本文から計画ファイルパス（`.claude/plans/*.md`）を抽出する。

    パス区切りは`/`と`\\`の双方を許容する。
    バッククォート囲み表記を優先し、囲み内は空白を含めて丸ごと抽出する
    （空白を含むホームディレクトリ名での部分抽出誤りを防ぐため）。
    一意に定まらない場合（0件または2件以上の異なるパスが検出された場合）は`None`を返す。
    呼び出し側は`None`を「抽出不能」として安全側（従来どおりブロック判定）に扱う。
    """
    matches = {m.group(1) for m in _PLAN_FILE_PATH_IN_BACKTICK_RE.finditer(prompt)}
    if not matches:
        matches = {m.group(0) for m in _PLAN_FILE_PATH_IN_PROMPT_RE.finditer(prompt)}
    if len(matches) != 1:
        return None
    return next(iter(matches))


def _normalize_plan_file_path(path_text: str) -> pathlib.Path | None:
    """計画ファイルパスの比較用正規化（`~`展開と絶対化）を行う。

    正規化に失敗した場合（`expanduser`が未解決ユーザーで例外を送出する場合等）は`None`を返す。
    呼び出し側は`None`を「抽出不能」と同じ従来判定へ倒す。
    """
    try:
        return pathlib.Path(path_text).expanduser().resolve(strict=False)
    except (OSError, RuntimeError, ValueError):
        return None


def _record_plan_impl_executor_plan_path(session_id: str, tool_input: dict) -> None:
    """実在する計画を参照したexecutor起動時に当該パスを記録する。"""
    if not session_id:
        return
    prompt = tool_input.get("prompt")
    if not isinstance(prompt, str):
        return
    referenced_path = _extract_referenced_plan_file_path(prompt)
    if referenced_path is None:
        return
    referenced = _normalize_plan_file_path(referenced_path)
    if referenced is None or not referenced.is_file():
        return
    _record_verified_plan_path(session_id, str(referenced))


def _record_verified_plan_path(session_id: str, plan_file_path: str) -> None:
    """実在する計画ファイルを参照した`plan-impl-executor`起動時、当該パスを記録する。

    記録値は正規化済みの絶対パスとし、以降の計画ファイル読み取りで`~`の未展開による失敗を避ける。
    `current_plan_file_path`は更新せず、計画作成側の状態と実装対象の記録を分離する。
    """

    def _set(state: dict) -> dict | None:
        if state.get(_PLAN_IMPL_EXECUTOR_VERIFIED_PLAN_PATH_KEY) == plan_file_path:
            return None
        state[_PLAN_IMPL_EXECUTOR_VERIFIED_PLAN_PATH_KEY] = plan_file_path
        return state

    update_state(session_id, _set)


def _reset_plan_mode_state(session_id: str) -> None:
    """plan-mode起動時に計画単位の状態をリセットする。"""
    if not session_id:
        return

    def _reset(current: dict) -> dict | None:
        changed = False
        if current.pop("current_plan_file_path", None) is not None:
            changed = True
        # 別の既存計画への切替記録も新計画へ持ち越さない。
        if current.pop(_PLAN_IMPL_EXECUTOR_VERIFIED_PLAN_PATH_KEY, None) is not None:
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


# --- Bash: 関連定数（git commit検出）---

_GIT_COMMIT_PATTERN = re.compile(r"\bgit\s+commit\b")


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
        if not flags.get(event.cwd, False):
            continue
        if not event.cwd:
            continue
        dirty = _git_status.has_tracked_dirty(event.cwd)
        if dirty is None:
            continue
        if dirty:
            print(
                _llm_notice(
                    f"blocked: git push after `git commit --amend` / `--fixup` with uncommitted tracked changes"
                    f" in {event.cwd}."
                    f" Run `git status` to review, then either `git add` + `git commit --amend`"
                    f" (or `--fixup=<sha>`) to fold the residual diff into the amended commit,"
                    f" or create a follow-up commit before pushing.",
                    tag="block",
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
) -> dict | None:
    """一括ステージ実行時に自セッション未編集の変更が含まれる場合の警告JSONを返す。

    `git add -A/--all/.` は未追跡を含む集合、`git add -u/--update` と
    `git commit -a/--all/-am`等は追跡済みのみを対象として作業ツリー変更を判定する。
    セッション状態の`session_edited_files`集合との差集合が空でない場合、
    個別ファイル指定への切替を促すwarnをhookSpecificOutputで返す。
    """
    for event in extract_git_events(command, payload_cwd):
        mode = _detect_bulk_stage_mode(event)
        if mode is None:
            continue
        effective_cwd = event.cwd or payload_cwd
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
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "additionalContext": _llm_notice(
                    "warn: bulk staging includes files that were not edited in this session."
                    f" Un-edited candidates: {sample}."
                    " Consider switching to per-file staging (`git add <file>`).",
                    tag="warn",
                ),
            },
        }
    return None


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
    "blocked: `uv run python` invocation without `--script` or `--no-project`"
    " before the `python` token"
    " (applies regardless of whether a path or `-c` follows `python`)."
    " In a non-Python project (pyproject.toml without a [project] section, or absent),"
    " uv treats the cwd as a project and generates `.venv` and `uv.lock` as a side effect."
    " Alternatives:"
    " (1) for a PEP 723 script, use `uv run --script <path>` or invoke the executable shebang directly;"
    " (2) to skip cwd project resolution, use `uv run --no-project python ...`;"
    " (3) inside a Python project, run it from that directory as a separate command."
    " A `cd` in the same command line does not help: this check does not resolve the effective"
    " working directory after an in-line `cd` and blocks such invocations on the safe side."
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


# --- Bash: sleep直後の読み取り専用状態確認連結の検出 ---

_SLEEP_COMMAND = "sleep"
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


def _split_serial_shell_commands(command: str) -> list[str]:
    """クォート外の`;`と`&&`だけでBash入力を直列コマンドへ分割し、コメント外の区切りのみを認識する。

    クォート外の`#`（Bashコメント開始）から行末までをスキップし、
    コメント内の`;`・`&&`を区切りとして誤検出しない。
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
        separator_length = 2 if command.startswith("&&", index) else 1 if char == ";" else 0
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


def _is_sleep_poll_pair(left: str, right: str) -> bool:
    """隣接する2コマンドがsleepと読み取り専用状態確認の組であるかを判定する。"""
    try:
        left_args = shlex.split(left, posix=True)
        right_args = shlex.split(right, posix=True)
    except ValueError:
        return False
    if not left_args or left_args[0] != _SLEEP_COMMAND or not right_args:
        return False
    if any(tuple(right_args[: len(prefix)]) == prefix for prefix in _POLL_COMMAND_PREFIXES):
        return True
    return tuple(right_args[:1]) == _CURL_COMMAND and not _curl_args_have_write_indicator(right_args[1:])


def _check_bash_sleep_poll_pattern(
    command: str,
    session_id: str,
    run_in_background: bool,
) -> str | None:
    """sleep直後の読み取り専用状態確認を初回warn、同一セッション内再検出でblockする。

    簡略化: クォート外の`;`・`&&`直列連結だけを検出する,
    既知の限界: サブシェルで包んだ状態確認は検出しない,
    見直し契機: サブシェル包みの反復ポーリングを実測した場合
    """
    if run_in_background or re.match(r"^\s*until\b", command):
        return None
    segments = _split_serial_shell_commands(command)
    if not any(_is_sleep_poll_pair(left, right) for left, right in zip(segments, segments[1:], strict=False)):
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
        "Use one `until <condition>; do sleep <interval>; done` call, or start the job in\n"
        "the background and wait once for its completion marker."
    )
    if already_detected:
        print(
            _llm_notice(
                f"block: foreground sleep followed by a status check was detected again in this session.\n{guidance}",
                tag="block",
            ),
            file=sys.stderr,
        )
        return "block"
    return _llm_notice(
        f"warn: foreground sleep followed by a status check may cause repeated polling.\n{guidance}",
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
        _llm_notice(
            "blocked: pattern-based process termination (pkill/killall) is prohibited because"
            " process ownership cannot be verified. Use `kill <PID>` for a process you started"
            " and identified by PID instead.",
            tag="block",
        ),
        file=sys.stderr,
    )
    return True


# --- Bash: 検証コマンド出力の切り詰め検出 ---

_VERIFICATION_TOOL_RE = re.compile(r"\b(pyfltr|pytest|cargo\s+test|dotnet\s+test|npm\s+(run\s+)?test|vitest|make\s+test)\b")
_OUTPUT_TRUNCATION_RE = re.compile(r"\|\s*(tail|head)\b")
_TEE_RE = re.compile(r"\btee\b")


def _check_bash_output_truncation(command: str) -> str | None:
    """検証コマンドの出力を`tail`・`head`で切り詰める指定を検出し、全量保存を促す警告を返す。

    実行自体は止めない。全量をファイルへ保存してから必要部分を抽出する形を促す。
    `tee`で全量を先に保存してから`tail`・`head`で抽出する形は、切り詰めに該当しないため対象外とする。
    """
    if not _VERIFICATION_TOOL_RE.search(command):
        return None
    if not _OUTPUT_TRUNCATION_RE.search(command):
        return None
    if _TEE_RE.search(command):
        return None
    return _llm_notice(
        "warn: verification command output is piped through `tail`/`head`, truncating it."
        " Save the full output first (e.g. `tee /tmp/<name>.log`) and extract from the saved"
        " file instead of truncating the live output.",
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


def _check_bash_git_commit(command: str, session_id: str, cwd: str) -> dict | None:
    """テスト未実行のままgit commitする場合に警告JSONを返す。

    テスト実行済み（stateの`test_executed`が真）の場合はスキップする。
    状態ファイル不在時は`test_executed` = falseとして扱い警告を表示する。
    コミット対象が全てMarkdownファイルの場合はpre-commit側に検証を委ねる運用を想定してスキップする。
    `git`コマンドの検出はシェルトークン解析（`extract_git_events`）に基づき、各セグメントの先頭
    トークンが`git`である場合のみサブコマンドを認識する。単純な部分文字列一致と異なり、
    `grep`の検索パターン文字列等クォート内に現れる`git commit`は先頭トークンに現れないため
    誤反応しない（`_check_bash_amend_rebase_without_log`等と同一の検出方式）。
    ヒアドキュメント本文中の記述は`extract_git_events`の既知の限界として本checkでも扱わない
    （`_bash_command_parser.split_bash_segments`のdocstring参照）。
    """
    commit_events = [e for e in extract_git_events(command, cwd) if e.subcommand == "commit"]
    if not commit_events:
        return None
    state = read_state(session_id)
    if state.get("test_executed", False):
        return None
    if _is_docs_only_commit(commit_events[0], cwd):
        return None
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "additionalContext": _llm_notice(
                "committing without running tests. Follow the verify-then-commit procedure in 01-agent.md and run tests first.",
                tag="warn",
            ),
        },
    }


# --- Bash: agent-toolkit/配下のversion bump漏れ警告 ---

_AGENT_TOOLKIT_PREFIX = "agent-toolkit/"
_AGENT_TOOLKIT_PLUGIN_MANIFEST = _plan_format.PLUGIN_MANIFEST_PATH
_AGENT_TOOLKIT_TEST_SUFFIX = "_test.py"
_AGENT_TOOLKIT_SCRIPTS_PREFIX = "agent-toolkit/scripts/"


def _check_bash_agent_toolkit_version_bump(command: str, cwd: str) -> dict | None:
    """agent-toolkit/配下の変更をコミットする際にversion bump漏れを警告する。

    判定:

    1. `git commit`を検出した場合のみ動作する
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
    match = _GIT_COMMIT_PATTERN.search(command)
    if match is None or not _likely_real_command(command, match.start()):
        return None
    if not cwd:
        return None

    staged = _git_status.run_git_lines(["git", "diff", "--cached", "--name-only"], cwd)
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
        cwd,
    )
    if unpushed is None:
        default_branch = _git_status.resolve_default_branch(cwd)
        if default_branch is not None:
            unpushed = _git_status.run_git_lines(
                ["git", "rev-list", f"{default_branch}..HEAD", "--", _AGENT_TOOLKIT_PLUGIN_MANIFEST],
                cwd,
            )
    if unpushed:
        return None

    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "additionalContext": _llm_notice(
                "agent-toolkit/ files are staged but"
                " `agent-toolkit/.claude-plugin/plugin.json` `version` is unchanged"
                " in this commit and the unpushed range."
                " If user-facing behavior changes (hook script, skill, agent definition,"
                " rule file, etc.), bump the `version` field in plugin.json"
                " (and keep `.claude-plugin/marketplace.json` in sync) before committing.",
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
        "systemMessage": "[agent-toolkit] auto-inserted --decorate into git log.",
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
            "additionalContext": _llm_notice(
                "running codex exec."
                " If this run submits a plan file for review, check whether any decisions"
                " were made by assumption rather than user confirmation,"
                " and resolve open questions with the user before proceeding."
            ),
        },
    }


# --- mcp__codex__codex / mcp__codex__codex-reply: isSidechainプローブ ---


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
    ローテは_file_lock.rotate_if_neededを再利用する。
    """
    try:
        log_dir = pathlib.Path(tempfile.gettempdir())
        safe_session_id = re.sub(r"[^A-Za-z0-9_-]", "_", session_id or "unknown")
        log_path = log_dir / f"claude-agent-toolkit-issidechain-{safe_session_id}.log"
        _rotate_if_needed(log_path, max_bytes=1_000_000, generations=1)
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
        with log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass


def _check_delegation_not_invoked(state: dict, *, tool_name: str) -> bool:
    """メインセッションでdelegationが未起動のMCP呼び出しをブロックする。"""
    if state.get("delegation_skill_invoked", False):
        return False
    print(
        _llm_notice(
            f"{tool_name} call is blocked because `agent-toolkit:delegation` was not invoked."
            " Invoke the skill before calling codex MCP from the main session.",
            tag="block",
        ),
        file=sys.stderr,
    )
    return True


# --- mcp__codex__codex: sandbox明示指定の強制・approval-policy自動修正 ---


def _check_codex_mcp_sandbox(tool_input: dict) -> bool:
    """`sandbox`が`danger-full-access`以外の呼び出しを検出してブロック要否を返す。

    未指定も対象に含める。`read-only`・`workspace-write`ではcodexプロセスが承認待ちのまま
    復帰せず、呼び出し元が完了を検知できないまま停止する事象を実測している。
    `updatedInput`による書き換えは承認ダイアログの発生自体を抑止できないため、
    書き換えに依存せず呼び出し側へ明示指定を求める。
    """
    if tool_input.get("sandbox") == "danger-full-access":
        return False
    specified = tool_input.get("sandbox")
    actual = f"`{specified}`" if isinstance(specified, str) else "unspecified"
    print(
        _llm_notice(
            f'blocked: mcp__codex__codex requires sandbox="danger-full-access" (got {actual}).'
            " Other sandbox modes leave the codex process waiting for approval and it never returns."
            ' Retry with sandbox="danger-full-access".'
        ),
        file=sys.stderr,
    )
    return True


def _check_codex_mcp_cwd(tool_input: dict) -> bool:
    """`cwd`が非空の絶対パスでない呼び出しを検出してブロック要否を返す。

    未指定・相対パスの場合、セッションの作業ディレクトリはMCPサーバープロセスの作業ディレクトリを
    起点に解決される。worktree内から起動したセッションであっても本体リポジトリを指す場合があり、
    委譲プロンプト本文で作業ディレクトリを伝えてもツール側の作業ディレクトリは変わらない。
    値の実在確認（対象パスの存在・worktree一致）は呼び出し元の環境依存のため本関数の対象外とし、
    絶対パス形式であることのみを検査する。相対パスは、たとえ非空でも本チェックの対象とする
    （相対パスのままではMCPサーバープロセスの作業ディレクトリ基準で解決され、根本原因を解消しない）。
    """
    cwd = tool_input.get("cwd")
    if isinstance(cwd, str) and cwd.strip() != "" and pathlib.PurePath(cwd).is_absolute():
        return False
    specified = tool_input.get("cwd")
    actual = f"`{specified}`" if isinstance(specified, str) and specified != "" else "unspecified"
    print(
        _llm_notice(
            f"blocked: mcp__codex__codex requires a non-empty absolute cwd parameter (got {actual})."
            " Without it, the session's working directory resolves to the MCP server"
            " process's working directory, which may point at the main repository"
            " even when invoked from inside a worktree."
            " Retry with cwd set to the absolute path of the target working directory."
        ),
        file=sys.stderr,
    )
    return True


def _check_codex_mcp_execution(tool_input: dict) -> dict:
    """Codex MCP呼び出しのapproval-policyを`never`へ強制固定する。

    approval-policyは`never`固定。承認プロンプトの発生を抑止し、失敗時はモデルへ結果を返す挙動へ統一する。
    `sandbox`は`_check_codex_mcp_sandbox`が`danger-full-access`の明示指定を必須とするため、
    本関数へ到達した時点で条件を満たす。

    設計意図（回帰予防）: 過去に「利用者の明示指定を尊重する」形へ変更された履歴があるが、
    本環境では承認プロンプト抑止を優先し安全側の強制固定を採用する。
    フィードバック反映等で「利用者の明示指定を尊重する」形へ再度変更しないこと。
    """
    updated_input = dict(tool_input)
    approval_ok = tool_input.get("approval-policy") == "never"
    updated_input["approval-policy"] = "never"
    result: dict = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "allow",
            "updatedInput": updated_input,
        },
    }
    if not approval_ok:
        result["systemMessage"] = "[agent-toolkit] forced codex MCP approval-policy to never."
    return result
