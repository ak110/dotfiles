# pylint: disable=useless-import-alias  # pytestのfixtureを試験モジュールの名前空間へ登録するため。
"""選定の本文パスの抽出と略記を検証する。"""

import pathlib
import re
import typing

import check_selection
import pytest
import yaml

from agent_toolkit._testing.selection_check_support import (
    _awi,
    _dispatch,
    _overlap_record,
    _run,
    _track,
    _write_files,
    _write_selection,
)
from agent_toolkit._testing.selection_check_support import fixture_env as fixture_env


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
    assert "正当な`導出した新設先`の個別パス" in diagnostic


@pytest.mark.parametrize(
    ("case", "expected"),
    [
        ("missing", 1),
        ("separate", 0),
        ("same-definition", 1),
        ("extra-path", 1),
        ("duplicate", 1),
        ("reversed-duplicate", 1),
        ("serial-separate", 0),
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
    if case == "serial-unshared":
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
    env: tuple[pathlib.Path, pathlib.Path],
    wi_type: str,
    code: bool,
    suffix: str,
) -> None:
    """参照位置と定義名を分離し、AWIとUWIから元ファイルだけを抽出する。"""
    repo, notes = env
    reference = "src/model.py" + suffix
    if code:
        reference = f"`{reference}`"
    body = f"{reference}の`Worker.run`と`Service.start`を修正する。"
    heading = "## 反映内容と反映先" if wi_type == "awi" else "## 回答"
    (notes / "processing" / "a.md").write_text(f"---\ntype: {wi_type}\n---\n\n{heading}\n\n{body}\n", encoding="utf-8")
    source = (notes / "processing/a.md").read_text(encoding="utf-8")
    assert check_selection.reflected_paths(source, repo) == {"src/model.py"}


def test_public_check_resolves_abbreviated_paths_and_classification(
    env: tuple[pathlib.Path, pathlib.Path],
) -> None:
    """文章とインラインコードの省略表記を反映先の期待集合へ解決する。"""
    repo, notes = env
    fresh_path = "agent-toolkit/agent_toolkit/_agents_server/" + "fresh.py"
    _awi(
        notes,
        "short.md",
        "src/のmodel.py・new_module.pyと、`docs/development/design.md`・`new.md`を変更する。"
        "次に`agent-toolkit/agent_toolkit/_agents_server/status_file.py`と`fresh.py`を変更する。",
    )
    body = (notes / "processing/short.md").read_text(encoding="utf-8")
    assert check_selection.reflected_paths(body, repo) == {
        "src/",
        "src/model.py",
        "src/new_module.py",
        "docs/development/design.md",
        "docs/development/new.md",
        "agent-toolkit/agent_toolkit/_agents_server/status_file.py",
        fresh_path,
    }


def test_abbreviated_paths_require_directory_context_and_existing_parent(
    tmp_path: pathlib.Path, env: tuple[pathlib.Path, pathlib.Path], capsys: pytest.CaptureFixture[str]
) -> None:
    """孤立した名前、コマンド、存在しない親を反映先として誤診断しない。"""
    repo, notes = env
    _awi(notes, "isolated.md", "model.pyと`atk wi add`、https://example.com/x.py、missing/dir/x.py・other.pyは対象外。")

    assert _run(tmp_path, repo, [{"awi": "isolated.md", "lane": "lane-01", "write_files": []}]) == 0
    assert capsys.readouterr().err == ""


_PLUGIN_SHARE_FILES = ("agent-toolkit/share/exec.parent.md", "agent-toolkit/share/rules/rules-main.md", "share/README.md")


@pytest.mark.parametrize("wi_type", ["awi", "uwi"])
@pytest.mark.parametrize("inline_code", [False, True])
@pytest.mark.parametrize("following", ["test_model.py", "report.md"])
def test_public_check_resolves_cross_directory_abbreviations(
    env: tuple[pathlib.Path, pathlib.Path],
    wi_type: str,
    inline_code: bool,
    following: str,
) -> None:
    """別親の追跡ファイルの一意実体へ略記を解決する。"""
    repo, notes = env
    actual = f"tests/{following}"
    _track(repo, actual)
    first = "src/model.py"
    names = [first, following]
    if inline_code:
        names = [f"`{name}`" for name in names]
    section = "反映内容と反映先" if wi_type == "awi" else "回答"
    body = f"---\ntype: {wi_type}\nsource: test\n---\n\n# 題\n\n## {section}\n\n{names[0]}と{names[1]}を変更する。\n"
    (notes / "processing" / "short.md").write_text(body, encoding="utf-8")
    assert check_selection.reflected_paths(body, repo) == {first, actual}


@pytest.mark.parametrize("wi_type", ["awi", "uwi"])
def test_public_check_reports_ambiguous_abbreviation_and_accepts_explicit_path(
    tmp_path: pathlib.Path,
    env: tuple[pathlib.Path, pathlib.Path],
    capsys: pytest.CaptureFixture[str],
    wi_type: str,
) -> None:
    """略記の複数候補を診断し、明示した相対パスを受理する。"""
    repo, notes = env
    _track(repo, "tests/report.md", "archive/report.md")
    section = "反映内容と反映先" if wi_type == "awi" else "回答"
    prefix = f"---\ntype: {wi_type}\nsource: test\n---\n\n# 題\n\n## {section}\n\n"
    wi = notes / "processing" / "ambiguous.md"
    wi.write_text(prefix + "`src/model.py`と`report.md`を変更する。\n", encoding="utf-8")
    selection = _write_selection(
        tmp_path / "selection.yaml",
        [{"WI": "ambiguous.md", "レーン": "lane-01", "書込対象": ["src/model.py", "tests/report.md"]}],
    )

    assert _dispatch("--work-dir", str(repo), str(selection)) != 0
    errors = capsys.readouterr().err
    assert "tests/report.md" in errors and "archive/report.md" in errors
    assert "リポジトリ相対パス" in errors
    assert "src/report.md" not in errors
    wi.write_text(prefix + "`src/model.py`と`tests/report.md`を変更する。\n", encoding="utf-8")
    assert _dispatch("--work-dir", str(repo), str(selection)) == 0
    assert capsys.readouterr().err == ""


@pytest.mark.parametrize("wi_type", ["awi", "uwi"])
@pytest.mark.parametrize(
    "text",
    [
        "src/のmodel.pyのWorker.runを変更する。",
        "docs/のguide.mdのfoo.barと変更",
        "`src/`の`model.py`の`Worker.run`を変更する。",
        "`docs/`の`guide.md`の`foo.bar`と変更",
        "src/のmodel.py・new_module.pyのWorker.runを変更する。",
    ],
)
def test_public_check_excludes_definitions_after_directory_file_names(
    env: tuple[pathlib.Path, pathlib.Path],
    wi_type: str,
    text: str,
) -> None:
    """明示dirの後でもファイルの「の」を定義名の区切りとして扱う。"""
    repo, notes = env
    _track(repo, "docs/guide.md")
    section = "反映内容と反映先" if wi_type == "awi" else "回答"
    body = f"---\ntype: {wi_type}\nsource: test\n---\n\n# 題\n\n## {section}\n\n{text}\n"
    (notes / "processing" / "definitions.md").write_text(body, encoding="utf-8")
    expected_file = "docs/guide.md" if "guide.md" in text else "src/model.py"
    expected = {"docs/" if "guide.md" in text else "src/", expected_file}
    if "new_module.py" in text:
        expected.add("src/new_module.py")
    assert check_selection.reflected_paths(body, repo) == expected


def test_public_check_preserves_explicit_directory_and_excludes_identifiers(
    tmp_path: pathlib.Path, env: tuple[pathlib.Path, pathlib.Path], capsys: pytest.CaptureFixture[str]
) -> None:
    """明示dirでの新設は別親の同名候補と区別し、孤立語・glob・定義名を含めない。"""
    repo, notes = env
    _track(repo, "tests/new_module.py", "archive/new_module.py")
    _awi(
        notes,
        "explicit.md",
        "src/のmodel.py・new_module.pyと`docs/development/new.md`を変更する。\n\n"
        "孤立したnew_module.py、`src/*.py`、`missing/`、`src/model.py`の`Worker.run`は追加対象外。",
    )
    selection = _write_selection(
        tmp_path / "selection.yaml",
        [{"WI": "explicit.md", "レーン": "lane-01", "書込対象": ["src/", "docs/development/new.md"]}],
    )
    assert _dispatch("--work-dir", str(repo), str(selection)) == 0
    assert capsys.readouterr().err == ""


def test_public_check_skips_glob_fragments_and_missing_ranges_and_resolves_short_path(
    env: tuple[pathlib.Path, pathlib.Path],
) -> None:
    """グロブの断片と不在範囲を除き、略記の一意実体と個別パスを抽出する。"""
    repo, notes = env
    _track(repo, *_PLUGIN_SHARE_FILES)
    _awi(
        notes,
        "glob.md",
        "`agent-toolkit/`配下のうち`share/rules/rules-*.md`と`share/exec-review.{parent,subagent}.md`、"
        "略記の`rules/`と`share/exec.parent.md`を改める。`docs/development/*.md`の記述は変えない。あわせて`src/model.py`と`src/new_module.py`も変える。",
    )
    body = (notes / "processing/glob.md").read_text(encoding="utf-8")
    assert check_selection.reflected_paths(body, repo) == {
        "agent-toolkit/",
        "agent-toolkit/share/exec.parent.md",
        "src/model.py",
        "src/new_module.py",
    }


def test_reflected_paths_from_mixed_notations(env: tuple[pathlib.Path, pathlib.Path]) -> None:
    """多様な表記を混ぜた反映先から、本文が指すファイルと範囲だけを抽出する。

    グロブの断片、不在の範囲、拡張子の欠けた名前、ファイルの「の」に続く定義名（`Worker.run`など）を
    抽出すると、期待する集合に無い架空のパスが加わって失敗する。架空のパスがメインへ渡ると、
    本文の新設先や過大範囲の判定へ誤った候補が渡る。
    """
    repo, _notes = env
    _track(repo, *_PLUGIN_SHARE_FILES)
    body = (
        "---\ntype: awi\nsource: test\n---\n\n# 題\n\n## 反映内容と反映先\n\n"
        "変更対象はsrc/model.pyとdocs/development/design.mdである。"
        "`share/rules/rules-*.md`、`agent-toolkit/share/rules/rules-*.md`、`share/exec-review.{parent,subagent}.md`、`src/*.py`を改める。"
        "略記の`rules/`・`UCR/`・`share/exec.parent.md`、範囲`src/`と`missing/`、"
        "src/のmodel.py・new_module.py・draft、`docs/development/new.md`と`docs/development/new.`も扱う。"
        "src/model.pyのWorker.runと`src/model.py:42`の`Service.start`も直す。\n"
    )

    assert check_selection.reflected_paths(body, repo) == {
        "agent-toolkit/share/exec.parent.md",
        "docs/development/design.md",
        "docs/development/new.md",
        "src/",
        "src/model.py",
        "src/new_module.py",
    }


_NON_ASCII_FILES = ("docs/dev/ログ監視.md", "docs/design/既存.md", "docs/café.md", "ログ/a.md", "docs/infra/クラウド料金.md")


def test_public_command_extracts_non_ascii_paths_whole(
    tmp_path: pathlib.Path, env: tuple[pathlib.Path, pathlib.Path], capsys: pytest.CaptureFixture[str]
) -> None:
    """インラインコードと文章中の非ASCIIを含むパスを末尾まで読み、個別のパスだけの`書込対象`を受理する。

    非ASCIIの手前で抽出が終わると、`docs/dev/`のような範囲や`docs/design/LLM`のような途中の名前が
    反映先になり、メインは実際には書かない範囲を`書込対象`へ置いて別レーンと重ねる。
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
    # 範囲の説明も抽出結果には保持し、書込予定とは区別する。
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
    """非書込範囲配下の個別書込を受理し、区分が矛盾する重複は報告する。"""
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


__all__ = ["fixture_env"]
