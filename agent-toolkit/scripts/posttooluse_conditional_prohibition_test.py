"""agent-toolkit/scripts/posttooluse.pyの条件付き禁止形機械検出（fb06）のテスト。

`is_agent_facing_md`が対象と判定するコーディングエージェント向け`.md`編集時に
「〜した状態で…しない/禁止」パターンを警告検出する挙動を検証する。
`posttooluse_test.py`のpylint too-many-lines回避のため独立ファイルへ配置する。
"""

import json
import os
import pathlib
import subprocess

import _fork_runner

_SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "posttooluse.py"


def _run(payload: dict, *, state_dir: pathlib.Path) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["TMPDIR"] = str(state_dir)
    env["TEMP"] = str(state_dir)
    env["TMP"] = str(state_dir)
    return _fork_runner.run_script(_SCRIPT, input=json.dumps(payload, ensure_ascii=False), env=env)


class TestCheckConditionalProhibition:
    """条件付き禁止形の機械検出テスト"""

    def test_no_pattern_returns_empty(self, tmp_path: pathlib.Path) -> None:
        """パターン非該当は警告0を返す"""
        target = tmp_path / "agent-toolkit" / "rules" / "foo.md"
        target.parent.mkdir(parents=True)
        target.write_text("通常の規範文。\n", encoding="utf-8")
        result = _run(
            {"session_id": "cp-none", "tool_name": "Write", "tool_input": {"file_path": str(target), "content": "x"}},
            state_dir=tmp_path,
        )
        assert result.returncode == 0
        assert "条件付き禁止形" not in result.stdout

    def test_conditional_prohibition_reports_warning(self, tmp_path: pathlib.Path) -> None:
        """「〜した状態で...しない」パターンで警告を発する"""
        target = tmp_path / "agent-toolkit" / "rules" / "foo.md"
        target.parent.mkdir(parents=True)
        target.write_text("計画ファイルを未確認した状態で実装しない。\n", encoding="utf-8")
        result = _run(
            {"session_id": "cp-warn", "tool_name": "Write", "tool_input": {"file_path": str(target), "content": "x"}},
            state_dir=tmp_path,
        )
        assert result.returncode == 0
        assert "条件付き禁止形" in result.stdout

    def test_out_of_scope_file_returns_empty(self, tmp_path: pathlib.Path) -> None:
        """対象外パスの.mdファイルは警告しない"""
        target = tmp_path / "docs" / "guide.md"
        target.parent.mkdir(parents=True)
        target.write_text("計画ファイルを未確認した状態で実装しない。\n", encoding="utf-8")
        result = _run(
            {"session_id": "cp-scope", "tool_name": "Write", "tool_input": {"file_path": str(target), "content": "x"}},
            state_dir=tmp_path,
        )
        assert result.returncode == 0
        assert "条件付き禁止形" not in result.stdout

    def test_quoted_example_is_excluded(self, tmp_path: pathlib.Path) -> None:
        """全角鍵括弧で引用した例示文は誤検出しない"""
        target = tmp_path / "agent-toolkit" / "rules" / "foo.md"
        target.parent.mkdir(parents=True)
        target.write_text("「未確認した状態で実装しない」という表現は避ける。\n", encoding="utf-8")
        result = _run(
            {"session_id": "cp-quoted", "tool_name": "Write", "tool_input": {"file_path": str(target), "content": "x"}},
            state_dir=tmp_path,
        )
        assert result.returncode == 0
        assert "条件付き禁止形" not in result.stdout

    def test_line_number_unaffected_by_preceding_quoted_line(self, tmp_path: pathlib.Path) -> None:
        """引用除外の前置行があっても後続行の警告行番号が実位置とずれない"""
        target = tmp_path / "agent-toolkit" / "rules" / "foo.md"
        target.parent.mkdir(parents=True)
        target.write_text(
            "「未確認した状態で実装しない」という表現は避ける。\n実際に検証した状態で禁止する。\n",
            encoding="utf-8",
        )
        result = _run(
            {"session_id": "cp-lineno", "tool_name": "Write", "tool_input": {"file_path": str(target), "content": "x"}},
            state_dir=tmp_path,
        )
        assert result.returncode == 0
        assert f"{target}:2:" in result.stdout
