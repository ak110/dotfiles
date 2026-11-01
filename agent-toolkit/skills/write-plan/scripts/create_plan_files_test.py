"""現行の1ファイル計画作成処理を検証する。"""

import datetime
import json
import os
import pathlib
import subprocess
import time
import types
import typing

import check_plan_file
import create_plan_files
import pytest

from agent_toolkit import atk
from agent_toolkit._atk import plans as _atk_plans
from agent_toolkit._plan import locations as _locations
from agent_toolkit._testing import fork_runner as _fork_runner
from agent_toolkit._testing import git_repository
from agent_toolkit._testing import plan_fixture as _plan_fixture
from agent_toolkit._testing import stop_check_runner as _stop_check_runner
from agent_toolkit._testing.helpers import _write_transcript


@pytest.mark.parametrize(
    ("clock", "option", "value", "stem", "saved_month", "recorded_date"),
    [
        ("2026-10-31T14:59:00+00:00", "--lane", "lane-02", "31-2359_process-wi_レーン02", "2026/10", "2026-10-31"),
        ("2026-12-31T14:59:00+00:00", "--lane", "lane-02", "31-2359_process-wi_レーン02", "2026/12", "2026-12-31"),
        ("2026-10-31T15:01:00+00:00", "--name", "計画", "31-1501_計画", "2026/10", "2026-10-31"),
    ],
)
def test_created_date_survives_edit_save_checkout_and_resave(
    repo: pathlib.Path,
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    clock: str,
    option: str,
    value: str,
    stem: str,
    saved_month: str,
    recorded_date: str,
) -> None:
    """命名と同時に確定した日付を、月年跨ぎの編集と取得後にも保持する。"""
    instant = datetime.datetime.fromisoformat(clock)
    jst = datetime.timezone(datetime.timedelta(hours=9))

    class FixedDatetime(datetime.datetime):
        """ローカルをJSTとして命名時刻を固定する時計。"""

        @classmethod
        def now(cls, tz: datetime.tzinfo | None = None) -> typing.Self:
            return cls.fromtimestamp(instant.timestamp(), tz or jst)

        def astimezone(self, tz: datetime.tzinfo | None = None) -> typing.Self:
            return super().astimezone(tz or jst)

    monkeypatch.setattr(create_plan_files, "datetime", types.SimpleNamespace(datetime=FixedDatetime, UTC=datetime.UTC))
    source, bug_source = _source(repo, tmp_path, bug=True)
    assert bug_source is not None
    home = tmp_path / "home"
    notes = git_repository.init_repository(tmp_path / "notes", commit_message="初期状態")
    monkeypatch.setenv("AGENT_TOOLKIT_PRIVATE_NOTES", str(notes))
    assert (
        create_plan_files.main(
            [
                "--main-source",
                str(source),
                "--bugs-source",
                str(bug_source),
                option,
                value,
                "--home",
                str(home),
                "--work-dir",
                str(repo),
            ]
        )
        == 0
    )
    main = _locations.working_plans_root(home) / f"{stem}.md"
    record = main.with_suffix(".created.json")
    assert json.loads(record.read_text(encoding="utf-8")) == {"date": recorded_date}
    main.write_bytes(main.read_bytes() + "\n追記\n".encode())
    contents = {path.name: path.read_bytes() for path in (main, main.with_suffix(".bugs.md"))}

    def unavailable_creation(_path: pathlib.Path, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr(_locations, "_creation_epoch", unavailable_creation)
    # 推定だけなら翌月となる日まで編集し、局所記録の優先を検証する。
    edited = datetime.datetime(2027, 2, 28, 12).timestamp()
    os.utime(main, (edited, edited))
    portable = f"$(atk config get private_notes)/plans/{saved_month}/{main.name}"
    assert _locations.to_portable_plan_file(main, private_notes=notes, home=home) == portable
    assert [entry["path"] for entry in _atk_plans.list_working_plans(home)] == [str(main)]
    with pytest.raises(SystemExit) as saved:
        atk.main(["plans", "commit", main.name, "--skip-push"], home=home)
    assert saved.value.code == 0
    assert not record.exists()
    saved_main = notes / "plans" / saved_month / main.name
    assert {path.name: path.read_bytes() for path in saved_main.parent.iterdir()} == contents
    assert _locations.resolve_plan_file(portable, private_notes=notes, home=home) == saved_main
    with pytest.raises(SystemExit) as checked_out:
        atk.main(["plans", "checkout", f"{saved_month}/{main.name}"], home=home)
    assert checked_out.value.code == 0
    assert json.loads(record.read_text(encoding="utf-8")) == {"date": recorded_date}
    os.utime(main, (edited, edited))
    assert _locations.to_portable_plan_file(main, private_notes=notes, home=home) == portable
    assert {name: (main.parent / name).read_bytes() for name in contents} == contents
    with pytest.raises(SystemExit) as resaved:
        atk.main(["plans", "commit", main.name, "--skip-push"], home=home)
    assert resaved.value.code == 0
    assert not record.exists()
    assert {path.name: path.read_bytes() for path in saved_main.parent.iterdir()} == contents


@pytest.mark.parametrize("invalid", ["columns", "initial-progress"])
def test_document_template_rejects_invalid_initial_structure(
    repo: pathlib.Path,
    tmp_path: pathlib.Path,
    invalid: str,
) -> None:
    """雛形から記入しても、列数が違う表と起草時の進捗内容行は作成しない。"""
    source, _ = _source(repo, tmp_path)
    content = source.read_text(encoding="utf-8")
    if invalid == "columns":
        content = content.replace("| 実施内容 | 由来 | 採否 | 根拠 |", "| 実施内容 | 由来 | 採否 |")
    else:
        content += "| 2026-10-09 00:00 | 実装 | 成功 |\n"
    source.write_text(content, encoding="utf-8")
    assert (
        create_plan_files.main(
            ["--main-source", str(source), "--name", "invalid", "--work-dir", str(repo), "--home", str(tmp_path / "home")]
        )
        != 0
    )
    assert not list((tmp_path / "home" / ".claude" / "plans").glob("*.md"))


def _make_repo(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> pathlib.Path:
    """構造の判定に使うGitリポジトリを準備する。"""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    repo = tmp_path / "repo"
    repo.mkdir()
    git_repository.init_repository(repo)
    return repo


@pytest.fixture(name="repo")
def fixture_repo(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> pathlib.Path:
    """構造の判定に使うGitリポジトリを準備する。"""
    return _make_repo(tmp_path, monkeypatch)


def _source(
    repo: pathlib.Path,
    directory: pathlib.Path,
    *,
    bug: bool = False,
    bug_reference: str | None = None,
) -> tuple[pathlib.Path, pathlib.Path | None]:
    """計画の基準を満たす入力本文を保存する。

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

- 起動経路: `agent-toolkit:write-plan`
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

| シナリオ | 由来 | 消費主体と入口 | 利用目的と後続行動 | 操作 | 期待結果 | テスト |
| --- | --- | --- | --- | --- | --- | --- |
| 公開契約 | ユーザー指示 | ユーザーと公開入口 | 判定結果から次の作業を選ぶ | 判定する | 更新結果を得る | 対象の公開テスト |

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
| 変更範囲の検証 | `pytest` |

## 公開工程

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
    assert not path.with_suffix(".created.json").exists()


def test_wrong_execution_worktree_guides_recovery_without_changing_source(
    repo: pathlib.Path, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """対象と別の場所からの作成・確認は実行場所を案内し、その指定だけで同じ本文を作成できる。"""
    source, _bug_source = _source(repo, tmp_path)
    before = source.read_bytes()
    other = tmp_path / "other"
    other.mkdir()
    git_repository.init_repository(other)
    monkeypatch.chdir(other)
    args = ["--main-source", str(source), "--name", "13-0217_対象worktree", "--home", str(tmp_path / "home")]
    assert create_plan_files.main(args) == 1
    error = capsys.readouterr().err
    assert "--work-dir" in error and "列挙した各違反を計画本文" not in error
    assert check_plan_file.main([str(source)]) == 1
    error = capsys.readouterr().err
    assert "--work-dir" in error and "計画ファイルを直し" not in error
    assert source.read_bytes() == before
    assert create_plan_files.main([*args, "--work-dir", str(repo)]) == 0


def test_prior_plan_repository_mismatch_names_values_and_recovery(
    repo: pathlib.Path, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """先行計画だけ対象が異なる場合も、両値と入力・宣言の修正先を示す。"""
    monkeypatch.setenv("AGENT_TOOLKIT_PRIVATE_NOTES", str(tmp_path / "absent-notes"))
    source, _ = _source(repo, tmp_path)
    prior = tmp_path / "prior.md"
    other = tmp_path / "other"
    prior.write_text(source.read_text(encoding="utf-8").replace(str(repo.resolve()), str(other)), encoding="utf-8")
    source.write_text(
        source.read_text(encoding="utf-8").replace("- 関連WI: なし", "- 関連WI:\n  - 20261020-000000-001.md: 対応"),
        encoding="utf-8",
    )
    before = source.read_bytes(), prior.read_bytes()
    args = [str(source), "--work-dir", str(repo), "--prior-plan", str(prior), "--expected-wi", "20261020-000000-001.md"]
    assert check_plan_file.main(args) == 1
    error = capsys.readouterr().err
    assert f"先行計画の対象リポジトリが異なる: {prior}" in error
    assert f"宣言={other}、--work-dir={repo.resolve()}" in error
    assert "--prior-planへ同じ対象の計画" in error
    assert "--work-dirへ対象worktree" in error
    assert "先行計画の対象リポジトリを修正" in error
    assert "各行が示す規定のとおりに計画ファイルを直し" not in error
    assert (source.read_bytes(), prior.read_bytes()) == before
    corrected = tmp_path / "correct-prior.md"
    corrected.write_text(prior.read_text(encoding="utf-8").replace(str(other), str(repo.resolve())), encoding="utf-8")
    assert check_plan_file.main([value if value != str(prior) else str(corrected) for value in args]) == 0


@pytest.mark.parametrize("bom", [False, True])
def test_cli_accepts_bom_source_and_preserves_bytes(
    repo: pathlib.Path,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    bom: bool,
) -> None:
    """BOMの有無によらず計画を作成・構造チェックでき、原稿と本文中の文字を保持する。"""
    source, _ = _source(repo, tmp_path)
    content = source.read_text(encoding="utf-8").replace("対象の公開契約を更新する。", "対象の公開\ufeff契約を更新する。", 1)
    original = (("\ufeff" if bom else "") + content).encode("utf-8")
    source.write_bytes(original)
    assert (
        create_plan_files.main(
            [
                "--source",
                str(source),
                "--name",
                "13-0217_BOM受理",
                "--home",
                str(tmp_path / "home"),
                "--work-dir",
                str(repo),
            ]
        )
        == 0
    )
    captured = capsys.readouterr()
    path = pathlib.Path(captured.out.strip())
    assert path.read_bytes() == original
    errors, _warnings = check_plan_file.check(path, repo, home=tmp_path / "home", reject_migration_warnings=True)
    assert not errors


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
    monkeypatch.setattr(create_plan_files, "named_plan_name", lambda name, **_kwargs: f"18-0334_{name}")

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
        lambda lane, **_kwargs: "14-1405_process-wi_レーン02" if lane == "lane-02" else "unexpected",
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


@pytest.mark.parametrize(
    ("work_type", "bug_field", "provide_bug", "message"),
    [
        ("バグ対応", True, False, "行がある場合は計画ファイル（バグ）の入力が必要"),
        ("バグ対応", False, True, "行が無い場合は計画ファイル（バグ）の入力を指定できません"),
        ("通常変更", False, True, "作業種別が通常変更の場合"),
    ],
)
def test_rejects_work_type_and_bug_input_mismatch(
    repo: pathlib.Path, tmp_path: pathlib.Path, work_type: str, bug_field: bool, provide_bug: bool, message: str
) -> None:
    """作業種別・計画ファイル（バグ）の行・バグ入力の有無が一致しない本文を拒否する。"""
    reference = f"{create_plan_files.PLAN_ADJUNCT_REFERENCE_PREFIX}{create_plan_files.PLAN_STEM_PLACEHOLDER}.bugs.md"
    source = _related_wi_source(repo, tmp_path, work_type=work_type, bug_reference=reference if bug_field else None)
    bug_source = tmp_path / "bug.md"
    bug_source.write_text(_plan_fixture.bug_file(), encoding="utf-8")

    with pytest.raises(create_plan_files.PlanCreationError, match=message):
        create_plan_files.create_plan_files(
            source,
            "13-0217_入力不一致",
            bug_source=bug_source if provide_bug else None,
            home=tmp_path / "home",
            work_dir=repo,
        )


def _related_wi_source(
    repo: pathlib.Path, directory: pathlib.Path, *, work_type: str = "バグ対応", bug_reference: str | None = None
) -> pathlib.Path:
    """関連WIを持つ計画本文を保存してパスを返す。"""
    source = directory / "related-wi.md"
    source.write_text(
        _plan_fixture.current_plan(
            repo=repo.resolve(),
            work_type=work_type,
            related_wi=_plan_fixture.WI_FILES,
            bug_reference=bug_reference,
        ),
        encoding="utf-8",
    )
    return source


def test_cli_creates_bug_plan_without_bug_file_when_related_wi_exists(
    repo: pathlib.Path, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """関連WIの原因分析に従うバグ対応計画は、計画ファイル（バグ）なしで作成する。"""
    monkeypatch.setenv("AGENT_TOOLKIT_PRIVATE_NOTES", str(tmp_path / "absent-notes"))
    source = _related_wi_source(repo, tmp_path)

    result = create_plan_files.main(
        [
            "--main-source",
            str(source),
            "--name",
            "13-0217_WI参照のバグ対応",
            "--home",
            str(tmp_path / "home"),
            "--work-dir",
            str(repo),
        ]
    )

    captured = capsys.readouterr()
    assert result == 0, captured.err
    paths = [pathlib.Path(line) for line in captured.out.splitlines()]
    assert [path.name for path in paths] == ["13-0217_WI参照のバグ対応.md"]
    assert not list((tmp_path / "home").rglob("*.bugs.md"))


def test_cli_rejects_bug_plan_without_bug_file_and_related_wi(
    repo: pathlib.Path, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """関連WIが無いバグ対応計画は計画ファイル（バグ）の行と入力なしでは作成しない。"""
    source = tmp_path / "no-wi.md"
    source.write_text(
        _plan_fixture.current_plan(repo=repo.resolve(), work_type="バグ対応"),
        encoding="utf-8",
    )

    result = create_plan_files.main(
        [
            "--main-source",
            str(source),
            "--name",
            "13-0217_WIなしのバグ対応",
            "--home",
            str(tmp_path / "home"),
            "--work-dir",
            str(repo),
        ]
    )

    assert result != 0
    assert "関連WIが無いバグ対応の計画" in capsys.readouterr().err
    assert not list((tmp_path / "home").rglob("*.md"))


def test_cli_rejects_removed_detail_source() -> None:
    """廃止したdetail入力をCLIが受理しない。"""
    with pytest.raises(SystemExit) as raised:
        create_plan_files.main(["--detail-source", "detail.md", "--name", "13-0217_計画"])

    assert raised.value.code == 2


_CONVERSATION_ENVS = ("AGENT_TOOLKIT_OWNER_SESSION", "CLAUDE_CODE_SESSION_ID", "CODEX_THREAD_ID")


def _run_plan_save_advisor(home: pathlib.Path, state_dir: pathlib.Path, session_id: str) -> dict:
    """保存確認のStopフックを別プロセスで起動し、出力した判定を返す。

    フックは入力の`session_id`が所有記録の会話と一致する計画だけを通知する。起動したプロセスの環境は使わないため、会話を表す環境変数は除く。
    """
    transcript = _write_transcript(
        state_dir,
        [{"type": "assistant", "message": {"stop_reason": "end_turn", "content": [{"type": "text", "text": "完了"}]}}],
    )
    env = {key: value for key, value in os.environ.items() if key not in _CONVERSATION_ENVS}
    env.update(
        {"HOME": str(home), "USERPROFILE": str(home), "TMPDIR": str(state_dir), "TEMP": str(state_dir), "TMP": str(state_dir)}
    )
    payload = json.dumps({"session_id": session_id, "transcript_path": str(transcript)})
    result: subprocess.CompletedProcess[str] = _fork_runner.run_script(
        pathlib.Path(_stop_check_runner.__file__), argv=("plan_save_advisor",), input=payload, env=env
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout) if result.stdout else {}


@pytest.mark.parametrize(
    ("environment", "owner"),
    [
        ({"CODEX_THREAD_ID": "codex-thread"}, "codex-thread"),
        ({"CLAUDE_CODE_SESSION_ID": "claude-session"}, "claude-session"),
        ({"AGENT_TOOLKIT_OWNER_SESSION": "owner-session", "CODEX_THREAD_ID": "delegate-thread"}, "owner-session"),
        ({"AGENT_TOOLKIT_OWNER_SESSION": "mcp-0123456789abcdef0123456789abcdef", "CODEX_THREAD_ID": "delegate-thread"}, None),
    ],
    ids=["codex-top-level", "claude-top-level", "delegate-with-owner", "process-root"],
)
def test_public_plan_create_owner_reaches_listing_and_save_advisor(
    repo: pathlib.Path,
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    environment: dict[str, str],
    owner: str | None,
) -> None:
    """公開の`atk run-script plan-create`で作った計画の所有会話を、公開の一覧と保存確認フックまで対応付ける。

    作成処理が所有記録を書かなくなると、作成した会話の保存案内から計画が漏れる。
    プロセス専用のrootは会話へ対応しないため、どの会話の保存案内にも現れない。
    """
    for name in _CONVERSATION_ENVS:
        monkeypatch.delenv(name, raising=False)
    for name, value in environment.items():
        monkeypatch.setenv(name, value)
    home = tmp_path / "home"
    notes = git_repository.init_repository(tmp_path / "notes", commit_message="初期状態")
    monkeypatch.setenv("AGENT_TOOLKIT_PRIVATE_NOTES", str(notes))
    source, _bug_source = _source(repo, tmp_path)
    arguments = ["--main-source", str(source), "--name", "所有会話", "--home", str(home), "--work-dir", str(repo)]
    with pytest.raises(SystemExit) as created:
        atk.main(["run-script", "plan-create", "--", *arguments], home=home)
    assert created.value.code == 0
    (main,) = _locations.working_plans_root(home).glob("*_所有会話.md")

    capsys.readouterr()
    with pytest.raises(SystemExit) as listed:
        atk.main(["plans", "list"], home=home)
    assert listed.value.code == 0
    (line,) = capsys.readouterr().out.splitlines()
    assert line.split("\t")[:2] == [str(main), owner or "なし"]

    state_dir = tmp_path / "hook-state"
    state_dir.mkdir()
    assert not _run_plan_save_advisor(home, state_dir, "other-conversation")
    for session in ("codex-thread", "claude-session", "owner-session", "delegate-thread"):
        result = _run_plan_save_advisor(home, state_dir, session)
        if session == owner:
            assert str(main) in result["hookSpecificOutput"]["additionalContext"]
        else:
            assert not result
