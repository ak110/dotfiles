"""`notation-rules.md`が定める外部パス検査の回帰テスト。"""

import json
import pathlib
import subprocess
import sys


def _run_pyfltr(target: pathlib.Path, *, cwd: pathlib.Path, extra: list[str]) -> list[dict[str, object]]:
    """指定した起動条件でpyfltrを実行し、JSONLレコードを返す。"""
    command = [
        sys.executable,
        "-m",
        "pyfltr",
        "run",
        "--commands=textlint,colloquial-check",
        "--enable=colloquial-check",
        "--no-exclude",
        "--no-fix",
        "--no-quiet",
        "--output-format=jsonl",
        *extra,
        str(target),
    ]
    result = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=cwd,
    )
    assert result.returncode == 0, result.stdout or result.stderr
    return [json.loads(line) for line in result.stdout.splitlines() if line.startswith("{")]


def _assert_all_commands_reach_one_file(records: list[dict[str, object]]) -> None:
    """要求した2コマンドが対象1件へ到達し、警告とskipが無いことを確認する。"""
    header = next(record for record in records if record.get("kind") == "header")
    assert header["files"] == 1
    commands = [record for record in records if record.get("kind") == "command"]
    assert {record["command"] for record in commands} == {"textlint", "colloquial-check"}
    assert all(record.get("files") == 1 for record in commands)
    assert all(record.get("status") != "skipped" for record in commands)
    assert not any(record.get("kind") == "warning" for record in records)


def test_pyfltr_reaches_an_external_markdown_file(tmp_path: pathlib.Path) -> None:
    """設定プロジェクト外の絶対パスを警告やskipなしで検査する。"""
    project_root = pathlib.Path(__file__).resolve().parents[4]
    target = tmp_path / "external-target.md"
    target.write_text("# 外部対象\n\n検査対象の文書である。\n", encoding="utf-8")

    records = _run_pyfltr(
        target,
        cwd=project_root,
        extra=["--allow-external-paths", "--work-dir", str(project_root)],
    )

    _assert_all_commands_reach_one_file(records)


def test_external_markdown_without_allow_external_paths_is_not_fully_reached(tmp_path: pathlib.Path) -> None:
    """許可指定を外すとexternal-path警告とskipにより対象未到達になる。"""
    project_root = pathlib.Path(__file__).resolve().parents[4]
    target = tmp_path / "external-target.md"
    target.write_text("# 外部対象\n\n検査対象の文書である。\n", encoding="utf-8")

    records = _run_pyfltr(target, cwd=project_root, extra=["--work-dir", str(project_root)])

    assert any(record.get("source") == "external-path" for record in records)
    assert any(record.get("status") == "skipped" and record.get("files") == 0 for record in records)


def test_external_markdown_without_work_dir_does_not_reach_project_commands(tmp_path: pathlib.Path) -> None:
    """設定起点を外すとプロジェクトの検査コマンド全てへ対象が到達しない。"""
    target = tmp_path / "external-target.md"
    target.write_text("# 外部対象\n\n検査対象の文書である。\n", encoding="utf-8")

    records = _run_pyfltr(target, cwd=tmp_path, extra=["--allow-external-paths"])

    commands = {record["command"] for record in records if record.get("kind") == "command"}
    assert commands != {"textlint", "colloquial-check"}
    assert any(record.get("kind") == "warning" for record in records)


def test_repository_markdown_reaches_commands_with_the_existing_invocation() -> None:
    """リポジトリ内入力は追加オプションなしの従来経路で対象1件へ到達する。"""
    project_root = pathlib.Path(__file__).resolve().parents[4]
    target = project_root / "agent-toolkit/skills/writing-standards/references/notation-rules.md"

    records = _run_pyfltr(target, cwd=project_root, extra=[])

    _assert_all_commands_reach_one_file(records)
