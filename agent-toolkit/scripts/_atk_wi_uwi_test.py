"""atk (agent-toolkit `atk wi`) のtbd系サブコマンドのテスト。

TBD種別の投入・一覧・編集・回答・採用・削除の単体テストを集約する。
既存サブコマンドのテストは`atk_test.py`に、拡張サブコマンド・オプションのテストは
`_atk_wi_extras_test.py`に分離する。共通ヘルパーは`atk_test.py`から再利用する。
"""

import argparse
import contextlib
import pathlib
import subprocess
import sys
from collections.abc import Callable
from typing import Any

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import _atk_wi_add as add_module  # noqa: E402  # pylint: disable=wrong-import-position
import _atk_wi_uwi as tbd_module  # noqa: E402  # pylint: disable=wrong-import-position
import atk  # noqa: E402  # pylint: disable=wrong-import-position
from _atk_git_fake_test_helpers import _FIXED_HEAD_COMMIT  # noqa: E402  # pylint: disable=wrong-import-position
from _atk_wi_common import _is_uwi_answered  # noqa: E402  # pylint: disable=wrong-import-position
from _atk_wi_uwi import (  # noqa: E402  # pylint: disable=wrong-import-position
    _cmd_answer,
    _detect_self_containment_deficiency,
)
from atk_test import (  # pylint: disable=wrong-import-position
    _FIXED_DT,
    _FIXED_TIMESTAMP,
    _GitCall,
    _make_subprocess_fake,
    _setup_notes,
    _write_feedback_file,
    _write_tbd_file,
)  # noqa: E402  # pylint: disable=wrong-import-position

_AGENT_ENVIRONMENT_VARIABLES = ("AI_AGENT", "CODEX_CI", "CLAUDECODE", "CURSOR_AGENT")


@pytest.fixture(autouse=True)
def _clear_agent_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """回答テストをホスト側のエージェント環境変数から隔離する。"""
    for name in _AGENT_ENVIRONMENT_VARIABLES:
        monkeypatch.delenv(name, raising=False)


def _make_tbd_add_fake(myrepo: pathlib.Path) -> Callable[..., subprocess.CompletedProcess[Any]]:
    """TBD投入検証用fake_runを生成する。`myrepo`のorigin URLのみ実URLを返し、それ以外は空応答を返す。"""

    def fake_run(cmd: list[str], *_a: object, **kw: object) -> subprocess.CompletedProcess[Any]:
        if cmd == ["git", "-C", str(myrepo), "remote", "get-url", "origin"]:
            stdout: Any = (
                "https://github.com/example/myrepo.git\n" if kw.get("text") else b"https://github.com/example/myrepo.git\n"
            )
            return subprocess.CompletedProcess(cmd, returncode=0, stdout=stdout, stderr="" if kw.get("text") else b"")
        if cmd == ["git", "-C", str(myrepo), "rev-parse", "--is-inside-work-tree"]:
            stdout = "true\n" if kw.get("text") else b"true\n"
            return subprocess.CompletedProcess(cmd, returncode=0, stdout=stdout, stderr="" if kw.get("text") else b"")
        if cmd == ["git", "-C", str(myrepo), "rev-parse", "--verify", "HEAD^{commit}"]:
            stdout = f"{_FIXED_HEAD_COMMIT}\n" if kw.get("text") else f"{_FIXED_HEAD_COMMIT}\n".encode()
            return subprocess.CompletedProcess(cmd, returncode=0, stdout=stdout, stderr="" if kw.get("text") else b"")
        empty: Any = "" if kw.get("text") else b""
        return subprocess.CompletedProcess(cmd, returncode=0, stdout=empty, stderr=empty)

    return fake_run


def test_flat_tbd_operations_are_public(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """平引数追加が生成名を返し、回答欄付きTBDを書き込む。"""
    notes = tmp_path / "private-notes"
    monkeypatch.setattr(add_module, "_repo_lock", lambda *_args, **_kwargs: contextlib.nullcontext())
    monkeypatch.setattr(add_module, "_pull", lambda _path: None)
    monkeypatch.setattr(add_module, "_commit_and_push", lambda *_args, **_kwargs: None)
    generated = add_module.add_entries(
        notes,
        messages=["この方針を採用しますか？"],
        target_repo="github.com/example/repo",
        source=None,
        now=_FIXED_DT,
        entry_type="uwi",
        scope="test",
        question_type="yes-no",
        choices=None,
    )
    assert generated == [f"{_FIXED_TIMESTAMP}-001.md"]
    content = (notes / "inbox" / generated[0]).read_text(encoding="utf-8")
    assert "type: uwi" in content
    assert "question_type: yes-no" in content
    assert "ユーザーはこの行以降に回答を追記する" in content


class TestDetectSelfContainmentDeficiency:
    """`_detect_self_containment_deficiency`単体テスト（FB2: TBD本文の自己完結性検査）。"""

    def test_temporary_identifier_alone(self) -> None:
        assert _detect_self_containment_deficiency("fb 090830 これでよいか") == "一時識別子の単独使用"

    def test_too_short(self) -> None:
        assert _detect_self_containment_deficiency("採否は?") == "本文が短すぎる"

    def test_no_reasoning_vocabulary(self) -> None:
        long_body = (
            "対象ファイルの現在の実装状況を確認した。今回変更するコードの分量は限定的であり、"
            "既存のテストケースを維持しながら新しい関数を追加する。実装完了後は担当者へ結果を共有し、"
            "必要に応じて追加の修正を行う予定である。"
        )
        assert _detect_self_containment_deficiency(long_body) == "判定根拠語彙の欠落"

    def test_self_contained_message(self) -> None:
        body = (
            "採否判定を実施したい。理由は自己完結性欠如を検出したいためであり、判定根拠として"
            "一時識別子の単独使用の有無・本文の長さ・判定根拠語彙の有無をそれぞれ確認した結果、"
            "いずれの欠落条件にも該当しないと判断した。選択肢は警告のみか投入拒否かのトレードオフを含む。"
        )
        assert _detect_self_containment_deficiency(body) is None

    def test_identifier_with_context_words_not_flagged(self) -> None:
        """一時識別子の近傍に文脈語がある場合、判定Aでは警告しない。ただし短文なら判定Bで該当し得る。"""
        body = (
            "fb 090830についての背景として、既存実装の対象範囲を確認したうえで反映方針を判定した"
            "経緯があり、実装完了後の採否は本文のみで判定可能であり、判定根拠・選択肢・トレードオフも"
            "すべて本文中に記載済みである。"
        )
        assert len(body.strip()) >= 100
        assert _detect_self_containment_deficiency(body) is None

    def test_only_length_boundary_short(self) -> None:
        """判定Bのみ発火（100文字未満だが識別子・語彙欠落は無し）。"""
        body = "理由は根拠が薄いためであり、選択肢は採用と却下の二択で背景も明確である。"
        assert len(body.strip()) < 100
        assert _detect_self_containment_deficiency(body) == "本文が短すぎる"

    def test_only_identifier_boundary_long(self) -> None:
        """判定Aのみ発火（100文字以上だが単独識別子）。近傍30文字以内に文脈語を含めない冗長文とする。"""
        body = (
            "あああああああああああああああああああああああああああああああ"
            "fb090830いいいいいいいいいいいいいいいいいいいいいいいいいいいいいい"
            "ううううううううううううううううううううううううううううううう"
        )
        assert len(body.strip()) >= 100
        assert _detect_self_containment_deficiency(body) == "一時識別子の単独使用"


class TestCmdTbdAddSelfContainmentWarning:
    """TBD投入: 自己完結性ヒューリスティック警告と疑問文警告の併存を検証する。"""

    def test_warning_printed_for_short_body(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """短い本文投入時に自己完結性警告が出力され、疑問文警告と併存し得ること。"""
        _setup_notes(tmp_path)
        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()
        monkeypatch.setattr(subprocess, "run", _make_tbd_add_fake(myrepo))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(
                ["wi", "add", "--type=uwi", str(myrepo), "採否は?"],
                home=tmp_path,
                now=_FIXED_DT,
            )
        assert exc_info.value.code == 0
        stderr = capsys.readouterr().err
        assert "agent-toolkit:add-feedbackが定める自己完結要件" in stderr
        assert "agent-toolkit:process-feedbacks" not in stderr


class TestTbdAdd:
    """TBD投入の基本動作検証。"""

    def test_single_message_generates_one_file(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """単一メッセージで1ファイルが生成され、frontmatter・本文構造が正しい。"""
        notes = _setup_notes(tmp_path)
        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()
        monkeypatch.setattr(subprocess, "run", _make_tbd_add_fake(myrepo))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(
                ["wi", "add", "--type=uwi", str(myrepo), "--scope", "theme1", "未確認の挙動"],
                home=tmp_path,
                now=_FIXED_DT,
            )
        assert exc_info.value.code == 0

        files = sorted((notes / "inbox").iterdir())
        assert len(files) == 1
        content = files[0].read_text(encoding="utf-8")
        assert "target_repo: github.com/example/myrepo" in content
        assert f"target_commit: {_FIXED_HEAD_COMMIT}" in content
        assert "scope: theme1" in content
        assert "question_type: free-form" in content
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

        with pytest.raises(SystemExit) as exc_info:
            atk.main(
                ["wi", "add", "--type=uwi", str(myrepo), "--question-type", "choice", "q"],
                home=tmp_path,
                now=_FIXED_DT,
            )
        assert exc_info.value.code == 2
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
        monkeypatch.setattr(subprocess, "run", _make_tbd_add_fake(myrepo))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(
                ["wi", "add", "--type=uwi", str(myrepo), "実施報告のみで疑問文を含まない本文"],
                home=tmp_path,
                now=_FIXED_DT,
            )
        assert exc_info.value.code == 0
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
        monkeypatch.setattr(subprocess, "run", _make_tbd_add_fake(myrepo))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(
                [
                    "wi",
                    "add",
                    str(myrepo),
                    "この対応でよいか？判定根拠は既存実装の挙動確認結果であり、"
                    "選択肢は採用と却下の二択とし、トレードオフとして実装コストと品質維持の"
                    "バランスを考慮した経緯を踏まえて判断してほしい。背景として前提となる範囲・方針も併記する。",
                ],
                home=tmp_path,
                now=_FIXED_DT,
            )
        assert exc_info.value.code == 0
        assert "警告" not in capsys.readouterr().err

    def test_choice_without_question_mark_no_warning(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """question-type=choice時は疑問文を含まない本文でも警告が出力されない。"""
        _setup_notes(tmp_path)
        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()
        monkeypatch.setattr(subprocess, "run", _make_tbd_add_fake(myrepo))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(
                [
                    "wi",
                    "add",
                    "--type=uwi",
                    str(myrepo),
                    "--question-type",
                    "choice",
                    "--choices",
                    "A,B",
                    "実施報告のみで疑問文を含まない選択式本文。判定根拠は既存実装の挙動確認結果であり、"
                    "選択肢は採用と却下の二択とし、トレードオフとして実装コストと品質維持の"
                    "バランスを考慮した経緯を踏まえて判断してほしい。",
                ],
                home=tmp_path,
                now=_FIXED_DT,
            )
        assert exc_info.value.code == 0
        assert "警告" not in capsys.readouterr().err


class TestTbdAddEditorBeforePull:
    """TBD投入: `_collect_message_via_editor`を`_pull`より前に呼ぶ順序保証。

    エディター起動はロック外・ロック取得前に行う設計であり、`_pull`失敗はエディターで
    確定済みの本文取得後（`_repo_lock`保持下）に発生する。
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
            atk.main(["wi", "add", "--type=uwi"], home=tmp_path, now=_FIXED_DT)

        assert exc_info.value.code == 1
        assert editor_calls
        assert not list((notes / "inbox").iterdir())

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

        with pytest.raises(SystemExit) as exc_info:
            atk.main(
                ["wi", "add", "--type=uwi", str(myrepo), "--question-type", "choice", "q"],
                home=tmp_path,
                now=_FIXED_DT,
            )

        assert exc_info.value.code == 2
        assert not any(c[:2] in (["git", "fetch"], ["git", "merge"], ["git", "rebase"]) for c in git_cmds)


class TestTbdAddRepoPathOverrideCli:
    """`mq add --type=uwi`のREPO_PATH位置引数廃止に伴うCLI事前変換層の検証。"""

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

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "add", "--type=uwi", "この対応でよいか"], home=tmp_path, now=_FIXED_DT)

        assert exc_info.value.code == 0
        content = next((notes / "inbox").iterdir()).read_text(encoding="utf-8")
        assert "target_repo: github.com/example/cwdrepo" in content

    def test_message_only_directory_errors(
        self,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """本文が続かないディレクトリのみの呼び出しは、usage表示付きの平易なエラーでexit 2になる。"""
        _setup_notes(tmp_path)
        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "add", "--type=uwi", str(myrepo)], home=tmp_path, now=_FIXED_DT)

        assert exc_info.value.code == 2
        captured = capsys.readouterr()
        assert "使い方: atk wi add" in captured.err
        error_line = captured.err.rstrip("\n").splitlines()[-1]
        assert "パスの指定は不要です" in error_line
        assert "REPO_PATH" not in error_line
        assert "MESSAGE" not in error_line

    def test_directory_followed_by_message_uses_compat_path(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """MESSAGE先頭が実在ディレクトリで残り本文がある場合、旧REPO_PATH形式として互換動作する。"""
        notes = _setup_notes(tmp_path)
        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()
        monkeypatch.setattr(subprocess, "run", _make_tbd_add_fake(myrepo))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "add", "--type=uwi", str(myrepo), "この対応でよいか"], home=tmp_path, now=_FIXED_DT)

        assert exc_info.value.code == 0
        content = next((notes / "inbox").iterdir()).read_text(encoding="utf-8")
        assert "target_repo: github.com/example/myrepo" in content
        assert "この対応でよいか" in content


class TestTbdAddSourceOption:
    """TBD投入: `--source`指定時にfrontmatterへsource行を記録する。"""

    def test_source_recorded_when_given(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """--source=session-hold指定時、frontmatterにsource: session-holdが含まれる。"""
        notes = _setup_notes(tmp_path)
        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()
        monkeypatch.setattr(subprocess, "run", _make_tbd_add_fake(myrepo))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(
                ["wi", "add", "--type=uwi", str(myrepo), "--scope", "hold", "--source", "session-hold", "保留理由"],
                home=tmp_path,
                now=_FIXED_DT,
            )
        assert exc_info.value.code == 0

        files = sorted((notes / "inbox").iterdir())
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
        monkeypatch.setattr(subprocess, "run", _make_tbd_add_fake(myrepo))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "add", "--type=uwi", str(myrepo), "疑問文を含む質問本文か"], home=tmp_path, now=_FIXED_DT)
        assert exc_info.value.code == 0

        files = sorted((notes / "inbox").iterdir())
        content = files[0].read_text(encoding="utf-8")
        assert "source:" not in content


class TestTbdMutationTargetRepoVerification:
    """TBDのedit・adopt・rm: `--target-repo`指定時のfrontmatter一致検証を検証する。

    既定のfrontmatter`target_repo`は`github.com/example/foo`（`_write_tbd_file`既定値）。
    """

    def test_tbd_edit_mismatch_exits_2(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """mq edit: `--target-repo`不一致時にexit 2でエディターは起動されない。"""
        notes = _setup_notes(tmp_path)
        _write_tbd_file(notes, f"{_FIXED_TIMESTAMP}-001.md", question="q")
        monkeypatch.setenv("EDITOR", "fake-editor")
        editor_calls: list[list[str]] = []

        def fake_run(cmd: list[str], *_a: object, **_kw: object) -> subprocess.CompletedProcess[bytes]:
            if cmd[0] == "fake-editor":
                editor_calls.append(list(cmd))
            return subprocess.CompletedProcess(cmd, returncode=0, stdout=b"", stderr=b"")

        monkeypatch.setattr(subprocess, "run", fake_run)

        with pytest.raises(SystemExit) as exc_info:
            atk.main(
                ["wi", "edit", f"{_FIXED_TIMESTAMP}-001.md", "--target-repo", "github.com/other/repo"],
                home=tmp_path,
            )

        assert exc_info.value.code == 2
        assert not editor_calls

    def test_tbd_adopt_mismatch_exits_2(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """mq adopt: `--target-repo`不一致時にexit 2でファイルは移動されない。"""
        notes = _setup_notes(tmp_path)
        _write_tbd_file(notes, f"{_FIXED_TIMESTAMP}-001.md", question="q", answer="はい")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(
                ["wi", "adopt", f"{_FIXED_TIMESTAMP}-001.md", "--target-repo", "github.com/other/repo"],
                home=tmp_path,
            )

        assert exc_info.value.code == 2
        assert (notes / "inbox" / f"{_FIXED_TIMESTAMP}-001.md").exists()
        assert not (notes / "adopted" / f"{_FIXED_TIMESTAMP}-001.md").exists()

    def test_tbd_adopt_match_succeeds(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """mq adopt: `--target-repo`一致時は通常通りadopted/へ移動する。"""
        notes = _setup_notes(tmp_path)
        _write_tbd_file(notes, f"{_FIXED_TIMESTAMP}-001.md", question="q", answer="はい")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(
                ["wi", "adopt", f"{_FIXED_TIMESTAMP}-001.md", "--target-repo", "github.com/example/foo"],
                home=tmp_path,
            )

        assert exc_info.value.code == 0
        assert (notes / "adopted" / f"{_FIXED_TIMESTAMP}-001.md").exists()

    def test_tbd_rm_mismatch_exits_2(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """mq rm: `--target-repo`不一致時にexit 2でファイルは削除されない。"""
        notes = _setup_notes(tmp_path)
        _write_tbd_file(notes, f"{_FIXED_TIMESTAMP}-001.md")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(
                ["wi", "rm", f"{_FIXED_TIMESTAMP}-001.md", "--target-repo", "github.com/other/repo"],
                home=tmp_path,
            )

        assert exc_info.value.code == 2
        assert (notes / "inbox" / f"{_FIXED_TIMESTAMP}-001.md").exists()


class TestTbdList:
    """TBD一覧のフィルター動作検証。"""

    def test_status_filter(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """--answered=noで未回答のみが1件1行（ファイル名・`target_repo`・要約）形式で出力される。"""
        notes = _setup_notes(tmp_path)
        _write_tbd_file(notes, f"{_FIXED_TIMESTAMP}-001.md", question="q1", answer="")
        _write_tbd_file(notes, f"{_FIXED_TIMESTAMP}-002.md", question="q2", answer="回答あり\n")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "list", "--type=uwi", "--answered", "no", "--no-json"], home=tmp_path)
        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert captured.out == f"# uwi\n{_FIXED_TIMESTAMP}-001.md: github.com/example/foo [inbox/unanswered] q1\n"


class TestTbdListSkipPull:
    """TBD一覧: --skip-pull指定時はremote同期全体をスキップする。"""

    def test_skip_pull_omits_git_pull(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """--skip-pull指定時はfetch・merge・rebaseが実行されない。"""
        notes = _setup_notes(tmp_path)
        _write_tbd_file(notes, f"{_FIXED_TIMESTAMP}-001.md", question="q1")
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(git_calls))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "list", "--type=uwi", "--skip-pull"], home=tmp_path)

        assert exc_info.value.code == 0
        assert not any(c["cmd"][:2] in (["git", "fetch"], ["git", "merge"], ["git", "rebase"]) for c in git_calls)


class TestTbdEdit:
    """TBD編集の境界条件検証。"""

    def test_rejects_traversal(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """パストラバーサル系のファイル名でexit 2。"""
        _setup_notes(tmp_path)
        monkeypatch.setenv("EDITOR", "vi")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "edit", "../escape.md"], home=tmp_path)
        assert exc_info.value.code == 2

    def test_no_diff_skips_commit(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """編集差分なしの場合はcommit・pushしない。"""
        notes = _setup_notes(tmp_path)
        _write_tbd_file(notes, f"{_FIXED_TIMESTAMP}-001.md", question="q")
        monkeypatch.setenv("EDITOR", "vi")
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(git_calls))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "edit", f"{_FIXED_TIMESTAMP}-001.md"], home=tmp_path)
        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "差分なし" in captured.out
        commit_calls = [c for c in git_calls if c["cmd"][:2] == ["git", "commit"]]
        assert commit_calls == []


class TestTbdAnswer:
    """answerサブコマンドの空集合・差分なし時の挙動検証。"""

    def test_no_unanswered_prints_message(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """未回答ゼロ時は案内のみでcommitしない。"""
        notes = _setup_notes(tmp_path)
        _write_tbd_file(notes, f"{_FIXED_TIMESTAMP}-001.md", question="q", answer="ans\n")
        monkeypatch.setenv("EDITOR", "vi")
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(git_calls))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "answer"], home=tmp_path)
        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "未回答のTBDはありません" in captured.out
        commit_calls = [c for c in git_calls if c["cmd"][:2] == ["git", "commit"]]
        assert commit_calls == []


class TestTbdAnswerEditorFailure:
    """answerサブコマンド: エディター非ゼロ終了時にexit 0を返さないことを検証する。"""

    def test_nonzero_editor_exit_is_treated_as_failure(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """エディターが非ゼロ終了コードで終了した場合、中断してexit 1を返しcommitしない。"""
        notes = _setup_notes(tmp_path)
        _write_tbd_file(notes, f"{_FIXED_TIMESTAMP}-001.md", question="q?", answer="")
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
        assert "エディターが終了コード1で終了しました" in captured.err
        commit_calls = [c for c in git_calls if c["cmd"][:2] == ["git", "commit"]]
        assert commit_calls == []


class TestTbdAnswerNonInteractive:
    """answerサブコマンド: ファイル名と回答本文を引数で受け取る非対話経路を検証する。

    自律実行中のエージェントが`$EDITOR`を介さずに回答を記録できることを担保する。
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
        path = _write_tbd_file(notes, filename, question="q?", answer=f"{tbd_module.ANSWER_MARKER}\n")
        monkeypatch.delenv("EDITOR", raising=False)
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(git_calls))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "answer", filename, "採用する"], home=tmp_path)

        assert exc_info.value.code == 0
        content = path.read_text(encoding="utf-8")
        assert content.rstrip().endswith("採用する")
        assert _is_uwi_answered(content)
        captured = capsys.readouterr()
        assert f"1件回答反映: {filename}" in captured.out
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
        path = _write_tbd_file(notes, filename, question="q?", answer=f"{tbd_module.ANSWER_MARKER}\n")
        before = path.read_text(encoding="utf-8")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "answer", filename, f"{tbd_module.ANSWER_MARKER}\n採用する"], home=tmp_path)

        assert exc_info.value.code == 1
        assert path.read_text(encoding="utf-8") == before
        assert "回答欄マーカーを含められません" in capsys.readouterr().err

    def test_filename_without_answer_body_exits_1(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """ファイル名のみの指定は対話モードへ移行せずexit 1で案内する。"""
        notes = _setup_notes(tmp_path)
        filename = f"{_FIXED_TIMESTAMP}-001.md"
        _write_tbd_file(notes, filename, question="q?", answer=f"{tbd_module.ANSWER_MARKER}\n")
        monkeypatch.setenv("EDITOR", "fake-editor")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "answer", filename], home=tmp_path)

        assert exc_info.value.code == 1
        assert "ファイル名と回答本文の両方を指定してください" in capsys.readouterr().err

    def test_answer_body_without_filename_exits_1(
        self,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """回答本文のみの指定もexit 1で案内する（CLIの位置引数では組めないため直接渡す）。"""
        notes = _setup_notes(tmp_path)

        with pytest.raises(SystemExit) as exc_info:
            _cmd_answer(argparse.Namespace(filename=None, answer_body="採用する"), notes)

        assert exc_info.value.code == 1
        assert "ファイル名と回答本文の両方を指定してください" in capsys.readouterr().err

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
        assert "inbox・processingのいずれにも存在しません" in capsys.readouterr().err

    def test_non_tbd_entry_exits_1(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """対象がTBDでない場合はexit 1となり、対象ファイルを変更しない。"""
        notes = _setup_notes(tmp_path)
        filename = f"{_FIXED_TIMESTAMP}-001.md"
        path = _write_feedback_file(notes, filename)
        before = path.read_text(encoding="utf-8")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "answer", filename, "採用する"], home=tmp_path)

        assert exc_info.value.code == 1
        assert path.read_text(encoding="utf-8") == before
        assert "回答はTBDのエントリにのみ適用できます" in capsys.readouterr().err


@pytest.mark.parametrize("environment_name", _AGENT_ENVIRONMENT_VARIABLES)
def test_agent_environment_rejects_tbd_answer_before_writing(
    environment_name: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """各エージェント環境ではTBD回答をCLI入口で拒否する。"""
    notes = _setup_notes(tmp_path)
    filename = f"{_FIXED_TIMESTAMP}-001.md"
    path = _write_tbd_file(notes, filename, question="q?", answer=f"{tbd_module.ANSWER_MARKER}\n")
    before = path.read_bytes()
    for name in _AGENT_ENVIRONMENT_VARIABLES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv(environment_name, "1")

    with pytest.raises(SystemExit) as exc_info:
        atk.main(["wi", "answer", filename, "採用する"], home=tmp_path)

    assert exc_info.value.code == 1
    assert path.read_bytes() == before
    assert (
        capsys.readouterr().err
        == "TBDの回答はユーザーだけが書き込みます。エージェント環境から起動したatkでは回答できません。\n"
    )


def test_answer_tbd_common_core_accepts_agent_environment(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ブラウザー経路が使う共有中核はエージェント環境でも回答を保存する。"""
    notes = _setup_notes(tmp_path)
    path = _write_tbd_file(notes, "tbd.md", question="q?", answer=f"{tbd_module.ANSWER_MARKER}\n")
    monkeypatch.setenv("AI_AGENT", "1")
    monkeypatch.setattr(tbd_module, "_repo_lock", lambda *_args, **_kwargs: contextlib.nullcontext())
    monkeypatch.setattr(tbd_module, "_pull", lambda _path: None)
    monkeypatch.setattr(tbd_module, "_commit_and_push", lambda *_args, **_kwargs: None)

    assert tbd_module.answer_tbd(notes, filename=path.name, answer="採用する")
    assert path.read_text(encoding="utf-8").endswith("採用する\n")


class TestTbdAdopt:
    """TBD採用: inboxからadopted/へ移動しコミットする。"""

    def test_single_file_adopted(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """1件のtb adopt実行でinboxから移動されadopted/に置かれコミットメッセージが正しいこと。"""
        notes = _setup_notes(tmp_path)
        _write_tbd_file(notes, f"{_FIXED_TIMESTAMP}-001.md", question="q", answer="はい")
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(git_calls))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "adopt", f"{_FIXED_TIMESTAMP}-001.md"], home=tmp_path)

        assert exc_info.value.code == 0
        assert not (notes / "inbox" / f"{_FIXED_TIMESTAMP}-001.md").exists()
        assert (notes / "adopted" / f"{_FIXED_TIMESTAMP}-001.md").exists()

        commit_cmd = [c["cmd"] for c in git_calls if "commit" in c["cmd"]][0]
        assert "chore: process 1 entry (adopted)" in commit_cmd

    def test_stamp_written_with_all_fields(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """--note・--commit指定時、adopted/配下のファイル末尾に採否・処理日時・対応commit・メモが追記される。"""
        notes = _setup_notes(tmp_path)
        _write_tbd_file(notes, f"{_FIXED_TIMESTAMP}-001.md", question="q", answer="はい")
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(git_calls))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(
                [
                    "wi",
                    "adopt",
                    f"{_FIXED_TIMESTAMP}-001.md",
                    "--note",
                    "TBD採用メモ",
                    "--commit",
                    "xyz9876",
                ],
                home=tmp_path,
            )

        assert exc_info.value.code == 0
        adopted_text = (notes / "adopted" / f"{_FIXED_TIMESTAMP}-001.md").read_text(encoding="utf-8")
        assert "## 処理結果" in adopted_text
        assert "- 採否: adopted" in adopted_text
        assert "- 処理日時: " in adopted_text
        assert "- 対応commit: xyz9876" in adopted_text
        assert "未検証のまま記録" in capsys.readouterr().err
        assert "- メモ: TBD採用メモ" in adopted_text

    def test_short_commit_is_resolved_with_explicit_worktree(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """明示作業ツリーに対応するTBDでは短縮revisionを完全OID化する。"""
        notes = _setup_notes(tmp_path)
        worktree = tmp_path / "worktree"
        worktree.mkdir()
        _write_tbd_file(notes, f"{_FIXED_TIMESTAMP}-001.md", question="q", answer="はい")
        git_calls: list[_GitCall] = []
        base_fake = _make_subprocess_fake(git_calls)
        full_oid = "c" * 40

        def fake_run(cmd: list[str], *args: object, **kwargs: object) -> subprocess.CompletedProcess[Any]:
            if cmd == ["git", "-C", str(worktree), "remote", "get-url", "origin"]:
                return subprocess.CompletedProcess(cmd, 0, "https://github.com/example/foo.git\n", "")
            if cmd == [
                "git",
                "-C",
                str(worktree),
                "rev-parse",
                "--verify",
                "--end-of-options",
                "abcdef1^{commit}",
            ]:
                return subprocess.CompletedProcess(cmd, 0, full_oid + "\n", "")
            return base_fake(cmd, *args, **kwargs)

        monkeypatch.setattr(subprocess, "run", fake_run)
        with pytest.raises(SystemExit) as exc_info:
            atk.main(
                [
                    "wi",
                    "adopt",
                    f"{_FIXED_TIMESTAMP}-001.md",
                    "--target-repo",
                    str(worktree),
                    "--commit=abcdef1",
                ],
                home=tmp_path,
            )
        assert exc_info.value.code == 0
        content = (notes / "adopted" / f"{_FIXED_TIMESTAMP}-001.md").read_text(encoding="utf-8")
        assert f"- 対応commit: {full_oid}" in content

    def test_multiple_files_adopted_single_commit(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """3件のtb adoptで全件がadopted/へ移動し単一コミットが行われること。"""
        notes = _setup_notes(tmp_path)
        _write_tbd_file(notes, f"{_FIXED_TIMESTAMP}-001.md", question="q1", answer="a1")
        _write_tbd_file(notes, f"{_FIXED_TIMESTAMP}-002.md", question="q2", answer="a2")
        _write_tbd_file(notes, f"{_FIXED_TIMESTAMP}-003.md", question="q3", answer="a3")
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(git_calls))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(
                [
                    "wi",
                    "adopt",
                    f"{_FIXED_TIMESTAMP}-001.md",
                    f"{_FIXED_TIMESTAMP}-002.md",
                    f"{_FIXED_TIMESTAMP}-003.md",
                ],
                home=tmp_path,
            )

        assert exc_info.value.code == 0
        for name in (f"{_FIXED_TIMESTAMP}-001.md", f"{_FIXED_TIMESTAMP}-002.md", f"{_FIXED_TIMESTAMP}-003.md"):
            assert not (notes / "inbox" / name).exists()
            assert (notes / "adopted" / name).exists()

        commit_calls = [c["cmd"] for c in git_calls if c["cmd"][:2] == ["git", "commit"]]
        assert len(commit_calls) == 1
        assert "chore: process 3 entries (adopted)" in commit_calls[0]

    def test_pushes_after_commit(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """TBD採用の実行後にgit pushが行われること。"""
        notes = _setup_notes(tmp_path)
        _write_tbd_file(notes, f"{_FIXED_TIMESTAMP}-001.md", question="q", answer="はい")
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(git_calls))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "adopt", f"{_FIXED_TIMESTAMP}-001.md"], home=tmp_path)

        assert exc_info.value.code == 0
        assert any(c["cmd"] == ["git", "push"] for c in git_calls)

    def test_rejects_traversal(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """パストラバーサル系のファイル名でexit 2。"""
        _setup_notes(tmp_path)
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "adopt", "../escape.md"], home=tmp_path)

        assert exc_info.value.code == 2
        captured = capsys.readouterr()
        assert "不正なファイル名" in captured.err or "基準ディレクトリ外" in captured.err

    def test_missing_file_exits(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """inboxに存在しないファイル名指定でexit 2と案内が出力される。"""
        _setup_notes(tmp_path)
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "adopt", "nonexistent.md"], home=tmp_path)

        assert exc_info.value.code == 2
        captured = capsys.readouterr()
        assert "inbox・processingのいずれにも存在しません" in captured.err

    def test_partial_missing_file_prevents_any_move(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """複数ファイル指定時、一部が未存在ならどのファイルも移動されない（部分移動防止）。"""
        notes = _setup_notes(tmp_path)
        _write_tbd_file(notes, f"{_FIXED_TIMESTAMP}-001.md", question="q1", answer="a1")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(
                ["wi", "adopt", f"{_FIXED_TIMESTAMP}-001.md", "nonexistent.md"],
                home=tmp_path,
            )

        assert exc_info.value.code == 2
        assert (notes / "inbox" / f"{_FIXED_TIMESTAMP}-001.md").exists()
        assert not (notes / "adopted" / f"{_FIXED_TIMESTAMP}-001.md").exists()

    def test_duplicate_filenames_deduplicated_with_warning(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """同一ファイル名を重複指定した場合、重複除去のうえ警告を出力して1回のみ移動されること。

        重複除去前は`src.rename(dst)`が1回目の成功後に2回目で対象不在となり
        `FileNotFoundError`のTracebackが露出していた（FB7類似見直し対象）。
        """
        notes = _setup_notes(tmp_path)
        _write_tbd_file(notes, f"{_FIXED_TIMESTAMP}-001.md", question="q", answer="はい")
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(git_calls))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(
                ["wi", "adopt", f"{_FIXED_TIMESTAMP}-001.md", f"{_FIXED_TIMESTAMP}-001.md"],
                home=tmp_path,
            )

        assert exc_info.value.code == 0
        assert not (notes / "inbox" / f"{_FIXED_TIMESTAMP}-001.md").exists()
        assert (notes / "adopted" / f"{_FIXED_TIMESTAMP}-001.md").exists()
        stderr = capsys.readouterr().err
        assert "重複が含まれます" in stderr
        commit_calls = [c["cmd"] for c in git_calls if c["cmd"][:2] == ["git", "commit"]]
        assert len(commit_calls) == 1
        assert "chore: process 1 entry (adopted)" in commit_calls[0]


class TestTbdRm:
    """TBD削除の単体テスト。"""

    def test_single_file_removed(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """1件のtb rm実行でinbox配下ファイルが削除されコミットメッセージが正しいこと。"""
        notes = _setup_notes(tmp_path)
        _write_tbd_file(notes, f"{_FIXED_TIMESTAMP}-001.md")
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(git_calls))
        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "rm", f"{_FIXED_TIMESTAMP}-001.md"], home=tmp_path)
        assert exc_info.value.code == 0
        assert not (notes / "inbox" / f"{_FIXED_TIMESTAMP}-001.md").exists()
        commit_cmd = [c["cmd"] for c in git_calls if "commit" in c["cmd"]][0]
        assert "chore: remove 1 entry" in " ".join(commit_cmd)

    def test_note_included_in_commit_message(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """`--note`指定時にcommit messageへ`(理由: <note>)`形式で追記されること。"""
        notes = _setup_notes(tmp_path)
        _write_tbd_file(notes, f"{_FIXED_TIMESTAMP}-001.md")
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(git_calls))
        with pytest.raises(SystemExit) as exc_info:
            atk.main(
                ["wi", "rm", f"{_FIXED_TIMESTAMP}-001.md", "--note", "誤投入"],
                home=tmp_path,
            )
        assert exc_info.value.code == 0
        commit_cmd = [c["cmd"] for c in git_calls if "commit" in c["cmd"]][0]
        assert "(理由: 誤投入)" in " ".join(commit_cmd)

    def test_multiple_files_removed_single_commit(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """複数ファイル指定時に1コミットへまとめて削除されること。"""
        notes = _setup_notes(tmp_path)
        _write_tbd_file(notes, f"{_FIXED_TIMESTAMP}-001.md")
        _write_tbd_file(notes, f"{_FIXED_TIMESTAMP}-002.md")
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(git_calls))
        with pytest.raises(SystemExit) as exc_info:
            atk.main(
                ["wi", "rm", f"{_FIXED_TIMESTAMP}-001.md", f"{_FIXED_TIMESTAMP}-002.md"],
                home=tmp_path,
            )
        assert exc_info.value.code == 0
        commit_cmds = [c["cmd"] for c in git_calls if "commit" in c["cmd"]]
        assert len(commit_cmds) == 1
        assert "chore: remove 2 entries" in " ".join(commit_cmds[0])

    def test_rejects_traversal(
        self,
        tmp_path: pathlib.Path,
    ) -> None:
        """パストラバーサル文字列は削除前検証で拒否されること。"""
        _setup_notes(tmp_path)
        with pytest.raises(SystemExit):
            atk.main(["wi", "rm", "../evil.md"], home=tmp_path)

    def test_missing_file_exits(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """指定ファイルがinbox配下に存在しないときexit 2で終了すること。"""
        _setup_notes(tmp_path)
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))
        with pytest.raises(SystemExit):
            atk.main(
                ["wi", "rm", f"{_FIXED_TIMESTAMP}-999.md"],
                home=tmp_path,
            )

    def test_partial_missing_file_prevents_any_removal(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """複数指定で一部欠損時に既存ファイルも削除されずcommitも発生しないこと。"""
        notes = _setup_notes(tmp_path)
        existing = _write_tbd_file(notes, f"{_FIXED_TIMESTAMP}-001.md")
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(git_calls))
        with pytest.raises(SystemExit):
            atk.main(
                [
                    "wi",
                    "rm",
                    f"{_FIXED_TIMESTAMP}-001.md",
                    f"{_FIXED_TIMESTAMP}-999.md",
                ],
                home=tmp_path,
            )
        assert existing.exists()
        assert not [c["cmd"] for c in git_calls if "commit" in c["cmd"]]

    def test_duplicate_filenames_deduplicated_with_warning(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """同一ファイル名を重複指定した場合、重複除去のうえ警告を出力して1回のみ削除されること。

        重複除去前は`p.unlink()`が1回目の成功後に2回目で対象不在となり
        `FileNotFoundError`のTracebackが露出していた（FB7類似見直し対象）。
        """
        notes = _setup_notes(tmp_path)
        _write_tbd_file(notes, f"{_FIXED_TIMESTAMP}-001.md")
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(git_calls))
        with pytest.raises(SystemExit) as exc_info:
            atk.main(
                ["wi", "rm", f"{_FIXED_TIMESTAMP}-001.md", f"{_FIXED_TIMESTAMP}-001.md"],
                home=tmp_path,
            )
        assert exc_info.value.code == 0
        assert not (notes / "inbox" / f"{_FIXED_TIMESTAMP}-001.md").exists()
        stderr = capsys.readouterr().err
        assert "重複が含まれます" in stderr
        commit_cmd = [c["cmd"] for c in git_calls if "commit" in c["cmd"]][0]
        assert "chore: remove 1 entry" in " ".join(commit_cmd)


def test_answer_tbd_splits_at_last_marker(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """回答欄マーカーが重複するエントリでも最後のマーカー基準で分割し、見出しと質問本文を保全する。"""
    notes = _setup_notes(tmp_path)
    monkeypatch.setattr(tbd_module, "_repo_lock", lambda *_args, **_kwargs: contextlib.nullcontext())
    monkeypatch.setattr(tbd_module, "_pull", lambda _path: None)
    monkeypatch.setattr(tbd_module, "_commit_and_push", lambda *_args, **_kwargs: None)
    path = notes / "inbox" / "20260101-000000-001.md"
    path.write_text(
        "---\ntarget_repo: github.com/example/foo\ntype: uwi\nquestion_type: free-form\n---\n\n"
        f"{tbd_module.QUESTION_HEADING}\n\n"
        f"前半の質問本文。\n\n{tbd_module.ANSWER_MARKER}\n\n"
        f"後半の質問本文。\n\n{tbd_module.ANSWER_HEADING}\n\n{tbd_module.ANSWER_MARKER}\n",
        encoding="utf-8",
    )
    assert tbd_module.answer_tbd(notes, filename=path.name, answer="採用する") is True
    content = path.read_text(encoding="utf-8")
    assert tbd_module.ANSWER_HEADING in content
    assert "前半の質問本文。" in content
    assert "後半の質問本文。" in content
    assert content.rstrip().endswith("採用する")


def test_answer_tbd_keeps_behavior_for_single_marker(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """マーカーが1個の通常データでは従来と同じ結果になる。"""
    notes = _setup_notes(tmp_path)
    monkeypatch.setattr(tbd_module, "_repo_lock", lambda *_args, **_kwargs: contextlib.nullcontext())
    monkeypatch.setattr(tbd_module, "_pull", lambda _path: None)
    monkeypatch.setattr(tbd_module, "_commit_and_push", lambda *_args, **_kwargs: None)
    path = notes / "inbox" / "20260101-000000-002.md"
    path.write_text(
        "---\ntarget_repo: github.com/example/foo\ntype: uwi\nquestion_type: free-form\n---\n\n"
        f"{tbd_module.QUESTION_HEADING}\n\n質問本文。\n\n{tbd_module.ANSWER_HEADING}\n\n{tbd_module.ANSWER_MARKER}\n",
        encoding="utf-8",
    )
    assert tbd_module.answer_tbd(notes, filename=path.name, answer="不採用とする") is True
    content = path.read_text(encoding="utf-8")
    assert content.count(tbd_module.ANSWER_MARKER) == 1
    assert "質問本文。" in content
    assert content.rstrip().endswith("不採用とする")


def test_answer_tbd_rejects_empty_answer_without_changing_existing_answer(
    tmp_path: pathlib.Path,
) -> None:
    """空回答は共有コア入口で拒否し、既存回答を1バイトも変更しない。"""
    notes = _setup_notes(tmp_path)
    path = notes / "inbox" / "20260101-000000-003.md"
    path.write_text(
        "---\ntarget_repo: github.com/example/foo\ntype: uwi\nquestion_type: free-form\n---\n\n"
        f"{tbd_module.QUESTION_HEADING}\n\n質問本文。\n\n"
        f"{tbd_module.ANSWER_HEADING}\n\n{tbd_module.ANSWER_MARKER}\n既存回答\n",
        encoding="utf-8",
    )
    before = path.read_bytes()

    with pytest.raises(tbd_module.WebInputError, match="回答本文が空です"):
        tbd_module.answer_tbd(notes, filename=path.name, answer=" \n\t")

    assert path.read_bytes() == before


def test_answer_tbd_targets_explicit_state_and_keeps_legacy_priority(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """状態指定時は指定側へ回答し、省略時はprocessing優先を維持する。"""
    notes = _setup_notes(tmp_path)
    monkeypatch.setattr(tbd_module, "_repo_lock", lambda *_args, **_kwargs: contextlib.nullcontext())
    monkeypatch.setattr(tbd_module, "_pull", lambda _path: None)
    monkeypatch.setattr(tbd_module, "_commit_and_push", lambda *_args, **_kwargs: None)
    content = (
        "---\ntarget_repo: github.com/example/foo\ntype: uwi\nquestion_type: free-form\n---\n\n"
        f"{tbd_module.QUESTION_HEADING}\n\n質問本文。\n\n"
        f"{tbd_module.ANSWER_HEADING}\n\n{tbd_module.ANSWER_MARKER}\n"
    )
    inbox = notes / "inbox/same.md"
    processing = notes / "processing/same.md"
    processing.parent.mkdir()
    inbox.write_text(content, encoding="utf-8")
    processing.write_text(content, encoding="utf-8")

    assert tbd_module.answer_tbd(notes, filename="same.md", state="inbox", answer="未処理側への回答") is True
    assert inbox.read_text(encoding="utf-8").endswith("未処理側への回答\n")
    assert processing.read_text(encoding="utf-8") == content

    assert tbd_module.answer_tbd(notes, filename="same.md", answer="従来経路の回答") is True
    assert processing.read_text(encoding="utf-8").endswith("従来経路の回答\n")


def test_answer_tbd_accepts_explicit_hold_state(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """hold上のTBDへ明示状態指定で回答する。"""
    notes = _setup_notes(tmp_path)
    held = _write_tbd_file(notes, "held.md")
    held.write_text(held.read_text(encoding="utf-8") + f"{tbd_module.ANSWER_MARKER}\n", encoding="utf-8")
    (notes / "hold").mkdir(exist_ok=True)
    held.rename(notes / "hold/held.md")
    monkeypatch.setattr(tbd_module, "_repo_lock", lambda *_args, **_kwargs: contextlib.nullcontext())
    monkeypatch.setattr(tbd_module, "_pull", lambda _path: None)
    monkeypatch.setattr(tbd_module, "_commit_and_push", lambda *_args, **_kwargs: None)

    assert tbd_module.answer_tbd(notes, filename="held.md", state="hold", answer="回答") is True
    assert (notes / "hold/held.md").read_text(encoding="utf-8").endswith("回答\n")


def test_reject_reserved_tbd_markup_allows_plain_body() -> None:
    """予約書式を含まない本文は拒否しない。"""
    tbd_module.reject_reserved_tbd_markup("判定根拠を示したうえで、どちらの案を採用しますか？")


def test_reject_reserved_tbd_markup_ignores_inline_heading_text() -> None:
    """行頭以外に現れる見出し相当の文字列は拒否対象としない。"""
    tbd_module.reject_reserved_tbd_markup(f"本文中で`{tbd_module.ANSWER_HEADING}`という語に言及するだけの記述は許容しますか？")
