"""選定結果の`書込対象`がWI本文の反映先パスを覆うかの検証を確かめる。"""

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


def _write_selection(
    path: pathlib.Path, decisions: list[dict[str, typing.Any]], costs: list[dict[str, typing.Any]] | None = None
) -> pathlib.Path:
    """被覆の判定に関係しない必須の欄を補って選定結果を保存し、そのパスを返す。

    旧欄名（`awi`）で書いた項目は旧形式の最上位の欄名と`staleness`で補い、旧形式の読み取りも同じ判定へ通す。
    `costs`を省くと、`なし`でない各レーンの行を補う。
    """
    legacy = any("awi" in decision for decision in decisions)
    for decision in decisions:
        decision.setdefault("staleness" if "awi" in decision else "鮮度", {"status": "current", "later_commit_count": 0})
    if costs is None:
        lanes = {str(decision.get("lane", decision.get("レーン"))) for decision in decisions} - {"なし"}
        costs = [{"レーン": lane} for lane in sorted(lanes)]
    for row in costs:
        row.setdefault("実装秒数", 600)
        row.setdefault("統合秒数", 60)
        if "rationale" not in row:
            row.setdefault("根拠", "検査用の根拠")
    selection = {"decisions" if legacy else "選定": decisions, "レーンの所要時間": costs}
    path.write_text(yaml.safe_dump(selection, allow_unicode=True), encoding="utf-8")
    return path


def _run(tmp_path: pathlib.Path, repo: pathlib.Path, decisions: list[dict[str, typing.Any]]) -> int:
    """選定結果を保存して検証を実行し、終了コードを返す。"""
    selection = _write_selection(tmp_path / "selection.yaml", decisions)
    return check_selection.main(["--work-dir", str(repo), str(selection)])


def _dispatch(*args: str) -> int:
    """`atk run-script pick-wi-check`の公開名から検証を実行し、終了コードを返す。"""
    return run_script.dispatch(argparse.Namespace(script_name="pick-wi-check", script_args=["--", *args]))


def test_reports_uncovered_broad_and_invalid_exclusion(
    tmp_path: pathlib.Path, env: tuple[pathlib.Path, pathlib.Path], capsys: pytest.CaptureFixture[str]
) -> None:
    """未被覆、広すぎる範囲、反映先に無い除外の3区分を、AWIのファイル名とパスを示して非0で返す。"""
    repo, notes = env
    _awi(notes, "a.md", "`src/model.py`と`docs/development/design.md`を変える。")
    _awi(notes, "b.md", "`src/model.py`を変える。")
    _awi(notes, "c.md", "`src/model.py`を変える。`README.md`は変更しない。")
    decisions: list[dict[str, typing.Any]] = [
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
    decisions = [
        {"WI": "a.md", "レーン": "lane-01", "書込対象": ["src/model.py"]},
        {"WI": "c.md", "レーン": "lane-02", "書込対象": ["src/model.py"], "書き込まない反映先": ["LICENSE"]},
        {"WI": "d.md", "レーン": "なし", "書込対象": []},
    ]

    assert _run(tmp_path, repo, decisions) == 1

    err = capsys.readouterr().err
    assert "a.md: 未被覆: docs/development/design.md" in err
    assert "c.md: 未被覆: README.md" in err
    assert "c.md: 書き込まない反映先の不正: LICENSE" in err
    assert "d.md" not in err


def test_accepts_covered_selection_and_skips_out_of_scope_decisions(
    tmp_path: pathlib.Path, env: tuple[pathlib.Path, pathlib.Path], capsys: pytest.CaptureFixture[str]
) -> None:
    """全反映先を覆う選定結果、`lane: なし`、パスを明示しないUWIは違反なしで0を返す。"""
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


def test_public_write_target_covers_reflection_and_can_be_shared_across_lanes(
    tmp_path: pathlib.Path, env: tuple[pathlib.Path, pathlib.Path], capsys: pytest.CaptureFixture[str]
) -> None:
    """公開工程所有の同じpathは反映先を覆うが、別レーンの書込競合には加えない。"""
    repo, notes = env
    _awi(notes, "a.md", "`README.md`と`docs/development/design.md`を変える。")
    _awi(notes, "b.md", "`README.md`と`src/model.py`を変える。")
    decisions = [
        {
            "WI": "a.md",
            "レーン": "lane-01",
            "書込対象": ["docs/development/design.md"],
            "公開工程の書込対象": ["README.md"],
        },
        {
            "WI": "b.md",
            "レーン": "lane-02",
            "書込対象": ["src/model.py"],
            "公開工程の書込対象": ["README.md"],
        },
    ]
    costs = [
        {"レーン": lane, "根拠": "対象リポジトリの規範 AGENTS.md の公開工程の節がREADME.mdを割り当てる"}
        for lane in ("lane-01", "lane-02")
    ]
    selection = _write_selection(tmp_path / "selection.yaml", decisions, costs)

    assert check_selection.main(["--work-dir", str(repo), str(selection)]) == 0, capsys.readouterr().err


@pytest.mark.parametrize(
    "public_value,expected",
    [
        ("README.md", "`公開工程の書込対象`が文字列の列ではない"),
        (["README.md"], "区分間の重複: README.md"),
    ],
)
def test_rejects_invalid_or_overlapping_public_write_target(
    tmp_path: pathlib.Path,
    env: tuple[pathlib.Path, pathlib.Path],
    capsys: pytest.CaptureFixture[str],
    public_value: object,
    expected: str,
) -> None:
    """公開工程欄の型と3区分が重ならないことを、対象WI・欄・path付きで確かめる。"""
    repo, notes = env
    _awi(notes, "a.md", "`README.md`を変える。")
    decisions = [
        {
            "WI": "a.md",
            "レーン": "lane-01",
            "書込対象": ["README.md"],
            "公開工程の書込対象": public_value,
        }
    ]

    expected_exit = 2 if not isinstance(public_value, list) else 1
    assert _run(tmp_path, repo, decisions) == expected_exit
    stderr = capsys.readouterr().err
    assert expected in stderr
    if isinstance(public_value, list):
        assert "区分間で重複するパスは所有する1区分だけへ残す" in stderr


def test_rejects_public_write_target_without_repository_rule_section_rationale(
    tmp_path: pathlib.Path, env: tuple[pathlib.Path, pathlib.Path], capsys: pytest.CaptureFixture[str]
) -> None:
    """公開工程だけの所有を規範と節から確認できない分類を受理しない。"""
    repo, notes = env
    _awi(notes, "a.md", "`README.md`を変える。")
    decisions = [{"WI": "a.md", "レーン": "lane-01", "書込対象": [], "公開工程の書込対象": ["README.md"]}]

    assert _run(tmp_path, repo, decisions) == 1
    stderr = capsys.readouterr().err
    assert "`公開工程の書込対象`の根拠不足: README.md" in stderr
    assert "レーンの所要時間の根拠へ対象リポジトリの規範、節およびpathを記録する" in stderr


@pytest.mark.parametrize(
    ("first_resume", "second_resume"),
    [
        (
            "/home/aki/.claude/plans/05-1200_example.md の実装から再開する",
            "/home/aki/.claude/plans/05-1200_example.md のレビューから再開する",
        ),
        (
            "反映後の観測だけが残る（再開記録: AWI本文、計画: private-notes/plans/2026/10/05-1200_example.md）",
            "反映後の観測だけが残る（再開記録: AWI本文、計画: private-notes/plans/2026/10/05-1200_example.md）",
        ),
        (
            "/home/aki/.claude/plans/05-1200_example.md の実装から再開する",
            "反映後の観測だけが残る（再開記録: AWI本文、計画: private-notes/plans/2026/10/05-1200_example.md）",
        ),
    ],
)
def test_rejects_same_resume_plan_in_different_lanes(
    tmp_path: pathlib.Path,
    env: tuple[pathlib.Path, pathlib.Path],
    capsys: pytest.CaptureFixture[str],
    first_resume: str,
    second_resume: str,
) -> None:
    """通常中断と観測のみのどちらでも、同じ再開計画を複数レーンへ割り当てた選定を拒否する。"""
    repo, notes = env
    for name in ("a.md", "b.md"):
        _awi(notes, name, "反映先のパスを持たない説明。")
    decisions = [
        {"WI": "a.md", "レーン": "lane-01", "書込対象": [], "再開位置": first_resume},
        {"WI": "b.md", "レーン": "lane-02", "書込対象": [], "再開位置": second_resume},
    ]

    assert _run(tmp_path, repo, decisions) == 1
    error = capsys.readouterr().err
    assert "同じ再開計画を別レーンへ割り当てている" in error
    assert "a.md（lane-01）とb.md（lane-02）" in error


@pytest.mark.parametrize(
    ("first_resume", "second_resume"),
    [
        ("/home/aki/.claude/plans/05-1200_example.md の実装", "/home/aki/.claude/plans/05-1200_example.md のレビュー"),
        ("なし", "なし"),
        ("反映後の観測だけが残る（再開記録: AWI本文、計画: 計画なし）", "なし"),
        ("/home/aki/.claude/plans/05-1200_a.md の実装", "/home/aki/.claude/plans/05-1201_b.md の実装"),
    ],
)
def test_accepts_resume_plan_boundaries(
    tmp_path: pathlib.Path,
    env: tuple[pathlib.Path, pathlib.Path],
    capsys: pytest.CaptureFixture[str],
    first_resume: str,
    second_resume: str,
) -> None:
    """同じ計画の同一レーン、計画なし、再開なし、異なる計画は計画所有の条件で拒否しない。"""
    repo, notes = env
    for name in ("a.md", "b.md"):
        _awi(notes, name, "反映先のパスを持たない説明。")
    same_plan = first_resume.endswith(" の実装") and second_resume.endswith(" のレビュー")
    decisions: list[dict[str, typing.Any]] = [
        {"WI": "a.md", "レーン": "lane-01", "書込対象": [], "再開位置": first_resume},
        {"WI": "b.md", "レーン": "lane-01" if same_plan else "lane-02", "書込対象": [], "再開位置": second_resume},
    ]

    assert _run(tmp_path, repo, decisions) == 0, capsys.readouterr().err


def test_uwi_paths_in_answer_and_materials_require_classification(
    tmp_path: pathlib.Path, env: tuple[pathlib.Path, pathlib.Path], capsys: pytest.CaptureFixture[str]
) -> None:
    """UWIの判断材料と回答に明示されたパスは、書込対象か書き込まない反映先のどちらかへ分類しないと違反になる。

    UWIは`## 反映内容と反映先`を持たないため、同節だけからパスを抽出すると回答で求められた変更が空集合として合格し、
    レーン分けへ書込対象の欠けた選定が渡る。
    """
    repo, notes = env
    body = (
        "---\ntype: uwi\nsource: process-wi\n---\n\n## 質問\n\nこの対応で問題無いか？\n\n"
        "## 判断材料\n\n`docs/development/design.md`の記述に従って対応した。\n\n"
        "## 回答\n\n<!-- ユーザーはこの行以降に回答を追記する -->\n`src/model.py`の判定も直して。\n"
    )
    (notes / "processing" / "u.md").write_text(body, encoding="utf-8")

    assert _run(tmp_path, repo, [{"WI": "u.md", "レーン": "lane-01", "書込対象": []}]) == 1
    err = capsys.readouterr().err
    assert "u.md: 未被覆: src/model.py" in err
    assert "u.md: 未被覆: docs/development/design.md" in err

    classified = [
        {"WI": "u.md", "レーン": "lane-01", "書込対象": ["src/model.py"], "書き込まない反映先": ["docs/development/design.md"]}
    ]
    assert _run(tmp_path, repo, classified) == 0
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


def test_reports_missing_body_as_violation(
    tmp_path: pathlib.Path, env: tuple[pathlib.Path, pathlib.Path], capsys: pytest.CaptureFixture[str]
) -> None:
    """private-notesに本文が無い項目は、入力の解決失敗ではなく項目の違反として返す。"""
    repo, _notes = env

    assert _run(tmp_path, repo, [{"awi": "absent.md", "lane": "lane-01", "write_files": []}]) == 1
    assert "absent.md: 本文を特定できない" in capsys.readouterr().err


def test_selection_values_with_yaml_syntax_characters_round_trip(
    tmp_path: pathlib.Path, env: tuple[pathlib.Path, pathlib.Path], capsys: pytest.CaptureFixture[str]
) -> None:
    """書式例どおり単一引用符で囲んだ値は、コロン・括弧・引用符・改行を含んでも同じ構造で読める。

    pickerの保存直後とメインの受領時は同じ公開コマンドで同じ欄と型を受理するため、
    ここで受理した選定結果は双方で同じ値として読まれる。
    """
    repo, notes = env
    _awi(notes, "a.md", "`src/model.py`を変える。")
    selection = tmp_path / "selection.yaml"
    selection.write_text(
        "選定:\n"
        "- WI: 'a.md'\n"
        "  レーン: 'lane-01'\n"
        "  鮮度: {status: current, later_commit_count: 0}\n"
        "  書込対象: ['src/model.py']\n"
        "  担当モデル: {実装担当: 'claude:opus[1m]/medium'}\n"
        "  プロジェクト規範の指定: '観点群: 実装漏れと横展開 # 見出し, [括弧] {波括弧} と ''引用符'' の値'\n"
        "レーンの所要時間:\n"
        "- レーン: 'lane-01'\n"
        "  実装秒数: 600\n"
        "  統合秒数: 60\n"
        "  根拠: '同一レーン案: 3000秒\n"
        "\n"
        "    分割案: 2000秒'\n"
        "続行できない理由:\n"
        "- 'なし'\n",
        encoding="utf-8",
    )

    assert _dispatch("--work-dir", str(repo), str(selection)) == 0, capsys.readouterr().err
    loaded = check_selection.load_selection(selection)
    decision = typing.cast(list[dict[str, object]], loaded["選定"])[0]
    assert decision["担当モデル"] == {"実装担当": "claude:opus[1m]/medium"}
    assert decision["プロジェクト規範の指定"] == "観点群: 実装漏れと横展開 # 見出し, [括弧] {波括弧} と '引用符' の値"
    assert typing.cast(list[dict[str, object]], loaded["レーンの所要時間"])[0]["根拠"] == "同一レーン案: 3000秒\n分割案: 2000秒"


_FIX_SELECTION = "同じコマンドを再実行する"
_FIX_ARGUMENTS = "`--work-dir`へ対象リポジトリの絶対パスを渡して再実行する"


@pytest.mark.parametrize(
    ("text", "reason", "action"),
    [
        pytest.param(
            "選定:\n- WI: a.md\n  レーン: lane-01\n  鮮度: {status: current}\n  書込対象: [src/model.py]\n"
            "レーンの所要時間:\n- レーン: lane-01\n  実装秒数: 1\n  統合秒数: 1\n  根拠: 観点群: 実装漏れと横展開\n",
            "YAML構文が不正",
            "単一引用符で囲み",
            id="yaml-syntax",
        ),
        pytest.param(
            "選定:\n- WI: 'a.md'\n  レーン: 'lane-01'\n  鮮度: {status: current}\n  書込対象: ['src/model.py']\n"
            "  担当モデル: {実装担当: 'agents_server:claude:opus[1m]/medium'}\n"
            "レーンの所要時間:\n- レーン: 'lane-01'\n  実装秒数: 1\n  統合秒数: 1\n  根拠: '根拠'\n",
            "agents_server:claude:opus[1m]/medium",
            "`<claude|codex|agy>:<model>/<effort>`の値へ直す",
            id="model-type",
        ),
        pytest.param(
            "選定:\n- WI: 'a.md'\n  レーン: 'lane-01'\n  鮮度: {status: current}\n  書込対象: ['src/model.py']\n"
            "  担当モデル: {計画担当: 'claude:opus/high'}\n"
            "レーンの所要時間:\n- レーン: 'lane-01'\n  実装秒数: 1\n  統合秒数: 1\n  根拠: '根拠'\n",
            "計画担当",
            "`<claude|codex|agy>:<model>/<effort>`の値へ直す",
            id="unknown-model-role",
        ),
        pytest.param(
            "選定:\n- WI: 'a.md'\n  レーン: 'lane-01'\n  鮮度: {status: current}\n  書込対象: ['src/model.py']\n"
            "  書込対象の候補: ['src/']\n"
            "レーンの所要時間:\n- レーン: 'lane-01'\n  実装秒数: 1\n  統合秒数: 1\n  根拠: '根拠'\n",
            "a.md: 未知の欄: 書込対象の候補",
            "「出力」の欄名と型へ直して",
            id="unknown-key",
        ),
        pytest.param(
            "選定:\n- WI: 'a.md'\n  レーン: 'lane-01'\n  鮮度: {status: current}\n  書込対象: 'src/model.py'\n"
            "レーンの所要時間:\n- レーン: 'lane-01'\n  実装秒数: '1'\n  統合秒数: 1\n  根拠: '根拠'\n",
            "a.md: `書込対象`が文字列の列ではない",
            "「出力」の欄名と型へ直して",
            id="field-type",
        ),
        pytest.param(
            "選定:\n- WI: 'a.md'\n  レーン: 'lane-01'\n  書込対象: ['src/model.py']\n",
            "a.md: 必須の欄がない: 鮮度",
            "「出力」の欄名と型へ直して",
            id="missing-field",
        ),
    ],
)
def test_content_errors_guide_selection_fix(
    text: str,
    reason: str,
    action: str,
    tmp_path: pathlib.Path,
    env: tuple[pathlib.Path, pathlib.Path],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """引数が正しく選定結果の内容が誤る場合は、終了コード2と、引数ではなく選定結果を直す次の操作を返す。"""
    repo, notes = env
    _awi(notes, "a.md", "`src/model.py`を変える。")
    selection = tmp_path / "selection.yaml"
    selection.write_text(text, encoding="utf-8")

    assert _dispatch("--work-dir", str(repo), str(selection)) == 2

    err = capsys.readouterr().err
    assert reason in err
    next_action = next(line for line in err.splitlines() if line.startswith("次の操作: "))
    assert action in next_action
    assert _FIX_SELECTION in next_action
    assert _FIX_ARGUMENTS not in next_action
    assert "Traceback" not in err


def test_field_type_errors_are_reported_together(
    tmp_path: pathlib.Path, env: tuple[pathlib.Path, pathlib.Path], capsys: pytest.CaptureFixture[str]
) -> None:
    """1回の実行で全ての内容の誤りを返し、直すたびに次の誤りが現れる往復を生じさせない。"""
    repo, _notes = env
    selection = tmp_path / "selection.yaml"
    selection.write_text(
        "選定:\n- WI: 'a.md'\n  レーン: 'lane-01'\n  鮮度: {status: current}\n  書込対象: 'src/model.py'\n"
        "  担当モデル: {実装担当: 'claude:opus'}\n"
        "レーンの所要時間:\n- レーン: 'lane-01'\n  段階: 0\n  実装秒数: -1\n  統合秒数: 1\n  根拠: ''\n",
        encoding="utf-8",
    )

    assert _dispatch("--work-dir", str(repo), str(selection)) == 2

    err = capsys.readouterr().err
    for reason in (
        "a.md: `担当モデル`が担当別のengine:model/effortではない",
        "a.md: `書込対象`が文字列の列ではない",
        "lane-01: `段階`が1以上の整数ではない",
        "lane-01: `実装秒数`が0以上の数値ではない",
        "lane-01: `根拠`が空でない文字列ではない",
    ):
        assert reason in err


def test_path_errors_guide_argument_fix(
    tmp_path: pathlib.Path,
    env: tuple[pathlib.Path, pathlib.Path],
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """選定結果のファイル、`--work-dir`、private-notesを解決できない場合は、終了コード2とその場所を直す次の操作を返す。"""
    repo, notes = env
    _awi(notes, "a.md", "`src/model.py`を変える。")
    selection = _write_selection(
        tmp_path / "selection.yaml", [{"WI": "a.md", "レーン": "lane-01", "書込対象": ["src/model.py"]}]
    )
    missing_root = tmp_path / "missing-notes"

    def next_action(*args: str) -> str:
        assert _dispatch(*args) == 2
        err = capsys.readouterr().err
        assert "Traceback" not in err
        return next(line for line in err.splitlines() if line.startswith("次の操作: "))

    for args in (
        ("--work-dir", str(repo), str(tmp_path / "absent.yaml")),
        ("--work-dir", str(tmp_path / "absent-repo"), str(selection)),
    ):
        action = next_action(*args)
        assert _FIX_ARGUMENTS in action
        assert "pick-wi.subagent.md" not in action

    monkeypatch.setenv("AGENT_TOOLKIT_PRIVATE_NOTES", str(missing_root))
    action = next_action("--work-dir", str(repo), str(selection))
    assert "`atk config get private_notes`" in action
    assert _FIX_ARGUMENTS not in action


def test_public_name_runs_selection_check(
    tmp_path: pathlib.Path, env: tuple[pathlib.Path, pathlib.Path], capsys: pytest.CaptureFixture[str]
) -> None:
    """`atk run-script pick-wi-check`の公開名から同じ検証へ到達する。"""
    repo, notes = env
    _awi(notes, "a.md", "`src/model.py`を変える。")
    selection = _write_selection(tmp_path / "selection.yaml", [{"awi": "a.md", "lane": "lane-01", "write_files": []}])

    assert _dispatch("--work-dir", str(repo), str(selection)) == 1
    assert "a.md: 未被覆: src/model.py" in capsys.readouterr().err


def test_public_check_rejects_missing_stage(
    tmp_path: pathlib.Path, env: tuple[pathlib.Path, pathlib.Path], capsys: pytest.CaptureFixture[str]
) -> None:
    """公開名から、先行レーンが正しくても段階2の欠番を拒否する。"""
    repo, notes = env
    _awi(notes, "a.md", "`src/model.py`を変える。")
    _awi(notes, "b.md", "`docs/development/design.md`を変える。")
    selection = _write_selection(
        tmp_path / "selection.yaml",
        [
            {"WI": "a.md", "レーン": "lane-01", "書込対象": ["src/model.py"]},
            {"WI": "b.md", "レーン": "lane-02", "書込対象": ["docs/development/design.md"]},
        ],
        [{"レーン": "lane-01", "段階": 1}, {"レーン": "lane-02", "段階": 3, "先行レーン": ["lane-01"]}],
    )

    assert _dispatch("--work-dir", str(repo), str(selection)) == 1
    assert "段階は1から連続する正整数を指定する: 段階2がない" in capsys.readouterr().err


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
    selection = _write_selection(tmp_path / "selection.yaml", [{"WI": "short.md", "レーン": "lane-01", "書込対象": []}])

    assert _dispatch("--work-dir", str(repo), str(selection)) == 1
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

    _write_selection(
        selection,
        [
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
        ],
    )
    assert _dispatch("--work-dir", str(repo), str(selection)) == 0
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
    staleness_key, seconds = ("staleness", "implementation_seconds") if legacy else ("鮮度", "実装秒数")
    selection = tmp_path / "selection.yaml"
    selection.write_text(
        yaml.safe_dump(
            {
                decisions_key: [
                    {wi_key: "a.md", lane_key: "lane-01", files_key: [paths[0]], staleness_key: {"status": "current"}},
                    {
                        wi_key: "b.md",
                        lane_key: "lane-01" if same_lane else "lane-02",
                        files_key: [paths[1]],
                        staleness_key: {"status": "current"},
                    },
                ],
                costs_key: [
                    {lane_key: lane, rationale_key: rationale, seconds: 600, "統合秒数": 60} for lane in ("lane-01", "lane-02")
                ],
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )

    assert _dispatch("--work-dir", str(repo), str(selection)) == expected

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
    selection = _write_selection(
        tmp_path / "selection.yaml",
        [
            {"WI": "a.md", "レーン": "lane-01", "書込対象": ["src/model.py"]},
            {"WI": "b.md", "レーン": "lane-02", "書込対象": ["src/model.py"]},
        ],
        [
            {"レーン": lane, "根拠": "別対象" if lane == missing_lane else "src/model.pyの異なる定義"}
            for lane in ("lane-01", "lane-02")
        ],
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


def test_public_check_model_and_stage_selection_contract(
    tmp_path: pathlib.Path, env: tuple[pathlib.Path, pathlib.Path], capsys: pytest.CaptureFixture[str]
) -> None:
    """担当別モデルと後段の先行統合条件を、公開コマンドから同じ選定で確かめる。"""
    repo, notes = env
    for name in ("a.md", "b.md"):
        _awi(notes, name, "`src/model.py`を書き込む。")

    def run(second_lane: str, second_models: dict[str, str], prior: list[str]) -> tuple[int, str]:
        selection = _write_selection(
            tmp_path / "selection.yaml",
            [
                {
                    "WI": "a.md",
                    "レーン": "lane-01",
                    "書込対象": ["src/model.py"],
                    "担当モデル": {"実装担当": "claude:opus/high"},
                },
                {"WI": "b.md", "レーン": second_lane, "書込対象": ["src/model.py"], "担当モデル": second_models},
            ],
            [
                {"レーン": "lane-01", "段階": 1, "根拠": "先行"},
                {"レーン": "lane-02", "段階": 2, "先行レーン": prior, "根拠": "後段"},
            ],
        )
        code = _dispatch("--work-dir", str(repo), str(selection))
        return code, capsys.readouterr().err

    assert run("lane-02", {"実行レビュー担当": "codex:gpt-6-sol/medium"}, ["lane-01"]) == (0, "")

    code, err = run("lane-02", {"実行レビュー担当": "codex:gpt-6-sol/medium"}, [])
    assert code == 1
    assert "lane-02: 後段には先行レーンを指定する" in err
    assert "重複パスの根拠不足" in err

    code, err = run("lane-01", {"実装担当": "codex:gpt-6-sol/medium"}, ["lane-01"])
    assert code == 1
    assert "lane-01: 実装担当のモデル指定が衝突" in err
