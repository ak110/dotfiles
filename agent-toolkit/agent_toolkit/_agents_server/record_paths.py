"""委譲先sessionの記録を識別子から探す処理をまとめる。

`atk agents logs`とsession-reviewの`atk run-script session-review-evidence`は、
同じ委譲先の記録を実行系ごとに異なる保存先から探す。
探索を呼び出し側ごとに実装すると、`agents_server`へ実行系を追加したときに一部の呼び出し側だけが
新しい保存先を探さないまま残る。探索は本モジュールへ集約し、解決できる実行系の集合と
`agents_server`の実行系集合と一致することをテストで確かめる。

session識別子は実行系をまたいで一意であるため、探索は識別子だけで行う。同じ実行系で複数の記録が
一致した場合の扱い（先頭を表示する、別の記録の混入を避けて未解決とするなど）は呼び出し側の方針が決める。
"""

from __future__ import annotations

import dataclasses
import pathlib
import re

from agent_toolkit._agents_server import shared_layout
from agent_toolkit._atk import session_record_format
from agent_toolkit._common import host_homes

RECORD_ENGINES: frozenset[str] = frozenset({"claude", "codex", "agy"})
"""記録を探索できる実行系の名前。"""

_SESSION_ID_PATTERN = re.compile(r"[A-Za-z0-9_-]+")


@dataclasses.dataclass(frozen=True)
class SessionRecord:
    """識別子が一致した実行系と、その実行系で一致した記録の絶対パス（安定順）。"""

    engine: str
    paths: tuple[pathlib.Path, ...]


def find_session_record(session_id: str, *, codex_home: pathlib.Path | None = None) -> SessionRecord | None:
    """記録をClaude Codeの親・サブエージェント、Codex、Antigravityの順に探す。

    Args:
        session_id: 委譲先のsession識別子。英数字・`_`・`-`以外を含む場合は探索しない。
        codex_home: Codexの記録の保存先。省略時は空でない`CODEX_HOME`、`~/.codex`の順で決める。

    Returns:
        最初に一致した実行系とその全候補。一致が無い場合は`None`。
    """
    if _SESSION_ID_PATTERN.fullmatch(session_id) is None:
        return None
    claude_projects = host_homes.claude_config_dir() / "projects"
    if claude_projects.is_dir():
        claude_paths = tuple(
            path
            for project in sorted(claude_projects.iterdir())
            if project.is_dir() and (path := project / f"{session_id}{session_record_format.RECORD_SUFFIX}").is_file()
        )
        if claude_paths:
            return SessionRecord("claude", claude_paths)
        if re.fullmatch(r"agent-[0-9a-fA-F]+", session_id):
            child_paths = tuple(
                path
                for path in sorted(claude_projects.glob(f"*/*/subagents/{session_id}{session_record_format.RECORD_SUFFIX}"))
                if path.is_file()
            )
            if child_paths:
                return SessionRecord("claude", child_paths)
    codex_paths = find_codex_records(session_id, codex_home=codex_home)
    if codex_paths:
        return SessionRecord("codex", codex_paths)
    agy_paths = tuple(
        path
        for root_session_id in shared_layout.list_root_session_ids()
        if (path := shared_layout.session_log_path(root_session_id, session_id)).is_file()
    )
    if agy_paths:
        return SessionRecord("agy", agy_paths)
    return None


def find_codex_records(session_id: str, *, codex_home: pathlib.Path | None = None) -> tuple[pathlib.Path, ...]:
    """Codexのthread IDに一致するロールアウト記録をファイル名順で返す。

    Args:
        session_id: Codexのthread ID。英数字・`_`・`-`以外を含む場合は探索しない。
        codex_home: Codexの記録の保存先。省略時は空でない`CODEX_HOME`、`~/.codex`の順で決める。
    """
    if _SESSION_ID_PATTERN.fullmatch(session_id) is None:
        return ()
    sessions = (codex_home if codex_home is not None else host_homes.codex_home()) / "sessions"
    if not sessions.is_dir():
        return ()
    pattern = f"*/*/*/{session_record_format.CODEX_ROLLOUT_PREFIX}*-{session_id}{session_record_format.RECORD_SUFFIX}"
    return tuple(
        path
        for path in sorted(sessions.glob(pattern))
        if path.is_file() and session_record_format.codex_session_id(path) == session_id
    )
