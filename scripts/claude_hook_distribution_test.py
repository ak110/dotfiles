"""配布設定が登録する隔離実行の経路でフックが動作することを検査する。

既存のフックのテストはプロジェクト環境で起動するため、PEP 723ヘッダーの
依存の欠落を検出できない。本テストは配布設定と同じ起動形を再現する。
"""

import json
import pathlib
import re
import subprocess

_REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
_MANAGED_SETTINGS = _REPO_ROOT / "share" / "claude_settings_json_managed.posix.json"
_HOOK = _REPO_ROOT / "scripts" / "claude_hook.py"
_SUBCOMMAND_PATTERN = re.compile(r"claude_hook\.py\s+([a-z_]+)\b")


def _registered_subcommands() -> list[str]:
    """配布設定のコマンド文字列から登録済みのサブコマンドを抽出する。"""
    settings = json.loads(_MANAGED_SETTINGS.read_text(encoding="utf-8"))
    subcommands: list[str] = []
    for groups in settings["hooks"].values():
        for group in groups:
            for hook in group["hooks"]:
                command = hook["command"]
                if "claude_hook.py" not in command:
                    continue
                match = _SUBCOMMAND_PATTERN.search(command)
                if match is None:
                    raise AssertionError(f"フックのサブコマンドを抽出できない: {command}")
                subcommands.append(match.group(1))
    return list(dict.fromkeys(subcommands))


def _run_hook(subcommand: str, payload: object) -> subprocess.CompletedProcess[str]:
    """配布設定と同じ隔離実行の形でフックを起動する。"""
    return subprocess.run(
        ["uv", "run", "--no-project", "--script", str(_HOOK), subcommand],
        input=json.dumps(payload, ensure_ascii=False),
        capture_output=True,
        check=False,
        cwd=_REPO_ROOT,
        encoding="utf-8",
    )


class TestDistributionPath:
    """隔離実行の経路での起動を検査する。"""

    def test_registered_subcommands_run_without_traceback(self):
        """登録済みの各サブコマンドがtracebackなしで終了する。"""
        subcommands = _registered_subcommands()
        assert subcommands
        for subcommand in subcommands:
            result = _run_hook(subcommand, {})
            assert result.returncode == 0, result.stderr
            assert "Traceback" not in result.stderr

    def test_powershell_directive_check_blocks(self):
        """必須ディレクティブを欠くPowerShellの書き込みが終了コード2で拒否される。"""
        result = _run_hook(
            "pretooluse",
            {
                "tool_name": "Write",
                "tool_input": {
                    "file_path": "example.ps1",
                    "content": "Write-Host 'missing directives'\n",
                },
            },
        )
        assert result.returncode == 2, result.stderr
        assert "Traceback" not in result.stderr

    def test_plain_write_passes(self):
        """通常の書き込みが終了コード0で通過する。"""
        result = _run_hook(
            "pretooluse",
            {
                "tool_name": "Write",
                "tool_input": {
                    "file_path": "example.txt",
                    "content": "plain text\n",
                },
            },
        )
        assert result.returncode == 0, result.stderr
        assert "Traceback" not in result.stderr
