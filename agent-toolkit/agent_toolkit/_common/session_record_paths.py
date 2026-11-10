"""委譲先sessionの記録を識別子から探す処理をまとめる。

`atk agents logs`とreview-sessionの`atk run-script session-review-evidence`は、
同じ委譲先の記録を実行系ごとに異なる保存先から探す。
探索を呼び出し側ごとに実装すると、`agents_server`へ実行系を追加したときに一部の呼び出し側だけが
新しい保存先を探さないまま残る。探索は本モジュールへ集約し、解決できる実行系の集合と
`agents_server`の実行系集合と一致することをテストで確かめる。

session識別子は実行系をまたいで一意であるため、探索は識別子だけで行う。同じ実行系で複数の記録が
一致した場合の扱い（先頭を表示する、別の記録の混入を避けて未解決とするなど）は呼び出し側の方針が決める。
"""

from __future__ import annotations

import dataclasses
import os
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
    return _find_agy_or_opencode_record(session_id, state_root)


def _find_agy_or_opencode_record(session_id: str, state_root: pathlib.Path | None) -> SessionRecord | None:
    """AntigravityとOpenCodeの記録を`find_session_record`と同じ順で探す。識別子の検査は呼出側が済ませている。"""
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


class RecordLocator:
    """多数の識別子を続けて探す呼び出し向けに、保存先の一覧を1回だけ読み`find_session_record`と同じ結果を返す。

    子孫の多い記録の推定費用は数十件の委譲先を解決する。1件ごとにClaude Codeの全projectとCodexの全ロールアウトを
    列挙すると、解決だけで数秒かかる。一覧は最初の解決時に読み、同じ探索の間だけ使う。
    一覧に無い識別子は、先にAntigravityとOpenCodeの記録を探し、無ければ`find_session_record`へ委ねる
    （一覧の後に作られた記録とサブエージェント）。識別子は実行系をまたいで一意であるため、探す順を変えても結果は同じである。
    Antigravityの委譲先を数十件持つ家族で、1件ごとにClaude CodeとCodexの保存先を走査し直さないためである。
    """

    def __init__(
        self,
        *,
        codex_home: pathlib.Path | None = None,
        claude_home: pathlib.Path | None = None,
        state_root: pathlib.Path | None = None,
    ) -> None:
        self._codex_home = codex_home
        self._claude_home = claude_home
        self._state_root = state_root
        self._claude: dict[str, list[pathlib.Path]] | None = None
        self._codex: dict[str, list[pathlib.Path]] | None = None

    def find(self, session_id: str) -> SessionRecord | None:
        """`find_session_record`と同じ規則で記録を返す。"""
        if _SESSION_ID_PATTERN.fullmatch(session_id) is None:
            return None
        if self._claude is None:
            self._claude = self._claude_records()
        if paths := self._claude.get(session_id):
            return SessionRecord("claude", tuple(paths))
        if self._codex is None:
            self._codex = self._codex_records()
        if paths := self._codex.get(session_id):
            return SessionRecord("codex", tuple(paths))
        if (other := _find_agy_or_opencode_record(session_id, self._state_root)) is not None:
            return other
        return find_session_record(
            session_id, codex_home=self._codex_home, claude_home=self._claude_home, state_root=self._state_root
        )

    def _claude_records(self) -> dict[str, list[pathlib.Path]]:
        projects = (self._claude_home if self._claude_home is not None else host_homes.claude_config_dir()) / "projects"
        found: dict[str, list[pathlib.Path]] = {}
        if not projects.is_dir():
            return found
        # `find_session_record`と同じくproject名の順に並べ、同じ識別子の全候補を保持する。
        for project in sorted(projects.iterdir()):
            if not project.is_dir():
                continue
            for entry in os.scandir(project):
                if entry.name.endswith(RECORD_SUFFIX) and entry.is_file():
                    found.setdefault(entry.name[: -len(RECORD_SUFFIX)], []).append(project / entry.name)
        return found

    def _codex_records(self) -> dict[str, list[pathlib.Path]]:
        sessions = (self._codex_home if self._codex_home is not None else host_homes.codex_home()) / "sessions"
        found: dict[str, list[pathlib.Path]] = {}
        if not sessions.is_dir():
            return found
        for path in sorted(sessions.glob(f"*/*/*/{CODEX_ROLLOUT_PREFIX}*{RECORD_SUFFIX}")):
            session_id = codex_session_id(path)
            # `find_codex_records`の検索パターン（`rollout-*-<thread-id>.jsonl`）に一致する記録だけを数える。
            if path.name.endswith(f"-{session_id}{RECORD_SUFFIX}") and path.is_file():
                found.setdefault(session_id, []).append(path)
        return found


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
