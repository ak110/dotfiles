from __future__ import annotations

import dataclasses
import os
import pathlib
import time
from collections.abc import Callable

lazy import psutil

from agent_toolkit._atk import lane_records as _lane_records
from agent_toolkit._atk.managed_temp import permissions as temp_permissions
from agent_toolkit._common import registry_index, session_launchers, session_layout, session_release, state_paths
from agent_toolkit._common.wait_schedule import get_wait_timeout
from agent_toolkit._git import command as git_command
from agent_toolkit._git import worktrees as git_worktrees
lazy from agent_toolkit._common import session_registry, task_documents

# 所有側の解放応答は短い制御操作であり、LLMの結果待機TTLまで引き延ばさない。
_RELEASE_WAIT_SECONDS = 30.0


@dataclasses.dataclass(frozen=True)
class RunningSession:
    session_id: str
    label: str
    cwd: str
    worktree: pathlib.Path
    launch_kind: str

    task_document: pathlib.Path | None

    def writes_worktree(self) -> bool:
        if self.launch_kind == "explore":
            return False
        if self.task_document is None:
            return True
        declaration = task_documents.read_declaration(self.task_document)
        return isinstance(declaration, str) or declaration.writes_worktree

    def describe(self) -> str:
        document = f", 役割文書={self.task_document}" if self.task_document is not None else ""
        return (
            f"session_id={self.session_id}, label={self.label or '-'}{document}, cwd={self.cwd}, 所属worktree={self.worktree}"
        )


def unreadable_registration_message(path: pathlib.Path) -> str:
    """読めない担当登録のため作業を失いうる操作を止める理由と、回復手段を返す。

    登録の所属worktreeは`resume_info`の`cwd`にしか無く、読めない登録からは判定できない。
    稼働中の担当が対象worktreeにいた場合に未commitの作業を失わないよう、削除・統合・再利用・回収は止める。
    `lane`の失敗出力は例外の`str()`だけを理由として使うため、回復手段も本文へ含める。
    """
    return (
        f"担当登録を読めないため、作業を失いうる操作を止めた: {path.resolve()}。"
        f"`atk agents show {path.stem}`で担当が終了済みかを確かめ、終了済みと確かめた後に"
        f"{path.resolve()}を退避または削除して、同じ操作を再実行する"
    )


def running_sessions(worktree: pathlib.Path) -> list[RunningSession]:
    state_root = state_paths.state_dir()
    directory = session_launchers.registry_directory(state_root)
    if not directory.exists():
        return []
    entries = git_worktrees.list_worktrees(worktree, timeout=_lane_records.TIMEOUT)
    resolved = git_worktrees.resolve_worktrees(entries)
    target = git_worktrees.owning_resolved_worktree(worktree, resolved)
    running: list[RunningSession] = []

    for path in _registered_candidates(
        directory, state_root, resolved, target, include_unreadable=True, relevant=lambda entry: entry.pending or entry.claimed
    ):
        try:
            resolution = session_registry.resolve(path.stem, state_root=state_root)
        except ValueError as exc:
            raise ValueError(unreadable_registration_message(path)) from exc
        if resolution.state is session_registry.Resolution.UNREADABLE:
            raise ValueError(unreadable_registration_message(path))
        info = resolution.resume_info
        if resolution.state is not session_registry.Resolution.RUNNING or info is None or not info.cwd:
            continue
        owner = git_worktrees.owning_resolved_worktree(pathlib.Path(info.cwd), resolved)
        if owner is None or owner != target:
            continue
        running.append(
            RunningSession(
                session_id=path.stem,
                label=info.launch_info.label,
                cwd=info.cwd,
                worktree=owner.path.resolve(),
                launch_kind=info.launch_kind,
                task_document=task_documents.launched_task_document(info.launch_info.prompt),
            )
        )
    return running


def _registered_candidates(
    directory: pathlib.Path,
    state_root: pathlib.Path,
    resolved: dict[pathlib.Path, git_worktrees.Worktree],
    target: git_worktrees.Worktree | None,
    *,
    include_unreadable: bool,
    relevant: Callable[[registry_index.Entry], bool],
) -> list[pathlib.Path]:
    owners: dict[str, git_worktrees.Worktree | None] = {}
    candidates: list[pathlib.Path] = []
    for name, entry in sorted(registry_index.entries(state_root).items()):
        if not session_layout.valid_session_id(name) or include_unreadable and not entry.readable:
            candidates.append(directory / f"{name}.json")
            continue
        if not entry.cwd or not relevant(entry):
            continue
        if entry.cwd not in owners:
            owners[entry.cwd] = git_worktrees.owning_resolved_worktree(pathlib.Path(entry.cwd), resolved)
        if owners[entry.cwd] is not None and owners[entry.cwd] == target:
            candidates.append(directory / f"{name}.json")
    return candidates


def session_activity(worktree: pathlib.Path) -> str | None:
    running = running_sessions(worktree)
    return f"担当の終了を確認できない: {running[0].session_id}" if running else None


def caller_chain() -> frozenset[str]:
    """統合を呼び出した担当と、その委譲元の連鎖を登録簿と同じ識別子で返す。

    起点は呼出元のホストsessionとし、レーンの所有session（`SESSION_ENV`）は別の寿命を持つ識別子のため使わない。
    Codexの委譲先は起動元のClaude Codeの`CLAUDE_CODE_SESSION_ID`を継承し得るため、値を持つ両方の変数を起点にする。
    委譲元は統合中に作業ツリー全体へ作用する操作をしない契約のため、書込主体から外す。
    """
    directory = session_registry.registry_directory()
    chain: set[str] = set()
    for name in ("CLAUDE_CODE_SESSION_ID", "CODEX_THREAD_ID"):
        current = os.environ.get(name)
        while current and _lane_records.ID.fullmatch(current) and current not in chain:
            chain.add(current)
            current = session_launchers.read_launcher(directory / f"{current}.json")
    return frozenset(chain)


def unowned_writers(trees: tuple[pathlib.Path, ...], excluded: frozenset[str]) -> list[RunningSession]:
    return [
        session
        for tree in trees
        for session in running_sessions(tree)
        if session.session_id not in excluded and session.writes_worktree()
    ]


def process_activity(worktree: pathlib.Path) -> str | None:
    entries = git_worktrees.list_worktrees(worktree, timeout=_lane_records.TIMEOUT)

    resolved = git_worktrees.resolve_worktrees(entries)
    target = git_worktrees.owning_resolved_worktree(worktree, resolved)
    username = psutil.Process().username()
    for process in psutil.process_iter(["username"]):
        if process.info["username"] != username:
            continue
        try:
            cwd = pathlib.Path(process.cwd()).resolve()
        except psutil.NoSuchProcess:
            continue
        except psutil.AccessDenied:
            cwd = None
        if cwd is not None and git_worktrees.owning_resolved_worktree(cwd, resolved) == target:
            return f"外部プロセスが使用中: pid={process.pid}"
        # 所属の分からない別プロセスの取得失敗を、当該レーンの保持条件へ広げない。
        try:
            argv = process.cmdline()
        except psutil.NoSuchProcess, psutil.AccessDenied:
            continue
        for argument in argv:
            candidate = pathlib.Path(argument.split("=", 1)[-1])
            if candidate.is_absolute() and git_worktrees.owning_resolved_worktree(candidate, resolved) == target:
                return f"外部プロセスが資源を参照中: pid={process.pid}"
    return None


def worktree_activity(worktree: pathlib.Path, *, wait_release: bool = True) -> str | None:
    state_root = state_paths.state_dir()
    directory = session_launchers.registry_directory(state_root)
    deadline: float | None = None
    released: list[str] = []
    entries = git_worktrees.list_worktrees(worktree, timeout=_lane_records.TIMEOUT)
    resolved = git_worktrees.resolve_worktrees(entries)
    target = git_worktrees.owning_resolved_worktree(worktree, resolved)

    for path in _registered_candidates(
        directory,
        state_root,
        resolved,
        target,
        include_unreadable=False,
        relevant=lambda entry: entry.claimed or not entry.released,
    ):
        resolution = session_registry.resolve(path.stem, state_root=state_root)
        info = resolution.resume_info
        if info is None or not info.cwd or git_worktrees.owning_resolved_worktree(pathlib.Path(info.cwd), resolved) != target:
            continue
        instruction = f"agents_serverのstopへsession_id={path.stem}を渡し、同じlane deleteを再実行する"
        if resolution.state is session_registry.Resolution.RUNNING:
            return (
                f"担当の終了を確認できない: session_id={path.stem}。"
                "自然終了を確認し、atk agents waitで結果を回収して再開する予定が無ければ、"
                f"{instruction}"
            )
        if resolution.state is session_registry.Resolution.TERMINAL:
            if _exited_and_collected(path.stem, state_root):
                continue
            if deadline is None:
                deadline = time.monotonic() + (min(get_wait_timeout("main"), _RELEASE_WAIT_SECONDS) if wait_release else 0)
            reason = session_release.request_release(
                path.stem, worktree, deadline=deadline, worktree_paths=tuple(entry.path for entry in entries)
            )
            if reason:
                return f"session_id={path.stem}: {reason}。{instruction}"
            released.append(path.stem)

    activity = session_activity(worktree) or process_activity(worktree)
    if activity and released:
        identifiers = ",".join(released)
        return f"{activity}。session_id={identifiers}。agents_serverのstopへ各session_idを渡し、同じlane deleteを再実行する"
    return activity


def _exited_and_collected(session_id: str, state_root: pathlib.Path) -> bool:
    """終了済みの担当のうち、実行プロセスの終了と結果の回収を両方観測できるものを、解放要求なしで保持の理由から外す。

    所有側は担当を記憶から外した後（委譲元の会話を閉じた後など）、生存したまま解放要求へ応答しない場合がある。
    この場合に残る資源は登録簿の記録だけであり、回収側は記録を書き換えない。
    実行プロセスの生存、再開予約、未回収の結果・待機対象、回収を確かめられない結果のいずれかがあれば、所有側の解放を待つ。
    """
    resolution = session_registry.resolve(session_id, state_root=state_root)
    info = resolution.resume_info
    if (
        resolution.state is not session_registry.Resolution.TERMINAL
        or resolution.resume_pending
        or info is None
        or any(process.exists() for process in resolution.executor_processes)
    ):
        return False
    try:
        return registry_index.result_collected(session_id, info.turn_seq, state_root=state_root)
    except OSError:
        return False


def has_unignored_changes(worktree: pathlib.Path) -> bool:

    tracked: set[str] = set()
    for name in _lane_records.git(worktree, "ls-files", "--cached", "-z").split("\0"):
        if name:
            path = pathlib.PurePosixPath(name)
            tracked.update(item.as_posix() for item in (path, *path.parents))

    def ignored(path: pathlib.Path) -> bool:
        relative = path.relative_to(worktree).as_posix()
        if relative in tracked or relative == ".git":
            return False
        result = git_command.run(
            ["check-ignore", "--quiet", "--no-index", "--", relative],
            worktree,
            capture_output=True,
            text=True,
            check=False,
            timeout=_lane_records.TIMEOUT,
        )
        if result.returncode not in {0, 1}:
            raise ValueError(
                f"git check-ignoreが終了{result.returncode}: {result.stderr.strip()}。"
                f"{worktree}のGit設定と読取権限を確認し、表示された原因を解消して同じlane操作を再実行してください。"
            )
        return result.returncode == 0

    temp_permissions.prepare_tree(worktree, skip=ignored)
    return bool(_lane_records.git(worktree, "status", "--porcelain=v1", "--untracked-files=all", "--ignore-submodules=none"))
