"""独立中継の実保存境界で大量出力と終了状態を検証する。"""

from __future__ import annotations

import os
import pathlib
import signal
import subprocess
import sys

import pytest

from agent_toolkit._common import diagnostic_process


def test_relay_spawn_failure_offers_recovery(tmp_path: pathlib.Path, capsys) -> None:
    """起動失敗でも秘密値を保存せず、復旧のための操作を示す。"""
    assert diagnostic_process.relay_diagnostics([str(tmp_path / "missing"), "password=fake-spawn"]) == 1
    saved = capsys.readouterr().err
    assert "FileNotFoundError" in saved
    assert "fake-spawn" not in saved
    assert "存在と実行権限" in saved and "再実行" in saved


def test_relay_saves_redacted_both_streams_and_exit_code(tmp_path: pathlib.Path, host_environ) -> None:
    """PIPE容量を超える両ストリームと複数行秘密鍵を、中継の保存先で確認する。"""
    script = tmp_path / "child.py"
    script.write_text(
        "import sys\n"
        "for _ in range(2000):\n"
        " print('E401 password=fake-output')\n"
        " print('E402 token=fake-stderr', file=sys.stderr)\n"
        "print('-----BEGIN ' + 'PRIVATE KEY-----\\nfake-key-body\\n-----END ' + 'PRIVATE KEY-----', file=sys.stderr)\n"
        "raise SystemExit(7)\n",
        encoding="utf-8",
    )
    out, err = tmp_path / "stdout.log", tmp_path / "stderr.log"
    with out.open("wb") as stdout, err.open("wb") as stderr:
        result = subprocess.run(
            diagnostic_process.command([sys.executable, str(script)]),
            stdout=stdout,
            stderr=stderr,
            env=host_environ(),
            timeout=30,
            check=False,
        )
    assert result.returncode == 7
    saved_out, saved_err = out.read_text(encoding="utf-8"), err.read_text(encoding="utf-8")
    assert saved_out.count("E401") == saved_err.count("E402") == 2000
    assert "fake-output" not in saved_out and "fake-stderr" not in saved_err and "fake-key-body" not in saved_err


@pytest.mark.skipif(os.name == "nt", reason="POSIXのsignal終了コード契約")
def test_relay_preserves_signal_exit(host_environ) -> None:
    """子のsignal終了を通常の終了コードへ変換しない。"""
    result = subprocess.run(
        diagnostic_process.command([sys.executable, "-c", "import os,signal; os.kill(os.getpid(),signal.SIGTERM)"]),
        capture_output=True,
        env=host_environ(),
        timeout=30,
        check=False,
    )
    assert result.returncode == -signal.SIGTERM
