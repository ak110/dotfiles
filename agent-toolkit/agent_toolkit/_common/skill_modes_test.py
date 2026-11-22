import pytest

from agent_toolkit._common import skill_modes


@pytest.mark.parametrize(
    "skill,arguments,mode,remaining",
    [
        ("process-wi", None, None, None),
        ("review-session", "", None, ""),
        ("process-wi", "single-lane 追記  内容\n", "single-lane", "追記  内容\n"),
        ("process-wi", " \tall-repos\n追加指示", "all-repos", "追加指示"),
        ("review-session", "batch auto", "batch", "auto"),
        ("review-session", "batch", "batch", ""),
        ("review-session", "batchx", None, "batchx"),
        ("review-session", "batching 注意", None, "batching 注意"),
        ("review-session", "meta 追加の着眼点", "meta", "追加の着眼点"),
        ("review-session", "metadata", None, "metadata"),
        ("review-session", "auto", None, "auto"),
        ("process-wi", "unknown all-repos", None, "unknown all-repos"),
        ("process-wi", "single-lanex", None, "single-lanex"),
        ("other", "batch", None, "batch"),
    ],
)
def test_split_mode(skill, arguments, mode, remaining):
    assert skill_modes.split_mode(f"agent-toolkit:{skill}", arguments) == (mode, remaining)


def test_bare_work_name_has_the_same_scope_as_qualified_name():
    scope = skill_modes.invocation_scope
    assert (
        scope("review-session", "batch")
        == scope("agent-toolkit:review-session", "batch")
        == "agent-toolkit:review-session batch"
    )
    assert scope("review-session", None) == "agent-toolkit:review-session"
    assert scope("other", "batch") == "other"


@pytest.mark.parametrize(
    ("host", "command"), [("claude", "/atk-mode review-session batch"), ("codex", "$atk-mode review-session batch")]
)
def test_entry_command_follows_host_form(host, command):
    assert skill_modes.entry_command(host, "review-session batch") == command


def test_scope_ignores_free_text_and_separates_modes():
    scope = skill_modes.invocation_scope
    assert scope(skill_modes.PROCESS_WI, "single-lane A") == scope(skill_modes.PROCESS_WI, "single-lane B")
    assert scope(skill_modes.PROCESS_WI, "single-lane") != scope(skill_modes.PROCESS_WI, "all-repos")
    assert scope("agent-toolkit:review-session", "batch auto") != scope("agent-toolkit:review-session", "auto")
    assert scope("review-session", "meta 観点") == "agent-toolkit:review-session meta"


def test_implement_work_is_resolved_from_entry_skill():
    assert skill_modes.entry_work("implement") == "implement"
    assert skill_modes.resolve_work("atk-mode", "implement 機能を足す") == ("agent-toolkit:implement", "機能を足す")
    assert skill_modes.invocation_scope("implement", "機能を足す") == "agent-toolkit:implement"


def test_process_loop_work_is_named_independently() -> None:
    assert skill_modes.resolve_work("atk-mode", "process-loop 対象を巡回") == ("agent-toolkit:process-loop", "対象を巡回")
    assert skill_modes.invocation_scope("process-loop", "対象を巡回") == "agent-toolkit:process-loop"
