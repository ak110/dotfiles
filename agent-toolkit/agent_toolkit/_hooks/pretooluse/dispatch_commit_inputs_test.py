# pylint: disable=useless-import-alias  # pytestのfixtureを試験モジュールの名前空間へ登録するため。
# pylint: disable=protected-access
"""dispatch_commit_inputsの契約テスト。"""

import json
import pathlib

import pytest

from agent_toolkit._hooks.pretooluse import dispatch as pretooluse
from agent_toolkit._testing.dispatch_support import _claude_commit_hook_payload, _denial_reason
from agent_toolkit._testing.dispatch_support import _existing_work_contract as _existing_work_contract

_LOW_TRAILER = "Co-Authored-By: Claude Opus 5.5 / Low <noreply@anthropic.com>"


@pytest.mark.parametrize("tool", ["Read", "Grep"])
def test_original_record_warning_offers_public_extraction(tmp_path, monkeypatch, capsys, tool):
    """原記録の抽出手段を案内し、通常資料との境界と汎用の委譲指示の不在を保つ。"""
    home = tmp_path / "claude"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(home))
    record = home / "projects/project/record.jsonl"
    record.parent.mkdir(parents=True)
    record.write_text("{}\n", encoding="utf-8")
    key = "file_path" if tool == "Read" else "path"
    payload = {"tool_name": tool, "tool_input": {key: str(record)}, "cwd": str(tmp_path)}
    assert pretooluse.main(json.dumps(payload)) == 0
    notice = capsys.readouterr().out
    assert "session-review-evidence" in notice
    assert "委譲元へ返し" not in notice
    assert str(record) in notice
    payload["tool_input"] = {key: str(tmp_path / "AGENTS.md")}
    assert pretooluse.main(json.dumps(payload)) == 0
    assert "session-review-evidence" not in capsys.readouterr().out


def test_claude_commit_block_lists_all_violations_for_variable_repository_path(
    capsys: pytest.CaptureFixture[str], tmp_path: pathlib.Path
) -> None:
    """`git -C $R`のcommitも検査し、作業項目のファイル名と帰属行の欠落を1回の遮断に並べる。

    遮断は連結した`git add`を含むコマンド全体を止めるため、通知は未実行の範囲と再実行の範囲を示す。
    """
    command = 'R=/repo; git -C $R add -- a.md && git -C $R commit -q -m "fix: 変更\n\n20261109-105107-001.md"'

    assert pretooluse.main(json.dumps(_claude_commit_hook_payload(tmp_path, command))) == 0

    reason = _denial_reason(capsys.readouterr())
    assert "20261109-105107-001.md" in reason
    assert _LOW_TRAILER in reason
    assert "全体が実行されていない" in reason
    assert "同じコマンド全体へ修正を加えて再実行せよ" in reason


def test_claude_commit_blocks_missing_trailer_for_quoted_variable_repository_path(
    capsys: pytest.CaptureFixture[str], tmp_path: pathlib.Path
) -> None:
    """引用した変数展開のパスを`git -C`へ渡したcommitも、帰属行の欠落で遮断する。"""
    payload = _claude_commit_hook_payload(tmp_path, "git -C \"$R\" commit -q -m 'fix: 変更'")

    assert pretooluse.main(json.dumps(payload)) == 0
    assert _LOW_TRAILER in _denial_reason(capsys.readouterr())


@pytest.mark.parametrize(
    "command",
    [
        pytest.param("git -C $R commit -F message.txt", id="relative-file-with-unknown-location"),
        pytest.param("git -c \"$CONFIG\" commit -m 'fix: 変更'", id="unknown-config-value"),
    ],
)
def test_claude_commit_does_not_judge_unresolved_message_inputs(
    capsys: pytest.CaptureFixture[str], tmp_path: pathlib.Path, command: str
) -> None:
    """実行位置が不明な相対パスの本文と、メッセージに作用しうる語の未知語は、従来どおり判定しない。"""
    (tmp_path / "message.txt").write_text("fix: 変更\n", encoding="utf-8")

    assert pretooluse.main(json.dumps(_claude_commit_hook_payload(tmp_path, command))) == 0
    assert _denial_reason(capsys.readouterr()) == ""


def test_observed_commit_trailer_matches_the_line_required_by_commit_check(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """事前に示す帰属行は遮断の検査が期待する行と一致し、観測できない場合と無効化設定では示さない。"""
    payload = _claude_commit_hook_payload(tmp_path, "git commit -m 'fix: 変更'")
    # 設定の探索先を試験のディレクトリへ固定する。実行環境の値が残ると、試験が置く設定を読まない。
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))

    assert pretooluse.observed_commit_trailer(payload, str(tmp_path), is_codex=False) == _LOW_TRAILER
    assert pretooluse.observed_commit_trailer({"cwd": str(tmp_path)}, str(tmp_path), is_codex=False) is None

    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude/settings.json").write_text('{"attribution":{"commit":""}}', encoding="utf-8")
    assert pretooluse.observed_commit_trailer(payload, str(tmp_path), is_codex=False) is None
