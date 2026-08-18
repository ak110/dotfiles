"""CodexのClaude MCP登録削除処理を検証する。"""

import subprocess
from collections.abc import Callable

import pytest

from pytools._internal import remove_codex_claude_mcp

type Call = tuple[list[str], float | None, str | None]
type Result = subprocess.CompletedProcess[str] | None


def _result(returncode: int, *, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess[str]:
    """テスト用のコマンド結果を返す。"""
    return subprocess.CompletedProcess(["codex"], returncode, stdout=stdout, stderr=stderr)


def _fake_runner(results: list[Result], calls: list[Call]) -> Callable[..., Result]:
    """結果を順に返し、コマンド契約を記録する代用関数を返す。"""

    def run(
        cmd: list[str],
        *,
        timeout: float | None = None,
        tag: str | None = None,
        **kwargs: object,
    ) -> Result:
        del kwargs
        calls.append((cmd, timeout, tag))
        return results.pop(0)

    return run


@pytest.mark.parametrize(
    "stderr",
    [
        "Error: No MCP server named 'claude' found.\n",
        (
            "(node:40712) [UNDICI-EHPA] Warning: EnvHttpProxyAgent is experimental,"
            " expect them to change at any time.\n"
            "(Use `node --trace-warnings ...` to show where the warning was created)\n"
            "Error: No MCP server named 'claude' found.\n"
        ),
    ],
)
def test_run_skips_removal_when_claude_mcp_is_not_registered(monkeypatch: pytest.MonkeyPatch, stderr: str) -> None:
    """未登録なら取得だけを実行して変更なしを返す。

    実行環境が付加する警告が標準エラーへ先行する場合も未登録と判定する。
    """
    calls: list[Call] = []
    results: list[Result] = [_result(1, stderr=stderr)]
    monkeypatch.setattr(
        remove_codex_claude_mcp.claude_common,
        "run_subprocess",
        _fake_runner(results, calls),
    )

    assert remove_codex_claude_mcp.run() is False
    assert calls == [(["codex", "mcp", "get", "claude", "--json"], 30, "codex")]


def test_run_removes_registered_claude_mcp(monkeypatch: pytest.MonkeyPatch) -> None:
    """登録済みなら取得後に削除して変更ありを返す。"""
    calls: list[Call] = []
    results: list[Result] = [_result(0, stdout='{"name":"claude"}'), _result(0)]
    monkeypatch.setattr(
        remove_codex_claude_mcp.claude_common,
        "run_subprocess",
        _fake_runner(results, calls),
    )

    assert remove_codex_claude_mcp.run() is True
    assert calls == [
        (["codex", "mcp", "get", "claude", "--json"], 30, "codex"),
        (["codex", "mcp", "remove", "claude"], 30, "codex"),
    ]


@pytest.mark.parametrize(
    ("result", "error_detail"),
    [
        (None, "実行に失敗"),
        (_result(2, stderr="permission denied"), "stderr: permission denied"),
        (_result(1, stderr="Error: config file is invalid"), "stderr: Error: config file is invalid"),
    ],
)
def test_run_raises_when_get_fails(
    monkeypatch: pytest.MonkeyPatch,
    result: Result,
    error_detail: str,
) -> None:
    """取得不能と想定外の非ゼロ終了を例外として上位へ伝える。

    期待するエラー文言を含まない終了コード1の失敗を未登録と誤判定しない。
    """
    calls: list[Call] = []
    monkeypatch.setattr(
        remove_codex_claude_mcp.claude_common,
        "run_subprocess",
        _fake_runner([result], calls),
    )

    with pytest.raises(RuntimeError, match="Claude MCP登録の取得に失敗") as exc_info:
        remove_codex_claude_mcp.run()

    assert error_detail in str(exc_info.value)
    assert calls == [(["codex", "mcp", "get", "claude", "--json"], 30, "codex")]


@pytest.mark.parametrize(
    ("result", "error_detail"),
    [
        (None, "実行に失敗"),
        (_result(2, stdout="remove failed"), "stdout: remove failed"),
    ],
)
def test_run_raises_when_remove_fails(
    monkeypatch: pytest.MonkeyPatch,
    result: Result,
    error_detail: str,
) -> None:
    """削除不能と非ゼロ終了を例外として上位へ伝える。"""
    calls: list[Call] = []
    monkeypatch.setattr(
        remove_codex_claude_mcp.claude_common,
        "run_subprocess",
        _fake_runner([_result(0), result], calls),
    )

    with pytest.raises(RuntimeError, match="Claude MCP登録の削除に失敗") as exc_info:
        remove_codex_claude_mcp.run()

    assert error_detail in str(exc_info.value)
    assert calls == [
        (["codex", "mcp", "get", "claude", "--json"], 30, "codex"),
        (["codex", "mcp", "remove", "claude"], 30, "codex"),
    ]
