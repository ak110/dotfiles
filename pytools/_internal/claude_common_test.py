"""pytools._internal.claude_common のテスト。"""

import os
import subprocess
import typing

import pytest

from pytools._internal import claude_common


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
