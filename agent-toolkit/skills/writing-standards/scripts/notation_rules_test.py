# agent-doc-tone: test-data
"""`notation-rules.md`「口語表現チェック」が定める引数と完了判定の回帰テスト。

規範どおりの引数でpyfltr MCPの`run`ツールの実装（`pyfltr.cli.mcp_server.tool_run`）を呼び、
規範が定める判定条件が到達・完了した対象だけを完了と判定し、未到達・未完了を完了と判定しないことを確かめる。
"""

import asyncio
import json
import pathlib
import subprocess
import sys

import pyfltr.cli.mcp_server as pyfltr_mcp_server
import pytest

# textlintをpnpmの`dlx`経由で実際に動かすため、取得物の保存先をホストと共有して毎回の取得し直しを避ける。
pytestmark = pytest.mark.usefixtures("share_package_caches")

_PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[4]
_REPOSITORY_MARKDOWN = _PROJECT_ROOT / "agent-toolkit/skills/writing-standards/references/notation-rules.md"
_REPOSITORY_PYTHON = pathlib.Path(__file__).resolve()


def _commands_for(target: pathlib.Path) -> list[str]:
    """規範が定める拡張子別のコマンドを返す。"""
    return ["textlint", "colloquial-check"] if target.suffix == ".md" else ["colloquial-check"]


def _is_completed(result: dict[str, object], commands: list[str]) -> bool:
    """規範が定める到達・完了の条件を、MCP応答またはCLIの`summary`の共通4項目へ適用する。"""
    completed_commands = result["completed_commands"]
    assert isinstance(completed_commands, list)
    return (
        result["completion"] == "completed"
        and result["files_reached"] == 1
        and all(command in completed_commands for command in commands)
        and not result["incomplete_commands"]
    )


def _run_mcp(target: pathlib.Path, *, allow_external_paths: bool) -> dict[str, object]:
    """規範どおりの引数でpyfltr MCPの`run`を呼び、応答を辞書で返す。"""
    result = asyncio.run(
        pyfltr_mcp_server.tool_run(
            paths=[str(target)],
            work_dir=str(_PROJECT_ROOT),
            commands=_commands_for(target),
            enable=["colloquial-check"],
            no_exclude=True,
            no_fix=True,
            allow_external_paths=allow_external_paths,
        )
    )
    return result.model_dump()


def _external_markdown(tmp_path: pathlib.Path) -> pathlib.Path:
    target = tmp_path / "external-target.md"
    target.write_text("# 外部対象\n\n検査対象の文書である。\n", encoding="utf-8")
    return target


@pytest.mark.parametrize("kind", ["repository-markdown", "repository-python", "external-markdown"])
def test_mcp_run_with_documented_arguments_is_completed(kind: str, tmp_path: pathlib.Path) -> None:
    """リポジトリ内のMarkdown・Python、許可指定付きの外部Markdownは完了と判定され、診断取得用の`run_id`を持つ。"""
    target = {
        "repository-markdown": _REPOSITORY_MARKDOWN,
        "repository-python": _REPOSITORY_PYTHON,
        "external-markdown": _external_markdown(tmp_path),
    }[kind]

    result = _run_mcp(target, allow_external_paths=kind == "external-markdown")

    assert _is_completed(result, _commands_for(target)), result
    assert result["run_id"]


def test_mcp_run_without_allow_external_paths_is_not_completed(tmp_path: pathlib.Path) -> None:
    """外部Markdownで許可指定を外すと未完了になり、未完了のコマンドから理由を特定できる。"""
    target = _external_markdown(tmp_path)

    result = _run_mcp(target, allow_external_paths=False)

    assert not _is_completed(result, _commands_for(target))
    assert result["completion"] == "incomplete"
    assert result["incomplete_commands"]


def test_mcp_run_for_missing_target_is_not_reached(tmp_path: pathlib.Path) -> None:
    """存在しない対象は未到達になり、`missing_targets`から理由を特定できる。"""
    target = tmp_path / "missing.md"

    result = _run_mcp(target, allow_external_paths=True)

    assert not _is_completed(result, _commands_for(target))
    assert result["completion"] == "not_reached"
    assert result["missing_targets"]


@pytest.mark.parametrize(("allow_external_paths", "expected"), [(True, True), (False, False)])
def test_cli_summary_alone_decides_completion(tmp_path: pathlib.Path, allow_external_paths: bool, expected: bool) -> None:
    """MCPを使えない場合のCLIでは、JSONL最終行の`summary`だけでMCPと同じ判定になる。"""
    target = _external_markdown(tmp_path)
    commands = _commands_for(target)
    command = [
        sys.executable,
        "-m",
        "pyfltr",
        "run",
        f"--commands={','.join(commands)}",
        "--enable=colloquial-check",
        "--no-exclude",
        "--no-fix",
        "--output-format=jsonl",
        "--work-dir",
        str(_PROJECT_ROOT),
        *(["--allow-external-paths"] if allow_external_paths else []),
        str(target),
    ]
    process = subprocess.run(command, check=False, capture_output=True, text=True, encoding="utf-8", cwd=_PROJECT_ROOT)

    summary = json.loads(process.stdout.splitlines()[-1])

    assert summary["kind"] == "summary", process.stdout
    assert _is_completed(summary, commands) is expected
