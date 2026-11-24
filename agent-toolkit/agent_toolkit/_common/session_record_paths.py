from __future__ import annotations

import dataclasses
import datetime
import os
import pathlib
import re
import tempfile
import uuid
from collections.abc import Iterable

from agent_toolkit._common import host_homes
from agent_toolkit._common import session_layout as shared_layout

RECORD_SUFFIX = ".jsonl"
CODEX_ROLLOUT_PREFIX = "rollout-"


def stop_log_path(session_id: str) -> pathlib.Path:
    """Stop hookの判定根拠の常時ログのパスを返す。  pyfltr: allow-comment

    書き込みはStop hook（`_hooks/stop_session.py`）が行い、振り返りの失敗報告が同じパスから読む。
    """
    return pathlib.Path(tempfile.gettempdir()) / f"claude-agent-toolkit-stop-{session_id}.log"


def codex_record_roots(codex_home: pathlib.Path) -> tuple[pathlib.Path, pathlib.Path]:
    return codex_home / "sessions", codex_home / "archived_sessions"


def codex_rollouts(codex_home: pathlib.Path) -> Iterable[pathlib.Path]:
    sessions, archive = codex_record_roots(codex_home)
    name = f"{CODEX_ROLLOUT_PREFIX}*{RECORD_SUFFIX}"
    return (path for paths in (sessions.glob(f"*/*/*/{name}"), archive.glob(name)) for path in paths if path.is_file())


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
    claude_paths = find_claude_records(session_id, claude_home=claude_home)
    if claude_paths:
        return SessionRecord("claude", claude_paths)
    codex_paths = find_codex_records(session_id, codex_home=codex_home)
    if codex_paths:
        return SessionRecord("codex", codex_paths)
    return _find_agy_or_opencode_record(session_id, state_root)


def find_claude_records(session_id: str, *, claude_home: pathlib.Path | None = None) -> tuple[pathlib.Path, ...]:
    """Claude Codeの親会話の記録を全projectから、無ければサブエージェントの記録をproject名の順で返す。

    `find_session_record`のうちClaude Codeの部分だけを行う。Claude Codeの記録だけを使う呼び出しは、
    一致しない場合に続くCodexの全ロールアウトの列挙を省ける。
    """
    if _SESSION_ID_PATTERN.fullmatch(session_id) is None:
        return ()
    claude_projects = (claude_home if claude_home is not None else host_homes.claude_config_dir()) / "projects"
    if not claude_projects.is_dir():
        return ()
    claude_paths = tuple(
        path
        for project in sorted(claude_projects.iterdir())
        if project.is_dir() and (path := project / f"{session_id}{RECORD_SUFFIX}").is_file()
    )
    if claude_paths or not re.fullmatch(r"agent-[0-9a-fA-F]+", session_id):
        return claude_paths
    return tuple(path for path in sorted(claude_projects.glob(f"*/*/subagents/{session_id}{RECORD_SUFFIX}")) if path.is_file())


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
    UUIDv7のCodexのthread IDは`find_codex_records`と同じく作成日の日付ディレクトリだけを探し、Codexの一覧は読まない。
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
        if _SESSION_ID_PATTERN.fullmatch(session_id) is None:
            return None
        if self._claude is None:
            self._claude = self._claude_records()
        if paths := self._claude.get(session_id):
            return SessionRecord("claude", tuple(paths))
        if paths := self._codex_paths(session_id):
            return SessionRecord("codex", paths)
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

        for project in sorted(projects.iterdir()):
            if not project.is_dir():
                continue
            for entry in os.scandir(project):
                if entry.name.endswith(RECORD_SUFFIX) and entry.is_file():
                    found.setdefault(entry.name[: -len(RECORD_SUFFIX)], []).append(project / entry.name)
        return found

    def _codex_paths(self, session_id: str) -> tuple[pathlib.Path, ...]:
        home = self._codex_home if self._codex_home is not None else host_homes.codex_home()
        sessions, archive = codex_record_roots(home)
        if (directories := codex_thread_directories(sessions, session_id)) is not None:
            name = _codex_rollout_name(session_id)
            return _codex_matches(session_id, directories, name) or _codex_matches(session_id, (archive,), name)
        if self._codex is None:
            self._codex = self._codex_records(home)
        return tuple(self._codex.get(session_id, ()))

    @staticmethod
    def _codex_records(codex_home: pathlib.Path) -> dict[str, list[pathlib.Path]]:
        found: dict[str, list[pathlib.Path]] = {}
        for path in sorted(codex_rollouts(codex_home)):
            session_id = codex_session_id(path)
            # `find_codex_records`の検索パターン（`rollout-*-<thread-id>.jsonl`）に一致する記録だけを数える。
            if path.name.endswith(f"-{session_id}{RECORD_SUFFIX}") and path.is_file():
                found.setdefault(session_id, []).append(path)
        return found


def agy_session_id(path: pathlib.Path) -> str:
    return path.parents[2].name if path.name in {"transcript.jsonl", "transcript_full.jsonl"} else path.stem


def agy_session_records(gemini_home: pathlib.Path) -> tuple[pathlib.Path, ...]:
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

    UUIDv7のthread IDは、作成日の前後1日の日付ディレクトリで見つかればその記録だけを返し、全期間を列挙しない。

    Args:
        session_id: Codexのthread ID。英数字・`_`・`-`以外を含む場合は探索しない。
        codex_home: Codexの記録の保存先。省略時は空でない`CODEX_HOME`、`~/.codex`の順で決める。
    """
    if _SESSION_ID_PATTERN.fullmatch(session_id) is None:
        return ()
    sessions, archive = codex_record_roots(codex_home if codex_home is not None else host_homes.codex_home())
    name = _codex_rollout_name(session_id)
    nearby = _codex_matches(session_id, codex_thread_directories(sessions, session_id) or (), name)

    return nearby or _codex_matches(session_id, (archive,), name) or _codex_matches(session_id, (sessions,), f"*/*/*/{name}")


def has_archived_codex_record(session_id: str) -> bool:
    if _SESSION_ID_PATTERN.fullmatch(session_id) is None:
        return False
    _, archive = codex_record_roots(host_homes.codex_home())
    return bool(_codex_matches(session_id, (archive,), _codex_rollout_name(session_id)))


def _codex_rollout_name(session_id: str) -> str:
    return f"{CODEX_ROLLOUT_PREFIX}*-{session_id}{RECORD_SUFFIX}"


def codex_thread_directories(sessions: pathlib.Path, session_id: str) -> tuple[pathlib.Path, ...] | None:
    """UUIDv7のthread IDから、作成時刻のUTC日付と前後1日の日付ディレクトリを返す。UUIDv7でなければ`None`を返す。

    Codexは記録をthread作成時のローカル日付のディレクトリ（`sessions/YYYY/MM/DD`）へ置き、resumeでも同じファイルへ追記する。
    ローカル日付はUTC日付の前後1日に収まるため、3日分で全てのタイムゾーンを含む。
    """
    try:
        value = uuid.UUID(session_id)
        if value.version != 7:
            return None
        created = datetime.datetime.fromtimestamp((value.int >> 80) / 1000, datetime.UTC).date()
        days = [created + datetime.timedelta(days=offset) for offset in (-1, 0, 1)]
    except ValueError, OverflowError, OSError:
        return None
    return tuple(sessions / f"{day.year:04d}" / f"{day.month:02d}" / f"{day.day:02d}" for day in days)


def _codex_matches(session_id: str, directories: Iterable[pathlib.Path], pattern: str) -> tuple[pathlib.Path, ...]:
    return tuple(
        path
        for path in sorted(path for directory in directories for path in directory.glob(pattern))
        if path.is_file() and codex_session_id(path) == session_id
    )


def codex_session_id(path: pathlib.Path) -> str:
    stem = path.name[len(CODEX_ROLLOUT_PREFIX) : -len(RECORD_SUFFIX)]
    parts = stem.split("-")
    return "-".join(parts[-5:]) if len(parts) >= 5 else stem
