"""pytools._internal.common のテスト。"""

# pylint: disable=protected-access  # 内部ヘルパー関数の単体テスト目的で `_` プレフィックス関数へアクセスする

import io
import json
import os
import subprocess
import sys
import time
import types
import typing
from pathlib import Path

import psutil
import pytest

from pytools._internal import common


def _holds_resources(process: psutil.Process) -> bool:
    """対象が資源を保持しているかを返す。生存確認と状態取得の間に終了した対象は解放済みとして扱う。"""
    try:
        return process.is_running() and process.status() != psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return False


def test_process_scope_reports_residual_identifier_and_recovery(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """停止不能な子の識別子と、受信側が実行できる次操作・報告先を同じ警告へ残す。"""
    process = types.SimpleNamespace(stdin=None, stdout=io.BytesIO(), stderr=io.BytesIO(), poll=lambda: None)
    monkeypatch.setattr(subprocess, "Popen", lambda *_args, **_kwargs: process)
    monkeypatch.setattr(common, "stop_process_tree", lambda _process: ["12345: fixture-command --child"])
    with common.process_scope(["fixture-command"]):
        pass
    assert process.stdout.closed and process.stderr.closed
    assert "12345: fixture-command --child" in caplog.text
    assert "表示されたPIDと起動コマンドを確認" in caplog.text
    assert "終了してから呼出元を再実行" in caplog.text
    assert "dotfilesの管理者へ報告" in caplog.text


class TestResolveUvPath:
    """``resolve_uv_path``の公式導入先優先とPATHフォールバックを検証する。"""

    def test_prefers_local_bin(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        uv = tmp_path / ".local" / "bin" / "uv"
        monkeypatch.setattr(common.Path, "home", lambda: tmp_path)
        monkeypatch.setattr(common, "resolve_executable", lambda *_args, **_kwargs: uv)

        assert common.resolve_uv_path() == uv

    def test_falls_back_to_path_lookup(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setattr(common.Path, "home", lambda: tmp_path)
        monkeypatch.setattr(
            common,
            "resolve_executable",
            lambda *_args, **_kwargs: Path("/opt/uv/bin/uv"),
        )

        assert common.resolve_uv_path() == Path("/opt/uv/bin/uv")

    def test_returns_none_when_uv_is_missing(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setattr(common.Path, "home", lambda: tmp_path)
        monkeypatch.setattr(common, "resolve_executable", lambda *_args, **_kwargs: None)

        assert common.resolve_uv_path() is None


class TestResolveExecutable:
    """mise shimを除外した実行ファイル解決を検証する。"""

    @staticmethod
    def _fake_which(name: str, *, path: str | None = None) -> str | None:
        assert path is not None
        candidate = Path(path) / name
        return str(candidate) if candidate.is_file() else None

    def test_prefers_explicit_directory_before_path(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        preferred = tmp_path / "preferred"
        path_directory = tmp_path / "path"
        preferred.mkdir()
        path_directory.mkdir()
        (preferred / "claude").touch()
        (path_directory / "claude").touch()
        monkeypatch.setenv("PATH", str(path_directory))
        monkeypatch.setattr(common.shutil, "which", self._fake_which)

        assert common.resolve_executable("claude", preferred_directories=(preferred,)) == preferred / "claude"

    def test_missing_preferred_directory_falls_back_to_path(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        path_directory = tmp_path / "path"
        path_directory.mkdir()
        (path_directory / "codex").touch()
        monkeypatch.setenv("PATH", str(path_directory))
        monkeypatch.setattr(common.shutil, "which", self._fake_which)

        assert common.resolve_executable("codex", preferred_directories=(tmp_path / "missing",)) == path_directory / "codex"

    def test_relative_path_stays_bound_after_cwd_change(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        resolve_cwd = tmp_path / "resolve"
        launch_cwd = tmp_path / "launch"
        for cwd, output in ((resolve_cwd, "expected"), (launch_cwd, "wrong")):
            executable = cwd / "bin" / "claude"
            executable.parent.mkdir(parents=True)
            executable.write_text(f"#!/bin/sh\nprintf '%s\\n' {output}\n", encoding="utf-8")
            executable.chmod(0o755)

        monkeypatch.chdir(resolve_cwd)
        monkeypatch.setenv("PATH", "bin")
        resolved = common.resolve_executable("claude")

        assert resolved == resolve_cwd / "bin" / "claude"
        monkeypatch.chdir(launch_cwd)
        result = subprocess.run([str(resolved)], capture_output=True, text=True, check=True)
        assert result.stdout == "expected\n"

    def test_custom_mise_data_dir_excludes_only_custom_shims(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        default_shims = tmp_path / ".local" / "share" / "mise" / "shims"
        custom_shims = tmp_path / "custom-mise" / "shims"
        real_bin = tmp_path / "real"
        for directory in (default_shims, custom_shims, real_bin):
            directory.mkdir(parents=True)
            (directory / "claude").touch()
        monkeypatch.setattr(common.Path, "home", lambda: tmp_path)
        monkeypatch.setenv("MISE_DATA_DIR", str(tmp_path / "custom-mise"))
        monkeypatch.setenv("PATH", os.pathsep.join((str(custom_shims), str(default_shims), str(real_bin))))
        monkeypatch.setattr(common.shutil, "which", self._fake_which)

        assert common.resolve_executable("claude") == default_shims / "claude"

    def test_returns_none_when_only_mise_shim_exists(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        shims = tmp_path / "mise" / "shims"
        shims.mkdir(parents=True)
        (shims / "uv").touch()
        monkeypatch.setenv("MISE_DATA_DIR", str(tmp_path / "mise"))
        monkeypatch.setenv("PATH", str(shims))
        monkeypatch.setattr(common.shutil, "which", self._fake_which)

        assert common.resolve_executable("uv") is None

    def test_windows_local_app_data_shims_are_excluded(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        shims = tmp_path / "local-app-data" / "mise" / "shims"
        real_bin = tmp_path / "real"
        shims.mkdir(parents=True)
        real_bin.mkdir()
        (shims / "codex").touch()
        (real_bin / "codex").touch()
        monkeypatch.delenv("MISE_DATA_DIR", raising=False)
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local-app-data"))
        monkeypatch.setenv("PATH", os.pathsep.join((str(shims), str(real_bin))))
        monkeypatch.setattr(common.sys, "platform", "win32")
        monkeypatch.setattr(common.shutil, "which", self._fake_which)

        assert common.resolve_executable("codex") == real_bin / "codex"


class TestEnsureFlagFilePresent:
    """``ensure_flag_file_present``の冪等生成挙動を検証する。"""

    def test_creates_file_and_returns_true_when_absent(self, tmp_path: Path):
        flag = tmp_path / "sub" / "flag"
        assert common.ensure_flag_file_present(flag, tag="test-tag") is True
        assert flag.exists()

    def test_returns_false_when_already_present(self, tmp_path: Path):
        flag = tmp_path / "flag"
        flag.write_bytes(b"")
        assert common.ensure_flag_file_present(flag, tag="test-tag") is False


class TestRunSubprocess:
    """``run_subprocess``の文字コード・標準入力・環境継承を検証する。"""

    def test_explicit_code_page_preserves_powershell_diagnostic(self) -> None:
        """PowerShell 5.1相当のCP932診断を文字化けさせずに受け取る。"""
        message = "失効の関数は証明書の失効を確認できませんでした。"
        script = f"import sys; sys.stderr.buffer.write({message!r}.encode('cp932'))"
        result = common.run_subprocess([sys.executable, "-c", script], encoding="cp932")
        assert result is not None and result.returncode == 0
        assert result.stderr == message

    def test_stdin_is_devnull_and_env_inherits_when_no_overrides(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ):
        monkeypatch.setenv("COMMON_TEST_VALUE", "inherited")
        result = common.run_subprocess(
            [sys.executable, "-c", "import os,sys; print(repr(sys.stdin.read())); print(os.environ['COMMON_TEST_VALUE'])"]
        )
        assert result is not None and result.returncode == 0
        assert result.stdout == "''\ninherited\n"

    @pytest.mark.parametrize(
        ("overrides", "preset_env", "expected_subset"),
        [
            ({}, {"PRESET": "keep"}, {"PRESET": "keep"}),
            ({"NEW_KEY": "1"}, {"PRESET": "keep"}, {"PRESET": "keep", "NEW_KEY": "1"}),
            ({"EXISTING": "after"}, {"EXISTING": "before"}, {"EXISTING": "after"}),
        ],
    )
    def test_env_overrides_merge_with_os_environ(
        self,
        monkeypatch: pytest.MonkeyPatch,
        overrides: dict[str, str],
        preset_env: dict[str, str],
        expected_subset: dict[str, str],
    ):
        for key, value in preset_env.items():
            monkeypatch.setenv(key, value)
        result = common.run_subprocess(
            [sys.executable, "-c", "import json,os; print(json.dumps(dict(os.environ)))"], env_overrides=overrides
        )
        assert result is not None and result.returncode == 0
        env = json.loads(result.stdout)
        for key, value in expected_subset.items():
            assert env[key] == value
        # os.environ をベースとしているため、上書きしていない他のキーも残る
        for key, value in os.environ.items():
            if key in overrides:
                continue
            assert env.get(key) == value


def test_run_subprocess_timeout_stops_grandchildren(monkeypatch, tmp_path):
    """時間上限の失敗を返す時点で、準備済みの子孫が解放されている。"""
    ready = tmp_path / "ready"
    started: list[psutil.Process] = []

    class ReadyPopen(subprocess.Popen):
        def communicate(self, input=None, timeout=None):
            if timeout is not None:
                deadline = time.monotonic() + 30
                while not ready.exists() and time.monotonic() < deadline:
                    time.sleep(0.01)
                assert ready.exists(), "孫の準備が時間内に完了しなかった"
                parent = psutil.Process(self.pid)
                started.extend([parent, *parent.children(recursive=True)])
            return super().communicate(input, timeout=timeout)

    child = f"import pathlib,time; pathlib.Path({str(ready)!r}).touch(); time.sleep(120)"
    parent = f"import subprocess,sys,time; subprocess.Popen([sys.executable,'-c',{child!r}]); time.sleep(120)"
    monkeypatch.setattr(subprocess, "Popen", ReadyPopen)
    try:
        assert common.run_subprocess([sys.executable, "-c", parent], timeout=0.05) is None
        assert started
        assert not [p for p in started if _holds_resources(p)]
    finally:
        for process in started:
            if process.is_running():
                process.kill()


class TestCollectValueOnlyUpdates:
    """`_collect_value_only_updates` の分岐テスト。"""

    def test_identical_returns_empty(self):
        """同一値なら空dictを返す。"""
        assert common._collect_value_only_updates({"a": 1}, {"a": 1}) == {}  # noqa: SLF001

    def test_scalar_change_returns_path_mapping(self):
        """スカラー値の書き換えはpath→新値のマッピングを返す。"""
        result = common._collect_value_only_updates({"a": 1}, {"a": 2})  # noqa: SLF001
        assert result == {("a",): 2}

    def test_nested_scalar_change(self):
        """ネスト先のスカラー変更もpathで表現される。"""
        result = common._collect_value_only_updates(  # noqa: SLF001
            {"outer": {"inner": 1}},
            {"outer": {"inner": 2}},
        )
        assert result == {("outer", "inner"): 2}

    def test_key_addition_returns_none(self):
        """キー追加を含む差分はNoneを返す（構造変化）。"""
        assert common._collect_value_only_updates({"a": 1}, {"a": 1, "b": 2}) is None  # noqa: SLF001

    def test_key_deletion_returns_none(self):
        """キー削除を含む差分はNoneを返す。"""
        assert common._collect_value_only_updates({"a": 1, "b": 2}, {"a": 1}) is None  # noqa: SLF001

    def test_list_change_returns_none(self):
        """list全体の差し替えはNoneを返す。"""
        assert common._collect_value_only_updates({"a": [1, 2]}, {"a": [1, 2, 3]}) is None  # noqa: SLF001

    def test_list_same_returns_empty(self):
        """list同一なら空dictを返す。"""
        assert common._collect_value_only_updates({"a": [1, 2]}, {"a": [1, 2]}) == {}  # noqa: SLF001

    def test_type_change_returns_none(self):
        """型変化（dict→scalarなど）はNoneを返す。"""
        assert common._collect_value_only_updates({"a": {"b": 1}}, {"a": "string"}) is None  # noqa: SLF001

    def test_multiple_scalar_changes(self):
        """複数のスカラー変更が同時に検出される。"""
        result = common._collect_value_only_updates(  # noqa: SLF001
            {"a": 1, "b": 2, "c": {"d": 3}},
            {"a": 10, "b": 2, "c": {"d": 30}},
        )
        assert result == {("a",): 10, ("c", "d"): 30}


class TestAtomicWriteBytes:
    """`atomic_write_bytes()`の原子的置換・パーミッション設定・失敗時温存を検証する。"""

    def test_writes_content_and_sets_executable_mode(self, tmp_path: Path):
        target = tmp_path / "bin" / "tool"
        assert common.atomic_write_bytes(target, b"BINARY", mode=0o755) is True
        assert target.read_bytes() == b"BINARY"
        if sys.platform != "win32":
            assert target.stat().st_mode & 0o777 == 0o755

    def test_existing_file_preserved_on_replace_failure(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        target = tmp_path / "tool"
        target.write_bytes(b"OLD")

        def _boom(self, *_args, **_kwargs):
            raise OSError("boom")

        monkeypatch.setattr(Path, "replace", _boom)
        assert common.atomic_write_bytes(target, b"NEW") is False
        assert target.read_bytes() == b"OLD"


class TestAtomicEditJsonc:
    """`_atomic_edit_jsonc` のテスト。"""

    def test_empty_updates_returns_false(self, tmp_path: Path):
        """updatesが空の場合、書き込みせずFalseを返す。"""
        path = tmp_path / "target.jsonc"
        path.write_text('{"a": 1}', encoding="utf-8")
        assert common._atomic_edit_jsonc(path, {}) is False  # noqa: SLF001
        assert path.read_text(encoding="utf-8") == '{"a": 1}'

    def test_preserves_comments(self, tmp_path: Path):
        """コメントを維持したまま値を書き換える。"""
        path = tmp_path / "target.jsonc"
        path.write_text('{\n  // コメント\n  "a": 1\n}\n', encoding="utf-8")
        assert common._atomic_edit_jsonc(path, {("a",): 2}) is True  # noqa: SLF001
        text = path.read_text(encoding="utf-8")
        assert "// コメント" in text
        assert '"a": 2' in text


class TestWriteSettingsHybrid:
    """`write_settings_hybrid` の分岐と失敗経路のテスト。"""

    def test_scalar_change_uses_jsonc_edit(self, tmp_path: Path):
        """スカラー変更のみならJSONC編集経路でコメントを維持する。"""
        path = tmp_path / "settings.json"
        path.write_text('{\n  // コメント\n  "a": 1\n}\n', encoding="utf-8")
        assert common.write_settings_hybrid(path, {"a": 1}, {"a": 2}) is True
        text = path.read_text(encoding="utf-8")
        assert "// コメント" in text
        assert '"a": 2' in text

    def test_key_addition_falls_back_to_full_rewrite(self, tmp_path: Path):
        """キー追加はJSONCで扱えないため全書き換え経路へ倒す。"""
        path = tmp_path / "settings.json"
        path.write_text('{\n  // コメント\n  "a": 1\n}\n', encoding="utf-8")
        assert common.write_settings_hybrid(path, {"a": 1}, {"a": 1, "b": 2}) is True
        text = path.read_text(encoding="utf-8")
        assert "// コメント" not in text
        assert json.loads(text) == {"a": 1, "b": 2}

    def test_new_file_uses_full_write(self, tmp_path: Path):
        """既存ファイル無しなら全書き換え経路で新規作成する。"""
        path = tmp_path / "settings.json"
        assert common.write_settings_hybrid(path, {}, {"a": 1}) is True
        assert json.loads(path.read_text(encoding="utf-8")) == {"a": 1}

    def test_falls_back_when_jsonc_edit_raises(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """jsonc.editが例外を送出した場合は全書き換え経路へフォールバックする。

        コンカレントな他プロセス書き込みでパス構造が変化した場合を想定する。
        """
        path = tmp_path / "settings.json"
        path.write_text('{\n  // コメント\n  "a": 1\n}\n', encoding="utf-8")

        def _raise(*_args: typing.Any, **_kwargs: typing.Any) -> str:
            raise KeyError("path missing")

        monkeypatch.setattr(common.pytilpack.jsonc, "edit", _raise)
        assert common.write_settings_hybrid(path, {"a": 1}, {"a": 2}) is True
        text = path.read_text(encoding="utf-8")
        # フォールバック経路のため、コメントは失われるが値は書き込まれる
        assert "// コメント" not in text
        assert json.loads(text) == {"a": 2}
