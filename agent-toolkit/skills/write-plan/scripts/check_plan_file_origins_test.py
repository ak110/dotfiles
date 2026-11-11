# pylint: disable=useless-import-alias  # pytestのfixtureを試験モジュールの名前空間へ登録するため。
"""WI由来と原文の参照を検証する。"""

import pathlib
import typing

import check_plan_file
import pytest

from agent_toolkit._plan import structure as _plan_format
from agent_toolkit._testing import git_repository
from agent_toolkit._testing import plan_fixture as _plan_fixture
from agent_toolkit._testing.plan_file_check_support import fixture_repo as fixture_repo


def _origin_plan(repo: pathlib.Path, private_notes: pathlib.Path, *, source: bool) -> pathlib.Path:
    """由来の構文確認に使う現行計画とWIファイルを配置する。"""
    main_content = _plan_fixture.current_plan(repo=repo.resolve(), related_wi=_plan_fixture.WI_FILES)
    main_path = repo / "plan.md"
    main_path.write_text(main_content, encoding="utf-8")
    frontmatter = ["---", "status: inbox"]
    if source:
        frontmatter.append(f"{_plan_format.PLAN_WI_SOURCE_KEY}: agent-toolkit:review-session")
    frontmatter.append("---")
    inbox = private_notes / "inbox"
    inbox.mkdir(parents=True)
    (inbox / _plan_fixture.WI_FILES[0][0]).write_text(
        "\n".join([*frontmatter, "", "# 要求", "", "本文。", ""]), encoding="utf-8"
    )
    return main_path


def test_source_does_not_imply_origin_mismatch_on_read(repo: tuple[pathlib.Path, str], tmp_path: pathlib.Path) -> None:
    """WIのsourceから要求単位の由来を推定せず読み取る。"""
    work_dir, _base = repo
    private_notes = tmp_path / "private-notes"
    main_path = _origin_plan(work_dir, private_notes, source=True)
    errors, warnings = check_plan_file.check(main_path, work_dir, private_notes=private_notes)
    assert not errors, errors
    assert not warnings, warnings


def test_source_does_not_imply_origin_mismatch_on_creation(repo: tuple[pathlib.Path, str], tmp_path: pathlib.Path) -> None:
    """新規作成もsourceから由来の意味を判定しない。"""
    work_dir, _base = repo
    private_notes = tmp_path / "private-notes"
    main_path = _origin_plan(work_dir, private_notes, source=True)
    errors, warnings = check_plan_file.check(
        main_path,
        work_dir,
        private_notes=private_notes,
        reject_migration_warnings=True,
    )
    assert not errors, errors
    assert not warnings, warnings


def _assert_creation_check_passes_silently(
    work_dir: pathlib.Path, path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """公開CLIで新規作成の基準を満たすか確かめた結果が終了コード0で、警告を出力しないことを確かめる。"""
    result = check_plan_file.main(["--reject-migration-warnings", "--work-dir", str(work_dir), str(path)])
    captured = capsys.readouterr()
    assert result == 0, captured.err
    assert captured.err == ""


_AGENT_WI_ADOPTED_ROW = f"| 入力の境界を追加確認する | エージェント由来のWI ({_plan_fixture.WI_FILES[0][0]}) | 採用 | - |"


def _agent_wi_plan(repo: pathlib.Path, private_notes: pathlib.Path, wi_sections: str | None) -> pathlib.Path:
    """根拠が`-`の`エージェント由来のWI`採用行を持つ計画と、指定した節を持つWIファイルを配置する。

    `wi_sections`が`None`の場合はWIファイルを置かない。
    """
    main_content = _plan_fixture.current_plan(repo=repo.resolve(), related_wi=_plan_fixture.WI_FILES)
    main_content = main_content.replace(_plan_fixture.WI_ACTION_ROW, _AGENT_WI_ADOPTED_ROW, 1)
    path = repo / "agent-wi.md"
    path.write_text(main_content, encoding="utf-8")
    inbox = private_notes / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    if wi_sections is not None:
        frontmatter = f"---\n{_plan_format.PLAN_WI_SOURCE_KEY}: process-wi\n---\n\n"
        (inbox / _plan_fixture.WI_FILES[0][0]).write_text(f"{frontmatter}# 要求\n\n本文。\n\n{wi_sections}", encoding="utf-8")
    return path


def test_agent_wi_adopted_action_without_reason_accepts_new_format(
    repo: tuple[pathlib.Path, str], tmp_path: pathlib.Path
) -> None:
    """新書式の反映内容を登録済みの調査として参照する。"""
    work_dir, _base = repo
    private_notes = tmp_path / "private-notes"
    path = _agent_wi_plan(work_dir, private_notes, "## 反映内容と反映先\n\n対象。\n")
    read_errors, read_warnings = check_plan_file.check(path, work_dir, private_notes=private_notes)
    errors, warnings = check_plan_file.check(
        path,
        work_dir,
        private_notes=private_notes,
        reject_migration_warnings=True,
    )
    assert not read_errors, read_errors
    assert not any("適用範囲を再導出した結果と根拠" in warning for warning in read_warnings), read_warnings
    assert not any("適用範囲を再導出した結果と根拠" in error for error in errors), errors
    assert not any("適用範囲を再導出した結果と根拠" in warning for warning in warnings), warnings


@pytest.mark.parametrize(
    "scope",
    [
        "## 適用範囲\n\n",
        "```markdown\n## 適用範囲\n\n誤りの機構が依存する条件。\n```\n",
        "## 反映内容と反映先\n\n",
        "```markdown\n## 反映内容と反映先\n\n対象。\n```\n",
    ],
    ids=["empty-scope", "fenced-scope", "empty-content", "fenced-content"],
)
def test_agent_wi_adopted_action_without_reason_rejected_when_wi_scope_empty(
    repo: tuple[pathlib.Path, str], tmp_path: pathlib.Path, scope: str
) -> None:
    """WI本文の`## 適用範囲`が空かコードフェンス内にしか無い場合は、参照だけで根拠を省略できるとみなさない。"""
    work_dir, _base = repo
    private_notes = tmp_path / "private-notes"
    path = _agent_wi_plan(work_dir, private_notes, f"{scope}\n## 実現性\n\n確認済み。\n")
    errors, _warnings = check_plan_file.check(
        path,
        work_dir,
        private_notes=private_notes,
        reject_migration_warnings=True,
    )
    assert any("適用範囲を再導出した結果と根拠" in error for error in errors), errors


def test_agent_wi_adopted_action_without_reason_accepted_when_wi_has_scope(
    repo: tuple[pathlib.Path, str],
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """WI本文が非空の`## 適用範囲`を持つエージェント由来のWIの採用行は、根拠`-`のまま新規作成で受理する。"""
    work_dir, _base = repo
    private_notes = tmp_path / "private-notes"
    monkeypatch.setenv("AGENT_TOOLKIT_PRIVATE_NOTES", str(private_notes))
    path = _agent_wi_plan(work_dir, private_notes, "## 適用範囲\n\n誤りの機構が依存する条件。\n\n## 実現性\n\n確認済み。\n")
    _assert_creation_check_passes_silently(work_dir, path, capsys)


def test_agent_wi_adopted_action_without_reason_skips_when_wi_unresolvable(
    repo: tuple[pathlib.Path, str], tmp_path: pathlib.Path
) -> None:
    """WIファイルを特定できない場合もWI本文との比較を省略した事実は助言に留め、新規作成を遮断しない。"""
    work_dir, _base = repo
    private_notes = tmp_path / "private-notes"
    path = _agent_wi_plan(work_dir, private_notes, None)
    errors, warnings = check_plan_file.check(
        path,
        work_dir,
        private_notes=private_notes,
        reject_migration_warnings=True,
    )
    assert not errors, errors
    assert any("WIファイルを特定できない" in warning for warning in warnings), warnings


def _bug_plan_without_bug_file(repo: pathlib.Path, private_notes: pathlib.Path, *, related_wi: bool) -> pathlib.Path:
    """`計画ファイル（バグ）`行を持たないバグ対応計画と、人間由来のWIファイルを配置する。"""
    related = _plan_fixture.WI_FILES if related_wi else ()
    path = repo / "bug-plan.md"
    path.write_text(_plan_fixture.current_plan(repo=repo.resolve(), work_type="バグ対応", related_wi=related), encoding="utf-8")
    inbox = private_notes / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    for name, _summary in _plan_fixture.WI_FILES:
        (inbox / name).write_text("# 要求\n\n本文。\n", encoding="utf-8")
    return path


def test_bug_plan_without_bug_file_reference_accepted_with_related_wi(
    repo: tuple[pathlib.Path, str],
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """関連WIの原因分析に従うバグ対応計画は、計画ファイル（バグ）行なしで新規作成の基準を満たす。"""
    work_dir, _base = repo
    private_notes = tmp_path / "private-notes"
    monkeypatch.setenv("AGENT_TOOLKIT_PRIVATE_NOTES", str(private_notes))
    path = _bug_plan_without_bug_file(work_dir, private_notes, related_wi=True)
    _assert_creation_check_passes_silently(work_dir, path, capsys)


def test_bug_plan_without_bug_file_reference_rejected_without_related_wi(
    repo: tuple[pathlib.Path, str], tmp_path: pathlib.Path
) -> None:
    """関連WIが無いバグ対応計画は計画ファイル（バグ）行を必須とする。"""
    work_dir, _base = repo
    private_notes = tmp_path / "private-notes"
    path = _bug_plan_without_bug_file(work_dir, private_notes, related_wi=False)
    errors, _warnings = check_plan_file.check(
        path,
        work_dir,
        private_notes=private_notes,
        reject_migration_warnings=True,
    )
    assert any(_plan_format.PLAN_METADATA_BUG_FIELD in error for error in errors), errors


def test_origin_skip_stays_advisory_on_creation(repo: tuple[pathlib.Path, str], tmp_path: pathlib.Path) -> None:
    """根拠が記入済みなら由来の構文確認はWI本文を参照しない。"""
    work_dir, _base = repo
    private_notes = tmp_path / "absent"
    main_content = _plan_fixture.current_plan(repo=work_dir.resolve(), related_wi=_plan_fixture.WI_FILES)
    main_path = work_dir / "plan.md"
    main_path.write_text(main_content, encoding="utf-8")
    errors, warnings = check_plan_file.check(
        main_path,
        work_dir,
        private_notes=private_notes,
        reject_migration_warnings=True,
    )
    assert not errors, errors
    assert not warnings, warnings


@pytest.mark.parametrize("missing_queue", [True, False], ids=["missing-queue", "missing-wi"])
def test_cli_origin_skip_reports_one_next_action(
    repo: tuple[pathlib.Path, str],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    *,
    missing_queue: bool,
) -> None:
    """根拠を省略した行の適用範囲を取得できない場合は回復手順を示す。"""
    work_dir, _base = repo
    queue = work_dir / "queue"
    if not missing_queue:
        queue.mkdir()
    monkeypatch.setenv("AGENT_TOOLKIT_PRIVATE_NOTES", str(queue))
    path = work_dir / "origin.md"
    content = _plan_fixture.current_plan(repo=work_dir.resolve(), related_wi=_plan_fixture.WI_FILES)
    path.write_text(content.replace(_plan_fixture.WI_ACTION_ROW, _AGENT_WI_ADOPTED_ROW, 1), encoding="utf-8")
    assert check_plan_file.main(["--work-dir", str(work_dir), str(path)]) == 0
    lines = capsys.readouterr().err.splitlines()
    assert any(line.startswith("[warn]") and "由来をWI本文と比べられなかった" in line for line in lines)
    actions = [line for line in lines if line.startswith("次の操作: ")]
    assert len(actions) == 1
    expected = "AGENT_TOOLKIT_PRIVATE_NOTES" if missing_queue else "atk wi show"
    assert expected in actions[0]
    assert "解消できない" in actions[0] and "報告" in actions[0]


@pytest.mark.parametrize("read_failure", [False, True])
def test_cli_transfer_skip_reports_recovery(
    repo: tuple[pathlib.Path, str],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    *,
    read_failure: bool,
) -> None:
    """移管先だけをWI本文と比べられない場合にも、原因の解消と解消不能時の報告を案内する。"""
    work_dir, _base = repo
    queue = work_dir / "queue"
    if read_failure:
        (queue / "inbox").mkdir(parents=True)
        source = queue / "inbox" / "20261027-162634-999.md"
        source.write_text("# 後続要求\n", encoding="utf-8")
        original = pathlib.Path.is_file

        def is_file(file: pathlib.Path, *args: typing.Any, **kwargs: typing.Any) -> bool:
            if file == source:
                raise PermissionError("検証用の読取拒否")
            return original(file, *args, **kwargs)

        monkeypatch.setattr(pathlib.Path, "is_file", is_file)
    monkeypatch.setenv("AGENT_TOOLKIT_PRIVATE_NOTES", str(queue))
    content = _plan_fixture.current_plan(repo=work_dir.resolve()).replace(
        _plan_fixture.USER_ACTION_ROW,
        "| 公開契約に必要な変更を実装する | ユーザー指示 | 移管 | 20261027-162634-999.mdへ移管する。 |",
        1,
    )
    start = content.index("### 受入シナリオ")
    end = content.index("\n## ", start)
    path = work_dir / "transfer.md"
    path.write_text(content[:start] + "### 受入シナリオ\n\nなし\n" + content[end:], encoding="utf-8")
    assert check_plan_file.main(["--work-dir", str(work_dir), str(path)]) == 0
    lines = capsys.readouterr().err.splitlines()
    assert any(line.startswith("[warn]") and "移管先をWI本文と比べられなかった" in line for line in lines)
    assert not any("由来をWI本文と比べられなかった" in line for line in lines)
    actions = [line for line in lines if line.startswith("次の操作: ")]
    assert len(actions) == 1
    expected = "エラーの原因を解消" if read_failure else "AGENT_TOOLKIT_PRIVATE_NOTES"
    assert expected in actions[0]
    assert "解消できない" in actions[0] and "報告" in actions[0]


@pytest.mark.parametrize("history", ["deleted", "never"])
def test_cli_transfer_accepts_wi_deleted_after_registration(
    repo: tuple[pathlib.Path, str],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path_factory: pytest.TempPathFactory,
    history: str,
) -> None:
    """配置から消えた移管先は公開plan-checkが履歴の登録記録で受理し、削除commitと後続操作を示す。未登録の名前は拒否する。"""
    work_dir, _base = repo
    name = "20261027-162634-999.md"
    files = {f"2026/10/{name}": "# 後続要求\n"} if history == "deleted" else {"2026/10/20261027-162634-998.md": "# 別\n"}
    notes = git_repository.init_repository(tmp_path_factory.mktemp("notes"), files=files, commit_message="登録")
    if history == "deleted":
        git_repository.run_git(notes, "rm", "-q", "--", f"2026/10/{name}")
        git_repository.run_git(notes, "commit", "-q", "--no-verify", "-m", "別環境へ移す")
    deleted = git_repository.git_output(notes, "rev-parse", "--short", "HEAD")
    monkeypatch.setenv("AGENT_TOOLKIT_PRIVATE_NOTES", str(notes))
    content = _plan_fixture.current_plan(repo=work_dir.resolve()).replace(
        _plan_fixture.USER_ACTION_ROW, f"| 公開契約に必要な変更を実装する | ユーザー指示 | 移管 | {name}へ移管する。 |", 1
    )
    start = content.index("### 受入シナリオ")
    end = content.index("\n## ", start)
    path = work_dir / "transfer.md"
    path.write_text(content[:start] + "### 受入シナリオ\n\nなし\n" + content[end:], encoding="utf-8")
    code = check_plan_file.main(["--work-dir", str(work_dir), str(path)])
    lines = capsys.readouterr().err.splitlines()
    if history == "deleted":
        assert code == 0, lines
        assert any(
            line.startswith("[warn]") and f"履歴で登録済み: {name}（削除commit {deleted}、削除前のパス 2026/10/{name}）" in line
            for line in lines
        ), lines
        actions = [line for line in lines if line.startswith("次の操作: ")]
        assert len(actions) == 1 and "show --stat <削除commit>" in actions[0] and "計画の確認を続ける" in actions[0]
    else:
        assert code == 1, lines
        assert any(f"移管先WIが実在しない: {name}" in line for line in lines), lines


def test_cli_origin_read_failure_reports_recovery(
    repo: tuple[pathlib.Path, str], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """WI本文の取得に失敗しても省略を助言に保ち、取得エラーの解消を案内する。"""
    work_dir, _base = repo
    queue = work_dir / "queue"
    path = _agent_wi_plan(work_dir, queue, "## 反映内容と反映先\n\n対象。\n")
    source = queue / "inbox" / _plan_fixture.WI_FILES[0][0]
    monkeypatch.setenv("AGENT_TOOLKIT_PRIVATE_NOTES", str(queue))
    original = pathlib.Path.read_text

    def read_text(file: pathlib.Path, *args: typing.Any, **kwargs: typing.Any) -> str:
        if file == source:
            raise PermissionError("検証用の読取拒否")
        return original(file, *args, **kwargs)

    monkeypatch.setattr(pathlib.Path, "read_text", read_text)
    assert check_plan_file.main(["--work-dir", str(work_dir), str(path)]) == 0
    lines = capsys.readouterr().err.splitlines()
    assert any("WI本文を取得できない" in line and source.name in line for line in lines)
    actions = [line for line in lines if line.startswith("次の操作: ")]
    assert len(actions) == 1
    assert "エラーの原因を解消" in actions[0]


__all__ = ["fixture_repo"]
