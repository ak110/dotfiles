#!/usr/bin/env -S uv run --no-project --script
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""計画レビュー用cloneへ開始状態を再現し、終了時の境界を検査する。"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import stat
import subprocess
import sys
import typing

_FORMAT_VERSION = 2
_MANIFEST_NAME = "workspace.json"
_SOURCE_SNAPSHOT_DIR = "source-snapshot"
_CONDITIONAL_SOURCE_SNAPSHOT_DIR = "conditional-source-snapshot"
_REVIEW_REPO_DIR = "repository"
_REVIEW_SNAPSHOT_DIR = "review-snapshot"
_ORIGINAL_PLAN_NAME = "plan-original.md"
_REVIEW_PLAN_NAME = "plan-review.md"
_PLAN_DIFF_NAME = "plan.diff"
_REPRODUCTION_DIR = "reproduction"
_SNAPSHOT_SCRIPT = pathlib.Path(__file__).with_name("_worktree_snapshot.py")
_COMMAND_TIMEOUT_SECONDS = 120
_USE_POSIX_MODE = os.name == "posix"


class ReviewWorkspaceError(Exception):
    """利用者が修正できる入力または作業領域のエラー。"""


def main() -> None:
    """CLI引数を解釈して作業領域の作成または検査を実行する。"""
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    create_parser = subparsers.add_parser("create", help="隔離したレビュー作業領域を作成する")
    create_parser.add_argument("--source-repo", required=True, type=pathlib.Path)
    create_parser.add_argument("--conditional-source-repo", type=pathlib.Path)
    create_parser.add_argument("--plan-file", required=True, type=pathlib.Path)
    create_parser.add_argument("--output-dir", required=True, type=pathlib.Path)

    finish_parser = subparsers.add_parser("finish", help="cloneと計画コピーの終了状態を検査する")
    finish_parser.add_argument("--workspace-dir", required=True, type=pathlib.Path)

    args = parser.parse_args()
    try:
        if args.command == "create":
            result = _create(args.source_repo, args.conditional_source_repo, args.plan_file, args.output_dir)
            print(json.dumps(result, ensure_ascii=True, sort_keys=True))
            sys.exit(0)
        if args.command == "finish":
            result, changed = _finish(args.workspace_dir)
            print(json.dumps(result, ensure_ascii=True, sort_keys=True))
            sys.exit(1 if changed else 0)
        raise ReviewWorkspaceError(f"未対応のサブコマンド: {args.command}")
    except ReviewWorkspaceError as error:
        print(f"error: {error}", file=sys.stderr)
        sys.exit(2)


def _create(
    source_repo_arg: pathlib.Path,
    conditional_source_repo_arg: pathlib.Path | None,
    plan_file_arg: pathlib.Path,
    output_dir_arg: pathlib.Path,
) -> dict[str, typing.Any]:
    source_repo = _resolve_repo(source_repo_arg)
    conditional_source_repo = _resolve_repo(conditional_source_repo_arg) if conditional_source_repo_arg is not None else None
    plan_file = _resolve_file(plan_file_arg, "計画ファイル")
    protected_repos = {source_repo}
    if conditional_source_repo is not None:
        protected_repos.add(conditional_source_repo)
    output_dir = _resolve_new_dir(output_dir_arg, protected_repos)
    output_dir.mkdir(mode=0o700)

    source_snapshot = output_dir / _SOURCE_SNAPSHOT_DIR
    conditional_source_snapshot = output_dir / _CONDITIONAL_SOURCE_SNAPSHOT_DIR if conditional_source_repo is not None else None
    review_repo = output_dir / _REVIEW_REPO_DIR
    review_snapshot = output_dir / _REVIEW_SNAPSHOT_DIR
    original_plan = output_dir / _ORIGINAL_PLAN_NAME
    review_plan = output_dir / _REVIEW_PLAN_NAME
    reproduction_dir = output_dir / _REPRODUCTION_DIR

    plan_content = _read_bytes(plan_file, "計画ファイル")
    _write_private(original_plan, plan_content)
    _write_private(review_plan, plan_content)
    reproduction_dir.mkdir(mode=0o700)

    _run_snapshot("capture", "--repo", source_repo, "--output-dir", source_snapshot)
    if conditional_source_repo is not None and conditional_source_snapshot is not None:
        _run_snapshot(
            "capture",
            "--repo",
            conditional_source_repo,
            "--output-dir",
            conditional_source_snapshot,
        )
    source_manifest = _load_json_object(source_snapshot / "manifest.json", "source snapshot")
    _clone_repository(source_repo, review_repo)
    _materialize_snapshot(review_repo, source_snapshot, source_manifest)
    _run_snapshot("capture", "--repo", review_repo, "--output-dir", review_snapshot)
    review_manifest = _load_json_object(review_snapshot / "manifest.json", "review snapshot")
    review_files = _confirmed_file_inventory(review_repo)

    if _snapshot_signature(source_snapshot, source_manifest) != _snapshot_signature(review_snapshot, review_manifest):
        raise ReviewWorkspaceError("一時cloneへ開始時点のGit状態を再現できない")
    _ensure_separate_git_directories(source_repo, review_repo)

    source_compare = _run_snapshot("compare", "--repo", source_repo, "--snapshot-dir", source_snapshot)
    if source_compare.returncode != 0:
        raise ReviewWorkspaceError("作業領域の作成中に対象リポジトリが変化した")
    if conditional_source_repo is not None and conditional_source_snapshot is not None:
        conditional_compare = _run_snapshot(
            "compare",
            "--repo",
            conditional_source_repo,
            "--snapshot-dir",
            conditional_source_snapshot,
        )
        if conditional_compare.returncode != 0:
            raise ReviewWorkspaceError("作業領域の作成中に条件付き複製元が変化した")

    manifest = {
        "format_version": _FORMAT_VERSION,
        "source_repo": str(source_repo),
        "source_head": source_manifest["head"],
        "plan_file": str(plan_file),
        "plan_sha256": hashlib.sha256(plan_content).hexdigest(),
        "source_snapshot": str(source_snapshot),
        "conditional_source_repo": str(conditional_source_repo) if conditional_source_repo is not None else None,
        "conditional_source_snapshot": (str(conditional_source_snapshot) if conditional_source_snapshot is not None else None),
        "review_repo": str(review_repo),
        "review_snapshot": str(review_snapshot),
        "review_files": review_files,
        "original_plan": str(original_plan),
        "review_plan": str(review_plan),
        "plan_diff": str(output_dir / _PLAN_DIFF_NAME),
        "reproduction_dir": str(reproduction_dir),
    }
    _write_private(
        output_dir / _MANIFEST_NAME,
        (json.dumps(manifest, ensure_ascii=True, indent=2, sort_keys=True) + "\n").encode("utf-8"),
    )
    return {
        "workspace_dir": str(output_dir),
        "source_repo": str(source_repo),
        "conditional_source_repo": str(conditional_source_repo) if conditional_source_repo is not None else None,
        "review_repo": str(review_repo),
        "original_plan": str(original_plan),
        "review_plan": str(review_plan),
        "plan_diff": str(output_dir / _PLAN_DIFF_NAME),
        "reproduction_dir": str(reproduction_dir),
    }


def _finish(workspace_dir_arg: pathlib.Path) -> tuple[dict[str, typing.Any], bool]:
    workspace_dir = _resolve_existing_dir(workspace_dir_arg, "レビュー作業領域")
    manifest = _load_workspace_manifest(workspace_dir)
    review_repo = _workspace_path(workspace_dir, manifest, "review_repo")
    review_snapshot = _workspace_path(workspace_dir, manifest, "review_snapshot")
    source_repo = _resolve_repo(pathlib.Path(manifest["source_repo"]))
    source_snapshot = _workspace_path(workspace_dir, manifest, "source_snapshot")
    conditional_source_repo_value = manifest["conditional_source_repo"]
    conditional_source_snapshot_value = manifest["conditional_source_snapshot"]
    conditional_source_repo = (
        _resolve_repo(pathlib.Path(conditional_source_repo_value)) if isinstance(conditional_source_repo_value, str) else None
    )
    conditional_source_snapshot = (
        _workspace_path(workspace_dir, manifest, "conditional_source_snapshot")
        if isinstance(conditional_source_snapshot_value, str)
        else None
    )
    original_plan = _workspace_path(workspace_dir, manifest, "original_plan")
    review_plan = _workspace_path(workspace_dir, manifest, "review_plan")
    plan_diff = _workspace_path(workspace_dir, manifest, "plan_diff", must_exist=False)
    source_plan = _resolve_file(pathlib.Path(manifest["plan_file"]), "正規計画ファイル")

    original_content = _read_bytes(original_plan, "計画原本コピー")
    review_content = _read_bytes(review_plan, "レビュー用計画コピー")
    expected_digest = manifest["plan_sha256"]
    if hashlib.sha256(original_content).hexdigest() != expected_digest:
        raise ReviewWorkspaceError("計画原本コピーが作成後に変化している")

    source_plan_unchanged = hashlib.sha256(_read_bytes(source_plan, "正規計画ファイル")).hexdigest() == expected_digest
    source_compare = _run_snapshot(
        "compare",
        "--repo",
        source_repo,
        "--snapshot-dir",
        source_snapshot,
        allow_change=True,
    )
    if source_compare.returncode == 2:
        raise ReviewWorkspaceError(f"対象リポジトリを比較できない: {source_compare.stderr.strip()}")
    review_compare = _run_snapshot(
        "compare",
        "--repo",
        review_repo,
        "--snapshot-dir",
        review_snapshot,
        allow_change=True,
    )
    if review_compare.returncode == 2:
        raise ReviewWorkspaceError(f"レビュー用cloneを比較できない: {review_compare.stderr.strip()}")
    source_compare_json = _decode_compare_result(source_compare, "対象リポジトリ")
    review_compare_json = _decode_compare_result(review_compare, "レビュー用clone")
    conditional_source_compare_json: dict[str, typing.Any] | None = None
    conditional_source_repo_unchanged: bool | None = None
    if conditional_source_repo is not None and conditional_source_snapshot is not None:
        conditional_source_compare = _run_snapshot(
            "compare",
            "--repo",
            conditional_source_repo,
            "--snapshot-dir",
            conditional_source_snapshot,
            allow_change=True,
        )
        if conditional_source_compare.returncode == 2:
            raise ReviewWorkspaceError(f"条件付き複製元を比較できない: {conditional_source_compare.stderr.strip()}")
        conditional_source_compare_json = _decode_compare_result(conditional_source_compare, "条件付き複製元")
        conditional_source_repo_unchanged = conditional_source_compare.returncode == 0

    review_files_compare = _compare_file_inventories(manifest["review_files"], _confirmed_file_inventory(review_repo))

    diff = _plan_diff(original_plan, review_plan)
    _write_or_replace_private(plan_diff, diff)
    source_repo_unchanged = source_compare.returncode == 0
    review_git_unchanged = review_compare.returncode == 0
    review_repo_unchanged = review_git_unchanged and not review_files_compare["changed"]
    result = {
        "workspace_dir": str(workspace_dir),
        "source_plan_unchanged": source_plan_unchanged,
        "source_repo_unchanged": source_repo_unchanged,
        "source_repo_compare": source_compare_json,
        "conditional_source_repo_unchanged": conditional_source_repo_unchanged,
        "conditional_source_repo_compare": conditional_source_compare_json,
        "review_repo_unchanged": review_repo_unchanged,
        "review_repo_compare": review_compare_json,
        "review_files_compare": review_files_compare,
        "plan_changed": original_content != review_content,
        "plan_sha256_before": expected_digest,
        "plan_sha256_after": hashlib.sha256(review_content).hexdigest(),
        "plan_diff": str(plan_diff),
    }
    changed = (
        not source_plan_unchanged
        or not source_repo_unchanged
        or conditional_source_repo_unchanged is False
        or not review_repo_unchanged
    )
    return result, changed


def _decode_compare_result(result: subprocess.CompletedProcess[str], label: str) -> dict[str, typing.Any]:
    try:
        value = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise ReviewWorkspaceError(f"{label}の比較結果がJSONではない") from error
    if not isinstance(value, dict):
        raise ReviewWorkspaceError(f"{label}の比較結果がオブジェクトではない")
    return value


def _clone_repository(source_repo: pathlib.Path, review_repo: pathlib.Path) -> None:
    _run(
        [
            "git",
            "clone",
            "--no-hardlinks",
            "--no-checkout",
            "--",
            str(source_repo),
            str(review_repo),
        ],
        "一時cloneを作成できない",
    )


def _materialize_snapshot(
    review_repo: pathlib.Path,
    snapshot_dir: pathlib.Path,
    manifest: dict[str, typing.Any],
) -> None:
    head = manifest.get("head")
    if not isinstance(head, str):
        raise ReviewWorkspaceError("source snapshotのHEAD形式が不正")
    _run(["git", "-C", str(review_repo), "checkout", "--detach", "--force", head], "cloneのHEADを再現できない")

    index_patch = snapshot_dir / "index.patch"
    worktree_patch = snapshot_dir / "worktree.patch"
    if index_patch.stat().st_size:
        _run(
            ["git", "-C", str(review_repo), "apply", "--index", "--binary", str(index_patch)],
            "cloneのindex状態を再現できない",
        )
    if worktree_patch.stat().st_size:
        _run(
            ["git", "-C", str(review_repo), "apply", "--binary", str(worktree_patch)],
            "cloneの未ステージ状態を再現できない",
        )
    entries = manifest.get("untracked")
    if not isinstance(entries, list):
        raise ReviewWorkspaceError("source snapshotの未追跡一覧形式が不正")
    for entry in entries:
        _materialize_untracked(review_repo, snapshot_dir, entry)


def _materialize_untracked(
    review_repo: pathlib.Path,
    snapshot_dir: pathlib.Path,
    entry: object,
) -> None:
    if not isinstance(entry, dict):
        raise ReviewWorkspaceError("source snapshotの未追跡項目が不正")
    typed_entry = typing.cast(dict[str, typing.Any], entry)
    path_value = typed_entry.get("path")
    if not isinstance(path_value, str):
        raise ReviewWorkspaceError("source snapshotの未追跡項目が不正")
    relative = pathlib.PurePath(path_value)
    if relative.is_absolute() or ".." in relative.parts or str(relative) in {"", "."}:
        raise ReviewWorkspaceError(f"source snapshotに不正な未追跡パスがある: {relative}")
    target = review_repo.joinpath(*relative.parts)
    _prepare_parent(review_repo, target.parent)
    if target.exists() or target.is_symlink():
        raise ReviewWorkspaceError(f"cloneの未追跡パスが既に存在する: {target}")

    kind = typed_entry.get("kind")
    if kind == "file":
        blob = typed_entry.get("blob")
        mode = typed_entry.get("mode")
        if not isinstance(blob, str) or not isinstance(mode, int) or isinstance(mode, bool):
            raise ReviewWorkspaceError("source snapshotの未追跡ファイル形式が不正")
        content = _read_bytes(snapshot_dir / "blobs" / blob, "未追跡blob")
        if hashlib.sha256(content).hexdigest() != typed_entry.get("sha256"):
            raise ReviewWorkspaceError(f"未追跡blobのdigestが一致しない: {blob}")
        _write_private(target, content, mode=stat.S_IMODE(mode))
        return
    target_value = typed_entry.get("target")
    if kind == "symlink" and isinstance(target_value, str):
        try:
            os.symlink(target_value, target)
        except OSError as error:
            raise ReviewWorkspaceError(f"未追跡シンボリックリンクを再現できない: {target}: {error}") from error
        return
    raise ReviewWorkspaceError("source snapshotの未追跡項目種別が不正")


def _prepare_parent(review_repo: pathlib.Path, parent: pathlib.Path) -> None:
    relative = parent.relative_to(review_repo)
    current = review_repo
    for part in relative.parts:
        current /= part
        if current.is_symlink():
            raise ReviewWorkspaceError(f"cloneの未追跡パス祖先がシンボリックリンクである: {current}")
        try:
            current.mkdir(mode=0o700)
        except FileExistsError:
            if not current.is_dir():
                raise ReviewWorkspaceError(f"cloneの未追跡パス祖先がディレクトリではない: {current}") from None
        except OSError as error:
            raise ReviewWorkspaceError(f"cloneの未追跡パス祖先を作成できない: {current}: {error}") from error


def _snapshot_signature(
    snapshot_dir: pathlib.Path,
    manifest: dict[str, typing.Any],
) -> tuple[object, ...]:
    return (
        manifest.get("head"),
        _read_bytes(snapshot_dir / "index.patch", "indexパッチ"),
        _read_bytes(snapshot_dir / "worktree.patch", "未ステージパッチ"),
        manifest.get("index_paths"),
        manifest.get("worktree_paths"),
        manifest.get("untracked"),
    )


def _confirmed_file_inventory(repo: pathlib.Path) -> list[dict[str, typing.Any]]:
    initial = _file_inventory(repo)
    confirmed = _file_inventory(repo)
    if initial != confirmed:
        raise ReviewWorkspaceError(f"ファイル一覧の取得中にレビュー用cloneが変化した: {repo}")
    return confirmed


def _file_inventory(repo: pathlib.Path) -> list[dict[str, typing.Any]]:
    result: list[dict[str, typing.Any]] = []

    def visit(directory: pathlib.Path) -> None:
        try:
            entries = sorted(os.scandir(directory), key=lambda entry: entry.name)
        except OSError as error:
            raise ReviewWorkspaceError(f"レビュー用cloneの一覧を取得できない: {directory}: {error}") from error
        for entry in entries:
            path = pathlib.Path(entry.path)
            relative = path.relative_to(repo).as_posix()
            if directory == repo and entry.name == ".git":
                continue
            try:
                metadata = entry.stat(follow_symlinks=False)
                if entry.is_symlink():
                    result.append({"path": relative, "kind": "symlink", "target": os.readlink(path)})
                elif entry.is_dir(follow_symlinks=False):
                    visit(path)
                elif entry.is_file(follow_symlinks=False):
                    result.append(
                        {
                            "path": relative,
                            "kind": "file",
                            "mode": stat.S_IMODE(metadata.st_mode),
                            "sha256": _read_file_digest(path, metadata),
                        }
                    )
                else:
                    result.append({"path": relative, "kind": "other", "mode": metadata.st_mode})
            except OSError as error:
                raise ReviewWorkspaceError(f"レビュー用cloneのファイルを取得できない: {path}: {error}") from error

    visit(repo)
    return result


def _read_file_digest(path: pathlib.Path, expected: os.stat_result) -> str:
    descriptor: int | None = None
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or (expected.st_dev, expected.st_ino) != (opened.st_dev, opened.st_ino):
            raise ReviewWorkspaceError(f"レビュー用cloneのファイル実体が変化した: {path}")
        digest = hashlib.sha256()
        with os.fdopen(descriptor, "rb") as input_file:
            descriptor = None
            for chunk in iter(lambda: input_file.read(1024 * 1024), b""):
                digest.update(chunk)
        after = path.lstat()
    except OSError as error:
        raise ReviewWorkspaceError(f"レビュー用cloneのファイルを読み込めない: {path}: {error}") from error
    finally:
        if descriptor is not None:
            os.close(descriptor)
    if (after.st_dev, after.st_ino) != (opened.st_dev, opened.st_ino):
        raise ReviewWorkspaceError(f"レビュー用cloneのファイル実体が変化した: {path}")
    return digest.hexdigest()


def _compare_file_inventories(
    baseline_entries: object,
    current_entries: list[dict[str, typing.Any]],
) -> dict[str, typing.Any]:
    if not isinstance(baseline_entries, list):
        raise ReviewWorkspaceError("レビュー用cloneの基準ファイル一覧形式が不正")
    baseline = _inventory_by_path(baseline_entries, "基準")
    current = _inventory_by_path(current_entries, "現在")
    added = sorted(set(current) - set(baseline))
    removed = sorted(set(baseline) - set(current))
    modified = sorted(path for path in set(baseline) & set(current) if baseline[path] != current[path])
    return {
        "changed": bool(added or removed or modified),
        "added": added,
        "removed": removed,
        "modified": modified,
    }


def _inventory_by_path(entries: list[typing.Any], label: str) -> dict[str, dict[str, typing.Any]]:
    result: dict[str, dict[str, typing.Any]] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            raise ReviewWorkspaceError(f"レビュー用cloneの{label}ファイル一覧形式が不正")
        typed_entry = typing.cast(dict[str, typing.Any], entry)
        path = typed_entry.get("path")
        if not isinstance(path, str) or not path or path in result:
            raise ReviewWorkspaceError(f"レビュー用cloneの{label}ファイルパス形式が不正")
        result[path] = typed_entry
    return result


def _ensure_separate_git_directories(source_repo: pathlib.Path, review_repo: pathlib.Path) -> None:
    source_common = pathlib.Path(_git_text(source_repo, "rev-parse", "--path-format=absolute", "--git-common-dir")).resolve()
    review_common = pathlib.Path(_git_text(review_repo, "rev-parse", "--path-format=absolute", "--git-common-dir")).resolve()
    if source_common == review_common:
        raise ReviewWorkspaceError("一時cloneが対象リポジトリとGit管理領域を共有している")


def _plan_diff(original_plan: pathlib.Path, review_plan: pathlib.Path) -> bytes:
    result = _run_raw(
        [
            "git",
            "diff",
            "--no-index",
            "--no-ext-diff",
            "--binary",
            "--src-prefix=a/",
            "--dst-prefix=b/",
            "--",
            str(original_plan),
            str(review_plan),
        ]
    )
    if result.returncode not in {0, 1}:
        stderr = result.stderr.decode("utf-8", errors="replace").strip()
        raise ReviewWorkspaceError(f"計画コピーの差分を取得できない: {stderr}")
    return result.stdout


def _run_snapshot(
    command: str,
    *args: object,
    allow_change: bool = False,
) -> subprocess.CompletedProcess[str]:
    result = _run_raw([sys.executable, str(_SNAPSHOT_SCRIPT), command, *(str(arg) for arg in args)], text=True)
    accepted = {0, 1} if allow_change else {0}
    if result.returncode not in accepted:
        raise ReviewWorkspaceError(f"worktree snapshotの{command}に失敗した: {result.stderr.strip()}")
    return result


def _git_text(repo: pathlib.Path, *args: str) -> str:
    result = _run_raw(["git", "-C", str(repo), *args], text=True)
    if result.returncode != 0:
        raise ReviewWorkspaceError(f"git {' '.join(args)}に失敗した: {result.stderr.strip()}")
    return result.stdout.strip()


def _run(command: list[str], message: str) -> None:
    result = _run_raw(command)
    if result.returncode != 0:
        stderr = result.stderr.decode("utf-8", errors="replace").strip()
        raise ReviewWorkspaceError(f"{message}: {stderr}")


@typing.overload
def _run_raw(command: list[str], *, text: typing.Literal[False] = False) -> subprocess.CompletedProcess[bytes]: ...


@typing.overload
def _run_raw(command: list[str], *, text: typing.Literal[True]) -> subprocess.CompletedProcess[str]: ...


def _run_raw(
    command: list[str],
    *,
    text: bool = False,
) -> subprocess.CompletedProcess[bytes] | subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=text,
            timeout=_COMMAND_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ReviewWorkspaceError(f"コマンドを実行できない: {' '.join(command)}: {error}") from error


def _resolve_repo(path_arg: pathlib.Path) -> pathlib.Path:
    repo = _resolve_existing_dir(path_arg, "対象リポジトリ")
    top_level = pathlib.Path(_git_text(repo, "rev-parse", "--show-toplevel")).resolve()
    if top_level != repo:
        raise ReviewWorkspaceError(f"対象リポジトリ直下を指定する: {path_arg}; resolved={top_level}")
    return repo


def _resolve_new_dir(path_arg: pathlib.Path, protected_repos: set[pathlib.Path]) -> pathlib.Path:
    if not path_arg.is_absolute():
        raise ReviewWorkspaceError(f"出力先は絶対パスで指定する: {path_arg}")
    if path_arg.exists() or path_arg.is_symlink():
        raise ReviewWorkspaceError(f"出力先が既に存在する: {path_arg}")
    try:
        parent = path_arg.parent.resolve(strict=True)
    except OSError as error:
        raise ReviewWorkspaceError(f"出力先の親を解決できない: {path_arg}: {error}") from error
    output_dir = parent / path_arg.name
    if any(output_dir == repo or repo in output_dir.parents for repo in protected_repos):
        raise ReviewWorkspaceError(f"出力先を検査対象リポジトリ内には作成できない: {output_dir}")
    return output_dir


def _resolve_file(path_arg: pathlib.Path, label: str) -> pathlib.Path:
    if not path_arg.is_absolute():
        raise ReviewWorkspaceError(f"{label}は絶対パスで指定する: {path_arg}")
    try:
        path = path_arg.resolve(strict=True)
    except OSError as error:
        raise ReviewWorkspaceError(f"{label}を解決できない: {path_arg}: {error}") from error
    if not path.is_file():
        raise ReviewWorkspaceError(f"{label}は通常ファイルではない: {path}")
    return path


def _resolve_existing_dir(path_arg: pathlib.Path, label: str) -> pathlib.Path:
    if not path_arg.is_absolute():
        raise ReviewWorkspaceError(f"{label}は絶対パスで指定する: {path_arg}")
    try:
        path = path_arg.resolve(strict=True)
    except OSError as error:
        raise ReviewWorkspaceError(f"{label}を解決できない: {path_arg}: {error}") from error
    if not path.is_dir():
        raise ReviewWorkspaceError(f"{label}はディレクトリではない: {path}")
    return path


def _load_workspace_manifest(workspace_dir: pathlib.Path) -> dict[str, typing.Any]:
    manifest = _load_json_object(workspace_dir / _MANIFEST_NAME, "レビュー作業領域")
    required = {
        "format_version",
        "source_repo",
        "source_head",
        "plan_file",
        "plan_sha256",
        "source_snapshot",
        "conditional_source_repo",
        "conditional_source_snapshot",
        "review_repo",
        "review_snapshot",
        "review_files",
        "original_plan",
        "review_plan",
        "plan_diff",
        "reproduction_dir",
    }
    if set(manifest) != required or manifest.get("format_version") != _FORMAT_VERSION:
        raise ReviewWorkspaceError("レビュー作業領域のmanifest形式が不正")
    digest = manifest.get("plan_sha256")
    if not isinstance(digest, str) or len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
        raise ReviewWorkspaceError("レビュー作業領域の計画digest形式が不正")
    path_fields = required - {
        "format_version",
        "source_head",
        "plan_sha256",
        "conditional_source_repo",
        "conditional_source_snapshot",
        "review_files",
    }
    for field in path_fields:
        if not isinstance(manifest[field], str) or not pathlib.Path(manifest[field]).is_absolute():
            raise ReviewWorkspaceError(f"レビュー作業領域のパス形式が不正: {field}")
    conditional_values = (manifest["conditional_source_repo"], manifest["conditional_source_snapshot"])
    if all(value is None for value in conditional_values):
        pass
    elif not all(isinstance(value, str) and pathlib.Path(value).is_absolute() for value in conditional_values):
        raise ReviewWorkspaceError("レビュー作業領域の条件付き複製元形式が不正")
    _inventory_by_path(manifest["review_files"], "基準")
    return manifest


def _workspace_path(
    workspace_dir: pathlib.Path,
    manifest: dict[str, typing.Any],
    field: str,
    *,
    must_exist: bool = True,
) -> pathlib.Path:
    path = pathlib.Path(manifest[field])
    try:
        resolved = path.resolve(strict=must_exist)
    except OSError as error:
        raise ReviewWorkspaceError(f"レビュー作業領域のパスを解決できない: {field}: {error}") from error
    if resolved != workspace_dir and workspace_dir not in resolved.parents:
        raise ReviewWorkspaceError(f"レビュー作業領域外のパスが指定されている: {field}")
    return resolved


def _load_json_object(path: pathlib.Path, label: str) -> dict[str, typing.Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ReviewWorkspaceError(f"{label}のJSONを読み込めない: {path}: {error}") from error
    if not isinstance(value, dict):
        raise ReviewWorkspaceError(f"{label}のJSONがオブジェクトではない: {path}")
    return value


def _read_bytes(path: pathlib.Path, label: str) -> bytes:
    try:
        return path.read_bytes()
    except OSError as error:
        raise ReviewWorkspaceError(f"{label}を読み込めない: {path}: {error}") from error


def _write_private(path: pathlib.Path, content: bytes, *, mode: int = 0o600) -> None:
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), mode)
        with os.fdopen(descriptor, "wb") as output:
            output.write(content)
            if _USE_POSIX_MODE:
                os.fchmod(output.fileno(), mode)
    except OSError as error:
        raise ReviewWorkspaceError(f"ファイルを書き込めない: {path}: {error}") from error


def _write_or_replace_private(path: pathlib.Path, content: bytes) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    if temporary.exists() or temporary.is_symlink():
        raise ReviewWorkspaceError(f"一時出力先が既に存在する: {temporary}")
    _write_private(temporary, content)
    try:
        temporary.replace(path)
    except OSError as error:
        raise ReviewWorkspaceError(f"差分ファイルを更新できない: {path}: {error}") from error


if __name__ == "__main__":
    main()
