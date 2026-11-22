"""読取案内（探索の起動の本文の`launch_prompts.EXPLORE_CAPABILITIES`と`atk-mode`の`playbooks/host-codex.md`）の
行範囲読取がWindowsで成立することを確かめる。

シェルを指定しないコマンドがPowerShellで動くなら、日本語と空白を含む絶対パスの指定行を`Get-Content`で読み、
不在のパスでは非0で終わることをWindows CIの`powershell.exe`・`pwsh`で観測する。
"""

import os
import pathlib
import shutil
import subprocess
import sys
import time

import pytest

from agent_toolkit._testing.isolation import restore_host_environment

pytestmark = [pytest.mark.windows, pytest.mark.skipif(sys.platform != "win32", reason="Windows実機のシェルで確かめる")]

_EXPECTED = ["二行目 日本語", "三行目"]
# 1試験で起動する子シェル全体の上限。負荷の高いWindows CIではPowerShellの1回の起動が10秒を超えたことがあるため、
# 起動ごとに固定の上限を置かず、試験の残り時間を各起動へ割り当てる。
# pytestのテスト単位の上限（`--timeout=60`）より先に子の停止を検出して診断を残せる値にする。
_TEST_BUDGET = 50
# 案内の`Get-Content`の行範囲読取。PowerShellがパイプへ書く文字コードは案内の対象外のホスト側の設定であり、
# 本文の比較のために出力の文字コードだけをUTF-8へそろえる。
_POWERSHELL_READ = (
    "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; "
    "Get-Content -LiteralPath $env:READ_TARGET -Encoding utf8 -ErrorAction Stop | Select-Object -Skip 1 -First 2"
)


def _target(tmp_path: pathlib.Path) -> pathlib.Path:
    directory = tmp_path / "資料 フォルダ"
    directory.mkdir()
    target = directory / "規範 本文.md"
    target.write_bytes("一行目\n二行目 日本語\n三行目\n四行目\n".encode())
    return target


def _run(argv: list[str], target: pathlib.Path, deadline: float) -> subprocess.CompletedProcess[bytes]:
    timeout = max(deadline - time.monotonic(), 0)
    try:
        return subprocess.run(
            argv, capture_output=True, check=False, timeout=timeout, env={**os.environ, "READ_TARGET": str(target)}
        )
    except subprocess.TimeoutExpired as error:
        # 停止した子のどの段階で止まったかを、CIの失敗記録だけで判断できるよう、取得済みの出力を失敗へ残す。
        raise TimeoutError(
            f"{argv[0]}が残り時間の{timeout:.1f}秒で終わらなかった: stdout={error.stdout!r} stderr={error.stderr!r}"
        ) from error


def _powershells() -> list[str]:
    return [path for path in (shutil.which("powershell"), shutil.which("pwsh")) if path is not None]


def test_powershell_get_content_reads_the_line_range(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`powershell.exe`（5.1）と`pwsh`の両方で、案内の`Get-Content`が指定行を返し、不在のパスでは失敗する。"""

    restore_host_environment(monkeypatch)
    deadline = time.monotonic() + _TEST_BUDGET
    target = _target(tmp_path)
    shells = _powershells()
    assert shells, "Windows CIにPowerShellが見つからない"
    for shell in shells:
        argv = [shell, "-NoProfile", "-NonInteractive", "-Command", _POWERSHELL_READ]
        result = _run(argv, target, deadline)
        assert result.returncode == 0, (shell, result.stderr.decode("utf-8", "replace"))
        assert result.stdout.decode("utf-8").splitlines() == _EXPECTED, shell
        missing = _run(argv, target.with_name("存在しない.md"), deadline)
        assert missing.returncode != 0, shell
