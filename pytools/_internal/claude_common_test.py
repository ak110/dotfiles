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


class TestTargetHosts:
    """``TARGET_HOSTS``定数の内容検証。"""

    def test_contains_expected_hostnames(self):
        """バランスモード・フィードバック蓄積が対象とする5ホストを含む。"""
        assert claude_common.TARGET_HOSTS == ("stheno", "circe", "circe-container", "euryale", "euryale-container")


class TestIsTargetHost:
    """``is_target_host``の大文字小文字・FQDN接尾辞の扱いを検証する。"""

    @pytest.mark.parametrize(
        ("hostname", "expected"),
        [
            pytest.param("stheno", True, id="exact-match"),
            pytest.param("STHENO", True, id="uppercase"),
            pytest.param("Circe", True, id="mixed-case"),
            pytest.param("circe.local", True, id="fqdn-suffix-stripped"),
            pytest.param("other-host", False, id="non-target-host"),
        ],
    )
    def test_matches_target_hosts_case_and_fqdn_insensitively(self, hostname: str, expected: bool):
        assert claude_common.is_target_host(hostname) is expected


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
