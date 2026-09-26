"""委譲先sessionの記録を識別子から探す処理の正本を保持する。

`atk agents logs`とsession-reviewの証拠抽出器は、同じ委譲先の記録を実行系ごとに異なる保存先から探す。
探索を呼び出し側ごとに実装すると、`agents_server`へ実行系を追加したときに一部の呼び出し側だけが
新しい保存先を探さないまま残る。探索は本モジュールへ集約し、解決できる実行系の集合と
`agents_server`の実行系集合の一致をテストで検査する。

session識別子は実行系をまたいで一意であるため、探索は識別子だけで行う。同じ実行系で複数の記録が
一致した場合の扱い（先頭を表示する、別の記録の混入を避けて未解決とするなど）は呼び出し側の方針が決める。
"""

from __future__ import annotations

import dataclasses
import pathlib
import re

from agent_toolkit._agents_server import status_file
from agent_toolkit._atk.serve import sessions as session_records

RECORD_ENGINES: frozenset[str] = frozenset({"claude", "codex", "agy"})
"""記録を探索できる実行系の名前。"""

_SESSION_ID_PATTERN = re.compile(r"[A-Za-z0-9_-]+")


@dataclasses.dataclass(frozen=True)
class SessionRecord:
    """識別子が一致した実行系と、その実行系で一致した記録の絶対パス（安定順）。"""

    engine: str
    paths: tuple[pathlib.Path, ...]


def find_session_record(session_id: str, *, codex_home: pathlib.Path | None = None) -> SessionRecord | None:
    """委譲先sessionの記録をClaude Code、Codex、Antigravityの順に探す。

    Args:
        session_id: 委譲先のsession識別子。英数字・`_`・`-`以外を含む場合は探索しない。
        codex_home: Codexの記録の保存先。省略時は空でない`CODEX_HOME`、`~/.codex`の順で決める。

    Returns:
        最初に一致した実行系とその全候補。一致が無い場合は`None`。
    """
    if _SESSION_ID_PATTERN.fullmatch(session_id) is None:
        return None
    claude_projects = session_records.default_claude_home() / "projects"
    if claude_projects.is_dir():
        claude_paths = tuple(
            path
            for project in sorted(claude_projects.iterdir())
            if project.is_dir() and (path := project / f"{session_id}{session_records.RECORD_SUFFIX}").is_file()
        )
        if claude_paths:
            return SessionRecord("claude", claude_paths)
    codex_sessions = (codex_home if codex_home is not None else session_records.default_codex_home()) / "sessions"
    if codex_sessions.is_dir():
        codex_paths = tuple(
            path
            for path in sorted(codex_sessions.glob(f"*/*/*/{session_records.CODEX_ROLLOUT_PREFIX}*-{session_id}.jsonl"))
            if path.is_file() and session_records.codex_session_id(path) == session_id
        )
        if codex_paths:
            return SessionRecord("codex", codex_paths)
    agy_paths = tuple(
        path
        for root_session_id in status_file.list_root_session_ids()
        if (path := status_file.session_log_path(root_session_id, session_id)).is_file()
    )
    if agy_paths:
        return SessionRecord("agy", agy_paths)
    return None
