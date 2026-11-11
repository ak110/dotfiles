"""未確定の本文引数を含む保存を、実行せずに公開hookへ与える。

実記録の2入力は、変数への代入、引用heredocでの本文作成、WI edit/addという構造だった。
本文の内容は静的解析に影響しないため、個人記録を含まない最小の本文へ置換する。
"""

import pytest

from agent_toolkit._hooks.pretooluse import operation_skills
from agent_toolkit._testing.pretooluse_support import _run_hook_in_process


@pytest.mark.parametrize(
    "command",
    [
        'd=/nonexistent; cat > "$d/file.md" <<\'EOF\'\n本文\nEOF\natk wi edit example.md --append --body-file "$d/file.md"',
        'd=/nonexistent; cat > "$d/file.md" <<\'EOF\'\n本文\nEOF\natk wi add --dry-run --type=uwi --body-file "$d/file.md"',
        'atk wi add --body-file "$d/file.md"',
        'atk wi add --body-file="$d/file.md"',
        'atk wi add --body-file "$(printf file.md)"',
    ],
)
def test_unknown_body_preserves_pre_and_post_operation_classification(tmp_path, command):
    """元入力と別形態を前後イベントへ渡し、取得記録の例外も検出する。"""
    inputs = {"command": command}
    assert operation_skills.operation_matches("wi-standard-save", "Bash", inputs, str(tmp_path)) is True
    assert operation_skills.operation_matches("wi-report-save", "Bash", inputs, str(tmp_path)) is False
    payload = {"session_id": "unknown-wi-body", "cwd": str(tmp_path), "tool_name": "Bash", "tool_input": inputs}
    for event, entry in (("PreToolUse", "pretooluse"), ("PostToolUse", "posttooluse")):
        result = _run_hook_in_process(entry, {**payload, "hook_event_name": event, "tool_response": ""})
        assert result.returncode == 0 and not result.stderr
        assert "embedded null byte" not in result.stdout
        assert "資料の読取結果を取得済みとして記録できない" not in result.stdout


def test_known_and_unknown_bodies_are_classified_independently(tmp_path):
    """未知値が混在しても、既知の報告標識と通常保存の両方を残す。"""
    report = tmp_path / "report.md"
    report.write_text("---\nrun_skill_report: 'true'\n---\n報告\n", encoding="utf-8")
    normal = tmp_path / "normal.md"
    normal.write_text("通常の本文\n", encoding="utf-8")
    for option in ("--body-file ", "--body-file="):
        command = f'atk wi add {option}"$d/file.md"; atk wi add {option}"{report}"; atk wi edit x.md {option}"{normal}"'
        inputs = {"command": command}
        assert operation_skills.operation_matches("wi-standard-save", "Bash", inputs, str(tmp_path)) is True
        assert operation_skills.operation_matches("wi-report-save", "Bash", inputs, str(tmp_path)) is True
