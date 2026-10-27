"""配布設定が登録する隔離実行の経路でフックが動作することを検査する。

配布設定からconsole scriptのサブコマンドを抽出し、同じサブコマンドを直接起動する。
"""

import json
import os
import pathlib
import re
import subprocess
import sysconfig

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_MANAGED_SETTINGS = _REPO_ROOT / "share" / "claude_settings_json_managed.posix.json"
_SUBCOMMAND_PATTERN = re.compile(r"dotfiles-claude-hook\s+([a-z_]+)\b")


def _registered_subcommands() -> list[str]:
    """配布設定のコマンド文字列から登録済みのサブコマンドを抽出する。"""
    settings = json.loads(_MANAGED_SETTINGS.read_text(encoding="utf-8"))
    subcommands: list[str] = []
    for groups in settings["hooks"].values():
        for group in groups:
            for hook in group["hooks"]:
                command = hook["command"]
                if "dotfiles-claude-hook" not in command:
                    continue
                match = _SUBCOMMAND_PATTERN.search(command)
                if match is None:
                    raise AssertionError(f"フックのサブコマンドを抽出できない: {command}")
                subcommands.append(match.group(1))
    return list(dict.fromkeys(subcommands))


def _run_hook(subcommand: str, payload: object) -> subprocess.CompletedProcess[str]:
    """配布設定と同じ隔離実行の形でフックを起動する。"""
    # 隔離fixtureは親venvをPATHから除くため、今回導入したconsole scriptを直接指定する。
    executable = pathlib.Path(sysconfig.get_path("scripts")) / (
        "dotfiles-claude-hook.exe" if os.name == "nt" else "dotfiles-claude-hook"
    )
    return subprocess.run(
        [str(executable), subcommand],
        input=json.dumps(payload, ensure_ascii=False),
        capture_output=True,
        check=False,
        cwd=_REPO_ROOT,
        encoding="utf-8",
        timeout=15,
    )


class TestDistributionPath:
    """配布設定が参照するconsole scriptの起動を検査する。"""

    def test_registered_subcommands_run_without_traceback(self):
        """登録済みの各サブコマンドがtracebackなしで終了する。"""
        subcommands = _registered_subcommands()
        assert subcommands
        for subcommand in subcommands:
            result = _run_hook(subcommand, {})
            assert result.returncode == 0, result.stderr
            assert "Traceback" not in result.stderr

    def test_powershell_directive_check_warns(self):
        """必須ディレクティブを欠くPowerShellの書き込みが、終了コード0のまま警告を返す。"""
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
        assert result.returncode == 0, result.stderr
        assert "Set-StrictMode" in result.stdout
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


def test_periodic_recheck_permission_checks_are_explicitly_allowed():
    """modが許可済みを求めるツールを配布設定へそろえる。"""
    source = (_REPO_ROOT / "agent-toolkit/hooks/periodic_recheck.ts").read_text(encoding="utf-8")
    names = re.findall(r'\$\.tool\.check\s*\(\s*\{\s*tool:\s*"([^"]+)"', source)
    checks = re.findall(r"\$\.tool\.check\s*\(", source)
    assert len(names) == len(checks), "許可を要するツール名を静的に抽出できないcheckがある"
    required = set(names)
    assert required, "許可を要するツールの抽出結果が空"
    settings = json.loads((_REPO_ROOT / "share/claude_settings_json_managed.json").read_text(encoding="utf-8"))
    assert required <= set(settings["permissions"]["allow"])
