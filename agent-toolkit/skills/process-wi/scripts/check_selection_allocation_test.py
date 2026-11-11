# pylint: disable=useless-import-alias  # pytestのfixtureを試験モジュールの名前空間へ登録するため。
"""選定の初回レーン配分と根拠を検証する。"""

import pathlib
import typing

import pytest
import yaml

from agent_toolkit._testing.selection_check_support import _REPO_FILES, _awi, _dispatch, _initial_allocation, _write_selection
from agent_toolkit._testing.selection_check_support import fixture_env as fixture_env


@pytest.mark.parametrize(
    "case,expected",
    [
        ("public-missing", 1),
        ("public-file", 1),
        ("public-heading", 1),
        ("public-valid", 0),
        ("public-duplicate", 1),
        ("public-surplus", 1),
        ("staged-missing", 1),
        ("staged-number", 0),
        ("staged-impossible", 0),
        ("staged-type", 2),
        ("staged-boundary", 0),
        ("raw-missing", 1),
        ("raw-type", 2),
        ("correction-missing", 1),
        ("correction-mismatch", 1),
        ("correction-no-source", 1),
        ("correction-valid", 0),
        ("correction-tolerance", 0),
    ],
)
def test_structured_records_and_rationale_independence(
    env: tuple[pathlib.Path, pathlib.Path],
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    case: str,
    expected: int,
) -> None:
    """3種類の記録を公開された検査コマンドで検査し、自由記述の置換で終了コードと診断が変わらないことを確かめる。"""
    repo, notes = env
    _awi(notes, "a.md", "`README.md`を変更する。")
    item: dict[str, typing.Any] = {"WI": "a.md", "レーン": "lane-01", "書込対象": ["README.md"]}
    path = _write_selection(tmp_path / "structured.yaml", [item])
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    item, row = data["選定"][0], data["レーンの所要時間"][0]
    if case.startswith("public-"):
        item.update({"書込対象": [], "公開工程の書込対象": ["README.md"]})
        (repo / "RULES.md").write_text("# 規範\n\n## 公開\n", encoding="utf-8")
        if case != "public-missing":
            record = {"パス": "README.md", "規範ファイル": "RULES.md", "節": "公開"}
            item["公開工程の規範根拠"] = [record]
            if case == "public-file":
                record["規範ファイル"] = "missing.md"
            if case == "public-heading":
                record["節"] = "存在しない節"
            if case == "public-duplicate":
                item["公開工程の規範根拠"].append(dict(record))
            if case == "public-surplus":
                record["パス"] = "src/model.py"
        # 同じ定型文を全レーンへ置いても、パスごとの記録欠落を埋めない。
        other = {**item, "WI": "b.md", "レーン": "lane-02"}
        _awi(notes, "b.md", "`README.md`を変更する。")
        data["選定"].append(other)
        data["レーンの所要時間"].append({**row, "レーン": "lane-02"})
        data["初回配分"] = _initial_allocation(data["選定"])
        for cost in data["レーンの所要時間"]:
            cost["根拠"] = "規範の節に従いREADME.mdを公開工程で書く"
    elif case.startswith("staged-"):
        row["実装秒数"] = row["補正前の実装秒数"] = 6000 if case == "staged-boundary" else 6001
        if case not in ("staged-missing", "staged-boundary"):
            data["段階案の完了見込み秒数"] = {
                "staged-number": 6200,
                "staged-impossible": "成立する段階案なし",
                "staged-type": True,
            }[case]
    elif case.startswith("raw-"):
        if case == "raw-missing":
            del row["補正前の実装秒数"]
        else:
            row["補正前の実装秒数"] = True
    else:
        if case == "correction-missing":
            del data["見込みの補正"]
        elif case == "correction-mismatch":
            row["実装秒数"] += 2
        else:
            data["見込みの補正"]["係数"] = 2
            row["実装秒数"] *= 2
            row["統合秒数"] *= 2
            if case != "correction-no-source":
                data["見込みの補正"]["参照した記録"] = ["plans/example.progress.jsonl"]
            if case == "correction-tolerance":
                row["実装秒数"] += 1
    observed = []
    for replacement in (False, True):
        if replacement:
            for cost in data["レーンの所要時間"]:
                cost["根拠"] = "配分案を比較した固定文"
        path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
        result = _dispatch(str(path), "--work-dir", str(repo))
        stderr = capsys.readouterr().err
        assert result == expected, stderr
        if result == 1:
            assert "次の操作:" in stderr
            field = (
                "公開工程の規範根拠"
                if case.startswith("public-")
                else "段階案の完了見込み秒数"
                if case.startswith("staged-")
                else "見込みの補正"
            )
            assert field in stderr
        observed.append((result, stderr))
    assert observed[0] == observed[1]


@pytest.mark.parametrize(
    "stages,completed,expected",
    [
        ([1, 1, 3], [], 1),
        ([1, 1, 3], [0], 1),
        ([1, 2], [0], 0),
        ([3, 1], [0], 0),
        ([1, 2, 3], [0, 1], 0),
        ([3], [0], 0),
    ],
)
def test_public_check_stage_continuity_preserves_completed_prefix(
    env: tuple[pathlib.Path, pathlib.Path],
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    stages: list[int],
    completed: list[int],
    expected: int,
) -> None:
    """未統合の段階飛びを拒否し、元配分の完了した前段から後段へ進める。"""
    repo, notes = env
    decisions = []
    costs = []
    for index, stage in enumerate(stages):
        name, lane, target = f"stage-{index}.md", f"lane-{index + 1:02}", _REPO_FILES[index]
        _awi(notes, name, f"`{target}`を変更する。")
        decisions.append({"WI": name, "レーン": lane, "書込対象": [target]})
        costs.append(
            {
                "レーン": lane,
                "段階": stage,
                "統合状態": "統合済み" if index in completed else "未統合",
                "先行レーン": [f"lane-{prior + 1:02}" for prior in range(index) if stages[prior] < stage],
            }
        )
    path = _write_selection(tmp_path / "stages.yaml", decisions, costs)
    assert _dispatch(str(path), "--work-dir", str(repo)) == expected
    if expected:
        assert "段階2がない" in capsys.readouterr().err


@pytest.mark.parametrize("state", ["未統合", "統合済み"])
@pytest.mark.parametrize("violation", ["unclassified", "broad", "classification", "norm-spec"])
def test_public_check_body_validation_follows_integration_state(
    env: tuple[pathlib.Path, pathlib.Path],
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    state: str,
    violation: str,
) -> None:
    """書込予定・範囲・区分・規範指定の本文検査は未統合の項目にだけ適用し、統合済みの項目は履歴として拒否しない。"""
    repo, notes = env
    _awi(notes, "a.md", "`src/model.py`を変更する。", state="adopt" if state == "統合済み" else "processing")
    item: dict[str, typing.Any] = {"WI": "a.md", "レーン": "lane-01", "書込対象": ["src/model.py"]}
    if violation == "unclassified":
        item["書込対象"] = []
        diagnostic = ""
    elif violation == "broad":
        item["書込対象"] = ["src/"]
        diagnostic = "広すぎる範囲"
    elif violation == "classification":
        item["書き込まない反映先"] = ["src/model.py"]
        diagnostic = "区分間の重複"
    else:
        (repo / "pyproject.toml").write_text(
            '[[tool.agent-toolkit.pick-wi-check.norm-spec]]\nname = "仕様"\npaths = ["src/"]\nrequire-text = ["設計を読む"]\n',
            encoding="utf-8",
        )
        diagnostic = "プロジェクト規範の指定の不足"
    path = _write_selection(tmp_path / "body.yaml", [item], [{"レーン": "lane-01", "統合状態": state}])
    if state == "統合済み" or violation == "unclassified":
        assert _dispatch(str(path), "--work-dir", str(repo)) == 0
        assert not capsys.readouterr().err
        return
    assert _dispatch(str(path), "--work-dir", str(repo)) == 1
    assert f"a.md: {diagnostic}" in capsys.readouterr().err


@pytest.mark.parametrize("count,lanes,expected", [(0, 0, 0), (1, 1, 0), (10, 1, 0), (10, 2, 1), (11, 2, 0)])
def test_public_check_initial_lane_limit_boundaries(
    env: tuple[pathlib.Path, pathlib.Path],
    tmp_path: pathlib.Path,
    count: int,
    lanes: int,
    expected: int,
) -> None:
    """候補数の十件境界を選定件数から分け、初回の上限を公開された検査コマンドで判定する。"""
    repo, notes = env
    decisions = []
    for index in range(lanes):
        name, lane = f"limit-{index}.md", f"lane-{index + 1:02}"
        target = f"file-{index}.md"
        (repo / target).write_text("x", encoding="utf-8")
        _awi(notes, name, f"`{target}`を変える。")
        decisions.append({"WI": name, "レーン": lane, "書込対象": [target]})
    path = _write_selection(tmp_path / "limit.yaml", decisions)
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["初回配分"]["候補WI"] = [item["WI"] for item in decisions] + [f"candidate-{i}.md" for i in range(count - lanes)]
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    assert _dispatch(str(path), "--work-dir", str(repo)) == expected


@pytest.mark.parametrize(
    "count,initial,total", [(0, 0, 0), (0, 0, 1), (11, 2, 2), (11, 2, 3), (24, 3, 3), (24, 3, 4), (24, 4, 4)]
)
@pytest.mark.parametrize("integrated", [False, True])
@pytest.mark.parametrize("seconds", [100, 6000, 6001])
def test_public_check_absolute_limit_counts_all_lanes(
    env: tuple[pathlib.Path, pathlib.Path],
    tmp_path: pathlib.Path,
    count: int,
    initial: int,
    total: int,
    integrated: bool,
    seconds: int,
) -> None:
    """初回と追加後の全レーンを数え、完了・後段・見込み時間で上限を変えない。"""
    repo, notes = env
    decisions = []
    for index in range(total):
        name, target = f"all-{index}.md", f"target-{index}.md"
        (repo / target).write_text("x", encoding="utf-8")
        _awi(notes, name, f"`{target}`を変える。")
        decisions.append({"WI": name, "レーン": f"lane-{index + 1:02}", "書込対象": [target]})
    path = _write_selection(tmp_path / "all.yaml", decisions)
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["初回配分"] = _initial_allocation(decisions[:initial])
    data["初回配分"]["候補WI"] = [item["WI"] for item in decisions[:initial]] + [
        f"candidate-{i}.md" for i in range(count - initial)
    ]
    data["追加WI"] = [item["WI"] for item in decisions[initial:]]
    for row in data["レーンの所要時間"]:
        row["実装秒数"] = seconds
        row["補正前の実装秒数"] = seconds
        if integrated:
            row["統合状態"] = "統合済み"
    if integrated and total > 1:
        data["レーンの所要時間"][-1].update({"段階": 2, "先行レーン": ["lane-01"]})
        data["単一段階案の完了見込み秒数"] = seconds
    if seconds > 6000:
        data["段階案の完了見込み秒数"] = "成立する段階案なし"
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    bound = {0: 0, 11: 2, 24: 3}[count]
    assert _dispatch(str(path), "--work-dir", str(repo)) == (1 if total > bound else 0)


@pytest.mark.parametrize("case,expected", [("valid", 1), ("sixteen", 1), ("mixed", 1), ("ordinary", 1), ("broken-edge", 1)])
def test_public_check_large_components_keep_absolute_limit(
    env: tuple[pathlib.Path, pathlib.Path],
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    case: str,
    expected: int,
) -> None:
    """巨大成分と独立成分の配分も候補件数から決まる上限へそろえる。"""
    repo, notes = env
    assignments = {"a.md": "lane-01", "b.md": "lane-01", "c.md": "lane-02", "d.md": "lane-03", "e.md": "lane-04"}
    if case == "mixed":
        assignments["c.md"] = "lane-01"
    if case == "sixteen":
        assignments["d.md"] = "lane-02"
        assignments["e.md"] = "lane-03"
        assignments["f.md"] = "lane-04"
    decisions = []
    for index, (name, lane) in enumerate(assignments.items()):
        target = f"target-{index}.md"
        (repo / target).write_text("x", encoding="utf-8")
        _awi(notes, name, f"`{target}`を変える。", dependencies=["a.md"] if name == "b.md" else [])
        decisions.append({"WI": name, "レーン": lane, "書込対象": [target]})
    path = _write_selection(tmp_path / "huge.yaml", decisions)
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    groups = [
        {
            "WI": ["a.md", "b.md"],
            "結合条件": [
                {
                    "WI1": "a.md",
                    "WI2": "b.md",
                    "種別": "依存",
                    "根拠": {
                        "リポジトリ1": "github.com/test/repo",
                        "リポジトリ2": "github.com/test/repo",
                        "依存元": "b.md",
                        "depends_on": ["a.md"] if case != "broken-edge" else [],
                    },
                }
            ],
        },
        *({"WI": [name], "結合条件": []} for name in assignments if name not in ("a.md", "b.md")),
    ]
    if case == "ordinary":
        groups[0] = {"WI": ["a.md"], "結合条件": []}
        groups.insert(1, {"WI": ["b.md"], "結合条件": []})
    candidate_count = 16 if case == "sixteen" else len(assignments)
    data["初回配分"] = {
        "候補WI": [*assignments, *(f"candidate-{i}.md" for i in range(candidate_count - len(assignments)))],
        "レーン割当": assignments,
        "不可分成分": groups,
    }
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    assert _dispatch(str(path), "--work-dir", str(repo)) == expected
    if case == "sixteen":
        output = capsys.readouterr().err
        assert "N=16" in output and "B=2" in output and "実レーン数=4" in output


@pytest.mark.parametrize("problem", ["missing", "duplicate-candidate", "missing-member", "wrong-kind", "wrong-type"])
def test_public_check_rejects_invalid_allocation_evidence(
    env: tuple[pathlib.Path, pathlib.Path],
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    problem: str,
) -> None:
    """情報の欠落と矛盾を、候補件数による推定で黙認しない。"""
    repo, notes = env
    _awi(notes, "a.md", "`README.md`を変える。")
    path = _write_selection(tmp_path / "invalid.yaml", [{"WI": "a.md", "レーン": "lane-01", "書込対象": ["README.md"]}])
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if problem == "missing":
        del data["初回配分"]
    elif problem == "duplicate-candidate":
        data["初回配分"]["候補WI"].append("a.md")
    elif problem == "missing-member":
        data["初回配分"]["不可分成分"] = []
    elif problem == "wrong-type":
        data["初回配分"]["不可分成分"][0]["実装秒数"] = True
    else:
        data["初回配分"]["不可分成分"][0]["結合条件"] = [{"WI1": "a.md", "WI2": "b.md", "種別": "未知", "根拠": {}}]
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    assert _dispatch(str(path), "--work-dir", str(repo)) != 0
    output = capsys.readouterr().err
    assert "初回配分" in output and "次の操作:" in output


@pytest.mark.parametrize("kind", ["依存", "再開計画", "書込重複"])
@pytest.mark.parametrize("matches", [True, False])
def test_public_check_component_evidence_matches_source(
    env: tuple[pathlib.Path, pathlib.Path],
    tmp_path: pathlib.Path,
    kind: str,
    matches: bool,
) -> None:
    """結合の構造だけを整えた偽の根拠は、元入力との対照で拒否する。書込重複は選定の現在値で判定する。"""
    repo, notes = env
    # 書込重複の不一致は、現在の`書込対象`が重ならない入力で表す。
    targets = {"a.md": "README.md", "b.md": "src/model.py" if kind == "書込重複" and not matches else "README.md"}
    for name, target in targets.items():
        _awi(notes, name, f"`{target}`を変える。", dependencies=["a.md"] if name == "b.md" else [])
    plan = str(tmp_path / "shared.md")
    path = _write_selection(
        tmp_path / "component.yaml",
        [{"WI": name, "レーン": "lane-01", "書込対象": [target], "再開位置": plan} for name, target in targets.items()],
    )
    evidences: dict[str, dict[str, typing.Any]] = {
        "依存": {
            "リポジトリ1": "github.com/test/repo",
            "リポジトリ2": "github.com/test/repo",
            "依存元": "b.md",
            "depends_on": ["a.md"],
        },
        "再開計画": {"計画1": plan, "計画2": plan},
        "書込重複": {"定義1": ["f"], "定義2": ["f"], "段階比較": "同じ定義なので段階でも直列となる"},
    }
    evidence = evidences[kind]
    if not matches:
        if kind == "依存":
            evidence["depends_on"] = ["a.md", "invented.md"]
        elif kind == "再開計画":
            evidence["計画1"] = evidence["計画2"] = str(tmp_path / "other.md")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["初回配分"]["不可分成分"] = [
        {"WI": ["a.md", "b.md"], "結合条件": [{"WI1": "a.md", "WI2": "b.md", "種別": kind, "根拠": evidence}]}
    ]
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    assert _dispatch(str(path), "--work-dir", str(repo)) == (0 if matches else 1)


__all__ = ["fixture_env"]
