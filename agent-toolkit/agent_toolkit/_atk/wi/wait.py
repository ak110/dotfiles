import argparse
import json
import math
import subprocess
import sys
import time
from pathlib import Path

from agent_toolkit._atk import outcome
from agent_toolkit._atk.wi import background_sync, entries, snapshot, uwi_scan
from agent_toolkit._atk.wi.constants import WI_PROCESSABLE_STATES, WI_STATES, WI_TYPE_AWI, WI_TYPE_UWI
from agent_toolkit._common.next_action import ActionableError
from agent_toolkit._git import command, remote

_POLL_SECONDS = 1.0
_SYNC_SECONDS = 60.0


def index_path(notes: Path) -> Path:
    result = command.run(["rev-parse", "--git-path", "index"], cwd=notes, check=True, capture_output=True, text=True)
    return notes / result.stdout.rstrip("\r\n")


def index_stamp(path: Path) -> str | None:
    """目印を取得できない場合はNoneを返す。前回と同じ目印としては扱わない。  pyfltr: allow-comment"""
    try:
        stat = path.stat()
    except OSError:
        return None
    return f"{stat.st_ino}:{stat.st_size}:{stat.st_mtime_ns}:{stat.st_ctime_ns}"


def prepare_args(args: argparse.Namespace) -> None:
    if not math.isfinite(args.timeout) or args.timeout < 0:
        args.subparser.error("--timeoutには有限の0以上の秒数を指定してください。")
    if not args.uwi and args.new_since is None:
        args.subparser.error("--uwiか--new-sinceを指定してください。")
    if (args.new_since is None) != (args.target_repo is None):
        args.subparser.error("--new-sinceと--target-repoは組み合わせて指定してください。")
    if args.target_repo is not None:
        try:
            args.target_repo = remote.normalize_remote_url(args.target_repo)
        except ValueError as error:
            args.subparser.error(str(error))
    for name in args.uwi:
        if not name.endswith(".md") or name == ".md" or any(char in name for char in "/\\:\0"):
            args.subparser.error(f"--uwiにはパスを含まない正式ファイル名を指定してください: {name!r}")


def _matches(
    notes: Path, names: set[str], target_repo: str | None, baseline: frozenset[snapshot.Identifier]
) -> list[tuple[str, str]]:
    rows = tuple(entries.iter_entries(notes, WI_STATES, None))
    matches = {
        (WI_TYPE_UWI, path.name)
        for path, _, body, _, kind in rows
        if kind == WI_TYPE_UWI and path.name in names and uwi_scan.is_uwi_answered(body)
    }
    if target_repo is not None:
        cache: dict[str, str | None] = {}
        matches.update(
            (WI_TYPE_AWI, path.name)
            for path, repo, _, state, kind in snapshot.new_entries(rows, baseline)
            if kind == WI_TYPE_AWI and state in WI_PROCESSABLE_STATES and entries.canonical_repo(repo, cache) == target_repo
        )
    return sorted(matches)


def cmd_wait(args: argparse.Namespace, notes: Path) -> int:
    try:
        baseline = snapshot.read(args.new_since) if args.new_since is not None else frozenset()
    except ActionableError as error:
        outcome.report_failure(str(error), next_action=error.next_action)
        return 2

    deadline = time.monotonic() + args.timeout
    names = set(args.uwi)
    try:
        index = index_path(notes)
        previous = None
        next_sync = 0.0
        while True:
            stamp = index_stamp(index)
            if stamp is None:
                raise OSError(f"Git indexの変更目印を取得できない: {index}")
            if stamp != previous:
                matches = _matches(notes, names, args.target_repo, baseline)
                if matches:
                    kind, name = matches[0]
                    print(json.dumps({"type": kind, "filename": name}, ensure_ascii=False))
                    return 0
                previous = stamp
            now = time.monotonic()
            if now >= deadline:
                print("WIの待機時間が終了した（条件に一致する項目は未検知）。", file=sys.stderr)
                return 3
            if now >= next_sync:
                background_sync.schedule(notes)
                next_sync = now + _SYNC_SECONDS
            remaining = deadline - time.monotonic()
            if remaining > 0:
                time.sleep(min(_POLL_SECONDS, remaining))
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        outcome.report_failure(str(error), next_action="private-notesの状態を確認し、原因を解消して再実行する")
        return 1
