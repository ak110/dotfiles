"""PreToolUseの警告一覧整形を検証する。"""

import pytest

from agent_toolkit._hooks.message_format import llm_notice
from agent_toolkit._hooks.pretooluse import warning_context
from agent_toolkit._hooks.pretooluse.warning_context import format_warning_context


@pytest.mark.parametrize(
    ("warnings", "expected"),
    [
        (["一件目"], "一件目"),
        (["一件目", "二件目"], "警告: 2件\n\n一件目\n\n二件目"),
        (["一件目", "二件目", "三件目"], "警告: 3件\n\n一件目\n\n二件目\n\n三件目"),
    ],
)
def test_format_warning_context(warnings: list[str], expected: str) -> None:
    """単一警告を維持し、複数警告へ件数見出しを付ける。"""
    assert format_warning_context(warnings) == expected


def test_preformatted_context_is_recounted_without_nested_header() -> None:
    """個々の判定が結合済みの警告と、統合フックが集めた単独の警告を総数で数え直す。"""
    first = llm_notice("first", "hook", tag="warn")
    second = llm_notice("second", "hook", tag="warn")
    third = llm_notice("third", "hook", tag="warn")
    combined = format_warning_context([first, second])

    assert format_warning_context([third, combined]) == f"警告: 3件\n\n{third}\n\n{first}\n\n{second}"


@pytest.mark.parametrize("failure", ["session", "write", "length"])
def test_unavailable_saved_notice_names_read_operation_and_missing_input(tmp_path, monkeypatch, failure):
    """通知原本を配送できない各分岐で、実在する手順の取得と問い合わせ先を示す。"""
    monkeypatch.setattr(warning_context.managed_temp, "resolve_session_temp", lambda _session: tmp_path)

    def fail_write(*_args, **_kwargs):
        raise OSError("試験用の書込失敗")

    if failure == "write":
        monkeypatch.setattr(warning_context, "atomic_write", fail_write)
    result = warning_context.bounded_codex_context(
        "未配送の通知本文" * 1000,
        session_id="" if failure == "session" else "notice-failure",
        reserved_bytes=1900 if failure == "length" else 0,
    )
    assert str(warning_context.plugin_resources.plugin_root() / "skills/managed-temp/SKILL.md") in result
    assert "cat --" in result or "Get-Content -LiteralPath" in result
    assert "委譲元（無ければユーザー）" in result
    assert "再試行せよ" in result
    assert "未配送の通知本文" not in result
