"""両ホストの宣言配送、取得範囲、主体と圧縮の境界を確かめる。"""

import pytest

from agent_toolkit._testing.document_delivery import run_hook as _run_hook_in_process
from agent_toolkit._testing.read_delivery_support import (
    _assert_acquired,
    _guidance,
    _lane_worktree_plugin,
    _local_plugin_root,
    _memory,
    _notice_body,
    _read_request,
    _role_plugin,
    _write_transcript,
)

__all__ = [
    "_assert_acquired",
    "_guidance",
    "_lane_worktree_plugin",
    "_local_plugin_root",
    "_memory",
    "_notice_body",
    "_read_request",
    "_role_plugin",
    "_write_transcript",
]


@pytest.mark.parametrize("host", ["claude-read", "claude-bash", "codex-exec"])
@pytest.mark.usefixtures("memory")
def test_other_role_read_is_not_warned(tmp_path, monkeypatch, host):
    """調査・検収と起動準備を区別できない読取には警告しない。"""
    _root, other, _contract, _lane, _integration = _role_plugin(tmp_path, monkeypatch)
    payload, tool, inputs = _read_request(host, other)
    result = _run_hook_in_process("pretooluse", {**payload, "cwd": str(tmp_path), "tool_name": tool, "tool_input": inputs})
    assert result.returncode == 0 and not result.stderr
    assert "他の担当の手順書" not in result.stdout
