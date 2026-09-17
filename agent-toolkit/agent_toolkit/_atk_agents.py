"""`atk agents`の委譲session観測・通知操作。"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import pathlib
import sys
from collections.abc import Mapping
from typing import Any

from agent_toolkit._agents_server import agents_wait, state, status_file
from agent_toolkit._atk import help_text as _help
from agent_toolkit._atk import output_file as _output_file
from agent_toolkit._atk.environment import is_agent_environment
from agent_toolkit._atk_agents_notify import send_notification


def build_parser(parser: argparse.ArgumentParser) -> None:
    """`agents`配下のサブコマンドを登録する。"""
    sub = _help.add_subcommands(parser, dest="agents_subcommand", required=False, show_help_when_missing=True)
    wait = _help.add_command(sub, "wait", **_help.HELP["atk agents wait"])
    # 保存先は待機の巡回より前に開く。`atk`のmainが`--output-file`を解決してから当該サブコマンドを
    # 実行するため、保存できない指定では結果ファイルと通知ファイルを削除せずに終わる。
    _output_file.add_output_file_arg(wait)
    notify = _help.add_command(sub, "notify", **_help.HELP["atk agents notify"])
    notify.set_defaults(error_parser=notify)
    notification_body = notify.add_mutually_exclusive_group(required=True)
    notification_body.add_argument("--body", help="委譲元へ送る本文。")
    notification_body.add_argument("--body-file", type=pathlib.Path, help="委譲元へ送る本文を保持するUTF-8ファイルの絶対パス。")
    list_ = _help.add_command(sub, "list", **_help.HELP["atk agents list"])
    list_.add_argument("--include-terminated", action="store_true", help="未回収結果を持たない終端済みsessionも含める。")
    show = _help.add_command(sub, "show", **_help.HELP["atk agents show"])
    show.add_argument("session_id", help="表示するsession識別子。")


def _dump(payload: Any, environment: Mapping[str, str]) -> str:
    """エージェント環境では区切り文字だけの1行、人間が読む環境では字下げしたJSONを返す。

    エージェント環境では出力量がそのままトークン消費になるため空白を含めず、
    人間が端末で読む環境では字下げした形にする。値そのものはいずれでも変えない。
    """
    if is_agent_environment(environment):
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return json.dumps(payload, ensure_ascii=False, indent=2)


def dispatch(args: argparse.Namespace, *, environment: Mapping[str, str] | None = None) -> int:
    """選択された`agents`サブコマンドを実行する。"""
    if args.agents_subcommand == "wait":
        return agents_wait.wait_for_result(environment=environment)
    if args.agents_subcommand == "notify":
        body = args.body
        if args.body_file is not None:
            if not args.body_file.is_absolute():
                args.error_parser.error("--body-fileには絶対パスを指定してください。")
            try:
                with args.body_file.open(encoding="utf-8", newline="") as stream:
                    body = stream.read()
            except (OSError, UnicodeError) as error:
                args.error_parser.error(f"--body-fileをUTF-8で読めません: {error}")
        assert body is not None
        return send_notification(body)
    env = os.environ if environment is None else environment
    root_session_id = status_file.resolve_conversation_root_session_id(env)
    if args.agents_subcommand == "list":
        sessions = (
            _load_sessions(root_session_id)
            if root_session_id is not None
            else _load_all_sessions(status_file.list_root_session_ids())
        )
        if not args.include_terminated:
            sessions = [
                session for session in sessions if session.get("status") == "running" or session.get("result_available") is True
            ]
        print(_dump({"sessions": [_without_prompt(session) for session in sessions]}, env))
        return 0
    if root_session_id is None:
        root_session_id = status_file.find_root_session_id_for_session(args.session_id)
    sessions = [] if root_session_id is None else _load_sessions(root_session_id)
    selected = next((session for session in sessions if session.get("session_id") == args.session_id), None)
    if selected is None:
        resolved = status_file.find_root_session_id_for_session(args.session_id)
        if resolved is not None and resolved != root_session_id:
            selected = next(
                (session for session in _load_sessions(resolved) if session.get("session_id") == args.session_id),
                None,
            )
    if selected is None:
        print(f"unknown session: {args.session_id}", file=sys.stderr)
        return 2
    print(_dump(selected, env))
    return 0


def _without_prompt(session: dict[str, Any]) -> dict[str, Any]:
    """起動文を除いた一覧用の射影を返す。

    `list`の用途は稼働状況の把握であり、起動文はこれに使わない。
    起動文の量はsession数と長さの積で増えるため、一覧から外して呼び出し元が受け取る量の伸びを抑える。
    起動文は`show`が返すため、除いても取得経路は失われない。
    """
    return {key: value for key, value in session.items() if key != "prompt"}


def _load_sessions(root_session_id: str) -> list[dict[str, Any]]:
    """ルートsession配下の状態ファイルを統合し、開始順のsession一覧を返す。"""
    results = status_file.results_directory(root_session_id)
    by_id: dict[str, dict[str, Any]] = {}
    for path in status_file.list_status_files(root_session_id):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        if not _status_payload_is_current(payload):
            continue
        raw_sessions = payload.get("sessions") if isinstance(payload, dict) else None
        if not isinstance(raw_sessions, list):
            continue
        for raw in raw_sessions:
            if not isinstance(raw, dict) or not isinstance(raw.get("session_id"), str):
                continue
            session = dict(raw)
            session["owner_status_file"] = path.name
            session["result_available"] = (results / f"{session['session_id']}.json").is_file()
            _add_output_activity(session)
            previous = by_id.get(session["session_id"])
            if previous is None or str(previous.get("updated_at", "")) <= str(session.get("updated_at", "")):
                by_id[session["session_id"]] = session
    return sorted(by_id.values(), key=lambda session: str(session.get("started_at", "")))


def _load_all_sessions(root_session_ids: list[str]) -> list[dict[str, Any]]:
    """複数のルート状態をsession識別子ごとの最新版へ統合する。"""
    by_id: dict[str, dict[str, Any]] = {}
    for root_session_id in root_session_ids:
        for session in _load_sessions(root_session_id):
            previous = by_id.get(session["session_id"])
            if previous is None or str(previous.get("updated_at", "")) <= str(session.get("updated_at", "")):
                by_id[session["session_id"]] = session
    return sorted(by_id.values(), key=lambda session: str(session.get("started_at", "")))


def _status_payload_is_current(payload: Any) -> bool:
    """heartbeatを持つ状態が有効期限内であるかを返す。"""
    heartbeat_at = payload.get("heartbeat_at") if isinstance(payload, dict) else None
    if not isinstance(heartbeat_at, str):
        return True
    try:
        heartbeat = datetime.datetime.fromisoformat(heartbeat_at)
    except ValueError:
        return False
    if heartbeat.tzinfo is None:
        return False
    cutoff = datetime.datetime.now(datetime.UTC) - datetime.timedelta(seconds=status_file.HEARTBEAT_EXPIRY_SECONDS)
    return heartbeat >= cutoff


def _add_output_activity(session: dict[str, Any]) -> None:
    """活動とテキスト出力からの経過秒、及び停滞印を公開射影へ加える。"""
    updated_at = session.get("updated_at")
    output_updated_at = session.get("output_updated_at")
    started_at = session.get("started_at")
    activity = state.activity_projection(
        updated_at=updated_at if isinstance(updated_at, str) else None,
        output_updated_at=output_updated_at if isinstance(output_updated_at, str) else None,
        started_at=started_at if isinstance(started_at, str) else None,
    )
    session.update(activity)
