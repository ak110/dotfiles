"""コーディングエージェント宛て共有メッセージ整形を検証する。"""

from xml.etree import ElementTree as ET

import pytest

from agent_toolkit._hooks.message_format import llm_notice


@pytest.mark.parametrize(
    ("tag", "expected_kind"),
    [
        pytest.param("", "notice", id="default"),
        pytest.param("warn", "warn", id="tagged"),
    ],
)
def test_llm_notice_wraps_body_with_xml_boundary(tag: str, expected_kind: str) -> None:
    """タグ有無にかかわらず出所、種別及び本文を保つ。"""
    notice = llm_notice("本文", "agent-toolkit/example", tag=tag)
    assert notice.startswith(f'<agent-toolkit-auto-inserted source="agent-toolkit/example" kind="{expected_kind}">')
    assert notice.endswith("\n本文\n</agent-toolkit-auto-inserted>")


def test_llm_notice_escapes_attribute_values() -> None:
    """属性値にXMLメタ文字があっても境界を壊さない。"""
    notice = llm_notice("本文", 'agent-toolkit/"example&', tag="warn")
    element = ET.fromstring(notice)
    assert element.attrib["source"] == 'agent-toolkit/"example&'
