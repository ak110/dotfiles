"""agent-toolkitのホスト共通フックエントリポイント。

`hooks.json`に登録されたフックスクリプト（`pretooluse.py`等）は、いずれも末尾で
同型の`try: sys.exit(main()) except Exception: traceback.print_exc(); sys.exit(0)`
epilogueを個別に持っていた。本スクリプトへ集約し、各モジュールは`main()`関数の定義のみを
担うライブラリへ縮小する。第1引数でサブコマンド（対象モジュール名）を指定する。

標準入力は各フックに共通する処理で生バイト列として一度だけ読み、UTF-8で厳密にデコードして各モジュールへ渡す。
標準出力と標準エラーもUTF-8へ統一する。`AGENT_TOOLKIT_HOOK_PAYLOAD_DUMP`が非空の場合は、
環境によって異なる入力変換をバイト単位で調査できるよう、デコード前の入力を指定ディレクトリへ保存する。

依存パッケージはplugin rootのuvプロジェクトへ集約する。全サブコマンドが同じvenvを共有し、
サブコマンド切替時の再解決コストを生じさせない。

読込・実行の障害は、通知欄を持つイベントで標識付き追加コンテキストへ集約する。
イベントを確定できない入力と、モデルへの非遮断配送ができない終了イベントでは通知を出力しない。
内部出力をホスト形式へ変換した後、実出力した通知だけを配送済みとして記録する。
"""

import contextlib
import importlib
import io
import json
import os
import pathlib
import sys
import types

# 正常な呼出しで使わない診断とdump用の依存を、必要になるまで読み込まない。
lazy import datetime

from agent_toolkit._common import inherited_venv
lazy from agent_toolkit._common import session_launchers as _session_launchers
lazy from agent_toolkit._common.plugin_roots import plugin_resolution_scope
lazy from agent_toolkit._hooks import agent_id as _agent_id
lazy from agent_toolkit._hooks import notice as _notice
lazy from agent_toolkit._hooks.notice import formatter as _notice_formatter

_SUBCOMMANDS: frozenset[str] = frozenset(
    {
        "pretooluse",
        "posttooluse",
        "stop",
        "subagent_stop_advisor",
        "session_end_cleanup",
        "stopfailure_notifier",
        "permissionrequest",
        "rules_context",
        "rules_context_codex",
        "user_prompt_submit",
        "session_activity",
        "antigravity",
    }
)


def _configure_standard_output() -> None:
    """標準出力と標準エラーをUTF-8へ統一する。"""
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if isinstance(sys.stderr, io.TextIOWrapper):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def _dump_payload(subcommand: str, payload_bytes: bytes) -> None:
    """デバッグ指定時にデコード前のpayloadを保存し、失敗時は処理を継続する。"""
    dump_directory = os.environ.get("AGENT_TOOLKIT_HOOK_PAYLOAD_DUMP", "")
    if not dump_directory:
        return
    timestamp = datetime.datetime.now(datetime.UTC).strftime("%Y%m%dT%H%M%S%fZ")
    dump_path = pathlib.Path(dump_directory) / f"{subcommand}-{timestamp}-{os.getpid()}.json"
    with contextlib.suppress(OSError):
        dump_path.write_bytes(payload_bytes)


def _record_activity(payload: object, *, output: str = "", returncode: int = 0) -> None:
    """既存hookの応答を変えずに、同じ呼び出しで活動を記録する。記録の失敗はhook本体の処理へ影響させない。"""
    if not isinstance(payload, dict) or not payload.get("hook_event_name") or not payload.get("session_id"):
        return
    with contextlib.suppress(Exception):
        activity = importlib.import_module("agent_toolkit._common.session_activity")
        activity.record_activity(payload, output=output, returncode=returncode)
        if payload.get("hook_event_name") == "UserPromptSubmit":
            review = importlib.import_module("agent_toolkit._atk.run_skill_batch")
            review.hook_request(payload, host="codex" if os.environ.get("CODEX_THREAD_ID") else "claude")


def main(argv: list[str]) -> int:
    """サブコマンド名から対象モジュールを解決し`main()`を呼び出す。"""
    inherited_venv.strip_inherited_venv(os.environ)
    _configure_standard_output()
    _notice.begin_delivery()
    _notice.set_warning_session_id("")
    if not argv or argv[0] not in _SUBCOMMANDS:
        return 0
    payload_bytes = sys.stdin.buffer.read()
    _dump_payload(argv[0], payload_bytes)
    try:
        payload_text = payload_bytes.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        return 0
    payload: object = None
    with contextlib.suppress(Exception):
        payload = json.loads(payload_text)
    if isinstance(payload, dict) and isinstance(payload.get("session_id"), str):
        _notice.set_warning_session_id(payload["session_id"])
        if isinstance(payload, dict):
            session_id = payload.get("session_id")
            transcript_path = payload.get("transcript_path")
            if isinstance(session_id, str) and isinstance(transcript_path, str):
                session_state = importlib.import_module("agent_toolkit._common.session_state")
                session_state.inherit_state_from_transcript(session_id, transcript_path)
    # Stopの処理中も活動中として扱い、正常な応答が決まった後だけ終了候補へ進める。
    if argv[0] != "session_activity":
        _record_activity(payload, returncode=2 if argv[0] in {"stop", "subagent_stop_advisor"} else 0)
    try:
        module = importlib.import_module(f"agent_toolkit._hooks.{argv[0]}")
    except Exception as exc:  # noqa: BLE001 -- 読込障害でも操作を継続する
        _failure_response(argv[0], exc)
        return 0
    cycle: contextlib.AbstractContextManager[None] = contextlib.nullcontext()
    if isinstance(payload, dict) and payload.get("session_id") and argv[0] != "pretooluse":
        cycle = _notice.notice_cycle(str(payload.get("session_id", "")), argv[0], _agent_id.resolve_hook_agent_id(payload))
    resolution: contextlib.AbstractContextManager[None] = contextlib.nullcontext()
    if isinstance(payload, dict) and payload.get("hook_event_name") != "SessionStart":
        session_id = _session_launchers.valid_launcher(payload.get("session_id"))
        if session_id is not None:
            # 全handlerの宣言・動的参照・読取記録で、同じ要求とsessionの一覧を共有する。
            # SessionStartは自身がcacheを消去するため、ここで保存済み一覧を読み戻さない。
            resolution = plugin_resolution_scope(session_id=session_id)
    try:
        with cycle, resolution:
            return _invoke_module(module, payload_text, payload, argv)
    except Exception as exc:  # noqa: BLE001 -- フック障害でも操作を継続する
        _failure_response(argv[0], exc)
        return 0


def _failure_response(subcommand: str, error: Exception, response: dict | None = None) -> dict:
    """通知を受理するイベントだけへ、既存応答と単一JSONで障害を返す。"""
    events = {"pretooluse": "PreToolUse", "posttooluse": "PostToolUse", "user_prompt_submit": "UserPromptSubmit"}
    result = response if response is not None else {}
    if subcommand in events:
        specific = result.setdefault("hookSpecificOutput", {})
        specific["hookEventName"] = events[subcommand]
        warning = _notice_formatter("hook")(
            f"[{subcommand}] 想定外エラー: {type(error).__name__}: {error}",
            tag="warn",
            removable_cause=False,
            fix="操作を継続せよ。再発する場合はagent-toolkitの不具合として委譲元またはユーザーへ報告せよ。",
        )
        specific["additionalContext"] = "\n\n".join(filter(None, (specific.get("additionalContext"), warning)))
    if response is None and result:
        _notice.emit_response(result)
    return result


def _invoke_module(module: types.ModuleType, payload_text: str, payload: object, argv: list[str]) -> int:
    """内部出力を捕捉し、ホストへ採用した本文の配送だけを確定する。"""
    output = io.StringIO()
    error = None
    result = 0
    with _notice.defer_delivery(), contextlib.redirect_stdout(output):
        try:
            if argv[0] == "antigravity":
                if len(argv) != 2:
                    raise ValueError("antigravityにはイベント名を1個渡す")
                result = module.main(payload_text, event=argv[1])
            else:
                result = module.main(payload_text)
        except Exception as exc:  # noqa: BLE001 -- 部分出力の後も単一JSONを保つ
            error = exc
    raw = output.getvalue()
    response = None
    with contextlib.suppress(ValueError):
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            response = parsed
    if error is not None:
        response = _failure_response(argv[0], error, response or {})
        raw = json.dumps(response, ensure_ascii=False) if response else ""
    if raw:
        sys.stdout.write(raw)
        if response is not None:
            # 配送後の状態記録失敗で、同じ応答へ別のJSONを追加しない。
            with contextlib.suppress(Exception):
                _notice.record_delivery(_notice.output_text(response))
    if argv[0] in {"stop", "subagent_stop_advisor"}:
        _record_activity(payload, output=raw, returncode=result)
    return result


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
