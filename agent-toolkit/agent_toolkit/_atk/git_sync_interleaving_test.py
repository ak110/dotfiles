"""private-notesのGit同期を、並行する実行主体の操作の順序を全て列挙して確かめる（有界の網羅検査）。

実際の`wi.sync`・`git_sync`のコードと実Gitを使い、各実行主体を1つのスレッドで動かして、
共有する資源（remoteのbareリポジトリ、複数の主体が使う作業コピー）へのGitの起動とロックの取得の直前で止める。
止めた主体のうち1つずつを進め、選び方の全ての組み合わせを深さ優先で再実行して列挙する。
1回のGitの起動を分割できない1手順とみなす。共有しない作業コピーへの操作は他の主体の操作と順序を入れ替えても
結果が変わらないため、止めずに続けて実行する。順序を入れ替えても結果が同じ操作の組（別の資源への操作、
remoteの読取同士）はsleep setで1通りだけ実行する。

対応表、性質、探索の上限、前提、最後に同期したcommitと実行コマンドは`.claude/skills/git-sync-interleavings/SKILL.md`にある。
"""

from __future__ import annotations

import dataclasses
import functools
import hashlib
import os
import pathlib
import shutil
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterator, Mapping
from typing import Any

import pytest

from agent_toolkit._atk import git_sync
from agent_toolkit._atk.wi import sync as wi_sync
from agent_toolkit._git import command as git_command
from agent_toolkit._testing import git_repository

_REMOTE = ("remote", "")
_REMOTE_READS = frozenset({"fetch", "ls-remote"})
_REMOTE_WRITES = frozenset({"push", "pull", "clone"})
_STEP_TIMEOUT_SECONDS = 60.0
_ENTRY_DIRECTORY = "2026/11"


def _entry(number: int) -> str:
    return f"{_ENTRY_DIRECTORY}/20261119-000000-{number:03d}.md"


def _base_content(number: int) -> str:
    return f"---\nstate: inbox\n---\n# 項目{number}\n"


def _updated_content(number: int, actor: str) -> str:
    return f"---\nstate: processing\nupdated_by: {actor}\n---\n# 項目{number}\n"


@dataclasses.dataclass(frozen=True)
class _Update:
    actor: str
    clone: str
    entry: int
    via_atk: bool = True
    """偽なら`atk`を使わない別ホストのcommit（取得・commit・pushを1手順で行う）。"""

    @property
    def path(self) -> str:
        return _entry(self.entry)

    @property
    def content(self) -> str:
        return _updated_content(self.entry, self.actor)


@dataclasses.dataclass(frozen=True)
class _Scenario:
    key: str
    name: str
    updates: tuple[_Update, ...]
    conflict: bool
    """真なら2つの主体が同じ項目を別の内容へ更新する（性質2）。偽なら全て別の項目（性質1）。"""


_SEPARATE_CLONES = _Scenario(
    "separate-clones",
    "別々のclone",
    (_Update("A", "x", 1), _Update("B", "y", 2), _Update("C", "z", 3, via_atk=False)),
    conflict=False,
)
_SHARED_CLONE = _Scenario(
    "shared-clone",
    "同じclone",
    (_Update("A", "x", 1), _Update("B", "x", 2), _Update("C", "z", 3, via_atk=False)),
    conflict=False,
)
_CONFLICT = _Scenario(
    "conflict",
    "同じ項目の競合",
    (_Update("A", "x", 1), _Update("B", "y", 1), _Update("C", "z", 3, via_atk=False)),
    conflict=True,
)
_CONFLICT_WITH_SHARED_CLONE = _Scenario(
    "conflict-shared-clone",
    "同じ項目の競合と同じcloneの別の更新",
    (_Update("A", "x", 1), _Update("B", "y", 1), _Update("D", "x", 4)),
    conflict=True,
)


@dataclasses.dataclass(frozen=True)
class _Op:
    resource: tuple[str, str]
    writes: bool
    label: str
    lock_file: str | None = None


def _independent(first: _Op, second: _Op) -> bool:
    return first.resource != second.resource or not (first.writes or second.writes)


def _subcommand(args: list[str]) -> str:
    words = iter(args)
    for word in words:
        if word in ("-c", "-C"):
            next(words, None)
        elif not word.startswith("-"):
            return word
    return ""


def _git_op(args: list[str], cwd: object) -> _Op | None:
    if args[:1] == ["rev-parse"] and any(word in ("--git-common-dir", "--git-path") for word in args):
        return None
    subcommand = _subcommand(args)
    label = "git " + " ".join(args)
    if subcommand in _REMOTE_READS:
        return _Op(_REMOTE, False, label)
    if subcommand in _REMOTE_WRITES:
        return _Op(_REMOTE, True, label)
    return _Op(("clone", str(pathlib.Path(str(cwd)).resolve())), True, label)


class _Aborted(BaseException):
    pass


@dataclasses.dataclass
class _Node:
    parked: dict[str, _Op]
    enabled: list[str]
    sleep: frozenset[str]
    chosen: str
    done: list[str] = dataclasses.field(default_factory=list)


class _Run:
    def __init__(
        self, shared: frozenset[tuple[str, str]], forced: list[str], initial_sleep: frozenset[str], remote: pathlib.Path
    ) -> None:
        self._shared = shared
        self.remote = remote
        self.remote_changed: dict[str, bool] = {}
        self._forced = forced
        self._initial_sleep = initial_sleep
        self._cv = threading.Condition()
        self._turn: str | None = None
        self._parked: dict[str, _Op] = {}
        self._names: dict[int, str] = {}
        self._holders: dict[str, str] = {}
        self._held: dict[str, set[tuple[str, str]]] = {}
        self._aborted = False
        self._sleep: frozenset[str] | None = initial_sleep if not forced else None
        self._choice_index = 0
        self._sleep_blocked = False
        self.new_nodes: list[_Node] = []
        self.trace: list[str] = []
        self.remote_writes: list[bool] = []
        """`trace`の各手順がremoteの参照を変えたか。"""
        self.unlocked: list[str] = []
        self.commits: dict[str, str] = {}
        """主体ごとの、更新をcommitした直後のmainのcommit。"""
        self.outcomes: dict[str, object] = {}

    def actor_name(self) -> str | None:
        return self._names.get(threading.get_ident())

    def before(self, op: _Op) -> None:
        name = self.actor_name()
        if name is None or op.resource not in self._shared:
            return
        if op.lock_file is None and op.resource != _REMOTE:
            # 同じcloneの他の主体は、操作の前に同じロックを待つ。保持者の操作の間に他の主体は割り込めないため止めない。
            if op.resource in self._held.get(name, set()):
                return
            self.unlocked.append(f"{name}: {op.label}")
        with self._cv:
            self._parked[name] = op
            self._turn = None
            self._cv.notify_all()
            while self._turn != name and not self._aborted:
                self._cv.wait()
            if self._aborted:
                raise _Aborted
            self.trace.append(f"{name}: {op.label}")
            self.remote_writes.append(False)

    def note_lock(self, lock_file: str, resource: tuple[str, str], *, held: bool) -> None:
        name = self.actor_name()
        if name is None:
            return
        with self._cv:
            if held:
                self._holders[lock_file] = name
                self._held.setdefault(name, set()).add(resource)
            else:
                self._holders.pop(lock_file, None)
                self._held.get(name, set()).discard(resource)

    def _enabled(self, name: str, op: _Op) -> bool:
        return op.lock_file is None or self._holders.get(op.lock_file, name) == name

    def _grant(self, name: str) -> None:
        with self._cv:
            self._parked.pop(name, None)
            self._turn = name
            self._cv.notify_all()
            deadline = time.monotonic() + _STEP_TIMEOUT_SECONDS
            while self._turn is not None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise AssertionError(
                        f"主体{name}が{_STEP_TIMEOUT_SECONDS:.0f}秒以内に次の手順へ止まらなかった: {self.trace}"
                    )
                self._cv.wait(remaining)

    def _actor_main(self, name: str, body: Callable[[], object]) -> None:
        self._names[threading.get_ident()] = name
        with self._cv:
            while self._turn != name and not self._aborted:
                self._cv.wait()
        try:
            if not self._aborted:
                self.outcomes[name] = body()
        except _Aborted:
            pass
        except Exception as error:  # noqa: BLE001  # 失敗の種類を性質の判定へ渡す
            self.outcomes[name] = error
        finally:
            with self._cv:
                self._turn = None
                self._cv.notify_all()

    def _choose(self, enabled: list[str], parked: dict[str, _Op]) -> str:
        if len(enabled) == 1:
            return enabled[0]
        index = self._choice_index
        self._choice_index += 1
        if index < len(self._forced):
            chosen = self._forced[index]
            assert chosen in enabled, f"再実行で選択点の状態が変わった（非決定的）: {chosen} not in {enabled}"
            return chosen
        assert self._sleep is not None
        candidates = [name for name in enabled if name not in self._sleep]
        if not candidates or self._sleep_blocked:
            self._sleep_blocked = True
            return enabled[0]
        self.new_nodes.append(_Node(dict(parked), list(enabled), self._sleep, candidates[0]))
        return candidates[0]

    def drive(self, bodies: Mapping[str, Callable[[], object]]) -> None:
        threads = [
            threading.Thread(target=self._actor_main, args=(name, body), name=f"actor-{name}", daemon=True)
            for name, body in bodies.items()
        ]
        for thread in threads:
            thread.start()
        try:
            for name in bodies:
                self._grant(name)
            while True:
                with self._cv:
                    parked = dict(self._parked)
                if not parked:
                    break
                enabled = [name for name, op in sorted(parked.items()) if self._enabled(name, op)]
                assert enabled, f"全ての主体がロック待ちで止まった: {parked}"
                chosen = self._choose(enabled, parked)
                self._grant(chosen)

                executed = parked[chosen]
                if chosen in self.remote_changed:
                    executed = dataclasses.replace(executed, writes=self.remote_changed.pop(chosen))
                if self._sleep is None:
                    if self._choice_index >= len(self._forced):
                        self._sleep = self._initial_sleep
                else:
                    self._sleep = frozenset(
                        name
                        for name in self._sleep
                        if name != chosen and name in parked and _independent(parked[name], executed)
                    )
        finally:
            with self._cv:
                self._aborted = True
                self._cv.notify_all()
            for thread in threads:
                thread.join(_STEP_TIMEOUT_SECONDS)


_CURRENT: list[_Run | None] = [None]
_LAUNCHES: list[float] = [0, 0.0]


@pytest.fixture
def scheduled_git(monkeypatch: pytest.MonkeyPatch) -> None:
    """製品コードのGitの起動とロックの取得を、探索中の実行の手順として止められるようにする。"""

    def timed(command: list[str], kwargs: dict[str, Any]) -> subprocess.CompletedProcess[bytes]:
        started = time.monotonic()
        result = subprocess.run(command, **kwargs)  # noqa: PLW1510  # pylint: disable=subprocess-run-check
        _LAUNCHES[0] += 1
        _LAUNCHES[1] += time.monotonic() - started
        return result

    def launch(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        run = _CURRENT[0]
        name = None if run is None else run.actor_name()
        if run is None or name is None:
            return timed(command, kwargs)
        op = _git_op(command[1:], kwargs.get("cwd"))
        if op is not None:
            run.before(op)
        if op is None or op.resource != _REMOTE:
            result = timed(command, kwargs)
        else:
            before = _ref(run.remote, "refs/heads/main")
            result = timed(command, kwargs)
            changed = _ref(run.remote, "refs/heads/main") != before
            run.remote_changed[name] = changed
            run.remote_writes[-1] = changed
        if result.returncode == 0 and _subcommand(command[1:]) == "commit":
            run.commits[name] = _ref(pathlib.Path(str(kwargs["cwd"])), "refs/heads/main") or ""
        return result

    real_acquire = git_sync._RepoLock.acquire  # pylint: disable=protected-access
    real_release = git_sync._RepoLock.release  # pylint: disable=protected-access

    def acquire(self: git_sync._RepoLock, *args: Any, **kwargs: Any) -> Any:  # pylint: disable=protected-access
        run = _CURRENT[0]
        first = not self.is_locked
        resource = ("clone", str(pathlib.Path(self._repo_path).resolve()))  # pylint: disable=protected-access
        if run is not None and first:
            run.before(_Op(resource, True, "repo_lockの取得", lock_file=self.lock_file))
        result = real_acquire(self, *args, **kwargs)
        if run is not None and first:
            run.note_lock(self.lock_file, resource, held=True)
        return result

    def release(self: git_sync._RepoLock, force: bool = False) -> None:  # pylint: disable=protected-access
        real_release(self, force)
        run = _CURRENT[0]
        if run is not None and not self.is_locked:
            resource = ("clone", str(pathlib.Path(self._repo_path).resolve()))  # pylint: disable=protected-access
            run.note_lock(self.lock_file, resource, held=False)

    monkeypatch.setenv("GIT_AUTHOR_DATE", "2026-11-19T00:00:00+00:00")
    monkeypatch.setenv("GIT_COMMITTER_DATE", "2026-11-19T00:00:00+00:00")
    monkeypatch.setattr(git_command, "_launch", launch)
    monkeypatch.setattr(git_sync._RepoLock, "acquire", acquire)  # pylint: disable=protected-access
    monkeypatch.setattr(git_sync._RepoLock, "release", release)  # pylint: disable=protected-access


class _Repositories:
    def __init__(self, root: pathlib.Path, scenario: _Scenario) -> None:
        self.work = root / "work"
        self._pristine = root / "pristine"
        self.remote = git_repository.init_bare_repository(self.work / "origin.git")
        seed = git_repository.init_repository(
            self.work / "seed",
            initial_branch="main",
            origin=str(self.remote),
            files={_entry(update.entry): _base_content(update.entry) for update in scenario.updates},
            commit_message="base",
        )
        git_repository.run_git(seed, "push", "-u", "origin", "main")
        self.clones = sorted({update.clone for update in scenario.updates})
        for clone in self.clones:
            git_repository.run_git(self.work, "clone", "--quiet", "--branch", "main", str(self.remote), clone)
        shutil.rmtree(seed)
        shutil.copytree(self.work, self._pristine, symlinks=True)

    def path(self, clone: str) -> pathlib.Path:
        return self.work / clone

    def reset(self) -> None:
        shutil.rmtree(self.work)
        shutil.copytree(self._pristine, self.work, symlinks=True)


def _remote_commit(repositories: _Repositories, run: _Run, update: _Update) -> str:
    run.before(_Op(_REMOTE, True, "別ホストのcommitとpush"))
    clone = repositories.path(update.clone)
    git_repository.run_git(clone, "pull", "--quiet", "--ff-only")
    (clone / update.path).write_text(update.content, encoding="utf-8")
    git_repository.run_git(clone, "commit", "--quiet", "-am", f"update by {update.actor}")
    git_repository.run_git(clone, "push", "--quiet", "origin", "main")
    run.remote_writes[-1] = True
    return "ok"


def _atk_update(repositories: _Repositories, run: _Run, update: _Update) -> str:
    """WIの状態遷移（`transitions.py`）と同じ排他区間で、1つの項目を書き換えてcommitとpushを行う。"""
    clone = repositories.path(update.clone)
    with wi_sync.DEFAULT_STORAGE.mutation(clone, push_pending=True) as mutation:
        run.before(_Op(("clone", str(clone.resolve())), True, f"{update.path}の書き換え"))
        (clone / update.path).write_text(update.content, encoding="utf-8")
        mutation.commit(f"chore: update by {update.actor}", [update.path])
    return "deferred" if git_sync.push_was_deferred(clone) else "ok"


def _shared_resources(repositories: _Repositories, scenario: _Scenario) -> frozenset[tuple[str, str]]:
    users: dict[str, int] = {}
    for update in scenario.updates:
        users[update.clone] = users.get(update.clone, 0) + 1
    clones = {("clone", str(repositories.path(clone).resolve())) for clone, count in users.items() if count > 1}
    return frozenset({_REMOTE, *clones})


def _ref(repository: pathlib.Path, name: str) -> str | None:
    git_dir = repository / ".git" if (repository / ".git").is_dir() else repository
    loose = git_dir / name
    if loose.is_file():
        return loose.read_text(encoding="utf-8").strip()
    packed = git_dir / "packed-refs"
    if packed.is_file():
        for line in packed.read_text(encoding="utf-8").splitlines():
            if line.endswith(" " + name):
                return line.split()[0]
    return None


def _blob_id(content: str) -> str:
    data = content.encode("utf-8")
    return hashlib.sha1(b"blob %d\0" % len(data) + data, usedforsecurity=False).hexdigest()


def _reachable_blobs(repository: pathlib.Path, ref: str) -> set[str]:
    output = git_repository.git_output(repository, "rev-list", "--objects", ref)
    return {line.split()[0] for line in output.splitlines()}


def _outcome_name(outcome: object) -> str:
    return type(outcome).__name__ if isinstance(outcome, BaseException) else str(outcome)


@dataclasses.dataclass
class _Verdict:
    trace: list[str]
    outcomes: dict[str, str]
    end_state: str = ""
    content_conflict: bool = False
    """2つ以上の主体が、同じ項目を他の主体の更新を見ずに変更前の本文から書き換えてcommitしたか。"""
    violations: dict[str, list[str]] = dataclasses.field(default_factory=dict)

    def violate(self, prop: str, message: str) -> None:
        self.violations.setdefault(prop, []).append(message)


def _judge(repositories: _Repositories, scenario: _Scenario, run: _Run) -> _Verdict:
    """全ての主体が終わった状態と、その後の同期の結果を性質と照らす。

    受理した更新は、主体のcommitが成功した更新（`atk`を使わない別ホストは操作が終わった更新）とする。
    受理する前に失敗を返した操作は、失敗が呼び出し元へ届くため、更新が残らないことだけを確かめる。
    """
    verdict = _Verdict(list(run.trace), {name: _outcome_name(value) for name, value in run.outcomes.items()})
    for step in run.unlocked:
        verdict.violate("共有cloneの操作はロックの内側", step)
    accepted = {
        update.actor
        for update in scenario.updates
        if update.actor in run.commits or (not update.via_atk and verdict.outcomes.get(update.actor) == "ok")
    }
    remote_blobs = _reachable_blobs(repositories.remote, "main")
    local_blobs = {clone: _reachable_blobs(repositories.path(clone), "refs/heads/main") for clone in repositories.clones}
    rebasing = {clone for clone in repositories.clones if git_sync.is_rebase_in_progress(repositories.path(clone))}
    verdict.end_state = (
        ", ".join(f"{name}={verdict.outcomes.get(name, '未終了')}" for name in sorted(verdict.outcomes))
        + f"; rebaseの途中: {','.join(sorted(rebasing)) or 'なし'}"
    )
    for update in scenario.updates:
        outcome = verdict.outcomes.get(update.actor)
        blob = _blob_id(update.content)
        if outcome is None:
            verdict.violate("終了", f"{update.actor}が終わらなかった")
        elif update.actor not in accepted:
            if outcome in ("ok", "deferred"):
                verdict.violate("成功は受理を伴う", f"{update.actor}は{outcome}で終わったがcommitしていない")
            if (repositories.path(update.clone) / update.path).read_text(encoding="utf-8") == update.content:
                verdict.violate(
                    "受理前の失敗は変更を残さない", f"{update.actor}は{outcome}で終わったが本文が書き換わったまま残る"
                )
        else:
            if blob not in remote_blobs and blob not in local_blobs[update.clone]:
                verdict.violate("受理した更新を失わない", f"{update.actor}の更新がremoteにも{update.clone}のmainにも無い")
            if outcome == "ok" and blob not in remote_blobs:
                verdict.violate("成功した更新はremoteにある", f"{update.actor}は成功で終わったが更新がremoteに無い")
    for update in scenario.updates:
        outcome_value = run.outcomes.get(update.actor)
        if not update.via_atk or isinstance(outcome_value, str):
            continue
        if isinstance(outcome_value, git_sync.RebaseInProgressError):
            # 性質2の拒否。原因の競合はこの主体の操作より前に起きうるため、操作の間の競合を求めない。
            if update.clone not in rebasing:
                verdict.violate("拒否はrebaseの途中に限る", f"{update.actor}は{update.clone}がrebaseの途中でない時に拒否された")
            continue
        own = [index for index, step in enumerate(run.trace) if step.startswith(f"{update.actor}: ")]
        contended = bool(own) and any(
            written and not run.trace[index].startswith(f"{update.actor}: ")
            for index, written in enumerate(run.remote_writes)
            if own[0] <= index <= own[-1]
        )
        if not contended:
            verdict.violate("競合が無い操作は成功する", f"{update.actor}は他の主体がremoteを変えていない間に失敗した")
    for clone in sorted(rebasing):
        if not any(
            update.clone == clone and verdict.outcomes.get(update.actor) == "CalledProcessError" for update in scenario.updates
        ):
            verdict.violate("rebaseの停止は報告される", f"{clone}がrebaseの途中だが、失敗を返した主体が無い")
    if not scenario.conflict:
        refused = sorted(update.actor for update in scenario.updates if update.actor not in accepted)
        unpushed = sorted(
            update.actor
            for update in scenario.updates
            if update.actor in accepted and _blob_id(update.content) not in remote_blobs
        )
        if refused or unpushed or rebasing:
            verdict.violate(
                "性質1（操作の終了時）",
                f"受理前に失敗: {refused}、受理したがremoteに無い: {unpushed}、rebaseの途中: {sorted(rebasing)}",
            )
    unsynced = {clone for clone in repositories.clones if clone in rebasing or local_blobs[clone] - remote_blobs}
    _follow_up(repositories, scenario, unsynced, verdict)
    remote_blobs = _reachable_blobs(repositories.remote, "main")
    for update in scenario.updates:
        if update.actor not in accepted:
            continue
        blob = _blob_id(update.content)
        if blob not in remote_blobs and blob not in _reachable_blobs(repositories.path(update.clone), "refs/heads/main"):
            verdict.violate("受理した更新を失わない（後続の同期の後）", f"{update.actor}の更新が失われた")
        elif not scenario.conflict and blob not in remote_blobs:
            verdict.violate("性質1（後続の同期の後）", f"受理した{update.actor}の更新がremoteに無い")
    _judge_content_conflict(repositories, scenario, run, verdict)
    return verdict


def _judge_content_conflict(repositories: _Repositories, scenario: _Scenario, run: _Run, verdict: _Verdict) -> None:
    """同じ項目を互いの更新を見ずに書き換えた2つの更新は、後続の同期の後に、片方のcloneが競合を保ったrebaseの途中で止まる。

    受理した更新の到達だけを見ると、後から統合する側が競合を自動で解決して先の更新を上書きしても、
    先の更新が履歴に残るため違反にならない。そのため、更新のcommitの親の本文から内容の衝突を判定し、停止そのものを確かめる。
    """
    blind = [
        update
        for update in scenario.updates
        if update.actor in run.commits
        and git_repository.git_output(repositories.path(update.clone), "show", f"{run.commits[update.actor]}^:{update.path}")
        == _base_content(update.entry).strip()
    ]
    for entry in sorted({update.entry for update in blind}):
        rivals = [update for update in blind if update.entry == entry]
        if len(rivals) < 2:
            continue
        verdict.content_conflict = True
        if not any(
            git_sync.is_rebase_in_progress(repositories.path(update.clone))
            and git_repository.git_output(repositories.path(update.clone), "ls-files", "--unmerged", "--", update.path)
            for update in rivals
        ):
            verdict.violate(
                "性質2（内容の衝突で止まる）",
                f"{'と'.join(update.actor for update in rivals)}が項目{entry}を互いの更新を見ずに書き換えたが、"
                "後続の同期の後に競合を保ったrebaseの途中で止まったcloneが無い",
            )


def _tracked_bytes(clone: pathlib.Path) -> tuple[str, dict[str, bytes]]:
    head = git_repository.git_output(clone, "rev-parse", "HEAD")
    files = {str(path.relative_to(clone)): path.read_bytes() for path in sorted((clone / _ENTRY_DIRECTORY).glob("*.md"))}
    return head, files


def _follow_up(repositories: _Repositories, scenario: _Scenario, unsynced: set[str], verdict: _Verdict) -> None:
    """次の`atk`操作に当たる同期を、他の主体が動かない状態で、未同期のcloneへ1回ずつ行う。

    rebaseの途中のcloneと、同期で競合して止まったcloneでは、pullとpushが拒否してHEADと本文を変えないことを確かめる。
    """
    for clone in sorted(unsynced):
        path = repositories.path(clone)
        with wi_sync.repo_lock(path):
            if not git_sync.is_rebase_in_progress(path):
                try:
                    wi_sync.push_pending_commits(path)
                    wi_sync.pull(path)
                except subprocess.CalledProcessError as error:
                    if not (scenario.conflict and git_sync.is_rebase_in_progress(path)):
                        verdict.violate("後続の同期", f"{clone}の同期が{error!r}")
                except Exception as error:  # noqa: BLE001
                    verdict.violate("後続の同期", f"{clone}の同期が{error!r}")
            if not git_sync.is_rebase_in_progress(path):
                continue
            before = _tracked_bytes(path)
            for operation in (wi_sync.pull, wi_sync.push_pending_commits):
                try:
                    operation(path)
                except git_sync.RebaseInProgressError:
                    pass
                except Exception as error:  # noqa: BLE001
                    verdict.violate("性質2（rebaseの途中は拒否）", f"{clone}の{operation.__name__}が{error!r}")
                else:
                    verdict.violate("性質2（rebaseの途中は拒否）", f"{clone}の{operation.__name__}が拒否しなかった")
                if _tracked_bytes(path) != before:
                    verdict.violate("性質2（rebaseの途中は拒否）", f"{clone}の{operation.__name__}がHEADか本文を変えた")


@dataclasses.dataclass
class _Exploration:
    scenario: _Scenario
    schedules: int = 0
    choice_points: int = 0
    seconds: float = 0.0
    truncated: bool = False
    verdicts: list[_Verdict] = dataclasses.field(default_factory=list)

    def counterexamples(self, prop: str) -> list[_Verdict]:
        return [verdict for verdict in self.verdicts if prop in verdict.violations]

    def violated(self) -> set[str]:
        return {prop for verdict in self.verdicts for prop in verdict.violations}


def _schedules(repositories: _Repositories, scenario: _Scenario) -> Iterator[_Run]:
    shared = _shared_resources(repositories, scenario)
    stack: list[_Node] = []
    forced: list[str] = []
    initial_sleep: frozenset[str] = frozenset()
    while True:
        repositories.reset()
        run = _Run(shared, forced, initial_sleep, repositories.remote)
        bodies = {
            update.actor: functools.partial(_atk_update if update.via_atk else _remote_commit, repositories, run, update)
            for update in scenario.updates
        }
        _CURRENT[0] = run
        try:
            run.drive(bodies)
        finally:
            _CURRENT[0] = None
        for node in run.new_nodes:
            node.done.append(node.chosen)
            stack.append(node)
        yield run
        node, chosen = None, None
        while stack:
            node = stack[-1]
            chosen = next((name for name in node.enabled if name not in node.sleep and name not in node.done), None)
            if chosen is not None:
                break
            stack.pop()
        if node is None or chosen is None:
            return
        initial_sleep = frozenset(
            name
            for name in (*node.sleep, *node.done)
            if name in node.parked and _independent(node.parked[name], node.parked[chosen])
        )
        node.done.append(chosen)
        node.chosen = chosen
        forced = [entry.chosen for entry in stack]


def _explore(root: pathlib.Path, scenario: _Scenario, *, stop_on_violation: bool = False) -> _Exploration:
    started = time.monotonic()
    _LAUNCHES[:] = [0, 0.0]
    repositories = _Repositories(root, scenario)
    exploration = _Exploration(scenario)
    for run in _schedules(repositories, scenario):
        exploration.schedules += 1
        exploration.choice_points += len(run.new_nodes)
        verdict = _judge(repositories, scenario, run)
        exploration.verdicts.append(verdict)
        if exploration.schedules % 20 == 0:
            print(
                f"{scenario.key}: {exploration.schedules}通り、{time.monotonic() - started:.0f}秒", file=sys.stderr, flush=True
            )
        if stop_on_violation and set(verdict.violations) - _KNOWN_FINDINGS.get(scenario.key, set()):
            exploration.truncated = True
            break
    exploration.seconds = time.monotonic() - started
    return exploration


def _summary(exploration: _Exploration) -> str:
    lines = [
        f"{exploration.scenario.name}: 順序{exploration.schedules}通り"
        f"{'（違反で打ち切り）' if exploration.truncated else ''}、{exploration.seconds:.1f}秒"
        f"（製品コードのGit起動{_LAUNCHES[0]:.0f}回・{_LAUNCHES[1]:.1f}秒）、"
        f"内容が衝突した順序{sum(verdict.content_conflict for verdict in exploration.verdicts)}通り、"
        f"違反した性質: {sorted(exploration.violated()) or 'なし'}"
    ]
    states: dict[str, int] = {}
    for verdict in exploration.verdicts:
        states[verdict.end_state] = states.get(verdict.end_state, 0) + 1
    lines.extend(f"  終了状態 {count}通り: {state}" for state, count in sorted(states.items(), key=lambda item: -item[1]))
    for prop in sorted(exploration.violated()):
        groups: dict[str, list[_Verdict]] = {}
        for verdict in exploration.counterexamples(prop):
            groups.setdefault("; ".join(verdict.violations[prop]), []).append(verdict)
        for message, verdicts in groups.items():
            lines.append(f"- {prop}（{len(verdicts)}通り）: {message}")
            lines.append(f"  結果: {verdicts[0].outcomes}")
            lines.extend(f"  {step}" for step in verdicts[0].trace)
    return "\n".join(lines)


_SCENARIOS = (_SEPARATE_CLONES, _SHARED_CLONE, _CONFLICT, _CONFLICT_WITH_SHARED_CLONE)
_KNOWN_FINDINGS = {_SEPARATE_CLONES.key: {"性質1（操作の終了時）"}}
"""現在のコードで反例が見つかっている性質。内容は`.claude/skills/git-sync-interleavings/SKILL.md`「既知の反例」にある。"""

_MODE = os.environ.get("AGENT_TOOLKIT_GIT_SYNC_INTERLEAVINGS", "")
_EXPLORATIONS: dict[str, _Exploration] = {}

pytestmark = pytest.mark.skipif(
    _MODE not in ("all", "first"),
    reason="全ての順序を実Gitで実行する重い検査のため通常の試験から外す。実行方法は.claude/skills/git-sync-interleavings/SKILL.md",
)


def _explored(tmp_path: pathlib.Path, scenario: _Scenario) -> _Exploration:
    if scenario.key not in _EXPLORATIONS:
        exploration = _explore(tmp_path, scenario, stop_on_violation=_MODE == "first")
        print(_summary(exploration))
        _EXPLORATIONS[scenario.key] = exploration
    return _EXPLORATIONS[scenario.key]


@pytest.mark.usefixtures("scheduled_git")
@pytest.mark.parametrize("scenario", _SCENARIOS, ids=lambda s: s.key)
def test_every_interleaving_keeps_sync_contract(tmp_path: pathlib.Path, scenario: _Scenario) -> None:
    """全ての順序で、受理した更新を失わず、内容が衝突すれば競合を保って止まり、rebaseの途中では拒否し、失敗は競合した場合だけに限る。"""
    exploration = _explored(tmp_path, scenario)
    unexpected = exploration.violated() - _KNOWN_FINDINGS.get(scenario.key, set())
    assert not unexpected, _summary(exploration)
    if scenario.conflict:
        assert any(verdict.content_conflict for verdict in exploration.verdicts), (
            "内容が衝突する順序を1つも実行しなかったため、性質2（内容の衝突で止まる）を確かめていない\n" + _summary(exploration)
        )


@pytest.mark.xfail(
    strict=True,
    reason=(
        "既知の反例: 別ホストのpushが取得とpushの間に入ると、操作は失敗を返し、更新は操作の終了時にremoteへ届かない"
        "（.claude/skills/git-sync-interleavings/SKILL.md「既知の反例」）"
    ),
)
@pytest.mark.usefixtures("scheduled_git")
def test_separate_clones_publish_every_update_by_operation_end(tmp_path: pathlib.Path) -> None:
    exploration = _explored(tmp_path, _SEPARATE_CLONES)
    assert "性質1（操作の終了時）" not in exploration.violated(), _summary(exploration)
