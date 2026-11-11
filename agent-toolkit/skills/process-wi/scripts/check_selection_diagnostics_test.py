# pylint: disable=useless-import-alias  # pytestのfixtureを試験モジュールの名前空間へ登録するため。
"""選定入力の失敗診断を検証する。"""

import pathlib
import typing

import check_selection
import pytest
import selection_contract
import yaml

from agent_toolkit._testing.selection_check_support import _awi, _derived, _dispatch, _overlap_record, _write_selection
from agent_toolkit._testing.selection_check_support import fixture_env as fixture_env


def _diagnostic(output: str, marker: str) -> str:
    """診断出力の違反行から`marker`を含む行を1行だけ選んで返す。"""
    lines = [line for line in output.splitlines() if marker in line and not line.startswith("次の操作:")]
    assert len(lines) == 1, output
    return lines[0]


@pytest.mark.parametrize(
    "case,marker,expected",
    [
        ("derived-scope-missing", "`導出した新設先`の対応が不正", ["実在ディレクトリ", "missing/"]),
        ("derived-scope-missing", "`書込対象`のパスの不正", ["src/save.py", "`導出した新設先`の記録が不正"]),
        ("derived-duplicate", "`導出した新設先`の対応が不正", ["重複して宣言"]),
        (
            "derived-missing-write",
            "`導出した新設先`の対応が不正",
            ["書込区分（`書込対象`・`公開工程の書込対象`）に同じパスが無い"],
        ),
        ("derived-directory", "`導出した新設先`の対応が不正", ["通常ファイルではない"]),
        ("derived-missing-parent", "`導出した新設先`の対応が不正", ["完全な新設名", "src/missing/save.py"]),
        ("derived-scope-outside", "`導出した新設先`の対応が不正", ["反映範囲がパスを含まない", "docs/"]),
        ("broad", "広すぎる範囲", ["src/", "src/model.py"]),
        ("edge-definitions", "結合根拠が成立しない", ["定義が交わらない", "入力", "出力"]),
        ("edge-dependency", "結合根拠が成立しない", ["depends_on", "a.md", "b.md"]),
        ("source-dependency", "結合根拠が元入力と一致しない", ["depends_on", "invented.md", "frontmatter"]),
        ("source-plan", "結合根拠が元入力と一致しない", ["計画1", "other.md", "shared.md"]),
    ],
)
def test_compound_diagnostics_name_failed_condition(
    env: tuple[pathlib.Path, pathlib.Path],
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    case: str,
    marker: str,
    expected: list[str],
) -> None:
    """複数の条件をまとめた判定の診断行は、成立しなかった条件と直すのに要る値を含む。"""
    repo, notes = env
    plan = str(tmp_path / "shared.md")
    if case.startswith("derived"):
        record = _derived("src/missing/save.py" if case == "derived-missing-parent" else "src/save.py")
        item: dict[str, typing.Any] = {
            "WI": "a.md",
            "レーン": "lane-01",
            "書込対象": [record["パス"]],
            "導出した新設先": [record],
        }
        body = "`src/`へ保存用入口を新設する。"
        if case == "derived-scope-missing":
            record["反映範囲"] = "missing/"
        elif case == "derived-duplicate":
            item["導出した新設先"].append(dict(record))
        elif case == "derived-missing-write":
            item["書込対象"] = []
        elif case == "derived-directory":
            (repo / record["パス"]).mkdir()
        elif case == "derived-scope-outside":
            record["反映範囲"] = "docs/"
            body = "保存用入口を新設する。"
        _awi(notes, "a.md", body)
        path = _write_selection(tmp_path / "selection.yaml", [item])
    elif case == "broad":
        _awi(notes, "a.md", "`src/model.py`を変える。")
        path = _write_selection(tmp_path / "selection.yaml", [{"WI": "a.md", "レーン": "lane-01", "書込対象": ["src/"]}])
    else:
        for name in ("a.md", "b.md"):
            _awi(notes, name, "`README.md`を変える。", dependencies=["a.md"] if name == "b.md" else [])
        path = _write_selection(
            tmp_path / "selection.yaml",
            [{"WI": name, "レーン": "lane-01", "書込対象": ["README.md"], "再開位置": plan} for name in ("a.md", "b.md")],
        )
        evidences: dict[str, dict[str, typing.Any]] = {
            "edge-definitions": {"定義1": ["入力"], "定義2": ["出力"], "段階比較": "同段階で書く"},
            "edge-dependency": {
                "リポジトリ1": "github.com/test/repo",
                "リポジトリ2": "github.com/test/repo",
                "依存元": "b.md",
                "depends_on": ["c.md"],
            },
            "source-dependency": {
                "リポジトリ1": "github.com/test/repo",
                "リポジトリ2": "github.com/test/repo",
                "依存元": "b.md",
                "depends_on": ["a.md", "invented.md"],
            },
            "source-plan": {"計画1": str(tmp_path / "other.md"), "計画2": str(tmp_path / "other.md")},
        }
        evidence = evidences[case]
        kind = {"edge-definitions": "書込重複", "source-plan": "再開計画"}.get(case, "依存")
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        data["初回配分"]["不可分成分"] = [
            {"WI": ["a.md", "b.md"], "結合条件": [{"WI1": "a.md", "WI2": "b.md", "種別": kind, "根拠": evidence}]}
        ]
        path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    assert _dispatch(str(path), "--work-dir", str(repo)) == 1
    line = _diagnostic(capsys.readouterr().err, marker)
    for value in expected:
        assert value in line, line


# 違反の種類ごとの次の操作。実装の定数を種類の識別に使い、出力に現れた種類の対処だけが1回ずつ出ることを確かめる。
_NEXT_ACTIONS = {
    name: value
    for module in (check_selection, selection_contract)
    for name, value in vars(module).items()
    if name.startswith("_NEXT_") and name != "_NEXT_RERUN"
}


def _next_action_case(case: str, tmp_path: pathlib.Path, notes: pathlib.Path) -> pathlib.Path:
    """違反の種類ごとに、その種類の違反だけを生む選定YAMLを保存してそのパスを返す。"""
    item: dict[str, typing.Any] = {"WI": "a.md", "レーン": "lane-01", "書込対象": ["README.md"]}
    if case in {"uncovered", "uncovered-multiple", "broad", "uncovered-broad"}:
        reflected = {
            "uncovered": "`README.md`と`src/model.py`を変える。",
            "uncovered-multiple": "`README.md`、`src/model.py`、`docs/development/design.md`を変える。",
            "broad": "`src/model.py`を変える。",
            "uncovered-broad": "`src/model.py`と`README.md`を変える。",
        }[case]
        _awi(notes, "a.md", reflected)
        if case in {"broad", "uncovered-broad"}:
            item["書込対象"] = ["src/"]
        return _write_selection(tmp_path / "selection.yaml", [item])
    if case == "missing-body":
        return _write_selection(tmp_path / "selection.yaml", [item])
    _awi(notes, "a.md", "`README.md`を変える。")
    _awi(notes, "b.md", "`README.md`を変える。", dependencies=["a.md"])
    second: dict[str, typing.Any] = {"WI": "b.md", "レーン": "lane-01", "書込対象": ["README.md"]}
    costs: list[dict[str, typing.Any]] | None = None
    if case == "model-conflict":
        item["担当モデル"] = {"実装担当": "claude:opus/medium"}
        second["担当モデル"] = {"実装担当": "codex:gpt/medium"}
    elif case in {"stage-gap", "prior-missing"}:
        second["レーン"] = "lane-02"
        if case == "stage-gap":
            costs = [{"レーン": "lane-01"}, {"レーン": "lane-02", "段階": 3, "先行レーン": ["lane-01"]}]
        else:
            _awi(notes, "b.md", "`src/model.py`を変える。")
            second["書込対象"] = ["src/model.py"]
            costs = [{"レーン": "lane-01"}, {"レーン": "lane-02", "段階": 2}]
    elif case == "lane-bound":
        second["レーン"] = "lane-02"
        _awi(notes, "b.md", "`src/model.py`を変える。")
        second["書込対象"] = ["src/model.py"]
    path = _write_selection(tmp_path / "selection.yaml", [item, second], costs)
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if case == "stage-gap":
        data["レーン間の重なり"] = [_overlap_record("README.md", "交わる")]
    elif case == "lane-bound":
        data["初回配分"]["候補WI"] = ["a.md", "b.md"]
    elif case in {"edge-failure", "source-mismatch"}:
        kind, evidence = (
            ("書込重複", {"定義1": ["入力"], "定義2": ["出力"], "段階比較": "同段階で書く"})
            if case == "edge-failure"
            else (
                "依存",
                {
                    "リポジトリ1": "github.com/test/repo",
                    "リポジトリ2": "github.com/test/repo",
                    "依存元": "b.md",
                    "depends_on": ["a.md", "invented.md"],
                },
            )
        )
        data["初回配分"]["不可分成分"] = [
            {"WI": ["a.md", "b.md"], "結合条件": [{"WI1": "a.md", "WI2": "b.md", "種別": kind, "根拠": evidence}]}
        ]
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    return path


@pytest.mark.parametrize(
    "case,expected",
    [
        ("broad", ["_NEXT_BROAD"]),
        ("uncovered-broad", ["_NEXT_BROAD"]),
        ("missing-body", ["_NEXT_MISSING_BODY"]),
        ("model-conflict", ["_NEXT_LANE_MODEL"]),
        ("stage-gap", ["_NEXT_STAGE_SEQUENCE"]),
        ("prior-missing", ["_NEXT_PRIOR_MISSING"]),
        # 成立しない辺は成分の連結も失わせるため、2つの種類の対処が1回ずつ現れる。
        ("edge-failure", ["_NEXT_EDGE_FAILURE", "_NEXT_COMPONENT_LINK"]),
        ("source-mismatch", ["_NEXT_SOURCE_MISMATCH"]),
        ("lane-bound", ["_NEXT_LANE_BOUND"]),
    ],
)
def test_next_action_lists_only_reported_kinds(
    env: tuple[pathlib.Path, pathlib.Path],
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    case: str,
    expected: list[str],
) -> None:
    """次の操作は出力に現れた違反の種類の対処だけを種類ごとに1回含み、違反行は対処を重ねない。"""
    repo, notes = env
    path = _next_action_case(case, tmp_path, notes)
    assert _dispatch(str(path), "--work-dir", str(repo)) == 1
    lines = capsys.readouterr().err.splitlines()
    actions = [line for line in lines if line.startswith("次の操作:")]
    violations = [line for line in lines if not line.startswith("次の操作:")]
    assert len(actions) == 1 and violations, lines
    present = {name for name, value in _NEXT_ACTIONS.items() if value in actions[0]}
    assert present == set(expected), lines
    for name in expected:
        assert actions[0].count(_NEXT_ACTIONS[name]) == 1
    if case in {"uncovered-multiple", "lane-bound"}:
        # 同じ種類の違反が複数行あっても、その種類の対処は1回だけ現れる。
        assert len(violations) >= 2
    for line in violations:
        assert not any(value in line for value in _NEXT_ACTIONS.values()), line


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
            "`<claude|codex|agy>:<model>/<effort>`か`opencode:<provider>/<model>@<effort>`の値へ直す",
            id="model-type",
        ),
        pytest.param(
            "選定:\n- WI: 'a.md'\n  レーン: 'lane-01'\n  鮮度: {status: current}\n  書込対象: ['src/model.py']\n"
            "  担当モデル: {計画担当: 'claude:opus/high'}\n"
            "レーンの所要時間:\n- レーン: 'lane-01'\n  実装秒数: 1\n  統合秒数: 1\n  根拠: '根拠'\n",
            "計画担当",
            "`<claude|codex|agy>:<model>/<effort>`か`opencode:<provider>/<model>@<effort>`の値へ直す",
            id="unknown-model-role",
        ),
        pytest.param(
            "選定:\n- WI: 'a.md'\n  レーン: 'lane-01'\n  鮮度: {status: current}\n  書込対象: ['src/model.py']\n"
            "  書込対象の候補: ['src/']\n"
            "レーンの所要時間:\n- レーン: 'lane-01'\n  実装秒数: 1\n  統合秒数: 1\n  根拠: '根拠'\n",
            "a.md: 未知の欄: 書込対象の候補",
            "「選定結果の書式」の欄名と型へ直して",
            id="unknown-key",
        ),
        pytest.param(
            "選定:\n- WI: 'a.md'\n  レーン: 'lane-01'\n  鮮度: {status: current}\n  書込対象: 'src/model.py'\n"
            "レーンの所要時間:\n- レーン: 'lane-01'\n  実装秒数: '1'\n  統合秒数: 1\n  根拠: '根拠'\n",
            "a.md: `書込対象`が文字列の列ではない",
            "「選定結果の書式」の欄名と型へ直して",
            id="field-type",
        ),
        pytest.param(
            "選定:\n- WI: 'a.md'\n  レーン: 'lane-01'\n  書込対象: ['src/model.py']\n",
            "a.md: 必須の欄がない: 鮮度",
            "「選定結果の書式」の欄名と型へ直して",
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
    """引数が正しく選定結果の内容が誤る場合は、終了コード2と、選定結果を直す次の操作を返す。"""
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


@pytest.mark.parametrize("model", ["claude:opus", "opencode:local/medium"])
def test_field_type_errors_are_reported_together(
    tmp_path: pathlib.Path, env: tuple[pathlib.Path, pathlib.Path], capsys: pytest.CaptureFixture[str], model: str
) -> None:
    """1回の実行で全ての内容の誤りを返し、直すたびに次の誤りが現れる往復を生じさせない。"""
    repo, _notes = env
    selection = tmp_path / "selection.yaml"
    selection.write_text(
        "選定:\n- WI: 'a.md'\n  レーン: 'lane-01'\n  鮮度: {status: current}\n  書込対象: 'src/model.py'\n"
        f"  担当モデル: {{実装担当: '{model}'}}\n"
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
    selection = _write_selection(tmp_path / "selection.yaml", [{"awi": "a.md", "lane": "lane-01", "write_files": ["src/"]}])

    assert _dispatch("--work-dir", str(repo), str(selection)) == 1
    assert "a.md: 広すぎる範囲: src/" in capsys.readouterr().err


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
    assert "段階が1から連続しない: 段階2がない" in capsys.readouterr().err


__all__ = ["fixture_env"]
