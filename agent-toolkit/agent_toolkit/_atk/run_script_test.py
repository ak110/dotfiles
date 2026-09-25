"""登録済みplugin scriptの起動を検証する。"""

import argparse
import json
import os
import pathlib
import shlex
import subprocess
import sys

import pytest

from agent_toolkit._atk import run_script


def test_registry_stays_inside_plugin_root() -> None:
    for relative in run_script.SCRIPT_PATHS.values():
        target = (run_script.PLUGIN_ROOT / relative).resolve()
        assert target.is_relative_to(run_script.PLUGIN_ROOT)
        assert target.is_file()


def test_dispatch_forwards_help_and_exit_code(capsys: pytest.CaptureFixture[str]) -> None:
    args = argparse.Namespace(script_name="plan-check", script_args=["--", "--help"])
    assert run_script.dispatch(args) == 0
    assert "計画の成立に必要な情報契約" in capsys.readouterr().out


def test_dispatch_rejects_missing_registered_script(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(run_script.SCRIPT_PATHS, "missing", pathlib.Path("missing.py"))
    args = argparse.Namespace(script_name="missing", script_args=[])
    with pytest.raises(ValueError, match="plugin root内に存在しません"):
        run_script.dispatch(args)


def test_dispatch_forwards_session_review_evidence_arguments(monkeypatch: pytest.MonkeyPatch) -> None:
    """再照合の全引数を抽出器へ同じ順序で渡す。"""
    observed: list[str] = []

    def capture_argv(_target: str, *, run_name: str) -> None:
        assert run_name == "__main__"
        observed.extend(sys.argv)

    monkeypatch.setattr(run_script.runpy, "run_path", capture_argv)
    script_args = [
        "--transcript",
        "/tmp/transcript.jsonl",
        "--user-events",
        "--since",
        "2026-09-21T20:00:00Z",
        "--observation-boundary",
        "2026-09-21T20:05:00Z",
        "--output-file",
        "/tmp/additional-events.jsonl",
    ]

    assert run_script.dispatch(argparse.Namespace(script_name="session-review-evidence", script_args=["--", *script_args])) == 0

    assert observed == [str(run_script.registered_script_path("session-review-evidence")), *script_args]


def test_session_review_documents_use_public_script_entries() -> None:
    """振り返りの手順書の実行例が登録済み入口を使い、実装ファイルを直接起動しない。"""
    documents = (
        run_script.PLUGIN_ROOT / "skills" / "session-review" / "SKILL.md",
        run_script.PLUGIN_ROOT / "skills" / "session-review" / "references" / "lane-processing.md",
    )
    commands: list[list[str]] = []
    for document in documents:
        in_block = False
        for line in document.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped == "```text":
                in_block = True
            elif stripped == "```":
                in_block = False
            elif in_block and stripped.startswith("atk run-script session-review-"):
                commands.append(shlex.split(stripped))

    assert {command[2] for command in commands} == {
        "session-review-prepare",
        "session-review-decisions",
        "session-review-report",
    }
    for command in commands:
        assert command[:2] == ["atk", "run-script"]
        assert command[3] == "--"
        assert run_script.registered_script_path(command[2]).is_file()


def test_dispatch_forwards_completion_report_stage_and_state(monkeypatch: pytest.MonkeyPatch) -> None:
    """報告段階と振り返り状態を検査器へ同じ順序で渡す。"""
    observed: list[str] = []

    def capture_argv(_target: str, *, run_name: str) -> None:
        assert run_name == "__main__"
        observed.extend(sys.argv)

    monkeypatch.setattr(run_script.runpy, "run_path", capture_argv)
    script_args = ["/tmp/report.md", "--stage", "review-result", "--review-state", "failed"]

    assert run_script.dispatch(argparse.Namespace(script_name="completion-report-check", script_args=["--", *script_args])) == 0

    assert observed == [str(run_script.registered_script_path("completion-report-check")), *script_args]


@pytest.mark.parametrize(
    ("module_name", "script_source"),
    [
        ("eager_sibling", "import eager_sibling\nprint(eager_sibling.VALUE)\n"),
        (
            "delayed_sibling",
            "def main():\n    import delayed_sibling\n    print(delayed_sibling.VALUE)\n\nmain()\n",
        ),
    ],
)
def test_dispatch_resolves_sibling_module_and_restores_sys_path(
    module_name: str,
    script_source: str,
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    plugin_root = tmp_path / "plugin"
    plugin_root.mkdir()
    (plugin_root / f"{module_name}.py").write_text("VALUE = 'resolved'\n", encoding="utf-8")
    (plugin_root / "probe.py").write_text(script_source, encoding="utf-8")
    monkeypatch.setattr(run_script, "PLUGIN_ROOT", plugin_root)
    monkeypatch.setitem(run_script.SCRIPT_PATHS, "probe", pathlib.Path("probe.py"))
    previous_path = list(sys.path)

    try:
        assert run_script.dispatch(argparse.Namespace(script_name="probe", script_args=[])) == 0

        assert capsys.readouterr().out == "resolved\n"
        assert sys.path == previous_path
    finally:
        sys.modules.pop(module_name, None)


def test_dispatch_restores_sys_path_when_script_raises(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin_root = tmp_path / "plugin"
    plugin_root.mkdir()
    script = plugin_root / "probe.py"
    script.write_text("import sys\nsys.path.append('leaked')\nraise RuntimeError('probe failed')\n", encoding="utf-8")
    monkeypatch.setattr(run_script, "PLUGIN_ROOT", plugin_root)
    monkeypatch.setitem(run_script.SCRIPT_PATHS, "probe", pathlib.Path("probe.py"))
    previous_path = list(sys.path)

    with pytest.raises(RuntimeError, match="probe failed"):
        run_script.dispatch(argparse.Namespace(script_name="probe", script_args=[]))

    assert sys.path == previous_path


def test_dispatch_keeps_worktree_inputs_independent(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    plugin_root = tmp_path / "plugin"
    plugin_root.mkdir()
    script = plugin_root / "probe.py"
    script.write_text(
        "import json, pathlib, sys\nprint(json.dumps({'cwd': str(pathlib.Path.cwd()), 'args': sys.argv[1:]}))\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(run_script, "PLUGIN_ROOT", plugin_root)
    monkeypatch.setitem(run_script.SCRIPT_PATHS, "probe", pathlib.Path("probe.py"))

    results: list[dict[str, object]] = []
    for name in ("worktree-a", "worktree-b"):
        worktree = tmp_path / name
        worktree.mkdir()
        monkeypatch.chdir(worktree)
        assert run_script.dispatch(argparse.Namespace(script_name="probe", script_args=["--", name])) == 0
        results.append(json.loads(capsys.readouterr().out))

    assert results == [
        {"cwd": str(tmp_path / "worktree-a"), "args": ["worktree-a"]},
        {"cwd": str(tmp_path / "worktree-b"), "args": ["worktree-b"]},
    ]


def test_registered_plan_create_runs_outside_repository_without_pythonpath(tmp_path: pathlib.Path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    subprocess.run(["git", "init", "--quiet"], cwd=repository, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=repository, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=repository, check=True)
    subprocess.run(["git", "commit", "--quiet", "--allow-empty", "-m", "base"], cwd=repository, check=True)
    source = tmp_path / "source.md"
    source.write_text(
        f"""# 外部入口検証

## 概要

登録済み入口を検証する。

### 計画メタ情報

- 起動経路: `agent-toolkit:plan-mode`
- 対象リポジトリ: `{repository}`
- 関連WI: なし
- 作業種別: 通常変更

## 実施内容

| 実施内容 | 由来 | 採否 | 根拠 |
| --- | --- | --- | --- |
| 登録済み入口を検証する | ユーザー指示 | 採用 | 原文の要求単位は1件であり、開放性を保ったまま実施範囲とする。 |

## 要件・外部仕様

隔離したhomeへ計画を保存する。

## 恒久化・リファクタリング

### 恒久化

| 知見 | 出所 | 反映先 | 根拠 |
| --- | --- | --- | --- |
| 登録済み入口 | ユーザー指示 | 検体 | 公開経路を固定するため。 |

### リファクタリング

| 対象 | 現状の問題 | 対応 |
| --- | --- | --- |
| 起動経路 | 実入口の検体が無い。 | 統合検体を追加する。 |

## 変更履歴

### ユーザー発言1

```text
登録済み入口を検証する。
```

## 検証

| 区分 | 検証コマンド |
| --- | --- |
| 近接検証 | `pytest` |
| 全体検証 | `make test` |

## 終端工程

なし

## 進捗ログ

| 日時 | 完了した工程 | 結果・特記事項 |
| --- | --- | --- |
""",
        encoding="utf-8",
    )
    outside = tmp_path / "outside"
    outside.mkdir()
    isolated_home = tmp_path / "home"
    executable = run_script.PLUGIN_ROOT / "bin/atk"
    assert executable.is_file()
    environment = dict(os.environ)
    environment.pop("PYTHONPATH", None)
    environment.pop("VIRTUAL_ENV", None)

    result = subprocess.run(
        [
            executable,
            "run-script",
            "plan-create",
            "--",
            "--main-source",
            str(source),
            "--name",
            "01-0000_external-entry",
            "--home",
            str(isolated_home),
            "--work-dir",
            str(repository),
        ],
        cwd=outside,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    output = pathlib.Path(result.stdout.strip())
    assert output == isolated_home / ".claude/plans/01-0000_external-entry.md"
    assert output.is_file()
