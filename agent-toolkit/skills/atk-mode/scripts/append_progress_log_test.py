import argparse
import datetime
import json
import pathlib

import append_progress_log
import pytest
from agent_toolkit._atk import run_script
from agent_toolkit._testing import git_repository


def _plan(heading: str = "進捗ログ", *, newline: str = "\n", final_newline: bool = True) -> str:
    content = newline.join(
        [
            "# 計画",
            "",
            f"## {heading}",
            "",
            "| 日時 | 完了した工程 | 結果・特記事項 |",
            "| --- | --- | --- |",
        ]
    )
    return content + (newline if final_newline else "")


@pytest.mark.parametrize("heading", ["進捗ログ", "進捗ログ（実行時）"])
def test_appends_local_clock_row_for_current_and_legacy_heading(tmp_path: pathlib.Path, heading: str) -> None:
    path = tmp_path / "plan.md"
    path.write_text(_plan(heading), encoding="utf-8")
    now = datetime.datetime(2026, 9, 20, 12, 34, tzinfo=datetime.timezone(datetime.timedelta(hours=9)))

    append_progress_log.append_progress_log(path, "工程", "成功", clock=lambda: now)

    assert path.read_text(encoding="utf-8").endswith("| 2026-09-20T12:34:00+09:00 | 工程 | 成功 |\n")


def test_keeps_minute_format_when_plan_already_has_minute_rows(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "plan.md"
    path.write_text(_plan() + "| 2026-09-20 12:00 | 開始: 実装 | |\n", encoding="utf-8")
    now = datetime.datetime(2026, 9, 20, 12, 34, 56, tzinfo=datetime.timezone(datetime.timedelta(hours=9)))

    append_progress_log.append_progress_log(path, "終了: 実装", "成功", clock=lambda: now)

    assert path.read_text(encoding="utf-8").endswith("| 2026-09-20 12:34 | 終了: 実装 | 成功 |\n")


def test_preserves_crlf_and_missing_final_newline(tmp_path: pathlib.Path) -> None:
    """既存のCRLFを維持し、末尾改行が無い表にも1行を追加する。"""
    path = tmp_path / "plan.md"
    path.write_bytes(_plan(newline="\r\n", final_newline=False).encode())
    now = datetime.datetime(2026, 9, 20, 3, 34, tzinfo=datetime.UTC)

    append_progress_log.append_progress_log(path, "工程", "成功", clock=lambda: now)

    updated = path.read_bytes()
    assert b"\r\n" in updated
    assert not updated.endswith(b"\n")
    assert updated.endswith("| 2026-09-20T03:34:00+00:00 | 工程 | 成功 |".encode())


def test_escapes_table_cells(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "plan.md"
    path.write_text(_plan(), encoding="utf-8")
    now = datetime.datetime(2026, 9, 20, 3, 34, tzinfo=datetime.UTC)

    append_progress_log.append_progress_log(path, "工程|A\nB", r"結果\値|C", clock=lambda: now)

    assert r"工程\|A<br>B | 結果\\値\|C" in path.read_text(encoding="utf-8")


def test_ignores_progress_heading_inside_code_fence(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "plan.md"
    path.write_text("# 計画\n\n````markdown\n## 進捗ログ\n````\n\n" + _plan().removeprefix("# 計画\n\n"), encoding="utf-8")
    now = datetime.datetime(2026, 9, 20, 3, 34, tzinfo=datetime.UTC)

    append_progress_log.append_progress_log(path, "工程", "成功", clock=lambda: now)

    assert path.read_text(encoding="utf-8").endswith("| 2026-09-20T03:34:00+00:00 | 工程 | 成功 |\n")


@pytest.mark.parametrize(
    "content",
    [
        "# 計画\n",
        _plan() + _plan(),
        "# 計画\n\n## 進捗ログ\n\n本文だけ。\n",
        "# 計画\n\n## 進捗ログ\n\n| 日時 | 工程 | 結果 |\n| --- | --- | --- |\n",
    ],
)
def test_rejects_invalid_structure_without_changes(tmp_path: pathlib.Path, content: str) -> None:
    path = tmp_path / "plan.md"
    original = content.encode()
    path.write_bytes(original)

    with pytest.raises(append_progress_log.ProgressLogError) as raised:
        append_progress_log.append_progress_log(path, "工程", "結果")

    assert path.read_bytes() == original

    next_action = raised.value.next_action
    assert "`atk run-script plan-check --" in next_action or "の列）だけの固定表を置いてから再実行する" in next_action


@pytest.mark.parametrize("handoff", [False, True])
def test_public_cli_records_and_reads_commit_mapping(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], handoff: bool
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    for args in [
        ["init"],
        ["-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "--allow-empty", "-m", "example"],
    ]:
        git_repository.run_git(repo, *args)
    previous_head = git_repository.run_git(repo, "rev-parse", "HEAD").stdout.strip()
    git_repository.run_git(
        repo, "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "--allow-empty", "-m", "recorded"
    )
    oid = git_repository.run_git(repo, "rev-parse", "HEAD").stdout.strip()
    wi = "20261004-044311-001.md"
    path = tmp_path / "record.md"
    original = (
        "# 引継ぎ\n\n既存の判断。\n"
        if handoff
        else (
            "# 計画\n\n## 概要\n\n### 計画メタ情報\n\n- 関連WI:\n  - " + wi + ": 対応\n\n" + _plan().removeprefix("# 計画\n\n")
        )
    )
    path.write_text(original, encoding="utf-8")
    common = [str(path), "--worktree", str(repo), "--awi", wi]
    if handoff:
        common += ["--handoff", "--allowed-awi", wi]
    assert (
        append_progress_log.main(
            [*common, "--commit", "HEAD", "--previous-head", previous_head, "--completed-step", "実装", "--result", "成功"]
        )
        == 0
    )
    body = path.read_text(encoding="utf-8")
    assert body.startswith(original)
    assert "実装" in body.removeprefix(original)
    assert "wi-commits" not in body
    attachment = tmp_path / "record.wi-commits.jsonl"
    short = git_repository.git_output(repo, "rev-parse", "--short", oid)
    assert [json.loads(line) for line in attachment.read_text(encoding="utf-8").splitlines()] == [
        {"commits": [short], "awi": [wi]}
    ]
    assert append_progress_log.main([*common], retrieval=True) == 0
    assert json.loads(capsys.readouterr().out) == {"awi": wi, "commits": [short]}
    saved = path.read_bytes()
    saved_attachment = attachment.read_bytes()
    assert (
        append_progress_log.main(
            [
                *common,
                "--commit",
                "missing-commit",
                "--previous-head",
                previous_head,
                "--completed-step",
                "実装",
                "--result",
                "成功",
            ]
        )
        == 1
    )
    assert path.read_bytes() == saved
    assert attachment.read_bytes() == saved_attachment
    assert "次の操作:" in capsys.readouterr().err
    with pytest.raises(SystemExit, match="2"):
        append_progress_log.main([*common, "--commit", oid, "--completed-step", "実装", "--result", "失敗"])
    assert path.read_bytes() == saved
    capsys.readouterr()
    assert (
        append_progress_log.main(
            [*common, "--commit", oid, "--previous-head", oid, "--completed-step", "実装", "--result", "失敗"]
        )
        == 1
    )
    assert path.read_bytes() == saved
    capsys.readouterr()

    git_repository.run_git(
        repo, "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "--amend", "--allow-empty", "-m", "new"
    )
    new_oid = git_repository.run_git(repo, "rev-parse", "HEAD").stdout.strip()

    malformed = tmp_path / "malformed.json"
    malformed.write_text("{", encoding="utf-8")
    saved = path.read_bytes()
    for value in [json.dumps({oid: new_oid}), str(tmp_path / "missing.json"), str(malformed)]:
        assert (
            append_progress_log.main([*common, "--rewrite-map", value, "--completed-step", "履歴検収", "--result", "失敗"]) == 1
        )
        error = capsys.readouterr().err
        assert "--rewrite-map" in error
        assert "計画ファイル" not in error
        assert path.read_bytes() == saved
    replacements = tmp_path / "rewrite.json"
    replacements.write_text(json.dumps({short: new_oid}), encoding="utf-8")
    assert (
        append_progress_log.main(
            [*common, "--rewrite-map", str(replacements), "--completed-step", "履歴検収", "--result", "成功"]
        )
        == 0
    )
    assert "wi-commits" not in path.read_text(encoding="utf-8")
    new_short = git_repository.git_output(repo, "rev-parse", "--short", new_oid)
    assert json.loads(attachment.read_text(encoding="utf-8").splitlines()[-1]) == {"rewrite": {short: new_short}}
    assert append_progress_log.main([*common], retrieval=True) == 0
    assert json.loads(capsys.readouterr().out) == {"awi": wi, "commits": [new_short]}


def test_one_commit_records_multiple_awi(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    """一体の変更を1つのcommitにまとめても、1回の記録で全AWIへ同じcommitが対応付く。

    commitの単位を作業項目から切り離す規定（commit/SKILL.md「通常commit」節）が、
    作業項目ごとの採否とcommitの対応を失わずに成り立つことを固定する。
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    git_repository.run_git(repo, "init")
    identity = ["-c", "user.name=Test", "-c", "user.email=test@example.invalid"]
    git_repository.run_git(repo, *identity, "commit", "--allow-empty", "-m", "base")
    previous_head = git_repository.run_git(repo, "rev-parse", "HEAD").stdout.strip()
    git_repository.run_git(repo, *identity, "commit", "--allow-empty", "-m", "combined")
    short = git_repository.git_output(repo, "rev-parse", "--short", "HEAD")
    wis = ["20261111-000000-001.md", "20261111-000000-002.md"]
    path = tmp_path / "plan.md"
    path.write_text(
        "# 計画\n\n## 概要\n\n### 計画メタ情報\n\n- 関連WI:\n"
        + "".join(f"  - {wi}: 対応\n" for wi in wis)
        + "\n"
        + _plan().removeprefix("# 計画\n\n"),
        encoding="utf-8",
    )
    awi_args = [arg for wi in wis for arg in ("--awi", wi)]
    common = [str(path), "--worktree", str(repo)]
    assert (
        append_progress_log.main(
            [
                *common,
                *awi_args,
                "--commit",
                "HEAD",
                "--previous-head",
                previous_head,
                "--completed-step",
                "実装",
                "--result",
                "成功",
            ]
        )
        == 0
    )
    assert append_progress_log.main(common, retrieval=True) == 0
    assert [json.loads(line) for line in capsys.readouterr().out.splitlines()] == [
        {"awi": wi, "commits": [short]} for wi in wis
    ]


def test_cli_structure_error_reports_next_action(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / "plan.md"
    path.write_text("# 計画\n", encoding="utf-8")

    assert append_progress_log.main([str(path), "--completed-step", "工程", "--result", "結果"]) == 1

    lines = capsys.readouterr().err.splitlines()
    assert lines[0].startswith("進捗ログを更新できません: ")
    assert lines[1].startswith("次の操作: `atk run-script plan-check --")


def test_rejects_non_utf8_without_changes(tmp_path: pathlib.Path) -> None:
    """UTF-8でない入力は変更しない。"""
    path = tmp_path / "plan.md"
    original = b"\x81"
    path.write_bytes(original)

    with pytest.raises(append_progress_log.ProgressLogError, match="UTF-8"):
        append_progress_log.append_progress_log(path, "工程", "結果")

    assert path.read_bytes() == original


def test_writer_failure_keeps_original_file(tmp_path: pathlib.Path) -> None:
    """原子的書込みが失敗した場合は元ファイルを保つ。"""
    path = tmp_path / "plan.md"
    original = _plan().encode()
    path.write_bytes(original)

    def fail_writer(_path: pathlib.Path, _content: str) -> None:
        raise OSError("write failed")

    with pytest.raises(OSError, match="write failed"):
        append_progress_log.append_progress_log(path, "工程", "結果", writer=fail_writer)

    assert path.read_bytes() == original


def test_cli_rejects_removed_start_head_option(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "plan.md"
    path.write_text(_plan(), encoding="utf-8")
    saved = path.read_bytes()
    with pytest.raises(SystemExit) as error_exit:
        append_progress_log.main([str(path), "--completed-step", "開始", "--result", "専用worktree", "--start-head", "HEAD"])
    assert error_exit.value.code == 2
    assert path.read_bytes() == saved


def test_main_rejects_saved_plan_root(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """保存済み計画の領域の計画は変更せずに失敗し、取得と保存の手順を示す。"""
    private_notes = tmp_path / "private-notes"
    monkeypatch.setenv("AGENT_TOOLKIT_PRIVATE_NOTES", str(private_notes))
    path = private_notes / "plans" / "2026" / "09" / "28-example-1a2b.md"
    path.parent.mkdir(parents=True)
    path.write_text(_plan(), encoding="utf-8")
    original = path.read_bytes()

    assert append_progress_log.main([str(path), "--completed-step", "工程", "--result", "結果"]) == 1

    error = capsys.readouterr().err
    assert "\n次の操作: " in error
    assert "`atk plans checkout 2026/09/28-example-1a2b.md`" in error
    assert "`atk plans commit 2026/09/28-example-1a2b.md`" in error
    assert path.read_bytes() == original


def test_get_commits_reads_unique_saved_plan_from_removed_working_path(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    notes = tmp_path / "private-notes"
    monkeypatch.setenv("AGENT_TOOLKIT_PRIVATE_NOTES", str(notes))
    repo = tmp_path / "repo"
    repo.mkdir()
    git_repository.init_repository(repo)
    git_repository.run_git(
        repo, "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "--allow-empty", "-m", "example"
    )
    previous_head = git_repository.run_git(repo, "rev-parse", "HEAD").stdout.strip()
    git_repository.run_git(
        repo, "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "--allow-empty", "-m", "recorded"
    )
    wi = "20261004-044311-001.md"
    working = tmp_path / ".claude" / "plans" / "04-example-1a2b.md"
    saved = notes / "plans" / "2026" / "10" / working.name
    saved.parent.mkdir(parents=True)
    content = (
        "# 計画\n\n## 概要\n\n### 計画メタ情報\n\n- 関連WI:\n  - " + wi + ": 対応\n\n" + _plan().removeprefix("# 計画\n\n")
    )
    saved.write_text(content, encoding="utf-8")
    record_args = [
        "--worktree",
        str(repo),
        "--awi",
        wi,
        "--commit",
        "HEAD",
        "--previous-head",
        previous_head,
        "--completed-step",
        "実装",
        "--result",
        "成功",
    ]
    assert append_progress_log.main([str(saved), *record_args]) == 1
    working.parent.mkdir(parents=True)
    working.write_text(content, encoding="utf-8")
    assert append_progress_log.main([str(working), *record_args]) == 0
    saved.write_bytes(working.read_bytes())
    working.unlink()

    attachment = working.with_name(working.stem + ".wi-commits.jsonl")
    attachment.rename(saved.with_name(attachment.name))
    assert append_progress_log.main([str(working), "--worktree", str(repo), "--awi", wi], retrieval=True) == 0
    assert json.loads(capsys.readouterr().out)["awi"] == wi
    duplicate = notes / "plans" / "2025" / "10" / working.name
    duplicate.parent.mkdir(parents=True)
    duplicate.write_bytes(saved.read_bytes())
    assert append_progress_log.main([str(working), "--worktree", str(repo), "--awi", wi], retrieval=True) == 1
    assert "一意に特定できません" in capsys.readouterr().err
    assert append_progress_log.main([str(saved), "--worktree", str(repo), "--awi", wi], retrieval=True) == 0
    saved.unlink()
    duplicate.unlink()
    assert append_progress_log.main([str(working), "--worktree", str(repo), "--awi", wi], retrieval=True) == 1
    assert "同名=0件" in capsys.readouterr().err


def _related_plan(*wis: str) -> str:
    related = "".join(f"  - {wi}: 対応\n" for wi in wis)
    return "# 計画\n\n## 概要\n\n### 計画メタ情報\n\n- 関連WI:\n" + related + "\n" + _plan().removeprefix("# 計画\n\n")


@pytest.mark.usefixtures("leaf_git_commands")
@pytest.mark.cpu_limit(
    1.0,
    reason=("実Gitの状態・履歴を使う結合境界。修正前Linux4並列全件計測の所有CPU最大0.465秒。"),
)
@pytest.mark.parametrize("handoff", [False, True])
def test_records_range_from_cherry_pick_and_fast_forward_in_one_call(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], handoff: bool
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    git_repository.init_repository(repo, initial_branch="main")
    git_repository.git_output(repo, "commit", "--allow-empty", "-m", "base")
    git_repository.git_output(repo, "switch", "-c", "group")
    for message in ("group-1", "group-2"):
        git_repository.git_output(repo, "commit", "--allow-empty", "-m", message)
    git_repository.git_output(repo, "switch", "main")
    wi = "20261004-044311-001.md"
    path = tmp_path / "record.md"
    path.write_text("# 引継ぎ\n" if handoff else _related_plan(wi), encoding="utf-8")
    common = [str(path), "--worktree", str(repo), "--awi", wi]
    if handoff:
        common += ["--handoff", "--allowed-awi", wi]
    previous = git_repository.git_output(repo, "rev-parse", "--short", "HEAD")
    git_repository.git_output(repo, "merge", "--ff-only", "group")
    assert (
        append_progress_log.main(
            [*common, "--commit", "HEAD", "--previous-head", previous, "--completed-step", "統合", "--result", "ff"]
        )
        == 0
    )
    git_repository.git_output(repo, "commit", "--allow-empty", "-m", "lane")
    git_repository.git_output(repo, "switch", "-c", "group2")
    for message in ("group2-1", "group2-2", "group2-3"):
        git_repository.git_output(repo, "commit", "--allow-empty", "-m", message)
    git_repository.git_output(repo, "switch", "main")
    lane = git_repository.git_output(repo, "rev-parse", "HEAD")
    git_repository.git_output(repo, "cherry-pick", "--allow-empty", "main..group2")
    assert (
        append_progress_log.main(
            [*common, "--commit", "HEAD", "--previous-head", lane, "--completed-step", "統合", "--result", "pick"]
        )
        == 0
    )
    records = (tmp_path / "record.wi-commits.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(records) == 2
    assert [len(json.loads(line)["commits"]) for line in records] == [2, 3]
    assert append_progress_log.main([*common], retrieval=True) == 0
    commits = json.loads(capsys.readouterr().out)["commits"]
    added = [
        *git_repository.git_output(repo, "rev-list", f"{previous}..group").splitlines(),
        *git_repository.git_output(repo, "rev-list", f"{lane}..HEAD").splitlines(),
    ]
    expected = [git_repository.git_output(repo, "rev-parse", "--short", oid) for oid in added]
    assert sorted(commits) == sorted(expected)

    saved = path.read_bytes()
    attachment = (tmp_path / "record.wi-commits.jsonl").read_bytes()
    head = git_repository.git_output(repo, "rev-parse", "HEAD")
    git_repository.git_output(repo, "switch", "-c", "side", previous)
    git_repository.git_output(repo, "commit", "--allow-empty", "-m", "side")
    side = git_repository.git_output(repo, "rev-parse", "HEAD")
    git_repository.git_output(repo, "switch", "main")
    for previous_head, message in (
        (head, "範囲が空"),
        (side, "first-parentの祖先ではありません"),
        ("0000000", "前HEADを一意なcommitへ解決できません"),
    ):
        assert (
            append_progress_log.main(
                [*common, "--commit", "HEAD", "--previous-head", previous_head, "--completed-step", "統合", "--result", "x"]
            )
            == 1
        )
        error = capsys.readouterr().err
        assert message in error
        assert "次の操作: " in error
        assert path.read_bytes() == saved
        assert (tmp_path / "record.wi-commits.jsonl").read_bytes() == attachment
    assert (
        append_progress_log.main(
            [*common, "--commit", "HEAD~1", "--previous-head", lane, "--completed-step", "統合", "--result", "x"]
        )
        == 1
    )
    assert "記録済みのcommit" in capsys.readouterr().err


def test_legacy_body_comment_and_attachment_are_combined(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    git_repository.init_repository(repo, initial_branch="main")
    git_repository.git_output(repo, "commit", "--allow-empty", "-m", "base")
    git_repository.git_output(repo, "commit", "--allow-empty", "-m", "legacy")
    legacy = git_repository.git_output(repo, "rev-parse", "HEAD")
    wi_a, wi_b = "20261004-044311-001.md", "20261004-044247-001.md"
    path = tmp_path / "plan.md"
    legacy_row = '| 2026-10-01 10:00 | 実装 | 成功 <!-- wi-commits: {"commit":"' + legacy + '","awi":["' + wi_a + '"]} --> |\n'
    path.write_text(_related_plan(wi_a, wi_b) + legacy_row, encoding="utf-8")
    git_repository.git_output(repo, "commit", "--allow-empty", "-m", "current")
    common = [str(path), "--worktree", str(repo)]
    assert (
        append_progress_log.main(
            [
                *common,
                "--awi",
                wi_b,
                "--commit",
                "HEAD",
                "--previous-head",
                legacy,
                "--completed-step",
                "実装",
                "--result",
                "成功",
            ]
        )
        == 0
    )
    assert path.read_text(encoding="utf-8").count("wi-commits") == 1
    assert append_progress_log.main([*common, "--awi", wi_a, "--awi", wi_b], retrieval=True) == 0
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert lines == [
        {"awi": wi_b, "commits": [git_repository.git_output(repo, "rev-parse", "--short", "HEAD")]},
        {"awi": wi_a, "commits": [git_repository.git_output(repo, "rev-parse", "--short", legacy)]},
    ]


@pytest.mark.parametrize("handoff", [False, True])
def test_public_progress_split_preserves_assignment_and_rejects_loss(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], handoff: bool
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    git_repository.init_repository(repo)
    git_repository.git_output(repo, "commit", "--allow-empty", "-m", "base")
    base = git_repository.git_output(repo, "rev-parse", "HEAD")
    git_repository.git_output(repo, "commit", "--allow-empty", "-m", "old")
    old = git_repository.git_output(repo, "rev-parse", "HEAD")
    wi_a, wi_b = "20261004-044311-001.md", "20261004-044247-001.md"
    path = tmp_path / "record.md"
    path.write_text("# 引き継ぎ\n" if handoff else _related_plan(wi_a, wi_b), encoding="utf-8")
    common = [str(path), "--worktree", str(repo), "--awi", wi_a, "--awi", wi_b]
    if handoff:
        common += ["--handoff", "--allowed-awi", wi_a, "--allowed-awi", wi_b]

    def dispatch(*extra: str) -> int:
        return run_script.dispatch(argparse.Namespace(script_name="plan-progress", script_args=[*common, *extra]))

    assert dispatch("--commit", old, "--previous-head", base, "--completed-step", "実装", "--result", "成功") == 0
    git_repository.git_output(repo, "switch", "-c", "split", base)
    destinations = []
    for message in ("split-a", "split-b"):
        git_repository.git_output(repo, "commit", "--allow-empty", "-m", message)
        destinations.append(git_repository.git_output(repo, "rev-parse", "HEAD"))
    attachment = path.with_name(path.stem + ".wi-commits.jsonl")
    saved = path.read_bytes(), attachment.read_bytes()
    replacement = tmp_path / "rewrite.json"
    assigned = [{"commit": oid, "awi": [wi]} for oid, wi in zip(destinations, (wi_a, wi_b), strict=True)]
    replacement.write_text(json.dumps({old: assigned[:1]}), encoding="utf-8")
    capsys.readouterr()
    assert dispatch("--rewrite-map", str(replacement), "--completed-step", "分割", "--result", "失敗") == 1
    diagnostic = capsys.readouterr().err
    assert wi_b in diagnostic and old[:7] in diagnostic and "次の操作:" in diagnostic
    assert (path.read_bytes(), attachment.read_bytes()) == saved
    replacement.write_text(json.dumps({old: assigned}), encoding="utf-8")
    assert dispatch("--rewrite-map", str(replacement), "--completed-step", "分割", "--result", "成功") == 0
    assert path.read_bytes().startswith(saved[0])
    capsys.readouterr()
    assert run_script.dispatch(argparse.Namespace(script_name="plan-commits", script_args=common)) == 0
    records = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert {row["awi"]: row["commits"] for row in records} == {
        wi: [git_repository.git_output(repo, "rev-parse", "--short", oid)]
        for oid, wi in zip(destinations, (wi_a, wi_b), strict=True)
    }


@pytest.mark.parametrize("handoff", [False, True])
def test_public_progress_same_oid_split_adds_awi_or_rejects_outside(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], handoff: bool
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    git_repository.init_repository(repo)
    git_repository.git_output(repo, "commit", "--allow-empty", "-m", "base")
    base = git_repository.git_output(repo, "rev-parse", "HEAD")
    git_repository.git_output(repo, "commit", "--allow-empty", "-m", "implemented")
    implemented = git_repository.git_output(repo, "rev-parse", "HEAD")
    wi_a, wi_b, outside = "20261004-044311-001.md", "20261004-044247-001.md", "20261004-044200-001.md"
    path = tmp_path / "record.md"
    path.write_text("# 引き継ぎ\n" if handoff else _related_plan(wi_a, wi_b), encoding="utf-8")
    common = [str(path), "--worktree", str(repo)]
    if handoff:
        common += ["--handoff", "--allowed-awi", wi_a, "--allowed-awi", wi_b]

    def dispatch(script: str, *extra: str) -> int:
        return run_script.dispatch(argparse.Namespace(script_name=script, script_args=[*common, *extra]))

    record = ("--completed-step", "実装", "--result", "成功")
    assert dispatch("plan-progress", "--commit", implemented, "--previous-head", base, "--awi", wi_a, *record) == 0
    attachment = path.with_name(path.stem + ".wi-commits.jsonl")
    saved = path.read_bytes(), attachment.read_bytes()
    replacement = tmp_path / "add.json"
    replacement.write_text(json.dumps({implemented: [{"commit": implemented, "awi": [wi_a, outside]}]}), encoding="utf-8")
    capsys.readouterr()
    assert dispatch("plan-progress", "--rewrite-map", str(replacement), "--completed-step", "補正", "--result", "失敗") == 1
    diagnostic = capsys.readouterr().err
    assert outside in diagnostic and "次の操作:" in diagnostic
    assert (path.read_bytes(), attachment.read_bytes()) == saved
    replacement.write_text(json.dumps({implemented: [{"commit": implemented, "awi": [wi_a, wi_b]}]}), encoding="utf-8")
    assert dispatch("plan-progress", "--rewrite-map", str(replacement), "--completed-step", "補正", "--result", "成功") == 0
    capsys.readouterr()
    assert dispatch("plan-commits", "--awi", wi_a, "--awi", wi_b) == 0
    records = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    short = git_repository.git_output(repo, "rev-parse", "--short", implemented)
    assert {row["awi"]: row["commits"] for row in records} == {wi_a: [short], wi_b: [short]}


@pytest.mark.parametrize("handoff", [False, True])
def test_record_past_ranges_per_awi(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], handoff: bool) -> None:
    assert run_script.dispatch(argparse.Namespace(script_name="plan-progress", script_args=["--help"])) == 0
    help_text = capsys.readouterr().out
    assert all(option in help_text for option in ("--commit", "--previous-head", "--awi", "--worktree"))
    repo = tmp_path / "repo"
    repo.mkdir()
    git_repository.init_repository(repo, initial_branch="main")
    commits = []
    for message in ("base", "first", "second", "third"):
        git_repository.git_output(repo, "commit", "--allow-empty", "-m", message)
        commits.append(git_repository.git_output(repo, "rev-parse", "HEAD"))
    wis = ["20261017-171745-001.md", "20261017-171745-002.md", "20261017-171745-003.md"]
    path = tmp_path / "record.md"
    path.write_text("# 引き継ぎ\n" if handoff else _related_plan(*wis), encoding="utf-8")
    common = [str(path), "--worktree", str(repo)]
    if handoff:
        common += ["--handoff", *[value for wi in wis for value in ("--allowed-awi", wi)]]
    for previous, current, wi in zip(commits[:-1], commits[1:], wis, strict=True):
        assert (
            run_script.dispatch(
                argparse.Namespace(
                    script_name="plan-progress",
                    script_args=[
                        *common,
                        "--commit",
                        current,
                        "--previous-head",
                        previous,
                        "--awi",
                        wi,
                        "--completed-step",
                        "区間記録",
                        "--result",
                        "成功",
                    ],
                )
            )
            == 0
        )
    capsys.readouterr()
    expected = {
        wi: [git_repository.git_output(repo, "rev-parse", "--short", oid)] for wi, oid in zip(wis, commits[1:], strict=True)
    }
    assert run_script.dispatch(argparse.Namespace(script_name="plan-commits", script_args=common)) == 0
    rows = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert {row["awi"]: row["commits"] for row in rows} == expected

    assert run_script.dispatch(argparse.Namespace(script_name="plan-progress", script_args=[*common, "--get-commits"])) == 2
    assert git_repository.git_output(repo, "rev-parse", "HEAD") == commits[-1]
    assert len(path.with_suffix(".wi-commits.jsonl").read_text(encoding="utf-8").splitlines()) == 3


@pytest.mark.parametrize("handoff", [False, True])
@pytest.mark.parametrize("case", ["branch", "second-parent", "previous", "empty", "merge", "duplicate", "overlap"])
def test_record_range_rejections_keep_files(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], handoff: bool, case: str
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    git_repository.init_repository(repo, initial_branch="main")
    git_repository.git_output(repo, "commit", "--allow-empty", "-m", "base")
    base = git_repository.git_output(repo, "rev-parse", "HEAD")
    git_repository.git_output(repo, "switch", "-c", "side")
    git_repository.git_output(repo, "commit", "--allow-empty", "-m", "side")
    side = git_repository.git_output(repo, "rev-parse", "HEAD")
    git_repository.git_output(repo, "switch", "main")
    git_repository.git_output(repo, "commit", "--allow-empty", "-m", "first")
    first = git_repository.git_output(repo, "rev-parse", "HEAD")
    git_repository.git_output(repo, "commit", "--allow-empty", "-m", "second")
    second = git_repository.git_output(repo, "rev-parse", "HEAD")
    if case in {"second-parent", "merge"}:
        git_repository.git_output(repo, "merge", "--no-ff", "-m", "merge", "side")
    wi = "20261017-171745-001.md"
    path = tmp_path / "record.md"
    path.write_text("# 引き継ぎ\n" if handoff else _related_plan(wi), encoding="utf-8")
    common = [str(path), "--worktree", str(repo), "--awi", wi, "--completed-step", "記録", "--result", "結果"]
    if handoff:
        common += ["--handoff", "--allowed-awi", wi]

    def record(current: str, previous: str) -> int:
        return run_script.dispatch(
            argparse.Namespace(
                script_name="plan-progress", script_args=[*common, "--commit", current, "--previous-head", previous]
            )
        )

    assert record(first, base) == 0
    attachment = path.with_suffix(".wi-commits.jsonl")
    saved = path.read_bytes(), attachment.read_bytes()
    revision, previous, diagnostic = {
        "branch": (side, base, "first-parentの履歴上にありません"),
        "second-parent": (side, base, "first-parentの履歴上にありません"),
        "previous": (second, side, "first-parentの祖先ではありません"),
        "empty": (second, second, "範囲が空"),
        "merge": ("HEAD", second, "変更のない接続mergeだけ"),
        "duplicate": (first, base, "記録済みのcommit"),
        "overlap": (second, base, "記録済みのcommit"),
    }[case]
    capsys.readouterr()
    assert record(revision, previous) == 1
    error = capsys.readouterr().err
    assert diagnostic in error and "次の操作:" in error
    if case in {"duplicate", "overlap"}:
        assert git_repository.git_output(repo, "rev-parse", "--short", first) in error
        assert "未記録の区間" in error
    assert (path.read_bytes(), attachment.read_bytes()) == saved


@pytest.mark.parametrize("rewritten", [False, True])
def test_record_range_checks_legacy_and_rewritten_mapping(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], rewritten: bool
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    git_repository.init_repository(repo)
    commits = []
    for message in ("base", "first", "second"):
        git_repository.git_output(repo, "commit", "--allow-empty", "-m", message)
        commits.append(git_repository.git_output(repo, "rev-parse", "HEAD"))
    wi = "20261017-171745-001.md"
    path = tmp_path / "record.md"
    old = "d" * 40
    legacy = {"commit": old if rewritten else commits[1], "awi": [wi]}
    path.write_text(_related_plan(wi) + "\n<!-- wi-commits: " + json.dumps(legacy) + " -->\n", encoding="utf-8")
    attachment = path.with_suffix(".wi-commits.jsonl")
    events: list[dict[str, object]] = [{"commits": ["badcafe"], "awi": [wi]}]
    if rewritten:
        events.append({"rewrite": {old[:9]: commits[1]}})
    attachment.write_text("".join(json.dumps(event) + "\n" for event in events), encoding="utf-8")
    common = [str(path), "--worktree", str(repo), "--awi", wi, "--completed-step", "記録", "--result", "結果"]
    saved = path.read_bytes(), attachment.read_bytes()
    assert append_progress_log.main([*common, "--commit", commits[1], "--previous-head", commits[0]]) == 1
    assert "記録済みのcommit" in capsys.readouterr().err
    assert (path.read_bytes(), attachment.read_bytes()) == saved
    assert append_progress_log.main([*common, "--commit", commits[2], "--previous-head", commits[1]]) == 0


def test_public_cli_handoff_progress_uses_current_utc_clock(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "handoff.md"
    quoted = "CI success（事象時刻: 2026-11-23T09:28:20Z、出所: gh run view）"
    for step, result in [("検証", "成功"), ("CI", quoted)]:
        before = datetime.datetime.now(datetime.UTC).replace(microsecond=0)
        assert append_progress_log.main([str(path), "--handoff", "--completed-step", step, "--result", result]) == 0
        after = datetime.datetime.now(datetime.UTC)
        stamp, _, rest = path.read_text(encoding="utf-8").splitlines()[-1].partition(" ")
        recorded = datetime.datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=datetime.UTC)
        assert before <= recorded <= after
        assert rest == f"{step}: {result}"
    assert path.read_text(encoding="utf-8").splitlines()[0].endswith(" 検証: 成功")


@pytest.mark.parametrize("option", ["--datetime", "--time", "--recorded-at", "--now"])
def test_public_cli_rejects_time_arguments(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], option: str) -> None:
    path = tmp_path / "handoff.md"
    path.write_text("# 引き継ぎ\n", encoding="utf-8")
    argv = [str(path), "--handoff", "--completed-step", "検証", "--result", "成功", option, "2026-11-24T00:00:00Z"]
    with pytest.raises(SystemExit) as raised:
        append_progress_log.main(argv)
    assert raised.value.code == 2
    assert option in capsys.readouterr().err
    assert path.read_text(encoding="utf-8") == "# 引き継ぎ\n"


def _awi_plan(target_repo: pathlib.Path, *wis: str) -> str:
    related = "".join(f"  - {wi}\n" for wi in wis)
    return (
        f"---\nlaunch_route: process-wi lane-01\ntarget_repo: {target_repo}\n"
        f"related_wis:\n{related}work_kind: 通常変更\n---\n\n"
        "# 計画\n\n本文\n"
    )


@pytest.mark.usefixtures("leaf_git_commands")
def test_awi_plan_records_mapping_without_changing_body(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    notes = tmp_path / "private-notes"
    monkeypatch.setenv("AGENT_TOOLKIT_PRIVATE_NOTES", str(notes))
    repo = tmp_path / "repo"
    repo.mkdir()
    git_repository.init_repository(repo)
    git_repository.git_output(repo, "commit", "--allow-empty", "-m", "base")
    base = git_repository.git_output(repo, "rev-parse", "HEAD")
    git_repository.git_output(repo, "commit", "--allow-empty", "-m", "implemented")
    implemented = git_repository.git_output(repo, "rev-parse", "HEAD")
    wi, outside = "20261004-044311-001.md", "20261004-044200-001.md"
    path = tmp_path / "plan.md"
    path.write_text(_awi_plan(repo, wi), encoding="utf-8")
    body = path.read_bytes()
    attachment = path.with_name(path.stem + ".wi-commits.jsonl")

    def dispatch(script: str, target: pathlib.Path, *extra: str) -> int:
        args = [str(target), "--worktree", str(repo), *extra]
        return run_script.dispatch(argparse.Namespace(script_name=script, script_args=args))

    record = ("--commit", implemented, "--previous-head", base)
    assert dispatch("plan-progress", path, *record, "--awi", outside) == 1
    assert dispatch("plan-progress", path, "--commit", base, "--previous-head", implemented, "--awi", wi) == 1
    assert path.read_bytes() == body
    assert not attachment.exists()
    assert dispatch("plan-progress", path, *record, "--awi", wi) == 0
    assert path.read_bytes() == body
    assert attachment.is_file()
    capsys.readouterr()
    assert dispatch("plan-commits", path) == 0
    short = git_repository.git_output(repo, "rev-parse", "--short", implemented)
    assert [json.loads(line) for line in capsys.readouterr().out.splitlines()] == [{"awi": wi, "commits": [short]}]
    git_repository.git_output(repo, "switch", "-c", "rewritten", base)
    git_repository.git_output(repo, "commit", "--allow-empty", "-m", "rewritten")
    rewritten = git_repository.git_output(repo, "rev-parse", "HEAD")
    replacement = tmp_path / "rewrite.json"
    replacement.write_text(json.dumps({implemented: [{"commit": rewritten, "awi": [wi]}]}), encoding="utf-8")
    assert dispatch("plan-progress", path, "--rewrite-map", str(replacement)) == 0
    assert path.read_bytes() == body
    capsys.readouterr()
    assert dispatch("plan-commits", path) == 0
    short = git_repository.git_output(repo, "rev-parse", "--short", rewritten)
    assert [json.loads(line) for line in capsys.readouterr().out.splitlines()] == [{"awi": wi, "commits": [short]}]
    saved = notes / "plans" / "2026" / "10" / "04-example-1a2b.md"
    saved.parent.mkdir(parents=True)
    saved.write_bytes(body)
    capsys.readouterr()
    assert dispatch("plan-progress", saved, "--commit", rewritten, "--previous-head", base, "--awi", wi) == 1
    assert "`atk plans checkout" in capsys.readouterr().err
    assert saved.read_bytes() == body
    assert not saved.with_name(saved.stem + ".wi-commits.jsonl").exists()
    assert dispatch("plan-progress", path, "--completed-step", "工程", "--result", "結果") == 1
    assert path.read_bytes() == body
    legacy = tmp_path / "legacy.md"
    legacy.write_text(_related_plan(wi), encoding="utf-8")
    with pytest.raises(SystemExit) as error_exit:
        append_progress_log.main([str(legacy), "--worktree", str(repo), *record, "--awi", wi])
    assert error_exit.value.code == 2
    assert "--completed-step" in capsys.readouterr().err
    with pytest.raises(SystemExit) as error_exit:
        append_progress_log.main(
            [str(tmp_path / "handoff.md"), "--worktree", str(repo), *record, "--awi", wi, "--handoff", "--allowed-awi", wi]
        )
    assert error_exit.value.code == 2
    assert "--completed-step" in capsys.readouterr().err
