"""scripts/claude_hook_call_formatter.py のテスト。

subprocess でスクリプトを起動し、対象ファイルへの書き込み効果と exit code を検証する。
実際に pyfltr/ruff-format が走る必要があるため、pyfltr が dotfiles venv に sync 済み
(= `make setup` 実行済み) であることを前提とする。
"""

import json
import pathlib
import subprocess
import sys

import pytest

_SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "claude_hook_call_formatter.py"


def _run(payload: object) -> subprocess.CompletedProcess[str]:
    """スクリプトを subprocess で起動し CompletedProcess を返す。"""
    text = payload if isinstance(payload, str) else json.dumps(payload)
    return subprocess.run(
        [sys.executable, str(_SCRIPT)],
        input=text,
        capture_output=True,
        text=True,
        check=False,
    )


class TestFormattedCases:
    """.py ファイルが実際に整形されるケース。"""

    def test_py_file_is_formatted(self, tmp_path: pathlib.Path):
        """要整形の .py を渡すと内容が書き換わる (ruff-format 相当)。"""
        target = tmp_path / "sample.py"
        target.write_text("x =1\n", encoding="utf-8")

        result = _run({"tool_name": "Write", "tool_input": {"file_path": str(target)}})

        assert result.returncode == 0
        # ruff-format により `x =1` → `x = 1` に変わる
        assert target.read_text(encoding="utf-8") == "x = 1\n"


class TestPassthroughCases:
    """フォーマッタを呼ばずに exit 0 で通すケース。"""

    def test_non_python_extension(self, tmp_path: pathlib.Path):
        """.py 以外の拡張子はフォーマッタを呼ばない。"""
        sample = tmp_path / "a.md"
        sample.write_text("unchanged", encoding="utf-8")

        result = _run({"tool_name": "Write", "tool_input": {"file_path": str(sample)}})

        assert result.returncode == 0
        assert sample.read_text(encoding="utf-8") == "unchanged"

    @pytest.mark.parametrize(
        "payload",
        [
            # 対象外の tool_name
            {"tool_name": "Bash", "tool_input": {"command": "echo"}},
            # tool_input 欠落
            {"tool_name": "Write"},
            # file_path が空
            {"tool_name": "Write", "tool_input": {"file_path": ""}},
        ],
    )
    def test_irrelevant_payload(self, payload: dict):
        """対象外 tool_name・不完全ペイロードは即 exit 0。"""
        result = _run(payload)
        assert result.returncode == 0

    def test_missing_file(self, tmp_path: pathlib.Path):
        """file_path が存在しない場合でも exit 0 (フェイルセーフ)。"""
        result = _run({"tool_name": "Write", "tool_input": {"file_path": str(tmp_path / "nope.py")}})
        assert result.returncode == 0

    def test_invalid_json(self):
        """不正 JSON はフェイルセーフで exit 0。"""
        result = _run("this is not json")
        assert result.returncode == 0
