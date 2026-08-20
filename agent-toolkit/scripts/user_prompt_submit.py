"""Claude Code・Codex plugin agent-toolkit: UserPromptSubmitセッション状態記録。

ホスト別コマンド形式（Claude Codeは`/agent-toolkit:<name>`・`/<name>`、
Codexは`$agent-toolkit:<name>`・`$<name>`）でのスキル起動を検出し、
対応するセッション状態フラグを立てる。
既存のPostToolUse(Skill)経由の記録では捕捉できない手動起動を補完する。

検出対象スキルと対応フラグ:

- plan-mode → `plan_mode_skill_invoked`
- session-review → `session_review_invoked`（辞書。キーは`agent-toolkit:session-review`で正規化）
- process-feedbacks → `process_feedbacks_skill_invoked`
- plan-and-add-feedback → `plan_and_add_feedback_skill_invoked`
- add-feedback → `add_feedback_skill_invoked`

例外時はfail-openで exit 0 を返す。
session-reviewの手動コマンド完全一致時は、payloadの`session_id`と`transcript_path`を
変更せず`additionalContext`へ渡す。
"""

from __future__ import annotations

import json
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))

from _hook_tool_input import is_codex_payload  # noqa: E402  # pylint: disable=wrong-import-position,import-error
from _message_format import llm_notice as _llm_notice_base  # noqa: E402  # pylint: disable=wrong-import-position,import-error
from _plan_file import is_plan_file  # noqa: E402  # pylint: disable=wrong-import-position,import-error
from _session_state import update_state  # noqa: E402  # pylint: disable=wrong-import-position,import-error
from posttooluse import (  # noqa: E402  # pylint: disable=wrong-import-position,import-error
    _ADD_FEEDBACK_SKILL_NAMES,
    _PLAN_AND_ADD_FEEDBACK_SKILL_NAMES,
    _PLAN_MODE_SKILL_NAMES,
    _PROCESS_FEEDBACKS_SKILL_NAMES,
    _SESSION_REVIEW_SKILL_NAMES,
)


def _extend_with_short_names(names: frozenset[str]) -> frozenset[str]:
    """フルスキル名`agent-toolkit:<name>`から短縮名`<name>`を追加した拡張集合を返す。

    posttooluse.py側の集合定数には短縮名が未登録のスキル
    （session-review）が存在するため、
    UserPromptSubmit経路のスラッシュコマンド検出用に補完する。
    """
    extended = set(names)
    for name in names:
        if ":" in name:
            _, short = name.split(":", 1)
            if short:
                extended.add(short)
    return frozenset(extended)


# スラッシュコマンド起動時にも検出できるように、フルネームと短縮名の両方を含む拡張集合を組み立てる。
_PLAN_MODE_NAMES_EXTENDED = _extend_with_short_names(_PLAN_MODE_SKILL_NAMES)
_SESSION_REVIEW_NAMES_EXTENDED = _extend_with_short_names(_SESSION_REVIEW_SKILL_NAMES)
_PROCESS_FEEDBACKS_NAMES_EXTENDED = _extend_with_short_names(_PROCESS_FEEDBACKS_SKILL_NAMES)
_PLAN_AND_ADD_FEEDBACK_NAMES_EXTENDED = _extend_with_short_names(_PLAN_AND_ADD_FEEDBACK_SKILL_NAMES)
_ADD_FEEDBACK_NAMES_EXTENDED = _extend_with_short_names(_ADD_FEEDBACK_SKILL_NAMES)

# ホスト判定後の手動コマンドから<name>を抽出する。
# 先頭記号の直後に`agent-toolkit:`prefixがある場合と無い場合の両方を許容する。
# スキル名として妥当な文字（英数・ハイフン・アンダースコア）のみを対象とする。
_SKILL_COMMAND_PATTERN = re.compile(r"\A(?:agent-toolkit:)?([A-Za-z0-9][A-Za-z0-9_-]*)\b")
_HARNESS_MESSAGE_RE = re.compile(r"^\s*<task-notification\b")
_SESSION_REVIEW_COMMAND_NAMES = frozenset({"session-review", "agent-toolkit:session-review"})
_HOOK_ID = "agent-toolkit/user-prompt-submit"


def _is_harness_message(prompt: str) -> bool:
    """ハーネスが挿入したメッセージかを判定する。

    ハーネス通知を利用者のスラッシュコマンドとして扱わないために用いる。
    """
    return _HARNESS_MESSAGE_RE.search(prompt) is not None


def _resolve_canonical_name(name: str, extended: frozenset[str], canonical: frozenset[str]) -> str | None:
    """<name>が拡張集合に含まれる場合、正規名（フルスキル名優先）を返す。

    セッション状態フラグの辞書キー正規化に使う。
    フルスキル名が候補にあればそれを、無ければ短縮名をそのまま返す。
    """
    if name not in extended:
        return None
    for candidate in canonical:
        if candidate == name or candidate.endswith(":" + name):
            return candidate
    return name


def _set_plan_mode_invoked(state: dict) -> dict | None:
    if state.get("plan_mode_skill_invoked", False):
        return None
    state["plan_mode_skill_invoked"] = True
    return state


def _make_session_review_mutator(canonical_name: str):
    def _mutator(state: dict) -> dict | None:
        invoked = state.get("session_review_invoked")
        if not isinstance(invoked, dict):
            invoked = {}
        if invoked.get(canonical_name) is True:
            return None
        invoked[canonical_name] = True
        state["session_review_invoked"] = invoked
        return state

    return _mutator


def _set_process_feedbacks_invoked(state: dict) -> dict | None:
    if state.get("process_feedbacks_skill_invoked", False):
        return None
    state["process_feedbacks_skill_invoked"] = True
    return state


def _set_named_flag(key: str):
    """指定した自動振り返り起点フラグを冪等に真化する。"""

    def _mutator(state: dict) -> dict | None:
        if state.get(key, False):
            return None
        state[key] = True
        return state

    return _mutator


def _session_review_context(session_id: str, transcript_path: str) -> str:
    """手動振り返りへpayloadのセッション識別子とtranscript絶対パスを渡す本文を返す。"""
    return _llm_notice_base(
        f"Use these exact values for agent-toolkit:session-review: session_id={session_id}; transcript_path={transcript_path}",
        _HOOK_ID,
    )


def _plan_session_title(session_id: str) -> str | None:
    """計画ファイルのstemをClaude CodeのsessionTitleへ一度だけ反映する。"""
    emitted: list[str] = []

    def _set_title(state: dict) -> dict | None:
        raw_plan_path = state.get("current_plan_file_path")
        if not isinstance(raw_plan_path, str) or not raw_plan_path or not is_plan_file(raw_plan_path):
            return None
        plan_stem = pathlib.Path(raw_plan_path).stem
        if not plan_stem:
            return None

        if state.get("last_hook_session_title"):
            return None

        state["last_hook_session_title"] = plan_stem
        emitted.append(plan_stem)
        return state

    update_state(session_id, _set_title)
    return emitted[0] if emitted else None


def _emit_hook_output(*, additional_context: str | None = None, session_title_output: str | None = None) -> None:
    """UserPromptSubmitの追加情報とsessionTitleを1つの応答JSONへまとめて出力する。"""
    hook_specific_output: dict[str, str] = {"hookEventName": "UserPromptSubmit"}
    if additional_context is not None:
        hook_specific_output["additionalContext"] = additional_context
    if session_title_output is not None:
        hook_specific_output["sessionTitle"] = session_title_output
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

    prompt = payload.get("prompt")
    if not isinstance(prompt, str) or not prompt:
        return 0

    # 公式契約では`prompt`は利用者の送信本文である。実装版2.1.221で観測した
    # `<task-notification>`通知の混入経路だけを防御的に除外し、一般的な入力契約とは扱わない。
    if _is_harness_message(prompt):
        return 0

    is_codex = "model" in payload or is_codex_payload(payload)

    # Claude CodeのUserPromptSubmitだけがsessionTitleを出力する。
    # Codexはスキル起動の状態記録だけを行い、計画名を出力しない。
    plan_session_title = None
    if not is_codex:
        plan_session_title = _plan_session_title(session_id)

    # 先頭行のみを取り出して照合する（先頭行以外は無視）。
    first_line = prompt.split("\n", 1)[0].strip()
    command_prefix = "$" if is_codex else "/"
    if not first_line.startswith(command_prefix):
        if plan_session_title is not None:
            _emit_hook_output(session_title_output=plan_session_title)
        return 0

    match = _SKILL_COMMAND_PATTERN.match(first_line[len(command_prefix) :])
    if match is None:
        if plan_session_title is not None:
            _emit_hook_output(session_title_output=plan_session_title)
        return 0

    name = match.group(1)
    full_name = f"agent-toolkit:{name}"
    exact_session_review_commands = {command_prefix + name for name in _SESSION_REVIEW_COMMAND_NAMES}
    exact_session_review_command = prompt.strip() in exact_session_review_commands

    # 対応スキル別にフラグを設定する。
    if name in _PLAN_MODE_NAMES_EXTENDED or full_name in _PLAN_MODE_SKILL_NAMES:
        update_state(session_id, _set_plan_mode_invoked)
    additional_context = None
    if name in _SESSION_REVIEW_NAMES_EXTENDED or full_name in _SESSION_REVIEW_SKILL_NAMES:
        canonical = _resolve_canonical_name(name, _SESSION_REVIEW_NAMES_EXTENDED, _SESSION_REVIEW_SKILL_NAMES) or full_name
        update_state(session_id, _make_session_review_mutator(canonical))
        raw_transcript_path = payload.get("transcript_path")
        transcript_path = raw_transcript_path if isinstance(raw_transcript_path, str) else ""
        if exact_session_review_command:
            additional_context = _session_review_context(session_id, transcript_path)
    if name in _PROCESS_FEEDBACKS_NAMES_EXTENDED or full_name in _PROCESS_FEEDBACKS_SKILL_NAMES:
        update_state(session_id, _set_process_feedbacks_invoked)
    if name in _PLAN_AND_ADD_FEEDBACK_NAMES_EXTENDED or full_name in _PLAN_AND_ADD_FEEDBACK_SKILL_NAMES:
        update_state(session_id, _set_named_flag("plan_and_add_feedback_skill_invoked"))
    if name in _ADD_FEEDBACK_NAMES_EXTENDED or full_name in _ADD_FEEDBACK_SKILL_NAMES:
        update_state(session_id, _set_named_flag("add_feedback_skill_invoked"))

    if additional_context is not None or plan_session_title is not None:
        _emit_hook_output(additional_context=additional_context, session_title_output=plan_session_title)

    return 0
