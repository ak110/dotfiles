"""ローカル保存を妨げずにprivate-notesの送受信を行う。  pyfltr: allow-comment"""

from __future__ import annotations

import hashlib
import json
import os
import pathlib
import subprocess
import sys
import threading
import time
import uuid

import filelock

from agent_toolkit._atk import git_sync, outcome
from agent_toolkit._atk.wi import sync
from agent_toolkit._common import state_paths
from agent_toolkit._common.atomic_file import atomic_write
from agent_toolkit._common.windows_job import HIDDEN_DETACHED_CREATIONFLAGS
from agent_toolkit._git import command as git


def _files(notes: pathlib.Path) -> tuple[pathlib.Path, pathlib.Path]:
    key = hashlib.sha256(os.fsencode(notes.resolve())).hexdigest()
    root = state_paths.state_dir() / "wi-sync"
    return root / f"{key}.lock", root / f"{key}.json"


def read_status(notes: pathlib.Path) -> dict:
    _, path = _files(notes)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except OSError, ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def _report(path: pathlib.Path, state: str, *, last_fetch: float | None, reason: str | None = None) -> None:
    atomic_write(path, json.dumps({"state": state, "at": time.time(), "last_fetch": last_fetch, "reason": reason}))


def run(notes: pathlib.Path) -> bool | None:
    if not sync.has_remote(notes):
        return False
    lock, path = _files(notes)
    path.parent.mkdir(parents=True, exist_ok=True)
    request = lock.with_suffix(".request")
    while True:
        try:
            with filelock.FileLock(lock, timeout=0):
                generation = _generation(request)
                for _ in range(3):
                    if not _synchronize(notes, path):
                        break
        except filelock.Timeout:
            return None
        if _generation(request) == generation:
            break
    return read_status(notes).get("state") == "synced"


def _generation(request: pathlib.Path) -> str | None:
    try:
        return request.read_text(encoding="ascii")
    except FileNotFoundError:
        return None


def _synchronize(notes: pathlib.Path, status: pathlib.Path) -> bool:
    if not sync.has_remote(notes):
        return False
    last_fetch = read_status(notes).get("last_fetch")
    try:
        with sync.repo_lock(notes):
            sync.ensure_mutation_allowed(notes)
            remote, ref, tracking = _upstream_refs(notes)
            before = git.output(["rev-parse", tracking], notes)
        # 通信中に通常の追跡refを更新すると、別の操作が同じロック下で読んだ同期根拠を書き換えてしまう。  # pyfltr: allow-comment
        fetched_ref = "refs/agent-toolkit/wi-sync/fetched"
        git.run(
            ["fetch", "--no-tags", "--no-write-fetch-head", "--refmap=", "--", remote, f"+{ref}:{fetched_ref}"],
            cwd=notes,
            check=True,
            capture_output=True,
        )
        with sync.repo_lock(notes):
            sync.ensure_mutation_allowed(notes)
            if _upstream_refs(notes) != (remote, ref, tracking) or git.output(["rev-parse", tracking], notes) != before:
                _report(status, "pending", last_fetch=last_fetch)
                return True
            upstream = git.output(["rev-parse", fetched_ref], notes)
            git.run(["update-ref", tracking, upstream, before], cwd=notes, check=True, capture_output=True)
            ahead = git.run(["merge-base", "--is-ancestor", upstream, "HEAD"], cwd=notes, check=False)
            if ahead.returncode == 1:
                behind = git.run(["merge-base", "--is-ancestor", "HEAD", upstream], cwd=notes, check=False)
                if behind.returncode == 0:
                    git.run(["merge", "--ff-only", upstream], cwd=notes, check=True, capture_output=True)
                elif behind.returncode == 1:
                    if git_sync.is_worktree_dirty(notes):
                        _report(
                            status,
                            "recovery-required",
                            last_fetch=last_fetch,
                            reason="未保存の変更があるため履歴の分岐を解消できない",
                        )
                        return False
                    try:
                        git.run(
                            ["-c", "submodule.recurse=false", "rebase", upstream], cwd=notes, check=True, capture_output=True
                        )
                    except subprocess.CalledProcessError:
                        _report(
                            status, "recovery-required", last_fetch=last_fetch, reason="private-notesのrebaseを完了できない"
                        )
                        return False
                else:
                    raise RuntimeError("private-notesの先行commitを判定できない")
            elif ahead.returncode != 0:
                raise RuntimeError("private-notesのupstreamを解決できない")
            last_fetch = time.time()
            with sync.local_save(True):
                sync.migrate_legacy_reservations(notes)
            head = git.output(["rev-parse", "HEAD"], notes)
        if head != upstream:
            # 送信中にHEADが進んでも、排他区間で取得したcommitだけを送る。  # pyfltr: allow-comment
            try:
                git.run(["push", "--", remote, f"{head}:{ref}"], cwd=notes, check=True, capture_output=True)
            except subprocess.CalledProcessError as error:
                _report(status, "failed", last_fetch=last_fetch, reason=f"送信に失敗した（終了コード {error.returncode}）")
                return True
        with sync.repo_lock(notes):
            current = git.output(["rev-parse", "HEAD"], notes)
        pending = current != head
        _report(status, "pending" if pending else "synced", last_fetch=last_fetch)
        return pending
    except git_sync.RebaseInProgressError as error:
        _report(status, "recovery-required", last_fetch=last_fetch, reason=str(error))
    except (OSError, RuntimeError, subprocess.SubprocessError) as error:
        _report(status, "failed", last_fetch=last_fetch, reason=str(error))
    return False


def _upstream_refs(notes: pathlib.Path) -> tuple[str, str, str]:
    refspec = git.output(
        [
            "for-each-ref",
            "--format=%(upstream:remotename)%09%(upstream:remoteref)%09%(upstream)",
            git.output(["symbolic-ref", "HEAD"], notes),
        ],
        notes,
    ).split("\t")
    if (
        len(refspec) != 3
        or not refspec[0]
        or not refspec[1].startswith("refs/heads/")
        or not refspec[2].startswith("refs/remotes/")
    ):
        raise RuntimeError("private-notesの送受信先を解決できない")
    return refspec[0], refspec[1], refspec[2]


def prepare_read(notes: pathlib.Path, *, skip: bool, force: bool) -> None:
    warn_if_unhealthy(notes)
    if skip:
        return
    if force:
        with sync.repo_lock(notes):
            sync.pull_with_recent_reuse(notes, force_pull=True)
        return
    if not sync.has_remote(notes):
        return
    last_fetch = read_status(notes).get("last_fetch")
    if isinstance(last_fetch, (int, float)) and time.time() - last_fetch < sync.PULL_MIN_INTERVAL_SECONDS:
        return
    if last_fetch is None and sync.pulled_recently(notes):
        return
    observed = (
        time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(last_fetch)) if isinstance(last_fetch, (int, float)) else "未取得"
    )
    try:
        schedule(notes)
    except OSError as error:
        outcome.report_warning(
            f"private-notesの裏の取得を起動できなかった: {error}", next_action="最新の一覧が必要なら --pull を指定する"
        )
    outcome.report_warning(
        f"ローカルのWIを表示するため、結果が古い可能性がある。裏の最終取得: {observed}",
        next_action="最新の一覧が必要なら --pull を指定する",
    )


def warn_if_unhealthy(notes: pathlib.Path) -> bool:
    status = read_status(notes)
    if status.get("state") not in {"failed", "recovery-required"}:
        return False
    timestamp = status.get("at")
    when = time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(timestamp)) if isinstance(timestamp, (int, float)) else "不明"
    outcome.report_warning(
        f"以前のprivate-notes同期が未回復（{when}）: {status.get('reason') or '原因不明'}",
        next_action=f"git -C {notes} statusで原因を確認し、解消後に atk wi pull または atk wi commit を実行してください",
    )
    return True


def finish_local_save(notes: pathlib.Path, exit_code: int, *, skip: bool) -> int:
    if exit_code:
        return exit_code
    unhealthy = warn_if_unhealthy(notes)
    if not skip and sync.has_remote(notes):
        try:
            schedule(notes)
        except OSError as error:
            outcome.report_warning(
                f"ローカル保存は完了したが、裏の同期を起動できなかった: {error}",
                next_action="atk wi commitで同期する",
            )
            return 3
    return 3 if unhealthy else 0


def explicit_sync_finished(notes: pathlib.Path) -> None:
    if not sync.has_remote(notes):
        return
    _, status = _files(notes)
    status.parent.mkdir(parents=True, exist_ok=True)
    ahead = git_sync.pending_commit_count(notes)
    if ahead == 0:
        _report(status, "synced", last_fetch=time.time())
    elif isinstance(ahead, int):
        previous = read_status(notes)
        _report(status, previous.get("state") or "pending", last_fetch=time.time(), reason=previous.get("reason"))


def schedule(notes: pathlib.Path) -> None:
    if not sync.has_remote(notes):
        return
    lock, _ = _files(notes)
    lock.parent.mkdir(parents=True, exist_ok=True)
    # 稼働中のworkerに追送を伝え、最後の送信と親の新しいcommitが交差しても次の取得を行う。  # pyfltr: allow-comment
    atomic_write(lock.with_suffix(".request"), uuid.uuid4().hex)
    try:
        with filelock.FileLock(lock, timeout=0):
            pass
    except filelock.Timeout:
        return
    environment = os.environ.copy()
    root = str(pathlib.Path(__file__).resolve().parents[3])
    previous = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = root + (os.pathsep + previous if previous else "")
    worker = subprocess.Popen(  # noqa: SIM115  # pylint: disable=consider-using-with
        [sys.executable, "-P", "-m", __name__, str(notes.resolve())],
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=os.name != "nt",
        creationflags=HIDDEN_DETACHED_CREATIONFLAGS if os.name == "nt" else 0,
    )
    threading.Thread(target=worker.wait, daemon=True).start()


if __name__ == "__main__":
    run(pathlib.Path(sys.argv[1]))
