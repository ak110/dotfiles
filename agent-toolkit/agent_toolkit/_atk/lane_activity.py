"""作業ツリーを使う担当・外部プロセスと未保存変更の判定。"""

from __future__ import annotations

import dataclasses
import os
import pathlib
import time

lazy import psutil

from agent_toolkit._atk import lane_records as _lane_records
from agent_toolkit._atk.managed_temp import permissions as temp_permissions
from agent_toolkit._common import session_launchers, session_release, state_paths
from agent_toolkit._common.wait_schedule import get_wait_timeout
from agent_toolkit._git import command as git_command
from agent_toolkit._git import worktrees as git_worktrees
lazy from agent_toolkit._common import session_registry, task_documents


@dataclasses.dataclass(frozen=True)
class RunningSession:
    """作業ツリーを使う稼働中の担当。統合の稼働判定・診断と回収の使用判定が同じ収集結果を使う。"""

    session_id: str
    label: str
    cwd: str
    worktree: pathlib.Path
    launch_kind: str
    # 起動本文が`task`の見出しで始まる場合だけ、その役割文書の絶対パスを持つ。
    task_document: pathlib.Path | None

    def writes_worktree(self) -> bool:
        """統合の稼働判定で書込主体として数えるかを返す。

        役割文書を特定できない起動と、宣言を読めない役割文書は、書き込む担当として数える。
        """
        if self.launch_kind == "explore":
            return False
        if self.task_document is None:
            return True
        declaration = task_documents.read_declaration(self.task_document)
        return isinstance(declaration, str) or declaration.writes_worktree

    def describe(self) -> str:
        """不足担当の診断へ示す1件分の識別情報を返す。"""
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
    """session登録簿の解決結果から、その作業ツリーへ所属する稼働中の担当を全件返す。

    終了の判定は`session_registry.resolve`に従い、所有側が終了を公開せずに終了して実行プロセスも
    存在しない記録は終了した担当として扱う。解決できない登録は所属を判定できず、未確認の担当を見逃さないよう
    `ValueError`で止める。対象外のworktreeに属すると判定できる登録は除かれる。新しいworktreeの作成は本関数を呼ばない。
    """
    state_root = state_paths.state_dir()
    directory = session_launchers.registry_directory(state_root)
    if not directory.exists():
        return []
    entries = git_worktrees.list_worktrees(worktree, timeout=_lane_records.TIMEOUT)
    target = git_worktrees.owning_worktree(worktree, entries)
    running: list[RunningSession] = []
    for path in sorted(directory.glob("*.json")):
        try:
            resolution = session_registry.resolve(path.stem, state_root=state_root)
        except ValueError as exc:
            raise ValueError(unreadable_registration_message(path)) from exc
        if resolution.state is session_registry.Resolution.UNREADABLE:
            raise ValueError(unreadable_registration_message(path))
        info = resolution.resume_info
        if resolution.state is not session_registry.Resolution.RUNNING or info is None or not info.cwd:
            continue
        owner = git_worktrees.owning_worktree(pathlib.Path(info.cwd), entries)
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


def session_activity(worktree: pathlib.Path) -> str | None:
    """その作業ツリーを使う担当が全て終了したかを判定し、稼働中なら最初の担当を理由として返す。"""
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
    """両作業ツリーの稼働中の担当から、除外集合に無い書込主体を全件返す。"""
    return [
        session
        for tree in trees
        for session in running_sessions(tree)
        if session.session_id not in excluded and session.writes_worktree()
    ]


def process_activity(worktree: pathlib.Path) -> str | None:
    entries = git_worktrees.list_worktrees(worktree, timeout=_lane_records.TIMEOUT)
    target = git_worktrees.owning_worktree(worktree, entries)
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
        if cwd is not None and git_worktrees.owning_worktree(cwd, entries) == target:
            return f"外部プロセスが使用中: pid={process.pid}"
        # 所属の分からない別プロセスの取得失敗を、当該レーンの保持条件へ広げない。
        try:
            argv = process.cmdline()
        except psutil.NoSuchProcess, psutil.AccessDenied:
            continue
        for argument in argv:
            candidate = pathlib.Path(argument.split("=", 1)[-1])
            if candidate.is_absolute() and git_worktrees.owning_worktree(candidate, entries) == target:
                return f"外部プロセスが資源を参照中: pid={process.pid}"
    return None


def worktree_activity(worktree: pathlib.Path, *, wait_release: bool = True) -> str | None:
    """所有側へ終了資源の解放を要求してから、担当と外部プロセスの使用を再判定する。"""
    directory = session_registry.registry_directory()
    deadline: float | None = None
    released: list[str] = []
    entries = git_worktrees.list_worktrees(worktree, timeout=_lane_records.TIMEOUT)
    target = git_worktrees.owning_worktree(worktree, entries)
    for path in directory.glob("*.json"):
        resolution = session_registry.resolve(path.stem)
        info = resolution.resume_info
        if info is None or not info.cwd or git_worktrees.owning_worktree(pathlib.Path(info.cwd), entries) != target:
            continue
        instruction = f"agents_serverのstopへsession_id={path.stem}を渡し、同じlane deleteを再実行する"
        if resolution.state is session_registry.Resolution.RUNNING:
            return (
                f"担当の終了を確認できない: session_id={path.stem}。"
                "自然終了を確認し、atk agents waitで結果を回収して再開する予定が無ければ、"
                f"{instruction}"
            )
        if resolution.state is session_registry.Resolution.TERMINAL:
            if deadline is None:
                deadline = time.monotonic() + (get_wait_timeout("main") if wait_release else 0)
            reason = session_release.request_release(
                path.stem, worktree, deadline=deadline, worktree_paths=tuple(entry.path for entry in entries)
            )
            if reason:
                return f"session_id={path.stem}: {reason}。{instruction}"
            released.append(path.stem)
    # 所有側の応答は要求対象の終了まで検収する。応答後の独立した使用は、その時点の保持理由にする。
    activity = session_activity(worktree) or process_activity(worktree)
    if activity and released:
        identifiers = ",".join(released)
        return f"{activity}。session_id={identifiers}。agents_serverのstopへ各session_idを渡し、同じlane deleteを再実行する"
    return activity


def has_unignored_changes(worktree: pathlib.Path) -> bool:
    """Gitの保護対象だけを返し、無視された生成物は固有撤去へ渡す。"""
    # indexの列挙は作業木を読まない。ignore配下でも追跡済みファイルとその親は必ず保護する。
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
    return bool(_lane_records.git(worktree, "status", "--porcelain=v1", "--untracked-files=all"))
