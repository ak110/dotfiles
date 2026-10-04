"""登録されたhookと出力スキーマ・テストコードの対応を検証する。"""

from agent_toolkit._hooks import output_contract_test as cases
from agent_toolkit._hooks.output_contract import HOOK_OUTPUT_SCHEMAS


def test_fixture_table_covers_every_registered_hook() -> None:
    assert set(cases._FIXTURES) == cases._registered_hooks()  # pylint: disable=protected-access
    assert set(HOOK_OUTPUT_SCHEMAS) == {
        event_name
        for event_name, _ in cases._registered_hooks()  # pylint: disable=protected-access
    }
