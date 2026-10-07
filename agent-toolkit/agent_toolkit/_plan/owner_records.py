"""計画バンドルの所有セッションの記録（`<stem>.owner.json`）と、記録へ書くセッション識別子の解決。

所有記録は`~/.claude/plans`の局所状態であり、private-notesへ保存しない。
"""

from __future__ import annotations

import datetime
import json
import os
import pathlib

from agent_toolkit._common import delegated_session as _delegated_session
from agent_toolkit._plan import bundle_kinds as _bundle_kinds

_OWNER_SESSION_ENVIRONMENT_KEYS = (_delegated_session.OWNER_SESSION_ENV, "CLAUDE_CODE_SESSION_ID")

PROCESS_ROOT_SESSION_PREFIX = "mcp-"
"""`agents_server`が会話の識別子を受け取らずに生成するプロセス専用のrootの接頭辞。

`_agents_server.shared_roots.create_process_root_identity`が生成し、状態ファイルのrootと通知の配送には使うが、
会話のセッションを表さない。
"""


def owner_record_path(main_plan_path: pathlib.Path | str) -> pathlib.Path:
    """計画ファイル（メイン）のパスから所有記録のパスを返す。"""
    path = pathlib.Path(main_plan_path)
    return path.with_name(_bundle_kinds.OWNER_RECORD.name_for(path.stem))


def resolve_owner_session_id() -> str | None:
    """所有記録へ書くセッション識別子を環境から解決する。

    委譲先には委譲元が`AGENT_TOOLKIT_OWNER_SESSION`で自身の識別子を渡す。
    この値が無い場合は、実行中のセッション自身を示す`CLAUDE_CODE_SESSION_ID`を用いる。
    Codex CLIが直接起動するMCPサーバーにはいずれの識別子も渡らないため、
    そのMCPサーバーが作成した計画バンドルは所有記録を持たない。
    いずれも非空の値を持たない場合は解決しない。
    """
    for key in _OWNER_SESSION_ENVIRONMENT_KEYS:
        value = os.environ.get(key)
        if value:
            return value
    return None


def resolve_conversation_session_id() -> str | None:
    """計画の所有会話とUWIの投入元会話として記録できるセッション識別子を返す。

    `resolve_owner_session_id`の値のうち、プロセス専用のrootは会話へ対応しないため除き、`None`を返す。
    除いた場合も`CLAUDE_CODE_SESSION_ID`へは戻らない。委譲先自身の識別子は委譲元の会話を表さないためである。
    """
    session_id = resolve_owner_session_id()
    if session_id is None or session_id.startswith(PROCESS_ROOT_SESSION_PREFIX):
        return None
    return session_id


def write_owner_record(main_plan_path: pathlib.Path | str, *, session_id: str) -> pathlib.Path:
    """計画バンドルの所有記録を出力し、出力したパスを返す。"""
    path = owner_record_path(main_plan_path)
    record = {"session_id": session_id, "recorded_at": datetime.datetime.now().astimezone().isoformat()}
    path.write_text(json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def record_plan_owner(main_plan_path: pathlib.Path | str) -> pathlib.Path | None:
    """所有会話を解決できた場合だけ所有記録を出力し、出力したパスを返す。

    解決できない場合とプロセス専用のrootしか得られない場合は記録を残さず、呼び出し元の取得・作成そのものは成功として扱う。
    """
    session_id = resolve_conversation_session_id()
    if session_id is None:
        return None
    return write_owner_record(main_plan_path, session_id=session_id)


def read_owner_session_id(main_plan_path: pathlib.Path | str) -> str | None:
    """所有記録が示すセッション識別子を返す。

    記録が無い場合、JSONオブジェクトとして解釈できない場合、`session_id`が非空の文字列でない場合は
    いずれも所有を確定できないものとして`None`を返す。
    """
    try:
        record = json.loads(owner_record_path(main_plan_path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(record, dict):
        return None
    session_id = record.get("session_id")
    if isinstance(session_id, str) and session_id:
        return session_id
    return None


def remove_owner_record(main_plan_path: pathlib.Path | str) -> None:
    """計画バンドルの所有記録を回収する。"""
    owner_record_path(main_plan_path).unlink(missing_ok=True)
