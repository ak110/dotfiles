# pylint: disable=useless-import-alias  # pytestのfixtureを試験モジュールの名前空間へ登録するため。
"""選定の書込区分と本文検査範囲を検証する。"""

import pathlib
import typing

import check_selection
import pytest
import yaml

from agent_toolkit._testing.selection_check_support import _awi, _dispatch, _run, _write_selection
from agent_toolkit._testing.selection_check_support import fixture_env as fixture_env


def test_public_check_limits_body_scope(
    tmp_path: pathlib.Path, env: tuple[pathlib.Path, pathlib.Path], capsys: pytest.CaptureFixture[str]
) -> None:
    """指定外の本文を取得せず、追加した処理対象WIだけの違反と成功要約、省略時の全件検査を保つ。"""
    repo, notes = env
    _awi(notes, "new.md", "`src/model.py`を変更する。")
    _awi(notes, "second.md", "`README.md`を変更する。")
    decisions: list[dict[str, typing.Any]] = [
        {"WI": "old.md", "レーン": "lane-01", "書込対象": []},
        {"WI": "new.md", "レーン": "lane-01", "書込対象": ["src/model.py"]},
        {"WI": "second.md", "レーン": "lane-01", "書込対象": ["../README.md"]},
        {"WI": "none.md", "レーン": "なし", "書込対象": []},
    ]
    path = _write_selection(tmp_path / "selection.yaml", decisions)
    args = (str(path), "--work-dir", str(repo))
    scope = ("--body-wi", "new.md", "--body-wi", "second.md", "--body-wi", "new.md", "--body-wi", "none.md")
    assert _dispatch(*args, *scope) == 1
    result = capsys.readouterr()
    assert "second.md: `書込対象`のパスの不正: ../README.md" in result.err
    assert "old.md:" not in result.err
    assert "不正な要素" in result.err
    decisions[2]["書込対象"] = ["README.md"]
    _write_selection(path, decisions)
    assert _dispatch(*args, *scope) == 0
    result = capsys.readouterr()
    assert result.err == ""
    assert '本文検査対象: ["new.md", "second.md"]' in result.out
    assert "分類と配分の意味はWIの元本文" in result.out
    assert _dispatch(*args) == 1
    assert "old.md: 本文を特定できない" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("violation", "expected", "code"),
    [
        ("overlap", "重複パスの記録不足", 1),
        ("resume", "同じ再開計画を別レーン", 1),
        ("stage", "後段に先行レーンがない", 1),
        ("public", "`公開工程の書込対象`の根拠不足", 1),
        ("structure", "未知の欄", 2),
        ("model-type", "担当別のengine:model/effort", 2),
        ("model-conflict", "実装担当のモデル指定が衝突", 1),
    ],
)
def test_public_check_keeps_global_checks(
    tmp_path: pathlib.Path,
    env: tuple[pathlib.Path, pathlib.Path],
    capsys: pytest.CaptureFixture[str],
    violation: str,
    expected: str,
    code: int,
) -> None:
    """本文の指定外にある不正でも、選定全体の関係と入力構造の違反を見落とさない。"""
    repo, notes = env
    _awi(notes, "new.md", "`README.md`を変更する。")
    old: dict[str, typing.Any] = {"WI": "old.md", "レーン": "lane-01", "書込対象": ["src/model.py"]}
    other: dict[str, typing.Any] = {"WI": "other.md", "レーン": "lane-02", "書込対象": []}
    new = {"WI": "new.md", "レーン": "lane-03", "書込対象": ["README.md"]}
    costs: list[dict[str, typing.Any]] = [{"レーン": f"lane-0{number}"} for number in range(1, 4)]
    if violation == "overlap":
        other["書込対象"] = ["src/model.py"]
    elif violation == "resume":
        old["再開位置"] = other["再開位置"] = "/plans/shared.md"
    elif violation == "stage":
        costs[0]["段階"] = 2
    elif violation == "public":
        old["公開工程の書込対象"] = ["README.md"]
        old["公開工程の規範根拠"] = []
    elif violation == "structure":
        old["unknown"] = True
    elif violation == "model-type":
        old["担当モデル"] = {"実装担当": "invalid"}
    else:
        other["レーン"] = "lane-01"
        costs.pop(1)
        old["担当モデル"] = {"実装担当": "codex:first/high"}
        other["担当モデル"] = {"実装担当": "codex:second/high"}
    path = _write_selection(tmp_path / "selection.yaml", [old, other, new], costs)
    assert _dispatch(str(path), "--work-dir", str(repo), "--body-wi", "new.md") == code
    result = capsys.readouterr()
    assert expected in result.err
    assert "本文を特定できない" not in result.err
    assert result.out == ""


@pytest.mark.parametrize("violation", ["classification", "norm-spec"])
def test_public_check_applies_body_rules_only_to_selected_wis(
    tmp_path: pathlib.Path,
    env: tuple[pathlib.Path, pathlib.Path],
    capsys: pytest.CaptureFixture[str],
    violation: str,
) -> None:
    """本文由来の書込予定・区分・規範指定の違反を持つ同じ項目を、指定ありと指定なしで比較する。"""
    repo, notes = env
    _awi(notes, "old.md", "`src/model.py`を変更する。")
    _awi(notes, "new.md", "`README.md`を変更する。")
    old: dict[str, typing.Any] = {"WI": "old.md", "レーン": "lane-01", "書込対象": ["src/model.py"]}
    if violation == "classification":
        old["書き込まない反映先"] = ["src/model.py"]
        expected = "区分間の重複"
    else:
        (repo / "pyproject.toml").write_text(
            '[[tool.agent-toolkit.pick-wi-check.norm-spec]]\nname = "仕様"\npaths = ["src/"]\nrequire-text = ["設計を読む"]\n',
            encoding="utf-8",
        )
        expected = "プロジェクト規範の指定の不足"
    path = _write_selection(
        tmp_path / "selection.yaml",
        [old, {"WI": "new.md", "レーン": "lane-01", "書込対象": ["README.md"]}],
    )
    args = (str(path), "--work-dir", str(repo))
    assert _dispatch(*args, "--body-wi", "new.md") == 0
    assert capsys.readouterr().err == ""
    assert _dispatch(*args, "--body-wi", "old.md") == 1
    assert f"old.md: {expected}" in capsys.readouterr().err
    assert _dispatch(*args) == 1
    assert f"old.md: {expected}" in capsys.readouterr().err


def test_public_check_rejects_unknown_body_wi(
    tmp_path: pathlib.Path, env: tuple[pathlib.Path, pathlib.Path], capsys: pytest.CaptureFixture[str]
) -> None:
    """集合の指定誤りは本文違反とは区別して終了コード2と未知のWI名を返す。"""
    repo, _notes = env
    path = _write_selection(tmp_path / "selection.yaml", [])
    assert _dispatch(str(path), "--work-dir", str(repo), "--body-wi", "missing.md") == 2
    result = capsys.readouterr()
    assert "`--body-wi`に選定結果に無いWIがある: missing.md" in result.err
    assert result.out == ""


def test_reports_uncovered_broad_and_invalid_exclusion(
    tmp_path: pathlib.Path, env: tuple[pathlib.Path, pathlib.Path], capsys: pytest.CaptureFixture[str]
) -> None:
    """過大な範囲と記載済み除外の不正を診断し、非書込の未列挙は拒否しない。"""
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
    decisions: list[dict[str, typing.Any]] = [
        {"awi": "a.md", "lane": "lane-01", "write_files": ["src/"]},
        {"awi": "b.md", "lane": "なし", "write_files": []},
        {"awi": "u.md", "lane": "lane-01", "write_files": ["src/"]},
    ]

    assert _run(tmp_path, repo, decisions) == 0
    assert capsys.readouterr().err == ""


@pytest.mark.parametrize("legacy", [False, True])
def test_legacy_write_scope_reasons_are_dropped(
    env: tuple[pathlib.Path, pathlib.Path], tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], legacy: bool
) -> None:
    """旧版が保存した`書込範囲の理由`は読取時に破棄し、記録の過不足を判定せず、保存時にも出力しない。

    同欄は重なりの判定と配分に寄与しないため撤去した。新しく書く追加入力の同欄は未知の欄として拒否する。
    """
    repo, notes = env
    _awi(notes, "a.md", "`README.md`を変える。")
    keys = ("awi", "lane", "write_files") if legacy else ("WI", "レーン", "書込対象")
    item = {keys[0]: "a.md", keys[1]: "lane-01", keys[2]: ["README.md", "src/"]}
    path = _write_selection(tmp_path / "selection.yaml", [item])
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    items = data["decisions" if legacy else "選定"]
    # 旧版では必須パスと記録の不一致（余分な記録と空の理由）で拒否された記録を置く。
    items[0]["書込範囲の理由"] = [{"パス": "README.md", "理由": " "}, {"パス": "README.md", "理由": "重複"}]
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    assert _dispatch(str(path), "--work-dir", str(repo)) == 0
    assert "書込範囲の理由" not in capsys.readouterr().err
    assert _dispatch(str(path), "--mark-integrated", "lane-01", "--output", str(path), "--work-dir", str(repo)) == 0
    assert "書込範囲の理由" not in path.read_text(encoding="utf-8")
    addition = tmp_path / "extend.yaml"
    addition.write_text(
        yaml.safe_dump(
            {"選定": [{"WI": "a.md", "書込対象": ["README.md"], "書込範囲の理由": [{"パス": "README.md", "理由": "x"}]}]},
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    before = path.read_bytes()
    assert _dispatch(str(path), "--work-dir", str(repo), "--extend-write-targets", str(addition), "--output", str(path)) == 2
    assert path.read_bytes() == before


def test_public_write_target_covers_reflection_and_can_be_shared_across_lanes(
    tmp_path: pathlib.Path, env: tuple[pathlib.Path, pathlib.Path], capsys: pytest.CaptureFixture[str]
) -> None:
    """公開工程所有の同じpathは反映先を覆うが、別レーンの書込競合には加えない。"""
    repo, notes = env
    _awi(notes, "a.md", "`README.md`と`docs/development/design.md`を変える。")
    _awi(notes, "b.md", "`README.md`と`src/model.py`を変える。")
    decisions: list[dict[str, typing.Any]] = [
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
    costs = [{"レーン": lane, "根拠": "同一レーン案と分割案を比較した"} for lane in ("lane-01", "lane-02")]
    (repo / "AGENTS.md").write_text("# 規範\n\n## 公開工程\n", encoding="utf-8")
    for item in decisions:
        item["公開工程の規範根拠"] = [{"パス": "README.md", "規範ファイル": "AGENTS.md", "節": "公開工程"}]
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
        assert "区分間で重複するパスは、所有する1区分だけへ残す" in stderr


def test_rejects_public_write_target_without_repository_rule_section_rationale(
    tmp_path: pathlib.Path, env: tuple[pathlib.Path, pathlib.Path], capsys: pytest.CaptureFixture[str]
) -> None:
    """公開工程だけの所有を規範と節から確認できない分類を受理しない。"""
    repo, notes = env
    _awi(notes, "a.md", "`README.md`を変える。")
    decisions = [
        {"WI": "a.md", "レーン": "lane-01", "書込対象": [], "公開工程の書込対象": ["README.md"], "公開工程の規範根拠": []}
    ]

    assert _run(tmp_path, repo, decisions) == 1
    stderr = capsys.readouterr().err
    assert "`公開工程の書込対象`の根拠不足" in stderr
    assert "`公開工程の規範根拠`" in stderr


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
    """UWIの判断材料と回答から抽出し、参照先の非書込列挙を求めない。"""
    repo, notes = env
    body = (
        "---\ntype: uwi\nsource: process-wi\n---\n\n## 質問\n\nこの対応で問題無いか？\n\n"
        "## 判断材料\n\n`docs/development/design.md`の記述に従って対応した。\n\n"
        "## 回答\n\n<!-- ユーザーはこの行以降に回答を追記する -->\n`src/model.py`の判定も直して。\n"
    )
    (notes / "processing" / "u.md").write_text(body, encoding="utf-8")

    assert check_selection.reflected_paths(body, repo) == {"src/model.py", "docs/development/design.md"}
    assert _run(tmp_path, repo, [{"WI": "u.md", "レーン": "lane-01", "書込対象": ["src/model.py"]}]) == 0
    assert not capsys.readouterr().err


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
    assert "statusline.md: 広すぎる範囲: rust/claude-statusline/" in err


def test_reports_missing_body_as_violation(
    tmp_path: pathlib.Path, env: tuple[pathlib.Path, pathlib.Path], capsys: pytest.CaptureFixture[str]
) -> None:
    """private-notesに本文が無い項目は、項目の違反として返す。"""
    repo, _notes = env

    assert _run(tmp_path, repo, [{"awi": "absent.md", "lane": "lane-01", "write_files": []}]) == 1
    assert "absent.md: 本文を特定できない" in capsys.readouterr().err


@pytest.mark.parametrize(
    "model",
    ["claude:opus[1m]/medium", "opencode:local/Qwen/Qwen3.8-27B@thinking", "opencode:local/Qwen/Qwen3.8-27B/high@medium"],
)
def test_selection_values_with_yaml_syntax_characters_round_trip(
    tmp_path: pathlib.Path, env: tuple[pathlib.Path, pathlib.Path], capsys: pytest.CaptureFixture[str], model: str
) -> None:
    """書式例どおり単一引用符で囲んだ値は、コロン・括弧・引用符・改行を含んでも同じ構造で読める。

    保存後の検査と追加統合は同じ公開コマンドで同じ欄と型を受理するため、
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
        f"  担当モデル: {{実装担当: '{model}'}}\n"
        "  プロジェクト規範の指定: '観点群: 実装漏れと横展開 # 見出し, [括弧] {波括弧} と ''引用符'' の値'\n"
        "レーンの所要時間:\n"
        "- レーン: 'lane-01'\n"
        "  実装秒数: 600\n"
        "  統合秒数: 60\n"
        "  補正前の実装秒数: 600\n"
        "  補正前の統合秒数: 60\n"
        "  根拠: '同一レーン案: 3000秒\n"
        "\n"
        "    分割案: 2000秒'\n"
        "続行できない理由:\n"
        "- 'なし'\n"
        "見込みの補正: {係数: 1, 参照した記録: [], 除外した記録: []}\n"
        "初回配分:\n  候補WI: [a.md]\n  レーン割当: {a.md: lane-01}\n"
        "  不可分成分:\n  - WI: [a.md]\n    結合条件: []\n",
        encoding="utf-8",
    )

    assert _dispatch("--work-dir", str(repo), str(selection)) == 0, capsys.readouterr().err
    loaded = check_selection.load_selection(selection)
    decision = typing.cast(list[dict[str, object]], loaded["選定"])[0]
    assert decision["担当モデル"] == {"実装担当": model}
    assert decision["プロジェクト規範の指定"] == "観点群: 実装漏れと横展開 # 見出し, [括弧] {波括弧} と '引用符' の値"
    assert typing.cast(list[dict[str, object]], loaded["レーンの所要時間"])[0]["根拠"] == "同一レーン案: 3000秒\n分割案: 2000秒"


__all__ = ["fixture_env"]
