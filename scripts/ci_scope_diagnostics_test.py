"""差分判定の失敗理由と全体実行への切替を検証する。"""

import subprocess

import ci_scope
import pytest


@pytest.mark.parametrize("failure", ["sha", "paths", "git"])
def test_ci_scope_preserves_fallback_reason(tmp_path, monkeypatch, capsys, failure):
    """SHA欠落・空差分・Git失敗をログとActionsの値へ残す。"""
    for key, value in {"EVENT_NAME": "pull_request", "PR_BASE_SHA": "base", "CURRENT_SHA": "head"}.items():
        monkeypatch.setenv(key, value)
    output = tmp_path / "output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    if failure == "sha":
        monkeypatch.setenv("PR_BASE_SHA", "")

    def run(command, **_kwargs):
        if failure == "git":
            raise subprocess.CalledProcessError(128, command, stderr="fatal: bad revision\nmissing history\n")
        return subprocess.CompletedProcess(command, 0, "common" if command[1] == "merge-base" else b"")

    monkeypatch.setattr(ci_scope.subprocess, "run", run)
    ci_scope.main()
    stdout = capsys.readouterr().out
    expected = {
        "sha": "比較元または対象のSHAがない",
        "paths": "変更パスを取得できない",
        "git": "fatal: bad revision missing history",
    }[failure]
    assert expected in stdout
    assert "全体を実行する" in stdout
    values = output.read_text(encoding="utf-8")
    assert "rust=true\nbrowser=true\n" in values
    assert expected in values
    assert len(values.splitlines()) == 3
    print(stdout)
