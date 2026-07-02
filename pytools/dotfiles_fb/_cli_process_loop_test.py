"""pytools.dotfiles_fb._cli のprocess-loopサブコマンド・リポジトリID解決のテスト。

process-loopサブコマンド、リモートURL正規化（`_normalize_remote_url`）、
リポジトリID解決（`_resolve_repo_id`）の単体テストを集約する。
既存サブコマンドの残テストは`_cli_test.py`に、他サブコマンドの分割先は`_cli_show_test.py`・
`_cli_mutations_test.py`に分離する。共通ヘルパーは`_cli_test.py`から再利用する。
"""

import pathlib
import subprocess
from typing import Any

import pytest

from pytools.dotfiles_fb import _cli, _repo
from pytools.dotfiles_fb._cli_test import (
    _setup_flag_and_notes,
    _write_feedback_file,
)


class TestProcessLoopEmptyInbox:
    """process-loopサブコマンド: 対象リポジトリのinbox空時はclaude未起動でexit 0。"""

    def test_empty_inbox_skips_claude(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """対象リポジトリのinboxが最初から空ならclaudeが一度も呼ばれない。"""
        _setup_flag_and_notes(tmp_path)
        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()
        claude_called = False

        def fake_run(cmd: list[str], *_args: object, **kwargs: object) -> subprocess.CompletedProcess[Any]:
            nonlocal claude_called
            if cmd == ["git", "-C", str(myrepo), "remote", "get-url", "origin"]:
                stdout: Any = (
                    "https://github.com/example/foo.git\n" if kwargs.get("text") else b"https://github.com/example/foo.git\n"
                )
                return subprocess.CompletedProcess(cmd, returncode=0, stdout=stdout, stderr="" if kwargs.get("text") else b"")
            if cmd[:1] == ["claude"]:
                claude_called = True
            empty: Any = "" if kwargs.get("text") else b""
            return subprocess.CompletedProcess(cmd, returncode=0, stdout=empty, stderr=empty)

        monkeypatch.setattr(subprocess, "run", fake_run)

        with pytest.raises(SystemExit) as exc_info:
            _cli.main(["process-loop", f"--target-repo={myrepo}"], home=tmp_path)

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "対象リポジトリのinboxは空です" in captured.out
        assert not claude_called


class TestProcessLoopSingleIteration:
    """process-loopサブコマンド: claude起動で対象リポジトリのinboxが空になれば1回で終了。"""

    def test_single_iteration_when_inbox_drains(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """claudeのfakeが対象リポジトリのinboxを空にすると1回の反復でループを抜ける。"""
        notes = _setup_flag_and_notes(tmp_path)
        inbox_path = _write_feedback_file(notes, "fb-001.md", target_repo="github.com/example/foo")
        claude_calls: list[list[str]] = []

        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()
        expected_local_path = str(myrepo.resolve())

        def fake_run(cmd: list[str], *_args: object, **kwargs: object) -> subprocess.CompletedProcess[Any]:
            if cmd == ["git", "-C", str(myrepo), "remote", "get-url", "origin"]:
                stdout: Any = (
                    "https://github.com/example/foo.git\n" if kwargs.get("text") else b"https://github.com/example/foo.git\n"
                )
                return subprocess.CompletedProcess(cmd, returncode=0, stdout=stdout, stderr="" if kwargs.get("text") else b"")
            if cmd[:1] == ["claude"]:
                claude_calls.append(list(cmd))
                inbox_path.unlink()
            empty: Any = "" if kwargs.get("text") else b""
            return subprocess.CompletedProcess(cmd, returncode=0, stdout=empty, stderr=empty)

        monkeypatch.setattr(subprocess, "run", fake_run)

        with pytest.raises(SystemExit) as exc_info:
            _cli.main(["process-loop", f"--target-repo={myrepo}"], home=tmp_path)

        assert exc_info.value.code == 0
        assert claude_calls == [["claude", "--permission-mode=auto", "/process-feedbacks", expected_local_path]]
        captured = capsys.readouterr()
        assert "[反復 1] 対象リポジトリのinbox残1件" in captured.out
        assert "対象リポジトリのinboxが空になりました（1回実行" in captured.out


class TestProcessLoopMaxIterations:
    """process-loopサブコマンド: --max-iterationsで反復上限を強制。"""

    def test_max_iterations_caps_loop(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """claudeが対象リポジトリのinboxを空にしなくても上限回数で停止する。"""
        notes = _setup_flag_and_notes(tmp_path)
        _write_feedback_file(notes, "fb-001.md", target_repo="github.com/example/foo")
        claude_calls: list[list[str]] = []

        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()

        def fake_run(cmd: list[str], *_args: object, **kwargs: object) -> subprocess.CompletedProcess[Any]:
            if cmd == ["git", "-C", str(myrepo), "remote", "get-url", "origin"]:
                stdout: Any = (
                    "https://github.com/example/foo.git\n" if kwargs.get("text") else b"https://github.com/example/foo.git\n"
                )
                return subprocess.CompletedProcess(cmd, returncode=0, stdout=stdout, stderr="" if kwargs.get("text") else b"")
            if cmd[:1] == ["claude"]:
                claude_calls.append(list(cmd))
            empty: Any = "" if kwargs.get("text") else b""
            return subprocess.CompletedProcess(cmd, returncode=0, stdout=empty, stderr=empty)

        monkeypatch.setattr(subprocess, "run", fake_run)

        with pytest.raises(SystemExit) as exc_info:
            _cli.main(["process-loop", "--max-iterations=2", f"--target-repo={myrepo}"], home=tmp_path)

        assert exc_info.value.code == 0
        assert len(claude_calls) == 2
        captured = capsys.readouterr()
        assert "反復上限2回に達しました（対象リポジトリのinbox残1件）" in captured.out


class TestProcessLoopClaudeFailure:
    """process-loopサブコマンド: claude非0終了時に同じexit codeで中断する。"""

    def test_claude_failure_exits_with_returncode(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """claudeのfakeが非0を返すとprocess-loopは同じexit codeで停止する。"""
        notes = _setup_flag_and_notes(tmp_path)
        _write_feedback_file(notes, "fb-001.md", target_repo="github.com/example/foo")

        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()

        def fake_run(cmd: list[str], *_args: object, **kwargs: object) -> subprocess.CompletedProcess[Any]:
            if cmd == ["git", "-C", str(myrepo), "remote", "get-url", "origin"]:
                stdout: Any = (
                    "https://github.com/example/foo.git\n" if kwargs.get("text") else b"https://github.com/example/foo.git\n"
                )
                return subprocess.CompletedProcess(cmd, returncode=0, stdout=stdout, stderr="" if kwargs.get("text") else b"")
            if cmd[:1] == ["claude"]:
                return subprocess.CompletedProcess(cmd, returncode=42, stdout=b"", stderr=b"")
            empty: Any = "" if kwargs.get("text") else b""
            return subprocess.CompletedProcess(cmd, returncode=0, stdout=empty, stderr=empty)

        monkeypatch.setattr(subprocess, "run", fake_run)

        with pytest.raises(SystemExit) as exc_info:
            _cli.main(["process-loop", f"--target-repo={myrepo}"], home=tmp_path)

        assert exc_info.value.code == 42
        captured = capsys.readouterr()
        assert "claudeがexit code 42で終了しました" in captured.err


class TestProcessLoopTargetRepoFilter:
    """process-loopサブコマンド: --target-repoで対象外フィードバックは件数に含めない。"""

    def test_target_repo_filters_count(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """対象外リポジトリのフィードバックは件数判定から除外され、claudeは起動しない。"""
        notes = _setup_flag_and_notes(tmp_path)
        _write_feedback_file(notes, "fb-other.md", target_repo="github.com/example/other")
        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()
        claude_called = False

        def fake_run(cmd: list[str], *_args: object, **kwargs: object) -> subprocess.CompletedProcess[Any]:
            nonlocal claude_called
            if cmd == ["git", "-C", str(myrepo), "remote", "get-url", "origin"]:
                stdout: Any = (
                    "https://github.com/example/foo.git\n" if kwargs.get("text") else b"https://github.com/example/foo.git\n"
                )
                return subprocess.CompletedProcess(cmd, returncode=0, stdout=stdout, stderr="" if kwargs.get("text") else b"")
            if cmd[:1] == ["claude"]:
                claude_called = True
            empty: Any = "" if kwargs.get("text") else b""
            return subprocess.CompletedProcess(cmd, returncode=0, stdout=empty, stderr=empty)

        monkeypatch.setattr(subprocess, "run", fake_run)

        with pytest.raises(SystemExit) as exc_info:
            _cli.main(["process-loop", f"--target-repo={myrepo}"], home=tmp_path)

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "対象リポジトリのinboxは空です" in captured.out
        assert not claude_called


class TestProcessLoopDefaultUsesGitToplevel:
    """process-loopサブコマンド: --target-repo未指定時はgit rev-parseで現リポジトリを取得する。"""

    def test_default_target_repo_resolved_via_git(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """--target-repo未指定なら`git rev-parse --show-toplevel`を呼び、リモートURLで件数判定し結果をclaude引数に渡す。"""
        notes = _setup_flag_and_notes(tmp_path)
        autorepo = tmp_path / "autorepo"
        autorepo.mkdir()
        expected_local_path = str(autorepo.resolve())
        inbox_path = _write_feedback_file(notes, "fb-001.md", target_repo="github.com/example/auto")
        claude_calls: list[list[str]] = []

        def fake_run(cmd: list[str], *_args: object, **kwargs: object) -> subprocess.CompletedProcess[Any]:
            if cmd == ["git", "rev-parse", "--show-toplevel"]:
                stdout: Any = f"{autorepo}\n" if kwargs.get("text") else f"{autorepo}\n".encode()
                stderr: Any = "" if kwargs.get("text") else b""
                return subprocess.CompletedProcess(cmd, returncode=0, stdout=stdout, stderr=stderr)
            if cmd == ["git", "-C", str(autorepo), "remote", "get-url", "origin"]:
                stdout = (
                    "https://github.com/example/auto.git\n" if kwargs.get("text") else b"https://github.com/example/auto.git\n"
                )
                return subprocess.CompletedProcess(cmd, returncode=0, stdout=stdout, stderr="" if kwargs.get("text") else b"")
            if cmd[:1] == ["claude"]:
                claude_calls.append(list(cmd))
                inbox_path.unlink()
            empty: Any = "" if kwargs.get("text") else b""
            return subprocess.CompletedProcess(cmd, returncode=0, stdout=empty, stderr=empty)

        monkeypatch.setattr(subprocess, "run", fake_run)

        with pytest.raises(SystemExit) as exc_info:
            _cli.main(["process-loop"], home=tmp_path)

        assert exc_info.value.code == 0
        assert claude_calls == [["claude", "--permission-mode=auto", "/process-feedbacks", expected_local_path]]


class TestNormalizeRemoteUrl:
    """_normalize_remote_url: 各種リモートURL形式を`host/owner/repo`へ正規化する。"""

    @pytest.mark.parametrize(
        ("url", "expected"),
        [
            # HTTPS（.gitサフィックスあり）
            ("https://github.com/owner/repo.git", "github.com/owner/repo"),
            # HTTPS（.gitサフィックスなし）
            ("https://github.com/owner/repo", "github.com/owner/repo"),
            # HTTPS（大文字ホスト → 小文字正規化）
            ("https://GitHub.com/Owner/Repo.git", "github.com/owner/repo"),
            # SSH短縮形
            ("git@github.com:owner/repo.git", "github.com/owner/repo"),
            # SSH URI（ssh://スキーム）
            ("ssh://git@github.com/owner/repo.git", "github.com/owner/repo"),
            # 既に正規化済み
            ("github.com/owner/repo", "github.com/owner/repo"),
        ],
    )
    def test_normalize_returns_expected(self, url: str, expected: str) -> None:
        """各URLフォーマットが期待する`host/owner/repo`形式へ変換されること。"""
        assert _repo._normalize_remote_url(url) == expected  # pylint: disable=protected-access  # noqa: SLF001

    def test_invalid_url_raises_value_error(self) -> None:
        """解析不能な文字列はValueErrorを送出すること。"""
        with pytest.raises(ValueError, match="リモートURLとして解析できません"):
            _repo._normalize_remote_url("not-a-url")  # pylint: disable=protected-access  # noqa: SLF001


class TestResolveRepoId:
    """_resolve_repo_id: URL・ローカルパス・Noneの各入力からリポジトリIDを取得する。"""

    def test_url_input_resolved_directly(self) -> None:
        """URL形式の入力はgit呼び出しなしで正規化されること。"""
        result = _repo._resolve_repo_id("https://github.com/owner/repo.git")  # pylint: disable=protected-access  # noqa: SLF001
        assert result == "github.com/owner/repo"

    def test_normalized_url_input_resolved_directly(self) -> None:
        """`host/owner/repo`形式の入力はgit呼び出しなしで正規化されること。"""
        result = _repo._resolve_repo_id("github.com/owner/repo")  # pylint: disable=protected-access  # noqa: SLF001
        assert result == "github.com/owner/repo"

    def test_local_path_resolved_via_git(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """ローカルパスはgit remote get-urlでURLを取得して正規化されること。"""
        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()

        def fake_run(cmd: list[str], *_args: object, **kwargs: object) -> subprocess.CompletedProcess[Any]:
            if cmd == ["git", "-C", str(myrepo), "remote", "get-url", "origin"]:
                stdout: Any = "git@github.com:owner/repo.git\n" if kwargs.get("text") else b"git@github.com:owner/repo.git\n"
                return subprocess.CompletedProcess(cmd, returncode=0, stdout=stdout, stderr="" if kwargs.get("text") else b"")
            empty: Any = "" if kwargs.get("text") else b""
            return subprocess.CompletedProcess(cmd, returncode=0, stdout=empty, stderr=empty)

        monkeypatch.setattr(subprocess, "run", fake_run)
        result = _repo._resolve_repo_id(str(myrepo))  # pylint: disable=protected-access  # noqa: SLF001
        assert result == "github.com/owner/repo"

    def test_none_resolved_from_cwd_via_git(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """Noneはgit rev-parseとgit remote get-urlでCWDのリモートURLを取得すること。"""
        myrepo = tmp_path / "cwdrepo"
        myrepo.mkdir()

        def fake_run(cmd: list[str], *_args: object, **kwargs: object) -> subprocess.CompletedProcess[Any]:
            if cmd == ["git", "rev-parse", "--show-toplevel"]:
                stdout: Any = f"{myrepo}\n" if kwargs.get("text") else f"{myrepo}\n".encode()
                return subprocess.CompletedProcess(cmd, returncode=0, stdout=stdout, stderr="" if kwargs.get("text") else b"")
            if cmd == ["git", "-C", str(myrepo), "remote", "get-url", "origin"]:
                stdout = "https://github.com/cwd/repo\n" if kwargs.get("text") else b"https://github.com/cwd/repo\n"
                return subprocess.CompletedProcess(cmd, returncode=0, stdout=stdout, stderr="" if kwargs.get("text") else b"")
            empty: Any = "" if kwargs.get("text") else b""
            return subprocess.CompletedProcess(cmd, returncode=0, stdout=empty, stderr=empty)

        monkeypatch.setattr(subprocess, "run", fake_run)
        result = _repo._resolve_repo_id(None)  # pylint: disable=protected-access  # noqa: SLF001
        assert result == "github.com/cwd/repo"

    def test_local_path_git_remote_failure_exits(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """ローカルパスが存在するがgit remote get-urlが失敗するとexit 2すること。"""
        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()

        def fake_run(cmd: list[str], *_args: object, **kwargs: object) -> subprocess.CompletedProcess[Any]:
            if cmd == ["git", "-C", str(myrepo.resolve()), "remote", "get-url", "origin"]:
                empty: Any = "" if kwargs.get("text") else b""
                return subprocess.CompletedProcess(cmd, returncode=128, stdout=empty, stderr=empty)
            empty = "" if kwargs.get("text") else b""
            return subprocess.CompletedProcess(cmd, returncode=0, stdout=empty, stderr=empty)

        monkeypatch.setattr(subprocess, "run", fake_run)
        with pytest.raises(SystemExit) as exc_info:
            _repo._resolve_repo_id(str(myrepo))  # pylint: disable=protected-access  # noqa: SLF001
        assert exc_info.value.code == 2

    def test_none_git_rev_parse_failure_exits(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """value=Noneのとき、git rev-parseが失敗するとexit 2すること。"""

        def fake_run(cmd: list[str], *_args: object, **kwargs: object) -> subprocess.CompletedProcess[Any]:
            if cmd == ["git", "rev-parse", "--show-toplevel"]:
                empty: Any = "" if kwargs.get("text") else b""
                return subprocess.CompletedProcess(cmd, returncode=128, stdout=empty, stderr=empty)
            empty = "" if kwargs.get("text") else b""
            return subprocess.CompletedProcess(cmd, returncode=0, stdout=empty, stderr=empty)

        monkeypatch.setattr(subprocess, "run", fake_run)
        with pytest.raises(SystemExit) as exc_info:
            _repo._resolve_repo_id(None)  # pylint: disable=protected-access  # noqa: SLF001
        assert exc_info.value.code == 2

    def test_none_git_remote_failure_exits(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """value=Noneのとき、rev-parseは成功するがgit remote get-urlが失敗するとexit 2すること。"""
        myrepo = tmp_path / "cwdrepo"
        myrepo.mkdir()

        def fake_run(cmd: list[str], *_args: object, **kwargs: object) -> subprocess.CompletedProcess[Any]:
            if cmd == ["git", "rev-parse", "--show-toplevel"]:
                stdout: Any = f"{myrepo}\n" if kwargs.get("text") else f"{myrepo}\n".encode()
                return subprocess.CompletedProcess(cmd, returncode=0, stdout=stdout, stderr="" if kwargs.get("text") else b"")
            if cmd == ["git", "-C", str(myrepo), "remote", "get-url", "origin"]:
                empty: Any = "" if kwargs.get("text") else b""
                return subprocess.CompletedProcess(cmd, returncode=128, stdout=empty, stderr=empty)
            empty = "" if kwargs.get("text") else b""
            return subprocess.CompletedProcess(cmd, returncode=0, stdout=empty, stderr=empty)

        monkeypatch.setattr(subprocess, "run", fake_run)
        with pytest.raises(SystemExit) as exc_info:
            _repo._resolve_repo_id(None)  # pylint: disable=protected-access  # noqa: SLF001
        assert exc_info.value.code == 2


class TestProcessLoopUrlInput:
    """process-loop: --target-repoにURLを渡した場合はexit 2すること。"""

    def test_url_input_exits_with_code_2(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """--target-repoにURL文字列（存在しないパス）を渡すとexit 2すること。

        _resolve_local_worktreeは実在しないパスをURL/不正パスとして判別し、
        ローカルパスが必要な旨をstderrへ出力してexit 2する。
        """
        _setup_flag_and_notes(tmp_path)

        monkeypatch.setattr(subprocess, "run", lambda *_a, **_kw: subprocess.CompletedProcess([], 0, "", ""))

        with pytest.raises(SystemExit) as exc_info:
            _cli.main(["process-loop", "--target-repo", "github.com/example/foo"], home=tmp_path)
        assert exc_info.value.code == 2
