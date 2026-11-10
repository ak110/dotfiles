"""公開PreToolUse入力で、シェル起動の値付きオプションと本文を区別する。"""

import pytest

from agent_toolkit._testing.pretooluse_support import _additional_context, _run


@pytest.mark.parametrize(
    "prefix",
    ["bash -co pipefail", "bash -cO extglob", "bash -c -o pipefail", "bash --rcfile data -c"],
)
def test_shell_option_values_reach_public_hook_input(prefix: str) -> None:
    """本文内の出力喪失を検知し、位置引数に同じ文字列があるだけの入力は警告しない。"""
    warning = "結果と終了状態を直接受領できない入力"
    actual = _run({"tool_name": "Bash", "tool_input": {"command": f"{prefix} 'atk wi list | cat'"}})
    assert actual.returncode == 0
    assert warning in _additional_context(actual)
    data = _run({"tool_name": "Bash", "tool_input": {"command": f"{prefix} 'printf marker' 'atk wi list | cat'"}})
    assert data.returncode == 0
    assert warning not in _additional_context(data)
