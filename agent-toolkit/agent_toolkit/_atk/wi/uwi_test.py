# pylint: disable=useless-import-alias  # pytestのfixtureを試験モジュールの名前空間へ登録するため。
"""WIのuwi: baseのテスト。"""

import contextlib
import pathlib
import subprocess
from typing import Any

import pytest

from agent_toolkit import atk
from agent_toolkit._atk.wi import add as add_module
from agent_toolkit._atk.wi import sync as _wi_sync
from agent_toolkit._atk.wi import uwi as uwi_module
from agent_toolkit._testing.git_fakes import _FIXED_HEAD_COMMIT
from agent_toolkit._testing.wi_entry_files import entry_path as _fixed_path
from agent_toolkit._testing.wi_uwi_support import _body_file_args, _invoke_uwi_add, _make_uwi_add_fake
from agent_toolkit._testing.wi_uwi_support import _clear_agent_environment as _clear_agent_environment
from agent_toolkit.atk_test import _FIXED_DT, _FIXED_TIMESTAMP, _setup_notes, _write_uwi_file


def test_flat_uwi_operations_are_public(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """平引数追加が生成名を返し、回答欄付きUWIを書き込む。"""
    notes = tmp_path / "private-notes"
    monkeypatch.setattr(_wi_sync, "repo_lock", lambda *_args, **_kwargs: contextlib.nullcontext())
    monkeypatch.setattr(_wi_sync, "pull", lambda _path: None)
    monkeypatch.setattr(_wi_sync, "commit_and_push", lambda *_args, **_kwargs: None)
    generated = add_module.add_entries(
        notes,
        messages=[f"## 質問\n\nこの方針を採用しますか？\n\n## 回答\n\n{uwi_module.ANSWER_MARKER}\n"],
        target_repo="github.com/example/repo",
        source=None,
        now=_FIXED_DT,
        entry_type="uwi",
        scope="test",
        question_type="yes-no",
        choices=None,
    )
    assert generated == [f"{_FIXED_TIMESTAMP}-001.md"]
    content = _fixed_path(notes, generated[0]).read_text(encoding="utf-8")
    assert "type: uwi" in content
    assert "question_type: yes-no" in content
    assert "ユーザーはこの行以降に回答を追記する" in content
    assert content.count(uwi_module.QUESTION_HEADING) == 1
    assert content.count(uwi_module.ANSWER_HEADING) == 1
    assert content.count(uwi_module.ANSWER_MARKER) == 1


@pytest.mark.parametrize(
    ("body", "warned"),
    [
        ("fb 090830 これでよいか?", True),
        ("採否は?", False),
        ("保存の失敗をエンドユーザーへ通知する変更を残してよいか?", False),
        ("fb 090830についての採否を残してよいか?", False),
        ("あ" * 40 + "fb090830" + "い" * 40 + "これでよいか?", True),
        ("`afb67cd`の変更を残してよいか?", False),
        ("対象ファイルの現在の実装状況を確認した。" * 8 + "この変更を残してよいか?", False),
    ],
)
def test_public_uwi_add_self_containment_warning(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    body: str,
    warned: bool,
) -> None:
    """文脈の無い一時識別子だけを警告し、短さやOIDによらず質問本文を保存する。"""
    notes = _setup_notes(tmp_path)
    myrepo = tmp_path / "myrepo"
    myrepo.mkdir()
    monkeypatch.setattr(subprocess, "run", _make_uwi_add_fake(myrepo))
    assert _invoke_uwi_add(tmp_path, str(myrepo), body=body).code == 0
    stderr = capsys.readouterr().err
    assert ("一時識別子の単独使用" in stderr) is warned
    assert ("自己完結要件" in stderr) is warned
    saved = _fixed_path(notes, f"{_FIXED_TIMESTAMP}-001.md").read_text(encoding="utf-8")
    assert body in saved
    assert saved.count(uwi_module.ANSWER_MARKER) == 1


class TestUwiAdd:
    """UWI投入の基本動作検証。"""

    @pytest.mark.parametrize("prefix", ["", "\n\n## 質問\n\n"], ids=["plain", "question-heading"])
    @pytest.mark.parametrize(
        "suffix",
        ["", "\n\n## 回答\n\n", f"\n\n## 回答\n\n{uwi_module.ANSWER_MARKER}\n\n"],
        ids=["plain", "empty-answer", "answer-marker"],
    )
    def test_saved_wrapper_is_accepted_by_add_and_edit(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
        prefix: str,
        suffix: str,
    ) -> None:
        """保存形式の未回答外枠を投入・編集しても、見出しとマーカーを各1つに保つ。"""
        notes = _setup_notes(tmp_path)
        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()
        monkeypatch.setattr(subprocess, "run", _make_uwi_add_fake(myrepo))
        question = "この方針を採用しますか？\n\n## 判断材料\n\n既存の挙動を確認した。"

        assert _invoke_uwi_add(tmp_path, str(myrepo), body=prefix + question + suffix).code == 0
        path = _fixed_path(notes, f"{_FIXED_TIMESTAMP}-001.md")
        content = path.read_text(encoding="utf-8")
        expected_body = f"## 質問\n\n{question}\n\n## 回答\n\n{uwi_module.ANSWER_MARKER}\n"
        assert content.split("---\n", 2)[2].lstrip("\n") == expected_body
        assert "警告" not in capsys.readouterr().err

        edited_question = question.replace("この方針", "更新した方針")
        with pytest.raises(SystemExit) as edited:
            atk.main(
                ["wi", "edit", path.name, *_body_file_args(tmp_path, prefix + edited_question + suffix)],
                home=tmp_path,
            )
        assert edited.value.code == 0
        assert path.read_text(encoding="utf-8").split("---\n", 2)[2].lstrip("\n") == expected_body.replace(
            "この方針", "更新した方針"
        )

    @pytest.mark.parametrize(
        "body",
        [
            f"## 質問\n\n採用しますか？\n\n## 回答\n\n{uwi_module.ANSWER_MARKER}\nはい",
            "採用しますか？\n\n## 回答\n\nはい",
            "採用しますか？\n\n## 質問\n\n補足",
            "採用しますか？\n\n## 回答\n\n補足\n\n## 判断材料\n\n事実",
            f"採用しますか？\n\n{uwi_module.ANSWER_MARKER}",
            f"採用しますか？\n\n## 回答\n\n{uwi_module.ANSWER_MARKER}\n{uwi_module.ANSWER_MARKER}",
            f"採用しますか？\n\n## 回答{uwi_module.ANSWER_MARKER}",
            "## 質問\n\n採用しますか？\n\n## 質問\n\n補足\n\n## 回答",
        ],
        ids=[
            "answered-marker",
            "answered",
            "inner-question",
            "inner-answer",
            "bare-marker",
            "double-marker",
            "inline-marker",
            "nested-wrapper",
        ],
    )
    def test_reserved_markup_is_rejected_after_wrapper_removal(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
        body: str,
    ) -> None:
        """外枠以外の予約要素を含む投入・編集は既存ファイルを変更しない。"""
        notes = _setup_notes(tmp_path)
        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()
        monkeypatch.setattr(subprocess, "run", _make_uwi_add_fake(myrepo))
        path = _write_uwi_file(notes, f"{_FIXED_TIMESTAMP}-001.md", question="元の質問か？")
        original = path.read_bytes()
        assert _invoke_uwi_add(tmp_path, str(myrepo), body=body).code == 1
        assert "UWI本文にツールが自動付与する要素" in capsys.readouterr().err
        with pytest.raises(SystemExit) as edited:
            atk.main(["wi", "edit", path.name, *_body_file_args(tmp_path, body)], home=tmp_path)
        assert edited.value.code == 1
        assert "UWI本文にツールが自動付与する要素" in capsys.readouterr().err
        assert path.read_bytes() == original
        assert list(notes.glob("[0-9][0-9][0-9][0-9]/[0-9][0-9]/*.md")) == [path]

    def test_single_message_generates_one_file(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """単一メッセージで1ファイルが生成され、frontmatter・本文構造が正しい。"""
        notes = _setup_notes(tmp_path)
        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()
        monkeypatch.setattr(subprocess, "run", _make_uwi_add_fake(myrepo))

        assert _invoke_uwi_add(tmp_path, str(myrepo), "--scope", "theme1", body="未確認の挙動").code == 0

        files = sorted(notes.glob("[0-9][0-9][0-9][0-9]/[0-9][0-9]/*.md"))
        assert len(files) == 1
        content = files[0].read_text(encoding="utf-8")
        assert "target_repo: github.com/example/myrepo" in content
        assert f"target_commit: {_FIXED_HEAD_COMMIT}" in content
        assert "scope: theme1" in content
        assert "question_type: yes-no" in content
        assert "created:" not in content.split("---\n\n", 1)[0]
        assert "## 質問\n\n未確認の挙動" in content
        assert "## 回答" in content

    def test_choice_requires_choices(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """question-type=choice時に--choices未指定でusage表示付きのexit 2になる。"""
        _setup_notes(tmp_path)
        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()

        def fake_run(cmd: list[str], *_a: object, **kw: object) -> subprocess.CompletedProcess[Any]:
            if cmd[:5] == ["git", "-C", str(myrepo), "remote", "get-url"]:
                stdout: Any = "https://github.com/example/myrepo.git\n" if kw.get("text") else b""
                return subprocess.CompletedProcess(cmd, returncode=0, stdout=stdout, stderr="" if kw.get("text") else b"")
            empty: Any = "" if kw.get("text") else b""
            return subprocess.CompletedProcess(cmd, returncode=0, stdout=empty, stderr=empty)

        monkeypatch.setattr(subprocess, "run", fake_run)

        assert _invoke_uwi_add(tmp_path, str(myrepo), "--question-type", "choice", body="q").code == 2
        captured = capsys.readouterr()
        assert "使い方: atk wi add" in captured.err
        assert "--choices を指定してください" in captured.err

    def test_add_without_question_mark_warns(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """問いを含まない本文投入時に警告が標準エラーへ出力され、投入自体は成功する。"""
        _setup_notes(tmp_path)
        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()
        monkeypatch.setattr(subprocess, "run", _make_uwi_add_fake(myrepo))

        assert _invoke_uwi_add(tmp_path, str(myrepo), body="実施報告のみで疑問文を含まない本文").code == 0
        stderr = capsys.readouterr().err
        assert "警告" in stderr
        assert f"{_FIXED_TIMESTAMP}-001.md" in stderr

    def test_add_with_question_mark_no_warning(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """疑問文を含む本文投入時は警告が出力されない。"""
        _setup_notes(tmp_path)
        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()
        monkeypatch.setattr(subprocess, "run", _make_uwi_add_fake(myrepo))

        result = _invoke_uwi_add(
            tmp_path,
            str(myrepo),
            body="この対応でよいか？判定根拠は既存実装の挙動確認結果であり、"
            "選択肢は採用と却下の二択とし、トレードオフとして実装コストと品質維持の"
            "バランスを考慮した経緯を踏まえて判断してほしい。背景として前提となる範囲・方針も併記する。",
        )
        assert result.code == 0
        assert "警告" not in capsys.readouterr().err

    def test_choice_without_question_mark_warns(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """question-type=choiceでも冒頭に問いが無い本文は警告され、保存は成功する。

        `uwi-format.md`「構成」は冒頭の疑問文を質問の種類によらず求めるため、choiceを判定から外さない。
        """
        _setup_notes(tmp_path)
        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()
        monkeypatch.setattr(subprocess, "run", _make_uwi_add_fake(myrepo))

        result = _invoke_uwi_add(
            tmp_path,
            str(myrepo),
            "--question-type",
            "choice",
            "--choices",
            "A,B",
            body="実施報告のみで疑問文を含まない選択式本文。判定根拠は既存実装の挙動確認結果であり、"
            "選択肢は採用と却下の二択とし、トレードオフとして実装コストと品質維持の"
            "バランスを考慮した経緯を踏まえて判断してほしい。",
        )
        assert result.code == 0
        stderr = capsys.readouterr().err
        assert "冒頭（最初のH2見出しより前）に問い（疑問文）が無い" in stderr

    @pytest.mark.parametrize(
        ("question_type", "choices", "body", "warned"),
        [
            # 観測されたUWIと同じ構成: 冒頭の問い、選択肢の節、平叙文で終わる判断材料。
            *(
                (
                    kind,
                    extra,
                    "既定の実行レビュー担当を`sol/medium`へ変えてよいですか。\n\n"
                    "## 選択肢と帰結\n\n- 変える: 次回から使う。\n- 変えない: 現状を保つ。\n\n"
                    "## 判断材料\n\n- 所要時間の差は未計測です。",
                    False,
                )
                for kind, extra in (("yes-no", ()), ("post-approval", ()), ("choice", ("--choices", "変える,変えない")))
            ),
            # 冒頭は平叙文で、判断材料の中だけに「か」で終わる文がある本文は、冒頭に問いが無いため警告する。
            (
                "yes-no",
                (),
                "既定の実行レビュー担当を`sol/medium`へ変える提案。\n\n## 判断材料\n\n- 所要時間が縮むかは不明か。",
                True,
            ),
            # 問いの「か」の直後に括弧書きの補足が続く本文も問いとみなす。
            (
                "choice",
                ("--choices", "A,B"),
                "この方針で回答してもらえますか（既存の判断を保つ場合はBを選ぶ）\n\n## 判断材料\n\n- 既存の判断の記録がある。",
                False,
            ),
        ],
        ids=["yes-no", "post-approval", "choice", "question-only-in-materials", "parenthetical"],
    )
    def test_question_is_judged_in_the_lead_before_the_first_heading(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
        question_type: str,
        choices: tuple[str, ...],
        body: str,
        warned: bool,
    ) -> None:
        """問いの有無は最初のH2見出しより前で判定し、本文末尾の文や質問の種類に左右されない。"""
        _setup_notes(tmp_path)
        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()
        monkeypatch.setattr(subprocess, "run", _make_uwi_add_fake(myrepo))

        result = _invoke_uwi_add(tmp_path, str(myrepo), "--question-type", question_type, *choices, body=body)
        assert result.code == 0
        stderr = capsys.readouterr().err
        # 警告の有無は文言の改訂に左右されない共通部分で判定する。
        assert ("問い（疑問文）が" in stderr) is warned, stderr


class TestUwiAddEditorBeforePull:
    """UWI投入: `collect_message_via_editor`を`pull`より前に呼ぶ順序保証。

    エディター起動はロック外・ロック取得前に行う設計であり、`pull`失敗はエディターで
    確定済みの本文取得後（`repo_lock`保持下）に発生する。
    `question_type == "choice" and not args.choices`のバリデーションは
    エディター起動より前に維持する。
    """

    def test_pull_fails_after_editor_invoked(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """messages省略時、エディターは起動された後にpullが失敗して例外が送出される（対象リポジトリはcwdから解決）。"""
        notes = _setup_notes(tmp_path)
        monkeypatch.setenv("EDITOR", "fake-editor")
        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()
        editor_calls: list[list[str]] = []

        def fake_run(cmd: list[str], *_a: object, **kw: object) -> subprocess.CompletedProcess[Any]:
            empty: Any = "" if kw.get("text") else b""
            if cmd == ["git", "rev-parse", "--show-toplevel"]:
                stdout: Any = f"{myrepo}\n" if kw.get("text") else f"{myrepo}\n".encode()
                return subprocess.CompletedProcess(cmd, returncode=0, stdout=stdout, stderr=empty)
            if cmd == ["git", "-C", str(myrepo), "rev-parse", "--is-inside-work-tree"]:
                return subprocess.CompletedProcess(cmd, returncode=0, stdout="true\n", stderr="")
            if cmd == ["git", "-C", str(myrepo), "remote", "get-url", "origin"]:
                stdout = (
                    "https://github.com/example/myrepo.git\n" if kw.get("text") else b"https://github.com/example/myrepo.git\n"
                )
                return subprocess.CompletedProcess(cmd, returncode=0, stdout=stdout, stderr=empty)
            if cmd == ["git", "-C", str(myrepo), "rev-parse", "--verify", "HEAD^{commit}"]:
                stdout = f"{_FIXED_HEAD_COMMIT}\n" if kw.get("text") else f"{_FIXED_HEAD_COMMIT}\n".encode()
                return subprocess.CompletedProcess(cmd, returncode=0, stdout=stdout, stderr=empty)
            if cmd[:2] == ["git", "merge"]:
                raise subprocess.CalledProcessError(returncode=1, cmd=cmd)
            if cmd[0] == "fake-editor":
                editor_calls.append(list(cmd))
                pathlib.Path(cmd[1]).write_text("編集後の本文\n", encoding="utf-8")
                return subprocess.CompletedProcess(cmd, returncode=0, stdout=empty, stderr=empty)
            return subprocess.CompletedProcess(cmd, returncode=0, stdout=empty, stderr=empty)

        monkeypatch.setattr(subprocess, "run", fake_run)

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "add", "--type=uwi", "--question-type=yes-no"], home=tmp_path, now=_FIXED_DT)

        assert exc_info.value.code == 1
        assert editor_calls
        assert not list(notes.glob("[0-9][0-9][0-9][0-9]/[0-9][0-9]/*.md"))

    def test_choice_validation_fires_before_pull(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """--question-type=choiceで--choices未指定の場合、pullを呼ばずexit 2で失敗する。"""
        _setup_notes(tmp_path)
        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()
        git_cmds: list[list[str]] = []

        def fake_run(cmd: list[str], *_a: object, **kw: object) -> subprocess.CompletedProcess[Any]:
            empty: Any = "" if kw.get("text") else b""
            if cmd == ["git", "-C", str(myrepo), "rev-parse", "--is-inside-work-tree"]:
                return subprocess.CompletedProcess(cmd, returncode=0, stdout="true\n", stderr="")
            if cmd == ["git", "-C", str(myrepo), "remote", "get-url", "origin"]:
                stdout: Any = (
                    "https://github.com/example/myrepo.git\n" if kw.get("text") else b"https://github.com/example/myrepo.git\n"
                )
                return subprocess.CompletedProcess(cmd, returncode=0, stdout=stdout, stderr=empty)
            if cmd[0] == "git":
                git_cmds.append(list(cmd))
            return subprocess.CompletedProcess(cmd, returncode=0, stdout=empty, stderr=empty)

        monkeypatch.setattr(subprocess, "run", fake_run)

        assert _invoke_uwi_add(tmp_path, str(myrepo), "--question-type", "choice", body="q").code == 2
        assert not any(c[:2] in (["git", "fetch"], ["git", "merge"], ["git", "rebase"]) for c in git_cmds)


class TestUwiAddRepoPathOverrideCli:
    """`atk wi add --type=uwi`（旧`mq add`）でREPO_PATH位置引数を廃止したことに伴うCLI事前変換層の検証。"""

    def test_repo_path_omitted_resolves_from_cwd(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """REPO_PATH省略時、対象リポジトリはカレントディレクトリのgit worktreeから解決される。"""
        notes = _setup_notes(tmp_path)
        cwd_repo = tmp_path / "cwdrepo"
        cwd_repo.mkdir()

        def fake_run(cmd: list[str], *_a: object, **kw: object) -> subprocess.CompletedProcess[Any]:
            empty: Any = "" if kw.get("text") else b""
            if cmd == ["git", "rev-parse", "--show-toplevel"]:
                stdout: Any = f"{cwd_repo}\n" if kw.get("text") else f"{cwd_repo}\n".encode()
                return subprocess.CompletedProcess(cmd, returncode=0, stdout=stdout, stderr=empty)
            if cmd == ["git", "-C", str(cwd_repo), "rev-parse", "--is-inside-work-tree"]:
                return subprocess.CompletedProcess(cmd, returncode=0, stdout="true\n", stderr="")
            if cmd == ["git", "-C", str(cwd_repo), "remote", "get-url", "origin"]:
                stdout = (
                    "https://github.com/example/cwdrepo.git\n"
                    if kw.get("text")
                    else b"https://github.com/example/cwdrepo.git\n"
                )
                return subprocess.CompletedProcess(cmd, returncode=0, stdout=stdout, stderr=empty)
            if cmd == ["git", "-C", str(cwd_repo), "rev-parse", "--verify", "HEAD^{commit}"]:
                stdout = f"{_FIXED_HEAD_COMMIT}\n" if kw.get("text") else f"{_FIXED_HEAD_COMMIT}\n".encode()
                return subprocess.CompletedProcess(cmd, returncode=0, stdout=stdout, stderr=empty)
            return subprocess.CompletedProcess(cmd, returncode=0, stdout=empty, stderr=empty)

        monkeypatch.setattr(subprocess, "run", fake_run)

        assert _invoke_uwi_add(tmp_path, body="この対応でよいか").code == 0
        content = next(notes.glob("[0-9][0-9][0-9][0-9]/[0-9][0-9]/*.md")).read_text(encoding="utf-8")
        assert "target_repo: github.com/example/cwdrepo" in content

    def test_directory_followed_by_message_uses_compat_path(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """MESSAGE先頭が実在ディレクトリで残り本文がある場合、旧REPO_PATH形式として互換動作する。"""
        notes = _setup_notes(tmp_path)
        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()
        monkeypatch.setattr(subprocess, "run", _make_uwi_add_fake(myrepo))

        assert _invoke_uwi_add(tmp_path, str(myrepo), body="この対応でよいか").code == 0
        content = next(notes.glob("[0-9][0-9][0-9][0-9]/[0-9][0-9]/*.md")).read_text(encoding="utf-8")
        assert "target_repo: github.com/example/myrepo" in content
        assert "この対応でよいか" in content


class TestUwiAddSourceOption:
    """UWI投入: `--source`指定時にfrontmatterへsource行を記録する。"""

    def test_source_recorded_when_given(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """--source=session-hold指定時、frontmatterにsource: session-holdが含まれる。"""
        notes = _setup_notes(tmp_path)
        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()
        monkeypatch.setattr(subprocess, "run", _make_uwi_add_fake(myrepo))

        result = _invoke_uwi_add(
            tmp_path,
            str(myrepo),
            "--scope",
            "hold",
            "--source",
            "session-hold",
            body="保留理由",
        )
        assert result.code == 0

        files = sorted(notes.glob("[0-9][0-9][0-9][0-9]/[0-9][0-9]/*.md"))
        content = files[0].read_text(encoding="utf-8")
        assert "source: session-hold" in content

    def test_source_absent_when_not_given(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """--source未指定時、frontmatterにsource行が含まれない。"""
        notes = _setup_notes(tmp_path)
        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()
        monkeypatch.setattr(subprocess, "run", _make_uwi_add_fake(myrepo))

        assert _invoke_uwi_add(tmp_path, str(myrepo), body="疑問文を含む質問本文か").code == 0

        files = sorted(notes.glob("[0-9][0-9][0-9][0-9]/[0-9][0-9]/*.md"))
        content = files[0].read_text(encoding="utf-8")
        assert "source:" not in content
