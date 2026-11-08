"""Codexの読取案内（`share/rules/rules-common.codex.md`・`share/agents-server-explore.md`）の行範囲読取がWindowsで成立することを確かめる。

案内は、シェルを指定しないコマンドがPowerShellで動く環境で、
実在を確かめたbashの`sed -n`か、bashが無い場合の`Get-Content`を示す。
どちらも日本語と空白を含む絶対パスの指定行を読み、終了コード0で同じ本文を返すこと、存在しないパスでは0以外で終わることを、
Windows CIの「Windows境界テスト」（`pytest -m windows`）で実機の`powershell.exe`・`pwsh`・bashに対して観測する。
"""

import os
import pathlib
import shutil
import subprocess
import sys

import pytest

from agent_toolkit._testing.isolation import restore_host_environment

pytestmark = [pytest.mark.windows, pytest.mark.skipif(sys.platform != "win32", reason="Windows実機のシェルで確かめる")]

_EXPECTED = ["二行目 日本語", "三行目"]
# 子シェル1回の上限。各試験は子を最大4回起動するため、
# pytestのテスト単位の上限（`--timeout=60`）より先に子の停止を検出して診断を残せる値にする。
_CHILD_TIMEOUT = 10

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


def _run(argv: list[str], target: pathlib.Path) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        argv, capture_output=True, check=False, timeout=_CHILD_TIMEOUT, env={**os.environ, "READ_TARGET": str(target)}
    )


def _powershells() -> list[str]:
    return [path for path in (shutil.which("powershell"), shutil.which("pwsh")) if path is not None]


def test_powershell_get_content_reads_the_line_range(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`powershell.exe`（5.1）と`pwsh`の両方で、案内の`Get-Content`が指定行を返し、不在のパスでは失敗する。"""
    # 案内が対象とするのはホストの実シェルなので、隔離したHOME・設定ディレクトリ・PATHをホストの値へ戻してから解決・起動する。
    restore_host_environment(monkeypatch)
    target = _target(tmp_path)
    shells = _powershells()
    assert shells, "Windows CIにPowerShellが見つからない"
    for shell in shells:
        argv = [shell, "-NoProfile", "-NonInteractive", "-Command", _POWERSHELL_READ]
        result = _run(argv, target)
        assert result.returncode == 0, (shell, result.stderr.decode("utf-8", "replace"))
        assert result.stdout.decode("utf-8").splitlines() == _EXPECTED, shell
        missing = _run(argv, target.with_name("存在しない.md"))
        assert missing.returncode != 0, shell


def test_bash_found_by_get_command_reads_the_line_range(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`Get-Command bash`で実在を確かめたbashが起動できる場合、案内の`sed -n`が同じ指定行を返す。"""
    restore_host_environment(monkeypatch)
    target = _target(tmp_path)
    shell = _powershells()[0]
    found = subprocess.run(
        [shell, "-NoProfile", "-NonInteractive", "-Command", "(Get-Command bash -ErrorAction Stop).Source"],
        capture_output=True,
        check=False,
        timeout=_CHILD_TIMEOUT,
    )
    if found.returncode != 0:
        pytest.skip("bashが無い環境は`Get-Content`の試験で確かめる")
    bash = found.stdout.decode("utf-8", "replace").strip()
    # WSLの起動用`bash.exe`のように、実在しても起動できないか応答しないbashは案内の「実在を確認したbash」に当たらない。
    try:
        launchable = _run([bash, "-c", "true"], target).returncode == 0
    except subprocess.TimeoutExpired:
        launchable = False
    if not launchable:
        pytest.skip(f"Get-Commandが返したbashを起動できない: {bash}")
    result = _run([bash, "-c", 'sed -n "2,3p" -- "$READ_TARGET"'], target)
    assert result.returncode == 0, result.stderr.decode("utf-8", "replace")
    assert result.stdout.decode("utf-8").splitlines() == _EXPECTED
