"""現行の1ファイル計画作成処理を検証する。"""

import datetime
import pathlib
import re
import subprocess
import time

import create_plan_files
import pytest

from agent_toolkit._plan import fixture as _plan_fixture


def _git(repo: pathlib.Path, *args: str) -> str:
    """テスト用Gitリポジトリでコマンドを実行する。"""
    result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True)
    return result.stdout.strip()


@pytest.fixture(name="repo")
def fixture_repo(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> pathlib.Path:
    """構造検査へ渡すGitリポジトリを準備する。"""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    return repo


def _source(
    repo: pathlib.Path,
    directory: pathlib.Path,
    *,
    bug: bool = False,
    bug_reference: str | None = None,
) -> tuple[pathlib.Path, pathlib.Path | None]:
    """検査を通過する入力本文を保存する。

    `bug_reference`は計画本文へ書く計画ファイル（バグ）の参照値を差し替える。
    """
    main_source = directory / "main.md"
    bug_source = directory / "bug.md" if bug else None
    reference = (
        bug_reference or f"{create_plan_files.PLAN_ADJUNCT_REFERENCE_PREFIX}{create_plan_files.PLAN_STEM_PLACEHOLDER}.bugs.md"
        if bug
        else None
    )
    bug_line = f"\n- 計画ファイル（バグ）: `{reference}`" if reference is not None else ""
    content = f"""# 計画の主題

## 概要

対象の公開契約を更新する。

### 計画メタ情報

- 起動経路: `agent-toolkit:plan-mode`
- 対象リポジトリ: `{repo.resolve()}`
- 関連WI: なし
- 作業種別: {"バグ対応" if bug else "通常変更"}{bug_line}

## 実施内容

| 実施内容 | 由来 | 採否 | 根拠 |
| --- | --- | --- | --- |
| 公開契約を更新する | ユーザー指示 | 採用 | 原文の要求単位は1件であり、開放性を保ったまま実施範囲とする。 |

## 要件・外部仕様

公開契約の判定を更新する。

### 受入シナリオ

| シナリオ | 由来 | 利用者と入口 | 操作 | 期待結果 | テスト |
| --- | --- | --- | --- | --- | --- |
| 公開契約 | ユーザー指示 | 利用者と公開入口 | 判定する | 更新結果を得る | 対象の公開テスト |

## 恒久化・リファクタリング

### 恒久化

| 知見 | 出所 | 反映先 | 根拠 |
| --- | --- | --- | --- |
| 判定契約 | ユーザー指示 | 対象コード | 実装で参照するため。 |

### リファクタリング

| 対象 | 現状の問題 | 対応 |
| --- | --- | --- |
| 判定処理 | 契約が旧い。 | 更新する。 |

## 変更履歴

### ユーザー発言1

```text
公開契約を更新する。
```

## 検証

| 区分 | 検証コマンド |
| --- | --- |
| 近接検証 | `pytest` |

## 終端工程

なし

## 進捗ログ

| 日時 | 完了した工程 | 結果・特記事項 |
| --- | --- | --- |
"""
    main_source.write_text(content, encoding="utf-8")
    if bug_source is not None:
        bug_source.write_text(
            _plan_fixture.bug_file(title=f"計画の主題 {create_plan_files.PLAN_STEM_PLACEHOLDER}"),
            encoding="utf-8",
        )
    return main_source, bug_source


def test_creates_single_file_with_supplied_stem(repo: pathlib.Path, tmp_path: pathlib.Path) -> None:
    """呼び出し側が渡したstemを変更せず通常計画へ使う。"""
    source, _bug_source = _source(repo, tmp_path)

    (path,) = create_plan_files.create_plan_files(source, "13-0217_計画保存先移行", home=tmp_path / "home", work_dir=repo)

    assert path == tmp_path / "home/.claude/plans/13-0217_計画保存先移行.md"
    assert path.is_file()


def test_named_plan_name_adds_utc_timestamp_to_bare_name() -> None:
    """名称だけの入力へ作成処理がUTCの日付と時刻を付ける。"""
    now = datetime.datetime(2026, 9, 18, 12, 34, tzinfo=datetime.timezone(datetime.timedelta(hours=9)))

    assert create_plan_files.named_plan_name("計画", now=now) == "18-0334_計画"


def test_named_plan_name_keeps_complete_stem() -> None:
    """日時接頭辞を持つ完全stemは変更しない。"""
    assert create_plan_files.named_plan_name("18-0334_計画") == "18-0334_計画"


def test_named_plan_name_rejects_naive_clock() -> None:
    """日時を生成する時計にはタイムゾーンを要求する。"""
    with pytest.raises(ValueError, match="タイムゾーン"):
        create_plan_files.named_plan_name("計画", now=datetime.datetime(2026, 9, 18, 3, 34))


def test_cli_accepts_bare_name(
    repo: pathlib.Path,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CLIの--nameへ名称だけを渡して日時付きファイルを作成できる。"""
    source, _bug_source = _source(repo, tmp_path)
    monkeypatch.setattr(create_plan_files, "named_plan_name", lambda name: f"18-0334_{name}")

    result = create_plan_files.main(
        [
            "--main-source",
            str(source),
            "--name",
            "計画",
            "--home",
            str(tmp_path / "home"),
            "--work-dir",
            str(repo),
        ]
    )

    captured = capsys.readouterr()
    assert result == 0, captured.err
    assert pathlib.Path(captured.out.strip()).name == "18-0334_計画.md"


def test_cli_help_describes_name_and_lane(capsys: pytest.CaptureFixture[str]) -> None:
    """呼び出し側が--helpだけで名称生成の受理形式を確定できる。"""
    with pytest.raises(SystemExit) as raised:
        create_plan_files.main(["--help"])

    assert raised.value.code == 0
    output = capsys.readouterr().out
    assert "名称だけの場合はUTCの日時を付ける" in output
    assert "lane-NN形式のレーン識別子" in output


def test_cli_accepts_source_alias_and_creates_bug_file(
    repo: pathlib.Path, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """CLIのsource別名とbugs入力から同じstemの2ファイルを作成する。"""
    source, bug_source = _source(repo, tmp_path, bug=True)
    assert bug_source is not None

    result = create_plan_files.main(
        [
            "--source",
            str(source),
            "--bugs-source",
            str(bug_source),
            "--name",
            "13-0217_バグ対応",
            "--home",
            str(tmp_path / "home"),
            "--work-dir",
            str(repo),
        ]
    )

    captured = capsys.readouterr()
    assert result == 0, captured.err
    paths = [pathlib.Path(line) for line in captured.out.splitlines()]
    assert [path.name for path in paths] == ["13-0217_バグ対応.md", "13-0217_バグ対応.bugs.md"]
    assert all(create_plan_files.PLAN_STEM_PLACEHOLDER not in path.read_text(encoding="utf-8") for path in paths)


_DOCUMENTED_BUG_REFERENCE_PATTERN = re.compile(r"^- 計画ファイル（バグ）: `([^`]+)`$", re.MULTILINE)


def _documented_bug_reference() -> str:
    """計画ファイル作成基準が計画メタ情報の例として示す計画ファイル（バグ）の参照値を返す。"""
    standards = pathlib.Path(__file__).resolve().parents[3] / "skills/plan-mode/references/plan-file-standards.md"
    references = _DOCUMENTED_BUG_REFERENCE_PATTERN.findall(standards.read_text(encoding="utf-8"))
    assert len(references) == 1, "計画ファイル（バグ）の参照値の記載例が1件に定まらない"
    return references[0]


def test_documented_bug_reference_is_accepted_without_substitution(repo: pathlib.Path, tmp_path: pathlib.Path) -> None:
    """計画ファイル作成基準の記載例を置換せず転記した計画本文を作成処理が受理する。

    記載例は起草者がそのまま書き写す値であり、受理形式を満たさない例は計画作成のやり直しを招く。
    書式の正本（固定プレースホルダー）を改訂したときに記載例が追随しない状態を、本テストが検出する。
    """
    reference = _documented_bug_reference()
    assert reference.startswith(create_plan_files.PLAN_ADJUNCT_REFERENCE_PREFIX)
    source, bug_source = _source(repo, tmp_path, bug=True, bug_reference=reference)

    paths = create_plan_files.create_plan_files(
        source,
        "13-0217_記載例の転記",
        bug_source=bug_source,
        home=tmp_path / "home",
        work_dir=repo,
    )

    assert [path.name for path in paths] == ["13-0217_記載例の転記.md", "13-0217_記載例の転記.bugs.md"]
    assert all(create_plan_files.PLAN_STEM_PLACEHOLDER not in path.read_text(encoding="utf-8") for path in paths)


def test_process_lane_plan_name_uses_local_time_and_two_digit_lane() -> None:
    """process-wiのstemを実行環境のローカル時刻と2桁レーン番号から生成する。"""
    now = datetime.datetime(2026, 9, 14, 23, 5, tzinfo=datetime.timezone(datetime.timedelta(hours=9)))

    expected = time.strftime("%d-%H%M", time.localtime(now.timestamp()))
    assert create_plan_files.process_lane_plan_name("lane-02", now=now) == f"{expected}_process-wi_レーン02"


@pytest.mark.parametrize("lane", ["lane-2", "lane-002", "02", "lane-aa"])
def test_process_lane_plan_name_rejects_noncanonical_identifier(lane: str) -> None:
    """2桁の正規レーン識別子以外を拒否する。"""
    with pytest.raises(ValueError, match="lane-NN"):
        create_plan_files.process_lane_plan_name(lane)


def test_cli_creates_process_lane_plan_with_generated_name(
    repo: pathlib.Path, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """CLIのlane入力では呼び出し側から自由名を受け取らない。"""
    source, _bug_source = _source(repo, tmp_path)
    monkeypatch.setattr(
        create_plan_files,
        "process_lane_plan_name",
        lambda lane: "14-1405_process-wi_レーン02" if lane == "lane-02" else "unexpected",
    )

    result = create_plan_files.main(
        [
            "--main-source",
            str(source),
            "--lane",
            "lane-02",
            "--home",
            str(tmp_path / "home"),
            "--work-dir",
            str(repo),
        ]
    )

    captured = capsys.readouterr()
    assert result == 0, captured.err
    assert pathlib.Path(captured.out.strip()).name == "14-1405_process-wi_レーン02.md"


def _lane_plan_creation_step() -> str:
    """レーン担当が読む計画作成手順の本文を返す。"""
    task_path = pathlib.Path(__file__).resolve().parents[3] / "share/exec.subagent.md"
    content = task_path.read_text(encoding="utf-8")
    steps = re.split(r"\n(?=\d+\. )", content)
    matches = [step for step in steps if re.match(r"\d+\. ", step) and "plan-create" in step]
    assert len(matches) == 1
    return matches[0]


@pytest.mark.parametrize("bug", [False, True])
def test_lane_plan_creation_step_arguments_are_accepted_by_current_cli(
    repo: pathlib.Path,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    bug: bool,
) -> None:
    """手順5が指示する引数名を現行CLIへそのまま渡して受理されることを確認する。"""
    step = _lane_plan_creation_step()
    source, bug_source = _source(repo, tmp_path, bug=bug)
    placeholders = {
        "--main-source": str(source),
        "--lane": "lane-02",
    }
    if bug:
        placeholders["--bugs-source"] = str(bug_source)
    argv: list[str] = []
    for option, value in placeholders.items():
        assert f"{option} <" in step, f"手順5が{option}を指示していない"
        argv.extend([option, value])
    argv.extend(["--home", str(tmp_path / "home"), "--work-dir", str(repo)])

    result = create_plan_files.main(argv)

    captured = capsys.readouterr()
    assert result == 0, captured.err
    created = [pathlib.Path(line) for line in captured.out.splitlines() if line]
    assert len(created) == (2 if bug else 1)
    assert all(path.exists() for path in created)


def test_adds_hex_suffix_only_after_collision(
    repo: pathlib.Path, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """第一候補が衝突した場合だけ4桁16進suffixを付ける。"""
    plans = tmp_path / "home/.claude/plans"
    plans.mkdir(parents=True)
    (plans / "13-0217_計画.md").write_text("既存\n", encoding="utf-8")
    source, _bug_source = _source(repo, tmp_path)
    monkeypatch.setattr(create_plan_files.secrets, "token_hex", lambda _bytes: "a1b2")

    (path,) = create_plan_files.create_plan_files(source, "13-0217_計画", home=tmp_path / "home", work_dir=repo)

    assert path.name == "13-0217_計画-a1b2.md"


@pytest.mark.parametrize(("bug", "provide_bug"), [(True, False), (False, True)])
def test_rejects_work_type_and_bug_input_mismatch(
    repo: pathlib.Path, tmp_path: pathlib.Path, bug: bool, provide_bug: bool
) -> None:
    """作業種別とバグ入力の有無が一致しない本文を拒否する。"""
    source, bug_source = _source(repo, tmp_path, bug=bug)
    if provide_bug and bug_source is None:
        bug_source = tmp_path / "bug.md"
        bug_source.write_text(_plan_fixture.bug_file(), encoding="utf-8")

    with pytest.raises(create_plan_files.PlanCreationError, match="作業種別"):
        create_plan_files.create_plan_files(
            source,
            "13-0217_入力不一致",
            bug_source=bug_source if provide_bug else None,
            home=tmp_path / "home",
            work_dir=repo,
        )


def test_cli_rejects_removed_detail_source() -> None:
    """廃止したdetail入力をCLIが受理しない。"""
    with pytest.raises(SystemExit) as raised:
        create_plan_files.main(["--detail-source", "detail.md", "--name", "13-0217_計画"])

    assert raised.value.code == 2
