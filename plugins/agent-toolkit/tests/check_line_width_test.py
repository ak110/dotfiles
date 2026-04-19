"""plugins/agent-toolkit/skills/writing-standards/scripts/check_line_width.py のテスト。

Markdown 1行幅検査スクリプトを subprocess で起動し、
正常系・異常系・閾値切り替え・コードブロック除外・frontmatter対象化の挙動を検証する。
"""

import pathlib
import subprocess
import sys

# テストファイルは plugins/agent-toolkit/tests/ 直下配置を前提とし、
# parents[1] でプラグインルート（plugins/agent-toolkit/）を指す。
_SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "skills" / "writing-standards" / "scripts" / "check_line_width.py"


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(_SCRIPT), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def _write(path: pathlib.Path, content: str) -> pathlib.Path:
    path.write_text(content, encoding="utf-8")
    return path


class TestCheckLineWidth:
    """1行幅検査の主要シナリオをまとめて検証する。"""

    def test_within_limit_passes(self, tmp_path: pathlib.Path):
        # 全角混在で127半角換算以内、127丁度の境界、半角のみのいずれも通る。
        path = _write(
            tmp_path / "ok.md",
            "短い見出し行\n"
            + "あ" * 63
            + "x\n"  # 63*2 + 1 = 127
            + ("a" * 127)
            + "\n",
        )
        result = _run(str(path))
        assert result.returncode == 0
        assert result.stderr == ""

    def test_body_overflow_is_reported(self, tmp_path: pathlib.Path):
        # 本文の128字超過は違反として出力される。
        path = _write(tmp_path / "ng.md", "あ" * 64 + "\n")  # 64*2 = 128
        result = _run(str(path))
        assert result.returncode == 1
        assert f"{path}:L1 幅=128" in result.stderr

    def test_table_row_is_checked(self, tmp_path: pathlib.Path):
        # 表（Markdownテーブル）の行も対象。
        long_cell = "あ" * 70
        path = _write(
            tmp_path / "table.md",
            f"| col1 | col2 |\n| --- | --- |\n| {long_cell} | x |\n",
        )
        result = _run(str(path))
        assert result.returncode == 1
        assert f"{path}:L3" in result.stderr

    def test_frontmatter_is_checked(self, tmp_path: pathlib.Path):
        # frontmatter（YAML）の長すぎる行も違反扱い。
        path = _write(
            tmp_path / "fm.md",
            "---\ntitle: " + "あ" * 64 + "\n---\n\n本文\n",
        )
        result = _run(str(path))
        assert result.returncode == 1
        assert f"{path}:L2" in result.stderr

    def test_fenced_code_block_is_excluded(self, tmp_path: pathlib.Path):
        # ``` フェンス内の超過行は対象外。
        path = _write(
            tmp_path / "code.md",
            "通常文\n```text\n" + ("a" * 200) + "\n```\n本文\n",
        )
        result = _run(str(path))
        assert result.returncode == 0
        assert result.stderr == ""

    def test_tilde_fence_is_excluded(self, tmp_path: pathlib.Path):
        # ~~~ フェンスでも同様にコードブロック扱い。
        path = _write(
            tmp_path / "tilde.md",
            "~~~text\n" + ("a" * 200) + "\n~~~\n",
        )
        result = _run(str(path))
        assert result.returncode == 0

    def test_width_option_overrides_threshold(self, tmp_path: pathlib.Path):
        # --width=80 を指定すると80字超過が検出される。
        path = _write(tmp_path / "narrow.md", "a" * 81 + "\n")
        result_default = _run(str(path))
        assert result_default.returncode == 0
        result_narrow = _run("--width=80", str(path))
        assert result_narrow.returncode == 1
        assert f"{path}:L1 幅=81" in result_narrow.stderr

    def test_multiple_files_aggregated(self, tmp_path: pathlib.Path):
        # 複数ファイルの違反を集約報告し、終了コード1。
        good = _write(tmp_path / "good.md", "ok\n")
        bad = _write(tmp_path / "bad.md", "あ" * 64 + "\n")
        result = _run(str(good), str(bad))
        assert result.returncode == 1
        assert f"{bad}:L1" in result.stderr
        assert str(good) not in result.stderr
