"""計画の進捗ログ追記処理を検証する。"""

import datetime
import json
import pathlib
import subprocess

import append_progress_log
import pytest


def _plan(heading: str = "進捗ログ", *, newline: str = "\n", final_newline: bool = True) -> str:
    """進捗ログの固定表を持つ最小本文を返す。"""
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
    """現行・旧見出しの固定3列表へ注入時計の1行だけを追加する。"""
    path = tmp_path / "plan.md"
    path.write_text(_plan(heading), encoding="utf-8")
    now = datetime.datetime(2026, 9, 20, 12, 34, tzinfo=datetime.timezone(datetime.timedelta(hours=9)))

    append_progress_log.append_progress_log(path, "工程", "成功", clock=lambda: now)

    assert path.read_text(encoding="utf-8").endswith("| 2026-09-20 12:34 | 工程 | 成功 |\n")


def test_preserves_crlf_and_missing_final_newline(tmp_path: pathlib.Path) -> None:
    """既存のCRLFを維持し、末尾改行が無い表にも1行を追加する。"""
    path = tmp_path / "plan.md"
    path.write_bytes(_plan(newline="\r\n", final_newline=False).encode())
    now = datetime.datetime(2026, 9, 20, 3, 34, tzinfo=datetime.UTC)

    append_progress_log.append_progress_log(path, "工程", "成功", clock=lambda: now)

    updated = path.read_bytes()
    assert b"\r\n" in updated
    assert not updated.endswith(b"\n")
    assert updated.endswith("| 2026-09-20 03:34 | 工程 | 成功 |".encode())


def test_escapes_table_cells(tmp_path: pathlib.Path) -> None:
    """改行、バックスラッシュおよびパイプを1つのGFM表セルへ収める。"""
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

    assert path.read_text(encoding="utf-8").endswith("| 2026-09-20 03:34 | 工程 | 成功 |\n")


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
    """見出しまたは固定表が不正なら元のバイト列を変更しない。"""
    path = tmp_path / "plan.md"
    original = content.encode()
    path.write_bytes(original)

    with pytest.raises(append_progress_log.ProgressLogError) as raised:
        append_progress_log.append_progress_log(path, "工程", "結果")

    assert path.read_bytes() == original
    # 構造の不正は、置くべき見出しと固定表か、構造を確かめるコマンドを次の操作として示す。
    next_action = raised.value.next_action
    assert "`atk run-script plan-check --" in next_action or "の列）だけの固定表を置いてから再実行する" in next_action


@pytest.mark.parametrize("handoff", [False, True])
def test_public_cli_records_and_reads_commit_mapping(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], handoff: bool
) -> None:
    """同じ公開CLIで計画と引継ぎの生成・取得を確認し、既存本文を保持する。"""
    repo = tmp_path / "repo"
    repo.mkdir()
    for args in [
        ["init"],
        ["-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "--allow-empty", "-m", "example"],
    ]:
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, timeout=30)
    previous_head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True, timeout=30
    ).stdout.strip()
    subprocess.run(
        ["git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "--allow-empty", "-m", "recorded"],
        cwd=repo,
        check=True,
        capture_output=True,
        timeout=30,
    )
    oid = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True, timeout=30
    ).stdout.strip()
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
    assert path.read_text(encoding="utf-8").startswith(original)
    assert append_progress_log.main([*common, "--get-commits"]) == 0
    assert json.loads(capsys.readouterr().out) == {"awi": wi, "commits": [oid]}
    saved = path.read_bytes()
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

    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "--amend",
            "--allow-empty",
            "-m",
            "new",
        ],
        cwd=repo,
        check=True,
        capture_output=True,
        timeout=30,
    )
    new_oid = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True, timeout=30
    ).stdout.strip()
    # JSON文字列そのもの、存在しないパス、JSONとして読めないファイルは、計画ファイルの失敗ではなく
    # `--rewrite-map`の値の誤りとして返し、記録を変えない。
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
    replacements.write_text(json.dumps({oid: new_oid}), encoding="utf-8")
    assert (
        append_progress_log.main(
            [*common, "--rewrite-map", str(replacements), "--completed-step", "履歴検収", "--result", "成功"]
        )
        == 0
    )
    assert append_progress_log.main([*common, "--get-commits"]) == 0
    assert json.loads(capsys.readouterr().out) == {"awi": wi, "commits": [new_oid]}


def test_cli_structure_error_reports_next_action(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    """CLIは理由の行に続けて次の操作の行を書く。"""
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
    """撤去した`--start-head`を渡すと引数エラーで終わり進捗ログを保つ。"""
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
    """旧作業パスから一意な保存先を読み、曖昧な保存先は選ばない。"""
    monkeypatch.setenv("HOME", str(tmp_path))
    notes = tmp_path / "private-notes"
    monkeypatch.setenv("AGENT_TOOLKIT_PRIVATE_NOTES", str(notes))
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, timeout=30)
    subprocess.run(
        ["git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "--allow-empty", "-m", "example"],
        cwd=repo,
        check=True,
        capture_output=True,
        timeout=30,
    )
    previous_head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True, timeout=30
    ).stdout.strip()
    subprocess.run(
        ["git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "--allow-empty", "-m", "recorded"],
        cwd=repo,
        check=True,
        capture_output=True,
        timeout=30,
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
    assert append_progress_log.main([str(working), "--worktree", str(repo), "--awi", wi, "--get-commits"]) == 0
    assert json.loads(capsys.readouterr().out)["awi"] == wi
    duplicate = notes / "plans" / "2025" / "10" / working.name
    duplicate.parent.mkdir(parents=True)
    duplicate.write_bytes(saved.read_bytes())
    assert append_progress_log.main([str(working), "--worktree", str(repo), "--awi", wi, "--get-commits"]) == 1
    assert "一意に特定できません" in capsys.readouterr().err
    assert append_progress_log.main([str(saved), "--worktree", str(repo), "--awi", wi, "--get-commits"]) == 0
    saved.unlink()
    duplicate.unlink()
    assert append_progress_log.main([str(working), "--worktree", str(repo), "--awi", wi, "--get-commits"]) == 1
    assert "同名=0件" in capsys.readouterr().err
