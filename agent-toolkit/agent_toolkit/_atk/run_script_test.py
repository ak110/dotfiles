"""登録済みplugin scriptの起動を検証する。"""

import argparse
import json
import os
import pathlib
import subprocess
import sys

import pytest

from agent_toolkit._atk import help_text, run_script
from agent_toolkit._common import next_action


def test_dispatch_forwards_help_and_exit_code(capsys: pytest.CaptureFixture[str]) -> None:
    args = argparse.Namespace(script_name="plan-check", script_args=["--", "--help"])
    assert run_script.dispatch(args) == 0
    assert "計画に必要な情報と実体が揃っているか確かめる" in capsys.readouterr().out


def test_dispatch_rejects_missing_registered_script(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(run_script.SCRIPT_PATHS, "missing", pathlib.Path("missing.py"))
    args = argparse.Namespace(script_name="missing", script_args=[])
    with pytest.raises(ValueError, match="plugin root内に存在しません"):
        run_script.dispatch(args)


def test_dispatch_forwards_session_review_evidence_arguments(monkeypatch: pytest.MonkeyPatch) -> None:
    """再度の一致確認に使う全引数を`atk run-script session-review-evidence`へ同じ順序で渡す。"""
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


@pytest.mark.parametrize("script_name", ["session-review-decisions", "session-review-report", "completion-report-check"])
def test_removed_session_review_entries_are_rejected(script_name: str, capsys: pytest.CaptureFixture[str]) -> None:
    """振り返りの判定入力と報告の生成器として撤去したコマンドを指定すると、未知の公開名として拒否する。"""
    parser = argparse.ArgumentParser()
    run_script.build_parser(parser)

    with pytest.raises(SystemExit) as raised:
        parser.parse_args([script_name])

    assert raised.value.code == 2
    assert script_name in capsys.readouterr().err
    assert "session-review-prepare" in run_script.SCRIPT_PATHS


def test_dispatch_forwards_termination_decision_file(monkeypatch: pytest.MonkeyPatch) -> None:
    """既存の終了判断記録へファイル引数を同じ順序で渡す。"""
    observed: list[str] = []

    def capture_argv(_target: str, *, run_name: str) -> None:
        assert run_name == "__main__"
        observed.extend(sys.argv)

    monkeypatch.setattr(run_script.runpy, "run_path", capture_argv)
    script_args = ["--decision-file", "/tmp/decision.json"]

    assert run_script.dispatch(argparse.Namespace(script_name="termination-evidence", script_args=["--", *script_args])) == 0

    assert observed == [str(run_script.registered_script_path("termination-evidence")), *script_args]


def test_dispatch_runs_review_contract_validator(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    """実行レビューの手順書が示す公開名で検証器を起動し、plugin環境の依存で実行して終了コードを透過する。

    登録が無い場合や登録パスが誤っている場合は起動できず、依存（yaml）を解決できない起動環境では検証へ到達しない。
    """
    contract = tmp_path / "review-contract.yaml"
    arguments = ["--", "--contract", str(contract), "--target-repo", str(tmp_path)]
    contract.write_text(
        "version: 1\nclauses:\n  - clause: 対象\n    content: commit abc1234の契約\n    source: 20260921-204636-005.md\n",
        encoding="utf-8",
    )

    assert run_script.dispatch(argparse.Namespace(script_name="review-contract", script_args=arguments)) == 0

    # `atk`と同じヘルプ書式で整形する。標準の書式は登録名の`-`で折り返し、名前の一致を判定できないため。
    parser = argparse.ArgumentParser(formatter_class=help_text.JapaneseHelpFormatter)
    run_script.build_parser(parser)
    # 手順書を読んだ主体が登録名を推測せず`--help`だけで見つけられることを保証する。
    assert "review-contract" in parser.format_help()

    contract.write_text("version: 1\nclauses: []\n", encoding="utf-8")

    assert run_script.dispatch(argparse.Namespace(script_name="review-contract", script_args=arguments)) == 2
    assert "clauses" in capsys.readouterr().err


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

### 受入シナリオ

| シナリオ | 由来 | 消費主体と入口 | 操作 | 期待結果 | テスト |
| --- | --- | --- | --- | --- | --- |
| 保存 | ユーザー指示 | 計画作成者と`atk` | 隔離したhomeを渡す | 計画を保存する | 本テスト |

## 恒久化・リファクタリング

### 恒久化

| 知見 | 出所 | 反映先 | 根拠 |
| --- | --- | --- | --- |
| 登録済み入口 | ユーザー指示 | テスト | 公開経路を固定するため。 |

### リファクタリング

| 対象 | 現状の問題 | 対応 |
| --- | --- | --- |
| 起動経路 | 実入口のテストが無い。 | 結合テストを追加する。 |

## 変更履歴

### ユーザー発言1

```text
登録済み入口を検証する。
```

## 検証

| 区分 | 検証コマンド |
| --- | --- |
| 変更範囲の検証 | `pytest` |

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


def test_public_plan_progress_entry_rejects_removed_start_head(tmp_path: pathlib.Path) -> None:
    """公開された`atk run-script plan-progress`のヘルプに撤去した`--start-head`が無く、渡すと引数エラーで終わる。

    開始時のHEADは専用branchとベースbranchから`git merge-base`で得るため、進捗ログへ記録する手段を撤去した。
    エンドユーザーが呼び出す`atk run-script`のスクリプト選択と引数の受け渡しを経ても、
    撤去したオプションが受理されないことを確かめる。
    """
    executable = run_script.PLUGIN_ROOT / "bin/atk"
    environment = dict(os.environ)
    environment.pop("PYTHONPATH", None)
    environment.pop("VIRTUAL_ENV", None)
    plan = tmp_path / "plan.md"
    plan.write_text("# 計画\n", encoding="utf-8")
    saved = plan.read_bytes()

    help_result = subprocess.run(
        [executable, "run-script", "plan-progress", "--", "--help"],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    rejected = subprocess.run(
        [
            executable,
            "run-script",
            "plan-progress",
            "--",
            str(plan),
            "--completed-step",
            "開始",
            "--result",
            "専用worktree",
            "--start-head",
            "HEAD",
        ],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert help_result.returncode == 0, help_result.stderr
    assert "--completed-step" in help_result.stdout
    assert "--start-head" not in help_result.stdout
    assert rejected.returncode == 2, rejected.stderr
    assert "--start-head" in rejected.stderr
    assert plan.read_bytes() == saved


def test_unregistered_script_lists_registered_names() -> None:
    """未登録のscript名は登録済みscriptの一覧を次の操作として示す。"""
    with pytest.raises(next_action.ActionableError) as raised:
        run_script.registered_script_path("unknown-script")

    assert "plan-check" in raised.value.next_action
    assert "session-review-prepare" in raised.value.next_action


def test_string_system_exit_is_reported_as_failure_with_next_action(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """scriptが文字列で終了した場合は失敗行と、再実行と受理形式の確認を示す次の操作の行で包む。"""

    def exit_with_message(_target: str, *, run_name: str) -> None:
        del run_name
        raise SystemExit("入力ファイルが無い")

    monkeypatch.setattr(run_script.runpy, "run_path", exit_with_message)

    assert run_script.dispatch(argparse.Namespace(script_name="plan-check", script_args=[])) == 1

    lines = capsys.readouterr().err.splitlines()
    assert lines[0].startswith("失敗: ")
    assert "入力ファイルが無い" in lines[0]
    assert lines[1].startswith("次の操作: ")
    assert "atk run-script plan-check -- --help" in lines[1]
