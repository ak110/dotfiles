# pylint: disable=useless-import-alias  # pytestのfixtureを試験モジュールの名前空間へ登録するため。
"""選定の導出した新設先を検証する。"""

import pathlib
import typing

import pytest
import yaml

from agent_toolkit._testing import git_repository
from agent_toolkit._testing.selection_check_support import _awi, _derived, _dispatch, _run, _write_selection
from agent_toolkit._testing.selection_check_support import fixture_env as fixture_env


@pytest.mark.parametrize("section", ["書込対象", "公開工程の書込対象"])
@pytest.mark.parametrize("documentation", [False, True])
def test_public_check_accepts_related_fixture_outside_body_directories(
    tmp_path: pathlib.Path,
    env: tuple[pathlib.Path, pathlib.Path],
    section: str,
    documentation: bool,
) -> None:
    """実装に必要な別配置のテストコードは、本文への文書範囲の併記にかかわらず個別登録できる。"""
    repo, notes = env
    (repo / "tests").mkdir()
    _awi(notes, "a.md", "`src/model.py`を是正する。" + ("`docs/`も更新する。" if documentation else ""))
    record = _derived("tests/model_test.py", "tests/")
    record["要求"] = "modelの是正結果を検証する"
    record["配置根拠"] = "テストコードはtestsへ配置する対象リポジトリの方針に従う"
    item: dict[str, typing.Any] = {
        "WI": "a.md",
        "レーン": "lane-01",
        "書込対象": ["src/model.py"],
        "導出した新設先": [record],
    }
    item.setdefault(section, []).append(record["パス"])
    if documentation:
        item["書込対象"].append("docs/")
    path = _write_selection(
        tmp_path / "selection.yaml",
        [item],
        [{"レーン": "lane-01", "根拠": "対象リポジトリの規範AGENTS.mdの公開の節でtests/model_test.pyを所有する"}],
    )
    assert _dispatch(str(path), "--work-dir", str(repo)) == 0
    saved = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert record["パス"] in saved["選定"][0][section]


@pytest.mark.parametrize("body_scope", [False, True])
@pytest.mark.parametrize("origin", ["https://other.example/test/repo.git", "https://github.com/test/renamed.git"])
def test_public_check_rejects_migrated_repository_until_metadata_is_updated(
    tmp_path: pathlib.Path,
    env: tuple[pathlib.Path, pathlib.Path],
    body_scope: bool,
    origin: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """同じHEADのホスト移転・パス改名でも、保存値を揃えるまで初期・追加の検査で拒否する。"""
    repo, notes = env
    git_repository.commit_all(repo, "初期状態")
    original_head = git_repository.git_output(repo, "rev-parse", "HEAD")
    git_repository.run_git(repo, "remote", "set-url", "origin", origin)
    assert git_repository.git_output(repo, "rev-parse", "HEAD") == original_head
    _awi(notes, "a.md", "`src/model.py`を是正する。")
    path = _write_selection(
        tmp_path / "selection.yaml",
        [{"WI": "a.md", "レーン": "lane-01", "書込対象": ["src/model.py"]}],
    )
    args = [str(path), "--work-dir", str(repo), *(["--body-wi", "a.md"] if body_scope else [])]
    assert _dispatch(*args) == 1
    diagnostic = capsys.readouterr().err
    assert "a.md: target_repoとworktreeのoriginが一致しない" in diagnostic
    assert "保存値=github.com/test/repo" in diagnostic
    assert str(repo) in diagnostic and "atk wi edit" in diagnostic and "start-processing前" in diagnostic
    source = notes / "processing/a.md"
    source.write_text(source.read_text(encoding="utf-8").replace("github.com/test/repo", origin), encoding="utf-8")
    assert _dispatch(*args) == 0


def test_public_check_normalizes_equivalent_repository_addresses(
    tmp_path: pathlib.Path,
    env: tuple[pathlib.Path, pathlib.Path],
) -> None:
    """SSH短縮形とHTTPSの構文差は所属の不一致にしない。"""
    repo, notes = env
    git_repository.run_git(repo, "remote", "set-url", "origin", "git@GitHub.com:TEST/REPO.git")
    _awi(notes, "a.md", "`src/model.py`を変更する。")
    assert _run(tmp_path, repo, [{"WI": "a.md", "レーン": "lane-01", "書込対象": ["src/model.py"]}]) == 0


@pytest.mark.parametrize("section", ["書込対象", "公開工程の書込対象"])
def test_public_check_accepts_derived_new_paths(
    tmp_path: pathlib.Path, env: tuple[pathlib.Path, pathlib.Path], section: str, capsys: pytest.CaptureFixture[str]
) -> None:
    """名前のない要求から導出した個別新設は、同じ親の別レーンとも独立して受理する。"""
    repo, notes = env
    items = []
    costs = []
    for number in (1, 2):
        wi, lane, new = f"a{number}.md", f"lane-0{number}", f"src/save{number}.py"
        _awi(notes, wi, "`src/`の結果保存用の入口を新設する。")
        items.append({"WI": wi, "レーン": lane, "書込対象": [], section: [new], "導出した新設先": [_derived(new)]})
        costs.append({"レーン": lane, "根拠": f"対象リポジトリの規範AGENTS.mdの公開の節で{new}を所有する"})
    path = _write_selection(tmp_path / "selection.yaml", items, costs)
    assert _dispatch(str(path), "--work-dir", str(repo)) == 0
    assert not capsys.readouterr().err


@pytest.mark.parametrize("section", ["書込対象", "公開工程の書込対象"])
def test_public_mark_integrated_preserves_created_derived_paths(
    tmp_path: pathlib.Path, env: tuple[pathlib.Path, pathlib.Path], section: str, capsys: pytest.CaptureFixture[str]
) -> None:
    """新設先の作成をまたいでも、統合状態の反復保存と全WI対象の判定で判断履歴を保つ。"""
    repo, notes = env
    _awi(notes, "a.md", "`src/`へ保存用入口を新設する。")
    record = _derived("src/save.py")
    path = _write_selection(
        tmp_path / "selection.yaml",
        [{"WI": "a.md", "レーン": "lane-01", "書込対象": [], section: [record["パス"]], "導出した新設先": [record]}],
        [{"レーン": "lane-01", "根拠": "対象リポジトリの規範AGENTS.mdの公開の節でsrc/save.pyを所有する"}],
    )
    assert _dispatch(str(path), "--work-dir", str(repo)) == 0
    (repo / record["パス"]).write_text("# 保存入口\n", encoding="utf-8")
    for _ in range(2):
        assert _dispatch(str(path), "--work-dir", str(repo), "--mark-integrated", "lane-01", "--output", str(path)) == 0
        result = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert result["レーンの所要時間"][0]["統合状態"] == "統合済み"
        assert result["選定"][0]["導出した新設先"] == [record]
        assert result["選定"][0][section] == [record["パス"]]
        assert _dispatch(str(path), "--work-dir", str(repo)) == 0
    assert not capsys.readouterr().err


def test_public_check_derived_paths_from_answered_uwi(
    tmp_path: pathlib.Path, env: tuple[pathlib.Path, pathlib.Path], capsys: pytest.CaptureFixture[str]
) -> None:
    """パスを示さない回答からも限定調査の範囲を渡せる。意味は本文と根拠で検収する。"""
    repo, notes = env
    (notes / "processing/u.md").write_text(
        "---\ntype: uwi\n---\n\n## 回答\n\n結果を保存する入口も新設する。\n", encoding="utf-8"
    )
    path = _write_selection(
        tmp_path / "selection.yaml",
        [{"WI": "u.md", "レーン": "lane-01", "書込対象": ["src/save.py"], "導出した新設先": [_derived("src/save.py")]}],
    )
    assert _dispatch(str(path), "--work-dir", str(repo)) == 0
    assert not capsys.readouterr().err


@pytest.mark.parametrize(
    "case,code",
    [
        ("missing-reason", 2),
        ("unknown-field", 2),
        ("not-list", 2),
        ("empty", 2),
        ("identifier", 1),
        ("absolute", 1),
        ("escape", 1),
        ("glob", 1),
        ("missing-parent", 1),
        ("outside", 1),
        ("missing-write", 1),
        ("excluded-only", 1),
        ("duplicate", 1),
        ("directory", 1),
        ("missing-individual", 0),
        ("remaining", 0),
    ],
)
def test_public_check_rejects_invalid_derived_new_paths(
    tmp_path: pathlib.Path, env: tuple[pathlib.Path, pathlib.Path], case: str, code: int, capsys: pytest.CaptureFixture[str]
) -> None:
    """導出記録の不正を拒否し、他の本文パスの未列挙は受理する。"""
    repo, notes = env
    _awi(notes, "a.md", "`src/`へ保存用入口を新設する。")
    record = _derived("src/save.py")
    item: dict[str, typing.Any] = {"WI": "a.md", "レーン": "lane-01", "書込対象": [record["パス"]], "導出した新設先": [record]}
    if case == "missing-reason":
        del record["配置根拠"]
    elif case == "unknown-field":
        record["unknown"] = "extra"
    elif case == "not-list":
        item["導出した新設先"] = record
    elif case == "empty":
        record["要求"] = " "
    elif case in {"identifier", "absolute", "escape", "glob", "missing-parent", "outside"}:
        record["パス"] = {
            "identifier": "SAVE_NAME",
            "absolute": "/save.py",
            "escape": "../save.py",
            "glob": "src/save?.py",
            "missing-parent": "src/missing/save.py",
            "outside": "src-old/save.py",
        }[case]
        item["書込対象"] = [record["パス"]]
    elif case in {"missing-write", "excluded-only"}:
        item["書込対象"] = []
        if case == "excluded-only":
            item["書き込まない反映先"] = [record["パス"]]
    elif case == "duplicate":
        item["導出した新設先"].append(dict(record))
    elif case == "directory":
        (repo / record["パス"]).mkdir()
    elif case == "missing-individual":
        _awi(notes, "a.md", "`src/`へ保存入口を新設し、`README.md`も変える。")
    else:
        _awi(notes, "a.md", "`src/`へ保存入口を新設し、`docs/`にも入口を新設する。")
    path = _write_selection(tmp_path / "selection.yaml", [item])
    assert _dispatch(str(path), "--work-dir", str(repo)) == code
    diagnostic = capsys.readouterr().err
    if code:
        assert "次の操作:" in diagnostic
    else:
        assert not diagnostic
        return
    before = path.read_bytes()
    if code == 1:
        # 記録するレーンの項目は統合済みとして作業ツリーの状態を前提とする判定から外れ、統合状態を保存する。
        assert _dispatch(str(path), "--work-dir", str(repo), "--mark-integrated", "lane-01", "--output", str(path)) == 0
        assert yaml.safe_load(path.read_text(encoding="utf-8"))["選定"][0]["統合状態"] == "統合済み"
        path.write_bytes(before)
        capsys.readouterr()
    else:
        assert _dispatch(str(path), "--work-dir", str(repo), "--mark-integrated", "lane-01", "--output", str(path)) == code
        assert path.read_bytes() == before
        assert "次の操作:" in capsys.readouterr().err


__all__ = ["fixture_env"]
