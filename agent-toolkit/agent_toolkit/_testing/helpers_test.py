"""配送本文のテスト補助が外側境界と属性値を検証することを確認する。"""

import pytest

from agent_toolkit._testing.helpers import delivery_payload


@pytest.mark.parametrize(
    "attributes",
    [
        'source="agents-server" kind="delivery"',
        'kind="delivery" source="agents-server"',
    ],
)
def test_delivery_payload_uses_outer_boundary_and_attribute_values(attributes: str) -> None:
    """属性順を変えても入れ子の本文を同じ文字列として返す。"""
    body = '<atk-auto source="inner" kind="notice">内側</atk-auto>\r\n次の行'
    delivered = f"<atk-auto {attributes}>\n{body}\n</atk-auto>"
    assert delivery_payload(delivered) == body


@pytest.mark.parametrize(
    "opening,closing",
    [
        (
            '<other from="main:root" composed-by="caller" source="agent-toolkit/agents-server" kind="agent-delivery">',
            "</other>",
        ),
        (
            '<agent-toolkit-auto-inserted from="main:root" source="agent-toolkit/agents-server" kind="agent-delivery">',
            "</agent-toolkit-auto-inserted>",
        ),
        (
            '<agent-toolkit-auto-inserted from="main:root" composed-by="caller" source="wrong" kind="agent-delivery">',
            "</agent-toolkit-auto-inserted>",
        ),
        (
            '<agent-toolkit-auto-inserted from="main:root" composed-by="caller"'
            ' source="agent-toolkit/agents-server" kind="wrong">',
            "</agent-toolkit-auto-inserted>",
        ),
        (
            '<agent-toolkit-auto-inserted from="main:root" composed-by="caller"'
            ' source="agent-toolkit/agents-server" kind="agent-delivery">',
            "</other>",
        ),
    ],
)
def test_delivery_payload_rejects_invalid_boundary(opening: str, closing: str) -> None:
    """要素名、必要属性と外側の終了タグが違う配送を拒否する。"""
    with pytest.raises(AssertionError):
        delivery_payload(f"{opening}\n本文\n{closing}")


@pytest.mark.parametrize(
    "source,kind", [("agent-toolkit", "plugin-resources"), ("wrong", "plugin-resources"), ("agent-toolkit", "wrong")]
)
def test_delivery_payload_separates_only_known_resource_notice(source: str, kind: str) -> None:
    """正式な資源案内だけを配送本文と区別し、他の前置きは受理しない。"""
    notice = f'<atk-auto source="{source}" kind="{kind}">\nroot案内\n</atk-auto>'
    delivered = notice + '\n\n<atk-auto source="agents-server" kind="delivery">\n続行\n</atk-auto>'
    if (source, kind) == ("agent-toolkit", "plugin-resources"):
        assert delivery_payload(delivered) == "続行"
    else:
        with pytest.raises(AssertionError):
            delivery_payload(delivered)
