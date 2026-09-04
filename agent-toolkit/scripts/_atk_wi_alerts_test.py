"""`_atk_wi_alerts`モジュールのテスト。公開API経由でDI（依存性注入）駆動する。"""

import contextlib
import datetime
import json
import pathlib
import subprocess
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import _atk_wi_alerts as alerts  # noqa: E402  # pylint: disable=wrong-import-position
import _json_command  # noqa: E402  # pylint: disable=wrong-import-position

# 機能無効判定は`collect_new_alerts`経由では応答本文の分岐を網羅できないため直接検証する。
# private参照はモジュール冒頭で別名束縛し、抑制コメントを1箇所へ集約する。
_GH_DEPENDABOT_DISABLED_MESSAGE = alerts._GH_DEPENDABOT_DISABLED_MESSAGE  # pylint: disable=protected-access
_is_disabled_response = alerts._is_disabled_response  # pylint: disable=protected-access


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


def test_collect_github_dependabot_alerts_combines_all_open() -> None:
    """未解決Dependabotアラート全件を1件へまとめ、脆弱バージョン範囲・修正版を含む。"""
    payload = [
        {
            "number": 21,
            "security_advisory": {"severity": "high"},
            "dependency": {"package": {"name": "p21"}},
            "security_vulnerability": {
                "vulnerable_version_range": "< 1.2.0",
                "first_patched_version": {"identifier": "1.3.0"},
            },
        },
        {"number": 22, "security_advisory": {"severity": "low"}, "dependency": {"package": {"name": "p22"}}},
        {
            "number": 23,
            "security_advisory": {"severity": "critical"},
            "dependency": {"package": {"name": "p23"}},
            "security_vulnerability": {"vulnerable_version_range": "< 2.0.0", "first_patched_version": None},
        },
    ]
    result = alerts.collect_github_dependabot_alerts("owner/repo", alerts_fn=lambda _r: payload)
    assert result is not None
    assert result.keys == ("github-dependabot:21", "github-dependabot:22", "github-dependabot:23")
    # 脆弱バージョン範囲・修正版に異なる値を用い、列を取り違えていないことを行全体の一致で検証する。
    assert "| 21 | high | p21 | ? | < 1.2.0 | 1.3.0 |" in result.body
    assert "| 22 | low | p22 | ? | ? | ? |" in result.body
    # 修正版が存在しない脆弱性はGitHub REST APIが`first_patched_version`にnullを返すため、
    # `?`へ正規化されクラッシュしないことを確認する。
    assert "| 23 | critical | p23 | ? | < 2.0.0 | ? |" in result.body
    assert alerts.collect_github_dependabot_alerts("owner/repo", alerts_fn=lambda _r: []) is None


def test_collect_gitlab_ci_failures_only_when_latest_failed() -> None:
    """最新パイプラインが失敗した場合のみアラート化する。"""
    success = [{"id": 1, "status": "success"}]
    assert not alerts.collect_gitlab_ci_failures("owner/repo", "master", ci_list_fn=lambda _r, _b: success)
    failed = [{"id": 2, "status": "failed", "web_url": "u", "sha": "abc12345"}]
    result = alerts.collect_gitlab_ci_failures("owner/repo", "master", ci_list_fn=lambda _r, _b: failed)
    assert [alert.keys for alert in result] == [("gitlab-pipeline:2",)]


def test_resolve_target_branch_paths() -> None:
    """追跡先を優先し、失敗時は既定ブランチへ退避する。"""

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
        "---\ntarget_repo: github.com/other/repo\ntype: feedback\nalert_keys: github-dependabot:21\n---\n\n本文\n",
        encoding="utf-8",
    )
    payload = [{"number": 21, "security_advisory": {}, "dependency": {}}]
    result = alerts.collect_new_alerts("github.com/owner/repo", None, notes, forge="github", dependabot_fn=lambda _r: payload)
    assert [alert.keys for alert in result] == [("github-dependabot:21",)]
    (adopted / "same.md").write_text(
        "---\ntarget_repo: github.com/owner/repo\ntype: feedback\nalert_keys: github-dependabot:21\n---\n\n本文\n",
        encoding="utf-8",
    )
    assert not alerts.collect_new_alerts("github.com/owner/repo", None, notes, forge="github", dependabot_fn=lambda _r: payload)


def test_existing_alert_keys_parses_absent_multiple_and_empty(tmp_path: pathlib.Path) -> None:
    """`alert_keys`未指定・カンマ区切り複数・空文字列の各書式を公開関数経由で検証する。

    `_parse_alert_keys`はモジュール非公開のため、フロントマターを持つフィードバックファイルを
    実際に配置して`existing_alert_keys`経由で検証する
    （`agent-toolkit:coding-standards`の`references/testing.md`「private関数の直接テスト禁止」節に従う）。
    """
    notes = tmp_path / "private-notes"
    inbox = notes / "inbox"
    inbox.mkdir(parents=True)
    (inbox / "absent.md").write_text(
        "---\ntarget_repo: github.com/owner/repo\ntype: feedback\n---\n\n本文\n",
        encoding="utf-8",
    )
    assert alerts.existing_alert_keys(notes, "github.com/owner/repo") == set()

    (inbox / "absent.md").unlink()
    (inbox / "multiple.md").write_text(
        "---\ntarget_repo: github.com/owner/repo\ntype: feedback\n"
        "alert_keys: github-dependabot:21, github-dependabot:22\n---\n\n本文\n",
        encoding="utf-8",
    )
    assert alerts.existing_alert_keys(notes, "github.com/owner/repo") == {
        "github-dependabot:21",
        "github-dependabot:22",
    }

    (inbox / "multiple.md").unlink()
    (inbox / "empty.md").write_text(
        "---\ntarget_repo: github.com/owner/repo\ntype: feedback\nalert_keys: \n---\n\n本文\n",
        encoding="utf-8",
    )
    assert alerts.existing_alert_keys(notes, "github.com/owner/repo") == set()


def _prepare_alert_submission(monkeypatch: pytest.MonkeyPatch, notes: pathlib.Path) -> None:
    """外部更新を無効化し、保存本文を検査できるフィードバック領域を準備する。"""
    (notes / "inbox").mkdir(parents=True)

    def no_repo_lock(*_args: object, **_kwargs: object) -> contextlib.AbstractContextManager[None]:
        return contextlib.nullcontext()

    def no_repository_update(*_args: object, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr(  # pylint: disable=protected-access
        alerts._add,  # pylint: disable=protected-access
        "_repo_lock",
        no_repo_lock,
    )
    monkeypatch.setattr(alerts._add, "_pull", no_repository_update)  # pylint: disable=protected-access
    monkeypatch.setattr(  # pylint: disable=protected-access
        alerts._add,  # pylint: disable=protected-access
        "_commit_and_push",
        no_repository_update,
    )


def _saved_feedbacks_by_heading(notes: pathlib.Path) -> dict[str, str]:
    """保存された通常フィードバックをH1ごとに返す。"""
    feedbacks: dict[str, str] = {}
    for path in (notes / "inbox").iterdir():
        content = path.read_text(encoding="utf-8")
        heading = next(line for line in content.splitlines() if line.startswith("# "))
        feedbacks[heading] = content
    return feedbacks


def test_check_and_submit_alerts_invokes_add_entries(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """新規アラートをフィードバックへ投入し、件数とfrontmatterを返す。"""
    notes = tmp_path / "private-notes"
    _prepare_alert_submission(monkeypatch, notes)
    payload = [{"number": 21, "security_advisory": {}, "dependency": {}}]
    count = alerts.check_and_submit_alerts(
        notes,
        "github.com/owner/repo",
        tmp_path / "repo",
        forge="github",
        now=datetime.datetime(2026, 1, 1),
        git_fn=lambda _p, _a: None,
        dependabot_fn=lambda _r: payload,
    )
    assert count == 1
    content = next((notes / "inbox").iterdir()).read_text(encoding="utf-8")
    assert "alert_keys: github-dependabot:21" in content
    assert "source: alert-monitor" in content
    assert "# Dependabot未解決アラート1件" in content
    assert "- 反映内容:" in content
    assert "- 反映先: `github.com/owner/repo`" in content
    assert "- 理由:" in content
    assert "- メリット:" in content
    assert "- デメリット:" in content
    assert "- 完成条件:" in content


def test_check_and_submit_alerts_writes_kind_specific_completion(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """公開入口がアラート種別ごとの外部可視の完成条件を保存する。"""
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
        dependabot_fn=lambda _repo: [{"number": 21, "security_advisory": {}, "dependency": {}}],
    )
    gitlab_count = alerts.check_and_submit_alerts(
        notes,
        "gitlab.com/owner/repo",
        tmp_path / "gitlab-repo",
        forge="gitlab",
        now=datetime.datetime(2026, 1, 1),
        git_fn=git_fn,
        ci_list_fn=lambda _repo, _branch: [{"status": "failed", "id": 200}],
    )

    assert github_count == 2
    assert gitlab_count == 1
    feedbacks = _saved_feedbacks_by_heading(notes)
    workflow_completion = (
        "- 完成条件: 対象ワークフロー`CI`の失敗が解消し、ブランチ`main`で当該ワークフローが成功する。"
        "後続の実行で既に成功している場合は、確認結果の記録だけでよく、追加の変更を要しない"
    )
    pipeline_completion = (
        "- 完成条件: 対象パイプライン`200`の失敗が解消し、ブランチ`main`で後続のパイプラインが成功する。"
        "後続の実行で既に成功している場合は、確認結果の記録だけでよく、追加の変更を要しない"
    )
    dependabot_completion = (
        "- 完成条件: 対象アラートが未解決でなくなる。"
        "ロック済みバージョンが修正版以上の場合は、依存を変更せずアラートを棄却する。"
        "修正版未満の場合は依存を更新する"
    )
    assert workflow_completion in feedbacks["# ワークフローCI失敗"]
    assert pipeline_completion in feedbacks["# パイプライン200失敗"]
    assert dependabot_completion in feedbacks["# Dependabot未解決アラート1件"]
    assert dependabot_completion != workflow_completion


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
        dependabot_fn=lambda _r: [],
    )
    assert count == 0
    assert not calls


def test_collect_new_alerts_skips_disabled_dependabot_without_warning(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Dependabot機能が無効なリポジトリでは警告を出力せずアラート0件で返す。"""

    def disabled_fn(_repo: str) -> list[dict]:
        raise alerts.AlertFeatureDisabledError("dependabot/alerts取得: 対象リポジトリで当該機能が無効")

    result = alerts.collect_new_alerts(
        "github.com/o/r",
        None,
        tmp_path,
        forge="github",
        dependabot_fn=disabled_fn,
    )
    assert not result
    assert capsys.readouterr().err == ""


def test_collect_new_alerts_warns_on_generic_failure(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    """機能無効以外の取得失敗は従来どおり警告を出力する。"""

    def failing_fn(_repo: str) -> list[dict]:
        raise alerts.AlertCollectError("dependabot/alerts取得が失敗しました（exit=1）")

    result = alerts.collect_new_alerts(
        "github.com/o/r",
        None,
        tmp_path,
        forge="github",
        dependabot_fn=failing_fn,
    )
    assert not result
    assert "Dependabotアラートの取得に失敗しました" in capsys.readouterr().err


def test_collect_new_alerts_decodes_utf8_json_bytes_without_locale_dependency(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """公開収集経路が非ASCIIのUTF-8 JSON bytesをアラートへ変換する。"""
    payload = [{"number": 21, "security_advisory": {"summary": "日本語"}, "dependency": {}}]

    def fake_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[bytes]:
        assert "text" not in _kwargs
        return subprocess.CompletedProcess(command, 0, stdout=json.dumps(payload, ensure_ascii=False).encode(), stderr=b"")

    monkeypatch.setattr(_json_command.subprocess, "run", fake_run)

    result = alerts.collect_new_alerts("github.com/owner/repo", None, tmp_path, forge="github")

    assert [alert.keys for alert in result] == [("github-dependabot:21",)]
    assert "日本語" in result[0].body


def test_collect_new_alerts_warns_when_json_stdout_is_not_utf8(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """公開収集経路が不正UTF-8 stdoutを原因付き収集エラーとして警告する。"""

    def fake_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(command, 0, stdout=b"\xff", stderr=b"")

    monkeypatch.setattr(_json_command.subprocess, "run", fake_run)

    assert not alerts.collect_new_alerts("github.com/owner/repo", None, tmp_path, forge="github")
    assert "標準出力をUTF-8として復号できません" in capsys.readouterr().err


def test_collect_new_alerts_keeps_non_utf8_stderr_as_bytes_notation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """公開収集経路が非UTF-8診断bytesを警告へ残す。"""

    def fake_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(command, 1, stdout=b"", stderr=b"failed: \x81")

    monkeypatch.setattr(_json_command.subprocess, "run", fake_run)

    assert not alerts.collect_new_alerts("github.com/owner/repo", None, tmp_path, forge="github")
    assert "\\x81" in capsys.readouterr().err


def test_is_disabled_response_matches_only_known_message() -> None:
    """403かつ既知の機能無効メッセージと完全一致する応答のみを機能無効と判定する。"""
    known = (_GH_DEPENDABOT_DISABLED_MESSAGE,)
    disabled = '{"message":"Dependabot alerts are disabled for this repository.","status":"403"}'
    other_disabled = '{"message":"Your account has been disabled.","status":"403"}'
    forbidden = '{"message":"Resource not accessible by personal access token","status":"403"}'
    not_found = '{"message":"Not Found","status":"404"}'
    assert _is_disabled_response(disabled, known) is True
    assert _is_disabled_response(other_disabled, known) is False
    assert _is_disabled_response(forbidden, known) is False
    assert _is_disabled_response(not_found, known) is False
    assert _is_disabled_response(disabled, ()) is False
    assert _is_disabled_response("[]", known) is False
    assert _is_disabled_response("not json", known) is False
