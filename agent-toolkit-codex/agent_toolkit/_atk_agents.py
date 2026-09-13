"""`atk agents`の委譲session観測・通知操作。"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
from collections.abc import Mapping
from typing import Any

from agent_toolkit._agents_server import agents_wait, status_file
from agent_toolkit._atk import help_text as _help
from agent_toolkit._atk_agents_notify import send_notification


def build_parser(parser: argparse.ArgumentParser) -> None:
    """`agents`配下のサブコマンドを登録する。"""
    sub = _help.add_subcommands(parser, dest="agents_subcommand", required=False, show_help_when_missing=True)
    _help.add_command(sub, "wait", **_help.HELP["atk agents wait"])
    notify = _help.add_command(sub, "notify", **_help.HELP["atk agents notify"])
    notify.set_defaults(error_parser=notify)
    notification_body = notify.add_mutually_exclusive_group(required=True)
    notification_body.add_argument("--body", help="委譲元へ送る本文。")
    notification_body.add_argument("--body-file", type=pathlib.Path, help="委譲元へ送る本文を保持するUTF-8ファイルの絶対パス。")
    list_ = _help.add_command(sub, "list", **_help.HELP["atk agents list"])
    list_.add_argument("--include-terminated", action="store_true", help="未回収結果を持たない終端済みsessionも含める。")
    show = _help.add_command(sub, "show", **_help.HELP["atk agents show"])
    show.add_argument("session_id", help="表示するsession識別子。")


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
    if root_session_id is None:
        print("agents_serverの状態ディレクトリを解決できません。", file=sys.stderr)
        return 4
    sessions = _load_sessions(root_session_id)
    if args.agents_subcommand == "list":
        if not args.include_terminated:
            sessions = [
                session for session in sessions if session.get("status") == "running" or session.get("result_available") is True
            ]
        print(json.dumps({"sessions": sessions}, ensure_ascii=False, separators=(",", ":")))
        return 0
    selected = next((session for session in sessions if session.get("session_id") == args.session_id), None)
    if selected is None:
        print(f"unknown session: {args.session_id}", file=sys.stderr)
        return 2
    print(json.dumps(selected, ensure_ascii=False, separators=(",", ":")))
    return 0


def _load_sessions(root_session_id: str) -> list[dict[str, Any]]:
    """ルートsession配下の状態ファイルを統合し、開始順のsession一覧を返す。"""
    results = status_file.results_directory(root_session_id)
    by_id: dict[str, dict[str, Any]] = {}
    for path in status_file.list_status_files(root_session_id):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
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
            previous = by_id.get(session["session_id"])
            if previous is None or str(previous.get("updated_at", "")) <= str(session.get("updated_at", "")):
                by_id[session["session_id"]] = session
    return sorted(by_id.values(), key=lambda session: str(session.get("started_at", "")))
