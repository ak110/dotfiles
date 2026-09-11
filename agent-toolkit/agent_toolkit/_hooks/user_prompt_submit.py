"""Claude Code・Codex plugin agent-toolkit: UserPromptSubmitセッション状態記録と応答契約注入。

ホスト別コマンド形式（Claude Codeは`/agent-toolkit:<name>`・`/<name>`、
Codexは`$agent-toolkit:<name>`・`$<name>`）でのスキル起動を検出し、
対応するセッション状態フラグを立てる。
既存のPostToolUse(Skill)経由の記録では捕捉できない手動起動を補完する。

検出対象スキルと対応フラグ:

- plan-mode → `plan_mode_skill_invoked`
- process-wi → `process_wi_skill_invoked`

Claude CodeのsessionTitleは、process-loop起動セッションでは`process-loop`、
process-wi手動起動セッションでは`process-wi`の固定値を優先する
（両条件が真の場合はprocess-loopを優先する）。
いずれにも該当しないセッションは従来どおり計画ファイルのstemを一度だけ反映する。
固定値の判定は、当該呼び出しでのスキル起動フラグ更新の後に行う
（同一呼び出しで検出したスラッシュコマンド起動を、その場でsessionTitleへ反映するため）。

例外時はfail-openで exit 0 を返す。
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import time

from agent_toolkit._hooks.notice import (  # noqa: E402  # pylint: disable=wrong-import-position,import-error
    _WARN_TAG,
    set_warning_session_id,
)

# pylint: disable-next=wrong-import-position,import-error
from agent_toolkit._hooks.notice import formatter as _notice_formatter  # noqa: E402
from agent_toolkit._hooks.posttooluse import (  # noqa: E402  # pylint: disable=wrong-import-position,import-error
    _PLAN_MODE_SKILL_NAMES,
    _PROCESS_WI_SKILL_NAMES,
)
from agent_toolkit._hooks.session_state import (  # noqa: E402  # pylint: disable=wrong-import-position,import-error
    claim_session_title,
    read_state,
    update_state,
)
from agent_toolkit._hooks.tool_input import is_codex_payload  # noqa: E402  # pylint: disable=wrong-import-position,import-error
from agent_toolkit._plan.locations import is_plan_main_file  # noqa: E402  # pylint: disable=wrong-import-position,import-error


def _extend_with_short_names(names: frozenset[str]) -> frozenset[str]:
    """フルスキル名`agent-toolkit:<name>`から短縮名`<name>`を追加した拡張集合を返す。"""
    extended = set(names)
    for name in names:
        if ":" in name:
            _, short = name.split(":", 1)
            if short:
                extended.add(short)
    return frozenset(extended)


# スラッシュコマンド起動時にも検出できるように、フルネームと短縮名の両方を含む拡張集合を組み立てる。
_PLAN_MODE_NAMES_EXTENDED = _extend_with_short_names(_PLAN_MODE_SKILL_NAMES)
_PROCESS_WI_NAMES_EXTENDED = _extend_with_short_names(_PROCESS_WI_SKILL_NAMES)

# process-loop起動セッションであることを示す環境変数名（`autonomous_exit.py`と同じ）。
_ENV_PROCESS_LOOP_SESSION = "AGENT_TOOLKIT_PROCESS_LOOP_SESSION"
_LEGACY_ENV_PROCESS_LOOP_SESSION = "DOTFILES_AUTONOMOUS_EXIT_REQUIRED"

# ホスト判定後の手動コマンドから<name>を抽出する。
# 先頭記号の直後に`agent-toolkit:`prefixがある場合と無い場合の両方を許容する。
# スキル名として妥当な文字（英数・ハイフン・アンダースコア）のみを対象とする。
_SKILL_COMMAND_PATTERN = re.compile(r"\A(?:agent-toolkit:)?([A-Za-z0-9][A-Za-z0-9_-]*)\b")
_HARNESS_MESSAGE_RE = re.compile(r"^\s*<task-notification\b")
_VERIFICATION_NOTICE_INTERVAL_SECONDS = 180.0
_LAST_USER_PROMPT_AT_KEY = "last_user_prompt_at"
_VERIFICATION_NOTICE_BODY = (
    "発話が示す事実と是正要求は現物（原文・実装・規範・実行結果）で照合してから応答する。"
    "照合に用いた手段と結果を応答へ書く。照合できない場合は同意も変更もしない。"
    "同一の論点で2回目以降の差し替えを求められた場合は`AskUserQuestion`で意図を確認する。"
)
_llm_notice = _notice_formatter("agent-toolkit/user_prompt_submit")


def _is_harness_message(prompt: str) -> bool:
    """ハーネスが挿入したメッセージかを判定する。

    ハーネス通知をユーザーのスラッシュコマンドとして扱わないために用いる。
    """
    return _HARNESS_MESSAGE_RE.search(prompt) is not None


def _set_plan_mode_invoked(state: dict) -> dict | None:
    if state.get("plan_mode_skill_invoked", False):
        return None
    state["plan_mode_skill_invoked"] = True
    return state


def _set_process_wi_invoked(state: dict) -> dict | None:
    if state.get("process_wi_skill_invoked", False):
        return None
    state["process_wi_skill_invoked"] = True
    return state


def _fixed_session_title(session_id: str) -> str | None:
    """process-loop起動またはprocess-wi手動起動セッションの固定sessionTitleを返す。

    両条件が真の場合はprocess-loopを優先する。計画ファイルstemの反映（`claim_session_title`
    経由で一度だけ確定する）とは異なり、対象セッションである間は呼び出しごとに同じ固定値を返す。
    """
    if os.environ.get(_ENV_PROCESS_LOOP_SESSION) == "1" or os.environ.get(_LEGACY_ENV_PROCESS_LOOP_SESSION) == "1":
        return "process-loop"
    if read_state(session_id).get("process_wi_skill_invoked") is True:
        return "process-wi"
    return None


def _plan_session_title(session_id: str) -> str | None:
    """計画ファイルのstemをClaude CodeのsessionTitleへ一度だけ反映する。"""
    raw_plan_path = read_state(session_id).get("current_plan_file_path")
    if not isinstance(raw_plan_path, str) or not raw_plan_path or not is_plan_main_file(raw_plan_path):
        return None
    plan_stem = pathlib.Path(raw_plan_path).stem
    if not plan_stem or not claim_session_title(session_id, plan_stem):
        return None
    return plan_stem


def _claim_verification_notice(session_id: str, now: float) -> bool:
    """通常発話の時刻を更新し、照合指示を注入する場合に真を返す。"""
    claimed = False

    def update_timestamp(state: dict) -> dict:
        nonlocal claimed
        previous = state.get(_LAST_USER_PROMPT_AT_KEY)
        if isinstance(previous, (int, float)) and not isinstance(previous, bool):
            claimed = now - previous >= _VERIFICATION_NOTICE_INTERVAL_SECONDS
        state[_LAST_USER_PROMPT_AT_KEY] = now
        return state

    update_state(session_id, update_timestamp)
    return claimed


def _emit_hook_output(*, session_title_output: str | None, additional_context: str | None) -> None:
    """UserPromptSubmitの値を持つ応答欄を1つのJSONとして出力する。"""
    hook_specific_output: dict[str, str] = {"hookEventName": "UserPromptSubmit"}
    if session_title_output is not None:
        hook_specific_output["sessionTitle"] = session_title_output
    if additional_context is not None:
        hook_specific_output["additionalContext"] = additional_context
    print(json.dumps({"hookSpecificOutput": hook_specific_output}, ensure_ascii=False))


def main(payload_text: str) -> int:
    """エントリポイント。終了コードは常に0（fail-open原則）。"""
    try:
        payload = json.loads(payload_text)
    except (json.JSONDecodeError, ValueError):
        return 0

    if not isinstance(payload, dict):
        return 0

    session_id = payload.get("session_id", "")
    if not isinstance(session_id, str) or not session_id:
        return 0
    set_warning_session_id(session_id)

    prompt = payload.get("prompt")
    if not isinstance(prompt, str) or not prompt:
        return 0

    # 公式契約では`prompt`はユーザーの送信本文である。実装版2.1.221で観測した
    # `<task-notification>`通知の混入経路だけを防御的に除外し、一般的な入力契約とは扱わない。
    if _is_harness_message(prompt):
        return 0

    is_codex = "model" in payload or is_codex_payload(payload)
    first_line = prompt.split("\n", 1)[0].strip()
    command_prefix = "$" if is_codex else "/"
    is_normal_prompt = not first_line.startswith(command_prefix)
    additional_context = None
    if is_normal_prompt and _claim_verification_notice(session_id, time.time()):
        # 発火条件は受領側が変更できないため、原因の除去を求める反復注記を付けない。
        additional_context = _llm_notice(_VERIFICATION_NOTICE_BODY, tag=_WARN_TAG, removable_cause=False)

    if not is_normal_prompt:
        match = _SKILL_COMMAND_PATTERN.match(first_line[len(command_prefix) :])
        if match is not None:
            name = match.group(1)
            full_name = f"agent-toolkit:{name}"

            # 対応スキル別にフラグを設定する。sessionTitleの固定値判定より先に行い、
            # 当該呼び出しでの起動を同じ応答へ反映できるようにする。
            if name in _PLAN_MODE_NAMES_EXTENDED or full_name in _PLAN_MODE_SKILL_NAMES:
                update_state(session_id, _set_plan_mode_invoked)
            if name in _PROCESS_WI_NAMES_EXTENDED or full_name in _PROCESS_WI_SKILL_NAMES:
                update_state(session_id, _set_process_wi_invoked)

    # Claude CodeのUserPromptSubmitだけがsessionTitleを出力する。
    # Codexはスキル起動の状態記録だけを行い、sessionTitleを出力しない。
    session_title = None
    if not is_codex:
        session_title = _fixed_session_title(session_id)
        if session_title is None:
            session_title = _plan_session_title(session_id)

    if session_title is not None or additional_context is not None:
        _emit_hook_output(session_title_output=session_title, additional_context=additional_context)

    return 0
