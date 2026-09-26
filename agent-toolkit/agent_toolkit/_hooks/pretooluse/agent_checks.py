# ruff: noqa: F401,F821,I001
# pylint: disable=unused-import,used-before-assignment,wrong-import-order
r"""PreToolUse統合フックのうち、応答言語、TaskStop、agents_server及びplan-mode起動を扱う検査。"""

from __future__ import annotations

import datetime
import json
import os
import pathlib
import re
import sys
import tempfile
import time
from typing import TYPE_CHECKING


from agent_toolkit._agents_server import (
    tool_names as _agents_server_tool_names,  # noqa: E402  # pylint: disable=wrong-import-position,import-error
)
from agent_toolkit._common.delegated_session import is_delegated  # noqa: E402
from agent_toolkit._common.file_lock import (  # noqa: E402  # pylint: disable=wrong-import-position,import-error
    locked_rotate_and_append as _locked_rotate_and_append,
)

# pylint: disable=wrong-import-position
from agent_toolkit._hooks import (
    plugin_resources as _plugin_resources,  # noqa: E402  # pylint: disable=wrong-import-position,import-error
)
from agent_toolkit._hooks import (
    response_language_check as _response_language_check,  # noqa: E402  # pylint: disable=wrong-import-position,import-error
)
from agent_toolkit._hooks import (
    rules_context as _rules_context,  # noqa: E402  # pylint: disable=wrong-import-position,import-error
)

# pylint: disable-next=wrong-import-position,import-error
from agent_toolkit._hooks.notice import _WARN_TAG  # noqa: E402

from agent_toolkit._hooks.session_state import (  # noqa: E402  # pylint: disable=wrong-import-position,import-error
    read_state,
    update_state,
)
from agent_toolkit._hooks.task_stop_state import has_recent_completion, target_ids  # noqa: E402

if TYPE_CHECKING:
    from agent_toolkit._hooks.pretooluse.notices import (
        _block_notice,
        _llm_notice,
    )


def _handle_language_check(payload: dict, session_id: str) -> str | None:
    """直前メインエージェント応答の言語検査を実行し、セッション状態でエスカレーションを管理する。

    agents_serverが起動した委譲先セッションでは、作業途中の文章を呼び出し元もユーザーも読まないため検査しない。

    Returns:
        通知本文。対象外の場合はNone。

    検出した回の応答は既にユーザーへ届いており、当該ツール呼び出しを止めても当該応答は戻らない。
    以降の応答を日本語へ切り替えることで是正できるため、
    `agent-toolkit:writing-standards`の`references/claude-hooks.md`「遮断・警告フックの成立条件」の第1段により遮断しない。

    セッション状態キー:
    - english_warning_count: 連続英語ターンのカウンタ（int）
    - english_warning_msg_id: 前回検出時のmessage ID（str）

    エスカレーションロジック:
    - WARN: message IDが前回と異なればカウンタ+1、同一なら据え置き。カウンタ≧2で強い本文へ切り替える
    - PASS・SKIP: カウンタを0にリセットする。強い本文が「2ターン連続」と宣言するため、
      英語主体でないと判定した回を経た後は、1回の検出だけでは当該本文へ切り替えない
    - 切り替え後はカウンタを1に設定する（日本語に切り替わるまで毎ターン当該本文を返す）
    """
    transcript_path = payload.get("transcript_path", "")
    if not isinstance(transcript_path, str) or not transcript_path:
        return None
    if payload.get("isSidechain") is True:
        return None
    if os.environ.get("AGENT_TOOLKIT_DELEGATED_SESSION") == "1":
        return None

    outcome, body, msg_id = _response_language_check.detailed_check(transcript_path)

    if outcome in (
        _response_language_check.CheckOutcome.PASS,
        _response_language_check.CheckOutcome.SKIP,
    ):
        if session_id:

            def _reset_count(current: dict) -> dict | None:
                if current.get("english_warning_count", 0) == 0:
                    return None
                current["english_warning_count"] = 0
                return current

            update_state(session_id, _reset_count)
        return None

    # WARN
    if not session_id:
        return body

    # update_stateがOSErrorで失敗した場合、_incrementは実行されずcountは初期値0のまま残る。
    # この場合はブロックしない方向（安全側）にフォールバックする。
    count = 0
    duplicate = False

    def _increment(current: dict) -> dict | None:
        nonlocal count, duplicate
        prev_id = current.get("english_warning_msg_id", "")
        prev_count = current.get("english_warning_count", 0)
        if msg_id and prev_id == msg_id:
            count = prev_count
            duplicate = True
            return None
        count = prev_count + 1
        current["english_warning_count"] = count
        current["english_warning_msg_id"] = msg_id
        return current

    update_state(session_id, _increment)

    if duplicate:
        return None

    if count >= 2:

        def _set_threshold(current: dict) -> dict | None:
            current["english_warning_count"] = 1
            return current

        update_state(session_id, _set_threshold)
        return _response_language_check.BLOCK_BODY

    return body


# 日本語の応答指示を再注入するツール呼び出しの間隔。
# 2026-09-22以降のClaude Codeメイン記録で、セッション開始又は会話圧縮から最初の英語検知通知までの
# ツール呼び出し回数は55件で中央値20回、下位20%が7回、下位30%が11回だった。
# 10回ごとの注入は最初の英語化の約7割より前に日本語の指示を文脈の近くへ置き、注入は1行のため文脈の消費は小さい。
LANGUAGE_REINJECTION_INTERVAL = 10


def _advance_language_reinjection(payload: dict, session_id: str) -> bool:
    """メインセッションのツール呼び出しを数え、再注入の間隔に達した呼び出しで真を返す。

    委譲先（agents_serverの委譲先セッションと`agent_id`を持つサブエージェント）とセッション識別を
    確定できない呼び出しでは数えない。Codexの除外は呼び出し元が判定する。
    間隔に達した呼び出しでは回数を0へ戻す。SessionStartの注入時も`rules_context`が0へ戻す。
    """
    if not session_id or is_delegated(os.environ):
        return False
    if payload.get("agent_id") or payload.get("isSidechain") is True:
        return False
    reached = False
    key = _rules_context.LANGUAGE_REINJECTION_COUNT_KEY

    def _advance(current: dict) -> dict | None:
        nonlocal reached
        count = current.get(key, 0)
        if not isinstance(count, int) or isinstance(count, bool) or count < 0:
            count = 0
        count += 1
        if count >= LANGUAGE_REINJECTION_INTERVAL:
            reached = True
            count = 0
        current[key] = count
        return current

    update_state(session_id, _advance)
    return reached


# Claude CodeとCodexが生成するagents_serverの完全修飾MCP tool名。
_AGENTS_SERVER_NAMESPACES = _agents_server_tool_names.MCP_NAMESPACES
_AGENTS_SERVER_START_TOOLS = frozenset(
    f"{namespace}{tool}"
    for namespace in _AGENTS_SERVER_NAMESPACES
    for tool in ("start", "start_explore", "start_shell", "start_write")
)
_AGENTS_SERVER_SEND_TOOLS = frozenset(f"{namespace}send_message" for namespace in _AGENTS_SERVER_NAMESPACES)
_AGENTS_SERVER_KILL_TOOLS = frozenset(f"{namespace}kill" for namespace in _AGENTS_SERVER_NAMESPACES)
_AGENTS_SERVER_LIST_TOOLS = frozenset(f"{namespace}list" for namespace in _AGENTS_SERVER_NAMESPACES)
_AGENTS_SERVER_TOOL_NAMES = (
    _AGENTS_SERVER_START_TOOLS | _AGENTS_SERVER_SEND_TOOLS | _AGENTS_SERVER_KILL_TOOLS | _AGENTS_SERVER_LIST_TOOLS
)


# hooks.json・hooks.codex.jsonのPreToolUse matcherが被覆すべきagents_serverツール名の全体。
# 一致検査（pretooluse/dispatch_test.py）が実装側の集合として参照するため、下線接頭辞を付けない。
AGENTS_SERVER_HOOK_TOOL_NAMES = _AGENTS_SERVER_TOOL_NAMES

_AGENTS_SERVER_SESSION_CWD_KEY = "agents_server_cwd_by_session"
# --- 計画単位の状態管理 ---

# Skillツールの`skill`引数として許容するplan-modeスキル名。
# posttooluse.pyの`_PLAN_MODE_SKILL_NAMES`と対応させる。
_PLAN_MODE_SKILL_NAMES: frozenset[str] = frozenset({"agent-toolkit:plan-mode", "plan-mode"})


# --- TaskStop: 初回遮断と再実行窓 ---


def _check_task_stop(session_id: str, tool_input: dict) -> bool:
    """自セッションの所有記録又は対象別の停滞検知完了記録がある`TaskStop`だけを許可する。

    停止対象が状態キー`background_task_ids`へ記録済みの場合は遮断しない。
    当該キーは、PostToolUse(Bash)が`run_in_background`指定の応答から取得したタスクIDを
    記録したものであり、自セッションが起動して停止用の識別子を保持している対象を表す。
    起動主体の確認を要する遮断の対象は、自セッションの起動記録が無い停止に限る。

    `stall_detection_completed_at_by_task`に5分以内の一致記録がある場合も遮断しない。
    当該記録は待機手順を完了した主体が対象別に作成し、成功したPostToolUse(TaskStop)が消費する。

    いずれの記録も無い対象は、再実行回数にかかわらず遮断する。
    """
    now = time.time()
    state = read_state(session_id)
    recorded = state.get("background_task_ids")
    recorded_ids = {value for value in recorded if isinstance(value, str)} if isinstance(recorded, list) else set()
    targets = target_ids(tool_input)
    if targets & recorded_ids or has_recent_completion(session_id, targets, now=now):
        return False
    print(
        _block_notice(
            "blocked: TaskStop。現在のセッションには、指定した対象の所有記録も停滞検知完了記録も無い。"
            "背景タスクの停止は、ユーザーの明示的な即時停止要求があるか、"
            "停滞検知の手順を完了した場合に限る。"
            "当該手順の完了条件は`agent-toolkit:delegation`の"
            f"{_plugin_resources.skill_reference('delegation', 'references/waiting-and-monitoring.md')}"
            "「停滞の検知と巻き取り」節が定める。"
            "進行が遅いことや非効率に確認できることだけでは停止の指示にならない。"
            "意図の解釈が複数残る場合は、停止の前にAskUserQuestionで確認する。"
            "ユーザーの介入があった場合の扱いは`agent-toolkit:delegation`「継続と新規起動」が定める。",
            fix=(
                "自セッションが起動した対象は所有記録に一致する識別子を指定する。"
                "その他の対象は"
                f"{_plugin_resources.skill_reference('delegation', 'references/waiting-and-monitoring.md')}"
                "「停滞の検知と巻き取り」節に従い、"
                "対象別の停滞検知完了記録を作成してからTaskStopを実行する。"
                '記録は`uv run --project "${CLAUDE_PLUGIN_ROOT}" --locked --no-default-groups '
                '"${CLAUDE_PLUGIN_ROOT}/skills/delegation/scripts/record_stall_detection.py" '
                "--session-id <現在のCLAUDE_CODE_SESSION_ID> --task-id <停止対象の完全なタスクID>`で作成し、"
                "終了コード0を返した対象だけを5分以内に停止する。"
            ),
        ),
        file=sys.stderr,
    )
    return True


def _clear_current_plan_file_path(session_id: str) -> None:
    """plan-mode起動時に前の計画ファイルのパスを消去する。

    UserPromptSubmitが同じキーから計画名を`sessionTitle`へ出力するため、
    新しい計画を始めた後に前の計画名を出力し続けないようにする。
    """
    if not session_id:
        return

    def _clear(current: dict) -> dict | None:
        if current.pop("current_plan_file_path", None) is None:
            return None
        return current

    update_state(session_id, _clear)


# --- Codex App Server: isSidechainプローブ ---


def _record_iss_sidechain_probe(
    session_id: str,
    tool_name: str,
    payload: dict,
) -> None:
    """多重ネスト構成でのisSidechain実値採取用のデバッグログ記録。

    暫定機構: fb7 の実サンプル採取が目的。
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


def _check_agents_server_continuation_input(session_id: str, tool_input: dict, tool_name: str) -> bool | str:
    """`send_message`・`kill`の入力と保存済みcwdを検査する。

    必須値の欠落はツール自身が拒否する可逆な入力不成立として警告する。
    停止対象のcwd記録の欠落は所有を確認できないため遮断する。
    """
    display_name = tool_name.rsplit("__", 1)[-1]
    if tool_name in _AGENTS_SERVER_SEND_TOOLS:
        prompt = tool_input.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip():
            return _llm_notice(
                f"warn: {display_name}には空でない`prompt`が必要である。空でない`prompt`を指定して再実行する。",
                tag=_WARN_TAG,
                removable_cause=True,
            )
    remote_session_id = tool_input.get("session_id")
    if not isinstance(remote_session_id, str) or not remote_session_id:
        return _llm_notice(
            f"warn: {display_name}には空でない`session_id`が必要である。"
            "codex_startが返した`session_id`を使うか、codex_startで新しいセッションを開始する。",
            tag=_WARN_TAG,
            removable_cause=True,
        )
    state = read_state(session_id)
    cwd_map = state.get(_AGENTS_SERVER_SESSION_CWD_KEY)
    if not isinstance(cwd_map, dict) or not isinstance(cwd_map.get(remote_session_id), str):
        print(
            _block_notice(
                f"blocked: {display_name}は、`session_id`に対応する絶対`cwd`が保存されていないため続行できない。",
                fix=(
                    "対象が自身の起動した対象でない場合は、当該対象を起動した委譲先へ`send_message`で追送し、"
                    "当該委譲先に当該対象を打ち切らせる。"
                    "自身が所有する作業を続ける場合は、絶対`cwd`を指定したagents_serverのstartで新しいセッションを開始する。"
                ),
            ),
            file=sys.stderr,
        )
        return True
    return False
