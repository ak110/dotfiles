"""`atk run-skill`が起動したセッションの報告保存Stopフック。

環境変数`AGENT_TOOLKIT_RUN_SKILL_ID`と起動入力（`AGENT_TOOLKIT_SESSION_START_INPUT`のskill）を持つ最上位のセッションを対象とする。
対象セッションで、当該実行の報告用UWIが起動元の検収と同じ基準（`has_synced_run_report`）で保存されていなければ、
ターンの終了を遮断して報告の保存を促す。
完了まで続行させる働きは本判定が担う。

判定順序は以下のとおり。

1. run-skillが起動したセッションでない、または起動入力からskillを得られない: approve
2. hookの呼出主体が最上位でない（`Agent`ツールの委譲先、agents_serverの委譲先）: approve
3. `is_pending_async_work`が真: 非同期処理または未回収の結果が残るためapprove
   （再開後のStopで改めて判定する。Codexでは判定せず偽とする）
4. 報告用UWIの保存を確認できる: approve
5. 上記いずれでもない（保存確認の失敗を含む）: blockし、報告の保存を促す

連続blockの上限は`stop.py`が管理する。上限に達した場合はCLIが終了し、起動元が既存の「報告用UWI未保存」の失敗を報告する。

委譲先での実行可否: 委譲先は最上位セッションの報告を保存しないため、hook入力と環境印で除外する。
"""

import json
import os
import subprocess

from agent_toolkit._hooks.notice import block_formatter as _block_notice_formatter

# 関数の実行時にだけ使う依存は、使用時まで読み込まない。
lazy from agent_toolkit._atk.wi import add as _wi_add
lazy from agent_toolkit._common import delegated_session as _delegated_session
lazy from agent_toolkit._common import mcp_environment as _mcp_environment
lazy from agent_toolkit._common import private_notes as _private_notes
lazy from agent_toolkit._common.session_state import read_state
lazy from agent_toolkit._hooks.agent_id import is_main_agent_context
lazy from agent_toolkit._hooks.background_tasks import is_pending_async_work_from_payload
lazy from agent_toolkit._hooks.stop_session import append_stop_log
lazy from agent_toolkit._hooks.stop_session import parse_stop_session as _parse_stop_session

_HOOK_ID = "run_skill_report_gate"

# run-skillの起動元が渡す実行識別子の環境変数。
RUN_ID_ENV = "AGENT_TOOLKIT_RUN_SKILL_ID"

_REASON_BODY = """\
`atk run-skill`が起動したこのセッションで、当該実行の報告用UWIの保存（コミットと必要なプッシュ）を確認できず、終了を止めた。
作業の完了・未完了は判定していない。"""

_block_notice = _block_notice_formatter(_HOOK_ID)


def _run_target(environ: dict[str, str] | os._Environ[str]) -> tuple[str, str] | None:
    """run-skillの実行識別子とskill名を返す。対象外ならNoneを返す。"""
    run_id = environ.get(RUN_ID_ENV)
    if not run_id:
        return None
    try:
        startup = json.loads(environ.get(_mcp_environment.SESSION_START_INPUT_ENV) or "")
    except ValueError:
        return None
    skill = startup.get("skill") if isinstance(startup, dict) else None
    if not isinstance(skill, str) or not skill:
        return None
    return run_id, skill


def evaluate(payload_text: str) -> tuple[str, str]:
    """報告保存の判定結果と、遮断する場合の理由を返す。"""
    resolved = _parse_stop_session(payload_text, lambda: None)
    if resolved is None:
        return "approve", ""
    session_id, payload = resolved

    target = _run_target(os.environ)
    if target is None:
        return "approve", ""
    run_id, skill = target

    if not is_main_agent_context(payload) or _delegated_session.is_delegated(os.environ):
        append_stop_log(session_id, "approve_run_skill_delegated_session", {})
        return "approve", ""

    if is_pending_async_work_from_payload(payload, session_id, session_state=read_state(session_id)):
        append_stop_log(session_id, "approve_run_skill_pending_async", {})
        return "approve", ""

    try:
        saved = _wi_add.has_synced_run_report(_private_notes.default_private_notes(), run_id=run_id, skill=skill)
    except (OSError, subprocess.SubprocessError, ValueError) as error:
        # 起動元の検収と同じく、確認できない場合は未保存として扱う。
        append_stop_log(session_id, "run_skill_report_check_error", {"error": f"{type(error).__name__}: {error}"})
        saved = False
    if saved:
        append_stop_log(session_id, "approve_run_skill_report_saved", {})
        return "approve", ""

    append_stop_log(session_id, "block_run_skill_report_unsaved", {"run_id": run_id, "skill": skill})
    reason = _block_notice(
        _REASON_BODY,
        fix=(
            "残る工程を進め、`agent-toolkit:report-completion`の手順で報告用UWIを保存してコミット・プッシュせよ。"
            "前提不足などで完了できない場合も、未完了の内容を報告用UWIへ保存してから終えよ。"
        ),
    )
    return "block", reason
