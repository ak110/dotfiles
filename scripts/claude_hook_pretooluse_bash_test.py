"""scripts/claude_hook_pretooluse.py の`Bash`ツール関連テスト。"""

import pytest
from claude_hook_pretooluse_test import _AT_DIR, _run

_SCOPE_ESCALATION_INPUTS_PATH = _AT_DIR / "skills" / "agent-standards" / "references" / "_scope_escalation_test_inputs.txt"


def _load_scope_escalation_inputs() -> list[tuple[str, str]]:
    """隔離フィクスチャからテスト入力を読み込む。

    フォーマット・読み込み方針は`agent-toolkit/scripts/pretooluse_test.py`の
    同名ヘルパーと同一（コンテキスト汚染回避のため検出語を本ファイルへ転記しない）。
    """
    if not _SCOPE_ESCALATION_INPUTS_PATH.exists():
        return []
    inputs: list[tuple[str, str]] = []
    for raw in _SCOPE_ESCALATION_INPUTS_PATH.read_text(encoding="utf-8").splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "\t" not in stripped:
            continue
        category, text = stripped.split("\t", 1)
        inputs.append((text.strip(), category.strip()))
    return inputs


_SCOPE_ESCALATION_INPUTS = _load_scope_escalation_inputs()


class TestBashAtkFbTbdAddScopeEscalation:
    """`Bash`経由の`atk fb tbd-add`コマンド文字列への縮退フレーズ混入検出（block）。"""

    @pytest.mark.parametrize(("text", "category"), _SCOPE_ESCALATION_INPUTS)
    def test_blocks(self, text: str, category: str):
        command = f"atk fb tbd-add glatasks {text}"
        result = _run({"tool_name": "Bash", "tool_input": {"command": command}})
        assert result.returncode == 2
        assert category in result.stderr
        assert "[auto-generated: dotfiles/claude_hook_pretooluse]" in result.stderr

    def test_unrelated_command_allowed(self):
        result = _run({"tool_name": "Bash", "tool_input": {"command": "atk fb list --type=tbd"}})
        assert result.returncode == 0
        assert result.stdout == ""

    def test_non_atk_command_allowed(self):
        result = _run({"tool_name": "Bash", "tool_input": {"command": "echo tbd-add"}})
        assert result.returncode == 0
        assert result.stdout == ""

    def test_tbd_add_without_scope_escalation_allowed(self):
        result = _run({"tool_name": "Bash", "tool_input": {"command": "atk fb tbd-add glatasks 確認事項です"}})
        assert result.returncode == 0
        assert result.stdout == ""
