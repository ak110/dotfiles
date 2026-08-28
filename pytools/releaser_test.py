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
    _list_release_runs,
    _list_runs_for_commit,
    _ReleaserError,
    _run_release_flow,
    _validate_release_workflow_dict,
    _watch_run,
    main,
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
    """main()のsmokeテスト。"""

    def test_help(self, capsys: pytest.CaptureFixture[str]) -> None:
        with patch.object(sys, "argv", ["releaser", "--help"]), pytest.raises(SystemExit) as exc_info:
            main()
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
        with patch.object(sys, "argv", ["releaser"]), pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 0
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
        with patch.object(sys, "argv", ["releaser"]), pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "見つかりません" in captured.out

    def test_expected_error_has_no_traceback(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.setattr(sys, "argv", ["releaser", "patch"])
        with (
            patch("pytools.releaser._run_release_flow", side_effect=_ReleaserError("API取得失敗")),
            pytest.raises(SystemExit) as exc_info,
        ):
            main()
        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "Traceback" not in captured.out
        assert "Traceback" not in captured.err


class TestGhReadPolicy:
    """GitHub読取コマンドの再試行契約を検証する。"""

    @pytest.mark.parametrize(
        "stderr",
        [
            "HTTP 500",
            "HTTP 599",
            "request timeout",
            "request timed out",
            "connection reset",
            "connection refused",
            "temporary failure",
            "TLS handshake timeout",
            "unexpected EOF",
        ],
    )
    def test_transient_release_list_failure_recovers(self, stderr: str) -> None:
        sleeps: list[float] = []
        results = [
            _completed_process(returncode=1, stderr=stderr),
            _completed_process(stdout='[{"databaseId": 2}]'),
        ]
        with patch("pytools.releaser.subprocess.run", side_effect=results) as run:
            runs = _list_release_runs(
                attempts=2,
                timeout=7,
                sleep=sleeps.append,
                random_uniform=_upper_bound,
            )
        assert runs == [{"databaseId": 2}]
        assert sleeps == [1.0]
        assert run.call_count == 2
        assert run.call_args_list[0].kwargs["timeout"] == 7

    def test_timeout_for_commit_list_recovers(self) -> None:
        sleeps: list[float] = []
        results = [
            subprocess.TimeoutExpired(["gh", "run", "list"], 4, stderr=b"HTTP 502"),
            _completed_process(stdout='[{"databaseId": 3}]'),
        ]
        with patch("pytools.releaser.subprocess.run", side_effect=results):
            runs = _list_runs_for_commit(
                "abc",
                attempts=2,
                timeout=4,
                sleep=sleeps.append,
                random_uniform=_upper_bound,
            )
        assert runs == [{"databaseId": 3}]
        assert sleeps == [1.0]

    def test_permanent_failure_is_not_retried(self) -> None:
        with (
            patch(
                "pytools.releaser.subprocess.run",
                return_value=_completed_process(returncode=1, stderr="HTTP 404: Not Found"),
            ) as run,
            pytest.raises(_ReleaserError, match="HTTP 404: Not Found"),
        ):
            _list_release_runs(attempts=3)
        assert run.call_count == 1

    def test_retry_exhaustion_preserves_last_stderr(self) -> None:
        sleeps: list[float] = []
        results = [
            _completed_process(returncode=1, stderr="HTTP 500: first"),
            _completed_process(returncode=1, stderr="HTTP 502: second"),
            _completed_process(returncode=1, stderr="HTTP 503: last"),
        ]
        with (
            patch("pytools.releaser.subprocess.run", side_effect=results),
            pytest.raises(_ReleaserError, match="HTTP 503: last"),
        ):
            _list_release_runs(
                sleep=sleeps.append,
                random_uniform=_upper_bound,
            )
        assert sleeps == [1.0, 2.0]

    def test_invalid_json_is_not_retried_and_preserves_stderr(self) -> None:
        with (
            patch(
                "pytools.releaser.subprocess.run",
                return_value=_completed_process(stdout="not-json", stderr="response parse context"),
            ) as run,
            pytest.raises(_ReleaserError, match="response parse context"),
        ):
            _list_release_runs(attempts=3)
        assert run.call_count == 1


class TestWatchRun:
    """watch失敗後のrun状態判定を検証する。"""

    @pytest.mark.parametrize("conclusion", ["success", "skipped", "neutral"])
    def test_transient_view_failure_recovers_completed_success(self, conclusion: str) -> None:
        sleeps: list[float] = []
        results = [
            _completed_process(returncode=1),
            _completed_process(returncode=1, stderr="HTTP 500"),
            _completed_process(stdout=f'{{"status": "completed", "conclusion": "{conclusion}"}}'),
        ]
        with patch("pytools.releaser.subprocess.run", side_effect=results) as run:
            _watch_run(
                42,
                attempts=2,
                timeout=6,
                sleep=sleeps.append,
                random_uniform=_upper_bound,
            )
        assert run.call_count == 3
        assert sleeps == [1.0]
        assert run.call_args_list[1].kwargs["timeout"] == 6

    def test_completed_failure_is_not_hidden(self) -> None:
        results = [
            _completed_process(returncode=1),
            _completed_process(stdout='{"status": "completed", "conclusion": "failure"}'),
        ]
        with (
            patch("pytools.releaser.subprocess.run", side_effect=results),
            pytest.raises(_ReleaserError, match="conclusion=failure"),
        ):
            _watch_run(42)

    def test_incomplete_run_restarts_watch(self) -> None:
        sleeps: list[float] = []
        results = [
            _completed_process(returncode=1),
            _completed_process(stdout='{"status": "in_progress", "conclusion": null}'),
            _completed_process(),
        ]
        with patch("pytools.releaser.subprocess.run", side_effect=results) as run:
            _watch_run(42, sleep=sleeps.append, random_uniform=_upper_bound)
        watch_calls = [call for call in run.call_args_list if call.args[0][2] == "watch"]
        assert len(watch_calls) == 2
        assert [call.args[0] for call in watch_calls] == [
            ["gh", "run", "watch", "42", "--compact", "--exit-status"],
            ["gh", "run", "watch", "42", "--compact", "--exit-status"],
        ]
        assert sleeps == [1.0]

    def test_incomplete_run_exhaustion_is_api_error(self) -> None:
        sleeps: list[float] = []
        results = [
            _completed_process(returncode=1),
            _completed_process(stdout='{"status": "pending", "conclusion": null}'),
            _completed_process(returncode=1),
            _completed_process(stdout='{"status": "pending", "conclusion": null}'),
            _completed_process(returncode=1),
            _completed_process(stdout='{"status": "pending", "conclusion": null}'),
        ]
        with (
            patch("pytools.releaser.subprocess.run", side_effect=results),
            pytest.raises(_ReleaserError, match="状態取得が再試行上限"),
        ):
            _watch_run(42, sleep=sleeps.append, random_uniform=_upper_bound)
        assert sleeps == [1.0, 2.0]

    def test_unclassifiable_view_is_api_error(self) -> None:
        results = [
            _completed_process(returncode=1),
            _completed_process(stdout='{"status": "unknown", "conclusion": null}'),
        ]
        with (
            patch("pytools.releaser.subprocess.run", side_effect=results),
            pytest.raises(_ReleaserError, match="状態を判定できません"),
        ):
            _watch_run(42)

    def test_completed_view_without_conclusion_is_api_error(self) -> None:
        results = [
            _completed_process(returncode=1),
            _completed_process(stdout='{"status": "completed", "conclusion": null}'),
        ]
        with (
            patch("pytools.releaser.subprocess.run", side_effect=results),
            pytest.raises(_ReleaserError, match="完了結果を取得できません"),
        ):
            _watch_run(42)

    def test_dispatch_is_not_repeated_after_watch_failure(self, tmp_path: Path) -> None:
        root = tmp_path
        workflow_path = root / ".github" / "workflows" / "release.yaml"
        workflow_path.parent.mkdir(parents=True)
        workflow_path.write_text("name: release\n", encoding="utf-8")
        with (
            patch("pytools.releaser._get_git_root", return_value=root),
            patch("pytools.releaser._ensure_default_branch"),
            patch("pytools.releaser._ensure_clean_working_tree"),
            patch("pytools.releaser._push_to_remote"),
            patch("pytools.releaser._wait_for_ci"),
            patch("pytools.releaser._check_release_workflow"),
            patch("pytools.releaser._get_latest_release_run_id", return_value=1),
            patch("pytools.releaser._dispatch_release_workflow") as dispatch,
            patch("pytools.releaser._wait_for_new_release_run", return_value=2),
            patch("pytools.releaser._watch_run", side_effect=_ReleaserError("API取得失敗")),
            pytest.raises(_ReleaserError, match="API取得失敗"),
        ):
            _run_release_flow("PATCH")
        dispatch.assert_called_once_with("PATCH")


def _completed_process(
    *,
    returncode: int = 0,
    stdout: str = "",
    stderr: str = "",
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(["gh"], returncode, stdout, stderr)


def _upper_bound(_lower: float, upper: float) -> float:
    return upper


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
