"""CI待機の主題別テスト。"""

from __future__ import annotations

import json
import pathlib
import subprocess
from collections.abc import Callable
from typing import Any
from unittest import mock

import pytest

from agent_toolkit import wait_ci
from agent_toolkit._atk import wait_ci_polling
from agent_toolkit._testing import git_repository as _git_repository_support
from agent_toolkit._testing.wait_ci_support import (
    _FULL_SHA,
    _git_repository,
    _main_args,
    _next_action,
    _run,
    _write_test_baseline,
)
from agent_toolkit._testing.wait_ci_support import (
    _restore_signal_handlers as _restore_wait_ci_signal_handlers,  # noqa: F401  # pytestがfixtureとして収集する。
)

__all__ = ["_git_repository"]


class TestMainEntrypoint:
    """公開エントリ`main`経由の引数解析・SHA解決・シナリオ確認。"""

    def test_sha_resolution_failure_returns_gh_error(self, tmp_path: pathlib.Path):
        baseline = tmp_path / "baseline.json"
        _write_test_baseline(baseline)
        with mock.patch("subprocess.run", return_value=mock.Mock(stdout="", returncode=1, stderr="")):
            assert wait_ci.main(_main_args(baseline) + ["--timeout", "1"]) == wait_ci.EXIT_GH_ERROR

    def test_explicit_sha_success_path(self, tmp_path: pathlib.Path):
        baseline = tmp_path / "baseline.json"
        _write_test_baseline(baseline)

        def fake_run(cmd, **_kwargs):
            if cmd[:3] == ["git", "rev-parse", "--verify"]:
                return mock.Mock(stdout="a" * 40, returncode=0, stderr="")
            if cmd[:3] == ["gh", "run", "list"]:
                return mock.Mock(stdout=json.dumps([_run()]).encode(), returncode=0, stderr=b"")
            if cmd[:2] == ["gh", "api"]:
                return mock.Mock(stdout=b"[]", returncode=0, stderr=b"")
            raise AssertionError(cmd)

        with mock.patch("subprocess.run", side_effect=fake_run):
            assert wait_ci.main(_main_args(baseline) + ["--registration-grace", "0"]) == wait_ci.EXIT_SUCCESS

    def test_omitted_timeout_uses_public_default_and_explicit_timeout_is_preserved(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        baseline = tmp_path / "baseline.json"
        _write_test_baseline(baseline)
        captured: list[float] = []

        def fake_wait_for_ci(_sha: str, timeout: float, *_args: object, **_kwargs: object) -> int:
            captured.append(timeout)
            return wait_ci.EXIT_SUCCESS

        monkeypatch.setattr(wait_ci, "wait_for_ci", fake_wait_for_ci)

        assert wait_ci.main(_main_args(baseline, sha=None)) == wait_ci.EXIT_SUCCESS
        assert wait_ci.main(_main_args(baseline, sha=None) + ["--timeout", "900"]) == wait_ci.EXIT_SUCCESS
        assert captured == [270.0, 900.0]

    def test_subprocess_timeout_surfaces_as_gh_error(self, tmp_path: pathlib.Path):
        """内部subprocess呼び出しのタイムアウトが`main`経由でGH_ERRORに現れる。"""
        baseline = tmp_path / "baseline.json"
        _write_test_baseline(baseline)
        with mock.patch("subprocess.run", side_effect=subprocess.TimeoutExpired("gh", 10.0)):
            assert wait_ci.main(_main_args(baseline) + ["--registration-grace", "0", "--timeout", "1"]) == wait_ci.EXIT_GH_ERROR

    def test_write_baseline_records_context_and_existing_run_ids(self, tmp_path: pathlib.Path):
        baseline = tmp_path / "baseline.json"

        def fake_run(cmd, **_kwargs):
            if cmd[:3] == ["git", "rev-parse", "--verify"]:
                return mock.Mock(stdout=_FULL_SHA, returncode=0, stderr="")
            if cmd[:3] == ["gh", "run", "list"]:
                return mock.Mock(stdout=json.dumps([_run(db_id=11), _run(db_id=12)]).encode(), returncode=0, stderr=b"")
            raise AssertionError(cmd)

        args = [
            "--write-baseline",
            str(baseline),
            "--forge=github",
            "--repo=owner/repository",
            "--ref=refs/heads/main",
            "--source-ref=HEAD",
            "--sha=abc123",
        ]
        with mock.patch("subprocess.run", side_effect=fake_run):
            assert wait_ci.main(args) == wait_ci.EXIT_SUCCESS

        payload = json.loads(baseline.read_text(encoding="utf-8"))
        assert payload == {
            "version": 2,
            "forge": "github",
            "repository": "owner/repository",
            "ref": "refs/heads/main",
            "source_ref": "HEAD",
            "sha": _FULL_SHA,
            "run_ids": [11, 12],
            "reported_failures": [],
        }

    @pytest.mark.parametrize("source_ref", ["source-branch", "lightweight-tag", "annotated-tag"])
    def test_write_baseline_resolves_source_ref_to_commit(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        git_repository: tuple[pathlib.Path, str],
        source_ref: str,
    ) -> None:
        repository, commit_sha = git_repository
        baseline = tmp_path / f"{source_ref}.json"
        monkeypatch.chdir(repository)
        monkeypatch.setattr(wait_ci, "_default_run_list_fn", lambda *_args, **_kwargs: lambda _sha: [])

        result = wait_ci.main(
            [
                "--write-baseline",
                str(baseline),
                "--forge=github",
                "--repo=owner/repository",
                "--ref=refs/heads/main",
                "--source-ref",
                source_ref,
            ]
        )

        assert result == wait_ci.EXIT_SUCCESS
        assert json.loads(baseline.read_text(encoding="utf-8"))["sha"] == commit_sha

    def test_write_baseline_rejects_source_ref_that_cannot_peel_to_commit(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        tmp_path: pathlib.Path,
        git_repository: tuple[pathlib.Path, str],
    ) -> None:
        repository, _commit_sha = git_repository
        baseline = tmp_path / "baseline.json"
        monkeypatch.chdir(repository)
        monkeypatch.setattr(wait_ci, "_default_run_list_fn", lambda *_args, **_kwargs: lambda _sha: [])

        result = wait_ci.main(
            [
                "--write-baseline",
                str(baseline),
                "--forge=github",
                "--repo=owner/repository",
                "--ref=refs/heads/main",
                "--source-ref=non-commit-tag",
            ]
        )

        assert result == wait_ci.EXIT_GH_ERROR
        assert not baseline.exists()
        assert "non-commit-tag" in capsys.readouterr().err

    def test_wait_uses_saved_sha_after_source_ref_advances(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        git_repository: tuple[pathlib.Path, str],
    ) -> None:
        repository, baseline_sha = git_repository
        baseline = tmp_path / "baseline.json"
        monkeypatch.chdir(repository)
        monkeypatch.setattr(wait_ci, "_default_run_list_fn", lambda *_args, **_kwargs: lambda _sha: [])
        write_args = [
            "--write-baseline",
            str(baseline),
            "--forge=github",
            "--repo=owner/repository",
            "--ref=refs/heads/main",
            "--source-ref=HEAD",
        ]
        assert wait_ci.main(write_args) == wait_ci.EXIT_SUCCESS
        (repository / "tracked.txt").write_text("second\n", encoding="utf-8")
        _git_repository_support.run_git(repository, "add", "tracked.txt")
        _git_repository_support.run_git(repository, "commit", "-m", "second")
        assert _git_repository_support.run_git(repository, "rev-parse", "HEAD").stdout.strip() != baseline_sha
        captured: dict[str, str] = {}

        def fake_wait_for_ci(sha: str, *_args: object, **_kwargs: object) -> int:
            captured["sha"] = sha
            return wait_ci.EXIT_SUCCESS

        monkeypatch.setattr(wait_ci, "wait_for_ci", fake_wait_for_ci)
        result = wait_ci.main(_main_args(baseline, sha=None))

        assert result == wait_ci.EXIT_SUCCESS
        assert captured["sha"] == baseline_sha

    def test_explicit_sha_must_match_saved_sha(self, tmp_path: pathlib.Path) -> None:
        baseline = tmp_path / "baseline.json"
        _write_test_baseline(baseline)
        different_sha = "b" * 40

        def fake_run(cmd, **_kwargs):
            assert cmd == ["git", "rev-parse", "--verify", "--end-of-options", "different^{commit}"]
            return mock.Mock(stdout=different_sha, returncode=0, stderr="")

        with mock.patch("subprocess.run", side_effect=fake_run):
            result = wait_ci.main(_main_args(baseline, sha="different"))

        assert result == wait_ci.EXIT_GH_ERROR

    def test_baseline_context_mismatch_fails_before_ci_query(self, tmp_path: pathlib.Path):
        baseline = tmp_path / "baseline.json"
        _write_test_baseline(baseline)

        def fake_run(cmd, **_kwargs):
            assert cmd[:3] == ["git", "rev-parse", "--verify"]
            return mock.Mock(stdout=_FULL_SHA, returncode=0, stderr="")

        with mock.patch("subprocess.run", side_effect=fake_run):
            result = wait_ci.main(_main_args(baseline) + ["--repo=different/repository"])

        assert result == wait_ci.EXIT_GH_ERROR

    def test_source_ref_mismatch_fails_before_ci_query(self, tmp_path: pathlib.Path):
        baseline = tmp_path / "baseline.json"
        _write_test_baseline(baseline, source_ref="HEAD")

        def fake_run(cmd, **_kwargs):
            assert cmd[:3] == ["git", "rev-parse", "--verify"]
            return mock.Mock(stdout=_FULL_SHA, returncode=0, stderr="")

        with mock.patch("subprocess.run", side_effect=fake_run):
            result = wait_ci.main(_main_args(baseline, source_ref="refs/heads/local"))

        assert result == wait_ci.EXIT_GH_ERROR

    @pytest.mark.parametrize("flag", ["--timeout", "--poll-interval", "--registration-grace", "--subprocess-timeout"])
    @pytest.mark.parametrize("bad_value", ["nan", "inf", "-inf"])
    def test_non_finite_numeric_args_rejected(self, flag, bad_value):
        """`nan`/`inf`/`-inf`は境界比較をすり抜けるため明示的に拒否する。"""
        with pytest.raises(SystemExit) as exc_info:
            wait_ci.main([flag, bad_value])
        assert exc_info.value.code == wait_ci.EXIT_USAGE

    @pytest.mark.parametrize("flag", ["--timeout", "--poll-interval", "--subprocess-timeout"])
    def test_non_positive_args_rejected(self, flag):
        with pytest.raises(SystemExit) as exc_info:
            wait_ci.main([flag, "0"])
        assert exc_info.value.code == wait_ci.EXIT_USAGE

    def test_negative_registration_grace_rejected(self):
        with pytest.raises(SystemExit) as exc_info:
            wait_ci.main(["--registration-grace", "-1"])
        assert exc_info.value.code == wait_ci.EXIT_USAGE


class TestBaselineOnlyAndUsageErrors:
    """手順書どおりの`--baseline`だけの監視と、引数の誤りの終了コードを確かめる。"""

    def test_baseline_only_waits_with_saved_context(self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
        """`--baseline`だけの起動が、baselineの保存値を渡して実行一覧の取得関数を作成し、待機を終える。"""
        baseline = tmp_path / "baseline.json"
        _write_test_baseline(baseline, source_ref="refs/heads/develop")
        list_calls: list[tuple[object, ...]] = []

        def fake_run_list_fn(*args: object, **_kwargs: object) -> Callable[[str], list[dict[str, Any]]]:
            list_calls.append(args)
            return lambda _sha: [_run()]

        def fake_run(cmd: list[str], **_kwargs: Any) -> mock.Mock:
            if cmd[:2] == ["gh", "api"]:
                return mock.Mock(stdout=b"[]", returncode=0, stderr=b"")
            raise AssertionError(cmd)

        monkeypatch.setattr(wait_ci_polling, "_default_run_list_fn", fake_run_list_fn)
        with mock.patch("subprocess.run", side_effect=fake_run):
            result = wait_ci.main(["--baseline", str(baseline), "--registration-grace", "0", "--timeout", "5"])
        assert result == wait_ci.EXIT_SUCCESS
        assert [call[:3] for call in list_calls] == [("github", "owner/repository", "refs/heads/main")]

    def test_baseline_only_mismatch_of_explicit_value_still_fails(self, tmp_path: pathlib.Path) -> None:
        """指定した値はbaselineの保存値と比べ、不一致は終了コード3で終わる。"""
        baseline = tmp_path / "baseline.json"
        _write_test_baseline(baseline)
        assert wait_ci.main(["--baseline", str(baseline), "--ref", "refs/heads/other"]) == wait_ci.EXIT_GH_ERROR

    @pytest.mark.parametrize(
        "arguments",
        [
            ["--wait-sha", _FULL_SHA],
            ["--wait-sha", _FULL_SHA, "--repo", "owner/repository", "--ref", "refs/heads/main"],
            ["--write-baseline", "baseline.json", "--repo", "owner/repository", "--source-ref", "HEAD"],
            ["--wait-sha", "short"],
            ["--baseline", "baseline.json", "--timeout", "abc"],
        ],
    )
    def test_usage_error_returns_dedicated_exit_code(self, arguments: list[str], capsys: pytest.CaptureFixture[str]) -> None:
        """引数の誤りはtimeoutの2と区別した終了コード7と次の操作で終わる。"""
        with pytest.raises(SystemExit) as exc_info:
            wait_ci.main(arguments)
        assert exc_info.value.code == wait_ci.EXIT_USAGE
        assert wait_ci.EXIT_USAGE != wait_ci.EXIT_TIMEOUT
        assert "--help" in _next_action(capsys.readouterr().err)


class TestResolveShaViaMain:
    """`--sha`明示指定時も完全形式へ解決してから`wait_for_ci`へ渡す。"""

    def test_explicit_short_sha_is_resolved_to_full_form(self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
        captured: dict[str, object] = {}
        resolved_sha = "17561bd376c5fb8ace04153871b2a2d6993c380d"
        baseline = tmp_path / "baseline.json"
        _write_test_baseline(baseline, sha=resolved_sha)

        def fake_wait_for_ci(sha: str, *args: object, **kwargs: object) -> int:
            del args, kwargs
            captured["sha"] = sha
            return wait_ci.EXIT_SUCCESS

        def fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess:
            del kwargs
            assert cmd == ["git", "rev-parse", "--verify", "--end-of-options", "17561bd^{commit}"]
            return subprocess.CompletedProcess(cmd, returncode=0, stdout=f"{resolved_sha}\n", stderr="")

        monkeypatch.setattr(wait_ci, "wait_for_ci", fake_wait_for_ci)
        monkeypatch.setattr(subprocess, "run", fake_run)
        result = wait_ci.main(_main_args(baseline, sha="17561bd"))
        assert result == wait_ci.EXIT_SUCCESS
        assert captured["sha"] == resolved_sha

    def test_explicit_sha_resolution_failure_is_distinguished_from_no_runs(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: pathlib.Path
    ) -> None:
        baseline = tmp_path / "baseline.json"
        _write_test_baseline(baseline)

        def fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess:
            del cmd, kwargs
            return subprocess.CompletedProcess([], returncode=128, stdout="", stderr="fatal: bad revision")

        monkeypatch.setattr(subprocess, "run", fake_run)
        result = wait_ci.main(_main_args(baseline, sha="deadbee"))
        assert result == wait_ci.EXIT_GH_ERROR
        err = capsys.readouterr().err
        assert "deadbee" in err
        assert "commit解決に失敗" in err

    def test_missing_git_returns_sha_resolution_failure(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: pathlib.Path
    ) -> None:
        baseline = tmp_path / "baseline.json"
        _write_test_baseline(baseline)
        monkeypatch.setattr(subprocess, "run", mock.Mock(side_effect=FileNotFoundError("git")))

        result = wait_ci.main(_main_args(baseline, sha="17561bd"))

        assert result == wait_ci.EXIT_GH_ERROR
        assert "commit解決に失敗" in capsys.readouterr().err

    def test_option_like_sha_is_passed_after_end_of_options(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        baseline = tmp_path / "baseline.json"
        _write_test_baseline(baseline)

        def fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess:
            del kwargs
            assert cmd == ["git", "rev-parse", "--verify", "--end-of-options", "--not-an-option^{commit}"]
            return subprocess.CompletedProcess(cmd, returncode=128, stdout="", stderr="fatal: bad revision")

        monkeypatch.setattr(subprocess, "run", fake_run)

        result = wait_ci.main(_main_args(baseline, sha="--not-an-option"))

        assert result == wait_ci.EXIT_GH_ERROR
