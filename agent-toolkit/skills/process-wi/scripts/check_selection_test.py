"""選定結果の被覆・構造と、チェック専用操作の互換性を確かめる。"""

import argparse
import json
import pathlib
import re
import typing

import check_selection
import pytest
import yaml

from agent_toolkit._atk import run_script  # noqa: E402  # pylint: disable=wrong-import-position,import-error
from agent_toolkit._testing import git_repository

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
    selection: dict[str, object] = {"decisions" if legacy else "選定": decisions, "レーンの所要時間": costs}
    if any(isinstance(row.get("段階"), int) and row["段階"] >= 2 for row in costs):
        selection["単一段階案の完了見込み秒数"] = 1320
    path.write_text(yaml.safe_dump(selection, allow_unicode=True), encoding="utf-8")
    return path


def _run(tmp_path: pathlib.Path, repo: pathlib.Path, decisions: list[dict[str, typing.Any]]) -> int:
    """選定結果を保存して検証を実行し、終了コードを返す。"""
    selection = _write_selection(tmp_path / "selection.yaml", decisions)
    return check_selection.main(["--work-dir", str(repo), str(selection)])


def _dispatch(*args: str) -> int:
    """`atk run-script pick-wi-check`の公開名から検証を実行し、終了コードを返す。"""
    return run_script.dispatch(argparse.Namespace(script_name="pick-wi-check", script_args=["--", *args]))


@pytest.mark.parametrize("option", ["--merge", "--lane-map", "--output"])
def test_merge_options_must_be_provided_together(tmp_path: pathlib.Path, option: str) -> None:
    """統合モードの不完全な呼出は、ファイルを読む前に入力誤りとして拒否する。"""
    with pytest.raises(SystemExit) as error:
        check_selection.main([str(tmp_path / "selection.yaml"), option, str(tmp_path / "other")])
    assert error.value.code == 2


def test_cost_updates_require_merge(tmp_path: pathlib.Path) -> None:
    """費用更新単独の呼出を、ファイル読込前に拒否する。"""
    assert _dispatch(str(tmp_path / "selection.yaml"), "--lane-cost-updates", str(tmp_path / "updates.json")) == 2


def test_public_check_requires_single_stage_estimate(
    tmp_path: pathlib.Path, env: tuple[pathlib.Path, pathlib.Path], capsys: pytest.CaptureFixture[str]
) -> None:
    """同じ段階案の比較値だけを変え、欠落拒否・0の受理・成功要約を観測する。"""
    repo, notes = env
    for wi in ("a.md", "b.md"):
        _awi(notes, wi, "`src/model.py`を変える。")
    path = _write_selection(
        tmp_path / "selection.yaml",
        [{"WI": wi, "レーン": lane, "書込対象": ["src/model.py"]} for wi, lane in (("a.md", "lane-01"), ("b.md", "lane-02"))],
        [{"レーン": "lane-01"}, {"レーン": "lane-02", "段階": 2, "先行レーン": ["lane-01"]}],
    )
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["レーン間の重なり"] = [_overlap_record("src/model.py", "交わる")]
    field = "単一段階案の完了見込み秒数"
    for value, code in ((None, 1), (0, 0), (1320, 0)):
        data.pop(field, None)
        if value is not None:
            data[field] = value
        path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
        assert _dispatch(str(path), "--work-dir", str(repo), "--body-wi", "a.md") == code
        result = capsys.readouterr()
        if code:
            assert field in result.err and "次の操作:" in result.err
            assert "配分の説明" in result.err
        else:
            assert f"{field}: {value}" in result.out and not result.err
    path = _write_selection(tmp_path / "single.yaml", [{"WI": "a.md", "レーン": "lane-01", "書込対象": ["src/model.py"]}])
    assert _dispatch(str(path), "--work-dir", str(repo)) == 0
    assert field not in capsys.readouterr().out


@pytest.mark.parametrize("value", ["100", True, -1, [], None])
def test_public_check_rejects_invalid_single_stage_estimate(
    tmp_path: pathlib.Path, env: tuple[pathlib.Path, pathlib.Path], capsys: pytest.CaptureFixture[str], value: object
) -> None:
    """比較値の不正型は段階の有無に依存せず入力違反とする。"""
    repo, _notes = env
    path = _write_selection(tmp_path / "selection.yaml", [])
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["単一段階案の完了見込み秒数"] = value
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    assert _dispatch(str(path), "--work-dir", str(repo)) == 2
    message = capsys.readouterr().err
    assert "単一段階案の完了見込み秒数" in message and "型" in message and "次の操作:" in message


def _derived(path: str, scope: str = "src/") -> dict[str, str]:
    """名前を指定しない要求から限定調査で確定した新設先の記録を返す。"""
    return {
        "パス": path,
        "反映範囲": scope,
        "要求": "結果を保存する入口を新設する",
        "配置根拠": "既存model.pyの保存処理と同じ配置にする",
    }


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
        ("missing-individual", 1),
        ("remaining", 1),
    ],
)
def test_public_check_rejects_invalid_derived_new_paths(
    tmp_path: pathlib.Path, env: tuple[pathlib.Path, pathlib.Path], case: str, code: int, capsys: pytest.CaptureFixture[str]
) -> None:
    """根拠・パス・要求範囲・書込への対応の不成立と、他の反映先の被覆不足を区別する。"""
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
    elif case == "missing-individual":
        _awi(notes, "a.md", "`src/`へ保存入口を新設し、`README.md`も変える。")
    else:
        _awi(notes, "a.md", "`src/`へ保存入口を新設し、`docs/`にも入口を新設する。")
    path = _write_selection(tmp_path / "selection.yaml", [item])
    assert _dispatch(str(path), "--work-dir", str(repo)) == code
    diagnostic = capsys.readouterr().err
    assert "次の操作:" in diagnostic
    if case in {"missing-individual", "remaining"}:
        assert "未被覆" in diagnostic
        item["書込対象"].append("README.md" if case == "missing-individual" else "docs/")
        _write_selection(path, [item])
        assert _dispatch(str(path), "--work-dir", str(repo)) == 0


def _overlap_record(path: str, judgment: str = "交わらない") -> dict[str, typing.Any]:
    """2レーンの共有パスに対する定義と判定を返す。"""
    return {
        "レーン1": "lane-01",
        "レーン2": "lane-02",
        "共通パス": path,
        "レーン1の定義": ["入力の検査"],
        "レーン2の定義": ["結果の表示"],
        "判定": judgment,
    }


@pytest.mark.parametrize("section", ["書込対象", "公開工程の書込対象"])
@pytest.mark.parametrize("entry", ["LOGS_DIR", "AZURE_CLIENT_ID", "access.log", "src", "a.md", "src/new.py"])
def test_public_selection_rejects_non_paths(
    tmp_path: pathlib.Path,
    env: tuple[pathlib.Path, pathlib.Path],
    capsys: pytest.CaptureFixture[str],
    section: str,
    entry: str,
) -> None:
    """書込区分では識別子・存在しない新設先・末尾/なしディレクトリを拒否する。"""
    repo, notes = env
    _awi(notes, "a.md", "`README.md`を変更する。")
    item = {"WI": "a.md", "レーン": "lane-01", "書込対象": [], "書き込まない反映先": ["README.md"], section: [entry]}
    path = _write_selection(tmp_path / "selection.yaml", [item])
    assert _dispatch(str(path), "--work-dir", str(repo)) == 1
    diagnostic = capsys.readouterr().err
    assert f"a.md: `{section}`のパスの不正: {entry}" in diagnostic
    assert "次の操作:" in diagnostic
    assert "導出記録が正当な新設先" in diagnostic


@pytest.mark.parametrize(
    ("case", "expected"),
    [
        ("missing", 1),
        ("separate", 0),
        ("same-definition", 1),
        ("extra-path", 1),
        ("duplicate", 1),
        ("reversed-duplicate", 1),
        ("serial-separate", 1),
        ("serial-intersect", 0),
        ("parallel-intersect", 1),
        ("serial-unshared", 1),
        ("extra-lane", 1),
        ("missing-definition", 2),
    ],
)
def test_public_selection_contract_cases(
    tmp_path: pathlib.Path,
    env: tuple[pathlib.Path, pathlib.Path],
    capsys: pytest.CaptureFixture[str],
    case: str,
    expected: int,
) -> None:
    """構造化した共有記録と実際のパス・段階・先行関係の整合を公開コマンドで確かめる。"""
    repo, notes = env
    shared = "src/model.py"
    other = "README.md" if case == "serial-unshared" else shared
    _awi(notes, "a.md", f"`{shared}`を変更する。")
    _awi(notes, "b.md", f"`{other}`を変更する。")
    costs: list[dict[str, typing.Any]] = [{"レーン": "lane-01"}, {"レーン": "lane-02"}]
    if case.startswith("serial-"):
        costs[1].update({"段階": 2, "先行レーン": ["lane-01"]})
    path = _write_selection(
        tmp_path / "selection.yaml",
        [
            {"WI": "a.md", "レーン": "lane-01", "書込対象": [shared]},
            {"WI": "b.md", "レーン": "lane-02", "書込対象": [other]},
        ],
        costs,
    )
    record = _overlap_record(shared, "交わる" if case.endswith("intersect") else "交わらない")
    records = [] if case in {"missing", "serial-unshared"} else [record]
    if case == "same-definition":
        record["レーン2の定義"] = record["レーン1の定義"]
    elif case == "extra-path":
        record["共通パス"] = "README.md"
    elif case == "extra-lane":
        record["レーン2"] = "lane-03"
    elif case == "missing-definition":
        del record["レーン2の定義"]
    elif case in {"duplicate", "reversed-duplicate"}:
        records.append(dict(record))
        if case == "reversed-duplicate":
            records[1].update({"レーン1": "lane-02", "レーン2": "lane-01"})
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["レーン間の重なり"] = records
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    assert _dispatch(str(path), "--work-dir", str(repo)) == expected
    diagnostic = capsys.readouterr().err
    assert ("次の操作:" in diagnostic) == bool(expected)
    if case in {"serial-unshared", "serial-separate"}:
        assert "根拠の無い直列化" in diagnostic


def test_public_selection_preserves_path_context(
    tmp_path: pathlib.Path,
    env: tuple[pathlib.Path, pathlib.Path],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """ファイル直後の識別子を除き、コマンド内ディレクトリと明示した新設先は受理する。"""
    repo, notes = env
    _write_files(repo, "docs/guide.md", "docs/ops/a.md", "src/a.py")
    _awi(
        notes,
        "a.md",
        "`docs/guide.md`の`views.user.public`の許可行は変えない。"
        "`src/a.py`の`globalThis.config`を使う。`git grep -n foo -- docs/ops`は一致0件。",
    )
    body = (notes / "processing/a.md").read_text(encoding="utf-8")
    assert check_selection.reflected_paths(body, repo) == {"docs/guide.md", "src/a.py", "docs/ops/"}
    path = _write_selection(
        tmp_path / "selection.yaml",
        [{"WI": "a.md", "レーン": "lane-01", "書込対象": ["docs/guide.md"], "書き込まない反映先": ["src/a.py", "docs/ops/"]}],
    )
    assert _dispatch(str(path), "--work-dir", str(repo)) == 0
    assert capsys.readouterr().err == ""
    _awi(notes, "a.md", "`src/`の`a.py`・`new.py`と、`docs/guide.md`・`new.md`を変更する。")
    body = (notes / "processing/a.md").read_text(encoding="utf-8")
    reflected = {"src/", "src/a.py", "src/new.py", "docs/guide.md", "docs/new.md"}
    assert check_selection.reflected_paths(body, repo) == reflected
    _write_selection(path, [{"WI": "a.md", "レーン": "lane-01", "書込対象": sorted(reflected - {"src/"})}])
    assert _dispatch(str(path), "--work-dir", str(repo)) == 0
    assert capsys.readouterr().err == ""


@pytest.mark.parametrize("wi_type", ["awi", "uwi"])
@pytest.mark.parametrize("code", [False, True])
@pytest.mark.parametrize("suffix", ["", ":42", ":42-58", "#節", "#API.method"])
def test_public_check_distinguishes_file_references(
    tmp_path: pathlib.Path,
    env: tuple[pathlib.Path, pathlib.Path],
    capsys: pytest.CaptureFixture[str],
    wi_type: str,
    code: bool,
    suffix: str,
) -> None:
    """参照位置と定義名をファイルへ混入せず、元ファイルの被覆不足だけを公開入口から返す。"""
    repo, notes = env
    reference = "src/model.py" + suffix
    if code:
        reference = f"`{reference}`"
    body = f"{reference}の`Worker.run`と`Service.start`を修正する。"
    heading = "## 反映内容と反映先" if wi_type == "awi" else "## 回答"
    (notes / "processing" / "a.md").write_text(f"---\ntype: {wi_type}\n---\n\n{heading}\n\n{body}\n", encoding="utf-8")
    path = tmp_path / "selection.yaml"
    for covered in (False, True):
        _write_selection(path, [{"WI": "a.md", "レーン": "lane-01", "書込対象": ["src/model.py"] if covered else []}])
        assert _dispatch(str(path), "--work-dir", str(repo)) == (0 if covered else 1)
        result = capsys.readouterr()
        errors = [line for line in result.err.splitlines() if line.startswith("a.md:")]
        assert errors == ([] if covered else ["a.md: 未被覆: src/model.py"])


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
        {"WI": "second.md", "レーン": "lane-01", "書込対象": []},
        {"WI": "none.md", "レーン": "なし", "書込対象": []},
    ]
    path = _write_selection(tmp_path / "selection.yaml", decisions)
    args = (str(path), "--work-dir", str(repo))
    scope = ("--body-wi", "new.md", "--body-wi", "second.md", "--body-wi", "new.md", "--body-wi", "none.md")
    assert _dispatch(*args, *scope) == 1
    result = capsys.readouterr()
    assert "second.md: 未被覆: README.md" in result.err
    assert "old.md:" not in result.err
    assert "元本文へ戻り" in result.err
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
        ("stage", "後段には先行レーン", 1),
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


@pytest.mark.parametrize("violation", ["coverage", "classification", "norm-spec"])
def test_public_check_applies_body_rules_only_to_selected_wis(
    tmp_path: pathlib.Path,
    env: tuple[pathlib.Path, pathlib.Path],
    capsys: pytest.CaptureFixture[str],
    violation: str,
) -> None:
    """本文由来の被覆・区分・規範指定の違反を持つ同じ項目を、指定ありと指定なしで比較する。"""
    repo, notes = env
    _awi(notes, "old.md", "`src/model.py`を変更する。")
    _awi(notes, "new.md", "`README.md`を変更する。")
    old: dict[str, typing.Any] = {"WI": "old.md", "レーン": "lane-01", "書込対象": ["src/model.py"]}
    if violation == "coverage":
        old["書込対象"] = []
        expected = "未被覆"
    elif violation == "classification":
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
    """ディレクトリの範囲`src/`は、名前の先頭だけが一致する`src-old/`配下のファイルを覆わない。"""
    repo, notes = env
    _awi(notes, "a.md", "`src-old/model.py`を変える。")

    assert _run(tmp_path, repo, [{"awi": "a.md", "lane": "lane-01", "write_files": ["src/"]}]) == 1
    assert "a.md: 未被覆: src-old/model.py" in capsys.readouterr().err


_RANGE_DESCRIPTION_FILES = ("agent-toolkit/share/pick-wi.subagent.md", "agent-toolkit/skills/search/SKILL.md")


def test_range_description_needs_no_coverage_but_inner_paths_do(
    tmp_path: pathlib.Path, env: tuple[pathlib.Path, pathlib.Path], capsys: pytest.CaptureFixture[str]
) -> None:
    """配下の個別パスも同じ節に挙げたディレクトリ範囲（範囲説明）は被覆を求めず、個別パスだけに被覆を求める。

    範囲説明へ被覆を求めると、覆える区分が`書込対象`だけになり、pickerが広い範囲を`書込対象`へ入れて
    全レーンが包含関係になる。最上位（インラインコードと文章中）、入れ子の範囲、UWIの本文の範囲を入力に使う。
    """
    repo, notes = env
    for relative in _RANGE_DESCRIPTION_FILES:
        (repo / relative).parent.mkdir(parents=True, exist_ok=True)
        (repo / relative).write_text("x\n", encoding="utf-8")
    _awi(
        notes,
        "range.md",
        "対象リポジトリは`agent-toolkit/`配下とdocs/配下とする。`agent-toolkit/share/pick-wi.subagent.md`を変える。"
        "`agent-toolkit/skills/`配下に限り、`agent-toolkit/skills/search/SKILL.md`の節を直す。"
        "`docs/development/design.md`も変える。",
    )
    body = (
        "---\ntype: uwi\nsource: process-wi\n---\n\n## 質問\n\nこの対応で問題無いか？\n\n"
        "## 回答\n\n<!-- ユーザーはこの行以降に回答を追記する -->\n`src/`配下の`src/model.py`も直して。\n"
    )
    (notes / "processing" / "u.md").write_text(body, encoding="utf-8")
    inner = [*_RANGE_DESCRIPTION_FILES, "docs/development/design.md"]
    selection = _write_selection(
        tmp_path / "selection.yaml",
        [
            {"WI": "range.md", "レーン": "lane-01", "書込対象": list(inner)},
            {"WI": "u.md", "レーン": "lane-01", "書込対象": ["src/model.py"]},
        ],
    )

    assert _dispatch("--work-dir", str(repo), str(selection)) == 0
    assert capsys.readouterr().err == ""

    _write_selection(
        selection,
        [
            {"WI": "range.md", "レーン": "lane-01", "書込対象": [path for path in inner if "search" not in path]},
            {"WI": "u.md", "レーン": "lane-01", "書込対象": []},
        ],
    )
    assert _dispatch("--work-dir", str(repo), str(selection)) == 1
    errors = sorted(line for line in capsys.readouterr().err.splitlines() if ": 未被覆: " in line)
    assert errors == ["range.md: 未被覆: agent-toolkit/skills/search/SKILL.md", "u.md: 未被覆: src/model.py"]


def test_directory_range_without_inner_path_still_needs_coverage(
    tmp_path: pathlib.Path, env: tuple[pathlib.Path, pathlib.Path], capsys: pytest.CaptureFixture[str]
) -> None:
    """配下に抽出結果を持たないディレクトリ範囲は範囲説明に当たらず、従来どおり被覆を求める。"""
    repo, notes = env
    _awi(notes, "only-range.md", "`pytools/`配下のテストを変える。")
    selection = _write_selection(tmp_path / "selection.yaml", [{"WI": "only-range.md", "レーン": "lane-01", "書込対象": []}])

    assert _dispatch("--work-dir", str(repo), str(selection)) == 1
    assert "only-range.md: 未被覆: pytools/" in capsys.readouterr().err


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


def _track(repo: pathlib.Path, *relatives: str) -> None:
    """`relatives`を作成し、`repo`をGitリポジトリにして全ファイルを追跡対象へ加える。"""
    for relative in relatives:
        (repo / relative).parent.mkdir(parents=True, exist_ok=True)
        (repo / relative).write_text("x\n", encoding="utf-8")
    git_repository.init_repository(repo)
    git_repository.run_git(repo, "add", "-A")


_PLUGIN_SHARE_FILES = ("agent-toolkit/share/pick-wi.parent.md", "agent-toolkit/share/rules-main.md", "share/README.md")


def test_public_check_skips_glob_fragments_and_missing_ranges_and_resolves_short_path(
    tmp_path: pathlib.Path, env: tuple[pathlib.Path, pathlib.Path], capsys: pytest.CaptureFixture[str]
) -> None:
    """グロブの途中で途切れた語と不在のディレクトリ範囲を違反にせず、略記を追跡ファイルとして被覆判定する。

    作業ツリー直下にも`share/`があるため、途切れた語や略記を親ディレクトリの実在だけで採ると、
    `書込対象`の`agent-toolkit/`では覆えない未被覆の違反になる。グロブの手前の実在ディレクトリ
    （`docs/development/`）を範囲として採っても、同じく未被覆の違反になる。
    """
    repo, notes = env
    _track(repo, *_PLUGIN_SHARE_FILES)
    _awi(
        notes,
        "glob.md",
        "`agent-toolkit/`配下のうち`share/rules-*.md`と`share/exec-review.{parent,subagent}.md`、"
        "略記の`rules/`と`share/pick-wi.parent.md`を改める。`docs/development/*.md`の記述は変えない。あわせて`src/model.py`と`src/new_module.py`も変える。",
    )
    selection = _write_selection(
        tmp_path / "selection.yaml", [{"WI": "glob.md", "レーン": "lane-01", "書込対象": ["agent-toolkit/", "src/model.py"]}]
    )

    assert _dispatch("--work-dir", str(repo), str(selection)) == 1
    errors = [line for line in capsys.readouterr().err.splitlines() if line.startswith("glob.md: ")]
    assert errors == ["glob.md: 未被覆: src/new_module.py"]

    _write_selection(
        selection,
        [{"WI": "glob.md", "レーン": "lane-01", "書込対象": ["agent-toolkit/", "src/model.py", "src/new_module.py"]}],
    )
    assert _dispatch("--work-dir", str(repo), str(selection)) == 0
    assert capsys.readouterr().err == ""


def test_reflected_paths_from_mixed_notations(env: tuple[pathlib.Path, pathlib.Path]) -> None:
    """多様な表記を混ぜた反映先から、本文が指すファイルと範囲だけを抽出する。

    グロブの断片、不在の範囲、拡張子の欠けた名前、ファイルの「の」に続く定義名（`Worker.run`など）を
    抽出すると、期待する集合に無い架空のパスが加わって失敗する。架空のパスがpickerへ渡ると、
    pickerは本文が書き込まないパスを`書込対象`か`書き込まない反映先`へ分類させられる。
    """
    repo, _notes = env
    _track(repo, *_PLUGIN_SHARE_FILES)
    body = (
        "---\ntype: awi\nsource: test\n---\n\n# 題\n\n## 反映内容と反映先\n\n"
        "変更対象はsrc/model.pyとdocs/development/design.mdである。"
        "`share/rules-*.md`、`agent-toolkit/share/rules-*.md`、`share/exec-review.{parent,subagent}.md`、`src/*.py`を改める。"
        "略記の`rules/`・`UCR/`・`share/pick-wi.parent.md`、範囲`src/`と`missing/`、"
        "src/のmodel.py・new_module.py・draft、`docs/development/new.md`と`docs/development/new.`も扱う。"
        "src/model.pyのWorker.runと`src/model.py:42`の`Service.start`も直す。\n"
    )

    assert check_selection.reflected_paths(body, repo) == {
        "agent-toolkit/share/pick-wi.parent.md",
        "docs/development/design.md",
        "docs/development/new.md",
        "src/",
        "src/model.py",
        "src/new_module.py",
    }


@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize(
    ("paths", "same_lane", "rationale", "expected"),
    [
        (("src/model.py", "src/model.py"), False, "src/model.pyの異なる定義", 1),
        (("src/model.py", "src/model.py"), False, "別の対象", 1),
        (("src/", "src/model.py"), False, "src/model.pyの異なる定義", 1),
        (("src/", "src/model.py"), False, "src/の異なる定義", 1),
        (("src/", "src/models/"), False, "src/models/の異なる定義", 1),
        (("src/", "src/models/"), False, "src/models-old/の定義", 1),
        (("src/", "src-old/model.py"), False, "別対象", 0),
        (("src/model.py", "src/model.py"), True, "同じレーンで直列化", 0),
    ],
)
def test_public_check_rejects_rationale_only_overlaps(
    legacy: bool,
    paths: tuple[str, str],
    same_lane: bool,
    rationale: str,
    expected: int,
    tmp_path: pathlib.Path,
    env: tuple[pathlib.Path, pathlib.Path],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """新旧欄名とも自由文だけでは共有を受理せず、似た名前と同じレーンは共有から外す。"""
    repo, notes = env
    (repo / "src/models").mkdir()
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
        assert "lane-01とlane-02" in err
        assert "重複パスの記録不足" in err
        assert paths[1] in err
        assert "次の操作:" in err
        assert "双方の定義と判定" in err
    else:
        assert not err


@pytest.mark.parametrize("missing_lane", ["lane-01", "lane-02"])
def test_overlap_rejects_rationale_missing_on_either_side(
    missing_lane: str,
    tmp_path: pathlib.Path,
    env: tuple[pathlib.Path, pathlib.Path],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """片方の自由文だけが共通パスを持っても、構造化記録の不足を示す。"""
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
    assert "重複パスの記録不足: src/model.py" in capsys.readouterr().err


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
        if second_lane == "lane-02":
            data = yaml.safe_load(selection.read_text(encoding="utf-8"))
            data["レーン間の重なり"] = [_overlap_record("src/model.py", "交わる")]
            selection.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
        code = _dispatch("--work-dir", str(repo), str(selection))
        return code, capsys.readouterr().err

    assert run("lane-02", {"実行レビュー担当": "codex:gpt-6-sol/medium"}, ["lane-01"]) == (0, "")

    code, err = run("lane-02", {"実行レビュー担当": "codex:gpt-6-sol/medium"}, [])
    assert code == 1
    assert "lane-02: 後段には先行レーンを指定する" in err
    assert "依存がない" in err

    code, err = run("lane-01", {"実装担当": "codex:gpt-6-sol/medium"}, ["lane-01"])
    assert code == 1
    assert "lane-01: 実装担当のモデル指定が衝突" in err


def _write_files(repo: pathlib.Path, *relatives: str) -> None:
    """作業ツリーへファイルを作成する。"""
    for relative in relatives:
        (repo / relative).parent.mkdir(parents=True, exist_ok=True)
        (repo / relative).write_text("x\n", encoding="utf-8")


_NON_ASCII_FILES = ("docs/dev/ログ監視.md", "docs/design/既存.md", "docs/café.md", "ログ/a.md", "docs/infra/クラウド料金.md")


def test_public_command_extracts_non_ascii_paths_whole(
    tmp_path: pathlib.Path, env: tuple[pathlib.Path, pathlib.Path], capsys: pytest.CaptureFixture[str]
) -> None:
    """インラインコードと文章中の非ASCIIを含むパスを末尾まで読み、個別のパスだけの`書込対象`を受理する。

    非ASCIIの手前で抽出が終わると、`docs/dev/`のような範囲や`docs/design/LLM`のような途中の名前が
    反映先になり、pickerは実際には書かない範囲を`書込対象`へ置いて別レーンと重ねる。
    """
    repo, notes = env
    _write_files(repo, *_NON_ASCII_FILES)
    _awi(
        notes,
        "a.md",
        "`docs/dev/ログ監視.md`、`docs/design/LLMへのファイル入力.md`、`docs/café.md`、`ログ/a.md`を変える。"
        "文章中のdocs/infra/クラウド料金.mdを更新する。src/のmodel.py・new_module.pyも変える。",
    )
    expected = {
        "docs/dev/ログ監視.md",
        "docs/design/LLMへのファイル入力.md",
        "docs/café.md",
        "ログ/a.md",
        "docs/infra/クラウド料金.md",
        "src/model.py",
        "src/new_module.py",
    }
    # `src/`は配下の列挙を導く範囲説明として抽出され、被覆を求めない。
    text = (notes / "processing" / "a.md").read_text(encoding="utf-8")
    assert check_selection.reflected_paths(text, repo) == expected | {"src/"}
    selection = _write_selection(
        tmp_path / "selection.yaml", [{"WI": "a.md", "レーン": "lane-01", "書込対象": sorted(expected)}]
    )
    assert _dispatch("--work-dir", str(repo), str(selection)) == 0
    assert capsys.readouterr().err == ""


def test_excluded_range_may_contain_write_paths(
    tmp_path: pathlib.Path, env: tuple[pathlib.Path, pathlib.Path], capsys: pytest.CaptureFixture[str]
) -> None:
    """`書き込まない反映先`の範囲が書込区分のパスを含む組を受理し、残る重複と範囲の配下の未分類は報告する。

    受理しないと、AWI本文が変更しないと述べる上位ディレクトリを`書込対象`へ置かせ、別レーンとの重なりを生む。
    同じパスの2区分と、書込区分の範囲が非書込のパスを含む組まで受理すると、所有の区分が決まらない。
    """
    repo, notes = env
    _write_files(repo, "docs/design/性能.md", "docs/design/別.md", "docs/design/旧.md")
    excluded_range = "`docs/design/性能.md`を変える。他の`docs/`配下は変更しない。"
    _awi(notes, "write.md", excluded_range)
    _awi(notes, "public.md", excluded_range)
    _awi(notes, "same.md", excluded_range)
    _awi(notes, "range.md", "`docs/design/`配下の`docs/design/性能.md`を変える。`docs/design/旧.md`は変更しない。")
    _awi(notes, "inner.md", "`docs/design/性能.md`と`docs/design/別.md`を変える。他の`docs/`配下は変更しない。")
    decisions: list[dict[str, typing.Any]] = [
        {"WI": "write.md", "レーン": "lane-01", "書込対象": ["docs/design/性能.md"], "書き込まない反映先": ["docs/"]},
        {
            "WI": "public.md",
            "レーン": "lane-02",
            "書込対象": [],
            "公開工程の書込対象": ["docs/design/性能.md"],
            "書き込まない反映先": ["docs/"],
        },
        {
            "WI": "same.md",
            "レーン": "lane-03",
            "書込対象": ["docs/design/性能.md"],
            "書き込まない反映先": ["docs/", "docs/design/性能.md"],
        },
        {"WI": "range.md", "レーン": "lane-04", "書込対象": ["docs/design/"], "書き込まない反映先": ["docs/design/旧.md"]},
        {"WI": "inner.md", "レーン": "lane-05", "書込対象": ["docs/design/性能.md"], "書き込まない反映先": ["docs/"]},
    ]
    costs = [{"レーン": f"lane-0{number}"} for number in range(1, 6)]
    costs[1]["根拠"] = "対象リポジトリの規範AGENTS.mdの節「公開」がdocs/design/性能.mdを公開工程で書くと定める"
    selection = _write_selection(tmp_path / "selection.yaml", decisions, costs)

    assert _dispatch("--work-dir", str(repo), str(selection)) == 1
    errors = sorted(line for line in capsys.readouterr().err.splitlines() if re.match(r"\w+\.md: ", line))
    assert errors == [
        "inner.md: 未被覆: docs/design/別.md",
        "range.md: 区分間の重複: docs/design/旧.md（`書込対象`と`書き込まない反映先`）",
        "same.md: 区分間の重複: docs/design/性能.md（`書込対象`と`書き込まない反映先`）",
    ]


def test_non_ascii_write_paths_do_not_require_lane_overlap_rationale(
    tmp_path: pathlib.Path, env: tuple[pathlib.Path, pathlib.Path], capsys: pytest.CaptureFixture[str]
) -> None:
    """日本語名のファイルを個別に書く別レーンの項目どうしは、重複パスの根拠を求めない。"""
    repo, notes = env
    _write_files(repo, "docs/dev/ログ監視.md", "docs/design/性能.md")
    _awi(notes, "log.md", "`docs/dev/ログ監視.md`を変える。")
    _awi(notes, "perf.md", "`docs/design/性能.md`を変える。他の`docs/`配下は変更しない。")
    selection = _write_selection(
        tmp_path / "selection.yaml",
        [
            {"WI": "log.md", "レーン": "lane-01", "書込対象": ["docs/dev/ログ監視.md"]},
            {"WI": "perf.md", "レーン": "lane-02", "書込対象": ["docs/design/性能.md"], "書き込まない反映先": ["docs/"]},
        ],
    )
    assert _dispatch("--work-dir", str(repo), str(selection)) == 0
    assert capsys.readouterr().err == ""


# 本リポジトリの`pyproject.toml`。設定の削除や条件の縮小で検出が消える退行を、実物の設定で検出する。
_DOTFILES_PYPROJECT = pathlib.Path(__file__).resolve().parents[4] / "pyproject.toml"
_READ_REQUEST = (
    "docs/development/concepts.mdとdocs/development/incidents.mdの全文と、該当する分割ファイルの節を計画の採否確定前に読む"
)


def _norm_spec_selection(
    tmp_path: pathlib.Path, decisions: list[dict[str, typing.Any]], specs: dict[str, str | None]
) -> pathlib.Path:
    """各項目へ`プロジェクト規範の指定`を書き（`None`は省略）、選定結果を保存する。"""
    for decision in decisions:
        spec = specs.get(decision["WI"])
        if spec is not None:
            decision["プロジェクト規範の指定"] = spec
    return _write_selection(tmp_path / "selection.yaml", decisions)


@pytest.mark.parametrize(
    ("spec", "expected"),
    [
        (None, 1),
        ("なし", 1),
        ("", 1),
        (f"agent-toolkit/share/pick-wi.parent.md。{_READ_REQUEST}", 1),
        (f"agent-toolkit/share/pick-wi.parent.md、.claude/skills/edit/SKILL.md。{_READ_REQUEST}", 0),
    ],
    ids=["omitted", "none", "empty", "partial", "complete"],
)
def test_public_command_requires_project_norm_spec(
    tmp_path: pathlib.Path,
    env: tuple[pathlib.Path, pathlib.Path],
    capsys: pytest.CaptureFixture[str],
    spec: str | None,
    expected: int,
) -> None:
    """本リポジトリの設定で、規範ファイルを変える項目の指定の欠落を報告し、補った指定を受理する。

    指定が省略・`なし`・空欄・一部欠落のまま受理されると、レーン担当へ変更後の規範の適用と方針記録の
    読込の要求が届かない。コードだけを変える項目は省略しても受理する。
    """
    repo, notes = env
    (repo / "pyproject.toml").write_text(_DOTFILES_PYPROJECT.read_text(encoding="utf-8"), encoding="utf-8")
    _write_files(repo, "agent-toolkit/share/pick-wi.parent.md", ".claude/skills/edit/SKILL.md", "scripts/tool.py")
    _awi(notes, "norm.md", "`agent-toolkit/share/pick-wi.parent.md`と`.claude/skills/edit/SKILL.md`を変える。")
    _awi(notes, "code.md", "`scripts/tool.py`を変える。")
    decisions: list[dict[str, typing.Any]] = [
        {
            "WI": "norm.md",
            "レーン": "lane-01",
            "書込対象": ["agent-toolkit/share/pick-wi.parent.md", ".claude/skills/edit/SKILL.md"],
        },
        {"WI": "code.md", "レーン": "lane-02", "書込対象": ["scripts/tool.py"]},
    ]
    selection = _norm_spec_selection(tmp_path, decisions, {"norm.md": spec})
    # pickerの保存直後とメインの受領時は同じ入力へ同じコマンドを実行し、同じ判定を得る。
    for _ in range(2):
        assert _dispatch("--work-dir", str(repo), str(selection)) == expected
        lines = [
            line for line in capsys.readouterr().err.splitlines() if re.match(r"\S+\.md: プロジェクト規範の指定の不足", line)
        ]
        assert all(line.startswith("norm.md: ") for line in lines)
        if expected:
            assert lines
            if spec and spec != "なし":
                assert lines == [
                    "norm.md: プロジェクト規範の指定の不足: 規範変更の対象ファイル: 欠けた記載: .claude/skills/edit/SKILL.md"
                ]
        else:
            assert not lines


def test_agent_doc_reflection_requires_read_request(
    tmp_path: pathlib.Path, env: tuple[pathlib.Path, pathlib.Path], capsys: pytest.CaptureFixture[str]
) -> None:
    """配布原本など規範変更の範囲外のエージェント向け文書でも、方針と障害記録を読む要求の欠落を報告する。"""
    repo, notes = env
    (repo / "pyproject.toml").write_text(_DOTFILES_PYPROJECT.read_text(encoding="utf-8"), encoding="utf-8")
    original = ".chezmoi-source/dot_claude/rules/personal.md"
    _write_files(repo, original)
    _awi(notes, "rules.md", f"`{original}`を変える。")
    decisions: list[dict[str, typing.Any]] = [{"WI": "rules.md", "レーン": "lane-01", "書込対象": [original]}]
    _norm_spec_selection(tmp_path, decisions, {"rules.md": original})
    assert _dispatch("--work-dir", str(repo), str(tmp_path / "selection.yaml")) == 1
    error = capsys.readouterr().err
    assert "rules.md: プロジェクト規範の指定の不足: 方針と障害記録を読む要求: 欠けた記載: " in error
    decisions = [{"WI": "rules.md", "レーン": "lane-01", "書込対象": [original]}]
    _norm_spec_selection(tmp_path, decisions, {"rules.md": f"{original}。{_READ_REQUEST}"})
    assert _dispatch("--work-dir", str(repo), str(tmp_path / "selection.yaml")) == 0, capsys.readouterr().err


@pytest.mark.parametrize("config", ["absent", "custom"])
def test_project_norm_spec_follows_repository_config(
    tmp_path: pathlib.Path, env: tuple[pathlib.Path, pathlib.Path], capsys: pytest.CaptureFixture[str], config: str
) -> None:
    """設定の無いリポジトリでは指定の省略を受理し、独自の条件を設定したリポジトリではその条件で欠落を報告する。"""
    repo, notes = env
    if config == "custom":
        (repo / "pyproject.toml").write_text(
            '[[tool.agent-toolkit.pick-wi-check.norm-spec]]\nname = "設計記録"\npaths = ["docs/"]\n'
            'require-text = ["独自の要求"]\n',
            encoding="utf-8",
        )
    _awi(notes, "doc.md", "`docs/development/design.md`と`AGENTS.md`を変える。")
    _write_files(repo, "AGENTS.md")
    decisions: list[dict[str, typing.Any]] = [
        {"WI": "doc.md", "レーン": "lane-01", "書込対象": ["docs/development/design.md", "AGENTS.md"]}
    ]
    _norm_spec_selection(tmp_path, decisions, {})
    expected = 1 if config == "custom" else 0
    assert _dispatch("--work-dir", str(repo), str(tmp_path / "selection.yaml")) == expected
    error = capsys.readouterr().err
    if expected:
        assert "doc.md: プロジェクト規範の指定の不足: 設計記録（docs/development/design.mdが当たる）" in error
        decisions = [{"WI": "doc.md", "レーン": "lane-01", "書込対象": ["docs/development/design.md", "AGENTS.md"]}]
        _norm_spec_selection(tmp_path, decisions, {"doc.md": "独自の要求"})
        assert _dispatch("--work-dir", str(repo), str(tmp_path / "selection.yaml")) == 0, capsys.readouterr().err
    else:
        assert error == ""


@pytest.mark.parametrize(
    "content",
    [
        "[tool.agent-toolkit.pick-wi-check\n",
        '[tool.agent-toolkit.pick-wi-check]\nnorm-spec = "規範"\n',
        '[[tool.agent-toolkit.pick-wi-check.norm-spec]]\nname = "x"\npaths = "AGENTS.md"\nrequire-paths = true\n',
        '[[tool.agent-toolkit.pick-wi-check.norm-spec]]\nname = "x"\npaths = ["AGENTS.md"]\n',
        '[[tool.agent-toolkit.pick-wi-check.norm-spec]]\nname = "x"\nagent-doc = true\nrequire-paths = true\nunknown = 1\n',
    ],
    ids=["syntax", "not-array", "paths-type", "no-requirement", "unknown-key"],
)
def test_invalid_project_norm_config_is_input_error(
    tmp_path: pathlib.Path, env: tuple[pathlib.Path, pathlib.Path], capsys: pytest.CaptureFixture[str], content: str
) -> None:
    """不正な条件の設定は、条件なしとして受理せず、設定の場所と修正の操作を示して終了コード2を返す。"""
    repo, notes = env
    (repo / "pyproject.toml").write_text(content, encoding="utf-8")
    _awi(notes, "a.md", "`src/model.py`を変える。")
    selection = _write_selection(
        tmp_path / "selection.yaml", [{"WI": "a.md", "レーン": "lane-01", "書込対象": ["src/model.py"]}]
    )
    assert _dispatch("--work-dir", str(repo), str(selection)) == 2
    error = capsys.readouterr().err
    assert str(repo / "pyproject.toml") in error and "次の操作: " in error


def _parse_summary(output: str) -> dict[str, object]:
    """成功時の要約行を、WI総数・通常レーン数・レーンごとの値・`レーン: なし`の一覧へ読み直す。"""
    parsed: dict[str, object] = {"lanes": {}}
    lanes = typing.cast(dict[str, dict[str, object]], parsed["lanes"])
    for line in output.splitlines():
        if line.startswith("WI総数: "):
            parsed["total"] = int(line.removeprefix("WI総数: "))
        elif line.startswith("通常レーン数: "):
            parsed["lane_count"] = int(line.removeprefix("通常レーン数: "))
        elif line.startswith("レーン: なし: "):
            parsed["none"] = json.loads(line.removeprefix("レーン: なし: "))
        elif found := re.fullmatch(
            r"(\S+): WI (\d+)件、段階 (\d+)、先行レーン (\[.*?\])、実装秒数 (\S+)、統合秒数 (\S+)、WI (\[.*\])", line
        ):
            lanes[found[1]] = {
                "count": int(found[2]),
                "stage": int(found[3]),
                "prior": json.loads(found[4]),
                "seconds": (float(found[5]), float(found[6])),
                "wis": json.loads(found[7]),
            }
    return parsed


def _expected_summary(selection: dict[str, typing.Any]) -> dict[str, object]:
    """選定結果のYAMLの値から、読み取り契約の省略時の値と旧欄名を適用して要約の期待値を求める。"""
    items = selection.get("選定", selection.get("decisions", []))
    costs = selection.get("レーンの所要時間", selection.get("lane_costs", []))
    lane_of = [(item.get("WI", item.get("awi")), item.get("レーン", item.get("lane"))) for item in items]
    lanes: dict[str, dict[str, object]] = {}
    for awi, lane in lane_of:
        if lane == "なし":
            continue
        row = next(row for row in costs if row.get("レーン", row.get("lane")) == lane)
        entry = lanes.setdefault(
            lane,
            {
                "count": 0,
                "stage": row.get("段階", row.get("stage", 1)),
                "prior": row.get("先行レーン", row.get("prior_lanes", [])),
                "seconds": (
                    float(row.get("実装秒数", row.get("implementation_seconds"))),
                    float(row.get("統合秒数", row.get("integration_seconds"))),
                ),
                "wis": [],
            },
        )
        entry["count"] = typing.cast(int, entry["count"]) + 1
        typing.cast(list[str], entry["wis"]).append(awi)
    return {
        "lanes": lanes,
        "total": len(items),
        "lane_count": len(lanes),
        "none": [awi for awi, lane in lane_of if lane == "なし"],
    }


@pytest.mark.parametrize("layout", ["staged", "legacy", "empty"])
def test_public_command_prints_lane_summary_on_success(
    tmp_path: pathlib.Path, env: tuple[pathlib.Path, pathlib.Path], capsys: pytest.CaptureFixture[str], layout: str
) -> None:
    """成功時の標準出力だけから、WI総数・通常レーン数・各レーンの件数・全WI名・段階・先行レーン・秒数を読める。

    旧欄名の秒数を読まない、`レーン: なし`の項目が欠ける、WI名を切り詰めるのいずれかがあると、受領側はレーン構成を
    出力ファイルから読み直す必要が残る。期待値は入力のYAMLから求める。
    """
    repo, notes = env
    for name in ("a.md", "b.md", "c.md"):
        _awi(notes, name, "`src/model.py`を変える。")
    _awi(notes, "d.md", "`docs/development/design.md`を変える。")
    selection: dict[str, typing.Any]
    if layout == "staged":
        _awi(notes, "d.md", "`src/model.py`を変える。")
        selection = {
            "選定": [
                {"WI": "a.md", "レーン": "lane-01", "書込対象": ["src/model.py"]},
                {"WI": "b.md", "レーン": "lane-01", "書込対象": ["src/model.py"]},
                {"WI": "d.md", "レーン": "lane-02", "書込対象": ["src/model.py"]},
                {"WI": "c.md", "レーン": "なし", "書込対象": []},
            ],
            "レーンの所要時間": [
                {"レーン": "lane-01", "実装秒数": 900, "統合秒数": 120, "根拠": "先行"},
                {"レーン": "lane-02", "段階": 2, "先行レーン": ["lane-01"], "実装秒数": 300.5, "統合秒数": 30, "根拠": "後段"},
            ],
            "レーン間の重なり": [_overlap_record("src/model.py", "交わる")],
            "単一段階案の完了見込み秒数": 1500,
        }
    elif layout == "legacy":
        selection = {
            "decisions": [
                {"awi": "a.md", "lane": "lane-01", "write_files": ["src/model.py"]},
                {"awi": "c.md", "lane": "なし", "write_files": []},
            ],
            "lane_costs": [
                {"lane": "lane-01", "implementation_seconds": 450, "integration_seconds": 45, "rationale": "旧形式"}
            ],
        }
    else:
        selection = {"選定": [], "レーンの所要時間": []}
    for item in selection.get("選定", selection.get("decisions", [])):
        item.setdefault("鮮度" if "WI" in item else "staleness", {"status": "current", "later_commit_count": 0})
    path = tmp_path / "selection.yaml"
    path.write_text(yaml.safe_dump(selection, allow_unicode=True), encoding="utf-8")

    assert _dispatch("--work-dir", str(repo), str(path)) == 0
    result = capsys.readouterr()
    assert result.err == ""
    assert _parse_summary(result.out) == _expected_summary(selection)


@pytest.mark.parametrize("failure", ["content", "input", "norm-spec"])
def test_public_command_prints_no_summary_on_failure(
    tmp_path: pathlib.Path, env: tuple[pathlib.Path, pathlib.Path], capsys: pytest.CaptureFixture[str], failure: str
) -> None:
    """内容違反（規範指定の不足を含む）と入力不備では、終了コード1・2と次の操作を示し、標準出力へ要約を書かない。"""
    repo, notes = env
    _awi(notes, "a.md", "`src/model.py`と`AGENTS.md`を変える。")
    _write_files(repo, "AGENTS.md")
    decisions: list[dict[str, typing.Any]] = [{"WI": "a.md", "レーン": "lane-01", "書込対象": ["src/model.py", "AGENTS.md"]}]
    if failure == "content":
        decisions[0]["書込対象"] = ["src/model.py"]
    if failure == "norm-spec":
        (repo / "pyproject.toml").write_text(_DOTFILES_PYPROJECT.read_text(encoding="utf-8"), encoding="utf-8")
    path = _write_selection(tmp_path / "selection.yaml", decisions)
    if failure == "input":
        path.write_text("選定: [\n", encoding="utf-8")
    assert _dispatch("--work-dir", str(repo), str(path)) == (2 if failure == "input" else 1)
    result = capsys.readouterr()
    assert "次の操作: " in result.err
    assert "WI総数" not in result.out and "通常レーン数" not in result.out
