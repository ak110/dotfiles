"""`alerts`モジュールのテスト。公開API経由でDI（依存性注入）駆動する。"""

import argparse
import contextlib
import datetime
import json
import pathlib
import subprocess
from collections.abc import Callable
from typing import Any

import pytest

from agent_toolkit import atk
from agent_toolkit._atk.wi import (
    alerts,  # noqa: E402  # pylint: disable=wrong-import-position
    process_loop_alerts,
)
from agent_toolkit._atk.wi import sync as _wi_sync
from agent_toolkit._atk.wi.constants import WI_STATES
from agent_toolkit._common import json_command as _json_command  # noqa: E402  # pylint: disable=wrong-import-position


def test_collect_github_ci_failures_latest_completed_only() -> None:
    """ワークフローごとに直近の完了runのみを確認し、失敗中のみアラート化する。"""
    runs = [
        {"workflowName": "CI", "status": "completed", "conclusion": "failure", "databaseId": 2},
        {"workflowName": "CI", "status": "completed", "conclusion": "success", "databaseId": 1},
        {"workflowName": "Docs", "status": "completed", "conclusion": "success", "databaseId": 3},
    ]
    result = alerts.collect_github_ci_failures("owner/repo", "master", run_list_fn=lambda _r, _b: runs)
    assert [alert.keys for alert in result] == [("github-run:2",)]


def test_collect_github_ci_failures_skips_in_progress_latest() -> None:
    """進行中runを除外し、直近の完了runを判定する。"""
    runs = [
        {"workflowName": "CI", "status": "in_progress", "databaseId": 3},
        {"workflowName": "CI", "status": "completed", "conclusion": "failure", "databaseId": 2},
    ]
    result = alerts.collect_github_ci_failures("owner/repo", "master", run_list_fn=lambda _r, _b: runs)
    assert [alert.keys for alert in result] == [("github-run:2",)]


def test_collect_github_ci_failures_keeps_schedule_failure_after_push_success() -> None:
    """同じワークフローの`push`の成功が後にあっても、`schedule`の直近完了runの失敗をアラート化する。

    `event`は`alerts._run_gh_run_list`が`gh run list --json`で取得する公開フィールドである。
    """
    runs = [
        {"workflowName": "Audit", "event": "push", "status": "completed", "conclusion": "success", "databaseId": 5},
        {"workflowName": "Audit", "event": "schedule", "status": "completed", "conclusion": "failure", "databaseId": 4},
        {"workflowName": "Audit", "event": "schedule", "status": "completed", "conclusion": "success", "databaseId": 3},
    ]
    result = alerts.collect_github_ci_failures("owner/repo", "master", run_list_fn=lambda _r, _b: runs)
    assert [alert.keys for alert in result] == [("github-run:4",)]


def _gitlab_schedule_api(endpoints: list[str]) -> alerts.GlabApiFn:
    """Pipeline Scheduleの一覧と個別の取得へ応答する`glab api`の代用を返す。

    応答の形は2026年10月6日の`glab api 'projects/:id/pipeline_schedules'`（一覧は`active`・`ref`・`cron`を持ち
    `last_pipeline`を持たない）と`pipeline_schedules/<ID>`（`last_pipeline`の`id`・`status`・`web_url`）の観測から写した。
    """
    project = "projects/group%2Fsub%2Frepo/pipeline_schedules"
    responses: dict[str, object] = {
        f"{project}?per_page=100": [
            {
                "id": 494,
                "description": "model deprecation weekday check",
                "ref": "develop",
                "cron": "30 8 * * 1-5",
                "active": True,
            },
            {"id": 523, "description": "daily log monitor", "ref": "develop", "cron": "0 12 * * *", "active": True},
            {"id": 9, "description": "retired", "ref": "develop", "cron": "0 0 * * *", "active": False},
        ],
        f"{project}/494": {"id": 494, "last_pipeline": {"id": 437746, "status": "success", "ref": "develop", "web_url": "u1"}},
        f"{project}/523": {"id": 523, "last_pipeline": {"id": 437813, "status": "failed", "ref": "develop", "web_url": "u2"}},
    }

    def api(host: str, endpoint: str) -> object:
        assert host == "gitlab.example.com"
        endpoints.append(endpoint)
        return responses[endpoint]

    return api


def test_collect_new_alerts_reports_schedule_failure_hidden_by_later_pipeline(tmp_path: pathlib.Path) -> None:
    """後に起動したpushのパイプラインが実行中でも、有効なスケジュールの失敗したパイプラインを1件アラート化する。

    無効なスケジュールは照会しない。ブランチの直近1件だけを確かめると、失敗が後の実行に隠れてAWIへ入らない。
    """
    endpoints: list[str] = []
    branch_pipelines = [{"id": 437860, "status": "running"}, {"id": 437813, "status": "failed"}]
    result = alerts.collect_new_alerts(
        "gitlab.example.com/group/sub/repo",
        "develop",
        tmp_path,
        forge="gitlab",
        ci_list_fn=lambda _r, _b: branch_pipelines,
        glab_api_fn=_gitlab_schedule_api(endpoints),
    )
    assert [alert.keys for alert in result] == [("gitlab-pipeline:437813",)]
    assert "daily log monitor" in result[0].body
    assert not any(endpoint.endswith("/9") for endpoint in endpoints)


def test_check_and_submit_alerts_submits_one_awi_per_pipeline(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """ブランチの確認と定期実行の確認が同じパイプラインを返しても1件だけ投入し、次の確認では投入しない。"""
    notes = tmp_path / "private-notes"
    _prepare_alert_submission(monkeypatch, notes)

    def submit() -> alerts.AlertCheckResult:
        return alerts.check_and_submit_alerts(
            notes,
            "gitlab.example.com/group/sub/repo",
            tmp_path / "repo",
            forge="gitlab",
            now=datetime.datetime(2026, 1, 1),
            git_fn=lambda _p, args: "refs/remotes/origin/develop" if args[0] == "symbolic-ref" else None,
            ci_list_fn=lambda _r, _b: [{"id": 437813, "status": "failed"}],
            glab_api_fn=_gitlab_schedule_api([]),
        )

    assert submit().submitted == 1
    assert submit().submitted == 0
    assert list(_saved_awis_by_heading(notes)) == ["# パイプライン437813失敗"]


def test_collect_gitlab_ci_failures_only_when_latest_failed() -> None:
    """最新パイプラインが失敗した場合のみアラート化する。"""
    success = [{"id": 1, "status": "success"}]
    assert not alerts.collect_gitlab_ci_failures("owner/repo", "master", ci_list_fn=lambda _r, _b: success)
    failed = [{"id": 2, "status": "failed", "web_url": "u", "sha": "abc12345"}]
    result = alerts.collect_gitlab_ci_failures("owner/repo", "master", ci_list_fn=lambda _r, _b: failed)
    assert [alert.keys for alert in result] == [("gitlab-pipeline:2",)]


def test_resolve_target_branch_paths() -> None:
    """追跡先を優先し、失敗時は`origin/HEAD`が指すbranchへ退避する。"""

    def upstream_git(_path: pathlib.Path, args: list[str]) -> str | None:
        if args[0] == "rev-parse":
            return "origin/feature/foo"
        if args == ["remote"]:
            return "origin"
        raise AssertionError(args)

    assert alerts.resolve_target_branch(pathlib.Path("/repo"), git_fn=upstream_git) == "feature/foo"

    def fallback_git(_path: pathlib.Path, args: list[str]) -> str | None:
        if args[0] == "rev-parse":
            return None
        return "refs/remotes/origin/master"

    assert alerts.resolve_target_branch(pathlib.Path("/repo"), git_fn=fallback_git) == "master"
    assert alerts.resolve_target_branch(pathlib.Path("/repo"), git_fn=lambda _p, _a: None) is None


def test_collect_new_alerts_filters_keys_per_repository(tmp_path: pathlib.Path) -> None:
    """同一リポジトリの既出キーだけを重複除外する。"""
    notes = tmp_path / "private-notes"
    adopted = notes / "adopted"
    adopted.mkdir(parents=True)
    (adopted / "other.md").write_text(
        "---\ntarget_repo: github.com/other/repo\ntype: awi\nalert_keys: github-run:21\n---\n\n本文\n",
        encoding="utf-8",
    )
    runs = [{"workflowName": "CI", "status": "completed", "conclusion": "failure", "databaseId": 21}]
    result = alerts.collect_new_alerts("github.com/owner/repo", "main", notes, forge="github", run_list_fn=lambda _r, _b: runs)
    assert [alert.keys for alert in result] == [("github-run:21",)]
    (adopted / "same.md").write_text(
        "---\ntarget_repo: github.com/owner/repo\ntype: awi\nalert_keys: github-run:21\n---\n\n本文\n",
        encoding="utf-8",
    )
    assert not alerts.collect_new_alerts(
        "github.com/owner/repo", "main", notes, forge="github", run_list_fn=lambda _r, _b: runs
    )


def test_existing_alert_keys_parses_absent_multiple_and_empty(tmp_path: pathlib.Path) -> None:
    """`alert_keys`未指定・カンマ区切り複数・空文字列の各書式を公開関数経由で検証する。

    `_parse_alert_keys`はモジュール非公開のため、フロントマターを持つAWIファイルを
    実際に配置して`existing_alert_keys`経由で検証する
    （`agent-toolkit:writing-standards`の`references/testing.md`「private関数の直接テスト禁止」節に従う）。
    """
    notes = tmp_path / "private-notes"
    inbox = notes / "inbox"
    inbox.mkdir(parents=True)
    (inbox / "absent.md").write_text(
        "---\ntarget_repo: github.com/owner/repo\ntype: awi\n---\n\n本文\n",
        encoding="utf-8",
    )
    assert alerts.existing_alert_keys(notes, "github.com/owner/repo") == set()

    (inbox / "absent.md").unlink()
    (inbox / "multiple.md").write_text(
        "---\ntarget_repo: github.com/owner/repo\ntype: awi\n"
        "alert_keys: github-dependabot:21, github-dependabot:22\n---\n\n本文\n",
        encoding="utf-8",
    )
    assert alerts.existing_alert_keys(notes, "github.com/owner/repo") == {
        "github-dependabot:21",
        "github-dependabot:22",
    }

    (inbox / "multiple.md").unlink()
    (inbox / "empty.md").write_text(
        "---\ntarget_repo: github.com/owner/repo\ntype: awi\nalert_keys: \n---\n\n本文\n",
        encoding="utf-8",
    )
    assert alerts.existing_alert_keys(notes, "github.com/owner/repo") == set()


def _prepare_alert_submission(monkeypatch: pytest.MonkeyPatch, notes: pathlib.Path) -> None:
    """外部更新を無効化し、保存した本文を確認できるAWI領域を準備する。"""
    (notes / "inbox").mkdir(parents=True)

    def no_repo_lock(*_args: object, **_kwargs: object) -> contextlib.AbstractContextManager[None]:
        return contextlib.nullcontext()

    def no_repository_update(*_args: object, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr(_wi_sync, "repo_lock", no_repo_lock)
    monkeypatch.setattr(_wi_sync, "pull", no_repository_update)
    monkeypatch.setattr(_wi_sync, "commit_and_push", no_repository_update)


def _saved_awis_by_heading(notes: pathlib.Path) -> dict[str, str]:
    """保存された通常AWIをH1ごとに返す。"""
    awis: dict[str, str] = {}
    for path in (notes / "inbox").iterdir():
        content = path.read_text(encoding="utf-8")
        heading = next(line for line in content.splitlines() if line.startswith("# "))
        awis[heading] = content
    return awis


def test_check_and_submit_alerts_invokes_add_entries(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """新規アラートをAWIへ投入し、件数とfrontmatterを返す。"""
    monkeypatch.setenv("AI_AGENT", "1")
    notes = tmp_path / "private-notes"
    _prepare_alert_submission(monkeypatch, notes)
    runs = [{"workflowName": "CI", "status": "completed", "conclusion": "failure", "databaseId": 21}]
    count = alerts.check_and_submit_alerts(
        notes,
        "github.com/owner/repo",
        tmp_path / "repo",
        forge="github",
        now=datetime.datetime(2026, 1, 1),
        git_fn=lambda _p, args: "refs/remotes/origin/main" if args == ["symbolic-ref", "refs/remotes/origin/HEAD"] else None,
        run_list_fn=lambda _r, _b: runs,
    )
    assert count.submitted == 1
    content = next((notes / "inbox").iterdir()).read_text(encoding="utf-8")
    assert "alert_keys: github-run:21" in content
    assert "source: alert-monitor" in content
    assert "# ワークフローCI失敗" in content
    assert "## 反映内容と反映先" in content
    assert "反映先は`github.com/owner/repo`とする。" in content
    assert "## メリット" not in content
    assert "## デメリット" not in content
    assert "調査と変更の検証に作業が必要になる。" not in content
    assert "## 完成条件" in content


def test_check_and_submit_alerts_writes_kind_specific_completion(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """公開された収集コマンドがアラート種別ごとの外部可視の完成条件を保存する。"""
    notes = tmp_path / "private-notes"
    _prepare_alert_submission(monkeypatch, notes)

    def git_fn(_path: pathlib.Path, args: list[str]) -> str | None:
        if args == ["symbolic-ref", "refs/remotes/origin/HEAD"]:
            return "refs/remotes/origin/main"
        return None

    github_count = alerts.check_and_submit_alerts(
        notes,
        "github.com/owner/repo",
        tmp_path / "github-repo",
        forge="github",
        now=datetime.datetime(2026, 1, 1),
        git_fn=git_fn,
        run_list_fn=lambda _repo, _branch: [
            {"workflowName": "CI", "status": "completed", "conclusion": "failure", "databaseId": 100}
        ],
    )
    gitlab_count = alerts.check_and_submit_alerts(
        notes,
        "gitlab.com/owner/repo",
        tmp_path / "gitlab-repo",
        forge="gitlab",
        now=datetime.datetime(2026, 1, 1),
        git_fn=git_fn,
        ci_list_fn=lambda _repo, _branch: [{"status": "failed", "id": 200}],
        glab_api_fn=lambda _host, _endpoint: [],
    )

    assert github_count.submitted == 1
    assert gitlab_count.submitted == 1
    awis = _saved_awis_by_heading(notes)
    workflow_completion = (
        "## 完成条件\n\n対象ワークフロー`CI`の失敗が解消し、ブランチ`main`でそのワークフローが成功する。"
        "後続の実行で既に成功している場合は、確認結果の記録だけでよく、追加の変更を要しない"
    )
    pipeline_completion = (
        "## 完成条件\n\n対象パイプライン`200`の失敗が解消し、ブランチ`main`で後続のパイプラインが成功する。"
        "後続の実行で既に成功している場合は、確認結果の記録だけでよく、追加の変更を要しない"
    )
    assert workflow_completion in awis["# ワークフローCI失敗"]
    assert pipeline_completion in awis["# パイプライン200失敗"]
    assert set(awis) == {"# ワークフローCI失敗", "# パイプライン200失敗"}


def test_check_and_submit_alerts_returns_zero_when_empty(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """新規アラートが無い場合は投入しない。"""
    notes = tmp_path / "private-notes"
    (notes / "inbox").mkdir(parents=True)
    calls: list[int] = []

    def fake_add(*_args: object, **_kwargs: object) -> list[str]:
        calls.append(1)
        return []

    monkeypatch.setattr(alerts._add, "add_entries", fake_add)  # pylint: disable=protected-access
    count = alerts.check_and_submit_alerts(
        notes,
        "github.com/owner/repo",
        tmp_path / "repo",
        forge="github",
        now=datetime.datetime(2026, 1, 1),
        git_fn=lambda _p, _a: None,
    )
    assert count.submitted == 0
    assert not calls


def test_collect_new_alerts_warns_on_generic_failure(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    """CI状態の取得失敗は待機を止めずに警告を出力する。"""

    def failing_fn(_repo: str, _branch: str) -> list[dict]:
        raise alerts.AlertCollectError("gh run list（o/r）が失敗しました（exit=1）")

    result = alerts.collect_new_alerts(
        "github.com/o/r",
        "main",
        tmp_path,
        forge="github",
        run_list_fn=failing_fn,
    )
    assert not result
    stderr = capsys.readouterr().err
    assert "GitHub CI状態の取得に失敗しました" in stderr
    # 認証の確認手段を次の操作として続ける。
    assert "gh auth status" in stderr.split("\n次の操作: ", 1)[1]


def test_collect_new_alerts_decodes_utf8_json_bytes_without_locale_dependency(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """公開された収集コマンドが非ASCIIのUTF-8 JSON bytesをアラートへ変換する。"""
    payload = [{"workflowName": "日本語CI", "status": "completed", "conclusion": "failure", "databaseId": 21}]

    def fake_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[bytes]:
        assert "text" not in _kwargs
        return subprocess.CompletedProcess(command, 0, stdout=json.dumps(payload, ensure_ascii=False).encode(), stderr=b"")

    monkeypatch.setattr(_json_command.subprocess, "run", fake_run)

    result = alerts.collect_new_alerts("github.com/owner/repo", "main", tmp_path, forge="github")

    assert [alert.keys for alert in result] == [("github-run:21",)]
    assert "日本語CI" in result[0].body


def test_collect_new_alerts_warns_when_json_stdout_is_not_utf8(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """公開された収集コマンドが不正UTF-8 stdoutを原因付き収集エラーとして警告する。"""

    def fake_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(command, 0, stdout=b"\xff", stderr=b"")

    monkeypatch.setattr(_json_command.subprocess, "run", fake_run)

    assert not alerts.collect_new_alerts("github.com/owner/repo", "main", tmp_path, forge="github")
    assert "標準出力をUTF-8としてデコードできません" in capsys.readouterr().err


def test_collect_new_alerts_keeps_non_utf8_stderr_as_bytes_notation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """公開された収集コマンドが非UTF-8診断bytesを警告へ残す。"""

    def fake_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(command, 1, stdout=b"", stderr=b"failed: \x81")

    monkeypatch.setattr(_json_command.subprocess, "run", fake_run)

    assert not alerts.collect_new_alerts("github.com/owner/repo", "main", tmp_path, forge="github")
    assert "\\x81" in capsys.readouterr().err


def _prepare_alert_cli(
    monkeypatch: pytest.MonkeyPatch, root: pathlib.Path, *, forge: str = "github", branch: bool = True
) -> tuple[pathlib.Path, pathlib.Path]:
    """実Git対象と保存領域を作成し、private-notesの外部同期だけを隔離する。"""
    repo = root / "repo"
    repo.mkdir(parents=True)
    subprocess.run(["git", "init", "--quiet", str(repo)], check=True)
    host = "github.com" if forge == "github" else "gitlab.example.com"
    subprocess.run(["git", "-C", str(repo), "remote", "add", "origin", f"https://{host}/owner/repo.git"], check=True)
    if branch:
        subprocess.run(
            ["git", "-C", str(repo), "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main"], check=True
        )
    notes = root / "private-notes"
    _prepare_alert_submission(monkeypatch, notes)
    monkeypatch.setattr(_wi_sync, "ensure_environment", lambda _home: notes)
    monkeypatch.setattr(alerts, "_now_iso", lambda: "2026-01-01T00:00:00+00:00")
    return repo, notes


def _forge_responses(monkeypatch: pytest.MonkeyPatch, response: Callable[[list[str]], object], calls: list[list[str]]) -> None:
    """forgeの公開コマンドだけを差し替え、対象解決のGit操作は実行する。"""
    real_run = subprocess.run

    def run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess:
        if command[0] not in {"gh", "glab"}:
            return real_run(command, check=kwargs.pop("check", False), **kwargs)
        calls.append(command)
        payload = response(command)
        if isinstance(payload, Exception):
            raise payload
        return subprocess.CompletedProcess(command, 0, stdout=json.dumps(payload).encode(), stderr=b"")

    monkeypatch.setattr(_json_command.subprocess, "run", run)


def _invoke_alert_cli(repo: pathlib.Path | None = None, *, forge: str = "auto") -> int:
    """公開CLIを呼び、プロセスの終了値を検収する。"""
    argv = ["wi", "check-alerts", "--forge", forge]
    if repo is not None:
        argv.extend(("--target-repo", str(repo)))
    with pytest.raises(SystemExit) as result:
        atk.main(argv, now=datetime.datetime(2026, 1, 1))
    assert isinstance(result.value.code, int)
    return result.value.code


def _invoke_loop_alerts(repo: pathlib.Path, notes: pathlib.Path, forge: str) -> int:
    """常駐側の公開監視処理を呼び、process-wiの1回の実行とDependabot監査を起動しない。"""
    args = argparse.Namespace(no_alerts=False, alert_interval=0, alert_forge=forge)
    host = "github.com" if forge == "github" else "gitlab.example.com"
    return process_loop_alerts.check_process_loop_alerts(args, notes, f"{host}/owner/repo", repo, None, count_dependabot=False)[
        1
    ]


@pytest.mark.parametrize("explicit_target", [False, True], ids=["cwd", "target-worktree"])
def test_check_alerts_cli_submits_and_exits(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], explicit_target: bool
) -> None:
    """単発CLIは対象を解決し、CI失敗を保存して終了する。"""
    repo, notes = _prepare_alert_cli(monkeypatch, tmp_path)
    monkeypatch.chdir(repo if not explicit_target else tmp_path)
    calls: list[list[str]] = []
    _forge_responses(
        monkeypatch,
        lambda _command: [
            {"workflowName": "CI", "event": "push", "status": "completed", "conclusion": "failure", "databaseId": 21}
        ],
        calls,
    )
    assert _invoke_alert_cli(repo if explicit_target else None) == 0
    output = capsys.readouterr()
    assert "成功: CI失敗監視: 対象=github.com/owner/repo AWI投入=1件" in output.out
    assert output.err == ""
    assert len(calls) == 1 and calls[0][:3] == ["gh", "run", "list"]
    content = next(iter(_saved_awis_by_heading(notes).values()))
    assert "source: alert-monitor" in content and "alert_keys: github-run:21" in content


def test_check_alerts_cli_and_loop_deduplicate(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """全保存状態を除外し、別対象のキーを残して、単発と常駐の反復で二重保存しない。"""
    repo, notes = _prepare_alert_cli(monkeypatch, tmp_path)
    runs = []
    for number, state in enumerate(WI_STATES, start=1):
        directory = notes / state
        directory.mkdir(exist_ok=True)
        (directory / f"saved-{number}.md").write_text(
            f"---\ntarget_repo: github.com/owner/repo\ntype: awi\nalert_keys: github-run:{number}\n---\n\n# 保存済み\n",
            encoding="utf-8",
        )
        runs.append({"workflowName": f"CI-{number}", "status": "completed", "conclusion": "failure", "databaseId": number})
    (notes / "adopted" / "other.md").write_text(
        "---\ntarget_repo: github.com/other/repo\ntype: awi\nalert_keys: github-run:99\n---\n\n# 別対象\n", encoding="utf-8"
    )
    runs.append({"workflowName": "新規CI", "status": "completed", "conclusion": "failure", "databaseId": 99})
    _forge_responses(monkeypatch, lambda _command: runs, [])
    assert _invoke_alert_cli(repo) == 0
    assert "AWI投入=1件" in capsys.readouterr().out
    assert _invoke_alert_cli(repo) == 0
    assert "AWI投入=0件" in capsys.readouterr().out
    assert _invoke_loop_alerts(repo, notes, "github") == 0
    assert alerts.existing_alert_keys(notes, "github.com/owner/repo") == {
        *(f"github-run:{number}" for number in range(1, len(WI_STATES) + 1)),
        "github-run:99",
    }


@pytest.mark.parametrize("case", ["github-push", "github-schedule", "gitlab-normal", "gitlab-schedule", "gitlab-disabled"])
def test_check_alerts_cli_matches_loop_by_forge(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, case: str) -> None:
    """各収集種別で、単発と常駐が同じ本文・出所・キーを保存する。"""
    forge = "github" if case.startswith("github") else "gitlab"

    def response(command: list[str]) -> object:
        if forge == "github":
            return [
                {
                    "workflowName": "CI",
                    "event": case.split("-")[1],
                    "status": "completed",
                    "conclusion": "failure",
                    "databaseId": 21,
                }
            ]
        if command[1:3] == ["ci", "list"]:
            return [{"id": 22, "status": "failed"}] if case == "gitlab-normal" else []
        if command[-1].endswith("?per_page=100"):
            return [{"id": 1, "ref": "main", "active": case == "gitlab-schedule"}]
        return {"last_pipeline": {"id": 23, "status": "failed", "ref": "main"}}

    repo, notes = _prepare_alert_cli(monkeypatch, tmp_path / "single", forge=forge)
    _forge_responses(monkeypatch, response, [])
    assert _invoke_alert_cli(repo) == 0
    single = _saved_awis_by_heading(notes)
    # 同じ入力を別保存領域へ渡し、重複除外で本文比較が省かれないようにする。
    repo, notes = _prepare_alert_cli(monkeypatch, tmp_path / "loop", forge=forge)
    assert _invoke_loop_alerts(repo, notes, forge) == (0 if case == "gitlab-disabled" else 1)
    loop = _saved_awis_by_heading(notes)

    # 保存日時は呼び出しごとに異なるが、要求本文・source・keysは同じ生成主体が持つ。
    def normalize(text):
        return "\n".join(line for line in text.splitlines() if not line.startswith("created_at:"))

    assert {heading: normalize(text) for heading, text in single.items()} == {
        heading: normalize(text) for heading, text in loop.items()
    }
    assert len(single) == (0 if case == "gitlab-disabled" else 1)


@pytest.mark.parametrize("case", ["empty", "github-unavailable", "gitlab-partial", "branch-unresolved", "save-failure"])
def test_check_alerts_cli_reports_collection_failures(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], case: str
) -> None:
    """正常0件と取得不能を区別し、部分成功の実件数と保存失敗を報告する。"""
    forge = "gitlab" if case == "gitlab-partial" else "github"
    repo, notes = _prepare_alert_cli(monkeypatch, tmp_path, forge=forge, branch=case != "branch-unresolved")

    def response(command: list[str]) -> object:
        if case == "github-unavailable" or (case == "gitlab-partial" and command[1:3] == ["ci", "list"]):
            return FileNotFoundError(command[0])
        if case == "save-failure":
            return [{"workflowName": "CI", "status": "completed", "conclusion": "failure", "databaseId": 21}]
        if case == "gitlab-partial":
            if command[-1].endswith("?per_page=100"):
                return [{"id": 1, "active": True, "ref": "main"}]
            return {"last_pipeline": {"id": 23, "status": "failed", "ref": "main"}}
        return []

    _forge_responses(monkeypatch, response, [])
    if case == "save-failure":

        def failed_pull(*_args: object, **_kwargs: object) -> None:
            raise subprocess.CalledProcessError(1, ["git", "pull"], stderr="保存先同期失敗")

        monkeypatch.setattr(_wi_sync, "pull", failed_pull)
    assert _invoke_alert_cli(repo, forge=forge) == (0 if case == "empty" else 1)
    output = capsys.readouterr()
    if case == "empty":
        assert "成功: CI失敗監視:" in output.out and "AWI投入=0件" in output.out
        assert output.err == ""
    else:
        assert "成功: CI失敗監視:" not in output.out
        if case == "save-failure":
            assert "Git操作に失敗した" in output.err
            assert "git" in output.err and "pull" in output.err
        else:
            assert f"AWI投入={1 if case == 'gitlab-partial' else 0}件" in output.err
            assert "次の操作:" in output.err
            assert ("ブランチを解決できません" if case == "branch-unresolved" else "取得に失敗しました") in output.err
    assert len(_saved_awis_by_heading(notes)) == (1 if case == "gitlab-partial" else 0)


@pytest.mark.parametrize("target", ["https://github.com/owner/repo", "missing", "not-a-repo"])
def test_check_alerts_cli_rejects_invalid_target(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], target: str
) -> None:
    """ローカル対象の不正は収集せず終了コード2となる。"""
    _repo, notes = _prepare_alert_cli(monkeypatch, tmp_path)
    if target == "not-a-repo":
        (tmp_path / target).mkdir()
    monkeypatch.chdir(tmp_path)
    assert _invoke_alert_cli(pathlib.Path(target)) == 2
    assert "失敗:" in capsys.readouterr().err
    assert not _saved_awis_by_heading(notes)
