#!/usr/bin/env -S uv run --no-project --script
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Git作業ツリーを復旧可能な形で退避し、後続の変化を検出する。"""

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
_PATCH_NAME = "tracked.patch"
_BLOBS_DIR_NAME = "blobs"
_GIT_TIMEOUT_SECONDS = 30


class SnapshotError(Exception):
    """利用者が修正できる取得・形式エラー。"""


def main() -> None:
    """CLI引数を解釈して退避または比較を実行する。"""
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    capture_parser = subparsers.add_parser("capture", help="作業ツリー状態を退避する")
    capture_parser.add_argument("--repo", required=True, type=pathlib.Path)
    capture_parser.add_argument("--output-dir", required=True, type=pathlib.Path)

    compare_parser = subparsers.add_parser("compare", help="退避時点と現在の状態を比較する")
    compare_parser.add_argument("--repo", required=True, type=pathlib.Path)
    compare_parser.add_argument("--snapshot-dir", required=True, type=pathlib.Path)

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

    head, patch, tracked_paths, untracked = confirmed_state
    output_dir.mkdir(mode=0o700)
    blobs_dir = output_dir / _BLOBS_DIR_NAME
    blobs_dir.mkdir(mode=0o700)
    _write_private(output_dir / _PATCH_NAME, patch)
    captured_untracked = _capture_untracked(repo, blobs_dir, untracked)
    if _snapshot_state(repo) != confirmed_state:
        raise SnapshotError("退避対象が実体保存中に変化したため、同一時点の状態を確定できない")
    manifest = {
        "format_version": 1,
        "repo": str(repo),
        "head": head,
        "tracked_patch_sha256": hashlib.sha256(patch).hexdigest(),
        "tracked_paths": tracked_paths,
        "untracked": captured_untracked,
    }
    manifest_bytes = (json.dumps(manifest, ensure_ascii=True, indent=2, sort_keys=True) + "\n").encode()
    _write_private(output_dir / _MANIFEST_NAME, manifest_bytes)


def _compare(repo: pathlib.Path, snapshot_dir_arg: pathlib.Path) -> bool:
    snapshot_dir = _resolve_existing_dir(snapshot_dir_arg, "退避ディレクトリ")
    manifest = _load_manifest(snapshot_dir)
    if manifest["repo"] != str(repo):
        raise SnapshotError(f"退避対象リポジトリが一致しない: expected={manifest['repo']}, actual={repo}")

    patch_path = snapshot_dir / _PATCH_NAME
    try:
        patch = patch_path.read_bytes()
    except OSError as error:
        raise SnapshotError(f"追跡ファイル用パッチを読み込めない: {patch_path}: {error}") from error
    if hashlib.sha256(patch).hexdigest() != manifest["tracked_patch_sha256"]:
        raise SnapshotError(f"追跡ファイル用パッチが退避後に変化している: {patch_path}")

    initial_state = _snapshot_state(repo)
    confirmed_state = _snapshot_state(repo)
    if initial_state != confirmed_state:
        raise SnapshotError("比較対象が取得中に変化したため、同一時点の状態を確定できない")
    current_head, current_patch, current_tracked_paths, current_untracked = confirmed_state

    head_changed = current_head != manifest["head"]
    head_changed_paths = _git_paths(repo, "diff", "--name-only", "-z", manifest["head"], current_head) if head_changed else []
    tracked_changed = current_patch != patch
    baseline_untracked = {
        entry["path"]: {key: value for key, value in entry.items() if key != "blob"} for entry in manifest["untracked"]
    }
    untracked_paths = sorted(set(baseline_untracked) | set(current_untracked))
    changed_untracked = [path for path in untracked_paths if baseline_untracked.get(path) != current_untracked.get(path)]
    if not (head_changed or tracked_changed or changed_untracked):
        return False

    affected_tracked_paths = sorted(set(manifest["tracked_paths"]) | set(current_tracked_paths) | set(head_changed_paths))
    changed_paths = sorted(set(affected_tracked_paths) | set(changed_untracked))
    print("作業ツリーが退避時点から変化している。", file=sys.stderr)
    if head_changed:
        print(f"HEAD: {manifest['head']} -> {current_head}", file=sys.stderr)
    print("変更パス:", file=sys.stderr)
    for path in changed_paths:
        print(f"- {path}", file=sys.stderr)
    _print_recovery(
        repo,
        snapshot_dir,
        manifest,
        current_head,
        affected_tracked_paths,
        current_untracked,
        has_tracked_patch=bool(patch),
    )
    return True


def _print_recovery(
    repo: pathlib.Path,
    snapshot_dir: pathlib.Path,
    manifest: dict[str, typing.Any],
    current_head: str,
    affected_tracked_paths: list[str],
    current_untracked: dict[str, dict[str, typing.Any]],
    *,
    has_tracked_patch: bool,
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
    if has_tracked_patch:
        apply_patch = ["git", "-C", str(repo), "apply", "--index", "--binary", str(snapshot_dir / _PATCH_NAME)]
        print(f"- 退避パッチを再適用する: {shlex.join(apply_patch)}", file=sys.stderr)
    elif not baseline_tracked_paths:
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
) -> tuple[str, bytes, list[str], dict[str, dict[str, typing.Any]]]:
    """比較可能な作業ツリー状態を取得する。"""
    head = _git_text(repo, "rev-parse", "HEAD").strip()
    patch = _tracked_patch(repo)
    tracked_paths = _git_paths(repo, "diff", "--name-only", "-z", "HEAD")
    untracked = _inspect_untracked(repo)
    return head, patch, tracked_paths, untracked


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
    if not isinstance(raw, dict) or raw.get("format_version") != 1:
        raise SnapshotError(f"manifest形式が不正: {manifest_path}")
    required = {"repo", "head", "tracked_patch_sha256", "tracked_paths", "untracked"}
    if not required <= raw.keys():
        raise SnapshotError(f"manifestの必須項目が不足している: {manifest_path}")
    if not isinstance(raw["repo"], str) or not pathlib.Path(raw["repo"]).is_absolute():
        raise SnapshotError(f"manifestのリポジトリ形式が不正: {manifest_path}")
    if not _is_object_id(raw["head"]):
        raise SnapshotError(f"manifestのHEAD形式が不正: {manifest_path}")
    if not _is_sha256(raw["tracked_patch_sha256"]):
        raise SnapshotError(f"manifestの追跡パッチdigest形式が不正: {manifest_path}")
    if not isinstance(raw["tracked_paths"], list) or not isinstance(raw["untracked"], list):
        raise SnapshotError(f"manifestのパス一覧形式が不正: {manifest_path}")
    for path in raw["tracked_paths"]:
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
    return raw


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


def _tracked_patch(repo: pathlib.Path) -> bytes:
    """利用者のdiff.noprefix設定に依存しない再適用可能なパッチを取得する。"""
    return _git(
        repo,
        "diff",
        "--binary",
        "--no-textconv",
        "--no-ext-diff",
        "--src-prefix=a/",
        "--dst-prefix=b/",
        "HEAD",
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
