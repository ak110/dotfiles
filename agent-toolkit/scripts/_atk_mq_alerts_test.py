"""`_atk_mq_alerts`モジュールのテスト。公開API経由でDI（依存性注入）駆動する。"""

import contextlib
import datetime
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import _atk_mq_alerts as alerts  # noqa: E402  # pylint: disable=wrong-import-position

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
    """未解決Dependabotアラート全件を1件へまとめる。"""
    payload = [
        {"number": 21, "security_advisory": {"severity": "high"}, "dependency": {"package": {"name": "p21"}}},
        {"number": 22, "security_advisory": {"severity": "low"}, "dependency": {"package": {"name": "p22"}}},
    ]
    result = alerts.collect_github_dependabot_alerts("owner/repo", alerts_fn=lambda _r: payload)
    assert result is not None
    assert result.keys == ("github-dependabot:21", "github-dependabot:22")
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

    `_parse_alert_keys`はモジュール非公開のため、フロントマターを持つfeedbackファイルを
    実際に配置して`existing_alert_keys`経由で検証する
    （`coding-standards`の`references/testing.md`「private関数の直接テスト禁止」節に従う）。
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


def test_check_and_submit_alerts_invokes_add_entries(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """新規アラートをfeedbackへ投入し、件数とfrontmatterを返す。"""
    notes = tmp_path / "private-notes"
    (notes / "inbox").mkdir(parents=True)
    monkeypatch.setattr(  # pylint: disable=protected-access
        alerts._add,  # pylint: disable=protected-access
        "_repo_lock",
        lambda *_a, **_k: contextlib.nullcontext(),
    )
    monkeypatch.setattr(alerts._add, "_pull", lambda _p: None)  # pylint: disable=protected-access
    monkeypatch.setattr(  # pylint: disable=protected-access
        alerts._add,  # pylint: disable=protected-access
        "_commit_and_push",
        lambda *_a, **_k: None,
    )
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
