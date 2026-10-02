"""選定結果の`write_files`がAWI本文の反映先パスを覆うかの検証を確かめる。"""

import argparse
import pathlib
import typing

import check_selection
import pytest
import yaml

from agent_toolkit._atk import run_script  # noqa: E402  # pylint: disable=wrong-import-position,import-error

_REPO_FILES = (
    "README.md",
    "src/model.py",
    "src-old/model.py",
    "docs/development/design.md",
    "agent-toolkit/agent_toolkit/_agents_server/status_file.py",
    "rust/claude-statusline/src/agents_server.rs",
    "bin/update-dotfiles",
    "bin/update-dotfiles.cmd",
    "scripts/update_dotfiles.py",
    "pytools/post_apply.py",
)


@pytest.fixture(name="env")
def fixture_env(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> tuple[pathlib.Path, pathlib.Path]:
    """対象リポジトリとキュー管理リポジトリを`tmp_path`配下へ作成し、そのパスを返す。"""
    repo = tmp_path / "repo"
    for relative in _REPO_FILES:
        (repo / relative).parent.mkdir(parents=True, exist_ok=True)
        (repo / relative).write_text("x\n", encoding="utf-8")
    notes = tmp_path / "notes"
    (notes / "processing").mkdir(parents=True)
    monkeypatch.setenv("AGENT_TOOLKIT_PRIVATE_NOTES", str(notes))
    return repo, notes


def _awi(notes: pathlib.Path, name: str, reflected: str, *, state: str = "processing") -> None:
    """`## 反映内容と反映先`に`reflected`を持つAWI本文を保存する。"""
    (notes / state).mkdir(exist_ok=True)
    body = (
        f"---\ntype: awi\nsource: test\n---\n\n# 題\n\n## 反映内容と反映先\n\n{reflected}\n\n"
        "## 完成条件\n\n- `outside.py`を変える\n"
    )
    (notes / state / name).write_text(body, encoding="utf-8")


def _run(tmp_path: pathlib.Path, repo: pathlib.Path, decisions: list[dict[str, typing.Any]]) -> int:
    """選定結果を保存して検証を実行し、終了コードを返す。"""
    selection = tmp_path / "selection.yaml"
    selection.write_text(yaml.safe_dump({"decisions": decisions}, allow_unicode=True), encoding="utf-8")
    return check_selection.main(["--work-dir", str(repo), str(selection)])


def test_reports_uncovered_broad_and_invalid_exclusion(
    tmp_path: pathlib.Path, env: tuple[pathlib.Path, pathlib.Path], capsys: pytest.CaptureFixture[str]
) -> None:
    """未被覆、広すぎる範囲、反映先に無い除外の3区分を、AWIのファイル名とパスを示して非0で返す。"""
    repo, notes = env
    _awi(notes, "a.md", "`src/model.py`と`docs/development/design.md`を変える。")
    _awi(notes, "b.md", "`src/model.py`を変える。")
    _awi(notes, "c.md", "`src/model.py`を変える。`README.md`は変更しない。")
    decisions = [
        {"awi": "a.md", "lane": "lane-01", "write_files": ["src/model.py"]},
        {"awi": "b.md", "lane": "lane-01", "write_files": ["src/"]},
        {"awi": "c.md", "lane": "lane-02", "write_files": ["src/model.py"], "excluded_paths": ["README.md", "LICENSE"]},
    ]

    assert _run(tmp_path, repo, decisions) == 1

    err = capsys.readouterr().err
    assert "a.md: 未被覆: docs/development/design.md" in err
    assert "b.md: 広すぎる範囲: src/" in err
    assert "c.md: excluded_pathsの不正: LICENSE" in err
    assert "c.md: excluded_pathsの不正: README.md" not in err
    assert "次の操作: " in err


def test_accepts_covered_selection_and_skips_out_of_scope_decisions(
    tmp_path: pathlib.Path, env: tuple[pathlib.Path, pathlib.Path], capsys: pytest.CaptureFixture[str]
) -> None:
    """全反映先を覆う選定結果、`lane: なし`、節を持たない本文は違反なしで0を返す。"""
    repo, notes = env
    reflected = (
        "`src/model.py:12-20`と新設の`src/new_module.py`、範囲`src/`を変える。"
        "`atk wi add`、`/abs/path.py`、`~/x.md`、`$ROOT/a.py`、`<file>`、`*.py`、`https://example.com/a/b`、`missing/dir/x.py`、`model.py`は反映先ではない。"
    )
    _awi(notes, "a.md", reflected)
    _awi(notes, "b.md", "`docs/development/design.md`を変える。")
    (notes / "processing" / "u.md").write_text("---\ntype: uwi\n---\n\n## 質問\n\nどちらか？\n", encoding="utf-8")
    decisions = [
        {"awi": "a.md", "lane": "lane-01", "write_files": ["src/"]},
        {"awi": "b.md", "lane": "なし", "write_files": []},
        {"awi": "u.md", "lane": "lane-01", "write_files": ["src/"]},
    ]

    assert _run(tmp_path, repo, decisions) == 0
    assert capsys.readouterr().err == ""


def test_directory_range_matches_by_path_element(
    tmp_path: pathlib.Path, env: tuple[pathlib.Path, pathlib.Path], capsys: pytest.CaptureFixture[str]
) -> None:
    """`src/`は`src-old/model.py`を覆わない。"""
    repo, notes = env
    _awi(notes, "a.md", "`src-old/model.py`を変える。")

    assert _run(tmp_path, repo, [{"awi": "a.md", "lane": "lane-01", "write_files": ["src/"]}]) == 1
    assert "a.md: 未被覆: src-old/model.py" in capsys.readouterr().err


def test_detects_observed_selection_defects(
    tmp_path: pathlib.Path, env: tuple[pathlib.Path, pathlib.Path], capsys: pytest.CaptureFixture[str]
) -> None:
    """反映先の一部を欠いた`write_files`と、上位ディレクトリだけを書いた`write_files`の観測事例を検出する。"""
    repo, notes = env
    _awi(
        notes,
        "statusline.md",
        "`rust/claude-statusline/src/agents_server.rs`、`agent-toolkit/agent_toolkit/_agents_server/status_file.py`、"
        "`docs/development/design.md`を変える。",
    )
    _awi(notes, "update.md", "`bin/update-dotfiles`、`bin/update-dotfiles.cmd`、`scripts/update_dotfiles.py`を変える。")
    decisions = [
        {"awi": "statusline.md", "lane": "lane-01", "write_files": ["rust/claude-statusline/"]},
        {"awi": "update.md", "lane": "lane-02", "write_files": ["bin/update-dotfiles", "pytools/"]},
    ]

    assert _run(tmp_path, repo, decisions) == 1

    err = capsys.readouterr().err
    assert "statusline.md: 未被覆: agent-toolkit/agent_toolkit/_agents_server/status_file.py" in err
    assert "statusline.md: 未被覆: docs/development/design.md" in err
    assert "statusline.md: 広すぎる範囲: rust/claude-statusline/" in err
    assert "update.md: 未被覆: bin/update-dotfiles.cmd" in err
    assert "update.md: 未被覆: scripts/update_dotfiles.py" in err


def test_reports_missing_body_and_rejects_unreadable_input(
    tmp_path: pathlib.Path,
    env: tuple[pathlib.Path, pathlib.Path],
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """本文を特定できないdecisionは違反として、キュー管理リポジトリの不在と読めない選定結果は入力エラーとして返す。"""
    repo, _notes = env

    assert _run(tmp_path, repo, [{"awi": "absent.md", "lane": "lane-01", "write_files": []}]) == 1
    assert "absent.md: 本文を特定できない" in capsys.readouterr().err

    missing_root = tmp_path / "missing-notes"
    monkeypatch.setenv("AGENT_TOOLKIT_PRIVATE_NOTES", str(missing_root))
    assert _run(tmp_path, repo, [{"awi": "a.md", "lane": "lane-01", "write_files": []}]) == 2
    err = capsys.readouterr().err
    assert f"キュー管理リポジトリが実在しない: {missing_root}" in err
    assert "次の操作: " in err
    assert "Traceback" not in err

    broken = tmp_path / "broken.yaml"
    broken.write_text("decisions: [\n", encoding="utf-8")
    assert check_selection.main(["--work-dir", str(repo), str(broken)]) == 2
    err = capsys.readouterr().err
    assert "選定結果を読み込めない" in err
    assert "次の操作: " in err


def test_public_name_runs_selection_check(
    tmp_path: pathlib.Path, env: tuple[pathlib.Path, pathlib.Path], capsys: pytest.CaptureFixture[str]
) -> None:
    """`atk run-script pick-wi-check`の公開名から同じ検証へ到達する。"""
    repo, notes = env
    _awi(notes, "a.md", "`src/model.py`を変える。")
    selection = tmp_path / "selection.yaml"
    selection.write_text(
        yaml.safe_dump({"decisions": [{"awi": "a.md", "lane": "lane-01", "write_files": []}]}), encoding="utf-8"
    )

    code = run_script.dispatch(
        argparse.Namespace(script_name="pick-wi-check", script_args=["--", "--work-dir", str(repo), str(selection)])
    )

    assert code == 1
    assert "a.md: 未被覆: src/model.py" in capsys.readouterr().err
