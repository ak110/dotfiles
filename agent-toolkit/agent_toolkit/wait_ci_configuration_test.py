"""CI待機の主題別テスト。"""

from __future__ import annotations

import json
import subprocess
from typing import Any

import pytest

from agent_toolkit import wait_ci
from agent_toolkit._atk import wait_ci_polling
from agent_toolkit._common import json_command
from agent_toolkit._testing.wait_ci_support import (
    _FULL_SHA,
    _github_ci_configured,
    _port_endpoint_options,
    _wait_for_ci_without_runs,
)
from agent_toolkit._testing.wait_ci_support import (
    _restore_signal_handlers as _restore_wait_ci_signal_handlers,  # noqa: F401  # pytestがfixtureとして収集する。
)


class TestGithubCiConfiguration:
    """GitHub tree応答によるCI定義不在の即時判定と代替処理。"""

    @pytest.mark.parametrize(
        "payload",
        [
            {"truncated": False, "tree": []},
            {"truncated": False, "tree": [{"type": "tree", "path": ".github/workflows"}]},
        ],
    )
    def test_no_workflow_blob_is_confirmed_absent(self, monkeypatch: pytest.MonkeyPatch, payload: dict) -> None:
        def fake_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[bytes]:
            assert command == [
                "gh",
                "api",
                "-X",
                "GET",
                "repos/owner/repository/git/trees/sha1?recursive=1",
            ]
            return subprocess.CompletedProcess(command, 0, json.dumps(payload).encode(), b"")

        monkeypatch.setattr(subprocess, "run", fake_run)
        assert _github_ci_configured("owner/repository", "sha1", 10.0) is False

    def test_workflow_blob_is_confirmed_present(self, monkeypatch: pytest.MonkeyPatch) -> None:
        payload = {"truncated": False, "tree": [{"type": "blob", "path": ".github/workflows/test.yml"}]}
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda command, **_kwargs: subprocess.CompletedProcess(command, 0, json.dumps(payload).encode(), b""),
        )
        assert _github_ci_configured("owner/repository", "sha1", 10.0) is True

    @pytest.mark.parametrize(
        "path",
        [
            ".github/workflows/README.md",
            ".github/workflows/archive/disabled.yml",
            ".github/workflows/archive/disabled.yaml",
        ],
    )
    def test_non_workflow_blob_is_confirmed_absent(self, monkeypatch: pytest.MonkeyPatch, path: str) -> None:
        payload = {"truncated": False, "tree": [{"type": "blob", "path": path}]}
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda command, **_kwargs: subprocess.CompletedProcess(command, 0, json.dumps(payload).encode(), b""),
        )
        assert _github_ci_configured("owner/repository", "sha1", 10.0) is False

    @pytest.mark.parametrize(
        "response",
        [
            {"truncated": True, "tree": []},
            b"not-json",
        ],
    )
    def test_uncertain_tree_response_falls_back(self, monkeypatch: pytest.MonkeyPatch, response: object) -> None:
        def fake_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[bytes]:
            stdout = response if isinstance(response, bytes) else json.dumps(response).encode()
            return subprocess.CompletedProcess(command, 0, stdout, b"")

        monkeypatch.setattr(subprocess, "run", fake_run)
        assert _github_ci_configured("owner/repository", "sha1", 10.0) is None

    @pytest.mark.parametrize("status", [403, 404, 422])
    def test_tree_cli_failure_falls_back_to_registration_grace(
        self,
        monkeypatch: pytest.MonkeyPatch,
        status: int,
    ) -> None:
        """tree取得のCLI失敗時はrun登録猶予へ後退する。"""

        def fake_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[bytes]:
            if command[1:2] == ["api"]:
                return subprocess.CompletedProcess(command, 1, b"", f"HTTP {status}".encode())
            return subprocess.CompletedProcess(command, 0, b"[]", b"")

        monkeypatch.setattr(subprocess, "run", fake_run)
        assert _wait_for_ci_without_runs("owner/repository") == wait_ci.EXIT_NO_RUNS

    def test_gitlab_uncertain_lint_skips_tree_query_and_uses_existing_wait(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """GitLabの判定不能なLint応答ではtreeを照会せず既存の待機を使う。"""
        monkeypatch.setattr(
            wait_ci_polling,
            "_github_ci_configured",
            lambda *_args: (_ for _ in ()).throw(AssertionError("GitHub treeを照会しないこと")),
        )
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda command, **_kwargs: subprocess.CompletedProcess(command, 0, b"[]", b""),
        )
        assert _wait_for_ci_without_runs("group/repository", forge="gitlab") == wait_ci.EXIT_NO_RUNS

    def test_no_definition_exits_before_run_listing(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setattr(wait_ci_polling, "_github_ci_configured", lambda *_args: False)
        result = _wait_for_ci_without_runs("owner/repository")
        assert result == wait_ci.EXIT_NO_CI_CONFIG
        # CI定義が無い場合は、CI検収を不要として進めてよいことを次の操作で示す。
        assert "次の操作: CI検収は不要として次の工程へ進む" in capsys.readouterr().err


class TestGitlabCiExclusion:
    """公開CLIからCI Lintの確定的非生成と判定不能を区別する。"""

    @pytest.mark.parametrize("ref", ["refs/heads/release/v2", "refs/tags/v2"])
    def test_lint_exclusion_exits_before_listing(self, monkeypatch, capsys, ref):
        """確定的なworkflow除外は登録待機せず、対象と理由を証拠として返す。"""
        error = "The pipeline did not run. Review the workflow:rules configuration for the pipeline."
        calls = []

        def run(command, **kwargs):
            calls.append(command)
            if command[:3] == ["glab", "ci", "list"]:
                return subprocess.CompletedProcess(command, 0, b"[]", b"")
            assert command[:2] == ["glab", "api"]
            assert kwargs["env"]["GITLAB_HOST"] == "gitlab.example.com:8443"
            assert kwargs["env"]["GLAB_API_PROTOCOL"] == "https"
            assert command[2] == (
                f"projects/group%2Fsub%2Frepo/ci/lint?content_ref={_FULL_SHA}"
                f"&dry_run=true&dry_run_ref={'release%2Fv2' if 'heads' in ref else 'v2'}&include_jobs=true"
            )
            assert command[command.index("-X") + 1] == "GET"
            # スキーマ: https://docs.gitlab.com/api/lint/#validate-existing-cicd-configuration
            payload = {"valid": False, "jobs": [], "errors": [error]}
            return subprocess.CompletedProcess(command, 0, json.dumps(payload).encode(), b"")

        monkeypatch.setattr(json_command.subprocess, "run", run)
        repository = "https://gitlab.example.com:8443/group/sub/repo"
        args = _port_endpoint_options(repository)
        args[args.index("refs/heads/main")] = ref
        assert wait_ci.main(["--wait-sha", _FULL_SHA, *args]) == 8
        assert len(calls) == 1
        output = capsys.readouterr().err
        for value in (
            "CI実行対象外",
            "project=gitlab.example.com:8443/group/sub/repo",
            _FULL_SHA,
            ref,
            error,
            "ローカル全体検証",
        ):
            assert value in output

    @pytest.mark.parametrize(
        ("repository", "project"),
        [
            (
                "https://example-user:example-password@gitlab.example.com:8443/group/repo.git",
                "gitlab.example.com:8443/group/repo",
            ),
            ("ssh://example-user@gitlab.example.com/group/repo", "gitlab.example.com/group/repo"),
            ("https://example-user:example-password@[::1]:8443/group/repo", "[::1]:8443/group/repo"),
            ("group/repo", "group/repo"),
        ],
    )
    def test_exclusion_project_omits_synthetic_userinfo(self, monkeypatch, capsys, repository, project):
        """合成userinfoを含むURLでも、公開待機の証拠にはホストとprojectだけを表示する。"""
        error = "The pipeline did not run. Review the workflow:rules configuration for the pipeline."
        payload = {"valid": False, "jobs": [], "errors": [error]}
        monkeypatch.setattr(
            json_command.subprocess,
            "run",
            lambda command, **_kwargs: subprocess.CompletedProcess(command, 0, json.dumps(payload).encode(), b""),
        )
        assert (
            wait_ci.wait_for_ci(
                _FULL_SHA,
                1,
                0,
                0,
                False,
                1,
                repository=repository,
                ref="refs/heads/main",
                source_ref="HEAD",
                baseline_ids=frozenset(),
                forge="gitlab",
            )
            == wait_ci.EXIT_CI_EXCLUDED
        )
        output = capsys.readouterr().err
        assert "example-user" not in output
        assert "example-password" not in output
        for value in (f"project={project},", _FULL_SHA, "ref=refs/heads/main", error):
            assert value in output

    @pytest.mark.parametrize(
        "response",
        [
            {"valid": True, "jobs": [], "errors": []},
            {"valid": "false", "jobs": [], "errors": []},
            {"valid": 0, "jobs": [], "errors": []},
            {"valid": False, "jobs": [], "errors": []},
            {"valid": False, "jobs": [], "errors": "The pipeline did not run."},
            {"valid": False, "jobs": [], "errors": [None]},
            {"valid": False, "jobs": [], "errors": ["jobs config should contain at least one visible job"]},
            {},
            [],
            b"not-json",
            403,
            404,
            422,
            FileNotFoundError("glab"),
            subprocess.TimeoutExpired("glab", 1),
        ],
    )
    def test_uncertain_lint_uses_existing_registration_wait(self, monkeypatch, response):
        """不正・失敗・非生成以外の応答を実行対象外と扱わない。"""
        listed = []

        def run(command, **_kwargs):
            if command[:2] == ["glab", "api"]:
                if isinstance(response, Exception):
                    raise response
                if isinstance(response, int):
                    return subprocess.CompletedProcess(command, 1, b"", f"HTTP {response}".encode())
                stdout = response if isinstance(response, bytes) else json.dumps(response).encode()
                return subprocess.CompletedProcess(command, 0, stdout, b"")
            assert command[:3] == ["glab", "ci", "list"]
            listed.append(command)
            return subprocess.CompletedProcess(command, 0, b"[]", b"")

        monkeypatch.setattr(json_command.subprocess, "run", run)
        assert wait_ci.main(["--wait-sha", _FULL_SHA, *_port_endpoint_options("group/repo")]) == wait_ci.EXIT_NO_RUNS
        assert listed

    @pytest.mark.parametrize(
        "mutation",
        ["jobs", "missing-jobs", "null-jobs", "object-jobs", "mixed-errors", "missing-valid", "string-valid", "zero-valid"],
    )
    def test_incomplete_or_conflicting_exclusion_is_not_accepted(self, monkeypatch, mutation):
        """既知の非生成文言があっても、必須条件の欠落と他のエラーを受理しない。"""
        error = "The pipeline did not run. Review the workflow:rules configuration for the pipeline."
        payload: dict[str, Any] = {"valid": False, "jobs": [], "errors": [error]}
        if mutation == "jobs":
            payload["jobs"] = [{"name": "build"}]
        elif mutation == "missing-jobs":
            del payload["jobs"]
        elif mutation == "null-jobs":
            payload["jobs"] = None
        elif mutation == "object-jobs":
            payload["jobs"] = {}
        elif mutation == "mixed-errors":
            payload["errors"].append("invalid YAML")
        elif mutation == "string-valid":
            payload["valid"] = "false"
        elif mutation == "zero-valid":
            payload["valid"] = 0
        else:
            del payload["valid"]

        def run(command, **_kwargs):
            response = payload if command[:2] == ["glab", "api"] else []
            return subprocess.CompletedProcess(command, 0, json.dumps(response).encode(), b"")

        monkeypatch.setattr(json_command.subprocess, "run", run)
        assert wait_ci.main(["--wait-sha", _FULL_SHA, *_port_endpoint_options("group/repo")]) == wait_ci.EXIT_NO_RUNS

    @pytest.mark.parametrize("custom_fetcher", ["run", "job", "both"])
    def test_custom_fetchers_do_not_query_lint(self, monkeypatch, custom_fetcher):
        """取得関数を注入した呼び出しへ外部Lint照会を追加しない。"""
        calls = []

        def run(command, **_kwargs):
            calls.append(command)
            assert command[:3] == ["glab", "ci", "list"]
            return subprocess.CompletedProcess(command, 0, b"[]", b"")

        monkeypatch.setattr(json_command.subprocess, "run", run)
        assert (
            wait_ci.wait_for_ci(
                _FULL_SHA,
                10,
                1,
                0,
                False,
                1,
                repository="group/repo",
                ref="refs/heads/main",
                source_ref="HEAD",
                baseline_ids=frozenset(),
                forge="gitlab",
                run_list_fn=(lambda _sha: []) if custom_fetcher in {"run", "both"} else None,
                job_list_fn=(lambda _run: []) if custom_fetcher in {"job", "both"} else None,
            )
            == wait_ci.EXIT_NO_RUNS
        )
        assert bool(calls) is (custom_fetcher == "job")
