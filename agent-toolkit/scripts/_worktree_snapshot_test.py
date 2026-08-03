"""Git作業ツリー退避CLIの公開契約を検証する。"""

from __future__ import annotations

import json
import os
import pathlib
import shutil
import stat
import subprocess
import sys
import time

import pytest

_SCRIPT = pathlib.Path(__file__).with_name("_worktree_snapshot.py")

_CAPTURE_WRAPPER = """
import os
import pathlib
import sys
import time

sys.path.insert(0, os.environ["SCRIPT_DIR"])
import _worktree_snapshot as ws

_original = ws._read_untracked_file
_state = {"first": True}
_BARRIER_TIMEOUT_SECONDS = 5.0


def _patched(path, expected):
    if _state["first"]:
        _state["first"] = False
        pathlib.Path(os.environ["READY"]).write_text("1", encoding="utf-8")
        deadline = time.monotonic() + _BARRIER_TIMEOUT_SECONDS
        while not pathlib.Path(os.environ["DONE"]).exists():
            if time.monotonic() >= deadline:
                raise TimeoutError("同期障壁の応答待ちが期限を超過した")
            time.sleep(0.001)
    return _original(path, expected)


ws._read_untracked_file = _patched
sys.argv = ["_worktree_snapshot.py", *sys.argv[1:]]
ws.main()
"""


def _terminate_capture(capture: subprocess.Popen[str]) -> tuple[str, str]:
    """期限を超過した子プロセスを終了し、標準出力と標準エラーを回収する。"""
    capture.terminate()
    try:
        return capture.communicate(timeout=5)
    except subprocess.TimeoutExpired:
        capture.kill()
        return capture.communicate(timeout=5)


def _run(*args: object, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(_SCRIPT), *(str(arg) for arg in args)],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )


def _repo(tmp_path: pathlib.Path) -> pathlib.Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "test@example.invalid"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "test"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "commit.gpgsign", "false"], check=True)
    (repo / "tracked.bin").write_bytes(b"base\x00content\n")
    subprocess.run(["git", "-C", str(repo), "add", "tracked.bin"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "initial"], check=True)
    return repo


def _capture(repo: pathlib.Path, snapshot: pathlib.Path) -> subprocess.CompletedProcess[str]:
    return _run("capture", "--repo", repo, "--output-dir", snapshot)


def _compare(repo: pathlib.Path, snapshot: pathlib.Path) -> subprocess.CompletedProcess[str]:
    return _run("compare", "--repo", repo, "--snapshot-dir", snapshot)


def test_clean_and_unchanged_dirty_repositories_match(tmp_path: pathlib.Path) -> None:
    repo = _repo(tmp_path)
    clean_snapshot = tmp_path / "clean-snapshot"
    assert _capture(repo, clean_snapshot).returncode == 0
    assert _compare(repo, clean_snapshot).returncode == 0

    (repo / "tracked.bin").write_bytes(b"dirty\x00content\n")
    dirty_snapshot = tmp_path / "dirty-snapshot"
    assert _capture(repo, dirty_snapshot).returncode == 0
    assert _compare(repo, dirty_snapshot).returncode == 0
    assert stat.S_IMODE(dirty_snapshot.stat().st_mode) == 0o700
    assert stat.S_IMODE((dirty_snapshot / "manifest.json").stat().st_mode) == 0o600
    assert (dirty_snapshot / "tracked.patch").read_bytes().startswith(b"diff --git")


def test_snapshot_patch_ignores_textconv_and_remains_applicable(tmp_path: pathlib.Path) -> None:
    repo = _repo(tmp_path)
    (repo / ".gitattributes").write_text("tracked.bin diff=convert\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", ".gitattributes"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "attributes"], check=True)
    converter = tmp_path / "converter"
    converter.write_text("#!/bin/sh\nprintf 'converted output\\n'\n", encoding="utf-8")
    converter.chmod(0o755)
    subprocess.run(["git", "-C", str(repo), "config", "diff.convert.textconv", str(converter)], check=True)
    (repo / "tracked.bin").write_bytes(os.urandom(1024))
    snapshot = tmp_path / "snapshot"

    assert _capture(repo, snapshot).returncode == 0
    patch = (snapshot / "tracked.patch").read_bytes()
    assert b"GIT binary patch" in patch
    assert b"converted output" not in patch

    (repo / "tracked.bin").write_bytes(b"delegated")
    manifest = json.loads((snapshot / "manifest.json").read_text(encoding="utf-8"))
    subprocess.run(["git", "-C", str(repo), "reset", manifest["head"], "--", "tracked.bin"], check=True)
    (repo / "tracked.bin").unlink()
    subprocess.run(["git", "-C", str(repo), "checkout", manifest["head"], "--", "tracked.bin"], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "apply", "--index", "--binary", str(snapshot / "tracked.patch")],
        check=True,
    )
    assert _compare(repo, snapshot).returncode == 0


def test_unchanged_untracked_file_and_symlink_match(tmp_path: pathlib.Path) -> None:
    repo = _repo(tmp_path)
    untracked = repo / "untracked.bin"
    untracked.write_bytes(b"unchanged\x00bytes")
    untracked.chmod(0o740)
    (repo / "link").symlink_to("untracked.bin")
    snapshot = tmp_path / "snapshot"
    assert _capture(repo, snapshot).returncode == 0
    assert _compare(repo, snapshot).returncode == 0


def test_compare_detects_tracked_and_untracked_content_changes(tmp_path: pathlib.Path) -> None:
    repo = _repo(tmp_path)
    tracked = repo / "tracked.bin"
    tracked.write_bytes(b"first dirty\x00\n")
    existing = repo / "existing.bin"
    existing.write_bytes(b"original\x00bytes")
    existing.chmod(0o740)
    snapshot = tmp_path / "snapshot"
    assert _capture(repo, snapshot).returncode == 0

    tracked.write_bytes(b"second dirty\x00\n")
    existing.write_bytes(b"changed")
    added = repo / "added.txt"
    added.write_text("added", encoding="utf-8")
    result = _compare(repo, snapshot)
    assert result.returncode == 1
    assert "tracked.bin" in result.stderr
    assert "existing.bin" in result.stderr
    assert "added.txt" in result.stderr
    assert "git -C" in result.stderr
    assert "install -m 0740" in result.stderr
    assert "確認後に除去する対象" in result.stderr


def test_snapshot_preserves_deleted_untracked_bytes_and_symlink_target(tmp_path: pathlib.Path) -> None:
    repo = _repo(tmp_path)
    regular = repo / "untracked.bin"
    regular.write_bytes(b"\x00original\xff")
    regular.chmod(0o751)
    link = repo / "untracked-link"
    link.symlink_to("missing-target")
    snapshot = tmp_path / "snapshot"
    assert _capture(repo, snapshot).returncode == 0

    regular.unlink()
    link.unlink()
    result = _compare(repo, snapshot)
    assert result.returncode == 1
    manifest = json.loads((snapshot / "manifest.json").read_text(encoding="utf-8"))
    entries = {entry["path"]: entry for entry in manifest["untracked"]}
    regular_entry = entries["untracked.bin"]
    blob = snapshot / "blobs" / regular_entry["blob"]
    assert blob.read_bytes() == b"\x00original\xff"
    assert regular_entry["mode"] == 0o751
    assert entries["untracked-link"]["target"] == "missing-target"
    assert "ln -s missing-target" in result.stderr


def test_compare_detects_untracked_addition_and_deletion(tmp_path: pathlib.Path) -> None:
    repo = _repo(tmp_path)
    deleted = repo / "deleted.txt"
    deleted.write_text("before", encoding="utf-8")
    snapshot = tmp_path / "snapshot"
    assert _capture(repo, snapshot).returncode == 0
    deleted.unlink()
    (repo / "added.txt").write_text("after", encoding="utf-8")

    result = _compare(repo, snapshot)
    assert result.returncode == 1
    assert "deleted.txt" in result.stderr
    assert "added.txt" in result.stderr


def test_compare_detects_head_change(tmp_path: pathlib.Path) -> None:
    repo = _repo(tmp_path)
    snapshot = tmp_path / "snapshot"
    assert _capture(repo, snapshot).returncode == 0
    (repo / "tracked.bin").write_bytes(b"next commit")
    subprocess.run(["git", "-C", str(repo), "add", "tracked.bin"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "next"], check=True)

    result = _compare(repo, snapshot)
    assert result.returncode == 1
    assert "HEAD:" in result.stderr
    assert "退避用ブランチへ保全" in result.stderr
    assert "tracked.bin" in result.stderr


def test_compare_detects_symlink_target_change_without_reading_target(tmp_path: pathlib.Path) -> None:
    repo = _repo(tmp_path)
    outside = tmp_path / "outside-secret"
    outside.write_text("do not copy", encoding="utf-8")
    link = repo / "link"
    link.symlink_to(outside)
    snapshot = tmp_path / "snapshot"
    assert _capture(repo, snapshot).returncode == 0
    link.unlink()
    link.symlink_to("different-target")

    result = _compare(repo, snapshot)
    assert result.returncode == 1
    manifest_text = (snapshot / "manifest.json").read_text(encoding="utf-8")
    assert str(outside) in manifest_text
    assert "do not copy" not in manifest_text
    assert not any(path.read_bytes() == b"do not copy" for path in (snapshot / "blobs").iterdir())
    assert "未追跡パスの競合を確認後に除去する" in result.stderr
    assert "mkdir -p" in result.stderr


def test_reported_recovery_strategy_restores_added_tracked_and_type_changed_paths(tmp_path: pathlib.Path) -> None:
    repo = _repo(tmp_path)
    tracked = repo / "tracked.bin"
    tracked.write_bytes(b"baseline dirty\x00")
    regular = repo / "untracked.bin"
    regular.write_bytes(b"original\x00bytes")
    regular.chmod(0o740)
    link = repo / "untracked-link"
    link.symlink_to("untracked.bin")
    snapshot = tmp_path / "snapshot"
    assert _capture(repo, snapshot).returncode == 0

    tracked.write_bytes(b"delegated change")
    added = repo / "added.txt"
    added.write_text("staged addition", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "added.txt"], check=True)
    regular.unlink()
    regular.mkdir()
    link.unlink()
    link.write_text("type changed", encoding="utf-8")

    result = _compare(repo, snapshot)
    assert result.returncode == 1
    assert "追加追跡パスの除去対象" in result.stderr
    assert "git -C" in result.stderr
    assert " reset " in result.stderr

    manifest = json.loads((snapshot / "manifest.json").read_text(encoding="utf-8"))
    head = manifest["head"]
    subprocess.run(["git", "-C", str(repo), "reset", head, "--", "tracked.bin", "added.txt"], check=True)
    tracked.unlink()
    added.unlink()
    subprocess.run(["git", "-C", str(repo), "checkout", head, "--", "tracked.bin"], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "apply", "--index", "--binary", str(snapshot / "tracked.patch")],
        check=True,
    )

    shutil.rmtree(regular)
    link.unlink()
    entries = {entry["path"]: entry for entry in manifest["untracked"]}
    regular_entry = entries["untracked.bin"]
    shutil.copyfile(snapshot / "blobs" / regular_entry["blob"], regular)
    regular.chmod(regular_entry["mode"])
    link.symlink_to(entries["untracked-link"]["target"])
    assert _compare(repo, snapshot).returncode == 0


def test_reported_recovery_strategy_restores_only_added_tracked_path(tmp_path: pathlib.Path) -> None:
    repo = _repo(tmp_path)
    added = repo / "added.txt"
    added.write_text("baseline addition", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "added.txt"], check=True)
    snapshot = tmp_path / "snapshot"
    assert _capture(repo, snapshot).returncode == 0

    added.write_text("delegated change", encoding="utf-8")
    result = _compare(repo, snapshot)
    assert result.returncode == 1
    assert "退避パッチを再適用する" in result.stderr

    manifest = json.loads((snapshot / "manifest.json").read_text(encoding="utf-8"))
    subprocess.run(["git", "-C", str(repo), "reset", manifest["head"], "--", "added.txt"], check=True)
    added.unlink()
    subprocess.run(
        ["git", "-C", str(repo), "apply", "--index", "--binary", str(snapshot / "tracked.patch")],
        check=True,
    )
    assert _compare(repo, snapshot).returncode == 0


def test_recovery_removes_conflicting_parent_symlink_before_child_restore(tmp_path: pathlib.Path) -> None:
    repo = _repo(tmp_path)
    nested = repo / "dir" / "file.txt"
    nested.parent.mkdir()
    nested.write_text("baseline", encoding="utf-8")
    snapshot = tmp_path / "snapshot"
    assert _capture(repo, snapshot).returncode == 0

    shutil.rmtree(nested.parent)
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "file.txt"
    sentinel.write_text("outside sentinel", encoding="utf-8")
    nested.parent.symlink_to(outside, target_is_directory=True)

    result = _compare(repo, snapshot)
    assert result.returncode == 1
    ancestor_message = "子パス復元前に競合する祖先パス"
    child_message = "未追跡パスの競合を確認後に除去する"
    assert result.stderr.index(ancestor_message) < result.stderr.index(child_message)

    nested.parent.unlink()
    nested.parent.mkdir()
    manifest = json.loads((snapshot / "manifest.json").read_text(encoding="utf-8"))
    entry = next(item for item in manifest["untracked"] if item["path"] == "dir/file.txt")
    shutil.copyfile(snapshot / "blobs" / entry["blob"], nested)
    nested.chmod(entry["mode"])
    assert sentinel.read_text(encoding="utf-8") == "outside sentinel"
    assert _compare(repo, snapshot).returncode == 0


def test_capture_rejects_output_inside_worktree(tmp_path: pathlib.Path) -> None:
    repo = _repo(tmp_path)
    result = _capture(repo, repo / "snapshot")
    assert result.returncode == 2
    assert "worktree内" in result.stderr
    assert not (repo / "snapshot").exists()


def test_capture_rejects_repository_change_during_collection(tmp_path: pathlib.Path) -> None:
    repo = _repo(tmp_path)
    shim_dir = tmp_path / "shim"
    shim_dir.mkdir()
    shim = shim_dir / "git"
    shim.write_text(
        """#!/usr/bin/env python3
import os
import pathlib
import subprocess
import sys

args = sys.argv[1:]
result = subprocess.run([os.environ["REAL_GIT"], *args], check=False, capture_output=True)
marker = pathlib.Path(os.environ["MUTATION_MARKER"])
if "--name-only" in args and not marker.exists():
    repo = pathlib.Path(args[args.index("-C") + 1])
    (repo / "tracked.bin").write_bytes(b"changed during capture")
    marker.write_text("done", encoding="utf-8")
sys.stdout.buffer.write(result.stdout)
sys.stderr.buffer.write(result.stderr)
raise SystemExit(result.returncode)
""",
        encoding="utf-8",
    )
    shim.chmod(0o755)
    real_git = shutil.which("git")
    assert real_git is not None
    env = {
        **os.environ,
        "PATH": f"{shim_dir}{os.pathsep}{os.environ['PATH']}",
        "REAL_GIT": real_git,
        "MUTATION_MARKER": str(tmp_path / "mutated"),
    }
    result = _run("capture", "--repo", repo, "--output-dir", tmp_path / "snapshot", env=env)
    assert result.returncode == 2
    assert "取得中に変化" in result.stderr


def test_compare_rejects_repository_change_during_collection(tmp_path: pathlib.Path) -> None:
    repo = _repo(tmp_path)
    snapshot = tmp_path / "snapshot"
    assert _capture(repo, snapshot).returncode == 0
    shim_dir = tmp_path / "shim"
    shim_dir.mkdir()
    shim = shim_dir / "git"
    shim.write_text(
        """#!/usr/bin/env python3
import os
import pathlib
import subprocess
import sys

args = sys.argv[1:]
result = subprocess.run([os.environ["REAL_GIT"], *args], check=False, capture_output=True)
marker = pathlib.Path(os.environ["MUTATION_MARKER"])
if args[-2:] == ["rev-parse", "HEAD"] and not marker.exists():
    repo = pathlib.Path(args[args.index("-C") + 1])
    (repo / "tracked.bin").write_bytes(b"committed during compare")
    subprocess.run([os.environ["REAL_GIT"], "-C", str(repo), "add", "tracked.bin"], check=True)
    subprocess.run([os.environ["REAL_GIT"], "-C", str(repo), "commit", "-q", "-m", "concurrent"], check=True)
    marker.write_text("done", encoding="utf-8")
sys.stdout.buffer.write(result.stdout)
sys.stderr.buffer.write(result.stderr)
raise SystemExit(result.returncode)
""",
        encoding="utf-8",
    )
    shim.chmod(0o755)
    real_git = shutil.which("git")
    assert real_git is not None
    env = {
        **os.environ,
        "PATH": f"{shim_dir}{os.pathsep}{os.environ['PATH']}",
        "REAL_GIT": real_git,
        "MUTATION_MARKER": str(tmp_path / "mutated"),
    }
    result = _run("compare", "--repo", repo, "--snapshot-dir", snapshot, env=env)
    assert result.returncode == 2
    assert "比較対象が取得中に変化" in result.stderr


@pytest.mark.parametrize("replacement", ["symlink", "deleted"])
def test_capture_rejects_untracked_file_change_during_public_workflow(
    tmp_path: pathlib.Path,
    replacement: str,
) -> None:
    repo = _repo(tmp_path)
    untracked = repo / "untracked.bin"
    untracked.write_bytes(b"original")
    outside = tmp_path / "outside-secret"
    outside.write_bytes(b"must not be captured")
    shim_dir = tmp_path / "shim"
    shim_dir.mkdir()
    shim = shim_dir / "git"
    shim.write_text(
        """#!/usr/bin/env python3
import os
import pathlib
import subprocess
import sys

args = sys.argv[1:]
result = subprocess.run([os.environ["REAL_GIT"], *args], check=False, capture_output=True)
marker = pathlib.Path(os.environ["MUTATION_MARKER"])
if "ls-files" in args:
    count = int(marker.read_text(encoding="utf-8")) + 1 if marker.exists() else 1
    marker.write_text(str(count), encoding="utf-8")
    if count == 2:
        target = pathlib.Path(os.environ["UNTRACKED_PATH"])
        target.unlink()
        if os.environ["REPLACEMENT"] == "symlink":
            target.symlink_to(os.environ["OUTSIDE_PATH"])
sys.stdout.buffer.write(result.stdout)
sys.stderr.buffer.write(result.stderr)
raise SystemExit(result.returncode)
""",
        encoding="utf-8",
    )
    shim.chmod(0o755)
    real_git = shutil.which("git")
    assert real_git is not None
    env = {
        **os.environ,
        "PATH": f"{shim_dir}{os.pathsep}{os.environ['PATH']}",
        "REAL_GIT": real_git,
        "MUTATION_MARKER": str(tmp_path / "mutation-count"),
        "UNTRACKED_PATH": str(untracked),
        "REPLACEMENT": replacement,
        "OUTSIDE_PATH": str(outside),
    }

    snapshot = tmp_path / "snapshot"
    result = _run("capture", "--repo", repo, "--output-dir", snapshot, env=env)
    assert result.returncode == 2
    assert result.stderr.startswith("error:")
    assert not snapshot.exists()


@pytest.mark.parametrize("replacement", ["symlink", "deleted"])
def test_capture_rejects_untracked_change_during_blob_materialization(
    tmp_path: pathlib.Path,
    replacement: str,
) -> None:
    repo = _repo(tmp_path)
    untracked = repo / "untracked.bin"
    untracked.write_bytes(b"original")
    outside = tmp_path / "outside-secret"
    outside.write_bytes(b"must not be captured")
    snapshot = tmp_path / "snapshot"
    ready = tmp_path / "ready"
    done = tmp_path / "done"
    with subprocess.Popen(
        [sys.executable, "-c", _CAPTURE_WRAPPER, "capture", "--repo", str(repo), "--output-dir", str(snapshot)],
        env={
            **os.environ,
            "SCRIPT_DIR": str(_SCRIPT.parent),
            "READY": str(ready),
            "DONE": str(done),
        },
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    ) as capture:
        ready_deadline = time.monotonic() + 5
        while not ready.exists():
            if capture.poll() is not None:
                stdout, stderr = capture.communicate()
                pytest.fail(f"準備完了前に子プロセスが終了した: {stdout=} {stderr=}")
            if time.monotonic() >= ready_deadline:
                stdout, stderr = _terminate_capture(capture)
                pytest.fail(f"準備完了待ちが期限を超過した: {stdout=} {stderr=}")
            time.sleep(0.001)
        untracked.unlink()
        if replacement == "symlink":
            untracked.symlink_to(outside)
        done.write_text("1", encoding="utf-8")
        try:
            stdout, stderr = capture.communicate(timeout=30)
        except subprocess.TimeoutExpired:
            stdout, stderr = _terminate_capture(capture)
            pytest.fail(f"同期障壁の応答後も子プロセスが終了しなかった: {stdout=} {stderr=}")

    assert capture.returncode == 2
    assert "退避中に未追跡ファイル" in stderr
    assert "Traceback" not in stderr
    blobs = snapshot / "blobs"
    assert not blobs.exists() or not any(path.read_bytes() == outside.read_bytes() for path in blobs.iterdir())


def test_git_start_failure_is_reported_as_snapshot_error(tmp_path: pathlib.Path) -> None:
    repo = _repo(tmp_path)
    empty_path = tmp_path / "empty-path"
    empty_path.mkdir()
    result = _run(
        "capture",
        "--repo",
        repo,
        "--output-dir",
        tmp_path / "snapshot",
        env={**os.environ, "PATH": str(empty_path)},
    )
    assert result.returncode == 2
    assert "gitコマンドを実行できない" in result.stderr
    assert "Traceback" not in result.stderr


def test_capture_rejects_relative_paths(tmp_path: pathlib.Path) -> None:
    _repo(tmp_path)
    result = _run("capture", "--repo", "relative", "--output-dir", tmp_path / "snapshot")
    assert result.returncode == 2
    assert "絶対パス" in result.stderr


def test_compare_rejects_invalid_snapshot(tmp_path: pathlib.Path) -> None:
    repo = _repo(tmp_path)
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()
    (snapshot / "manifest.json").write_text('{"format_version": 99}', encoding="utf-8")
    result = _compare(repo, snapshot)
    assert result.returncode == 2
    assert "manifest形式が不正" in result.stderr


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("repo", 1, "リポジトリ形式"),
        ("head", 1, "HEAD形式"),
        ("head", "not-an-object-id", "HEAD形式"),
        ("tracked_patch_sha256", "invalid", "追跡パッチdigest形式"),
    ],
)
def test_compare_rejects_invalid_manifest_scalar_fields(
    tmp_path: pathlib.Path,
    field: str,
    value: object,
    message: str,
) -> None:
    repo = _repo(tmp_path)
    snapshot = tmp_path / "snapshot"
    assert _capture(repo, snapshot).returncode == 0
    manifest_path = snapshot / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest[field] = value
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    result = _compare(repo, snapshot)

    assert result.returncode == 2
    assert message in result.stderr


def test_compare_rejects_corrupted_untracked_blob(tmp_path: pathlib.Path) -> None:
    repo = _repo(tmp_path)
    (repo / "untracked.bin").write_bytes(b"original")
    snapshot = tmp_path / "snapshot"
    assert _capture(repo, snapshot).returncode == 0
    blob = next((snapshot / "blobs").iterdir())
    blob.write_bytes(b"corrupted")
    result = _compare(repo, snapshot)
    assert result.returncode == 2
    assert "blobが退避後に変化" in result.stderr


def test_manifest_rejects_parent_traversal_path(tmp_path: pathlib.Path) -> None:
    repo = _repo(tmp_path)
    snapshot = tmp_path / "snapshot"
    assert _capture(repo, snapshot).returncode == 0
    manifest_path = snapshot / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["tracked_paths"] = ["../outside"]
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    result = _compare(repo, snapshot)
    assert result.returncode == 2
    assert "不正な相対パス" in result.stderr


@pytest.mark.parametrize("mode", [0o600, 0o755])
def test_untracked_mode_change_is_detected(tmp_path: pathlib.Path, mode: int) -> None:
    repo = _repo(tmp_path)
    untracked = repo / "mode.txt"
    untracked.write_text("same", encoding="utf-8")
    untracked.chmod(mode)
    snapshot = tmp_path / "snapshot"
    assert _capture(repo, snapshot).returncode == 0
    untracked.chmod(0o700 if mode == 0o600 else 0o600)
    assert _compare(repo, snapshot).returncode == 1
