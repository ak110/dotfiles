# pylint: disable=useless-import-alias  # pytestのfixtureを試験モジュールの名前空間へ登録するため。
"""選定の書込対象の追加操作を検証する。"""

import json
import pathlib
import typing

import pytest
import yaml

from agent_toolkit._testing.selection_check_support import (
    _DOTFILES_PYPROJECT,
    _READ_REQUEST,
    _awi,
    _derived,
    _dispatch,
    _norm_spec_selection,
    _overlap_record,
    _write_files,
    _write_selection,
)
from agent_toolkit._testing.selection_check_support import fixture_env as fixture_env


def _generated_overlap_selection(tmp_path: pathlib.Path, repo: pathlib.Path, notes: pathlib.Path) -> pathlib.Path:
    """生成付き保存の公開操作で、書込重複の辺を持つ選定YAMLを作成してそのパスを返す。"""
    _awi(notes, "a.md", "`src/model.py`と`README.md`を変える。")
    _awi(notes, "b.md", "`src/model.py`を変える。")
    candidates = tmp_path / "candidates.jsonl"
    candidates.write_text(
        "".join(
            json.dumps({"filename": name, "staleness": {"status": "current", "later_commit_count": 0}}) + "\n"
            for name in ("a.md", "b.md")
        ),
        encoding="utf-8",
    )
    judgments = tmp_path / "judgments.yaml"
    judgments.write_text(
        yaml.safe_dump(
            {
                "選定": [
                    {"WI": "a.md", "レーン": "lane-01", "書込対象": ["src/model.py", "README.md"]},
                    {"WI": "b.md", "レーン": "lane-01", "書込対象": ["src/model.py"]},
                ],
                "見込みの補正": {"係数": 1, "参照した記録": [], "除外した記録": []},
                "レーンの所要時間": [
                    {
                        "レーン": "lane-01",
                        "実装秒数": 600,
                        "統合秒数": 60,
                        "補正前の実装秒数": 600,
                        "補正前の統合秒数": 60,
                        "根拠": "同じ定義を順に書く",
                    }
                ],
                "結合条件": [
                    {
                        "WI1": "a.md",
                        "WI2": "b.md",
                        "種別": "書込重複",
                        "根拠": {"定義1": ["model"], "定義2": ["model"], "段階比較": "同じ定義を同段階で書く"},
                    }
                ],
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    path = tmp_path / "selection.yaml"
    assert _dispatch(str(judgments), "--candidate-file", str(candidates), "--output", str(path), "--work-dir", str(repo)) == 0
    return path


def _update_items(path: pathlib.Path, update: typing.Callable[[dict[str, dict[str, typing.Any]]], None]) -> dict[str, object]:
    """保存済み選定YAMLの`選定`の項目をWI名で更新して保存し、変更前の`初回配分`を返す。"""
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    update({item["WI"]: item for item in data["選定"]})
    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return data["初回配分"]


@pytest.mark.parametrize("change", ["append", "reorder"])
def test_write_targets_can_be_extended_after_save(
    env: tuple[pathlib.Path, pathlib.Path], tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], change: str
) -> None:
    """保存後の書込対象の追加と並べ替えは、初回配分を書き換えずに終了コード0となる。"""
    repo, notes = env
    path = _generated_overlap_selection(tmp_path, repo, notes)
    initial = yaml.safe_load(path.read_text(encoding="utf-8"))["初回配分"]

    def update(items: dict[str, dict[str, typing.Any]]) -> None:
        if change == "append":
            items["b.md"]["書込対象"].append("docs/development/design.md")
        else:
            items["a.md"]["書込対象"].reverse()

    assert _update_items(path, update) == initial
    capsys.readouterr()
    assert _dispatch(str(path), "--work-dir", str(repo)) == 0, capsys.readouterr().err


@pytest.mark.parametrize("legacy", [False, True])
def test_public_extend_write_targets(
    env: tuple[pathlib.Path, pathlib.Path], tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], legacy: bool
) -> None:
    """既存・新設・共有の追加を一度で保存し、初回配分と他項目を保持して公開操作へ再入力する。"""
    repo, notes = env
    _awi(notes, "a.md", "`README.md`を変更し、結果保存用の入口を新設する。")
    _awi(notes, "b.md", "`src/model.py`を変更する。")
    keys = ("awi", "lane", "write_files") if legacy else ("WI", "レーン", "書込対象")
    path = _write_selection(
        tmp_path / "selection.yaml",
        [
            {keys[0]: name, keys[1]: lane, keys[2]: [target]}
            for name, lane, target in (("a.md", "lane-01", "README.md"), ("b.md", "lane-02", "src/model.py"))
        ],
    )
    before = yaml.safe_load(path.read_text(encoding="utf-8"))
    addition = tmp_path / "extend.yaml"
    record = _derived("src/save.py")
    overlap = _overlap_record("src/model.py")
    addition.write_text(
        yaml.safe_dump(
            {
                "選定": [
                    {
                        "WI": "a.md",
                        "書込対象": ["docs/development/design.md", "src/save.py", "src/model.py", "README.md"],
                        "導出した新設先": [record],
                    }
                ],
                "レーン間の重なり": [overlap],
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    # 同じ入力を再送しても、パスと記録の重複を増やさず同じ選定を保存する。
    for _ in range(2):
        assert (
            _dispatch(str(path), "--work-dir", str(repo), "--extend-write-targets", str(addition), "--output", str(path)) == 0
        )
        result = yaml.safe_load(path.read_text(encoding="utf-8"))
        items = result["decisions" if legacy else "選定"]
        assert items[0][keys[2]] == ["README.md", "docs/development/design.md", "src/save.py", "src/model.py"]
        assert items[0]["導出した新設先"] == [record]
        assert items[1] == before["decisions" if legacy else "選定"][1]
        assert result["レーン間の重なり"] == [overlap]
        for key, value in before.items():
            if key not in {"選定", "decisions"}:
                assert result[key] == value
        assert _dispatch(str(path), "--work-dir", str(repo)) == 0
    assert not capsys.readouterr().err


@pytest.mark.parametrize("kind", ["new", "overlap", "new-record-fields"])
def test_public_extend_requires_new_path_and_overlap_records(
    env: tuple[pathlib.Path, pathlib.Path], tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], kind: str
) -> None:
    """必要記録の欠落を診断し、記録を補った同じ追加を成功させる。"""
    repo, notes = env
    _awi(notes, "a.md", "`README.md`を変更し、結果保存用の入口を新設する。")
    _awi(notes, "b.md", "`src/model.py`を変更する。")
    path = _write_selection(
        tmp_path / "selection.yaml",
        [
            {"WI": "a.md", "レーン": "lane-01", "書込対象": ["README.md"]},
            {"WI": "b.md", "レーン": "lane-02", "書込対象": ["src/model.py"]},
        ],
    )
    before = path.read_bytes()
    addition = tmp_path / "extend.yaml"
    update: dict[str, typing.Any] = {"WI": "a.md", "書込対象": ["src/model.py" if kind == "overlap" else "src/save.py"]}
    if kind == "new-record-fields":
        update["導出した新設先"] = [{"パス": "src/save.py"}]
    data: dict[str, typing.Any] = {"選定": [update]}
    addition.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    assert _dispatch(str(path), "--work-dir", str(repo), "--extend-write-targets", str(addition), "--output", str(path)) != 0
    assert path.read_bytes() == before
    diagnostic = capsys.readouterr().err
    assert "次の操作:" in diagnostic
    if kind == "overlap":
        assert "src/model.py" in diagnostic and "重複パスの記録不足" in diagnostic
        assert "双方の定義と判定" in diagnostic
        data["レーン間の重なり"] = [_overlap_record("src/model.py")]
    else:
        assert "導出した新設先" in diagnostic
        for field in ("パス", "反映範囲", "要求", "配置根拠"):
            assert field in diagnostic
        update["導出した新設先"] = [_derived("src/save.py")]
    addition.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    assert _dispatch(str(path), "--work-dir", str(repo), "--extend-write-targets", str(addition), "--output", str(path)) == 0
    assert _dispatch(str(path), "--work-dir", str(repo)) == 0


@pytest.mark.parametrize("kind", ["new", "overlap"])
def test_public_extend_adds_records_without_write_targets(
    env: tuple[pathlib.Path, pathlib.Path], tmp_path: pathlib.Path, kind: str
) -> None:
    """新設先の記録だけ・重なりの記録だけの追記でも、書込対象を保持して保存へ到達する。"""
    repo, notes = env
    target = "src/save.py" if kind == "new" else "src/model.py"
    _awi(notes, "a.md", "結果保存用の入口を新設する。")
    _awi(notes, "b.md", "`src/model.py`を変更する。")
    path = _write_selection(
        tmp_path / "selection.yaml",
        [
            {"WI": "a.md", "レーン": "lane-01", "書込対象": [target]},
            {"WI": "b.md", "レーン": "lane-02", "書込対象": ["src/model.py"]},
        ],
    )
    addition = tmp_path / "extend.yaml"
    data = (
        {"選定": [{"WI": "a.md", "導出した新設先": [_derived(target)]}]}
        if kind == "new"
        else {"レーン間の重なり": [_overlap_record(target)]}
    )
    addition.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    assert _dispatch(str(path), "--work-dir", str(repo), "--extend-write-targets", str(addition), "--output", str(path)) == 0
    assert _dispatch(str(path), "--work-dir", str(repo)) == 0


@pytest.mark.parametrize(
    "problem",
    [
        "unknown-wi",
        "unknown-field",
        "type",
        "norm-spec-type",
        "invalid-path",
        "invalid-yaml",
        "invalid-encoding",
        "missing-file",
        "other-wi",
    ],
)
@pytest.mark.parametrize("in_place", [False, True])
def test_public_extend_failure_preserves_selection(
    env: tuple[pathlib.Path, pathlib.Path],
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    problem: str,
    in_place: bool,
) -> None:
    """追加入力の不正と追加先以外の本文違反でも、元選定と既存保存先をバイト単位で保持する。"""
    repo, notes = env
    _awi(notes, "a.md", "`README.md`を変更する。")
    _awi(notes, "b.md", "`src/model.py`を変更する。")
    path = _write_selection(
        tmp_path / "selection.yaml",
        [
            {"WI": "a.md", "レーン": "lane-01", "書込対象": ["README.md"]},
            {"WI": "b.md", "レーン": "lane-01", "書込対象": ["src/"] if problem == "other-wi" else ["src/model.py"]},
        ],
    )
    addition = tmp_path / "extend.yaml"
    update: dict[str, typing.Any] = {"WI": "a.md", "書込対象": ["docs/development/design.md"]}
    if problem == "unknown-wi":
        update["WI"] = "absent.md"
    elif problem == "unknown-field":
        update["レーン"] = "lane-02"
    elif problem == "type":
        update["書込対象"] = "src/model.py"
    elif problem == "norm-spec-type":
        update["プロジェクト規範の指定"] = ["README.md"]
    elif problem == "invalid-path":
        update["書込対象"] = ["src/../escape.py"]
    addition.write_text(
        "選定: [" if problem == "invalid-yaml" else yaml.safe_dump({"選定": [update]}, allow_unicode=True), encoding="utf-8"
    )
    if problem == "invalid-encoding":
        addition.write_bytes(b"\xff")
    elif problem == "missing-file":
        addition.unlink()
    output = path if in_place else tmp_path / "output.yaml"
    if not in_place:
        output.write_text("保存先の既存データ\n", encoding="utf-8")
    original, saved = path.read_bytes(), output.read_bytes()
    assert _dispatch(str(path), "--work-dir", str(repo), "--extend-write-targets", str(addition), "--output", str(output)) != 0
    assert path.read_bytes() == original and output.read_bytes() == saved
    diagnostic = capsys.readouterr().err
    assert "次の操作:" in diagnostic and "Traceback" not in diagnostic
    if problem == "other-wi":
        assert "b.md: 広すぎる範囲: src/" in diagnostic


@pytest.mark.parametrize(
    "option", [None, "--merge", "--lane-map", "--candidate-file", "--mark-integrated", "--lane-cost-updates", "--body-wi"]
)
def test_public_extend_rejects_incompatible_options(tmp_path: pathlib.Path, option: str | None) -> None:
    """更新モードの混在と保存先の欠落を、選定や入力を読む前に拒否する。"""
    args = [str(tmp_path / "selection.yaml"), "--extend-write-targets", str(tmp_path / "extend.yaml")]
    if option:
        args.extend(["--output", str(tmp_path / "output.yaml"), option, "value"])
    assert _dispatch(*args) == 2


def test_write_overlap_edge_reports_missing_overlap(
    env: tuple[pathlib.Path, pathlib.Path], tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """両端の書込対象が重ならなくなった書込重複の辺は、2つのWI名と重ならない値を示して拒否する。"""
    repo, notes = env
    path = _generated_overlap_selection(tmp_path, repo, notes)

    def update(items: dict[str, dict[str, typing.Any]]) -> None:
        items["a.md"]["書込対象"] = ["README.md"]
        items["a.md"]["書き込まない反映先"] = ["src/model.py"]

    _update_items(path, update)
    capsys.readouterr()
    assert _dispatch(str(path), "--work-dir", str(repo)) == 1
    lines = [line for line in capsys.readouterr().err.splitlines() if "結合根拠が成立しない" in line]
    assert len(lines) == 1
    assert "a.md" in lines[0] and "b.md" in lines[0] and "重ならない" in lines[0]
    assert "README.md" in lines[0] and "src/model.py" in lines[0]


def test_legacy_write_overlap_evidence_is_accepted(
    env: tuple[pathlib.Path, pathlib.Path], tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """変更前の版が根拠へ複製したパスと段階は受理し、判定には使わない。"""
    repo, notes = env
    path = _generated_overlap_selection(tmp_path, repo, notes)
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    evidence = data["初回配分"]["不可分成分"][0]["結合条件"][0]["根拠"]
    # 変更前の生成付き保存が書いた形。値は保存後に書込対象を並べ替えた状態と一致しない。
    evidence.update({"パス1": ["README.md", "src/model.py"], "パス2": ["src/model.py"], "段階1": 1, "段階2": 1})
    data["選定"][0]["書込対象"].append("docs/development/design.md")
    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    capsys.readouterr()
    assert _dispatch(str(path), "--work-dir", str(repo)) == 0, capsys.readouterr().err


@pytest.mark.parametrize("with_spec", [True, False])
def test_public_extend_updates_project_norm_spec_with_the_added_agent_document(
    tmp_path: pathlib.Path, env: tuple[pathlib.Path, pathlib.Path], capsys: pytest.CaptureFixture[str], with_spec: bool
) -> None:
    """本リポジトリの設定で、エージェント向け文書の追加と同じ入力で規範指定を更新すれば保存される。

    規範指定を受け取れない追加操作は全体検査で拒否され、メインが選定YAMLを手で書き換えて再実行するしかなかった。
    規範指定を渡さない同じ追加は、規範指定の不足（終了コード1）で拒否し、選定YAMLを変えない。
    """
    repo, notes = env
    (repo / "pyproject.toml").write_text(_DOTFILES_PYPROJECT.read_text(encoding="utf-8"), encoding="utf-8")
    _write_files(repo, "agent-toolkit/share/exec.parent.md", ".claude/skills/edit/SKILL.md")
    _awi(notes, "norm.md", "`agent-toolkit/share/exec.parent.md`を変える。")
    decisions: list[dict[str, typing.Any]] = [
        {"WI": "norm.md", "レーン": "lane-01", "書込対象": ["agent-toolkit/share/exec.parent.md"]}
    ]
    path = _norm_spec_selection(tmp_path, decisions, {"norm.md": f"agent-toolkit/share/exec.parent.md。{_READ_REQUEST}"})
    assert _dispatch("--work-dir", str(repo), str(path)) == 0, capsys.readouterr().err
    before = path.read_bytes()
    spec = f"agent-toolkit/share/exec.parent.md、.claude/skills/edit/SKILL.md。{_READ_REQUEST}"
    update: dict[str, typing.Any] = {"WI": "norm.md", "書込対象": [".claude/skills/edit/SKILL.md"]}
    if with_spec:
        update["プロジェクト規範の指定"] = spec
    addition = tmp_path / "extend.yaml"
    addition.write_text(yaml.safe_dump({"選定": [update]}, allow_unicode=True), encoding="utf-8")
    capsys.readouterr()
    code = _dispatch(str(path), "--work-dir", str(repo), "--extend-write-targets", str(addition), "--output", str(path))
    error = capsys.readouterr().err
    if with_spec:
        assert code == 0, error
        item = yaml.safe_load(path.read_text(encoding="utf-8"))["選定"][0]
        assert item["書込対象"] == ["agent-toolkit/share/exec.parent.md", ".claude/skills/edit/SKILL.md"]
        assert item["プロジェクト規範の指定"] == spec
        assert _dispatch("--work-dir", str(repo), str(path)) == 0
    else:
        assert code == 1
        assert (
            "norm.md: プロジェクト規範の指定の不足: 規範変更の対象ファイル: 欠けた記載: .claude/skills/edit/SKILL.md" in error
        )
        assert "追加入力の該当項目へ更新後の`プロジェクト規範の指定`" in error
        assert path.read_bytes() == before


@pytest.mark.parametrize(
    ("reflected", "excluded", "added", "kept"),
    [
        ("`README.md`を変更し、`src/model.py`を参照する。", "src/model.py", "src/model.py", []),
        ("`README.md`を変更し、`docs/`は変更しない。", "docs/", "docs/development/design.md", ["docs/"]),
    ],
    ids=["same-path", "enclosing-range"],
)
def test_public_extend_removes_the_added_path_from_non_write_targets(
    tmp_path: pathlib.Path,
    env: tuple[pathlib.Path, pathlib.Path],
    capsys: pytest.CaptureFixture[str],
    reflected: str,
    excluded: str,
    added: str,
    kept: list[str],
) -> None:
    """非書込に分類済みのパスを書込対象へ加えると、同じ保存で非書込から除く。

    追加入力はその項目がパスを書き込む宣言であり、非書込の分類を残すと区分間の重複になって手で直すしかない。
    非書込の範囲が追加パスを真に含む組は、全体検査が受理する選定なので範囲を残す。
    """
    repo, notes = env
    _awi(notes, "a.md", reflected)
    path = _write_selection(
        tmp_path / "selection.yaml",
        [{"WI": "a.md", "レーン": "lane-01", "書込対象": ["README.md"], "書き込まない反映先": [excluded]}],
    )
    assert _dispatch("--work-dir", str(repo), str(path)) == 0, capsys.readouterr().err
    addition = tmp_path / "extend.yaml"
    addition.write_text(yaml.safe_dump({"選定": [{"WI": "a.md", "書込対象": [added]}]}, allow_unicode=True), encoding="utf-8")
    code = _dispatch(str(path), "--work-dir", str(repo), "--extend-write-targets", str(addition), "--output", str(path))
    assert code == 0, capsys.readouterr().err
    item = yaml.safe_load(path.read_text(encoding="utf-8"))["選定"][0]
    assert item["書込対象"] == ["README.md", added]
    assert item["書き込まない反映先"] == kept
    assert _dispatch("--work-dir", str(repo), str(path)) == 0


__all__ = ["fixture_env"]
