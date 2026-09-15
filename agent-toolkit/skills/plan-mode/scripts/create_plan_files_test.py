"""現行の1ファイル計画作成処理を検証する。"""

import datetime
import pathlib
import subprocess
import tempfile

import create_plan_files
import pytest

from agent_toolkit._hooks import session_state as _session_state
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


@pytest.fixture(name="state_root")
def fixture_state_root(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> pathlib.Path:
    """セッション状態ファイルの保存先を実行環境の一時ディレクトリから隔離する。"""
    root = tmp_path / "state"
    root.mkdir()
    for name in ("TMPDIR", "TEMP", "TMP"):
        monkeypatch.setenv(name, str(root))
    monkeypatch.setattr(tempfile, "tempdir", str(root))
    return root


def _source(repo: pathlib.Path, directory: pathlib.Path, *, bug: bool = False) -> tuple[pathlib.Path, pathlib.Path | None]:
    """検査を通過する入力本文を保存する。"""
    main_source = directory / "main.md"
    bug_source = directory / "bug.md" if bug else None
    reference = (
        f"{create_plan_files.PLAN_ADJUNCT_REFERENCE_PREFIX}{create_plan_files.PLAN_STEM_PLACEHOLDER}.bugs.md" if bug else None
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
| 公開契約を更新する | ユーザー指示 | 採用 | - |

## 要件・外部仕様

公開契約の判定を更新する。

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
| 全体検証 | `make test` |

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


def test_records_direct_edit_completion_state(
    repo: pathlib.Path,
    tmp_path: pathlib.Path,
    state_root: pathlib.Path,  # pylint: disable=unused-argument
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """計画の確定後に連続直接編集検査の完了3項目を現在のセッションへ記録する。"""
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "current-session")
    monkeypatch.delenv("AGENT_TOOLKIT_OWNER_SESSION", raising=False)
    _session_state.update_state(
        "current-session",
        lambda current: {**current, "direct_agent_toolkit_edit_count": 2, "last_agent_toolkit_edit_path": "/repo/a.md"},
    )
    source, _bug_source = _source(repo, tmp_path)

    create_plan_files.create_plan_files(source, "13-0217_状態記録", home=tmp_path / "home", work_dir=repo)

    state = _session_state.read_state("current-session")
    assert state["plan_file_written"] is True
    assert state["direct_agent_toolkit_edit_count"] == 0
    assert state["last_agent_toolkit_edit_path"] is None


def test_skips_state_record_for_delegating_session(
    repo: pathlib.Path,
    tmp_path: pathlib.Path,
    state_root: pathlib.Path,  # pylint: disable=unused-argument
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """自身のセッション識別子が無い場合は委譲元の識別子へ記録せず作成に成功する。"""
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)
    monkeypatch.setenv("AGENT_TOOLKIT_OWNER_SESSION", "owner-session")
    source, _bug_source = _source(repo, tmp_path)

    (path,) = create_plan_files.create_plan_files(source, "13-0217_状態非記録", home=tmp_path / "home", work_dir=repo)

    assert path.is_file()
    assert _session_state.read_state("owner-session") == {}


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


def test_process_lane_plan_name_uses_utc_and_two_digit_lane() -> None:
    """process-wiのstemをUTC時刻と2桁レーン番号から生成する。"""
    now = datetime.datetime(2026, 9, 14, 23, 5, tzinfo=datetime.timezone(datetime.timedelta(hours=9)))

    assert create_plan_files.process_lane_plan_name("lane-02", now=now) == "14-1405_process-wi_レーン02"


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
    step_start = content.index("\n4. ") + 1
    step_end = content.index("\n5. ", step_start)
    return content[step_start:step_end]


def test_process_lane_task_prepares_sources_before_creation() -> None:
    """レーン手順は本文の保存を作成処理より前へ置く。"""
    step = _lane_plan_creation_step()

    assert step.index("管理対象一時領域のファイルへ保存する") < step.index("create_plan_files.py")


@pytest.mark.parametrize("bug", [False, True])
def test_lane_plan_creation_step_arguments_are_accepted_by_current_cli(
    repo: pathlib.Path,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    bug: bool,
) -> None:
    """手順4が指示する引数名を現行CLIへそのまま渡して受理されることを確認する。"""
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
        assert f"{option} <" in step, f"手順4が{option}を指示していない"
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
