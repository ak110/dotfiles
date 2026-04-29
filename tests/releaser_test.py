"""releaserモジュールのテスト。"""

import subprocess
import sys
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
import yaml

from pytools.releaser import (
    _build_parser,
    _main,
    _ReleaserError,
    _validate_release_workflow_dict,
)


class TestValidateReleaseWorkflow:
    """release.yaml構造検証のテスト。"""

    @staticmethod
    def _build_valid_data() -> dict:
        return {
            "on": {
                "workflow_dispatch": {
                    "inputs": {
                        "bump": {
                            "options": ["PATCH", "MINOR", "MAJOR"],
                        },
                    },
                },
            },
        }

    def test_valid(self) -> None:
        _validate_release_workflow_dict(self._build_valid_data())

    def test_yaml_on_becomes_true_key(self) -> None:
        text = (
            "on:\n"
            "  workflow_dispatch:\n"
            "    inputs:\n"
            "      bump:\n"
            "        options:\n"
            "          - PATCH\n"
            "          - MINOR\n"
            "          - MAJOR\n"
        )
        data = yaml.safe_load(text)
        # PyYAMLのYAML 1.1仕様により`on`キーが真偽値Trueへ強制変換されることを前提に検証する。
        assert True in data
        assert "on" not in data
        _validate_release_workflow_dict(data)

    def test_top_level_not_dict(self) -> None:
        with pytest.raises(_ReleaserError, match="マップ"):
            _validate_release_workflow_dict(None)

    def test_missing_workflow_dispatch(self) -> None:
        data = {"on": {"push": {"branches": ["master"]}}}
        with pytest.raises(_ReleaserError, match="workflow_dispatch"):
            _validate_release_workflow_dict(data)

    def test_missing_bump(self) -> None:
        data: dict[str, Any] = {"on": {"workflow_dispatch": {"inputs": {"other": {}}}}}
        with pytest.raises(_ReleaserError, match="bump"):
            _validate_release_workflow_dict(data)

    def test_missing_required_option(self) -> None:
        data = self._build_valid_data()
        data["on"]["workflow_dispatch"]["inputs"]["bump"]["options"] = ["PATCH", "MINOR"]
        with pytest.raises(_ReleaserError, match="MAJOR"):
            _validate_release_workflow_dict(data)

    def test_options_not_list(self) -> None:
        data = self._build_valid_data()
        data["on"]["workflow_dispatch"]["inputs"]["bump"]["options"] = "PATCH"
        with pytest.raises(_ReleaserError, match="options"):
            _validate_release_workflow_dict(data)


class TestParser:
    """argparseパーサーのテスト。"""

    def test_bump_lowercase_accepted(self) -> None:
        args = _build_parser().parse_args(["patch"])
        assert args.bump == "patch"
        assert args.bump.upper() == "PATCH"

    def test_bump_optional(self) -> None:
        args = _build_parser().parse_args([])
        assert args.bump is None

    def test_bump_invalid_rejected(self) -> None:
        with pytest.raises(SystemExit):
            _build_parser().parse_args(["unknown"])


class TestMainSmoke:
    """_main()のsmokeテスト。"""

    def test_help(self, capsys: pytest.CaptureFixture[str]) -> None:
        with patch.object(sys, "argv", ["releaser", "--help"]), pytest.raises(SystemExit) as exc_info:
            _main()
        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "release.yaml" in captured.out

    def test_no_args_with_tag(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        _setup_git_repo(tmp_path)
        monkeypatch.chdir(tmp_path)
        _make_commit(tmp_path, "first")
        subprocess.run(["git", "-C", str(tmp_path), "tag", "v0.1.0"], check=True)
        _make_commit(tmp_path, "second")
        with patch.object(sys, "argv", ["releaser"]):
            _main()
        captured = capsys.readouterr()
        assert "v0.1.0" in captured.out
        assert "second" in captured.out

    def test_no_args_without_tag(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        _setup_git_repo(tmp_path)
        monkeypatch.chdir(tmp_path)
        _make_commit(tmp_path, "init")
        with patch.object(sys, "argv", ["releaser"]):
            _main()
        captured = capsys.readouterr()
        assert "見つかりません" in captured.out


def _setup_git_repo(path: Path) -> None:
    subprocess.run(["git", "init", "-q", "-b", "master", str(path)], check=True)
    for key, value in [
        ("user.email", "test@example.com"),
        ("user.name", "test"),
        ("commit.gpgsign", "false"),
    ]:
        subprocess.run(["git", "-C", str(path), "config", key, value], check=True)


def _make_commit(path: Path, message: str) -> None:
    subprocess.run(
        ["git", "-C", str(path), "commit", "--allow-empty", "-m", message],
        check=True,
    )
