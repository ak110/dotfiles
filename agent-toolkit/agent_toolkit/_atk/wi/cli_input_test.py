import pathlib

from agent_toolkit._atk.wi import cli_input as _wi_cli_input


class TestIsExistingDir:
    def test_returns_false_for_oversized_name_without_raising(self) -> None:
        oversized = pathlib.Path("x" * 5000)

        assert _wi_cli_input.is_existing_dir(oversized) is False
