"""Claude Code・Codex plugin agent-toolkit: UserPromptSubmitセッション状態記録。

ホスト別コマンド形式（Claude Codeは`/agent-toolkit:<name>`・`/<name>`、
Codexは`$agent-toolkit:<name>`・`$<name>`）でのスキル起動を検出し、
対応するセッション状態フラグを立てる。
既存のPostToolUse(Skill)経由の記録では捕捉できない手動起動を補完する。

検出対象スキルと対応フラグ:

- plan-mode → `plan_mode_skill_invoked`
- process-wi → `process_wi_skill_invoked`

例外時はfail-openで exit 0 を返す。
"""

from __future__ import annotations

import json
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))

from _hook_tool_input import is_codex_payload  # noqa: E402  # pylint: disable=wrong-import-position,import-error
from _plan_file import is_plan_main_file  # noqa: E402  # pylint: disable=wrong-import-position,import-error
from _session_state import (  # noqa: E402  # pylint: disable=wrong-import-position,import-error
    claim_session_title,
    read_state,
    update_state,
)
from posttooluse import (  # noqa: E402  # pylint: disable=wrong-import-position,import-error
    _PLAN_MODE_SKILL_NAMES,
    _PROCESS_AWIS_SKILL_NAMES,
)


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
_PROCESS_AWIS_NAMES_EXTENDED = _extend_with_short_names(_PROCESS_AWIS_SKILL_NAMES)

# ホスト判定後の手動コマンドから<name>を抽出する。
# 先頭記号の直後に`agent-toolkit:`prefixがある場合と無い場合の両方を許容する。
# スキル名として妥当な文字（英数・ハイフン・アンダースコア）のみを対象とする。
_SKILL_COMMAND_PATTERN = re.compile(r"\A(?:agent-toolkit:)?([A-Za-z0-9][A-Za-z0-9_-]*)\b")
_HARNESS_MESSAGE_RE = re.compile(r"^\s*<task-notification\b")


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


def _set_process_awis_invoked(state: dict) -> dict | None:
    if state.get("process_wi_skill_invoked", False):
        return None
    state["process_wi_skill_invoked"] = True
    return state


def _plan_session_title(session_id: str) -> str | None:
    """計画ファイルのstemをClaude CodeのsessionTitleへ一度だけ反映する。"""
    raw_plan_path = read_state(session_id).get("current_plan_file_path")
    if not isinstance(raw_plan_path, str) or not raw_plan_path or not is_plan_main_file(raw_plan_path):
        return None
    plan_stem = pathlib.Path(raw_plan_path).stem
    if not plan_stem or not claim_session_title(session_id, plan_stem):
        return None
    return plan_stem


def _emit_hook_output(*, session_title_output: str) -> None:
    """UserPromptSubmitのsessionTitleを応答JSONとして出力する。"""
    hook_specific_output: dict[str, str] = {
        "hookEventName": "UserPromptSubmit",
        "sessionTitle": session_title_output,
    }
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

    # 公式契約では`prompt`はユーザーの送信本文である。実装版2.1.221で観測した
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

    # 対応スキル別にフラグを設定する。
    if name in _PLAN_MODE_NAMES_EXTENDED or full_name in _PLAN_MODE_SKILL_NAMES:
        update_state(session_id, _set_plan_mode_invoked)
    if name in _PROCESS_AWIS_NAMES_EXTENDED or full_name in _PROCESS_AWIS_SKILL_NAMES:
        update_state(session_id, _set_process_awis_invoked)

    if plan_session_title is not None:
        _emit_hook_output(session_title_output=plan_session_title)

    return 0
