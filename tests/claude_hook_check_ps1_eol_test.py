"""scripts/claude_hook_check_ps1_eol.py のテスト。"""

import json
import pathlib
import subprocess
import sys

import pytest

_SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "claude_hook_check_ps1_eol.py"


def _run(payload: object) -> subprocess.CompletedProcess[str]:
    text = payload if isinstance(payload, str) else json.dumps(payload)
    return subprocess.run(
        [sys.executable, str(_SCRIPT)],
        input=text,
        capture_output=True,
        text=True,
        check=False,
    )


class TestBlockedCases:
    """LF のみの複数行書き込みをブロックするケース。"""

    @pytest.mark.parametrize("file_path", ["/tmp/foo.ps1", "/tmp/foo.ps1.tmpl", "/tmp/FOO.PS1"])
    def test_write_lf_only(self, file_path: str):
        result = _run({"tool_name": "Write", "tool_input": {"file_path": file_path, "content": "a\nb\n"}})
        assert result.returncode == 2
        assert "LF" in result.stderr or "CRLF" in result.stderr

    def test_edit_lf_only(self):
        result = _run(
            {
                "tool_name": "Edit",
                "tool_input": {
                    "file_path": "/tmp/a.ps1",
                    "old_string": "x",
                    "new_string": "line1\nline2\n",
                },
            }
        )
        assert result.returncode == 2

    def test_multiedit_lf_in_later_edit(self):
        result = _run(
            {
                "tool_name": "MultiEdit",
                "tool_input": {
                    "file_path": "/tmp/a.ps1",
                    "edits": [
                        {"old_string": "a", "new_string": "b"},
                        {"old_string": "c", "new_string": "x\ny\n"},
                    ],
                },
            }
        )
        assert result.returncode == 2


class TestAllowedCases:
    """exit 0 で通すケース。"""

    def test_crlf_content(self):
        result = _run(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": "/tmp/a.ps1", "content": "a\r\nb\r\n"},
            }
        )
        assert result.returncode == 0

    def test_non_ps1_file(self):
        result = _run(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": "/tmp/a.py", "content": "print(1)\n"},
            }
        )
        assert result.returncode == 0

    def test_single_line_edit(self):
        """改行を含まない Edit は誤検出しない。"""
        result = _run(
            {
                "tool_name": "Edit",
                "tool_input": {
                    "file_path": "/tmp/a.ps1",
                    "old_string": "foo",
                    "new_string": "bar",
                },
            }
        )
        assert result.returncode == 0

    def test_old_string_not_checked(self):
        """old_string 側の LF は対象外 (修復を妨げない)。"""
        result = _run(
            {
                "tool_name": "Edit",
                "tool_input": {
                    "file_path": "/tmp/a.ps1",
                    "old_string": "bad\nLF\n",
                    "new_string": "ok\r\n",
                },
            }
        )
        assert result.returncode == 0

    def test_invalid_json(self):
        result = _run("not json")
        assert result.returncode == 0
