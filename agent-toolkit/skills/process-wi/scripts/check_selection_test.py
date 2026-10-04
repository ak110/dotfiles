"""選定結果の`書込対象`がAWI本文の反映先パスを覆うかの検証を確かめる。"""

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
    """対象リポジトリとprivate-notesを`tmp_path`配下へ作成し、そのパスを返す。"""
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
    assert "c.md: 書き込まない反映先の不正: LICENSE" in err
    assert "c.md: 書き込まない反映先の不正: README.md" not in err
    assert "次の操作: " in err


def test_reads_current_field_names_like_legacy_ones(
    tmp_path: pathlib.Path, env: tuple[pathlib.Path, pathlib.Path], capsys: pytest.CaptureFixture[str]
) -> None:
    """日本語の欄名で書いた選定結果からも、旧欄名の選定結果と同じ違反を報告する。"""
    repo, notes = env
    _awi(notes, "a.md", "`src/model.py`と`docs/development/design.md`を変える。")
    _awi(notes, "c.md", "`src/model.py`を変える。`README.md`は変更しない。")
    _awi(notes, "d.md", "`docs/development/design.md`を変える。")
    selection = tmp_path / "selection.yaml"
    selection.write_text(
        yaml.safe_dump(
            {
                "選定": [
                    {"WI": "a.md", "レーン": "lane-01", "書込対象": ["src/model.py"]},
                    {"WI": "c.md", "レーン": "lane-02", "書込対象": ["src/model.py"], "書き込まない反映先": ["LICENSE"]},
                    {"WI": "d.md", "レーン": "なし", "書込対象": []},
                ]
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )

    assert check_selection.main(["--work-dir", str(repo), str(selection)]) == 1

    err = capsys.readouterr().err
    assert "a.md: 未被覆: docs/development/design.md" in err
    assert "c.md: 未被覆: README.md" in err
    assert "c.md: 書き込まない反映先の不正: LICENSE" in err
    assert "d.md" not in err


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
    """本文を特定できないdecisionは違反として、private-notesの不在と読めない選定結果は入力エラーとして返す。"""
    repo, _notes = env

    assert _run(tmp_path, repo, [{"awi": "absent.md", "lane": "lane-01", "write_files": []}]) == 1
    assert "absent.md: 本文を特定できない" in capsys.readouterr().err

    missing_root = tmp_path / "missing-notes"
    monkeypatch.setenv("AGENT_TOOLKIT_PRIVATE_NOTES", str(missing_root))
    assert _run(tmp_path, repo, [{"awi": "a.md", "lane": "lane-01", "write_files": []}]) == 2
    err = capsys.readouterr().err
    assert f"private-notesが実在しない: {missing_root}" in err
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


def test_public_check_resolves_abbreviated_paths_and_classification(
    tmp_path: pathlib.Path, env: tuple[pathlib.Path, pathlib.Path], capsys: pytest.CaptureFixture[str]
) -> None:
    """文章・インラインコードのディレクトリ省略表記を公開コマンドの被覆判定へ渡す。"""
    repo, notes = env
    fresh_path = "agent-toolkit/agent_toolkit/_agents_server/" + "fresh.py"
    _awi(
        notes,
        "short.md",
        "src/のmodel.py・new_module.pyと、`docs/development/design.md`・`new.md`を変更する。"
        "次に`agent-toolkit/agent_toolkit/_agents_server/status_file.py`と`fresh.py`を変更する。",
    )
    selection = tmp_path / "selection.yaml"
    selection.write_text(
        yaml.safe_dump({"選定": [{"WI": "short.md", "レーン": "lane-01", "書込対象": []}]}, allow_unicode=True),
        encoding="utf-8",
    )

    args = argparse.Namespace(script_name="pick-wi-check", script_args=["--", "--work-dir", str(repo), str(selection)])
    assert run_script.dispatch(args) == 1
    err = capsys.readouterr().err
    for path in (
        "src/model.py",
        "src/new_module.py",
        "docs/development/design.md",
        "docs/development/new.md",
        "agent-toolkit/agent_toolkit/_agents_server/status_file.py",
        fresh_path,
    ):
        assert f"short.md: 未被覆: {path}" in err

    selection.write_text(
        yaml.safe_dump(
            {
                "選定": [
                    {
                        "WI": "short.md",
                        "レーン": "lane-01",
                        "書込対象": [
                            "src/",
                            "docs/development/design.md",
                            "docs/development/new.md",
                            "agent-toolkit/agent_toolkit/_agents_server/status_file.py",
                            fresh_path,
                        ],
                    }
                ]
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    assert run_script.dispatch(args) == 0
    assert capsys.readouterr().err == ""


def test_abbreviated_paths_require_directory_context_and_existing_parent(
    tmp_path: pathlib.Path, env: tuple[pathlib.Path, pathlib.Path], capsys: pytest.CaptureFixture[str]
) -> None:
    """孤立した名前、コマンド、存在しない親を反映先として誤診断しない。"""
    repo, notes = env
    _awi(notes, "isolated.md", "model.pyと`atk wi add`、https://example.com/x.py、missing/dir/x.py・other.pyは対象外。")

    assert _run(tmp_path, repo, [{"awi": "isolated.md", "lane": "lane-01", "write_files": []}]) == 0
    assert capsys.readouterr().err == ""


@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize(
    ("paths", "same_lane", "rationale", "expected"),
    [
        (("src/model.py", "src/model.py"), False, "src/model.pyの異なる定義", 0),
        (("src/model.py", "src/model.py"), False, "別の対象", 1),
        (("src/", "src/model.py"), False, "src/model.pyの異なる定義", 0),
        (("src/", "src/model.py"), False, "src/の異なる定義", 1),
        (("src/", "src/models/"), False, "src/models/の異なる定義", 0),
        (("src/", "src/models/"), False, "src/models-old/の定義", 1),
        (("src/", "src-old/model.py"), False, "別対象", 0),
        (("src/model.py", "src/model.py"), True, "同じレーンで直列化", 0),
    ],
)
def test_public_check_requires_shared_path_in_both_lane_rationales(
    legacy: bool,
    paths: tuple[str, str],
    same_lane: bool,
    rationale: str,
    expected: int,
    tmp_path: pathlib.Path,
    env: tuple[pathlib.Path, pathlib.Path],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """公開名から新旧入力のパス包含と双方の根拠を確かめ、似た名前と同じレーンを区別する。"""
    repo, notes = env
    for name, path in zip(("a.md", "b.md"), paths, strict=True):
        _awi(notes, name, f"`{path}`を書き込む。")
    decisions_key, wi_key, lane_key, files_key, costs_key, rationale_key = (
        ("decisions", "awi", "lane", "write_files", "lane_costs", "rationale")
        if legacy
        else ("選定", "WI", "レーン", "書込対象", "レーンの所要時間", "根拠")
    )
    selection = tmp_path / "selection.yaml"
    selection.write_text(
        yaml.safe_dump(
            {
                decisions_key: [
                    {wi_key: "a.md", lane_key: "lane-01", files_key: [paths[0]]},
                    {wi_key: "b.md", lane_key: "lane-01" if same_lane else "lane-02", files_key: [paths[1]]},
                ],
                costs_key: [
                    {lane_key: "lane-01", rationale_key: rationale},
                    {lane_key: "lane-02", rationale_key: rationale},
                ],
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )

    assert (
        run_script.dispatch(
            argparse.Namespace(
                script_name="pick-wi-check",
                script_args=["--", "--work-dir", str(repo), str(selection)],
            )
        )
        == expected
    )

    err = capsys.readouterr().err
    if expected:
        assert "a.md（lane-01）とb.md（lane-02）" in err
        assert "重複パスの根拠不足" in err
        assert paths[1] in err
        assert "次の操作:" in err
        assert "同じレーンへまとめる" in err
    else:
        assert not err


@pytest.mark.parametrize("missing_lane", ["lane-01", "lane-02"])
def test_overlap_rejects_rationale_missing_on_either_side(
    missing_lane: str,
    tmp_path: pathlib.Path,
    env: tuple[pathlib.Path, pathlib.Path],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """片方の根拠だけが共通パスを持つ場合も、根拠が不足するレーンを示す。"""
    repo, notes = env
    for name in ("a.md", "b.md"):
        _awi(notes, name, "`src/model.py`を書き込む。")
    selection = tmp_path / "selection.yaml"
    selection.write_text(
        yaml.safe_dump(
            {
                "選定": [
                    {"WI": "a.md", "レーン": "lane-01", "書込対象": ["src/model.py"]},
                    {"WI": "b.md", "レーン": "lane-02", "書込対象": ["src/model.py"]},
                ],
                "レーンの所要時間": [
                    {"レーン": lane, "根拠": "別対象" if lane == missing_lane else "src/model.pyの異なる定義"}
                    for lane in ("lane-01", "lane-02")
                ],
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )

    assert check_selection.main(["--work-dir", str(repo), str(selection)]) == 1
    assert f"src/model.py（{missing_lane}）" in capsys.readouterr().err


def test_public_check_accepts_zero_candidate_selection(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """候補0件の選定結果は`選定: []`だけで受理し、通常のWI集合を別の欄で重ねて求めない。

    候補0件のprocess-wiの1回の実行はアラート監査だけを行うため、受領側が選定結果を拒否すると監査へ進めない。
    """
    notes = tmp_path / "notes"
    (notes / "processing").mkdir(parents=True)
    path = tmp_path / "selection.yaml"
    path.write_text(yaml.safe_dump({"選定": [], "レーンの所要時間": []}, allow_unicode=True), encoding="utf-8")
    monkeypatch.setattr(check_selection._plan_file, "private_notes_root", lambda: notes)  # pylint: disable=protected-access
    assert check_selection.main([str(path), "--work-dir", str(tmp_path)]) == 0, capsys.readouterr().err


@pytest.mark.parametrize("field", [None, "先行レーン", "after_lanes"])
def test_public_check_rejects_removed_after_lanes_field(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], field: str | None
) -> None:
    """`レーンの所要時間`の撤去した欄（旧欄名を含む）を受理せず、欄の無い選定結果は受理する。

    候補内の依存は同じレーンで依存先から処理するため、別レーンの統合完了を待つ指定は受領側で使えない。
    """
    notes = tmp_path / "notes"
    (notes / "processing").mkdir(parents=True)
    cost: dict[str, object] = {"レーン": "lane-01", "実装秒数": 60, "統合秒数": 30, "根拠": "1件だけのレーン"}
    if field is not None:
        cost[field] = []
    path = tmp_path / "selection.yaml"
    path.write_text(yaml.safe_dump({"選定": [], "レーンの所要時間": [cost]}, allow_unicode=True), encoding="utf-8")
    monkeypatch.setattr(check_selection._plan_file, "private_notes_root", lambda: notes)  # pylint: disable=protected-access
    assert check_selection.main([str(path), "--work-dir", str(tmp_path)]) == (0 if field is None else 1)
    error = capsys.readouterr().err
    if field is not None:
        assert f"撤去した欄`{field}`" in error and "lane-01" in error
