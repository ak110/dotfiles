import base64
import importlib.util
import json
import os
import pathlib
import subprocess
import sys
import typing

import pytest

from agent_toolkit._plan import viewer_files

_HELPER_PATH = pathlib.Path(__file__).resolve().with_name("atk_serve_plans_remote_helper.py")
_SPEC = importlib.util.spec_from_file_location("atk_serve_plans_remote_helper", _HELPER_PATH)
assert _SPEC is not None and _SPEC.loader is not None
helper = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(helper)

# リモートの実行環境（`uv run --no-project --with watchdog --with platformdirs`）と同じ依存集合だけを
# importできる状態でヘルパーを`python -c`と同じ名前空間で実行する。
_RESTRICTED_RUNNER = """
import importlib.abc
import sys

allowed = set(sys.stdlib_module_names) | {"agent_toolkit", "platformdirs", "watchdog"}


class _RemoteRuntimeOnly(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path, target=None):
        if name.partition(".")[0] not in allowed:
            raise ImportError(f"リモートの実行環境に無いモジュール: {name}")
        return None


sys.meta_path.insert(0, _RemoteRuntimeOnly())
helper_path = sys.argv[1]
sys.argv = [helper_path, "list"]
namespace = {"__name__": "__main__", "__file__": helper_path}
with open(helper_path, encoding="utf-8") as file:
    exec(compile(file.read(), helper_path, "exec"), namespace)
"""


def _isolate(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> tuple[pathlib.Path, pathlib.Path]:
    """ホーム、private-notesと作成日時インデックスを一時ディレクトリへ隔離し、新旧のrootを返す。"""
    home = tmp_path / "home"
    notes = tmp_path / "notes"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("AGENT_TOOLKIT_PRIVATE_NOTES", str(notes))
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.setattr(helper.creation_times, "_CREATION_TIME_INDEX_PATH", tmp_path / "cache" / "index.json")
    monkeypatch.setattr(helper, "ROOTS", None)
    new_root = notes / "plans"
    legacy_root = home / ".claude" / "plans"
    for root in (new_root, legacy_root):
        root.mkdir(parents=True)
    return new_root, legacy_root


def _run(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], *argv: str) -> typing.Any:
    """ヘルパーの`main`を指定の操作で実行し、標準出力のJSONを返す。"""
    monkeypatch.setattr(sys, "argv", ["atk_serve_plans_remote_helper.py", *argv])
    assert helper.main() == 0
    return json.loads(capsys.readouterr().out)


def _b64(value: str) -> str:
    return base64.b64encode(value.encode("utf-8")).decode("ascii")


def test_list_returns_listed_files_of_each_root(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`list`は各rootの一覧対象だけを、保存元IDと時刻を持つ項目として返す。"""
    new_root, legacy_root = _isolate(monkeypatch, tmp_path)
    (new_root / "2026" / "10").mkdir(parents=True)
    (new_root / "ci").mkdir()
    for path in (
        new_root / "2026" / "10" / "07-計画-ab12.md",
        new_root / "2026" / "10" / "07-計画-ab12.bugs.md",
        new_root / "2026" / "10" / "07-計画-ab12.exec-review.tsv",
        new_root / "ci" / "ci-abc1234.exec-review.tsv",
        legacy_root / "07-作業-cd34.md",
        legacy_root / "07-作業-cd34.detail.md",
    ):
        path.write_text("本文\n", encoding="utf-8")

    entries = _run(monkeypatch, capsys, "list")

    assert sorted((entry["source_id"], entry["path"]) for entry in entries) == [
        (viewer_files.LEGACY_SOURCE_ID, "07-作業-cd34.md"),
        (viewer_files.NEW_SOURCE_ID, "2026/10/07-計画-ab12.md"),
        (viewer_files.NEW_SOURCE_ID, "ci/ci-abc1234.exec-review.tsv"),
    ]
    assert all(set(entry) == {"path", "name", "mtime_epoch", "ctime_epoch", "source_id"} for entry in entries)


def test_read_and_search_resolve_files_within_the_source(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`read`は保存元IDと相対パスで本文を返し、`search`は一致した相対パスと保存元を返す。"""
    new_root, legacy_root = _isolate(monkeypatch, tmp_path)
    (new_root / "p.md").write_text("保存済みの本文\n", encoding="utf-8")
    (legacy_root / "p.md").write_text("作業中の本文\n", encoding="utf-8")

    read = _run(monkeypatch, capsys, "read", _b64(viewer_files.NEW_SOURCE_ID), _b64("p.md"))
    search = _run(monkeypatch, capsys, "search", _b64(viewer_files.LEGACY_SOURCE_ID), _b64("作業中"))

    assert base64.b64decode(read["data"]).decode("utf-8") == "保存済みの本文\n"
    assert search == {"paths": ["p.md"], "matches": [{"source_id": viewer_files.LEGACY_SOURCE_ID, "path": "p.md"}]}


@pytest.mark.parametrize("name", ("p.detail.md", "p.plan-review.tsv"))
def test_working_root_excludes_removed_attachments(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, name: str) -> None:
    """リモート側も`~/.claude/plans`の旧付属ファイルを読み取りの対象にしない。"""
    _, legacy_root = _isolate(monkeypatch, tmp_path)
    (legacy_root / name).write_text("legacy\n", encoding="utf-8")

    response = helper._handle_request(  # pylint: disable=protected-access
        {"id": 1, "op": "read", "source_id": viewer_files.LEGACY_SOURCE_ID, "path": _b64(name)}
    )

    assert response["ok"] is False
    assert response["error"].startswith("FileNotFoundError")


def test_helper_imports_only_remote_runtime_dependencies(tmp_path: pathlib.Path) -> None:
    """ヘルパーはリモートの実行環境が持つ依存（標準ライブラリ、platformdirs、watchdog）だけで起動できる。"""
    env = {
        **os.environ,
        "HOME": str(tmp_path / "home"),
        "USERPROFILE": str(tmp_path / "home"),
        "XDG_CACHE_HOME": str(tmp_path / "cache"),
        "AGENT_TOOLKIT_PRIVATE_NOTES": str(tmp_path / "notes"),
    }
    env.pop("CLAUDE_CONFIG_DIR", None)

    completed = subprocess.run(
        [sys.executable, "-c", _RESTRICTED_RUNNER, str(_HELPER_PATH)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        env=env,
        timeout=60,
    )

    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout) == []
