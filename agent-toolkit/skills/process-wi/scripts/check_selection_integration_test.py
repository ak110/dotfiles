# pylint: disable=useless-import-alias  # pytestのfixtureを試験モジュールの名前空間へ登録するため。
"""選定の統合済みレーンと再開操作を検証する。"""

import json
import pathlib
import typing

import pytest
import yaml

from agent_toolkit._testing.selection_check_support import _awi, _dispatch, _initial_allocation, _track, _write_selection
from agent_toolkit._testing.selection_check_support import fixture_env as fixture_env


def test_require_integrated_checks_all_stages_without_current_wi(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """公開前確認は全段階の保存状態を読み、既に無いWI本文や配置を再検査しない。"""
    path = _write_selection(
        tmp_path / "selection.yaml",
        [
            {"WI": "a.md", "レーン": "lane-01", "書込対象": ["removed.py"], "統合状態": "統合済み"},
            {"WI": "b.md", "レーン": "lane-02", "書込対象": ["removed.py"]},
            {"WI": "c.md", "レーン": "lane-03", "書込対象": ["removed.py"]},
        ],
        [
            {"レーン": "lane-01", "統合状態": "統合済み"},
            {"レーン": "lane-02", "段階": 2, "先行レーン": ["lane-01"]},
            {"レーン": "lane-03", "段階": 3, "先行レーン": ["lane-02"]},
        ],
    )
    before = path.read_bytes()
    assert _dispatch(str(path), "--require-integrated") == 1
    output = capsys.readouterr()
    assert all(value in output.err for value in ("lane-02", "lane-03", "b.md", "c.md", "2", "3"))
    assert path.read_bytes() == before
    data = yaml.safe_load(before)
    for row in data["レーンの所要時間"]:
        row["統合状態"] = "統合済み"
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    assert _dispatch(str(path), "--require-integrated") == 0


@pytest.mark.parametrize("state", ["未知", 1])
def test_require_integrated_rejects_invalid_state(
    tmp_path: pathlib.Path, state: object, capsys: pytest.CaptureFixture[str]
) -> None:
    """未知の状態は未統合の正常診断と区別する。"""
    path = _write_selection(
        tmp_path / "selection.yaml", [{"WI": "a.md", "レーン": "lane-01", "書込対象": [], "統合状態": state}]
    )
    assert _dispatch(str(path), "--require-integrated") == 2
    assert "統合状態" in capsys.readouterr().err


@pytest.mark.parametrize("case", ["empty", "missing", "malformed", "unassigned-lane"])
def test_require_integrated_empty_and_unreadable_inputs(tmp_path: pathlib.Path, case: str) -> None:
    """正常な0レーンだけを完了とし、読取不能・破損・所属不一致を入力不備として返す。"""
    path = tmp_path / "selection.yaml"
    if case == "empty":
        _write_selection(path, [])
    elif case == "malformed":
        path.write_text("選定: [\n", encoding="utf-8")
    elif case == "unassigned-lane":
        _write_selection(path, [], costs=[{"レーン": "lane-01"}])
    before = path.read_bytes() if path.exists() else None
    assert _dispatch(str(path), "--require-integrated") == (0 if case == "empty" else 2)
    assert (path.read_bytes() if path.exists() else None) == before


def test_reopen_wi_retains_original_allocation_and_other_integrations(
    env: tuple[pathlib.Path, pathlib.Path], tmp_path: pathlib.Path
) -> None:
    """上限使用済みでも同じWIだけを再開し、他の統合履歴を保持して不可分保存する。"""
    repo, notes = env
    _awi(notes, "a.md", "`README.md`を変更する。")
    _awi(notes, "b.md", "`README.md`を変更する。")
    _awi(notes, "c.md", "`src/model.py`を変更する。")
    path = _write_selection(
        tmp_path / "selection.yaml",
        [
            {"WI": "a.md", "レーン": "lane-01", "書込対象": ["README.md"]},
            {"WI": "b.md", "レーン": "lane-01", "書込対象": ["README.md"]},
            {"WI": "c.md", "レーン": "lane-02", "書込対象": ["src/model.py"]},
        ],
        [{"レーン": "lane-01", "統合状態": "統合済み"}, {"レーン": "lane-02", "統合状態": "統合済み"}],
    )
    before = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert _dispatch(str(path), "--reopen-wi", "a.md", "--output", str(path), "--work-dir", str(repo)) == 0
    after = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert after["初回配分"] == before["初回配分"]
    assert [row["統合状態"] for row in after["レーンの所要時間"]] == ["未統合", "統合済み"]
    assert [row["統合状態"] for row in after["選定"]] == ["未統合", "統合済み", "統合済み"]
    saved = path.read_bytes()
    assert _dispatch(str(path), "--reopen-wi", "missing.md", "--output", str(path), "--work-dir", str(repo)) == 2
    assert path.read_bytes() == saved


@pytest.mark.parametrize("state,expected", [(None, 1), ("未統合", 1), ("統合済み", 0)])
def test_public_check_overlap_uses_integration_state(
    env: tuple[pathlib.Path, pathlib.Path],
    tmp_path: pathlib.Path,
    state: str | None,
    expected: int,
) -> None:
    """同段階の2レーンが同じ定義を書く選定は、片方が統合済みの場合だけ競合として拒否しない。"""
    repo, notes = env
    for name in ("a.md", "b.md"):
        _awi(notes, name, "`README.md`を変える。")
    path = _write_selection(
        tmp_path / "state.yaml",
        [
            {"WI": "a.md", "レーン": "lane-01", "書込対象": ["README.md"]},
            {"WI": "b.md", "レーン": "lane-02", "書込対象": ["README.md"]},
        ],
    )
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["レーン間の重なり"] = [
        {
            "レーン1": "lane-01",
            "レーン2": "lane-02",
            "共通パス": "README.md",
            "レーン1の定義": ["f"],
            "レーン2の定義": ["f"],
            "判定": "交わる",
        }
    ]
    if state is not None:
        data["レーンの所要時間"][0]["統合状態"] = state
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    assert _dispatch(str(path), "--work-dir", str(repo)) == expected


def test_public_command_records_integrated_lane(env: tuple[pathlib.Path, pathlib.Path], tmp_path: pathlib.Path) -> None:
    """統合・終了検収後の記録を公開された同じ保存コマンドへ渡し、反復と再入力も成功する。"""
    repo, notes = env
    _awi(notes, "a.md", "`README.md`を変える。")
    path = _write_selection(tmp_path / "state.yaml", [{"WI": "a.md", "レーン": "lane-01", "書込対象": ["README.md"]}])
    for _ in range(2):
        assert _dispatch(str(path), "--mark-integrated", "lane-01", "--output", str(path), "--work-dir", str(repo)) == 0
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert data["レーンの所要時間"][0]["統合状態"] == "統合済み"
    assert data["選定"][0]["WI"] == "a.md"
    assert _dispatch(str(path), "--work-dir", str(repo)) == 0
    before = path.read_bytes()
    assert _dispatch(str(path), "--mark-integrated", "lane-99", "--output", str(path), "--work-dir", str(repo)) == 2
    assert path.read_bytes() == before


@pytest.mark.parametrize("problem", ["unknown-state", "state-type", "unknown-prior", "broad", "invalid-path", "conflict"])
def test_state_update_preserves_output_on_failure(
    env: tuple[pathlib.Path, pathlib.Path],
    tmp_path: pathlib.Path,
    problem: str,
) -> None:
    """不正な状態・完了履歴の未知参照・未統合の別レーンの違反・残る未統合競合は、既存の保存先を更新しない。

    記録するレーンの項目は作業ツリーの状態を前提とする判定から外れるため、本文の違反は未統合の別レーンの項目へ置く。
    """
    repo, notes = env
    decisions = []
    for number in (1, 2, 3):
        name = f"{number}.md"
        _awi(notes, name, "`README.md`を変える。")
        decisions.append({"WI": name, "レーン": f"lane-0{number}", "書込対象": ["README.md"]})
    if problem in ("broad", "invalid-path"):
        _awi(notes, "other.md", "`src/model.py`を変える。")
        decisions = [decisions[0], {"WI": "other.md", "レーン": "lane-02", "書込対象": ["src/model.py"]}]
    path = _write_selection(tmp_path / "state.yaml", decisions)
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if problem not in ("conflict", "broad", "invalid-path"):
        data["選定"] = data["選定"][:1]
        data["レーンの所要時間"] = data["レーンの所要時間"][:1]
        data["初回配分"] = _initial_allocation(data["選定"])
    row = data["レーンの所要時間"][0]
    if problem in ("unknown-state", "state-type"):
        row["統合状態"] = "完了" if problem == "unknown-state" else []
    elif problem == "unknown-prior":
        row["統合状態"] = "統合済み"
        row["先行レーン"] = ["lane-99"]
    elif problem in ("broad", "invalid-path"):
        data["選定"][1]["書込対象"] = ["src/"] if problem == "broad" else ["docs/../src/model.py"]
    else:
        data["レーン間の重なり"] = [
            {
                "レーン1": left,
                "レーン2": right,
                "共通パス": "README.md",
                "レーン1の定義": ["f"],
                "レーン2の定義": ["f"],
                "判定": "交わる",
            }
            for left, right in (("lane-01", "lane-02"), ("lane-01", "lane-03"), ("lane-02", "lane-03"))
        ]
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    before = path.read_bytes()
    assert _dispatch(str(path), "--mark-integrated", "lane-01", "--output", str(path), "--work-dir", str(repo)) != 0
    assert path.read_bytes() == before


def _integrated_lane_selection(tmp_path: pathlib.Path, notes: pathlib.Path) -> pathlib.Path:
    """本文が書込対象を名指ししないlane-01と、未統合の2レーンを持つ選定を保存して返す。"""
    _awi(notes, "a.md", "モデルの実装を見直す。")
    _awi(notes, "b.md", "`README.md`を変える。")
    _awi(notes, "c.md", "`docs/development/design.md`を変える。")
    return _write_selection(
        tmp_path / "selection.yaml",
        [
            {"WI": "a.md", "レーン": "lane-01", "書込対象": ["src/model.py"]},
            {"WI": "b.md", "レーン": "lane-02", "書込対象": ["README.md"]},
            {"WI": "c.md", "レーン": "lane-03", "書込対象": ["docs/development/design.md"]},
        ],
    )


def test_mark_integrated_accepts_renamed_write_target(
    env: tuple[pathlib.Path, pathlib.Path], tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """レーンの作業が書込対象を改名した作業ツリーでも、そのレーンと所属項目の統合状態を記録できる。

    2026-10-16に、改名前のパスを選定時の前提で判定し直して統合記録が終了コード1で止まった事象の再発防止である。
    """
    repo, notes = env
    path = _integrated_lane_selection(tmp_path, notes)
    assert _dispatch(str(path), "--work-dir", str(repo)) == 0
    (repo / "src/model.py").rename(repo / "src/model2.py")
    assert _dispatch(str(path), "--mark-integrated", "lane-01", "--output", str(path), "--work-dir", str(repo)) == 0
    result = yaml.safe_load(path.read_text(encoding="utf-8"))
    states = {row["レーン"]: row.get("統合状態", "未統合") for row in result["レーンの所要時間"]}
    assert states == {"lane-01": "統合済み", "lane-02": "未統合", "lane-03": "未統合"}
    items = {item["WI"]: item.get("統合状態", "未統合") for item in result["選定"]}
    assert items == {"a.md": "統合済み", "b.md": "未統合", "c.md": "未統合"}
    assert "src/model.py" not in capsys.readouterr().err


def test_integrated_items_do_not_block_later_operations(
    env: tuple[pathlib.Path, pathlib.Path], tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """統合済み項目の作業ツリーの変化は、単独検査・別レーンの統合記録・書込対象の追加を拒否させない。"""
    repo, notes = env
    path = _integrated_lane_selection(tmp_path, notes)
    (repo / "src/model.py").rename(repo / "src/model2.py")
    assert _dispatch(str(path), "--mark-integrated", "lane-01", "--output", str(path), "--work-dir", str(repo)) == 0
    assert _dispatch(str(path), "--work-dir", str(repo)) == 0
    assert _dispatch(str(path), "--mark-integrated", "lane-02", "--output", str(path), "--work-dir", str(repo)) == 0
    addition = tmp_path / "extend.yaml"
    addition.write_text(
        yaml.safe_dump({"選定": [{"WI": "c.md", "書込対象": ["scripts/update_dotfiles.py"]}]}, allow_unicode=True),
        encoding="utf-8",
    )
    output = tmp_path / "extended.yaml"
    assert _dispatch(str(path), "--work-dir", str(repo), "--extend-write-targets", str(addition), "--output", str(output)) == 0
    result = yaml.safe_load(output.read_text(encoding="utf-8"))
    assert result["選定"][2]["書込対象"] == ["docs/development/design.md", "scripts/update_dotfiles.py"]
    assert "src/model.py" not in capsys.readouterr().err


@pytest.mark.parametrize("source", ["mark-integrated", "lane-row-only"])
def test_merged_lane_keeps_integrated_items_exempt(
    env: tuple[pathlib.Path, pathlib.Path], tmp_path: pathlib.Path, source: str
) -> None:
    """統合済みレーンへ追加が合流してレーン行が未統合へ戻っても、既存項目は統合済みのまま判定から外れる。

    `lane-row-only`は項目の欄を持たない保存済みの選定YAMLであり、レーン行の状態から既存項目を区別する。
    """
    repo, notes = env
    path = _integrated_lane_selection(tmp_path, notes)
    if source == "mark-integrated":
        assert _dispatch(str(path), "--mark-integrated", "lane-01", "--output", str(path), "--work-dir", str(repo)) == 0
    else:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        data["レーンの所要時間"][0]["統合状態"] = "統合済み"
        path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    _awi(notes, "d.md", "`pytools/post_apply.py`を変える。")
    added = tmp_path / "added.yaml"
    added.write_text(
        yaml.safe_dump(
            {
                "選定": [
                    {
                        "WI": "d.md",
                        "レーン": "lane-09",
                        "鮮度": {"status": "current", "later_commit_count": 0},
                        "書込対象": ["pytools/post_apply.py"],
                        "統合状態": "統合済み",
                    }
                ],
                "見込みの補正": {"係数": 1, "参照した記録": [], "除外した記録": []},
                "レーンの所要時間": [
                    {
                        "レーン": "lane-09",
                        "実装秒数": 10,
                        "統合秒数": 2,
                        "補正前の実装秒数": 10,
                        "補正前の統合秒数": 2,
                        "根拠": "追加の根拠",
                    }
                ],
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    mapping = tmp_path / "map.json"
    mapping.write_text(json.dumps({"lane-09": "lane-01"}), encoding="utf-8")
    args = ("--merge", str(added), "--lane-map", str(mapping), "--output", str(path), "--work-dir", str(repo))
    assert _dispatch(str(path), *args) == 0
    result = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert result["レーンの所要時間"][0]["統合状態"] == "未統合"
    items = {item["WI"]: item.get("統合状態", "未統合") for item in result["選定"]}
    assert items == {"a.md": "統合済み", "b.md": "未統合", "c.md": "未統合", "d.md": "未統合"}
    (repo / "src/model.py").rename(repo / "src/model2.py")
    assert _dispatch(str(path), "--work-dir", str(repo)) == 0
    # 合流した追加項目は未統合として従来どおり判定する。
    (repo / "pytools/post_apply.py").unlink()
    (repo / "pytools").rmdir()
    assert _dispatch(str(path), "--work-dir", str(repo)) == 1


@pytest.mark.parametrize("state", ["未統合", "統合済み"])
@pytest.mark.parametrize("change", ["directory", "abbreviation", "norm-spec", "public-heading"])
def test_integrated_items_tolerate_worktree_changes(
    env: tuple[pathlib.Path, pathlib.Path],
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    state: str,
    change: str,
) -> None:
    """作業ツリーの状態を前提とする項目ごとの判定は、統合済みの項目へ変化を起こしても違反と入力エラーにならない。

    同じ変化を未統合の項目へ起こした場合は、従来どおり違反（終了コード1）か入力エラー（終了コード2）になる。
    """
    repo, notes = env
    item: dict[str, typing.Any] = {"WI": "a.md", "レーン": "lane-01", "書込対象": ["src/model.py"]}
    if change == "directory":
        _awi(notes, "a.md", "`src/`配下を整理する。")
        item["書込対象"] = ["src/"]
    elif change == "abbreviation":
        # 列挙の後続名は推測した親（src/）に実体が無いため、追跡ファイルの末尾との一意な一致で解決される。
        _track(repo)
        _awi(notes, "a.md", "`src/model.py`と`post_apply.py`を変更する。")
        item["書込対象"] = ["src/model.py", "pytools/post_apply.py"]
    elif change == "public-heading":
        _awi(notes, "a.md", "`README.md`を変更する。")
        item["書込対象"] = []
        item["公開工程の書込対象"] = ["README.md"]
    else:
        _awi(notes, "a.md", "`src/model.py`を変更する。")
    path = _write_selection(tmp_path / "selection.yaml", [item], [{"レーン": "lane-01", "統合状態": state}])
    assert _dispatch(str(path), "--work-dir", str(repo)) == 0
    if change == "directory":
        (repo / "src").rename(repo / "src-new")
        expected, diagnostic = 1, "`書込対象`のパスの不正: src/"
    elif change == "abbreviation":
        _track(repo, "scripts/post_apply.py")
        expected, diagnostic = 2, "略記 `post_apply.py` の候補が複数ある"
    elif change == "norm-spec":
        (repo / "pyproject.toml").write_text(
            '[[tool.agent-toolkit.pick-wi-check.norm-spec]]\nname = "仕様"\npaths = ["src/"]\nrequire-text = ["設計を読む"]\n',
            encoding="utf-8",
        )
        expected, diagnostic = 1, "プロジェクト規範の指定の不足"
    else:
        (repo / "TEST_RULES.md").write_text("# 検査用規範\n\n## 公開の手順\n", encoding="utf-8")
        expected, diagnostic = 1, "`公開工程の書込対象`の根拠不足"
    capsys.readouterr()
    if state == "統合済み":
        assert _dispatch(str(path), "--work-dir", str(repo)) == 0
        assert diagnostic not in capsys.readouterr().err
    else:
        assert _dispatch(str(path), "--work-dir", str(repo)) == expected
        assert diagnostic in capsys.readouterr().err


def test_unintegrated_missing_write_target_still_rejected(
    env: tuple[pathlib.Path, pathlib.Path], tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """未統合の項目が実在せず本文にも新設先にも無い書込対象を持つと、統合記録の呼び出しも従来の診断で拒否する。"""
    repo, notes = env
    path = _integrated_lane_selection(tmp_path, notes)
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["選定"][1]["書込対象"] = ["README.md", "src/missing.py"]
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    before = path.read_bytes()
    assert _dispatch(str(path), "--mark-integrated", "lane-01", "--output", str(path), "--work-dir", str(repo)) == 1
    assert path.read_bytes() == before
    assert (
        "b.md: `書込対象`のパスの不正: src/missing.py: 未作成のパスで、本文の反映先にも`導出した新設先`にも無い"
        in capsys.readouterr().err
    )
    assert _dispatch(str(path), "--work-dir", str(repo)) == 1


__all__ = ["fixture_env"]
