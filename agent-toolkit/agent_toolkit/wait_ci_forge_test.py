"""CI待機の主題別テスト。"""

from __future__ import annotations

import json
import locale
import pathlib
import subprocess
import sys
from typing import Any
from unittest import mock

import pytest

from agent_toolkit import wait_ci
from agent_toolkit._atk import wait_ci_forge
from agent_toolkit._testing import git_repository as _git_repository_support
from agent_toolkit._testing.wait_ci_support import (
    _all_cancelled,
    _all_success,
    _gh_job_list,
    _gh_run_list,
    _glab_job_list,
    _glab_pipeline_list,
    _normalize_gitlab_pipeline,
    _resolve_forge,
    _run,
    _run_json_command,
)
from agent_toolkit._testing.wait_ci_support import (
    _restore_signal_handlers as _restore_wait_ci_signal_handlers,  # noqa: F401  # pytestがfixtureとして収集する。
)


class TestResolveForge:
    """forge判別の分岐を検証する。"""

    def test_explicit_value_is_returned_as_is(self) -> None:
        """明示指定時はリモート照会をしない。"""
        assert _resolve_forge("gitlab", "owner/repository") == "gitlab"
        assert _resolve_forge("github", "owner/repository") == "github"

    def test_auto_detects_github_from_repository_host(self) -> None:
        """ホスト名のラベルがgithubと一致する場合はgithubと判定する（SSH短縮形式）。"""
        assert _resolve_forge("auto", "git@github.com:o/r.git") == "github"

    def test_auto_detects_github_enterprise_host(self) -> None:
        """GitHub Enterprise Serverの標準的なホスト名もgithubと判定する。"""
        assert _resolve_forge("auto", "https://github.example.com/o/r.git") == "github"

    def test_auto_detects_gitlab_for_self_hosted_host(self) -> None:
        """ホスト名のラベルがgitlabと一致する私設ホストはgitlabと判定する（サブグループ付きHTTPS形式）。"""
        assert _resolve_forge("auto", "https://gitlab.example.com/group/sub/repo.git") == "gitlab"

    def test_auto_detects_gitlab_from_ssh_uri(self) -> None:
        """ポート付きSSH URI形式からもホスト名を抽出する。"""
        assert _resolve_forge("auto", "ssh://git@gitlab.example.com:2222/group/repo.git") == "gitlab"

    def test_auto_does_not_match_host_label_by_substring(self) -> None:
        """ラベルの部分一致では判定せず、無関係なホストを誤分類しない。"""
        assert _resolve_forge("auto", "https://notgithub.example.com/o/r.git") is None

    @pytest.mark.parametrize("repository", ["owner/repository", "git@git.example.com:o/r.git", "invalid"])
    def test_auto_returns_none_without_recognized_host(self, repository: str, tmp_path: pathlib.Path) -> None:
        assert _resolve_forge("auto", repository, cwd=tmp_path) is None

    @pytest.mark.parametrize(
        ("remotes", "expected"),
        [
            pytest.param({"origin": "git@github.com:Owner/Repo.git"}, "github", id="github-ssh"),
            pytest.param({"origin": "https://gitlab.example.com/owner/repo.git"}, "gitlab", id="gitlab-https"),
            pytest.param({"origin": "git@github.com:other/repo.git"}, None, id="no-matching-remote"),
            pytest.param(
                {"origin": "git@github.com:owner/repo.git", "mirror": "https://gitlab.example.com/owner/repo.git"},
                None,
                id="ambiguous-forges",
            ),
        ],
    )
    def test_auto_resolves_short_repository_from_matching_remote(
        self, tmp_path: pathlib.Path, remotes: dict[str, str], expected: str | None
    ) -> None:
        """`--repo`の`owner/repo`形式は、作業ディレクトリのGit remoteのうちpathが一致するもののホストで判別する。

        手順書どおりの`owner/repo`で`--forge`を省くと必ず終了コード3になる不整合を検出する。
        一致するremoteが無い場合と、一致したremoteのforgeが分かれる場合は推測せず判別不能とする。
        """
        _git_repository_support.init_repository(tmp_path)
        for name, url in remotes.items():
            _git_repository_support.run_git(tmp_path, "remote", "add", name, url)
        assert _resolve_forge("auto", "owner/repo", cwd=tmp_path) == expected


class TestNormalizeGitlabPipeline:
    """GitLabパイプラインの正規化を検証する。"""

    def test_success_maps_to_completed_success(self) -> None:
        """successは完了かつconclusion=successへ写像される。"""
        record = _normalize_gitlab_pipeline({"id": 7, "status": "success", "name": "build", "sha": "abc"})
        assert record["status"] == "completed"
        assert record["conclusion"] == "success"
        assert record["databaseId"] == 7
        assert record["headSha"] == "abc"

    def test_canceled_spelling_is_converted(self) -> None:
        """GitLabのcanceledをGitHub綴りのcancelledへ変換する。"""
        record = _normalize_gitlab_pipeline({"id": 8, "status": "canceled"})
        assert record["conclusion"] == "cancelled"
        assert _all_cancelled([record]) is True

    def test_manual_is_completed_and_not_success(self) -> None:
        """manualは完了かつ非成功として扱う。"""
        record = _normalize_gitlab_pipeline({"id": 9, "status": "manual"})
        assert record["status"] == "completed"
        assert _all_success([record]) is False

    def test_pending_statuses_are_incomplete(self) -> None:
        """進行中系ステータスは未完了として扱う。"""
        for status in ("created", "pending", "running", "canceling", "preparing", "scheduled"):
            record = _normalize_gitlab_pipeline({"id": 1, "status": status})
            assert record["status"] != "completed", status

    def test_empty_name_falls_back_to_identifier(self) -> None:
        """nameが空の場合は識別子を含む代替表示にする。"""
        record = _normalize_gitlab_pipeline({"id": 42, "status": "success", "name": ""})
        assert "42" in str(record["name"])

    def test_unknown_status_is_completed_and_not_success(self) -> None:
        """未知のステータスは完了かつ非成功として扱う。"""
        record = _normalize_gitlab_pipeline({"id": 5, "status": "unknown_future_state"})
        assert record["status"] == "completed"
        assert _all_success([record]) is False


class TestRunJsonCommand:
    """forge CLIのJSON出力をbytes境界でUTF-8として厳格にデコードする。"""

    def test_decodes_utf8_from_binary_subprocess(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """CP932ロケールでもbytes境界からUTF-8の非ASCII JSONを取得する。"""
        code = (
            "import json, sys; "
            "value = chr(0x65e5) + chr(0x672c) + chr(0x8a9e) + chr(0x2014); "
            "sys.stdout.buffer.write(json.dumps({'name': value}, ensure_ascii=False).encode('utf-8'))"
        )
        original_run = subprocess.run
        monkeypatch.setattr(locale, "getencoding", lambda: "cp932")

        def run_binary(
            command: list[str],
            *,
            capture_output: bool,
            check: bool,
            timeout: float,
            **kwargs: Any,
        ) -> subprocess.CompletedProcess[bytes]:
            return original_run(
                command,
                capture_output=capture_output,
                check=check,
                timeout=timeout,
                **kwargs,
            )

        monkeypatch.setattr(subprocess, "run", run_binary)

        payload = _run_json_command([sys.executable, "-c", code], 5.0, "test command")

        assert payload == {"name": "日本語—"}

    def test_non_utf8_output_raises_run_list_error(self) -> None:
        """不正UTF-8をJSON型エラーへ変質させず境界エラーとして返す。"""
        code = "import sys; sys.stdout.buffer.write(bytes([0xff]))"

        with pytest.raises(wait_ci.RunListError, match="UTF-8") as exc_info:
            _run_json_command([sys.executable, "-c", code], 5.0, "test command")

        assert isinstance(exc_info.value.__cause__, UnicodeDecodeError)


class TestGhRunList:
    """GitHub一覧取得がrepository・ref・SHAの全てを明示することを確認する。"""

    @pytest.mark.parametrize(
        ("repository", "ref", "short_ref"),
        [
            ("owner/first", "refs/heads/main", "main"),
            ("owner/second", "refs/heads/release/v2", "release/v2"),
        ],
    )
    def test_command_targets_requested_repository_and_ref(
        self,
        monkeypatch: pytest.MonkeyPatch,
        repository: str,
        ref: str,
        short_ref: str,
    ) -> None:
        calls: list[tuple[list[str], float, str]] = []

        def fake_run_json(command: list[str], timeout: float, description: str) -> list[object]:
            calls.append((command, timeout, description))
            return []

        monkeypatch.setattr(wait_ci_forge, "_run_forge_json_command", fake_run_json)
        assert _gh_run_list(repository, ref, "deadbeef", 1.0) == []
        command, timeout, description = calls[0]
        assert command[command.index("--repo") + 1] == repository
        assert command[command.index("--branch") + 1] == short_ref
        assert command[command.index("--commit") + 1] == "deadbeef"
        assert timeout == 1.0
        assert description == "gh run list"


class TestGlabPipelineList:
    """glab呼び出しと応答検証を確認する。"""

    def test_command_uses_sha_and_json_output(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """glab ci listへSHAとJSON出力指定を渡す。"""
        calls: list[tuple[list[str], float, str]] = []

        def fake_run_json(command: list[str], timeout: float, description: str, **_kwargs: object) -> list[dict[str, object]]:
            calls.append((command, timeout, description))
            return [{"id": 1, "status": "success"}]

        monkeypatch.setattr(wait_ci_forge, "_run_forge_json_command", fake_run_json)
        records = _glab_pipeline_list("group/repository", "refs/heads/main", "deadbeef", 1.0)
        command, timeout, description = calls[0]
        assert command[:3] == ["glab", "ci", "list"]
        assert command[command.index("--repo") + 1] == "group/repository"
        assert command[command.index("--ref") + 1] == "main"
        assert "--sha" in command and "deadbeef" in command
        assert command[command.index("-F") + 1] == "json"
        assert timeout == 1.0
        assert description == "glab ci list"
        assert records[0]["conclusion"] == "success"

    def test_non_zero_exit_raises_run_list_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """非ゼロ終了はRunListErrorとして扱う。"""
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda cmd, **_kw: subprocess.CompletedProcess(cmd, 1, stdout=b"", stderr=b"boom"),
        )
        with pytest.raises(wait_ci.RunListError):
            _glab_pipeline_list("group/repository", "refs/heads/main", "deadbeef", 1.0)

    def test_unexpected_shape_raises_run_list_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """配列以外の応答はRunListErrorとして扱う。"""
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda cmd, **_kw: subprocess.CompletedProcess(cmd, 0, stdout=b'{"message":"x"}', stderr=b""),
        )
        with pytest.raises(wait_ci.RunListError):
            _glab_pipeline_list("group/repository", "refs/heads/main", "deadbeef", 1.0)


class TestGhJobList:
    def test_fetches_latest_jobs_from_all_pages(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls: list[list[str]] = []
        payload = [
            {"jobs": [{"id": 11, "name": "build", "status": "completed", "conclusion": "success", "html_url": "u1"}]},
            {"jobs": [{"id": 12, "name": "test", "status": "completed", "conclusion": "failure", "html_url": "u2"}]},
        ]

        def fake_run(cmd: list[str], **_kw: object) -> subprocess.CompletedProcess:
            calls.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(payload).encode(), stderr=b"")

        monkeypatch.setattr(subprocess, "run", fake_run)
        jobs = _gh_job_list("owner/repository", _run(db_id=7), 1.0)
        command = calls[0]
        assert "repos/owner/repository/actions/runs/7/jobs?filter=latest&per_page=100" in command
        assert "--paginate" in command
        assert "--slurp" in command
        assert "--jq" not in command
        assert [job["databaseId"] for job in jobs] == [11, 12]

    def test_custom_host_and_repository_are_explicit(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls: list[list[str]] = []

        def fake_run(cmd: list[str], **_kw: object) -> subprocess.CompletedProcess:
            calls.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, stdout=b"[]", stderr=b"")

        monkeypatch.setattr(subprocess, "run", fake_run)
        assert not _gh_job_list("https://github.example.com/owner/repository.git", _run(db_id=7), 1.0)
        assert "repos/owner/repository/actions/runs/7/jobs?filter=latest&per_page=100" in calls[0]
        assert calls[0][calls[0].index("--hostname") + 1] == "github.example.com"

    @pytest.mark.parametrize(
        "payload",
        ["{}", '[{"jobs":{}}]', '[{"other":[]}]', '[{"jobs":[1]}]', "not-json"],
    )
    def test_invalid_response_raises_run_list_error(self, monkeypatch: pytest.MonkeyPatch, payload: str) -> None:
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda cmd, **_kw: subprocess.CompletedProcess(cmd, 0, stdout=payload.encode(), stderr=b""),
        )
        with pytest.raises(wait_ci.RunListError):
            _gh_job_list("owner/repository", _run(), 1.0)

    def test_invalid_run_id_raises_run_list_error(self) -> None:
        with pytest.raises(wait_ci.RunListError):
            _gh_job_list("owner/repository", {"databaseId": None}, 1.0)

    @pytest.mark.parametrize(
        "side_effect",
        [subprocess.TimeoutExpired("gh", 1.0), FileNotFoundError("gh")],
    )
    def test_command_exception_raises_run_list_error(self, monkeypatch: pytest.MonkeyPatch, side_effect: Exception) -> None:
        monkeypatch.setattr(subprocess, "run", mock.Mock(side_effect=side_effect))
        with pytest.raises(wait_ci.RunListError):
            _gh_job_list("owner/repository", _run(), 1.0)

    def test_non_zero_exit_raises_run_list_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda cmd, **_kw: subprocess.CompletedProcess(cmd, 1, stdout=b"", stderr=b"boom"),
        )
        with pytest.raises(wait_ci.RunListError):
            _gh_job_list("owner/repository", _run(), 1.0)


class TestGlabJobList:
    def test_fetches_all_non_retried_jobs_without_fixed_host(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls: list[list[str]] = []
        payload = [
            {
                "id": 21,
                "name": "build",
                "status": "failed",
                "allow_failure": False,
                "web_url": "u1",
            },
            {
                "id": 22,
                "name": "lint",
                "status": "canceled",
                "allow_failure": True,
                "web_url": "u2",
            },
        ]

        def fake_run(cmd: list[str], **_kw: object) -> subprocess.CompletedProcess:
            calls.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(payload).encode(), stderr=b"")

        monkeypatch.setattr(subprocess, "run", fake_run)
        jobs = _glab_job_list("group/sub/repository", _run(db_id=8), 1.0)
        command = calls[0]
        assert command[:2] == ["glab", "api"]
        assert "projects/group%2Fsub%2Frepository/pipelines/8/jobs?include_retried=false&per_page=100" in command
        assert "--paginate" in command
        assert not any(item.startswith("--hostname") for item in command)
        assert jobs[0]["conclusion"] == "failure"
        assert jobs[1]["status"] == "cancelled"

    @pytest.mark.parametrize("allow_failure", [None, 0, "false"])
    def test_missing_or_non_bool_allow_failure_raises(self, monkeypatch: pytest.MonkeyPatch, allow_failure: object) -> None:
        item: dict[str, object] = {"id": 21, "name": "build", "status": "failed"}
        if allow_failure is not None:
            item["allow_failure"] = allow_failure
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda cmd, **_kw: subprocess.CompletedProcess(cmd, 0, stdout=json.dumps([item]).encode(), stderr=b""),
        )
        with pytest.raises(wait_ci.RunListError):
            _glab_job_list("group/repository", _run(), 1.0)

    def test_invalid_json_raises_run_list_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda cmd, **_kw: subprocess.CompletedProcess(cmd, 0, stdout=b"not-json", stderr=b""),
        )
        with pytest.raises(wait_ci.RunListError):
            _glab_job_list("group/repository", _run(), 1.0)

    def test_unexpected_shape_raises_run_list_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda cmd, **_kw: subprocess.CompletedProcess(cmd, 0, stdout=b'{"message":"x"}', stderr=b""),
        )
        with pytest.raises(wait_ci.RunListError):
            _glab_job_list("group/repository", _run(), 1.0)
