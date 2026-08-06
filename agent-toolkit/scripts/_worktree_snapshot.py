#!/usr/bin/env -S uv run --no-project --script
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Git作業ツリーを復旧可能な形で退避し、比較結果をJSONで返す。"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import shlex
import stat
import subprocess
import sys
import typing

_MANIFEST_NAME = "manifest.json"
_INDEX_PATCH_NAME = "index.patch"
_WORKTREE_PATCH_NAME = "worktree.patch"
_BLOBS_DIR_NAME = "blobs"
_GIT_TIMEOUT_SECONDS = 30


class SnapshotError(Exception):
    """利用者が修正できる取得・形式エラー。"""


class SnapshotState(typing.NamedTuple):
    """同一時点として比較するGit作業ツリーの状態。"""

    head: str
    index_patch: bytes
    worktree_patch: bytes
    index_paths: list[str]
    worktree_paths: list[str]
    untracked: dict[str, dict[str, typing.Any]]
    common_dir: str
    worktrees: list[dict[str, typing.Any]]


def main() -> None:
    """CLI引数を解釈して退避または比較を実行する。"""
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    capture_parser = subparsers.add_parser("capture", help="作業ツリー状態を退避する")
    capture_parser.add_argument("--repo", required=True, type=pathlib.Path)
    capture_parser.add_argument("--output-dir", required=True, type=pathlib.Path)

    compare_parser = subparsers.add_parser("compare", help="退避時点と現在の状態を比較してJSONで返す")
    compare_parser.add_argument("--repo", required=True, type=pathlib.Path)
    # `capture`と同じ引数名でも受理する。取得時と同じ名前で比較を起動した際の失敗を防ぐ。
    compare_parser.add_argument(
        "--snapshot-dir",
        "--output-dir",
        required=True,
        type=pathlib.Path,
        dest="snapshot_dir",
        metavar="SNAPSHOT_DIR",
        help="退避先ディレクトリ（`--output-dir`でも指定できる）",
    )

    args = parser.parse_args()
    try:
        repo = _resolve_repo(args.repo)
        if args.command == "capture":
            _capture(repo, args.output_dir)
            sys.exit(0)
        if args.command == "compare":
            changed = _compare(repo, args.snapshot_dir)
            sys.exit(1 if changed else 0)
        raise SnapshotError(f"未対応のサブコマンド: {args.command}")
    except SnapshotError as error:
        print(f"error: {error}", file=sys.stderr)
        sys.exit(2)


def _capture(repo: pathlib.Path, output_dir_arg: pathlib.Path) -> None:
    output_dir = _validate_new_snapshot_dir(repo, output_dir_arg)
    initial_state = _snapshot_state(repo)
    confirmed_state = _snapshot_state(repo)
    if initial_state != confirmed_state:
        raise SnapshotError("退避対象が取得中に変化したため、同一時点の状態を確定できない")

    head, index_patch, worktree_patch, index_paths, worktree_paths, untracked, common_dir, worktrees = confirmed_state
    output_dir.mkdir(mode=0o700)
    blobs_dir = output_dir / _BLOBS_DIR_NAME
    blobs_dir.mkdir(mode=0o700)
    _write_private(output_dir / _INDEX_PATCH_NAME, index_patch)
    _write_private(output_dir / _WORKTREE_PATCH_NAME, worktree_patch)
    captured_untracked = _capture_untracked(repo, blobs_dir, untracked)
    if _snapshot_state(repo) != confirmed_state:
        raise SnapshotError("退避対象が実体保存中に変化したため、同一時点の状態を確定できない")
    manifest = {
        "format_version": 3,
        "repo": str(repo),
        "common_dir": common_dir,
        "head": head,
        "index_patch_sha256": hashlib.sha256(index_patch).hexdigest(),
        "worktree_patch_sha256": hashlib.sha256(worktree_patch).hexdigest(),
        "index_paths": index_paths,
        "worktree_paths": worktree_paths,
        "untracked": captured_untracked,
        "worktrees": worktrees,
    }
    manifest_bytes = (json.dumps(manifest, ensure_ascii=True, indent=2, sort_keys=True) + "\n").encode()
    _write_private(output_dir / _MANIFEST_NAME, manifest_bytes)


def _compare(repo: pathlib.Path, snapshot_dir_arg: pathlib.Path) -> bool:
    snapshot_dir = _resolve_existing_dir(snapshot_dir_arg, "退避ディレクトリ")
    manifest = _load_manifest(snapshot_dir)
    if manifest["repo"] != str(repo):
        raise SnapshotError(f"退避対象リポジトリが一致しない: expected={manifest['repo']}, actual={repo}")

    index_patch = _load_snapshot_patch(snapshot_dir, _INDEX_PATCH_NAME, manifest["index_patch_sha256"], "index")
    worktree_patch = _load_snapshot_patch(
        snapshot_dir,
        _WORKTREE_PATCH_NAME,
        manifest["worktree_patch_sha256"],
        "未ステージ",
    )

    initial_state = _snapshot_state(repo)
    confirmed_state = _snapshot_state(repo)
    if initial_state != confirmed_state:
        raise SnapshotError("比較対象が取得中に変化したため、同一時点の状態を確定できない")
    (
        current_head,
        current_index_patch,
        current_worktree_patch,
        current_index_paths,
        current_worktree_paths,
        current_untracked,
        current_common_dir,
        current_worktrees,
    ) = confirmed_state
    if current_common_dir != manifest["common_dir"]:
        raise SnapshotError(f"Git共通ディレクトリが一致しない: expected={manifest['common_dir']}, actual={current_common_dir}")

    head_relation = _head_relation(repo, manifest["head"], current_head)
    head_changed = head_relation != "same"
    head_changed_paths = _git_paths(repo, "diff", "--name-only", "-z", manifest["head"], current_head) if head_changed else []
    index_changed = current_index_patch != index_patch
    worktree_changed = current_worktree_patch != worktree_patch
    tracked_changed = index_changed or worktree_changed
    baseline_untracked = {
        entry["path"]: {key: value for key, value in entry.items() if key != "blob"} for entry in manifest["untracked"]
    }
    untracked_added = sorted(set(current_untracked) - set(baseline_untracked))
    untracked_removed = sorted(set(baseline_untracked) - set(current_untracked))
    untracked_modified = sorted(
        path for path in set(baseline_untracked) & set(current_untracked) if baseline_untracked[path] != current_untracked[path]
    )
    changed_untracked = sorted(set(untracked_added) | set(untracked_removed) | set(untracked_modified))
    baseline_worktrees = {entry["path"]: entry for entry in manifest["worktrees"]}
    current_worktrees_by_path = {entry["path"]: entry for entry in current_worktrees}
    worktrees_added = sorted(set(current_worktrees_by_path) - set(baseline_worktrees))
    worktrees_removed = sorted(set(baseline_worktrees) - set(current_worktrees_by_path))
    worktrees_modified = sorted(
        path
        for path in set(baseline_worktrees) & set(current_worktrees_by_path)
        if _worktree_lock_state(baseline_worktrees[path]) != _worktree_lock_state(current_worktrees_by_path[path])
    )
    worktrees_changed = bool(worktrees_added or worktrees_removed or worktrees_modified)

    affected_index_paths = sorted(set(manifest["index_paths"]) | set(current_index_paths))
    affected_worktree_paths = sorted(set(manifest["worktree_paths"]) | set(current_worktree_paths))
    affected_tracked_paths = sorted(set(affected_index_paths) | set(affected_worktree_paths) | set(head_changed_paths))
    changed_paths = sorted(set(affected_tracked_paths) | set(changed_untracked))
    repository_changed = bool(head_changed or tracked_changed or changed_untracked)
    result = {
        "repo": str(repo),
        "common_dir": current_common_dir,
        "baseline_head": manifest["head"],
        "current_head": current_head,
        "head_relation": head_relation,
        "repository_changed": repository_changed,
        "tracked_changed": tracked_changed,
        "index_changed": index_changed,
        "worktree_changed": worktree_changed,
        "index_paths": affected_index_paths,
        "worktree_paths": affected_worktree_paths,
        "tracked_paths": affected_tracked_paths,
        "untracked_added": untracked_added,
        "untracked_removed": untracked_removed,
        "untracked_modified": untracked_modified,
        "changed_paths": changed_paths,
        "worktrees_changed": worktrees_changed,
        "worktrees_added": worktrees_added,
        "worktrees_removed": worktrees_removed,
        "worktrees_modified": worktrees_modified,
        "baseline_worktrees": manifest["worktrees"],
        "current_worktrees": current_worktrees,
    }
    print(json.dumps(result, ensure_ascii=True, sort_keys=True))

    if not (repository_changed or worktrees_changed):
        return False

    print("作業ツリーが退避時点から変化している。", file=sys.stderr)
    if head_changed:
        print(f"HEAD: {manifest['head']} -> {current_head} (relation={head_relation})", file=sys.stderr)
    if changed_paths:
        print("変更パス:", file=sys.stderr)
        for path in changed_paths:
            print(f"- {path}", file=sys.stderr)
    if worktrees_changed:
        print("同一Git共通ディレクトリのworktree一覧またはlock状態が変化している。", file=sys.stderr)
    if repository_changed:
        _print_recovery(
            repo,
            snapshot_dir,
            manifest,
            current_head,
            affected_tracked_paths,
            current_untracked,
            has_index_patch=bool(index_patch),
            has_worktree_patch=bool(worktree_patch),
        )
    return True


def _load_snapshot_patch(
    snapshot_dir: pathlib.Path,
    patch_name: str,
    expected_sha256: str,
    label: str,
) -> bytes:
    """退避patchの内容とdigestを検証して返す。"""
    patch_path = snapshot_dir / patch_name
    try:
        patch = patch_path.read_bytes()
    except OSError as error:
        raise SnapshotError(f"{label}用パッチを読み込めない: {patch_path}: {error}") from error
    if hashlib.sha256(patch).hexdigest() != expected_sha256:
        raise SnapshotError(f"{label}用パッチが退避後に変化している: {patch_path}")
    return patch


def _print_recovery(
    repo: pathlib.Path,
    snapshot_dir: pathlib.Path,
    manifest: dict[str, typing.Any],
    current_head: str,
    affected_tracked_paths: list[str],
    current_untracked: dict[str, dict[str, typing.Any]],
    *,
    has_index_patch: bool,
    has_worktree_patch: bool,
) -> None:
    print("復旧材料と手順（実行前に利用者の明示的な確認が必要）:", file=sys.stderr)
    if current_head != manifest["head"]:
        print(
            f"- HEAD変化を先に判断する。現在HEAD {current_head} を退避用ブランチへ保全してから、"
            f"基準HEAD {manifest['head']} への復帰要否を確定する。",
            file=sys.stderr,
        )
    baseline_tracked_paths = [path for path in affected_tracked_paths if _path_exists_at_revision(repo, manifest["head"], path)]
    added_tracked_paths = sorted(set(affected_tracked_paths) - set(baseline_tracked_paths))
    if affected_tracked_paths:
        reset_index = ["git", "-C", str(repo), "reset", manifest["head"], "--", *affected_tracked_paths]
        print(f"- 追跡パスのindexを基準HEADへ戻す: {shlex.join(reset_index)}", file=sys.stderr)
    for path in affected_tracked_paths:
        remove = shlex.join(["rm", "-rf", "--", str(repo / path)])
        print(f"- 現行の追跡パスを確認後に除去する: {remove}", file=sys.stderr)
    if baseline_tracked_paths:
        checkout = ["git", "-C", str(repo), "checkout", manifest["head"], "--", *baseline_tracked_paths]
        print(f"- 追跡ファイルを基準HEADへ戻す: {shlex.join(checkout)}", file=sys.stderr)
    if has_index_patch:
        apply_index = [
            "git",
            "-C",
            str(repo),
            "apply",
            "--index",
            "--binary",
            str(snapshot_dir / _INDEX_PATCH_NAME),
        ]
        print(f"- index用退避パッチをindexとworktreeへ再適用する: {shlex.join(apply_index)}", file=sys.stderr)
    if has_worktree_patch:
        apply_worktree = [
            "git",
            "-C",
            str(repo),
            "apply",
            "--binary",
            str(snapshot_dir / _WORKTREE_PATCH_NAME),
        ]
        print(f"- 未ステージ用退避パッチをworktreeへ再適用する: {shlex.join(apply_worktree)}", file=sys.stderr)
    elif not has_index_patch and not baseline_tracked_paths:
        print("- 復元対象となる追跡ファイルの変化は無い。", file=sys.stderr)
    for path in added_tracked_paths:
        print(f"- 基準HEADに存在しない追加追跡パスの除去対象: {repo / path}", file=sys.stderr)

    baseline = {entry["path"]: entry for entry in manifest["untracked"]}
    for path in _conflicting_ancestor_paths(repo, baseline):
        remove = shlex.join(["rm", "-rf", "--", str(repo / path)])
        print(f"- 子パス復元前に競合する祖先パスを確認後に除去する: {remove}", file=sys.stderr)
    for path, entry in baseline.items():
        target = repo / path
        remove = shlex.join(["rm", "-rf", "--", str(target)])
        mkdir = shlex.join(["mkdir", "-p", str(target.parent)])
        print(f"- 未追跡パスの競合を確認後に除去する: {remove}", file=sys.stderr)
        if entry["kind"] == "file":
            blob = snapshot_dir / _BLOBS_DIR_NAME / entry["blob"]
            print(
                f"- 未追跡通常ファイルを復元する: {mkdir}; "
                f"install -m {entry['mode']:04o} {shlex.quote(str(blob))} {shlex.quote(str(target))}",
                file=sys.stderr,
            )
        else:
            print(
                f"- 未追跡シンボリックリンクを再作成する: {mkdir}; "
                f"ln -s {shlex.quote(entry['target'])} {shlex.quote(str(target))}",
                file=sys.stderr,
            )
    added = sorted(set(current_untracked) - set(baseline))
    for path in added:
        print(
            f"- 委譲後に追加された未追跡パス。確認後に除去する対象: {repo / path}",
            file=sys.stderr,
        )


def _conflicting_ancestor_paths(repo: pathlib.Path, baseline: dict[str, typing.Any]) -> list[str]:
    conflicts: set[str] = set()
    for path in baseline:
        for parent in pathlib.PurePath(path).parents:
            if str(parent) == ".":
                continue
            target = repo / parent
            try:
                metadata = target.lstat()
            except FileNotFoundError:
                continue
            except OSError as error:
                raise SnapshotError(f"未追跡パスの祖先を確認できない: {target}: {error}") from error
            if not stat.S_ISDIR(metadata.st_mode):
                conflicts.add(str(parent))
    return sorted(conflicts, key=lambda path: (len(pathlib.PurePath(path).parts), path))


def _capture_untracked(
    repo: pathlib.Path,
    blobs_dir: pathlib.Path,
    entries: dict[str, dict[str, typing.Any]],
) -> list[dict[str, typing.Any]]:
    result: list[dict[str, typing.Any]] = []
    for path, entry in entries.items():
        copied = dict(entry)
        if entry["kind"] == "file":
            blob_name = entry["sha256"]
            blob_path = blobs_dir / blob_name
            if not blob_path.exists():
                content = _read_untracked_file(repo / path, entry)
                if hashlib.sha256(content).hexdigest() != blob_name:
                    raise SnapshotError(f"退避中に未追跡ファイルが変化した: {repo / path}")
                _write_private(blob_path, content)
            copied["blob"] = blob_name
        else:
            try:
                target = os.readlink(repo / path)
            except OSError as error:
                raise SnapshotError(f"退避中に未追跡シンボリックリンクが変化した: {repo / path}: {error}") from error
            if target != entry["target"]:
                raise SnapshotError(f"退避中に未追跡シンボリックリンクが変化した: {repo / path}")
        result.append(copied)
    return result


def _read_untracked_file(path: pathlib.Path, expected: dict[str, typing.Any]) -> bytes:
    """リンク参照先を読まず、確認済みの未追跡通常ファイルを読み込む。"""
    descriptor: int | None = None
    try:
        before = path.lstat()
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or not stat.S_ISREG(opened.st_mode):
            raise SnapshotError(f"退避中に未追跡ファイルの種別が変化した: {path}")
        if (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino):
            raise SnapshotError(f"退避中に未追跡ファイルの実体が変化した: {path}")
        with os.fdopen(descriptor, "rb") as input_file:
            descriptor = None
            content = input_file.read()
        after = path.lstat()
    except OSError as error:
        raise SnapshotError(f"退避中に未追跡ファイルが変化した: {path}: {error}") from error
    finally:
        if descriptor is not None:
            os.close(descriptor)
    if (after.st_dev, after.st_ino) != (opened.st_dev, opened.st_ino):
        raise SnapshotError(f"退避中に未追跡ファイルの実体が変化した: {path}")
    if stat.S_IMODE(opened.st_mode) != expected["mode"]:
        raise SnapshotError(f"退避中に未追跡ファイルのモードが変化した: {path}")
    return content


def _snapshot_state(
    repo: pathlib.Path,
) -> SnapshotState:
    """比較可能な作業ツリー状態を取得する。"""
    head = _git_text(repo, "rev-parse", "HEAD").strip()
    index_patch = _index_patch(repo)
    worktree_patch = _worktree_patch(repo)
    index_paths = _git_paths(repo, "diff", "--cached", "--name-only", "-z", "HEAD")
    worktree_paths = _git_paths(repo, "diff", "--name-only", "-z")
    untracked = _inspect_untracked(repo)
    common_dir = str(pathlib.Path(_git_text(repo, "rev-parse", "--path-format=absolute", "--git-common-dir").strip()))
    worktrees = _worktree_inventory(repo)
    return SnapshotState(
        head,
        index_patch,
        worktree_patch,
        index_paths,
        worktree_paths,
        untracked,
        common_dir,
        worktrees,
    )


def _head_relation(repo: pathlib.Path, baseline_head: str, current_head: str) -> str:
    """基準HEADに対する現HEADの系譜を返す。"""
    if baseline_head == current_head:
        return "same"
    if _is_ancestor(repo, baseline_head, current_head):
        return "descendant"
    if _is_ancestor(repo, current_head, baseline_head):
        return "ancestor"
    return "diverged"


def _is_ancestor(repo: pathlib.Path, older: str, newer: str) -> bool:
    result = _run_git(repo, "merge-base", "--is-ancestor", older, newer)
    if result.returncode in {0, 1}:
        return result.returncode == 0
    stderr = result.stderr.decode("utf-8", errors="replace").strip()
    raise SnapshotError(f"git merge-base --is-ancestorが失敗した: {stderr}")


def _worktree_inventory(repo: pathlib.Path) -> list[dict[str, typing.Any]]:
    """同じGit共通ディレクトリに属するworktreeとlock状態を返す。"""
    output = _git(repo, "worktree", "list", "--porcelain", "-z")
    records: list[dict[str, str]] = []
    current: dict[str, str] = {}
    for raw_field in output.split(b"\0"):
        if not raw_field:
            if current:
                records.append(current)
                current = {}
            continue
        field, separator, value = raw_field.partition(b" ")
        key = field.decode("ascii", errors="strict")
        current[key] = value.decode("utf-8", errors="surrogateescape") if separator else ""
    if current:
        records.append(current)

    inventory: list[dict[str, typing.Any]] = []
    for record in records:
        path = record.get("worktree")
        if path is None:
            raise SnapshotError("git worktree listの出力にworktreeパスが無い")
        inventory.append(
            {
                "path": path,
                "head": record.get("HEAD"),
                "branch": record.get("branch"),
                "detached": "detached" in record,
                "bare": "bare" in record,
                "locked": "locked" in record,
                "lock_reason": record.get("locked") or None,
                "prunable": "prunable" in record,
                "prune_reason": record.get("prunable") or None,
            }
        )
    inventory.sort(key=lambda entry: entry["path"])
    return inventory


def _worktree_lock_state(entry: dict[str, typing.Any]) -> tuple[bool, str | None]:
    """worktree比較の終了状態へ算入するlock状態を返す。"""
    return entry["locked"], entry["lock_reason"]


def _path_exists_at_revision(repo: pathlib.Path, revision: str, path: str) -> bool:
    """指定revisionでpathが追跡されているかを返す。"""
    result = _run_git(repo, "cat-file", "-e", f"{revision}:{path}")
    if result.returncode in {0, 128}:
        return result.returncode == 0
    stderr = result.stderr.decode("utf-8", errors="replace").strip()
    raise SnapshotError(f"git cat-file -eが失敗した: {stderr}")


def _inspect_untracked(repo: pathlib.Path) -> dict[str, dict[str, typing.Any]]:
    entries: dict[str, dict[str, typing.Any]] = {}
    for path in _git_paths(repo, "ls-files", "--others", "--exclude-standard", "-z"):
        target = repo / path
        try:
            metadata = target.lstat()
            if stat.S_ISLNK(metadata.st_mode):
                entries[path] = {"path": path, "kind": "symlink", "target": os.readlink(target)}
            elif stat.S_ISREG(metadata.st_mode):
                mode = stat.S_IMODE(metadata.st_mode)
                content = _read_untracked_file(target, {"mode": mode})
                entries[path] = {
                    "path": path,
                    "kind": "file",
                    "mode": mode,
                    "sha256": hashlib.sha256(content).hexdigest(),
                }
            else:
                raise SnapshotError(f"未追跡パスの種類を退避できない: {target}")
        except OSError as error:
            raise SnapshotError(f"未追跡パスを読み込めない: {target}: {error}") from error
    return entries


def _resolve_repo(repo_arg: pathlib.Path) -> pathlib.Path:
    if not repo_arg.is_absolute():
        raise SnapshotError(f"リポジトリは絶対パスで指定する: {repo_arg}")
    repo = _resolve_existing_dir(repo_arg, "リポジトリ")
    top_level = pathlib.Path(_git_text(repo, "rev-parse", "--show-toplevel").strip()).resolve()
    if top_level != repo:
        raise SnapshotError(f"リポジトリ直下を指定する: {repo_arg}; resolved={top_level}")
    return repo


def _validate_new_snapshot_dir(repo: pathlib.Path, output_dir_arg: pathlib.Path) -> pathlib.Path:
    if not output_dir_arg.is_absolute():
        raise SnapshotError(f"出力先は絶対パスで指定する: {output_dir_arg}")
    if output_dir_arg.exists() or output_dir_arg.is_symlink():
        raise SnapshotError(f"出力先が既に存在する: {output_dir_arg}")
    try:
        output_dir = output_dir_arg.resolve(strict=False)
        parent = output_dir.parent.resolve(strict=True)
    except OSError as error:
        raise SnapshotError(f"出力先の親ディレクトリを解決できない: {output_dir_arg}: {error}") from error
    output_dir = parent / output_dir.name
    if output_dir == repo or repo in output_dir.parents:
        raise SnapshotError(f"出力先を対象worktree内には作成できない: {output_dir}")
    if output_dir.exists() or output_dir.is_symlink():
        raise SnapshotError(f"出力先が既に存在する: {output_dir}")
    return output_dir


def _resolve_existing_dir(path_arg: pathlib.Path, label: str) -> pathlib.Path:
    if not path_arg.is_absolute():
        raise SnapshotError(f"{label}は絶対パスで指定する: {path_arg}")
    try:
        path = path_arg.resolve(strict=True)
    except OSError as error:
        raise SnapshotError(f"{label}を解決できない: {path_arg}: {error}") from error
    if not path.is_dir():
        raise SnapshotError(f"{label}はディレクトリではない: {path}")
    return path


def _load_manifest(snapshot_dir: pathlib.Path) -> dict[str, typing.Any]:
    manifest_path = snapshot_dir / _MANIFEST_NAME
    try:
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise SnapshotError(f"manifestを読み込めない: {manifest_path}: {error}") from error
    if not isinstance(raw, dict) or raw.get("format_version") != 3:
        raise SnapshotError(f"manifest形式が不正: {manifest_path}")
    required = {
        "repo",
        "common_dir",
        "head",
        "index_patch_sha256",
        "worktree_patch_sha256",
        "index_paths",
        "worktree_paths",
        "untracked",
        "worktrees",
    }
    if not required <= raw.keys():
        raise SnapshotError(f"manifestの必須項目が不足している: {manifest_path}")
    if not isinstance(raw["repo"], str) or not pathlib.Path(raw["repo"]).is_absolute():
        raise SnapshotError(f"manifestのリポジトリ形式が不正: {manifest_path}")
    if not isinstance(raw["common_dir"], str) or not pathlib.Path(raw["common_dir"]).is_absolute():
        raise SnapshotError(f"manifestのGit共通ディレクトリ形式が不正: {manifest_path}")
    if not _is_object_id(raw["head"]):
        raise SnapshotError(f"manifestのHEAD形式が不正: {manifest_path}")
    for field, label in (
        ("index_patch_sha256", "index"),
        ("worktree_patch_sha256", "未ステージ"),
    ):
        if not _is_sha256(raw[field]):
            raise SnapshotError(f"manifestの{label}パッチdigest形式が不正: {manifest_path}")
    if (
        not isinstance(raw["index_paths"], list)
        or not isinstance(raw["worktree_paths"], list)
        or not isinstance(raw["untracked"], list)
        or not isinstance(raw["worktrees"], list)
    ):
        raise SnapshotError(f"manifestのパス一覧形式が不正: {manifest_path}")
    for field in ("index_paths", "worktree_paths"):
        for path in raw[field]:
            _validate_relative_path(path, manifest_path)
    for entry in raw["untracked"]:
        if not isinstance(entry, dict) or "path" not in entry or entry.get("kind") not in {"file", "symlink"}:
            raise SnapshotError(f"manifestの未追跡項目が不正: {manifest_path}")
        _validate_relative_path(entry["path"], manifest_path)
        if entry["kind"] == "file" and not {"mode", "sha256", "blob"} <= entry.keys():
            raise SnapshotError(f"manifestの通常ファイル項目が不正: {manifest_path}")
        if entry["kind"] == "file":
            _validate_blob(snapshot_dir, entry, manifest_path)
        if entry["kind"] == "symlink" and not isinstance(entry.get("target"), str):
            raise SnapshotError(f"manifestのリンク項目が不正: {manifest_path}")
    paths = [entry["path"] for entry in raw["untracked"]]
    if len(paths) != len(set(paths)):
        raise SnapshotError(f"manifestの未追跡パスが重複している: {manifest_path}")
    _validate_worktrees(raw["worktrees"], manifest_path)
    return raw


def _validate_worktrees(entries: list[typing.Any], manifest_path: pathlib.Path) -> None:
    required = {
        "path",
        "head",
        "branch",
        "detached",
        "bare",
        "locked",
        "lock_reason",
        "prunable",
        "prune_reason",
    }
    paths: list[str] = []
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != required:
            raise SnapshotError(f"manifestのworktree項目が不正: {manifest_path}")
        path = entry["path"]
        if not isinstance(path, str) or not pathlib.Path(path).is_absolute():
            raise SnapshotError(f"manifestのworktreeパス形式が不正: {manifest_path}")
        if entry["head"] is not None and not _is_object_id(entry["head"]):
            raise SnapshotError(f"manifestのworktree HEAD形式が不正: {manifest_path}")
        if entry["branch"] is not None and not isinstance(entry["branch"], str):
            raise SnapshotError(f"manifestのworktree branch形式が不正: {manifest_path}")
        for field in ("detached", "bare", "locked", "prunable"):
            if not isinstance(entry[field], bool):
                raise SnapshotError(f"manifestのworktree状態形式が不正: {manifest_path}")
        for field in ("lock_reason", "prune_reason"):
            if entry[field] is not None and not isinstance(entry[field], str):
                raise SnapshotError(f"manifestのworktree理由形式が不正: {manifest_path}")
        paths.append(path)
    if len(paths) != len(set(paths)):
        raise SnapshotError(f"manifestのworktreeパスが重複している: {manifest_path}")


def _is_object_id(value: object) -> bool:
    return isinstance(value, str) and len(value) in {40, 64} and all(character in "0123456789abcdef" for character in value)


def _is_sha256(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(character in "0123456789abcdef" for character in value)


def _validate_blob(
    snapshot_dir: pathlib.Path,
    entry: dict[str, typing.Any],
    manifest_path: pathlib.Path,
) -> None:
    digest = entry["sha256"]
    blob_name = entry["blob"]
    mode = entry["mode"]
    if (
        not isinstance(digest, str)
        or len(digest) != 64
        or any(character not in "0123456789abcdef" for character in digest)
        or blob_name != digest
        or not isinstance(mode, int)
        or isinstance(mode, bool)
        or not 0 <= mode <= 0o7777
    ):
        raise SnapshotError(f"manifestの通常ファイル項目が不正: {manifest_path}")
    blob_path = snapshot_dir / _BLOBS_DIR_NAME / blob_name
    try:
        content = blob_path.read_bytes()
    except OSError as error:
        raise SnapshotError(f"未追跡ファイル用blobを読み込めない: {blob_path}: {error}") from error
    if hashlib.sha256(content).hexdigest() != digest:
        raise SnapshotError(f"未追跡ファイル用blobが退避後に変化している: {blob_path}")


def _validate_relative_path(value: object, manifest_path: pathlib.Path) -> None:
    if not isinstance(value, str):
        raise SnapshotError(f"manifestのパスが文字列ではない: {manifest_path}")
    path = pathlib.PurePath(value)
    if path.is_absolute() or ".." in path.parts or value in {"", "."}:
        raise SnapshotError(f"manifestに不正な相対パスがある: {value}")


def _write_private(path: pathlib.Path, content: bytes) -> None:
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as output:
            output.write(content)
    except OSError as error:
        raise SnapshotError(f"退避ファイルを書き込めない: {path}: {error}") from error


def _git_paths(repo: pathlib.Path, *args: str) -> list[str]:
    output = _git(repo, *args)
    if not output:
        return []
    parts = output.split(b"\0")
    if parts[-1] == b"":
        parts.pop()
    return [part.decode("utf-8", errors="surrogateescape") for part in parts]


def _git_text(repo: pathlib.Path, *args: str) -> str:
    return _git(repo, *args).decode("utf-8", errors="surrogateescape")


def _index_patch(repo: pathlib.Path) -> bytes:
    """HEADからindexまでの再適用可能なパッチを取得する。"""
    return _git(
        repo,
        "diff",
        "--cached",
        "--binary",
        "--no-textconv",
        "--no-ext-diff",
        "--src-prefix=a/",
        "--dst-prefix=b/",
        "HEAD",
    )


def _worktree_patch(repo: pathlib.Path) -> bytes:
    """indexからworktreeまでの再適用可能なパッチを取得する。"""
    return _git(
        repo,
        "diff",
        "--binary",
        "--no-textconv",
        "--no-ext-diff",
        "--src-prefix=a/",
        "--dst-prefix=b/",
    )


def _git(repo: pathlib.Path, *args: str) -> bytes:
    result = _run_git(repo, *args)
    if result.returncode != 0:
        stderr = result.stderr.decode("utf-8", errors="replace").strip()
        raise SnapshotError(f"git {' '.join(args)}が失敗した: {stderr}")
    return result.stdout


def _run_git(repo: pathlib.Path, *args: str) -> subprocess.CompletedProcess[bytes]:
    try:
        return subprocess.run(
            ["git", "-C", str(repo), *args],
            check=False,
            capture_output=True,
            timeout=_GIT_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise SnapshotError(f"gitコマンドを実行できない: git {' '.join(args)}: {error}") from error


if __name__ == "__main__":
    main()
