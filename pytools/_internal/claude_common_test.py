"""pytools._internal.claude_common のテスト。"""

# pylint: disable=protected-access  # 内部ヘルパー関数の単体テスト目的で `_` プレフィックス関数へアクセスする

import json
import os
import subprocess
import sys
import typing
from pathlib import Path

import pytest

from pytools._internal import claude_common


class TestIsTargetHost:
    """``is_target_host``の大文字小文字・FQDN接尾辞の扱いを検証する。"""

    @pytest.mark.parametrize(
        ("hostname", "expected"),
        [
            pytest.param("stheno", True, id="exact-match"),
            pytest.param("STHENO", True, id="uppercase"),
            pytest.param("Circe", True, id="mixed-case"),
            pytest.param("circe-container", True, id="circe-container"),
            pytest.param("euryale", True, id="euryale"),
            pytest.param("euryale-container", True, id="euryale-container"),
            pytest.param("circe.local", True, id="fqdn-suffix-stripped"),
            pytest.param("other-host", False, id="non-target-host"),
        ],
    )
    def test_matches_target_hosts_case_and_fqdn_insensitively(self, hostname: str, expected: bool):
        assert claude_common.is_target_host(hostname) is expected


class TestIsEuryale:
    """``is_euryale``のplatform・大文字小文字・FQDN接尾辞の扱いを検証する。"""

    @pytest.mark.parametrize(
        ("platform", "hostname", "expected"),
        [
            pytest.param("linux", "euryale", True, id="exact-match"),
            pytest.param("linux", "EURYALE", True, id="uppercase"),
            pytest.param("linux", "Euryale.example.test", True, id="fqdn-suffix-stripped"),
            pytest.param("linux", "circe", False, id="other-host"),
            pytest.param("win32", "euryale", False, id="non-linux"),
        ],
    )
    def test_matches_only_linux_euryale(
        self,
        monkeypatch: pytest.MonkeyPatch,
        platform: str,
        hostname: str,
        expected: bool,
    ) -> None:
        monkeypatch.setattr(claude_common.sys, "platform", platform)
        monkeypatch.setattr(claude_common.socket, "gethostname", lambda: hostname)
        assert claude_common.is_euryale() is expected


class TestResolveUvPath:
    """``resolve_uv_path``の公式導入先優先とPATHフォールバックを検証する。"""

    def test_prefers_local_bin(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        uv = tmp_path / ".local" / "bin" / "uv"
        monkeypatch.setattr(claude_common.Path, "home", lambda: tmp_path)
        monkeypatch.setattr(claude_common, "resolve_executable", lambda *_args, **_kwargs: uv)

        assert claude_common.resolve_uv_path() == uv

    def test_falls_back_to_path_lookup(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setattr(claude_common.Path, "home", lambda: tmp_path)
        monkeypatch.setattr(
            claude_common,
            "resolve_executable",
            lambda *_args, **_kwargs: Path("/opt/uv/bin/uv"),
        )

        assert claude_common.resolve_uv_path() == Path("/opt/uv/bin/uv")

    def test_returns_none_when_uv_is_missing(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setattr(claude_common.Path, "home", lambda: tmp_path)
        monkeypatch.setattr(claude_common, "resolve_executable", lambda *_args, **_kwargs: None)

        assert claude_common.resolve_uv_path() is None


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
        monkeypatch.setattr(claude_common.shutil, "which", self._fake_which)

        assert claude_common.resolve_executable("claude", preferred_directories=(preferred,)) == preferred / "claude"

    def test_missing_preferred_directory_falls_back_to_path(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        path_directory = tmp_path / "path"
        path_directory.mkdir()
        (path_directory / "codex").touch()
        monkeypatch.setenv("PATH", str(path_directory))
        monkeypatch.setattr(claude_common.shutil, "which", self._fake_which)

        assert (
            claude_common.resolve_executable("codex", preferred_directories=(tmp_path / "missing",)) == path_directory / "codex"
        )

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
        resolved = claude_common.resolve_executable("claude")

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
        monkeypatch.setattr(claude_common.Path, "home", lambda: tmp_path)
        monkeypatch.setenv("MISE_DATA_DIR", str(tmp_path / "custom-mise"))
        monkeypatch.setenv("PATH", os.pathsep.join((str(custom_shims), str(default_shims), str(real_bin))))
        monkeypatch.setattr(claude_common.shutil, "which", self._fake_which)

        assert claude_common.resolve_executable("claude") == default_shims / "claude"

    def test_returns_none_when_only_mise_shim_exists(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        shims = tmp_path / "mise" / "shims"
        shims.mkdir(parents=True)
        (shims / "uv").touch()
        monkeypatch.setenv("MISE_DATA_DIR", str(tmp_path / "mise"))
        monkeypatch.setenv("PATH", str(shims))
        monkeypatch.setattr(claude_common.shutil, "which", self._fake_which)

        assert claude_common.resolve_executable("uv") is None

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
        monkeypatch.setattr(claude_common.sys, "platform", "win32")
        monkeypatch.setattr(claude_common.shutil, "which", self._fake_which)

        assert claude_common.resolve_executable("codex") == real_bin / "codex"


class TestEnsureFlagFilePresent:
    """``ensure_flag_file_present``の冪等生成挙動を検証する。"""

    def test_creates_file_and_returns_true_when_absent(self, tmp_path: Path):
        flag = tmp_path / "sub" / "flag"
        assert claude_common.ensure_flag_file_present(flag, tag="test-tag") is True
        assert flag.exists()

    def test_returns_false_when_already_present(self, tmp_path: Path):
        flag = tmp_path / "flag"
        flag.write_bytes(b"")
        assert claude_common.ensure_flag_file_present(flag, tag="test-tag") is False


class TestRunSubprocess:
    """``run_subprocess`` が ``subprocess.run`` へ渡す引数の検証。"""

    def test_stdin_is_devnull_and_env_inherits_when_no_overrides(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ):
        captured: dict[str, typing.Any] = {}

        def fake_run(cmd: list[str], **kwargs: typing.Any) -> subprocess.CompletedProcess[str]:
            captured["cmd"] = cmd
            captured["kwargs"] = kwargs
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        monkeypatch.setattr(subprocess, "run", fake_run)
        result = claude_common.run_subprocess(["echo", "hi"])

        assert result is not None
        assert result.returncode == 0
        assert captured["cmd"] == ["echo", "hi"]
        assert captured["kwargs"]["stdin"] is subprocess.DEVNULL
        assert captured["kwargs"]["env"] is None

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
        captured: dict[str, typing.Any] = {}

        def fake_run(cmd: list[str], **kwargs: typing.Any) -> subprocess.CompletedProcess[str]:
            del cmd
            captured["kwargs"] = kwargs
            return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

        monkeypatch.setattr(subprocess, "run", fake_run)
        claude_common.run_subprocess(["echo"], env_overrides=overrides)

        env = captured["kwargs"]["env"]
        assert isinstance(env, dict)
        for key, value in expected_subset.items():
            assert env[key] == value
        # os.environ をベースとしているため、上書きしていない他のキーも残る
        for key, value in os.environ.items():
            if key in overrides:
                continue
            assert env.get(key) == value


class TestCollectValueOnlyUpdates:
    """`_collect_value_only_updates` の分岐テスト。"""

    def test_identical_returns_empty(self):
        """同一値なら空dictを返す。"""
        assert claude_common._collect_value_only_updates({"a": 1}, {"a": 1}) == {}  # noqa: SLF001

    def test_scalar_change_returns_path_mapping(self):
        """スカラー値の書き換えはpath→新値のマッピングを返す。"""
        result = claude_common._collect_value_only_updates({"a": 1}, {"a": 2})  # noqa: SLF001
        assert result == {("a",): 2}

    def test_nested_scalar_change(self):
        """ネスト先のスカラー変更もpathで表現される。"""
        result = claude_common._collect_value_only_updates(  # noqa: SLF001
            {"outer": {"inner": 1}},
            {"outer": {"inner": 2}},
        )
        assert result == {("outer", "inner"): 2}

    def test_key_addition_returns_none(self):
        """キー追加を含む差分はNoneを返す（構造変化）。"""
        assert claude_common._collect_value_only_updates({"a": 1}, {"a": 1, "b": 2}) is None  # noqa: SLF001

    def test_key_deletion_returns_none(self):
        """キー削除を含む差分はNoneを返す。"""
        assert claude_common._collect_value_only_updates({"a": 1, "b": 2}, {"a": 1}) is None  # noqa: SLF001

    def test_list_change_returns_none(self):
        """list全体の差し替えはNoneを返す。"""
        assert claude_common._collect_value_only_updates({"a": [1, 2]}, {"a": [1, 2, 3]}) is None  # noqa: SLF001

    def test_list_same_returns_empty(self):
        """list同一なら空dictを返す。"""
        assert claude_common._collect_value_only_updates({"a": [1, 2]}, {"a": [1, 2]}) == {}  # noqa: SLF001

    def test_type_change_returns_none(self):
        """型変化（dict→scalarなど）はNoneを返す。"""
        assert claude_common._collect_value_only_updates({"a": {"b": 1}}, {"a": "string"}) is None  # noqa: SLF001

    def test_multiple_scalar_changes(self):
        """複数のスカラー変更が同時に検出される。"""
        result = claude_common._collect_value_only_updates(  # noqa: SLF001
            {"a": 1, "b": 2, "c": {"d": 3}},
            {"a": 10, "b": 2, "c": {"d": 30}},
        )
        assert result == {("a",): 10, ("c", "d"): 30}


class TestAtomicWriteBytes:
    """`atomic_write_bytes()`の原子的置換・パーミッション設定・失敗時温存を検証する。"""

    def test_writes_content_and_sets_executable_mode(self, tmp_path: Path):
        target = tmp_path / "bin" / "tool"
        assert claude_common.atomic_write_bytes(target, b"BINARY", mode=0o755) is True
        assert target.read_bytes() == b"BINARY"
        if sys.platform != "win32":
            assert target.stat().st_mode & 0o777 == 0o755

    def test_existing_file_preserved_on_replace_failure(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        target = tmp_path / "tool"
        target.write_bytes(b"OLD")

        def _boom(self, *_args, **_kwargs):
            raise OSError("boom")

        monkeypatch.setattr(Path, "replace", _boom)
        assert claude_common.atomic_write_bytes(target, b"NEW") is False
        assert target.read_bytes() == b"OLD"


class TestAtomicEditJsonc:
    """`_atomic_edit_jsonc` のテスト。"""

    def test_empty_updates_returns_false(self, tmp_path: Path):
        """updatesが空の場合、書き込みせずFalseを返す。"""
        path = tmp_path / "target.jsonc"
        path.write_text('{"a": 1}', encoding="utf-8")
        assert claude_common._atomic_edit_jsonc(path, {}) is False  # noqa: SLF001
        assert path.read_text(encoding="utf-8") == '{"a": 1}'

    def test_preserves_comments(self, tmp_path: Path):
        """コメントを維持したまま値を書き換える。"""
        path = tmp_path / "target.jsonc"
        path.write_text('{\n  // コメント\n  "a": 1\n}\n', encoding="utf-8")
        assert claude_common._atomic_edit_jsonc(path, {("a",): 2}) is True  # noqa: SLF001
        text = path.read_text(encoding="utf-8")
        assert "// コメント" in text
        assert '"a": 2' in text


class TestWriteSettingsHybrid:
    """`write_settings_hybrid` の分岐と失敗経路のテスト。"""

    def test_scalar_change_uses_jsonc_edit(self, tmp_path: Path):
        """スカラー変更のみならJSONC編集経路でコメントを維持する。"""
        path = tmp_path / "settings.json"
        path.write_text('{\n  // コメント\n  "a": 1\n}\n', encoding="utf-8")
        assert claude_common.write_settings_hybrid(path, {"a": 1}, {"a": 2}) is True
        text = path.read_text(encoding="utf-8")
        assert "// コメント" in text
        assert '"a": 2' in text

    def test_key_addition_falls_back_to_full_rewrite(self, tmp_path: Path):
        """キー追加はJSONCで扱えないため全書き換え経路へ倒す。"""
        path = tmp_path / "settings.json"
        path.write_text('{\n  // コメント\n  "a": 1\n}\n', encoding="utf-8")
        assert claude_common.write_settings_hybrid(path, {"a": 1}, {"a": 1, "b": 2}) is True
        text = path.read_text(encoding="utf-8")
        assert "// コメント" not in text
        assert json.loads(text) == {"a": 1, "b": 2}

    def test_new_file_uses_full_write(self, tmp_path: Path):
        """既存ファイル無しなら全書き換え経路で新規作成する。"""
        path = tmp_path / "settings.json"
        assert claude_common.write_settings_hybrid(path, {}, {"a": 1}) is True
        assert json.loads(path.read_text(encoding="utf-8")) == {"a": 1}

    def test_falls_back_when_jsonc_edit_raises(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """jsonc.editが例外を送出した場合は全書き換え経路へフォールバックする。

        コンカレントな他プロセス書き込みでパス構造が変化した場合を想定する。
        """
        path = tmp_path / "settings.json"
        path.write_text('{\n  // コメント\n  "a": 1\n}\n', encoding="utf-8")

        def _raise(*_args: typing.Any, **_kwargs: typing.Any) -> str:
            raise KeyError("path missing")

        monkeypatch.setattr(claude_common.pytilpack.jsonc, "edit", _raise)
        assert claude_common.write_settings_hybrid(path, {"a": 1}, {"a": 2}) is True
        text = path.read_text(encoding="utf-8")
        # フォールバック経路のため、コメントは失われるが値は書き込まれる
        assert "// コメント" not in text
        assert json.loads(text) == {"a": 2}
