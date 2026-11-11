# pylint: disable=useless-import-alias  # pytestのfixtureを試験モジュールの名前空間へ登録するため。
"""WIのuwi: answerのテスト。"""

import contextlib
import pathlib
import subprocess
from typing import Any

import pytest

from agent_toolkit import atk
from agent_toolkit._atk.wi import sync as _wi_sync
from agent_toolkit._atk.wi import uwi as uwi_module
from agent_toolkit._testing.wi_entry_files import entry_path as _fixed_path
from agent_toolkit._testing.wi_uwi_support import _AGENT_ENVIRONMENT_VARIABLES
from agent_toolkit._testing.wi_uwi_support import _clear_agent_environment as _clear_agent_environment
from agent_toolkit.atk_test import (
    _FIXED_TIMESTAMP,
    _GitCall,
    _make_subprocess_fake,
    _setup_notes,
    _write_awi_file,
    _write_uwi_file,
)


class TestUwiAnswer:
    """answerサブコマンドの空集合・差分なし時の挙動検証。"""

    def test_no_unanswered_prints_message(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """未回答ゼロ時は案内のみでcommitしない。"""
        notes = _setup_notes(tmp_path)
        _write_uwi_file(notes, f"{_FIXED_TIMESTAMP}-001.md", question="q", answer="ans\n")
        monkeypatch.setenv("EDITOR", "vi")
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(git_calls))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "answer"], home=tmp_path)
        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        # 対象0件も接頭辞付きの成功行で返し、受信側が接頭辞だけで成否を判定できるようにする。
        assert captured.out.startswith("成功: 未回答のUWIが無いため")
        commit_calls = [c for c in git_calls if c["cmd"][:2] == ["git", "commit"]]
        assert commit_calls == []


class TestUwiAnswerEditorFailure:
    """answerサブコマンド: エディター非ゼロ終了時にexit 0を返さないことを検証する。"""

    def test_nonzero_editor_exit_is_treated_as_failure(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """エディターが非ゼロ終了コードで終了した場合、中断してexit 1を返しcommitしない。"""
        notes = _setup_notes(tmp_path)
        _write_uwi_file(notes, f"{_FIXED_TIMESTAMP}-001.md", question="q?", answer="")
        monkeypatch.setenv("EDITOR", "fake-editor")
        git_calls: list[_GitCall] = []

        def fake_run(cmd: list[str], *_args: object, **kwargs: object) -> subprocess.CompletedProcess[Any]:
            if cmd[:1] == ["fake-editor"]:
                return subprocess.CompletedProcess(cmd, returncode=1)
            git_calls.append({"cmd": list(cmd), "kwargs": dict(kwargs)})
            empty: Any = "" if kwargs.get("text") else b""
            return subprocess.CompletedProcess(cmd, returncode=0, stdout=empty, stderr=empty)

        monkeypatch.setattr(subprocess, "run", fake_run)

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "answer"], home=tmp_path)

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "エディターが終了コード1で終了した" in captured.err
        commit_calls = [c for c in git_calls if c["cmd"][:2] == ["git", "commit"]]
        assert commit_calls == []


class TestUwiAnswerNonInteractive:
    """answerサブコマンド: ファイル名と回答本文を引数で渡し、非対話で回答できることを検証する。

    自律モードで動くエージェントが`$EDITOR`を介さずに回答を記録できることを担保する。
    """

    def test_arguments_record_answer_without_editor(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """ファイル名と回答本文の指定で回答欄が更新され、`$EDITOR`未設定でも成功する。"""
        notes = _setup_notes(tmp_path)
        filename = f"{_FIXED_TIMESTAMP}-001.md"
        path = _write_uwi_file(notes, filename, question="q?", answer=f"{uwi_module.ANSWER_MARKER}\n")
        monkeypatch.delenv("EDITOR", raising=False)
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(git_calls))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "answer", filename, "採用する"], home=tmp_path)

        assert exc_info.value.code == 0
        content = path.read_text(encoding="utf-8")
        assert content.rstrip().endswith("採用する")
        captured = capsys.readouterr()
        assert f"成功: UWIへ回答を反映した: {filename}" in captured.out
        assert "$EDITOR" not in captured.err
        assert [c["cmd"] for c in git_calls if c["cmd"][:2] == ["git", "commit"]]

    def test_answer_marker_in_body_is_rejected(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """回答本文が回答欄マーカーを含む場合はexit 1となり、対象ファイルを変更しない。"""
        notes = _setup_notes(tmp_path)
        filename = f"{_FIXED_TIMESTAMP}-001.md"
        path = _write_uwi_file(notes, filename, question="q?", answer=f"{uwi_module.ANSWER_MARKER}\n")
        before = path.read_text(encoding="utf-8")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "answer", filename, f"{uwi_module.ANSWER_MARKER}\n採用する"], home=tmp_path)

        assert exc_info.value.code == 1
        assert path.read_text(encoding="utf-8") == before
        assert "回答欄マーカーを含められない" in capsys.readouterr().err

    def test_filename_without_answer_body_exits_1(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """ファイル名のみの指定は対話モードへ移行せずexit 1で案内する。"""
        notes = _setup_notes(tmp_path)
        filename = f"{_FIXED_TIMESTAMP}-001.md"
        _write_uwi_file(notes, filename, question="q?", answer=f"{uwi_module.ANSWER_MARKER}\n")
        monkeypatch.setenv("EDITOR", "fake-editor")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "answer", filename], home=tmp_path)

        assert exc_info.value.code == 1
        assert "ファイル名と回答本文の両方を指定する" in capsys.readouterr().err

    def test_missing_entry_exits_1(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """対象が存在しない場合はTracebackを露出せずexit 1で案内する。"""
        _setup_notes(tmp_path)
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "answer", f"{_FIXED_TIMESTAMP}-999.md", "採用する"], home=tmp_path)

        assert exc_info.value.code == 1
        assert "processing・inbox・holdのいずれにも存在しない" in capsys.readouterr().err

    def test_non_uwi_entry_exits_1(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """対象がUWIでない場合はexit 1となり、対象ファイルを変更しない。"""
        notes = _setup_notes(tmp_path)
        filename = f"{_FIXED_TIMESTAMP}-001.md"
        path = _write_awi_file(notes, filename)
        before = path.read_text(encoding="utf-8")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "answer", filename, "採用する"], home=tmp_path)

        assert exc_info.value.code == 1
        assert path.read_text(encoding="utf-8") == before
        assert "回答はUWIのエントリにのみ適用できます" in capsys.readouterr().err


@pytest.mark.parametrize("environment_name", _AGENT_ENVIRONMENT_VARIABLES)
def test_agent_environment_rejects_uwi_answer_before_writing(
    environment_name: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """各エージェント環境ではCLIで受け取ったUWI回答を拒否する。"""
    notes = _setup_notes(tmp_path)
    filename = f"{_FIXED_TIMESTAMP}-001.md"
    path = _write_uwi_file(notes, filename, question="q?", answer=f"{uwi_module.ANSWER_MARKER}\n")
    before = path.read_bytes()
    for name in _AGENT_ENVIRONMENT_VARIABLES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv(environment_name, "1")

    with pytest.raises(SystemExit) as exc_info:
        atk.main(["wi", "answer", filename, "採用する"], home=tmp_path)

    assert exc_info.value.code == 1
    assert path.read_bytes() == before
    failure, next_action = capsys.readouterr().err.splitlines()
    assert failure == "失敗: UWIの回答はユーザーだけが書き込む。エージェント環境から起動したatkでは回答できない"
    # エージェントは回答できないため、ユーザーへの依頼を次の操作として返す。
    assert next_action == "次の操作: ユーザーへ回答を依頼する"


def test_answer_uwi_common_core_accepts_agent_environment(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ブラウザーから呼ぶ共有の実装はエージェント環境でも回答を保存する。"""
    notes = _setup_notes(tmp_path)
    path = _write_uwi_file(notes, "uwi.md", question="q?", answer=f"{uwi_module.ANSWER_MARKER}\n")
    monkeypatch.setenv("AI_AGENT", "1")
    monkeypatch.setattr(_wi_sync, "repo_lock", lambda *_args, **_kwargs: contextlib.nullcontext())
    monkeypatch.setattr(_wi_sync, "pull", lambda _path: None)
    monkeypatch.setattr(_wi_sync, "commit_and_push", lambda *_args, **_kwargs: None)

    assert uwi_module.answer_uwi(notes, filename=path.name, answer="採用する")
    assert path.read_text(encoding="utf-8").endswith("採用する\n")


def test_answer_uwi_splits_at_last_marker(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """回答欄マーカーが重複するエントリでも最後のマーカー基準で分割し、見出しと質問本文を保全する。"""
    notes = _setup_notes(tmp_path)
    monkeypatch.setattr(_wi_sync, "repo_lock", lambda *_args, **_kwargs: contextlib.nullcontext())
    monkeypatch.setattr(_wi_sync, "pull", lambda _path: None)
    monkeypatch.setattr(_wi_sync, "commit_and_push", lambda *_args, **_kwargs: None)
    path = _fixed_path(notes, "20260101-000000-001.md")
    path.write_text(
        "---\nstate: inbox\ntarget_repo: github.com/example/foo\ntype: uwi\nquestion_type: free-form\n---\n\n"
        f"{uwi_module.QUESTION_HEADING}\n\n"
        f"前半の質問本文。\n\n{uwi_module.ANSWER_MARKER}\n\n"
        f"後半の質問本文。\n\n{uwi_module.ANSWER_HEADING}\n\n{uwi_module.ANSWER_MARKER}\n",
        encoding="utf-8",
    )
    assert uwi_module.answer_uwi(notes, filename=path.name, answer="採用する") is True
    content = path.read_text(encoding="utf-8")
    assert uwi_module.ANSWER_HEADING in content
    assert "前半の質問本文。" in content
    assert "後半の質問本文。" in content
    assert content.rstrip().endswith("採用する")


def test_answer_uwi_keeps_behavior_for_single_marker(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """マーカーが1個の通常データでは従来と同じ結果になる。"""
    notes = _setup_notes(tmp_path)
    monkeypatch.setattr(_wi_sync, "repo_lock", lambda *_args, **_kwargs: contextlib.nullcontext())
    monkeypatch.setattr(_wi_sync, "pull", lambda _path: None)
    monkeypatch.setattr(_wi_sync, "commit_and_push", lambda *_args, **_kwargs: None)
    path = _fixed_path(notes, "20260101-000000-002.md")
    path.write_text(
        "---\nstate: inbox\ntarget_repo: github.com/example/foo\ntype: uwi\nquestion_type: free-form\n---\n\n"
        f"{uwi_module.QUESTION_HEADING}\n\n質問本文。\n\n{uwi_module.ANSWER_HEADING}\n\n{uwi_module.ANSWER_MARKER}\n",
        encoding="utf-8",
    )
    assert uwi_module.answer_uwi(notes, filename=path.name, answer="不採用とする") is True
    content = path.read_text(encoding="utf-8")
    assert content.count(uwi_module.ANSWER_MARKER) == 1
    assert "質問本文。" in content
    assert content.rstrip().endswith("不採用とする")


@pytest.mark.parametrize(
    "question_metadata", ["question_type: post-approval", "question_type: choice\nchoices: [その対応で問題無い, 問題がある]"]
)
def test_answer_uwi_auto_adopts_affirmative_post_approval(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    question_metadata: str,
) -> None:
    """標準の事後承認UWIへ肯定回答した場合は回答保存と採用を同じ操作で完了する。"""
    notes = _setup_notes(tmp_path)
    monkeypatch.setattr(_wi_sync, "repo_lock", lambda *_args, **_kwargs: contextlib.nullcontext())
    monkeypatch.setattr(_wi_sync, "pull", lambda _path: None)
    commits: list[tuple[str, list[str]]] = []
    monkeypatch.setattr(
        _wi_sync,
        "commit_and_push",
        lambda _notes, message, paths, **_kwargs: commits.append((message, list(paths))),
    )
    path = _fixed_path(notes, "post-approval.md")
    path.write_text(
        f"---\nstate: inbox\ntarget_repo: github.com/example/foo\ntype: uwi\n{question_metadata}\n---\n\n"
        f"{uwi_module.QUESTION_HEADING}\n\n実施済みの対応を承認しますか。\n\n"
        f"{uwi_module.ANSWER_HEADING}\n\n{uwi_module.ANSWER_MARKER}\n",
        encoding="utf-8",
    )

    assert uwi_module.answer_uwi(notes, filename=path.name, answer=" その対応で問題無い\n") is True

    assert path.exists()
    content = path.read_text(encoding="utf-8")
    assert "state: adopted\n" in content
    assert "その対応で問題無い" in content
    assert "- 採否: adopted" in content
    assert commits == [("chore: answer and adopt uwi item", ["2026/10/post-approval.md"])]


def test_answer_without_diff_reports_success_line(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """保存済みと同じ回答は、変更の無い成功行で報告する。"""
    notes = _setup_notes(tmp_path)
    filename = f"{_FIXED_TIMESTAMP}-001.md"
    _write_uwi_file(notes, filename, question="q?", answer=f"{uwi_module.ANSWER_MARKER}\n採用する\n")
    monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

    with pytest.raises(SystemExit) as exc_info:
        atk.main(["wi", "answer", filename, "採用する"], home=tmp_path)

    assert exc_info.value.code == 0
    assert f"成功: 差分なし（変更は無い）: {filename}" in capsys.readouterr().out


def test_answer_uwi_auto_adopt_preserves_unrelated_terminal_entry(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """事後承認の採用は同じ保存パスで完結し、別の終了項目を保持する。"""
    notes = _setup_notes(tmp_path)
    monkeypatch.setattr(_wi_sync, "repo_lock", lambda *_args, **_kwargs: contextlib.nullcontext())
    monkeypatch.setattr(_wi_sync, "pull", lambda _path: None)
    monkeypatch.setattr(_wi_sync, "commit_and_push", lambda *_args, **_kwargs: None)
    path = _fixed_path(notes, "post-approval.md")
    path.write_text(
        "---\nstate: inbox\ntarget_repo: github.com/example/foo\ntype: uwi\nquestion_type: choice\n"
        "choices: [その対応で問題無い, 問題がある]\n---\n\n"
        f"{uwi_module.QUESTION_HEADING}\n\n実施済みの対応を承認しますか。\n\n"
        f"{uwi_module.ANSWER_HEADING}\n\n{uwi_module.ANSWER_MARKER}\n",
        encoding="utf-8",
    )
    other = _fixed_path(notes, "other.md")
    other.write_text("---\nstate: adopted\ntype: awi\n---\n既存\n", encoding="utf-8")
    before = other.read_bytes()

    assert uwi_module.answer_uwi(notes, filename=path.name, answer="その対応で問題無い")

    assert "state: adopted\n" in path.read_text(encoding="utf-8")
    assert other.read_bytes() == before


@pytest.mark.parametrize("answer", ["問題がある", "別の回答", "その対応で問題無い\nただし追加対応を求める"])
@pytest.mark.parametrize("question_type", ["post-approval", "choice", "yes-no"])
def test_answer_uwi_does_not_auto_adopt_other_answers(
    answer: str,
    question_type: str,
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """否定回答と標準外回答は従来どおり回答だけを保存する。"""
    notes = _setup_notes(tmp_path)
    monkeypatch.setattr(_wi_sync, "repo_lock", lambda *_args, **_kwargs: contextlib.nullcontext())
    monkeypatch.setattr(_wi_sync, "pull", lambda _path: None)
    monkeypatch.setattr(_wi_sync, "commit_and_push", lambda *_args, **_kwargs: None)
    path = _fixed_path(notes, "post-approval-other.md")
    path.write_text(
        f"---\nstate: inbox\ntarget_repo: github.com/example/foo\ntype: uwi\nquestion_type: {question_type}\n"
        "choices: その対応で問題無い,問題がある\n---\n\n"
        f"{uwi_module.QUESTION_HEADING}\n\n実施済みの対応を承認しますか。\n\n"
        f"{uwi_module.ANSWER_HEADING}\n\n{uwi_module.ANSWER_MARKER}\n",
        encoding="utf-8",
    )

    assert uwi_module.answer_uwi(notes, filename=path.name, answer=answer) is True
    assert path.exists()
    assert "state: inbox\n" in _fixed_path(notes, path.name).read_text(encoding="utf-8")


def test_answer_uwi_rejects_empty_answer_without_changing_existing_answer(
    tmp_path: pathlib.Path,
) -> None:
    """空回答は共有の実装で受け取る際に拒否し、既存回答を1バイトも変更しない。"""
    notes = _setup_notes(tmp_path)
    path = _fixed_path(notes, "20260101-000000-003.md")
    path.write_text(
        "---\nstate: inbox\ntarget_repo: github.com/example/foo\ntype: uwi\nquestion_type: free-form\n---\n\n"
        f"{uwi_module.QUESTION_HEADING}\n\n質問本文。\n\n"
        f"{uwi_module.ANSWER_HEADING}\n\n{uwi_module.ANSWER_MARKER}\n既存回答\n",
        encoding="utf-8",
    )
    before = path.read_bytes()

    with pytest.raises(uwi_module.WebInputError, match="回答本文が空です") as error_info:
        uwi_module.answer_uwi(notes, filename=path.name, answer=" \n\t")

    assert path.read_bytes() == before
    assert error_info.value.next_action == "回答を記入して再実行する"


def test_answer_uwi_without_marker_asks_to_report_to_user(tmp_path: pathlib.Path) -> None:
    """回答欄マーカーを失った保存済みUWIは、構造の破損としてユーザーへの報告を次の操作に返す。"""
    notes = _setup_notes(tmp_path)
    path = _fixed_path(notes, "20260101-000000-004.md")
    path.write_text(
        "---\nstate: inbox\ntarget_repo: github.com/example/foo\ntype: uwi\nquestion_type: free-form\n---\n\n"
        f"{uwi_module.QUESTION_HEADING}\n\n質問本文。\n\n{uwi_module.ANSWER_HEADING}\n\n",
        encoding="utf-8",
    )
    before = path.read_bytes()

    with pytest.raises(uwi_module.WebInputError, match="回答欄マーカーがありません") as error_info:
        uwi_module.answer_uwi(notes, filename=path.name, answer="採用する", storage=_wi_sync.Storage(local_only=True))

    assert path.read_bytes() == before
    assert "ユーザーへ報告する" in error_info.value.next_action


def test_answer_uwi_targets_explicit_and_stored_state(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """状態指定と省略の両方で保存した状態のUWIへ回答する。"""
    notes = _setup_notes(tmp_path)
    monkeypatch.setattr(_wi_sync, "repo_lock", lambda *_args, **_kwargs: contextlib.nullcontext())
    monkeypatch.setattr(_wi_sync, "pull", lambda _path: None)
    monkeypatch.setattr(_wi_sync, "commit_and_push", lambda *_args, **_kwargs: None)
    content = (
        "---\nstate: inbox\ntarget_repo: github.com/example/foo\ntype: uwi\nquestion_type: free-form\n---\n\n"
        f"{uwi_module.QUESTION_HEADING}\n\n質問本文。\n\n"
        f"{uwi_module.ANSWER_HEADING}\n\n{uwi_module.ANSWER_MARKER}\n"
    )
    inbox = _fixed_path(notes, "inbox.md")
    processing = _fixed_path(notes, "processing.md")
    inbox.write_text(content, encoding="utf-8")
    processing_content = content.replace("state: inbox", "state: processing")
    processing.write_text(processing_content, encoding="utf-8")

    assert uwi_module.answer_uwi(notes, filename=inbox.name, state="inbox", answer="未処理側への回答") is True
    assert inbox.read_text(encoding="utf-8").endswith("未処理側への回答\n")
    assert processing.read_text(encoding="utf-8") == processing_content

    assert uwi_module.answer_uwi(notes, filename=processing.name, answer="従来経路の回答") is True
    assert processing.read_text(encoding="utf-8").endswith("従来経路の回答\n")


def test_answer_uwi_accepts_explicit_hold_state(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """hold上のUWIへ明示状態指定で回答する。"""
    notes = _setup_notes(tmp_path)
    held = _write_uwi_file(notes, "held.md")
    held.write_text(held.read_text(encoding="utf-8") + f"{uwi_module.ANSWER_MARKER}\n", encoding="utf-8")
    held.write_text(held.read_text(encoding="utf-8").replace("state: inbox", "state: hold"), encoding="utf-8")
    monkeypatch.setattr(_wi_sync, "repo_lock", lambda *_args, **_kwargs: contextlib.nullcontext())
    monkeypatch.setattr(_wi_sync, "pull", lambda _path: None)
    monkeypatch.setattr(_wi_sync, "commit_and_push", lambda *_args, **_kwargs: None)

    assert uwi_module.answer_uwi(notes, filename="held.md", state="hold", answer="回答") is True
    assert held.read_text(encoding="utf-8").endswith("回答\n")


def test_reject_reserved_uwi_markup_allows_plain_body() -> None:
    """予約書式を含まない本文は拒否しない。"""
    uwi_module.reject_reserved_uwi_markup("判定根拠を示したうえで、どちらの案を採用しますか？")


def test_reject_reserved_uwi_markup_ignores_inline_heading_text() -> None:
    """行頭以外に現れる見出し相当の文字列は拒否対象としない。"""
    uwi_module.reject_reserved_uwi_markup(f"本文中で`{uwi_module.ANSWER_HEADING}`という語に言及するだけの記述は許容しますか？")


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        (uwi_module.ANSWER_MARKER, ["回答欄マーカー"]),
        (uwi_module.QUESTION_HEADING, ["見出し（## 質問）"]),
        (
            f"{uwi_module.ANSWER_MARKER}\n{uwi_module.QUESTION_HEADING}\n{uwi_module.ANSWER_HEADING}",
            ["回答欄マーカー", "見出し（## 質問）", "見出し（## 回答）"],
        ),
        (f"{uwi_module.ANSWER_HEADING}\n{uwi_module.ANSWER_HEADING}", ["見出し（## 回答）", "見出し（## 回答）"]),
    ],
)
def test_reject_reserved_uwi_markup_reports_every_violation(body: str, expected: list[str]) -> None:
    """予約要素を最後まで走査し、検出順に同じ入力エラーへ列挙する。"""
    with pytest.raises(uwi_module.WebInputError) as exc_info:
        uwi_module.reject_reserved_uwi_markup(body)
    message = str(exc_info.value)
    assert message.count("見出し（## 回答）") == expected.count("見出し（## 回答）")
    assert all(item in message for item in expected)
    assert exc_info.value.next_action == "本文には質問内容のみを書いてください"
