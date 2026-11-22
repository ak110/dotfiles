"""設定削除の検査を実際の配布ロジックで確かめる。"""

import datetime

import check_managed_claude_settings_removals as check
import pytest

from pytools._internal import update_claude_settings


def test_former_compaction_env_removals_require_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    """過去に残留した2キーは、登録がない場合だけ検出する。"""
    before = {"env": {"CLAUDE_AUTOCOMPACT_PCT_OVERRIDE": "45", "CLAUDE_CODE_AUTO_COMPACT_WINDOW": "1000000"}}
    after = {"env": {}}
    assert check.removals_left_after_update(before, after) == []
    monkeypatch.setattr(update_claude_settings, "_REMOVED_ENV_KEYS", ())
    assert check.removals_left_after_update(before, after) == [
        ('$["env"]["CLAUDE_AUTOCOMPACT_PCT_OVERRIDE"]', "辞書キー"),
        ('$["env"]["CLAUDE_CODE_AUTO_COMPACT_WINDOW"]', "辞書キー"),
    ]


def test_list_removals_distinguish_replaced_label_from_residue() -> None:
    """自動置換するラベルは除き、unionマージで残る権限だけを列挙する。"""
    before = {"autoMode": {"allow": ["Example: old"]}, "permissions": {"allow": ["Read", "Grep"]}}
    after = {"autoMode": {"allow": ["Example: new"]}, "permissions": {"allow": ["Read"]}}
    assert check.removals_left_after_update(before, after) == [('$["permissions"]["allow"][1]', "配列要素")]


def test_preview_preserves_user_value() -> None:
    """管理元に無いユーザー項目はプレビューでも保持する。"""
    assert update_claude_settings.preview_managed_settings_update({"userKey": True}, {"env": {}}) == {
        "userKey": True,
        "env": {},
    }


def test_cli_rejects_unavailable_comparison(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """比較元を読めない状態は合格にせず次の操作を示す。"""
    monkeypatch.setattr("sys.argv", ["check", "--base", "does-not-exist"])
    assert check.main() == 2
    assert "--base" in capsys.readouterr().err


def test_partial_hook_removal_and_legacy_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    """旧グループの一部が残る場合も、個別のhookとその除去指定を照合する。"""
    keep = {"type": "command", "command": "echo keep"}
    remove = {"type": "command", "command": "echo remove"}
    before = {"hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [keep, remove]}]}}
    after = {"hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [keep]}]}}
    assert check.removals_left_after_update(before, after) == [('$["hooks"]["PreToolUse"][0]["hooks"][0]', "hook")]
    monkeypatch.setattr(
        update_claude_settings,
        "_REMOVED_HOOK_COMMAND_SUBSTRINGS",
        (update_claude_settings.removal_registry.Registered("echo remove", datetime.date(2026, 11, 22)),),
    )
    assert check.removals_left_after_update(before, after) == []


def test_hook_moved_to_new_matcher_is_replaced() -> None:
    """現行hookの配置変更は、配布時の自動置換が成立すれば除去指定を求めない。"""
    hook = {"type": "command", "command": "echo keep"}
    before = {"hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [hook]}]}}
    after = {"hooks": {"PreToolUse": [{"matcher": "Write", "hooks": [hook]}]}}
    assert check.removals_left_after_update(before, after) == []


def test_cli_diagnoses_invalid_worktree_json(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """Gitの比較元がある場合、現行JSONの構文エラーを取得失敗と区別する。"""
    monkeypatch.setattr("sys.argv", ["check", "--base", "HEAD"])
    monkeypatch.setattr(check, "_from_worktree", lambda path, **_: check._read_object("{", str(path)))
    assert check.main() == 2
    error = capsys.readouterr().err
    assert "share/claude_settings_json_managed.json" in error
    assert "JSONの構文" in error
    assert "比較元のGit履歴" not in error


def test_cli_diagnoses_unavailable_git(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """Gitを起動できない場合は、JSONの修正ではなくGitの復旧方法を案内する。"""
    monkeypatch.setattr("sys.argv", ["check", "--base", "HEAD"])

    def unavailable(*_args: object, **_kwargs: object) -> None:
        raise FileNotFoundError("git")

    monkeypatch.setattr(check.subprocess, "run", unavailable)
    assert check.main() == 2
    error = capsys.readouterr().err
    assert "Gitを起動できない" in error
    assert "Gitの導入とPATH" in error


def test_cli_diagnoses_invalid_utf8(tmp_path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """読めない新版ファイル名と文字コードを診断する。"""
    bad = tmp_path / "managed-settings.json"
    bad.write_bytes(b"\xff")
    monkeypatch.setattr("sys.argv", ["check", "--base", "HEAD"])
    monkeypatch.setattr(check, "_SETTINGS", bad)
    monkeypatch.setattr(check, "_from_commit", lambda *_args, **_kwargs: {})
    assert check.main() == 2
    error = capsys.readouterr().err
    assert str(bad) in error
    assert "UTF-8" in error


def test_grouped_http_hooks_left_after_rearrangement() -> None:
    """同一マッチャー内で別グループへ移した場合も重複して残る位置を報告する。"""
    first = {"type": "http", "url": "https://example.invalid/a"}
    second = {"type": "http", "url": "https://example.invalid/b"}
    before = {"hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [first, second]}]}}
    after = {"hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [first]}, {"matcher": "Bash", "hooks": [second]}]}}
    assert check.removals_left_after_update(before, after) == [
        ('$["hooks"]["PreToolUse"][0]["hooks"][0]', "hook"),
        ('$["hooks"]["PreToolUse"][0]["hooks"][1]', "hook"),
    ]
    assert check.removals_left_after_update(after, after) == []


def test_preserved_hook_group_does_not_receive_residual_diagnostic() -> None:
    """同じhookが複数のグループにあっても、残すグループには診断を出さない。"""
    first, second, third = [{"type": "http", "url": f"https://example.invalid/{suffix}"} for suffix in ("a", "b", "c")]
    keep = {"matcher": "Bash", "hooks": [first, second]}
    obsolete = {"matcher": "Bash", "hooks": [first, third]}
    before = {"hooks": {"PreToolUse": [keep, obsolete]}}
    after = {"hooks": {"PreToolUse": [keep]}}
    assert check.removals_left_after_update(before, after) == [
        ('$["hooks"]["PreToolUse"][1]["hooks"][0]', "hook"),
        ('$["hooks"]["PreToolUse"][1]["hooks"][1]', "hook"),
    ]
