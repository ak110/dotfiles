"""公開mainからレポートとCSV履歴の結果を検証する。"""

import contextlib
import csv
import json
import os
import pathlib
import re
import shlex
import stat
import subprocess
import sys
import types
from collections.abc import Callable, Generator
from typing import Any

import pytest

from pytools import dirsize
from pytools._internal import dirsize_report


@pytest.fixture(autouse=True)
def _isolate_uv(monkeypatch: pytest.MonkeyPatch) -> None:
    def absent(*args: Any, **kwargs: Any) -> Any:
        raise FileNotFoundError("uv test double: absent")

    monkeypatch.setattr(dirsize_report, "process_scope", absent)
    for name in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial"):
        monkeypatch.delenv(name, raising=False)


def run_report(monkeypatch: pytest.MonkeyPatch, target: pathlib.Path, output: pathlib.Path, *extra: str, code: int = 0) -> dict:
    """公開mainを指定引数で呼び、保存されたレポートを返す。"""
    monkeypatch.setattr(sys, "argv", ["dirsize", "--report-dir", str(output), *extra, str(target)])
    with pytest.raises(SystemExit) as exc:
        dirsize.main()
    assert exc.value.code == code
    return _data(output)


def _data(output: pathlib.Path) -> dict:
    html = (output / "report.html").read_text(encoding="utf-8")
    match = re.search(r'<script id="report-data" type="application/json">(.*?)</script>', html)
    assert match is not None
    return json.loads(match[1])


def mock_uv(monkeypatch: pytest.MonkeyPatch, root: pathlib.Path) -> list[list[str]]:
    """uv照会だけを指定ルートへ差し替え、呼び出しを記録する。"""
    calls: list[list[str]] = []

    def run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        del kwargs
        calls.append(command)
        assert command == ["uv", "cache", "dir", "--no-config"]
        return subprocess.CompletedProcess(command, 0, str(root) + "\n", "")

    mock_uv_command(monkeypatch, run)
    return calls


def mock_uv_command(monkeypatch: pytest.MonkeyPatch, run: Callable[..., subprocess.CompletedProcess[str]]) -> None:
    """uv照会の結果・例外を公開CLIとブラウザーテストで共有する。"""

    @contextlib.contextmanager
    def scope(command: list[str], **kwargs: Any) -> Generator[types.SimpleNamespace]:
        result = run(command, **kwargs)

        def communicate(*, timeout: int) -> tuple[str, str]:
            assert timeout == 15
            return result.stdout, result.stderr

        yield types.SimpleNamespace(returncode=result.returncode, communicate=communicate)

    monkeypatch.setattr(dirsize_report, "process_scope", scope)


def test_report_initial_changes_conditions_and_output_exclusion(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "target"
    target.mkdir()
    (target / "sub").mkdir()
    (target / "sub" / "a").write_bytes(b"a" * 11)
    (target / "b").write_bytes(b"b" * 7)
    output = target / "report"
    first = run_report(monkeypatch, target, output)
    assert first["nodes"][0]["size"] == 18
    assert not first["previous"]
    assert all(n["delta"] is None for n in first["nodes"])
    (target / "sub" / "a").write_bytes(b"a" * 20)
    (target / "b").unlink()
    second = run_report(monkeypatch, target, output)
    assert second["nodes"][0]["size"] == 20
    assert second["nodes"][0]["delta"] == 2
    assert next(n for n in second["nodes"] if n["path"] == str(target / "sub"))["delta"] == 9
    assert next(n for n in second["nodes"] if n["path"] == str(output))["status"] == "excluded"
    assert (output / "history.csv").read_bytes().startswith(b"\xef\xbb\xbf")
    with (output / "history.csv").open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    assert rows[-len(second["nodes"])]["size"] == "20"
    changed = run_report(monkeypatch, target, output, "--exclude", "sub")
    assert changed["nodes"][0]["size"] == 0
    assert not changed["previous"]
    other = tmp_path / "other-target"
    other.mkdir()
    assert not run_report(monkeypatch, other, output)["previous"]
    restored = run_report(monkeypatch, target, output)
    assert restored["nodes"][0]["delta"] == 0


def test_default_cloud_and_explicit_exclusions(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(pathlib.Path, "home", classmethod(lambda cls: home))
    names = ["Pictures", "Photos", "OneDrive", "Dropbox", "iCloudDrive", "custom", "personal", "business", "renamed"]
    for name in names:
        (home / name).mkdir()
        (home / name / "data").write_bytes(b"content")
    for key, name in zip(("OneDrive", "OneDriveConsumer", "OneDriveCommercial"), names[5:8], strict=True):
        monkeypatch.setenv(key, str(home / name))
    data = run_report(monkeypatch, home, tmp_path / "out", "--exclude", "renamed")
    assert data["nodes"][0]["size"] == 0
    assert {n["path"] for n in data["nodes"][1:]} == {str(home / n) for n in names}
    assert all(n["status"] == "excluded" for n in data["nodes"][1:])
    (home / "ordinary").mkdir()
    (home / "ordinary" / "data").write_bytes(b"123")
    assert run_report(monkeypatch, home, tmp_path / "out", "--exclude", "renamed")["nodes"][0]["size"] == 3


@pytest.mark.parametrize("bad", ["absent", "\x00"])
def test_invalid_exclude_fails_before_scan(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, bad: str) -> None:
    def forbidden(*args: Any, **kwargs: Any) -> Any:
        del args, kwargs
        pytest.fail("不正入力では走査を開始しない")

    monkeypatch.setattr(sys, "argv", ["dirsize", "--report-dir", str(tmp_path / "out"), "--exclude", bad, str(tmp_path)])
    with monkeypatch.context() as scan_patch:
        scan_patch.setattr(dirsize_report.os, "scandir", forbidden)
        with pytest.raises(SystemExit) as exc:
            dirsize.main()
    assert exc.value.code == 1
    assert not (tmp_path / "out").exists()


def test_cycle_links_not_followed(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "target"
    target.mkdir()
    (target / "data").write_bytes(b"12345")
    try:
        (target / "cycle").symlink_to(target, target_is_directory=True)
        (target / "linked-file").symlink_to(target / "data")
    except OSError as exc:
        pytest.skip(f"実リンク作成権限なし: {exc}")
    data = run_report(monkeypatch, target, tmp_path / "out")
    assert data["nodes"][0]["size"] == 5
    assert len(data["nodes"]) == 4
    assert sum(n["status"] == "excluded" for n in data["nodes"]) == 2


@pytest.mark.parametrize("boundary", ["missing", "unavailable", "device", "reparse"])
def test_scan_boundaries_and_partial_history(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    boundary: str,
) -> None:
    target = tmp_path / "target"
    target.mkdir()
    (target / "good").write_bytes(b"123")
    sub = target / "uv"
    sub.mkdir()
    (sub / "bad").write_bytes(b"1234567")
    mock_uv(monkeypatch, sub)
    output = tmp_path / "out"
    run_report(monkeypatch, target, output)
    original = pathlib.Path.lstat

    def lstat(path: pathlib.Path, **kwargs: Any) -> Any:
        metadata = original(path, **kwargs)
        if path != sub / "bad":
            return metadata
        if boundary == "missing":
            raise FileNotFoundError("走査中の消失")
        if boundary == "unavailable":
            raise PermissionError("取得不能")
        return types.SimpleNamespace(
            st_mode=metadata.st_mode,
            st_size=metadata.st_size,
            st_dev=metadata.st_dev + (1 if boundary == "device" else 0),
            st_file_attributes=stat.FILE_ATTRIBUTE_REPARSE_POINT if boundary == "reparse" else 0,
        )

    monkeypatch.setattr(pathlib.Path, "lstat", lstat)
    partial = boundary in {"missing", "unavailable"}
    data = run_report(monkeypatch, target, output, code=int(partial))
    assert data["nodes"][0]["size"] == 3
    bad = next(n for n in data["nodes"] if n["path"] == str(sub / "bad"))
    assert bad["status"] == boundary if partial else bad["status"] == "excluded"
    assert bad["size"] is None
    assert data["candidate"] is None
    if partial:
        assert all(n["delta"] is None for n in data["nodes"])
        monkeypatch.setattr(pathlib.Path, "lstat", original)
        (target / "good").write_bytes(b"1234")
        restored = run_report(monkeypatch, target, output)
        assert restored["nodes"][0]["delta"] == 1


def test_scandir_failure_is_partial(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "target"
    target.mkdir()
    sub = target / "locked"
    sub.mkdir()
    original = os.scandir

    def scandir(path: pathlib.Path, **kwargs: Any) -> Any:
        if path == sub:
            raise PermissionError("アクセス不可")
        return original(path, **kwargs)

    monkeypatch.setattr(dirsize_report.os, "scandir", scandir)
    data = run_report(monkeypatch, target, tmp_path / "out", code=1)
    assert data["status"] == "partial"
    assert next(n for n in data["nodes"] if n["path"] == str(sub))["status"] == "unavailable"


def test_scan_never_opens_file_contents(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "target"
    target.mkdir()
    (target / "private").write_bytes(b"secret data")
    original = pathlib.Path.open

    def open_path(path: pathlib.Path, *args: Any, **kwargs: Any) -> Any:
        if path.is_relative_to(target):
            pytest.fail("走査対象本文を開いた")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(pathlib.Path, "open", open_path)
    data = run_report(monkeypatch, target, tmp_path / "out")
    assert data["nodes"][0]["size"] == 11


@pytest.mark.parametrize("failure", ["absent", "exit", "relative", "outside", "excluded", "timeout"])
def test_uv_no_candidate_reasons(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, failure: str) -> None:
    target = tmp_path / "target"
    target.mkdir()
    root = target / "uv"
    root.mkdir()
    mock_uv(monkeypatch, root)

    def run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        del kwargs
        if failure == "absent":
            raise FileNotFoundError("uvなし")
        if failure == "timeout":
            raise subprocess.TimeoutExpired(command, 15)
        return subprocess.CompletedProcess(
            command,
            1 if failure == "exit" else 0,
            "relative" if failure == "relative" else str(tmp_path if failure == "outside" else root),
            "uv diagnostic" if failure == "exit" else "",
        )

    mock_uv_command(monkeypatch, run)
    extra = ("--exclude", "uv") if failure == "excluded" else ()
    data = run_report(monkeypatch, target, tmp_path / "out", *extra)
    assert data["candidate"] is None
    assert data["candidate_reason"]
    if failure == "exit":
        assert "uv diagnostic" in data["candidate_reason"]


@pytest.mark.parametrize("location", ["default", "environment"])
def test_uv_root_scripts_and_safe_embedding(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, location: str) -> None:
    target = tmp_path / "target"
    target.mkdir()
    # POSIXだけが受理するファイル名はOS境界を分け、Windowsでも引用符を検証する。
    name = "uv ' $ cache" if os.name == "nt" else "uv ' $ `;\ncache"
    root = target / name
    root.mkdir()
    (root / "data").write_bytes(b"123")
    (target / "html & text").write_bytes(b"1")
    if location == "environment":
        monkeypatch.setenv("UV_CACHE_DIR", str(root))
    calls = mock_uv(monkeypatch, root)
    data = run_report(monkeypatch, target, tmp_path / "out")
    candidate = data["candidate"]
    assert candidate["path"] == str(root)
    assert shlex.split(candidate["shell"].split("set -eu\n", 1)[1]) == [
        "uv",
        "cache",
        "prune",
        "--no-config",
        "--offline",
        "--cache-dir",
        str(root),
    ]
    assert "'" + str(root).replace("'", "''") + "'" in candidate["powershell"]
    assert calls == [["uv", "cache", "dir", "--no-config"]]
    assert "\\u0026" in (tmp_path / "out" / "report.html").read_text(encoding="utf-8")


@pytest.mark.skipif(os.name == "nt", reason="Windowsでは末尾空白・改行を含むディレクトリ名を作れない")
@pytest.mark.parametrize("suffix", [" ", "\n", " \n"], ids=["space", "newline", "space-newline"])
def test_uv_root_preserves_trailing_whitespace(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    suffix: str,
) -> None:
    target = tmp_path / "target"
    target.mkdir()
    trimmed = target / "uv-cache"
    root = target / ("uv-cache" + suffix)
    for path in (trimmed, root):
        path.mkdir()
        (path / "data").write_bytes(b"123")
    calls = mock_uv(monkeypatch, root)
    candidate = run_report(monkeypatch, target, tmp_path / "out")["candidate"]
    print(json.dumps({"expected_root": str(root), "candidate": candidate}, ensure_ascii=True))
    assert candidate["path"] == str(root)
    assert shlex.split(candidate["shell"].split("set -eu\n", 1)[1]) == [
        "uv",
        "cache",
        "prune",
        "--no-config",
        "--offline",
        "--cache-dir",
        str(root),
    ]
    assert candidate["powershell"] == (
        "$ErrorActionPreference = 'Stop'\r\n"
        "& 'uv' 'cache' 'prune' '--no-config' '--offline' '--cache-dir' '"
        + str(root).replace("'", "''")
        + "'\r\nif ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }\r\n"
    )
    assert calls == [["uv", "cache", "dir", "--no-config"]]


@pytest.mark.parametrize(
    "bad_history",
    [
        "wrong,columns\n",
        "\xff",
        "time,target,conditions,run_status,path,kind,size,status\nnot-a-date,t,{},success,p,file,1,success\n",
    ],
)
def test_corrupt_history_fails_without_overwrite(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    bad_history: str,
) -> None:
    output = tmp_path / "out"
    output.mkdir()
    history = output / "history.csv"
    content = bad_history.encode("latin1")
    history.write_bytes(content)
    monkeypatch.setattr(sys, "argv", ["dirsize", "--report-dir", str(output), str(tmp_path)])
    with pytest.raises(SystemExit) as exc:
        dirsize.main()
    assert exc.value.code == 1
    assert history.read_bytes() == content
    assert not (output / "report.html").exists()


def test_save_failure_is_not_success(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    output = tmp_path / "out"
    output.mkdir()
    (output / "report.html").mkdir()
    monkeypatch.setattr(sys, "argv", ["dirsize", "--report-dir", str(output), str(tmp_path)])
    with pytest.raises(SystemExit) as exc:
        dirsize.main()
    assert exc.value.code == 1
    assert not (output / "history.csv").exists()
    assert not list(output.glob(".report.html-*"))


def test_text_mode_and_report_only_exclude(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture,
) -> None:
    (tmp_path / "data").write_bytes(b"123")
    monkeypatch.setattr(sys, "argv", ["dirsize", "-a", "-r", "1", str(tmp_path)])
    with pytest.raises(SystemExit) as exc:
        dirsize.main()
    assert exc.value.code == 0
    assert capsys.readouterr().out.endswith("       0MiB\t.\n")
    monkeypatch.setattr(sys, "argv", ["dirsize", "--exclude", "data", str(tmp_path)])
    with pytest.raises(SystemExit) as exc:
        dirsize.main()
    assert exc.value.code == 2
    assert not (tmp_path / "report.html").exists()
