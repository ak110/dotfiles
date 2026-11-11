# pylint: disable=useless-import-alias  # pytestのfixtureを試験モジュールの名前空間へ登録するため。
"""WIのadd: questionsのテスト。"""

# pylint: disable=protected-access,subprocess-run-check

import pathlib

import pytest

from agent_toolkit import atk
from agent_toolkit._atk.wi import add as add_module
from agent_toolkit._atk.wi import frontmatter
from agent_toolkit._atk.wi import uwi as uwi_module
from agent_toolkit._atk.wi.constants import WI_TYPE_UWI
from agent_toolkit._atk.wi.web_input import WebInputError
from agent_toolkit._testing import git_repository
from agent_toolkit._testing.wi_add_support import (
    _add_body_input_args,
    _choice_argv,
    _patch_add_git,
    _patch_cmd_add_operations,
    _prepare_notes,
)
from agent_toolkit._testing.wi_add_support import _isolate_managed_temp as _isolate_managed_temp
from agent_toolkit._testing.wi_bodies import AGENT_AWI_SECTIONS as _AGENT_AWI_SECTIONS
from agent_toolkit.atk_test import _FIXED_DT, _setup_notes


@pytest.mark.parametrize("outcome", ["adopted", "rejected"])
def test_run_report_sync_ignores_later_answer_and_state(tmp_path: pathlib.Path, outcome: str) -> None:
    """報告のpush後に回答・採否を未pushで追加しても、報告本体の未同期だけを検出する。"""
    notes = git_repository.init_repository(tmp_path / "notes", files={"README": "base\n"}, commit_message="base")
    origin = git_repository.init_bare_repository(tmp_path / "origin.git")
    git_repository.run_git(notes, "remote", "add", "origin", str(origin))
    git_repository.run_git(notes, "push", "-u", "origin", "HEAD")
    report = notes / "2026/11/20261103-000000-001.md"
    report.parent.mkdir(parents=True)
    content = (
        "---\nstate: inbox\ntype: uwi\nsource: run-skill\nscope: my-skill\n"
        "run_skill_report: true\nrun_skill_run_id: run-1\n---\n\n# 報告\n分析した。\n\n"
        f"{uwi_module.ANSWER_HEADING}\n\n{uwi_module.ANSWER_MARKER}\n"
    )
    report.write_text(content, encoding="utf-8")
    assert not add_module.has_synced_run_report(notes, run_id="run-1", skill="my-skill")
    git_repository.commit_all(notes, "report")
    assert not add_module.has_synced_run_report(notes, run_id="run-1", skill="my-skill")
    git_repository.run_git(notes, "push", "origin", "HEAD")
    updated = content.replace("state: inbox", f"state: {outcome}") + "了解した。\n\n## 処理結果\n採否を記録した。\n"
    report.write_text(updated, encoding="utf-8")
    git_repository.commit_all(notes, "answer")
    assert add_module.has_synced_run_report(notes, run_id="run-1", skill="my-skill")
    report.write_text(updated.replace("分析した。", "追加の分析をした。"), encoding="utf-8")
    assert not add_module.has_synced_run_report(notes, run_id="run-1", skill="my-skill")
    git_repository.commit_all(notes, "edit report")
    assert not add_module.has_synced_run_report(notes, run_id="run-1", skill="my-skill")
    git_repository.run_git(notes, "push", "origin", "HEAD")
    assert add_module.has_synced_run_report(notes, run_id="run-1", skill="my-skill")


@pytest.mark.parametrize("dry_run", [[], ["--dry-run"]])
@pytest.mark.parametrize("question_type", [[], ["--question-type=free-form"]])
def test_uwi_requires_question_type_and_rejects_free_form(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], question_type: list[str], dry_run: list[str]
) -> None:
    """UWIの新規作成は回答形式の省略とfree-formを保存前に拒否し、選択肢形式で投入する操作を示す。

    回答形式の省略を受理すると、選択肢を1組しか持てない形式を避けて複数の問いを1件へ束ねる投入が受理される。
    """
    notes = _setup_notes(tmp_path)
    body_path = tmp_path / "body.md"
    body_path.write_text("どちらの案を採用しますか？", encoding="utf-8")

    with pytest.raises(SystemExit) as exc_info:
        atk.main(
            [
                "wi",
                "add",
                "--target-repo",
                "github.com/example/repo",
                "--type=uwi",
                *question_type,
                *dry_run,
                "--body-file",
                str(body_path),
            ],
            home=tmp_path,
            now=_FIXED_DT,
        )

    assert exc_info.value.code == 2
    err = capsys.readouterr().err
    assert err.startswith("失敗: ")
    assert "--question-type=choice --choices" in err
    assert not list(notes.glob("[0-9][0-9][0-9][0-9]/[0-9][0-9]/*.md"))


@pytest.mark.parametrize("choice_args", [["A,B,C"], ["A", "B", "C"], ["A", "B,C"]])
def test_public_add_choice_variants(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, choice_args: list[str]) -> None:
    """公開CLIの反復・混在指定は前の値を失わず、同じ順で保存する。"""
    notes = _setup_notes(tmp_path)
    _patch_cmd_add_operations(monkeypatch)
    argv = _choice_argv(tmp_path, choice_args)
    with pytest.raises(SystemExit) as finished:
        atk.main(argv, home=tmp_path, now=_FIXED_DT)
    assert finished.value.code == 0
    saved = next(notes.glob("[0-9][0-9][0-9][0-9]/[0-9][0-9]/*.md")).read_text(encoding="utf-8")
    parsed = frontmatter.parse_frontmatter(saved)
    assert parsed is not None
    assert parsed[0]["choices"] == "A,B,C"


@pytest.mark.parametrize("choices", ["A", " , A, ", " , "])
@pytest.mark.parametrize("dry_run", [False, True])
def test_public_add_choice_requires_two_options(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    choices: str,
    dry_run: bool,
) -> None:
    """通常投入とdry-runは非空2件未満を拒否し、キューを変更しない。"""
    notes = _setup_notes(tmp_path)
    _patch_cmd_add_operations(monkeypatch)
    argv = _choice_argv(tmp_path, [choices])
    if dry_run:
        argv.append("--dry-run")
    with pytest.raises(SystemExit) as failed:
        atk.main(argv, home=tmp_path, now=_FIXED_DT)
    assert failed.value.code != 0
    assert "2件以上" in capsys.readouterr().err
    assert not list(notes.glob("[0-9][0-9][0-9][0-9]/[0-9][0-9]/*.md"))


def test_uwi_choice_question_type_is_saved_with_choices(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """選択肢形式のUWIは回答形式と選択肢をfrontmatterへ保存する。"""
    notes = _prepare_notes(tmp_path, monkeypatch)

    generated = add_module.add_entries(
        notes,
        messages=["どちらの案を採用しますか？"],
        target_repo="github.com/example/repo",
        source=None,
        now=_FIXED_DT,
        entry_type=WI_TYPE_UWI,
        question_type="choice",
        choices="案A,案B",
    )

    content = (notes / "2024" / "01" / generated[0]).read_text(encoding="utf-8")
    assert "question_type: choice" in content
    assert "案A" in content
    assert "案B" in content


def test_add_entries_rejects_answer_marker_in_uwi_body(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """UWI本文が回答欄マーカーを含む場合に投入を拒否する。"""
    notes = _prepare_notes(tmp_path, monkeypatch)
    with pytest.raises(WebInputError):
        add_module.add_entries(
            notes,
            messages=[f"この方針を採用しますか？\n\n{uwi_module.ANSWER_MARKER}\n"],
            target_repo="github.com/example/repo",
            source=None,
            now=_FIXED_DT,
            entry_type=WI_TYPE_UWI,
            question_type="yes-no",
        )


def test_add_entries_rejects_answer_heading_in_uwi_body(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """UWI本文が回答見出しを行頭に含む場合に投入を拒否する。"""
    notes = _prepare_notes(tmp_path, monkeypatch)
    with pytest.raises(WebInputError):
        add_module.add_entries(
            notes,
            messages=[f"この方針を採用しますか？\n\n{uwi_module.ANSWER_HEADING}\n\n未記入\n"],
            target_repo="github.com/example/repo",
            source=None,
            now=_FIXED_DT,
            entry_type=WI_TYPE_UWI,
            question_type="yes-no",
        )


@pytest.mark.parametrize("input_kind", ["editor", "body-file"])
def test_add_rejects_reserved_user_comment_heading_in_agent_environment(
    input_kind: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """エージェント環境では通常入力の全ての方法で予約見出しを拒否する。"""
    notes = _setup_notes(tmp_path)
    repo = tmp_path / "myrepo"
    repo.mkdir()
    _patch_add_git(monkeypatch, repo)
    monkeypatch.setenv("AI_AGENT", "1")
    body = "本文\n\n## ユーザーコメント\n\nユーザーの記入"
    argv = _add_body_input_args(monkeypatch, tmp_path, body, input_kind)

    with pytest.raises(SystemExit) as exc_info:
        atk.main(argv, home=tmp_path, now=_FIXED_DT)

    assert exc_info.value.code == 1
    assert not list(notes.glob("[0-9][0-9][0-9][0-9]/[0-9][0-9]/*.md"))
    assert "ユーザーコメント節を含む本文を投入できません" in capsys.readouterr().err


@pytest.mark.parametrize("input_kind", ["editor", "body-file"])
def test_add_accepts_reserved_user_comment_heading_outside_agent_environment(
    input_kind: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """エージェント環境でなければ通常入力の全ての方法で予約見出しを受理する。"""
    notes = _setup_notes(tmp_path)
    repo = tmp_path / "myrepo"
    repo.mkdir()
    _patch_add_git(monkeypatch, repo)
    for name in ("AI_AGENT", "CODEX_CI", "CLAUDECODE", "CURSOR_AGENT"):
        monkeypatch.delenv(name, raising=False)
    body = "本文\n\n## ユーザーコメント\n\nユーザーの記入"
    argv = _add_body_input_args(monkeypatch, tmp_path, body, input_kind)

    with pytest.raises(SystemExit) as exc_info:
        atk.main(argv, home=tmp_path, now=_FIXED_DT)

    assert exc_info.value.code == 0
    assert "## ユーザーコメント" in next(notes.glob("[0-9][0-9][0-9][0-9]/[0-9][0-9]/*.md")).read_text(encoding="utf-8")


def test_add_accepts_fenced_user_comment_heading_in_agent_environment(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """エージェント環境でもコードフェンス内だけの予約見出しは受理する。"""
    notes = _setup_notes(tmp_path)
    repo = tmp_path / "myrepo"
    repo.mkdir()
    _patch_add_git(monkeypatch, repo)
    monkeypatch.setenv("AI_AGENT", "1")
    body_path = tmp_path / "body.md"
    body_path.write_text(
        f"本文\n\n```markdown\n## ユーザーコメント\n```\n\n{_AGENT_AWI_SECTIONS}",
        encoding="utf-8",
    )

    with pytest.raises(SystemExit) as exc_info:
        atk.main(
            ["wi", "add", "--source", "test", "--body-file", str(body_path)],
            home=tmp_path,
            now=_FIXED_DT,
        )

    assert exc_info.value.code == 0
    assert list(notes.glob("[0-9][0-9][0-9][0-9]/[0-9][0-9]/*.md"))
