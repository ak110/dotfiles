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

例外時は標準エラー出力の先頭へ要約1行を書き、続けて従来どおりtraceback全文を出力する。
要約1行は`main()`実行中の例外のみを対象とし、モジュール読込時点の失敗
（`ModuleNotFoundError`等）は対象外とする。読込失敗時は素のPythonトレースバックのみを
標準エラー出力へ書き、フック処理を通過させる。

未知のサブコマンドは、hook定義の登録名と実装の`_SUBCOMMANDS`が一致していない状態を示す。稼働中の
セッションが起動時に読んだ旧定義を保持する場合のほか、hook定義の登録名の誤りや、実装より新しい
hook定義でも同じ処理が実行される。配布先の定義を追随させる機構は設けず、終了コード0を保ったまま
標準エラー出力で不整合を観測できるようにする。
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
lazy import traceback

from agent_toolkit._common import inherited_venv
lazy from agent_toolkit._atk import outcome as _outcome
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
        "permissionrequest_codex",
        "rules_context",
        "rules_context_codex",
        "user_prompt_submit",
        "session_activity",
        "antigravity",
    }
)

# 例外時に`_approve()`で終了を許可する対象。出力形式はStop系モジュールの実装へ委ねる。
_APPROVE_FALLBACK_SUBCOMMANDS: frozenset[str] = frozenset({"stop"})


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


def main(argv: list[str]) -> int:
    """サブコマンド名から対象モジュールを解決し`main()`を呼び出す。"""
    inherited_venv.strip_inherited_venv(os.environ)
    _configure_standard_output()
    known_subcommands = "|".join(sorted(_SUBCOMMANDS))
    if not argv or argv[0] not in _SUBCOMMANDS:
        session_id = None
        with contextlib.suppress(OSError, ValueError, UnicodeError):
            early_payload = json.loads(sys.stdin.read())
            if isinstance(early_payload, dict) and isinstance(early_payload.get("session_id"), str):
                session_id = early_payload["session_id"]
        if session_id and not _outcome.claim_persistent_warning(repr(argv), session_id=session_id):
            return 0
    if not argv:
        print(
            _notice_formatter("hook")(
                f"usage: hook.py <{known_subcommands}>",
                tag="warn",
                fix="フック定義の起動コマンドへ現行のサブコマンド名を渡す。",
                removable_cause=False,
            ),
            file=sys.stderr,
        )
        return 0
    if argv[0] not in _SUBCOMMANDS:
        print(
            _notice_formatter("hook")(
                f"hook定義と実装が不整合: 未知のサブコマンド'{argv[0]}'を受領した。現行のサブコマンド: {known_subcommands}。",
                tag="warn",
                fix=(
                    "呼び出し元のフック定義の登録名が現行のサブコマンドと一致するか確かめる。"
                    "稼働中のセッションが起動時に読んだ旧定義を保持している場合は、"
                    "プラグインを更新してセッションを再起動すると解消する。"
                ),
                removable_cause=False,
            ),
            file=sys.stderr,
        )
        return 0
    payload_bytes = sys.stdin.buffer.read()
    _dump_payload(argv[0], payload_bytes)
    try:
        payload_text = payload_bytes.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        print(
            _notice_formatter("hook")(
                f"stdinのUTF-8デコードに失敗したためフック処理を通過させる: {exc}",
                tag="warn",
                fix="再発する場合はagent-toolkitの不具合としてユーザーへ報告する。",
                removable_cause=False,
            ),
            file=sys.stderr,
        )
        return 0
    payload: object = None
    with contextlib.suppress(Exception):
        payload = json.loads(payload_text)
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
    except Exception:  # noqa: BLE001 -- 読込失敗でフック全体を停止させないため広範に捕捉
        traceback.print_exc()
        return 0
    cycle: contextlib.AbstractContextManager[None] = contextlib.nullcontext()
    if isinstance(payload, dict) and payload.get("session_id") and argv[0] != "pretooluse":
        cycle = _notice.notice_cycle(str(payload.get("session_id", "")), argv[0], _agent_id.resolve_hook_agent_id(payload))
    try:
        with cycle:
            return _invoke_module(module, payload_text, payload, argv)
    except Exception as exc:  # noqa: BLE001 -- フックの破損によって編集できなくならないよう広範に捕捉
        tb = traceback.extract_tb(exc.__traceback__)
        frame = tb[-1] if tb else None
        location = f" ({pathlib.Path(frame.filename).name}:{frame.lineno})" if frame is not None else ""
        label = argv[0]
        print(
            _notice_formatter("hook")(
                f"[{label}] 想定外エラー: {type(exc).__name__}: {exc}{location}",
                tag="warn",
                fix="対応不要（フック処理を通過させて継続した）。再発する場合はagent-toolkitの不具合としてユーザーへ報告する。",
                removable_cause=False,
            ),
            file=sys.stderr,
        )
        traceback.print_exc()
        if argv[0] in _APPROVE_FALLBACK_SUBCOMMANDS:
            approve = getattr(module, "_approve", None)
            if callable(approve):
                approve()
        return 0


def _invoke_module(module: types.ModuleType, payload_text: str, payload: object, argv: list[str]) -> int:
    """通知の有効期間の内側で、イベント固有の出力と活動記録を処理する。"""
    if argv[0] == "antigravity":
        if len(argv) != 2:
            raise ValueError("antigravityにはイベント名を1個渡す")
        return module.main(payload_text, event=argv[1])
    if argv[0] in {"stop", "subagent_stop_advisor"}:
        output = io.StringIO()
        try:
            with contextlib.redirect_stdout(output):
                result = module.main(payload_text)
        finally:
            sys.stdout.write(output.getvalue())
        response = output.getvalue()
        _record_activity(payload, output=response, returncode=result)
        return result
    return module.main(payload_text)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
