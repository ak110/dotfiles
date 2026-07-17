"""atk (agent-toolkit `atk fb`) のtbd系サブコマンドのテスト。

tbd-add/tbd-list/tbd-edit/tbd-answer/tbd-adopt/tbd-rmサブコマンドの単体テストを集約する。
既存サブコマンドのテストは`atk_test.py`に、拡張サブコマンド・オプションのテストは
`_atk_fb_extras_test.py`に分離する。共通ヘルパーは`atk_test.py`から再利用する。
"""

import pathlib
import subprocess
import sys
from collections.abc import Callable
from typing import Any

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import atk  # noqa: E402  # pylint: disable=wrong-import-position
from atk_test import (  # pylint: disable=wrong-import-position
    _FIXED_DT,
    _FIXED_TIMESTAMP,
    _GitCall,
    _make_subprocess_fake,
    _setup_tbd_env,
    _write_tbd_file,
)  # noqa: E402  # pylint: disable=wrong-import-position


def _make_tbd_add_fake(myrepo: pathlib.Path) -> Callable[..., subprocess.CompletedProcess[Any]]:
    """tbd-add検証用fake_runを生成する。`myrepo`のorigin URLのみ実URLを返し、それ以外は空応答を返す。"""

    def fake_run(cmd: list[str], *_a: object, **kw: object) -> subprocess.CompletedProcess[Any]:
        if cmd == ["git", "-C", str(myrepo), "remote", "get-url", "origin"]:
            stdout: Any = (
                "https://github.com/example/myrepo.git\n" if kw.get("text") else b"https://github.com/example/myrepo.git\n"
            )
            return subprocess.CompletedProcess(cmd, returncode=0, stdout=stdout, stderr="" if kw.get("text") else b"")
        empty: Any = "" if kw.get("text") else b""
        return subprocess.CompletedProcess(cmd, returncode=0, stdout=empty, stderr=empty)

    return fake_run


class TestTbdAdd:
    """tbd-addサブコマンドの基本動作検証。"""

    def test_single_message_generates_one_file(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """単一メッセージで1ファイルが生成され、frontmatter・本文構造が正しい。"""
        notes = _setup_tbd_env(tmp_path)
        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()
        monkeypatch.setattr(subprocess, "run", _make_tbd_add_fake(myrepo))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(
                ["fb", "tbd-add", str(myrepo), "--scope", "theme1", "未確認の挙動"],
                home=tmp_path,
                now=_FIXED_DT,
            )
        assert exc_info.value.code == 0

        files = sorted((notes / "tbd" / "inbox").iterdir())
        assert len(files) == 1
        content = files[0].read_text(encoding="utf-8")
        assert "target_repo: github.com/example/myrepo" in content
        assert "scope: theme1" in content
        assert "question_type: free" in content
        assert "created:" not in content.split("---\n\n", 1)[0]
        assert "## 質問\n\n未確認の挙動" in content
        assert "## 回答" in content

    def test_choice_requires_choices(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """question-type=choice時に--choices未指定でexit 2。"""
        _setup_tbd_env(tmp_path)
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
                ["fb", "tbd-add", str(myrepo), "--question-type", "choice", "q"],
                home=tmp_path,
                now=_FIXED_DT,
            )
        assert exc_info.value.code == 2

    def test_add_without_question_mark_warns(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """問いを含まない本文投入時に警告が標準エラーへ出力され、投入自体は成功する。"""
        _setup_tbd_env(tmp_path)
        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()
        monkeypatch.setattr(subprocess, "run", _make_tbd_add_fake(myrepo))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(
                ["fb", "tbd-add", str(myrepo), "実施報告のみで疑問文を含まない本文"],
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
        _setup_tbd_env(tmp_path)
        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()
        monkeypatch.setattr(subprocess, "run", _make_tbd_add_fake(myrepo))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(
                ["fb", "tbd-add", str(myrepo), "この対応でよいか？"],
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
        _setup_tbd_env(tmp_path)
        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()
        monkeypatch.setattr(subprocess, "run", _make_tbd_add_fake(myrepo))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(
                [
                    "fb",
                    "tbd-add",
                    str(myrepo),
                    "--question-type",
                    "choice",
                    "--choices",
                    "A,B",
                    "実施報告のみで疑問文を含まない選択式本文",
                ],
                home=tmp_path,
                now=_FIXED_DT,
            )
        assert exc_info.value.code == 0
        assert "警告" not in capsys.readouterr().err


class TestTbdAddPullBeforeEditor:
    """tbd-addサブコマンド: `_pull`を`_collect_message_via_editor`より前に呼ぶ順序保証。

    `question_type == "choice" and not args.choices`のバリデーションは`_pull`より前に維持する。
    """

    def test_editor_not_invoked_when_pull_fails(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """messages省略時にpullが失敗した場合、エディターは起動されずユーザー入力消失を予防する（対象リポジトリはcwdから解決）。"""
        notes = _setup_tbd_env(tmp_path)
        monkeypatch.setenv("EDITOR", "fake-editor")
        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()
        editor_calls: list[list[str]] = []

        def fake_run(cmd: list[str], *_a: object, **kw: object) -> subprocess.CompletedProcess[Any]:
            empty: Any = "" if kw.get("text") else b""
            if cmd == ["git", "rev-parse", "--show-toplevel"]:
                stdout: Any = f"{myrepo}\n" if kw.get("text") else f"{myrepo}\n".encode()
                return subprocess.CompletedProcess(cmd, returncode=0, stdout=stdout, stderr=empty)
            if cmd == ["git", "-C", str(myrepo), "remote", "get-url", "origin"]:
                stdout = (
                    "https://github.com/example/myrepo.git\n" if kw.get("text") else b"https://github.com/example/myrepo.git\n"
                )
                return subprocess.CompletedProcess(cmd, returncode=0, stdout=stdout, stderr=empty)
            if cmd[:2] == ["git", "pull"]:
                raise subprocess.CalledProcessError(returncode=1, cmd=cmd)
            if cmd[0] == "fake-editor":
                editor_calls.append(list(cmd))
                return subprocess.CompletedProcess(cmd, returncode=0, stdout=empty, stderr=empty)
            return subprocess.CompletedProcess(cmd, returncode=0, stdout=empty, stderr=empty)

        monkeypatch.setattr(subprocess, "run", fake_run)

        with pytest.raises(subprocess.CalledProcessError):
            atk.main(["fb", "tbd-add"], home=tmp_path, now=_FIXED_DT)

        assert not editor_calls
        assert not list((notes / "tbd" / "inbox").iterdir())

    def test_choice_validation_fires_before_pull(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """--question-type=choiceで--choices未指定の場合、pullを呼ばずexit 2で失敗する。"""
        _setup_tbd_env(tmp_path)
        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()
        git_cmds: list[list[str]] = []

        def fake_run(cmd: list[str], *_a: object, **kw: object) -> subprocess.CompletedProcess[Any]:
            empty: Any = "" if kw.get("text") else b""
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
                ["fb", "tbd-add", str(myrepo), "--question-type", "choice", "q"],
                home=tmp_path,
                now=_FIXED_DT,
            )

        assert exc_info.value.code == 2
        assert not any(c[:2] == ["git", "pull"] for c in git_cmds)


class TestTbdAddRepoPathOverrideCli:
    """`fb tbd-add`のREPO_PATH位置引数廃止に伴うCLI事前変換層の検証。"""

    def test_repo_path_omitted_resolves_from_cwd(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """REPO_PATH省略時、対象リポジトリはカレントディレクトリのgit worktreeから解決される。"""
        notes = _setup_tbd_env(tmp_path)
        cwd_repo = tmp_path / "cwdrepo"
        cwd_repo.mkdir()

        def fake_run(cmd: list[str], *_a: object, **kw: object) -> subprocess.CompletedProcess[Any]:
            empty: Any = "" if kw.get("text") else b""
            if cmd == ["git", "rev-parse", "--show-toplevel"]:
                stdout: Any = f"{cwd_repo}\n" if kw.get("text") else f"{cwd_repo}\n".encode()
                return subprocess.CompletedProcess(cmd, returncode=0, stdout=stdout, stderr=empty)
            if cmd == ["git", "-C", str(cwd_repo), "remote", "get-url", "origin"]:
                stdout = (
                    "https://github.com/example/cwdrepo.git\n"
                    if kw.get("text")
                    else b"https://github.com/example/cwdrepo.git\n"
                )
                return subprocess.CompletedProcess(cmd, returncode=0, stdout=stdout, stderr=empty)
            return subprocess.CompletedProcess(cmd, returncode=0, stdout=empty, stderr=empty)

        monkeypatch.setattr(subprocess, "run", fake_run)

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["fb", "tbd-add", "この対応でよいか"], home=tmp_path, now=_FIXED_DT)

        assert exc_info.value.code == 0
        content = next((notes / "tbd" / "inbox").iterdir()).read_text(encoding="utf-8")
        assert "target_repo: github.com/example/cwdrepo" in content

    def test_message_only_directory_errors(
        self,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """MESSAGE先頭がディレクトリで本文が続かない場合、誤指定としてexit 2になる。"""
        _setup_tbd_env(tmp_path)
        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["fb", "tbd-add", str(myrepo)], home=tmp_path, now=_FIXED_DT)

        assert exc_info.value.code == 2
        captured = capsys.readouterr()
        assert "MESSAGE引数がディレクトリを指しています" in captured.err

    def test_directory_followed_by_message_uses_compat_path(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """MESSAGE先頭が実在ディレクトリで残り本文がある場合、旧REPO_PATH形式として互換動作する。"""
        notes = _setup_tbd_env(tmp_path)
        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()
        monkeypatch.setattr(subprocess, "run", _make_tbd_add_fake(myrepo))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["fb", "tbd-add", str(myrepo), "この対応でよいか"], home=tmp_path, now=_FIXED_DT)

        assert exc_info.value.code == 0
        content = next((notes / "tbd" / "inbox").iterdir()).read_text(encoding="utf-8")
        assert "target_repo: github.com/example/myrepo" in content
        assert "この対応でよいか" in content


class TestTbdAddSourceOption:
    """tbd-addサブコマンド: `--source`指定時にfrontmatterへsource行を記録する。"""

    def test_source_recorded_when_given(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """--source=session-hold指定時、frontmatterにsource: session-holdが含まれる。"""
        notes = _setup_tbd_env(tmp_path)
        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()
        monkeypatch.setattr(subprocess, "run", _make_tbd_add_fake(myrepo))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(
                ["fb", "tbd-add", str(myrepo), "--scope", "hold", "--source", "session-hold", "保留理由"],
                home=tmp_path,
                now=_FIXED_DT,
            )
        assert exc_info.value.code == 0

        files = sorted((notes / "tbd" / "inbox").iterdir())
        content = files[0].read_text(encoding="utf-8")
        assert "source: session-hold" in content

    def test_source_absent_when_not_given(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """--source未指定時、frontmatterにsource行が含まれない。"""
        notes = _setup_tbd_env(tmp_path)
        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()
        monkeypatch.setattr(subprocess, "run", _make_tbd_add_fake(myrepo))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["fb", "tbd-add", str(myrepo), "疑問文を含む質問本文か"], home=tmp_path, now=_FIXED_DT)
        assert exc_info.value.code == 0

        files = sorted((notes / "tbd" / "inbox").iterdir())
        content = files[0].read_text(encoding="utf-8")
        assert "source:" not in content


class TestTbdMutationTargetRepoVerification:
    """tbd-edit/tbd-adopt/tbd-rm: `--target-repo`指定時のfrontmatter一致検証を検証する。

    既定のfrontmatter`target_repo`は`github.com/example/foo`（`_write_tbd_file`既定値）。
    """

    def test_tbd_edit_mismatch_exits_2(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """tbd-edit: `--target-repo`不一致時にexit 2でエディターは起動されない。"""
        notes = _setup_tbd_env(tmp_path)
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
                ["fb", "tbd-edit", f"{_FIXED_TIMESTAMP}-001.md", "--target-repo", "github.com/other/repo"],
                home=tmp_path,
            )

        assert exc_info.value.code == 2
        assert not editor_calls

    def test_tbd_adopt_mismatch_exits_2(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """tbd-adopt: `--target-repo`不一致時にexit 2でファイルは移動されない。"""
        notes = _setup_tbd_env(tmp_path)
        _write_tbd_file(notes, f"{_FIXED_TIMESTAMP}-001.md", question="q", answer="はい")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(
                ["fb", "tbd-adopt", f"{_FIXED_TIMESTAMP}-001.md", "--target-repo", "github.com/other/repo"],
                home=tmp_path,
            )

        assert exc_info.value.code == 2
        assert (notes / "tbd" / "inbox" / f"{_FIXED_TIMESTAMP}-001.md").exists()
        assert not (notes / "tbd" / "adopted" / f"{_FIXED_TIMESTAMP}-001.md").exists()

    def test_tbd_adopt_match_succeeds(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """tbd-adopt: `--target-repo`一致時は通常通りtbd/adopted/へ移動する。"""
        notes = _setup_tbd_env(tmp_path)
        _write_tbd_file(notes, f"{_FIXED_TIMESTAMP}-001.md", question="q", answer="はい")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(
                ["fb", "tbd-adopt", f"{_FIXED_TIMESTAMP}-001.md", "--target-repo", "github.com/example/foo"],
                home=tmp_path,
            )

        assert exc_info.value.code == 0
        assert (notes / "tbd" / "adopted" / f"{_FIXED_TIMESTAMP}-001.md").exists()

    def test_tbd_rm_mismatch_exits_2(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """tbd-rm: `--target-repo`不一致時にexit 2でファイルは削除されない。"""
        notes = _setup_tbd_env(tmp_path)
        _write_tbd_file(notes, f"{_FIXED_TIMESTAMP}-001.md")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(
                ["fb", "tbd-rm", f"{_FIXED_TIMESTAMP}-001.md", "--target-repo", "github.com/other/repo"],
                home=tmp_path,
            )

        assert exc_info.value.code == 2
        assert (notes / "tbd" / "inbox" / f"{_FIXED_TIMESTAMP}-001.md").exists()


class TestTbdList:
    """tbd-listサブコマンドのフィルター動作検証。"""

    def test_status_filter(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """--status=unansweredで未回答のみが1件1行（filename・target_repo・summary）形式で出力される。"""
        notes = _setup_tbd_env(tmp_path)
        _write_tbd_file(notes, f"{_FIXED_TIMESTAMP}-001.md", question="q1", answer="")
        _write_tbd_file(notes, f"{_FIXED_TIMESTAMP}-002.md", question="q2", answer="回答あり\n")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["fb", "tbd-list", "--status", "unanswered"], home=tmp_path)
        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert captured.out == f"# tbd\n{_FIXED_TIMESTAMP}-001.md: github.com/example/foo [unanswered] q1\n"


class TestTbdListSkipPull:
    """tbd-listサブコマンド: --skip-pull指定時はgit pullをスキップする。"""

    def test_skip_pull_omits_git_pull(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """--skip-pull指定時はgit pull --ff-onlyが実行されない。"""
        notes = _setup_tbd_env(tmp_path)
        _write_tbd_file(notes, f"{_FIXED_TIMESTAMP}-001.md", question="q1")
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(git_calls))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["fb", "tbd-list", "--skip-pull"], home=tmp_path)

        assert exc_info.value.code == 0
        assert not any(c["cmd"][:2] == ["git", "pull"] for c in git_calls)


class TestTbdEdit:
    """tbd-editサブコマンドの境界条件検証。"""

    def test_rejects_traversal(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """パストラバーサル系のファイル名でexit 2。"""
        _setup_tbd_env(tmp_path)
        monkeypatch.setenv("EDITOR", "vi")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["fb", "tbd-edit", "../escape.md"], home=tmp_path)
        assert exc_info.value.code == 2

    def test_no_diff_skips_commit(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """編集差分なしの場合はcommit・pushしない。"""
        notes = _setup_tbd_env(tmp_path)
        _write_tbd_file(notes, f"{_FIXED_TIMESTAMP}-001.md", question="q")
        monkeypatch.setenv("EDITOR", "vi")
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(git_calls))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["fb", "tbd-edit", f"{_FIXED_TIMESTAMP}-001.md"], home=tmp_path)
        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "差分なし" in captured.out
        commit_calls = [c for c in git_calls if c["cmd"][:2] == ["git", "commit"]]
        assert commit_calls == []


class TestTbdAnswer:
    """tbd-answerサブコマンドの空集合・差分なし時の挙動検証。"""

    def test_no_unanswered_prints_message(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """未回答ゼロ時は案内のみでcommitしない。"""
        notes = _setup_tbd_env(tmp_path)
        _write_tbd_file(notes, f"{_FIXED_TIMESTAMP}-001.md", question="q", answer="ans\n")
        monkeypatch.setenv("EDITOR", "vi")
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(git_calls))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["fb", "tbd-answer"], home=tmp_path)
        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "未回答のTBDはありません" in captured.out
        commit_calls = [c for c in git_calls if c["cmd"][:2] == ["git", "commit"]]
        assert commit_calls == []


class TestTbdAdopt:
    """tbd-adoptサブコマンド: 採用としてtbd/inboxからtbd/adopted/へ移動しコミットを行う。"""

    def test_single_file_adopted(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """1件のtbd-adopt実行でtbd/inboxから移動されtbd/adopted/に置かれコミットメッセージが正しいこと。"""
        notes = _setup_tbd_env(tmp_path)
        _write_tbd_file(notes, f"{_FIXED_TIMESTAMP}-001.md", question="q", answer="はい")
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(git_calls))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["fb", "tbd-adopt", f"{_FIXED_TIMESTAMP}-001.md"], home=tmp_path)

        assert exc_info.value.code == 0
        assert not (notes / "tbd" / "inbox" / f"{_FIXED_TIMESTAMP}-001.md").exists()
        assert (notes / "tbd" / "adopted" / f"{_FIXED_TIMESTAMP}-001.md").exists()

        commit_cmd = [c["cmd"] for c in git_calls if "commit" in c["cmd"]][0]
        assert "chore: adopt 1 tbd item" in commit_cmd

    def test_stamp_written_with_all_fields(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """--note・--commit指定時、tbd/adopted/配下のファイル末尾に採否・処理日時・対応commit・メモが追記される。"""
        notes = _setup_tbd_env(tmp_path)
        _write_tbd_file(notes, f"{_FIXED_TIMESTAMP}-001.md", question="q", answer="はい")
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(git_calls))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(
                [
                    "fb",
                    "tbd-adopt",
                    f"{_FIXED_TIMESTAMP}-001.md",
                    "--note",
                    "TBD採用メモ",
                    "--commit",
                    "xyz9876",
                ],
                home=tmp_path,
            )

        assert exc_info.value.code == 0
        adopted_text = (notes / "tbd" / "adopted" / f"{_FIXED_TIMESTAMP}-001.md").read_text(encoding="utf-8")
        assert "## 処理結果" in adopted_text
        assert "- 採否: tbd-adopted" in adopted_text
        assert "- 処理日時: " in adopted_text
        assert "- 対応commit: xyz9876" in adopted_text
        assert "- メモ: TBD採用メモ" in adopted_text

    def test_multiple_files_adopted_single_commit(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """3件のtbd-adoptで全件がtbd/adopted/へ移動し単一コミットが行われること。"""
        notes = _setup_tbd_env(tmp_path)
        _write_tbd_file(notes, f"{_FIXED_TIMESTAMP}-001.md", question="q1", answer="a1")
        _write_tbd_file(notes, f"{_FIXED_TIMESTAMP}-002.md", question="q2", answer="a2")
        _write_tbd_file(notes, f"{_FIXED_TIMESTAMP}-003.md", question="q3", answer="a3")
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(git_calls))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(
                [
                    "fb",
                    "tbd-adopt",
                    f"{_FIXED_TIMESTAMP}-001.md",
                    f"{_FIXED_TIMESTAMP}-002.md",
                    f"{_FIXED_TIMESTAMP}-003.md",
                ],
                home=tmp_path,
            )

        assert exc_info.value.code == 0
        for name in (f"{_FIXED_TIMESTAMP}-001.md", f"{_FIXED_TIMESTAMP}-002.md", f"{_FIXED_TIMESTAMP}-003.md"):
            assert not (notes / "tbd" / "inbox" / name).exists()
            assert (notes / "tbd" / "adopted" / name).exists()

        commit_calls = [c["cmd"] for c in git_calls if c["cmd"][:2] == ["git", "commit"]]
        assert len(commit_calls) == 1
        assert "chore: adopt 3 tbd items" in commit_calls[0]

    def test_pushes_after_commit(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """tbd-adopt実行後にgit pushが行われること。"""
        notes = _setup_tbd_env(tmp_path)
        _write_tbd_file(notes, f"{_FIXED_TIMESTAMP}-001.md", question="q", answer="はい")
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(git_calls))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["fb", "tbd-adopt", f"{_FIXED_TIMESTAMP}-001.md"], home=tmp_path)

        assert exc_info.value.code == 0
        assert any(c["cmd"] == ["git", "push"] for c in git_calls)

    def test_rejects_traversal(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """パストラバーサル系のファイル名でexit 2。"""
        _setup_tbd_env(tmp_path)
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["fb", "tbd-adopt", "../escape.md"], home=tmp_path)

        assert exc_info.value.code == 2
        captured = capsys.readouterr()
        assert "不正なファイル名" in captured.err or "基準ディレクトリ外" in captured.err

    def test_missing_file_exits(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """tbd/inboxに存在しないファイル名指定でexit 2と案内が出力される。"""
        _setup_tbd_env(tmp_path)
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["fb", "tbd-adopt", "nonexistent.md"], home=tmp_path)

        assert exc_info.value.code == 2
        captured = capsys.readouterr()
        assert "tbd/inboxに存在しません" in captured.err

    def test_partial_missing_file_prevents_any_move(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """複数ファイル指定時、一部が未存在ならどのファイルも移動されない（部分移動防止）。"""
        notes = _setup_tbd_env(tmp_path)
        _write_tbd_file(notes, f"{_FIXED_TIMESTAMP}-001.md", question="q1", answer="a1")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(
                ["fb", "tbd-adopt", f"{_FIXED_TIMESTAMP}-001.md", "nonexistent.md"],
                home=tmp_path,
            )

        assert exc_info.value.code == 2
        assert (notes / "tbd" / "inbox" / f"{_FIXED_TIMESTAMP}-001.md").exists()
        assert not (notes / "tbd" / "adopted" / f"{_FIXED_TIMESTAMP}-001.md").exists()


class TestTbdRm:
    """tbd-rmサブコマンドの単体テスト。"""

    def test_single_file_removed(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """1件のtbd-rm実行でinbox配下ファイルが削除されコミットメッセージが正しいこと。"""
        notes = _setup_tbd_env(tmp_path)
        _write_tbd_file(notes, f"{_FIXED_TIMESTAMP}-001.md")
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(git_calls))
        with pytest.raises(SystemExit) as exc_info:
            atk.main(["fb", "tbd-rm", f"{_FIXED_TIMESTAMP}-001.md"], home=tmp_path)
        assert exc_info.value.code == 0
        assert not (notes / "tbd" / "inbox" / f"{_FIXED_TIMESTAMP}-001.md").exists()
        commit_cmd = [c["cmd"] for c in git_calls if "commit" in c["cmd"]][0]
        assert "chore: remove 1 tbd item" in " ".join(commit_cmd)

    def test_note_included_in_commit_message(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """`--note`指定時にcommit messageへ`(理由: <note>)`形式で追記されること。"""
        notes = _setup_tbd_env(tmp_path)
        _write_tbd_file(notes, f"{_FIXED_TIMESTAMP}-001.md")
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(git_calls))
        with pytest.raises(SystemExit) as exc_info:
            atk.main(
                ["fb", "tbd-rm", f"{_FIXED_TIMESTAMP}-001.md", "--note", "誤投入"],
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
        notes = _setup_tbd_env(tmp_path)
        _write_tbd_file(notes, f"{_FIXED_TIMESTAMP}-001.md")
        _write_tbd_file(notes, f"{_FIXED_TIMESTAMP}-002.md")
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(git_calls))
        with pytest.raises(SystemExit) as exc_info:
            atk.main(
                ["fb", "tbd-rm", f"{_FIXED_TIMESTAMP}-001.md", f"{_FIXED_TIMESTAMP}-002.md"],
                home=tmp_path,
            )
        assert exc_info.value.code == 0
        commit_cmds = [c["cmd"] for c in git_calls if "commit" in c["cmd"]]
        assert len(commit_cmds) == 1
        assert "chore: remove 2 tbd items" in " ".join(commit_cmds[0])

    def test_rejects_traversal(
        self,
        tmp_path: pathlib.Path,
    ) -> None:
        """パストラバーサル文字列は削除前検証で拒否されること。"""
        _setup_tbd_env(tmp_path)
        with pytest.raises(SystemExit):
            atk.main(["fb", "tbd-rm", "../evil.md"], home=tmp_path)

    def test_missing_file_exits(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """指定ファイルがinbox配下に存在しないときexit 2で終了すること。"""
        _setup_tbd_env(tmp_path)
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))
        with pytest.raises(SystemExit):
            atk.main(
                ["fb", "tbd-rm", f"{_FIXED_TIMESTAMP}-999.md"],
                home=tmp_path,
            )

    def test_partial_missing_file_prevents_any_removal(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """複数指定で一部欠損時に既存ファイルも削除されずcommitも発生しないこと。"""
        notes = _setup_tbd_env(tmp_path)
        existing = _write_tbd_file(notes, f"{_FIXED_TIMESTAMP}-001.md")
        git_calls: list[_GitCall] = []
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake(git_calls))
        with pytest.raises(SystemExit):
            atk.main(
                [
                    "fb",
                    "tbd-rm",
                    f"{_FIXED_TIMESTAMP}-001.md",
                    f"{_FIXED_TIMESTAMP}-999.md",
                ],
                home=tmp_path,
            )
        assert existing.exists()
        assert not [c["cmd"] for c in git_calls if "commit" in c["cmd"]]
