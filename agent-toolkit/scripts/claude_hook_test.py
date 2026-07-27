"""agent-toolkit/scripts/claude_hook.py のテスト。

フック共通エントリポイントが、モジュール読込段階の失敗と`main()`実行中の例外を
区別して扱うことを検証する。読込失敗では素のtracebackだけを標準エラー出力へ書き、
`main()`実行中の例外では要約1行とtracebackを書いたうえでStop系サブコマンドの空JSON応答を返す。
"""

import pathlib
import shutil
import subprocess
import sys

_SCRIPT = pathlib.Path(__file__).resolve().parent / "claude_hook.py"


class TestEntrypointExceptionStages:
    """共通エントリポイントが例外の発生段階に応じて出力を分けることを検証する。"""

    @staticmethod
    def _copy_entrypoint(tmp_path: pathlib.Path) -> pathlib.Path:
        entrypoint = tmp_path / "claude_hook.py"
        shutil.copy2(_SCRIPT, entrypoint)
        return entrypoint

    def test_main_import_error_emits_summary_traceback_and_empty_json(self, tmp_path: pathlib.Path) -> None:
        entrypoint = self._copy_entrypoint(tmp_path)
        (tmp_path / "stop_advisor.py").write_text(
            "import json\n\n"
            "def _approve() -> None:\n"
            "    print(json.dumps({}))\n\n"
            "def main() -> int:\n"
            "    raise ImportError('main failure')\n",
            encoding="utf-8",
        )

        result = subprocess.run(
            [sys.executable, str(entrypoint), "stop_advisor"],
            input="",
            capture_output=True,
            text=True,
            check=False,
        )

        assert result.returncode == 0
        assert result.stdout == "{}\n"
        assert result.stderr.startswith("[stop_advisor] 想定外エラー: ImportError: main failure")
        assert "Traceback (most recent call last):" in result.stderr

    def test_module_import_error_emits_only_traceback(self, tmp_path: pathlib.Path) -> None:
        entrypoint = self._copy_entrypoint(tmp_path)
        (tmp_path / "stop_advisor.py").write_text(
            "raise ImportError('module failure')\n",
            encoding="utf-8",
        )

        result = subprocess.run(
            [sys.executable, str(entrypoint), "stop_advisor"],
            input="",
            capture_output=True,
            text=True,
            check=False,
        )

        assert result.returncode == 0
        assert not result.stdout
        assert result.stderr.startswith("Traceback (most recent call last):")
        assert "[stop_advisor] 想定外エラー" not in result.stderr

    def test_non_approve_fallback_subcommand_exception_returns_0_without_json(
        self,
        tmp_path: pathlib.Path,
    ) -> None:
        """approve対象外のサブコマンドは例外時もJSONなしでfail-openする。"""
        entrypoint = self._copy_entrypoint(tmp_path)
        (tmp_path / "pretooluse.py").write_text(
            "def main() -> int:\n    raise RuntimeError('boom')\n",
            encoding="utf-8",
        )
        result = subprocess.run(
            [sys.executable, str(entrypoint), "pretooluse"],
            input="",
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0
        assert not result.stdout
        assert result.stderr.startswith("[pretooluse] 想定外エラー: RuntimeError: boom")
        assert "Traceback (most recent call last):" in result.stderr
