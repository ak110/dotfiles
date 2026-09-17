"""agent-toolkit/agent_toolkit/_hooks/plugin_resources.py のテスト。"""

import pathlib

from agent_toolkit._hooks import plugin_resources, user_prompt_submit


def test_skill_reference_includes_an_existing_absolute_path() -> None:
    """実在する資料では、スキル名に加えて実在する絶対パスを併記する。"""
    reference = plugin_resources.skill_reference("confirmation-and-uwi", "references/user-utterance.md")

    assert "`agent-toolkit:confirmation-and-uwi`の`references/user-utterance.md`" in reference
    quoted = reference.split("（`", 1)[1].removesuffix("`）")
    assert pathlib.Path(quoted).is_absolute()
    assert pathlib.Path(quoted).is_file()


def test_skill_reference_omits_the_path_when_the_resource_is_absent() -> None:
    """実体が無い場合は従来どおりスキル名と相対パスだけを返す。"""
    reference = plugin_resources.skill_reference("confirmation-and-uwi", "references/absent.md")

    assert reference == "`agent-toolkit:confirmation-and-uwi`の`references/absent.md`"


def test_reference_notice_body_carries_the_absolute_path() -> None:
    """発話解釈の規範の読込を求める通知本文が、実在する絶対パスを含む。"""
    body = user_prompt_submit.REFERENCE_NOTICE_BODY
    assert "（`/" in body
    quoted = body.split("（`", 1)[1].split("`）", 1)[0]
    assert pathlib.Path(quoted).is_file()
