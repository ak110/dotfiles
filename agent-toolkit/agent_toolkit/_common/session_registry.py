"""agents_serverが起動したsessionの終了状態をプロセス間で共有する。

所有側のagents_serverがsessionを解放した場合は、レコードを削除せず解放済みレコードへ置き換える。
照会した別プロセスが「所有側が正常に解放した」と「どのagents_serverにも記録が無い」を区別できるようにするためである。

実行中のレコードは、所有するagents_serverとturnを実行する委譲先CLIのプロセスの識別（記録プロセス）を持つ。
所有側が終了を公開せずに終了した場合、照会側は記録された全プロセスの不在から終了（`interrupted`）と解決する。
この判定はレコードを書き換えず解決の結果として返し、書込は所有側と引き継ぎの処理に残す。

終了と所有の判定は記録プロセスの実在を一次の根拠とし、本モジュールの`resolve`と`running_ownership`へ集約する。
所有サーバーの起動単位が替わった後も、応答しない旧起動単位や委譲先CLIが生存し続ける場合があるためである。
状態ファイルの生存の印や委譲先CLIへの外部照会だけで終了・所有不在を決めると、作業中の委譲先を中断と公開する。
"""

from __future__ import annotations

import contextlib
import dataclasses
import datetime
import enum
import json
import os
import pathlib
import re
from collections.abc import Generator, Sequence
from typing import Any, Literal

import psutil

from agent_toolkit._common import file_lock
from agent_toolkit._common import session_launchers as _session_launchers
from agent_toolkit._common import state_paths as _state_paths
from agent_toolkit._common.atomic_file import atomic_write

_SESSION_ID_PATTERN = re.compile(r"^[0-9A-Za-z_-]+$")
_STATUSES = frozenset({"starting", "running", "completed", "failed", "interrupted"})
ReleaseReason = Literal["retention_expired", "stopped"]
_RELEASE_REASONS: frozenset[str] = frozenset({"retention_expired", "stopped"})
LAUNCHER_KEY = _session_launchers.LAUNCHER_KEY
InterruptionReason = Literal["owner_processes_exited", "owner_server_stopped"]
OWNER_PROCESSES_EXITED: InterruptionReason = "owner_processes_exited"
OWNER_SERVER_STOPPED: InterruptionReason = "owner_server_stopped"
_INTERRUPTION_MESSAGES: dict[str, str] = {
    OWNER_PROCESSES_EXITED: (
        "the owning agents_server exited without publishing the terminal state, and no process running the turn remains"
    ),
    OWNER_SERVER_STOPPED: "the owning agents_server stopped while the turn was running and published it as interrupted",
}
INTERRUPTED_TURNS_LIMIT = 16
"""登録簿が保持する中断turnの履歴の件数。新しい順に保ち、超えた古い要素は参照ごと失う。"""


class Resolution(enum.StrEnum):
    """登録簿からsessionを観測した区分。"""

    TERMINAL = "terminal"
    RUNNING = "running"
    MISSING = "missing"
    UNREADABLE = "unreadable"
    RELEASED = "released"


@dataclasses.dataclass(frozen=True)
class ProcessIdentity:
    """turnを実行し得るプロセスを、pidの再利用と区別できる起動時刻（`psutil`の`create_time`）とともに識別する。"""

    pid: int
    create_time: float

    @classmethod
    def of(cls, pid: int | None) -> ProcessIdentity | None:
        """実在するプロセスの識別を返し、取得できなければ`None`を返す。"""
        if pid is None:
            return None
        try:
            return cls(pid, psutil.Process(pid).create_time())
        except psutil.Error:
            return None

    def exists(self) -> bool:
        """同じpidと起動時刻のプロセスが終了していなければ真を返す。照会を拒否された場合は存在するものとして扱う。"""
        try:
            process = psutil.Process(self.pid)
            return process.create_time() == self.create_time and process.status() != psutil.STATUS_ZOMBIE
        except psutil.NoSuchProcess, psutil.ZombieProcess:
            return False
        except psutil.Error:
            return True


_OWNER_PROCESS: ProcessIdentity | None = None


def owner_process() -> ProcessIdentity | None:
    """登録簿を書くagents_server自身のプロセスの識別を返す。fork後は取得し直す。"""
    global _OWNER_PROCESS  # pylint: disable=global-statement
    if _OWNER_PROCESS is None or _OWNER_PROCESS.pid != os.getpid():
        _OWNER_PROCESS = ProcessIdentity.of(os.getpid())
    return _OWNER_PROCESS


_OWNER_SERVER_INSTANCE: str | None = None


def set_owner_server_instance(instance_id: str | None) -> None:
    """登録簿を書くagents_serverの起動単位を設定する。`publish`は以降の記録へこの値を書く。"""
    global _OWNER_SERVER_INSTANCE  # pylint: disable=global-statement
    _OWNER_SERVER_INSTANCE = instance_id


def owner_server_instance() -> str | None:
    """登録簿を書くagents_serverの起動単位を返す。stdio起動していないプロセスでは`None`を返す。"""
    return _OWNER_SERVER_INSTANCE


def owner_process_is_cached() -> bool:
    """現在のプロセスの識別をI/Oなしで返せるかを示す。"""
    return _OWNER_PROCESS is not None and _OWNER_PROCESS.pid == os.getpid()


def interruption_error(reason: InterruptionReason) -> dict[str, str]:
    """解決が示す中断の理由を、公開応答の`error`の形で返す。"""
    return {"message": _INTERRUPTION_MESSAGES[reason], "interruptionReason": reason}


@dataclasses.dataclass(frozen=True)
class LaunchInfo:
    """同じsessionの再開と引き継ぎに使う起動情報、追送と各turnの終了応答。

    再開で`SessionState`を再生成する処理と、session登録簿の書込・復元は、この定義の項目を走査して値を写す。
    項目を個別に列挙すると、項目を追加したときに一部の処理が値を写さなくなり、型やテストでは検出されないためである。
    `SessionState`と`SessionResumeState`は同名の属性を持つ。
    """

    label: str = ""
    prompt: str = ""
    # 項目を持たない旧形式の登録簿から復元したsessionでは開始時刻が不明なため`None`とする。
    created_at: str | None = None
    followup_prompts: tuple[str, ...] = ()
    final_responses: tuple[tuple[int, str], ...] = ()
    terminal_error: str | None = None

    @classmethod
    def of(cls, source: object) -> LaunchInfo:
        """同名の属性を持つsessionの状態から起動情報を取り出す。"""
        return cls(**{field.name: getattr(source, field.name) for field in dataclasses.fields(cls)})

    @classmethod
    def from_record(cls, payload: dict[str, Any]) -> LaunchInfo:
        """登録簿の任意項目を型ごとに復元し、欠けた値はフィールドの初期値とする。"""
        values = {
            field.name: payload[field.name] for field in dataclasses.fields(cls) if isinstance(payload.get(field.name), str)
        }
        for name in ("followup_prompts", "final_responses"):
            values.pop(name, None)
        followups = payload.get("followup_prompts", [])
        responses = payload.get("final_responses", [])
        return cls(
            **values,
            followup_prompts=tuple(item for item in followups if isinstance(item, str)) if isinstance(followups, list) else (),
            final_responses=tuple(
                (item[0], item[1])
                for item in responses
                if isinstance(item, list)
                and len(item) == 2
                and isinstance(item[0], int)
                and not isinstance(item[0], bool)
                and isinstance(item[1], str)
            )
            if isinstance(responses, list)
            else (),
        )

    def as_kwargs(self) -> dict[str, Any]:
        """同名の引数を持つ生成処理へ渡す項目を返す。"""
        return {field.name: getattr(self, field.name) for field in dataclasses.fields(self)}

    def apply_to(self, target: object) -> None:
        """再開で再生成したsessionへ値を写す。`None`の項目は写さず、生成時の値を残す。"""
        for name, value in self.as_kwargs().items():
            if value is not None:
                setattr(target, name, value)


@dataclasses.dataclass(frozen=True)
class ResumeInfo:
    """再起動後のsession再開へ必要な実行条件。"""

    engine: str
    cwd: str
    model: str | None
    effort: str | None
    model_type: str | None
    launch_kind: Literal["delegate", "explore", "shell", "write"]
    turn_seq: int
    status: Literal["starting", "running", "completed", "failed", "interrupted"]
    launch_info: LaunchInfo = LaunchInfo()
    # 項目を持たない旧形式のレコードでは`None`とする。
    started_at: str | None = None
    session_updated_at: str | None = None
    # 最後に公開した時点のturnを表すengine固有の識別子（Codexのturn id）。
    # 再起動後に残存記録の終了を委譲先CLIの記録と照らすために使う。項目を持たない記録とCodex以外では`None`とする。
    turn_id: str | None = None
    fast_mode: bool | None = None
    codex_model_provider: str | None = None
    codex_subscription_provider: str | None = None
    codex_state_provider: str | None = None
    claude_api_env_file: str | None = None
    claude_connection: str | None = None
    claude_role: str | None = None
    result_contract_recovery_consumed: bool = False


class RunningOwnership(enum.StrEnum):
    """`running`の記録の所有状態。`running_ownership`が記録プロセスの実在と状態ファイルの生存の印から判定する。"""

    OWNED = "owned"
    """生存の印が有効な状態ファイルがsessionを載せている。所有サーバーが終了を公開する。"""
    OWNER_LOST = "owner_lost"
    """所有サーバーを失った実行中。生存の印が有効な状態ファイルが無く、記録プロセスのうち委譲先CLIが実在する。"""
    ORPHANED = "orphaned"
    """所有者も委譲先CLIもいない。記録プロセスを持たない旧形式の記録と、委譲先CLIが実在しない記録が該当する。"""


@dataclasses.dataclass(frozen=True)
class InterruptedTurn:
    """中断で終わったturnと、そのturnを所有していた起動単位。`server_instance`が`None`なら旧記録を表す。"""

    turn_seq: int
    reason: InterruptionReason
    server_instance: str | None
    at: str


@dataclasses.dataclass(frozen=True)
class SessionResolution:
    """登録簿の解決結果と、利用可能な再開情報を保持する。"""

    state: Resolution
    resume_info: ResumeInfo | None = None
    # `RELEASED`の場合だけ、解放の理由と時刻（UTCのISO 8601）を保持する。
    released_reason: ReleaseReason | None = None
    released_at: str | None = None
    # 終了の理由が中断の判別値を持つ場合だけ保持する。記録上は`running`のまま実行プロセスの不在から
    # 終了と解決した場合と、その終了を引き継いで公開した記録の両方で設定する。
    interruption: InterruptionReason | None = None
    # 記録上は`running`で、記録された全プロセスの不在から終了と解決した場合に真とする。登録簿へ未公開であることを示す。
    inferred_terminal: bool = False
    handoff_info: ResumeInfo | None = None
    # 記録を最後に書いた所有サーバーの起動単位。項目を持たない旧記録では`None`とする。
    server_instance: str | None = None
    # 解放済み記録にも残る世代。世代を持たない旧記録との比較更新に用いる。
    recorded_turn_seq: int | None = None
    # 中断で終わったturnの履歴（新しい順）。再開・所有者の交代・解放の後も引き継ぐ。
    interrupted_turns: tuple[InterruptedTurn, ...] = ()
    # 記録プロセス。`processes`の先頭が所有サーバー、以降が委譲先CLIである。項目を持たない記録では空とする。
    owner_process: ProcessIdentity | None = None
    executor_processes: tuple[ProcessIdentity, ...] = ()
    # `running`の記録で、解決の時点で実在した委譲先CLI。終了の記録では照会しないため空とする。
    live_executors: tuple[ProcessIdentity, ...] = ()
    # 記録を書いた所有サーバーの状態ディレクトリのルート。項目を持たない旧記録では`None`とする。
    owner_root_session_id: str | None = None


def running_ownership(resolution: SessionResolution, *, writer_alive: bool) -> RunningOwnership | None:
    """`running`と解決した記録の所有状態を返す。`running`以外の解決には`None`を返す。

    `writer_alive`は、生存の印が有効な状態ファイルがそのsessionを載せているかを呼び出し側が照会した値である。
    状態ファイルを読む処理は`_agents_server`に属するため、層の順序に従い呼び出し側から受け取る。
    所有サーバーのプロセスが実在しても、生存の印を更新しない起動単位は所有者として扱わない。
    委譲先CLIが実在する限りturnが進んでいる可能性を排除できないため、終了と推定する処理は`ORPHANED`に限る。
    """
    if resolution.state is not Resolution.RUNNING:
        return None
    if writer_alive:
        return RunningOwnership.OWNED
    return RunningOwnership.OWNER_LOST if resolution.live_executors else RunningOwnership.ORPHANED


def process_payload(process: ProcessIdentity) -> dict[str, Any]:
    """公開応答と診断へ載せる記録プロセスの識別を返す。"""
    return {"pid": process.pid, "create_time": process.create_time}


def registry_directory(state_root: pathlib.Path | None = None) -> pathlib.Path:
    """session登録簿のディレクトリを返す。"""
    return _session_launchers.registry_directory(_state_paths.state_dir() if state_root is None else state_root)


@contextlib.contextmanager
def registry_lock(session_id: str, *, state_root: pathlib.Path | None = None) -> Generator[None]:
    """公開・比較更新・掃引をsession単位で直列化する。ロック実体は削除せず同じinodeを共有する。"""
    _validate_session_id(session_id)
    directory = registry_directory(state_root) / ".locks"
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (directory / f"{session_id}.lock").open("a+", encoding="utf-8") as handle:
        file_lock.acquire_lock(handle)
        try:
            yield
        finally:
            file_lock.release_lock(handle)


def _recorded_turn(payload: dict[str, Any]) -> int | None:
    value = payload.get("turn_seq")
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    for key in ("handoff_info", "codex_resume_info", "claude_resume_info"):
        nested = payload.get(key)
        if isinstance(nested, dict):
            value = nested.get("turn_seq")
            if isinstance(value, int) and not isinstance(value, bool):
                return value
    return None


def publish(
    session_id: str,
    *,
    terminal: bool,
    engine: str = "codex",
    cwd: str = "",
    model: str | None = None,
    effort: str | None = None,
    fast_mode: bool | None = None,
    codex_model_provider: str | None = None,
    codex_subscription_provider: str | None = None,
    codex_state_provider: str | None = None,
    claude_api_env_file: str | None = None,
    claude_connection: str | None = None,
    claude_role: str | None = None,
    model_type: str | None = None,
    launch_kind: Literal["delegate", "explore", "shell", "write"] = "delegate",
    turn_seq: int = 0,
    status: Literal["starting", "running", "completed", "failed", "interrupted"] | None = None,
    launch_info: LaunchInfo | None = None,
    started_at: str | None = None,
    session_updated_at: str | None = None,
    turn_id: str | None = None,
    processes: Sequence[ProcessIdentity] = (),
    interruption: InterruptionReason | None = None,
    launcher_session_id: str | None = None,
    preserve_server_instance: bool = False,
    owner_root_session_id: str | None = None,
    result_contract_recovery_consumed: bool = False,
    state_root: pathlib.Path | None = None,
) -> None:
    """sessionの終了可否と再開条件を原子的に公開する。

    `processes`は所有するagents_serverとturnを実行する委譲先CLIのプロセスの識別で、`resolve`が実行中の記録の
    終了を判定する入力になる。先頭を所有サーバー、以降を委譲先CLIとする（`resolve`と`session_release`がこの順序で読む）。
    `interruption`は実行プロセスの不在から引き継いだ終了の理由を残す。
    `owner_root_session_id`は所有サーバーの状態ディレクトリのルートで、所有サーバーを失った実行中の案内に使う。
    省略した場合は既存のレコードが持つ値を引き継ぐ。
    `launcher_session_id`はsessionを作成した時点の委譲元sessionの識別子である。
    省略した場合は既存のレコードが持つ値を引き継ぐ。`atk serve`の一覧が、親のセッション記録に
    起動結果が残らない委譲先の親を結ぶために読むため、状態の更新で失わないようにする。
    `server_instance`は所有サーバーの起動単位（`set_owner_server_instance`の値）を書く。
    `preserve_server_instance`が真の場合は既存の記録の値を保つ（所有者のいない記録の引き継ぎで使う）。
    既存の記録の`interrupted_turns`は引き継ぎ、`interruption`を渡した場合は現在のturnを履歴へ加える。
    """
    _validate_session_id(session_id)
    if not isinstance(terminal, bool):
        raise TypeError("terminal must be a bool")
    status = ("completed" if terminal else "running") if status is None else status
    if status not in _STATUSES:
        raise ValueError(f"invalid status: {status}")
    updated_at = datetime.datetime.now(datetime.UTC).isoformat()
    payload = {
        "version": 2,
        "session_id": session_id,
        "terminal": terminal,
        "engine": engine,
        "cwd": cwd,
        "model": model,
        "effort": effort,
        "model_type": model_type,
        "launch_kind": launch_kind,
        "turn_seq": turn_seq,
        "status": status,
        "updated_at": updated_at,
    }
    if launch_info is not None:
        # 版数2の任意項目として加え、項目を持たない旧形式のレコードは`LaunchInfo.from_record`が空文字列か`None`として読む。
        payload.update((name, value) for name, value in launch_info.as_kwargs().items() if value is not None)
    if started_at is not None:
        payload["started_at"] = started_at
    if session_updated_at is not None:
        payload["session_updated_at"] = session_updated_at
    if turn_id:
        payload["turn_id"] = turn_id
    if processes:
        payload["processes"] = [process_payload(process) for process in processes]
    if interruption is not None:
        payload["interruption"] = interruption
    if engine == "codex" and fast_mode is not None:
        payload["fast_mode"] = fast_mode
    if engine == "codex" and codex_model_provider:
        payload["codex_model_provider"] = codex_model_provider
        payload["codex_subscription_provider"] = codex_subscription_provider
    if engine == "codex" and codex_state_provider:
        payload["codex_state_provider"] = codex_state_provider
    if engine == "claude" and claude_api_env_file:
        payload["claude_api_env_file"] = claude_api_env_file
    if engine == "claude" and claude_connection:
        payload["claude_connection"] = claude_connection
        payload["claude_role"] = claude_role
    path = registry_directory(state_root) / f"{session_id}.json"
    with registry_lock(session_id, state_root=state_root):
        prior = _read_payload(path)
        previous_turn = _recorded_turn(prior)
        if previous_turn is not None and (previous_turn > turn_seq or prior.get("version") == 3 and previous_turn == turn_seq):
            return
        # session単位の回復上限は、別所有者の状態更新でも未消費へ戻さない。
        if result_contract_recovery_consumed or prior.get("result_contract_recovery_consumed") is True:
            payload["result_contract_recovery_consumed"] = True
        owner_root = owner_root_session_id if owner_root_session_id is not None else _recorded_owner_root(prior)
        if owner_root is not None:
            payload["owner_root_session_id"] = owner_root
        instance = prior.get("server_instance") if preserve_server_instance else _OWNER_SERVER_INSTANCE
        if isinstance(instance, str) and instance:
            payload["server_instance"] = instance
        turns = _recorded_interrupted_turns(prior)
        if interruption is not None:
            recorded_instance = instance if isinstance(instance, str) and instance else None
            turns = _with_interrupted_turn(turns, InterruptedTurn(turn_seq, interruption, recorded_instance, updated_at))
        if turns:
            payload["interrupted_turns"] = _interrupted_turns_payload(turns)
        launcher = launcher_session_id if launcher_session_id is not None else _recorded_launcher(path)
        if launcher is not None:
            payload[LAUNCHER_KEY] = launcher
        atomic_write(path, json.dumps(payload, ensure_ascii=False) + "\n")


_recorded_launcher = _session_launchers.read_launcher


def resolve(session_id: str, *, state_root: pathlib.Path | None = None) -> SessionResolution:
    """登録済みsessionを終了・実行中・解放済み・不在・読取不能へ区分して返す。

    `running`の記録がプロセスの識別を持ち、記録された全プロセスが存在しない場合は、所有側が終了を公開せずに
    終了したものとして`interrupted`の終了と解決する。1つでも存在する記録と識別を持たない記録は実行中とする。
    実行中の解決は、実在した委譲先CLIを`live_executors`へ返す。所有状態の判定は`running_ownership`が行う。
    """
    _validate_session_id(session_id)
    path = registry_directory(state_root) / f"{session_id}.json"
    try:
        payload: Any = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return SessionResolution(Resolution.MISSING)
    except OSError, UnicodeError, json.JSONDecodeError:
        return SessionResolution(Resolution.UNREADABLE)
    if (
        not isinstance(payload, dict)
        or payload.get("session_id") != session_id
        or not isinstance(payload.get("updated_at"), str)
    ):
        return SessionResolution(Resolution.UNREADABLE)
    if payload.get("version") == 3:
        reason = payload.get("released_reason")
        if reason not in _RELEASE_REASONS:
            return SessionResolution(Resolution.UNREADABLE)
        resume = payload.get("codex_resume_info")
        info = _resume_info(resume) if isinstance(resume, dict) and resume.get("engine") == "codex" else None
        if info is None:
            resume = payload.get("claude_resume_info")
            info = _resume_info(resume) if isinstance(resume, dict) and resume.get("engine") == "claude" else None
        handoff = payload.get("handoff_info")
        return SessionResolution(
            Resolution.RELEASED,
            info,
            released_reason=reason,
            released_at=payload["updated_at"],
            handoff_info=_resume_info(handoff) if isinstance(handoff, dict) else None,
            server_instance=_recorded_server_instance(payload),
            recorded_turn_seq=_recorded_turn(payload),
            interrupted_turns=_recorded_interrupted_turns(payload),
        )
    if payload.get("version") == 1:
        if not isinstance(payload.get("terminal"), bool):
            return SessionResolution(Resolution.UNREADABLE)
        return SessionResolution(Resolution.TERMINAL if payload["terminal"] else Resolution.RUNNING)
    if payload.get("version") != 2 or not isinstance(payload.get("terminal"), bool):
        return SessionResolution(Resolution.UNREADABLE)
    info = _resume_info(payload)
    if info is None:
        return SessionResolution(Resolution.UNREADABLE)
    instance = _recorded_server_instance(payload)
    turns = _recorded_interrupted_turns(payload)
    processes = _recorded_processes(payload)
    owner = processes[0] if processes else None
    executors = processes[1:]
    owner_root = _recorded_owner_root(payload)
    if payload["terminal"]:
        interruption = payload.get("interruption")
        return SessionResolution(
            Resolution.TERMINAL,
            info,
            interruption=interruption if interruption in _INTERRUPTION_MESSAGES else None,
            server_instance=instance,
            recorded_turn_seq=_recorded_turn(payload),
            interrupted_turns=turns,
            owner_process=owner,
            executor_processes=executors,
            owner_root_session_id=owner_root,
        )
    alive = [process for process in processes if process.exists()]
    if processes and not alive:
        return SessionResolution(
            Resolution.TERMINAL,
            dataclasses.replace(info, status="interrupted"),
            interruption=OWNER_PROCESSES_EXITED,
            inferred_terminal=True,
            server_instance=instance,
            recorded_turn_seq=_recorded_turn(payload),
            interrupted_turns=turns,
            owner_process=owner,
            executor_processes=executors,
            owner_root_session_id=owner_root,
        )
    return SessionResolution(
        Resolution.RUNNING,
        info,
        server_instance=instance,
        recorded_turn_seq=_recorded_turn(payload),
        interrupted_turns=turns,
        owner_process=owner,
        executor_processes=executors,
        live_executors=tuple(process for process in executors if process in alive),
        owner_root_session_id=owner_root,
    )


def _recorded_processes(payload: dict[str, Any]) -> tuple[ProcessIdentity, ...]:
    """記録されたプロセスの識別を返す。項目が無いか形式が不正な記録は識別を持たないものとして空を返す。"""
    entries = payload.get("processes")
    if not isinstance(entries, list) or not entries:
        return ()
    processes: list[ProcessIdentity] = []
    for entry in entries:
        pid = entry.get("pid") if isinstance(entry, dict) else None
        create_time = entry.get("create_time") if isinstance(entry, dict) else None
        if (
            not isinstance(pid, int)
            or isinstance(pid, bool)
            or not isinstance(create_time, (int, float))
            or isinstance(create_time, bool)
        ):
            return ()
        processes.append(ProcessIdentity(pid, float(create_time)))
    return tuple(processes)


def release(
    session_id: str,
    *,
    reason: ReleaseReason,
    state_root: pathlib.Path | None = None,
    expected_owner: tuple[str | None, int | None] | None = None,
) -> bool:
    """所有側の解放を記録し、APIを使う会話の内部再開条件を保つ。

    解放済みの公開区分は保持し、7日超の掃引で他のレコードと同じく回収する。
    `expected_owner`を渡した場合は起動単位とturn番号が一致する記録だけを置換し、変更済みなら偽を返す。
    """
    _validate_session_id(session_id)
    if reason not in _RELEASE_REASONS:
        raise ValueError(f"invalid release reason: {reason}")
    with registry_lock(session_id, state_root=state_root):
        path = registry_directory(state_root) / f"{session_id}.json"
        prior_payload = _read_payload(path)
        if expected_owner is not None and expected_owner != (
            _recorded_server_instance(prior_payload),
            _recorded_turn(prior_payload),
        ):
            return False
        payload: dict[str, Any] = {
            "version": 3,
            "session_id": session_id,
            "released_reason": reason,
            "turn_seq": _recorded_turn(prior_payload),
            "updated_at": datetime.datetime.now(datetime.UTC).isoformat(),
        }
        launcher = _recorded_launcher(path)
        resolution = resolve(session_id, state_root=state_root)
        info = resolution.resume_info or resolution.handoff_info
        if info is not None and info.launch_info.prompt:
            payload["handoff_info"] = prior_payload.get(
                "handoff_info", prior_payload.get("codex_resume_info", prior_payload.get("claude_resume_info", prior_payload))
            )
        if (
            info is not None
            and info.engine == "codex"
            and (
                info.codex_state_provider
                or info.codex_model_provider
                and info.codex_model_provider != info.codex_subscription_provider
            )
        ):
            # 初回解放のv2と、既に解放したv3の両方で同じ内部再開条件を引き継ぐ。
            payload["codex_resume_info"] = prior_payload.get("codex_resume_info", prior_payload)
        if info is not None and info.engine == "claude" and (info.claude_api_env_file or info.claude_connection):
            payload["claude_resume_info"] = prior_payload.get("claude_resume_info", prior_payload)
        instance = _recorded_server_instance(prior_payload)
        if instance is not None:
            payload["server_instance"] = instance
        turns = _recorded_interrupted_turns(prior_payload)
        if turns:
            payload["interrupted_turns"] = _interrupted_turns_payload(turns)
        if launcher is not None:
            payload[LAUNCHER_KEY] = launcher
        atomic_write(path, json.dumps(payload, ensure_ascii=False) + "\n")
        return True


def record_interrupted_turn(
    session_id: str, *, turn_seq: int, reason: InterruptionReason, state_root: pathlib.Path | None = None
) -> None:
    """所有側が中断として公開した終了の記録へ、中断の理由と中断turnの履歴を加える。

    記録が無いか、同じturnの終了を示さない場合は書き換えない。
    """
    _validate_session_id(session_id)
    with registry_lock(session_id, state_root=state_root):
        path = registry_directory(state_root) / f"{session_id}.json"
        payload = _read_payload(path)
        if payload.get("version") != 2 or payload.get("terminal") is not True or payload.get("turn_seq") != turn_seq:
            return
        payload["interruption"] = reason
        payload["interrupted_turns"] = _interrupted_turns_payload(
            _with_interrupted_turn(
                _recorded_interrupted_turns(payload),
                InterruptedTurn(
                    turn_seq, reason, _recorded_server_instance(payload), datetime.datetime.now(datetime.UTC).isoformat()
                ),
            )
        )
        atomic_write(path, json.dumps(payload, ensure_ascii=False) + "\n")


def recorded_server_instances(state_root: pathlib.Path | None = None) -> set[str]:
    """登録簿の全記録が参照する起動単位を返す。読めない記録は除く。"""
    instances: set[str] = set()
    try:
        paths = tuple(registry_directory(state_root).glob("*.json"))
    except OSError:
        return instances
    for path in paths:
        payload = _read_payload(path)
        instance = _recorded_server_instance(payload)
        if instance is not None:
            instances.add(instance)
        instances.update(turn.server_instance for turn in _recorded_interrupted_turns(payload) if turn.server_instance)
    return instances


def _read_payload(path: pathlib.Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError, UnicodeError, json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _recorded_owner_root(payload: dict[str, Any]) -> str | None:
    value = payload.get("owner_root_session_id")
    return value if isinstance(value, str) and _SESSION_ID_PATTERN.fullmatch(value) else None


def _recorded_server_instance(payload: dict[str, Any]) -> str | None:
    value = payload.get("server_instance")
    return value if isinstance(value, str) and _SESSION_ID_PATTERN.fullmatch(value) else None


def _recorded_interrupted_turns(payload: dict[str, Any]) -> tuple[InterruptedTurn, ...]:
    entries = payload.get("interrupted_turns")
    if not isinstance(entries, list):
        return ()
    turns: list[InterruptedTurn] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        turn_seq = entry.get("turn_seq")
        reason = entry.get("reason")
        instance = entry.get("server_instance")
        at = entry.get("at")
        if (
            isinstance(turn_seq, int)
            and not isinstance(turn_seq, bool)
            and reason in _INTERRUPTION_MESSAGES
            and (instance is None or isinstance(instance, str) and _SESSION_ID_PATTERN.fullmatch(instance))
            and isinstance(at, str)
        ):
            turns.append(InterruptedTurn(turn_seq, reason, instance, at))
    return tuple(turns)


def _with_interrupted_turn(turns: tuple[InterruptedTurn, ...], turn: InterruptedTurn) -> tuple[InterruptedTurn, ...]:
    if any(existing.turn_seq == turn.turn_seq for existing in turns):
        return turns
    return (turn, *turns)[:INTERRUPTED_TURNS_LIMIT]


def _interrupted_turns_payload(turns: tuple[InterruptedTurn, ...]) -> list[dict[str, Any]]:
    return [dataclasses.asdict(turn) for turn in turns]


def _validate_session_id(session_id: str) -> None:
    if not isinstance(session_id, str) or not _SESSION_ID_PATTERN.fullmatch(session_id):
        raise ValueError(f"invalid session_id: {session_id}")


def _resume_info(payload: dict[str, Any]) -> ResumeInfo | None:
    status = payload.get("status")
    launch_kind = payload.get("launch_kind")
    if (
        not isinstance(payload.get("engine"), str)
        or not isinstance(payload.get("cwd"), str)
        or payload.get("model") is not None
        and not isinstance(payload.get("model"), str)
        or payload.get("effort") is not None
        and not isinstance(payload.get("effort"), str)
        or payload.get("model_type") is not None
        and not isinstance(payload.get("model_type"), str)
        or launch_kind not in {"delegate", "explore", "shell", "write"}
        or not isinstance(payload.get("turn_seq"), int)
        or isinstance(payload.get("turn_seq"), bool)
        or status not in _STATUSES
    ):
        return None
    return ResumeInfo(
        engine=payload["engine"],
        cwd=payload["cwd"],
        model=payload["model"],
        effort=payload["effort"],
        model_type=payload["model_type"],
        launch_kind=launch_kind,
        turn_seq=payload["turn_seq"],
        status=status,
        launch_info=LaunchInfo.from_record(payload),
        result_contract_recovery_consumed=payload.get("result_contract_recovery_consumed") is True,
        started_at=payload.get("started_at") if isinstance(payload.get("started_at"), str) else None,
        session_updated_at=payload.get("session_updated_at") if isinstance(payload.get("session_updated_at"), str) else None,
        turn_id=payload.get("turn_id") if isinstance(payload.get("turn_id"), str) and payload.get("turn_id") else None,
        fast_mode=payload.get("fast_mode")
        if payload["engine"] == "codex" and isinstance(payload.get("fast_mode"), bool)
        else None,
        codex_model_provider=payload.get("codex_model_provider")
        if payload["engine"] == "codex" and isinstance(payload.get("codex_model_provider"), str)
        else None,
        codex_subscription_provider=payload.get("codex_subscription_provider")
        if payload["engine"] == "codex" and isinstance(payload.get("codex_subscription_provider"), str)
        else None,
        codex_state_provider=payload.get("codex_state_provider")
        if payload["engine"] == "codex" and isinstance(payload.get("codex_state_provider"), str)
        else None,
        claude_api_env_file=payload.get("claude_api_env_file")
        if payload["engine"] == "claude" and isinstance(payload.get("claude_api_env_file"), str)
        else None,
        claude_connection=payload.get("claude_connection")
        if payload.get("claude_connection") in {"api", "subscription", "inherit"}
        else "api"
        if payload.get("claude_api_env_file")
        else None,
        claude_role=payload.get("claude_role")
        if payload.get("claude_role") in {"orchestrator", "delegate"}
        else "orchestrator"
        if payload.get("model_type") == "orchestrate"
        else "delegate",
    )
