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
import uuid

from agent_toolkit._common import host_homes
from agent_toolkit._common import session_layout as shared_layout

RECORD_SUFFIX = ".jsonl"
CODEX_ROLLOUT_PREFIX = "rollout-"


RECORD_ENGINES: frozenset[str] = frozenset({"claude", "codex", "agy", "opencode"})
"""記録を探索できる実行系の名前。"""

_SESSION_ID_PATTERN = re.compile(r"[A-Za-z0-9_-]+")


_CODEX_AGENT_PATH = re.compile(r"/root(?:/[A-Za-z0-9_.-]+)+")
"""Codexの組み込み委譲`spawn_agent`が起動結果の`task_name`として返すagentのパス（例: `/root/reader_review`）。"""


def is_launch_identifier(value: object) -> bool:
    """規範が認める起動手段が起動結果として返す識別子を受理する。

    受理する形式と、それを返す起動手段は次のとおり。形式は各起動手段で実際に観測した値から定める。

    - UUID: `agents_server`の`start`が返す`session_id`
    - `ses_`付き英数字列: OpenCode backendが返す`session_id`
    - 16進列と`agent-`付き16進列: Claude Codeの`Agent`ツールが返すagent ID
    - `/root`で始まり`/`区切りの空でない名前を続けるパス: Codexの組み込み委譲`spawn_agent`が返す`task_name`

    任意の名前（`label`など）、`/root`で始まらないパス、空の区間を含むパスは、起動結果と取り違えないよう拒否する。
    """
    if not isinstance(value, str):
        return False
    if re.fullmatch(r"(?:agent-)?[0-9a-fA-F]+|ses_[0-9A-Za-z]+", value) or _CODEX_AGENT_PATH.fullmatch(value):
        return True
    try:
        return str(uuid.UUID(value)) == value.lower()
    except ValueError:
        return False


@dataclasses.dataclass(frozen=True)
class SessionRecord:
    """識別子が一致した実行系と、その実行系で一致した記録の絶対パス（安定順）。"""

    engine: str
    paths: tuple[pathlib.Path, ...]


def find_session_record(
    session_id: str,
    *,
    codex_home: pathlib.Path | None = None,
    claude_home: pathlib.Path | None = None,
    state_root: pathlib.Path | None = None,
) -> SessionRecord | None:
    """記録をClaude Codeの親・サブエージェント、Codex、Antigravityの順に探す。

    Args:
        session_id: 委譲先のsession識別子。英数字・`_`・`-`以外を含む場合は探索しない。
        codex_home: Codexの記録の保存先。省略時は空でない`CODEX_HOME`、`~/.codex`の順で決める。
        claude_home: Claudeの設定root。省略時はホスト共通の解決結果を使う。
        state_root: turn型CLIのログがある状態root。省略時は通常の状態ディレクトリを使う。

    Returns:
        最初に一致した実行系とその全候補。一致が無い場合は`None`。
    """
    if _SESSION_ID_PATTERN.fullmatch(session_id) is None:
        return None
    claude_projects = (claude_home if claude_home is not None else host_homes.claude_config_dir()) / "projects"
    if claude_projects.is_dir():
        claude_paths = tuple(
            path
            for project in sorted(claude_projects.iterdir())
            if project.is_dir() and (path := project / f"{session_id}{RECORD_SUFFIX}").is_file()
        )
        if claude_paths:
            return SessionRecord("claude", claude_paths)
        if re.fullmatch(r"agent-[0-9a-fA-F]+", session_id):
            child_paths = tuple(
                path for path in sorted(claude_projects.glob(f"*/*/subagents/{session_id}{RECORD_SUFFIX}")) if path.is_file()
            )
            if child_paths:
                return SessionRecord("claude", child_paths)
    codex_paths = find_codex_records(session_id, codex_home=codex_home)
    if codex_paths:
        return SessionRecord("codex", codex_paths)
    logs = pathlib.Path.home() / ".gemini" / "antigravity-cli" / "brain" / session_id / ".system_generated" / "logs"
    for name in ("transcript_full.jsonl", "transcript.jsonl"):
        if (logs / name).is_file():
            return SessionRecord("agy", (logs / name,))
    for engine in ("agy", "opencode"):
        paths = tuple(
            path
            for root_session_id in shared_layout.list_root_session_ids(state_root)
            if (path := shared_layout.session_log_path(root_session_id, session_id, state_root, engine=engine)).is_file()
        )
        if paths:
            return SessionRecord(engine, paths)
    return None


def agy_session_id(path: pathlib.Path) -> str:
    """対話記録のbrain祖先か、委譲先ログのstemから会話IDを返す。"""
    return path.parents[2].name if path.name in {"transcript.jsonl", "transcript_full.jsonl"} else path.stem


def agy_session_records(gemini_home: pathlib.Path) -> tuple[pathlib.Path, ...]:
    """会話ごとに切り詰めのない記録を優先し、各会話を1回だけ列挙する。"""
    brain = gemini_home / "antigravity-cli" / "brain"
    if not brain.is_dir():
        return ()
    paths = []
    for conversation in sorted(brain.iterdir()):
        logs = conversation / ".system_generated" / "logs"
        for name in ("transcript_full.jsonl", "transcript.jsonl"):
            path = logs / name
            if path.is_file():
                paths.append(path)
                break
    return tuple(paths)


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
    pattern = f"*/*/*/{CODEX_ROLLOUT_PREFIX}*-{session_id}{RECORD_SUFFIX}"
    return tuple(path for path in sorted(sessions.glob(pattern)) if path.is_file() and codex_session_id(path) == session_id)


def codex_session_id(path: pathlib.Path) -> str:
    """ロールアウトのファイル名からthread IDを取り出す。

    ファイル名は`rollout-<日時>-<thread-id>`の形であり、thread IDはUUIDの5区画で末尾に置かれる。
    """
    stem = path.name[len(CODEX_ROLLOUT_PREFIX) : -len(RECORD_SUFFIX)]
    parts = stem.split("-")
    return "-".join(parts[-5:]) if len(parts) >= 5 else stem
