"""`atk wi process-loop`の常駐ループの制御（処理対象の件数、対象リポジトリの解決、URL形式の入力）のテスト。"""

import pathlib
import subprocess
from typing import Any, NoReturn

import pytest

from agent_toolkit import atk
from agent_toolkit._atk.wi import process_loop as _process_loop
from agent_toolkit._atk.wi import process_loop_session as _pl_session
from agent_toolkit._atk.wi import process_loop_watch as _pl_watch
from agent_toolkit._atk.wi import process_loop_worktree as _pl_worktree
from agent_toolkit._atk.wi import readiness as _wi_readiness
from agent_toolkit._atk.wi import repo as _repo
from agent_toolkit._testing.process_loop_support import fake_run_with_remote_url, hook_debug_log, isolate_process_loop_commands
from agent_toolkit.atk_test import _setup_notes


@pytest.fixture(autouse=True)
def _isolate_process_loop_commands(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """外部コマンド・Claude設定・初期値のTTLをユーザー環境から分離する。"""
    isolate_process_loop_commands(monkeypatch, tmp_path)


class TestProcessLoopIncludesProcessingInCount:
    """`process-loop`がAWIの`inbox`・`processing`双方を検知件数に含めることを公開CLI経由で検証する。"""

    def test_inbox_and_processing_entries_are_both_counted(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """inbox・processing双方に`.md`を配置した状態でprocess-loopを起動し、
        検知メッセージ`{count}件のAWI/回答済みUWIを検知`の件数が合算値になること。
        """
        _setup_notes(tmp_path)
        private_notes = tmp_path / "private-notes"
        inbox_dir = private_notes / "inbox"
        processing_dir = private_notes / "processing"
        processing_dir.mkdir(parents=True)
        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()
        # `fake_run_with_remote_url`が返す正規化後IDと一致させる。
        target_repo_id = "github.com/example/myrepo"
        (inbox_dir / "a.md").write_text(
            f"---\ntarget_repo: {target_repo_id}\ntype: awi\n---\n\n本文A\n",
            encoding="utf-8",
        )
        (processing_dir / "b.md").write_text(
            f"---\ntarget_repo: {target_repo_id}\ntype: awi\n---\n\n本文B\n",
            encoding="utf-8",
        )

        base_fake_run = fake_run_with_remote_url(myrepo, [], 0)

        def fake_run(cmd: list[str], *_args: object, **kwargs: object) -> subprocess.CompletedProcess[Any]:
            # claude実行を模したのちファイルを削除し、次反復で件数0とすることで
            # `_wait_for_changes`を呼び出してループを終了させる。
            if cmd[:1] == ["claude"]:
                (inbox_dir / "a.md").unlink(missing_ok=True)
                (processing_dir / "b.md").unlink(missing_ok=True)
            return base_fake_run(cmd, *_args, **kwargs)

        monkeypatch.setattr(subprocess, "run", fake_run)

        def fake_wait(*_a: object, **_kw: object) -> None:
            raise KeyboardInterrupt

        monkeypatch.setattr(_pl_watch, "wait_for_changes", fake_wait)

        with pytest.raises(SystemExit):
            atk.main(
                ["wi", "process-loop", "--target-repo", str(myrepo), "--no-update"],
                home=tmp_path,
            )

        captured = capsys.readouterr()
        assert "2件のAWI/回答済みUWIを検知" in captured.out

    def test_cooldown_only_waits_without_starting_child_cli(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """期限待ちだけなら子CLIを起動せず変更待機へ進む。"""
        notes = _setup_notes(tmp_path)
        inbox = notes / "inbox"
        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()
        target_repo = "github.com/example/myrepo"
        (inbox / "cooldown.md").write_text(
            f"---\ntarget_repo: {target_repo}\ntype: awi\ncooldown_until: '2999-01-01T00:00:00+00:00'\n---\n\n本文\n",
            encoding="utf-8",
        )
        child_calls: list[dict[str, Any]] = []
        monkeypatch.setattr(subprocess, "run", fake_run_with_remote_url(myrepo, child_calls, 0))

        def stop_wait(*_args: object, **_kwargs: object) -> NoReturn:
            raise KeyboardInterrupt

        monkeypatch.setattr(_pl_watch, "wait_for_changes", stop_wait)

        with pytest.raises(SystemExit) as exc_info:
            atk.main(
                ["wi", "process-loop", "--target-repo", str(myrepo), "--no-update", "--no-alerts"],
                home=tmp_path,
            )

        assert exc_info.value.code == 0
        assert not child_calls

    def test_ready_entry_beside_cooldown_starts_child_cli(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """通常ready項目が混在すれば期限待ちを除外したまま子CLIを起動する。"""
        notes = _setup_notes(tmp_path)
        inbox = notes / "inbox"
        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()
        target_repo = "github.com/example/myrepo"
        (inbox / "cooldown.md").write_text(
            f"---\ntarget_repo: {target_repo}\ntype: awi\ncooldown_until: '2999-01-01T00:00:00+00:00'\n---\n\n本文\n",
            encoding="utf-8",
        )

        ready = inbox / "ready.md"
        ready.write_text(
            f"---\ntarget_repo: {target_repo}\ntype: awi\n---\n\n本文\n",
            encoding="utf-8",
        )
        child_calls: list[dict[str, Any]] = []
        base_fake_run = fake_run_with_remote_url(myrepo, child_calls, 0)

        def fake_run(cmd: list[str], *_args: object, **kwargs: object) -> subprocess.CompletedProcess[Any]:
            result = base_fake_run(cmd, *_args, **kwargs)
            if cmd[:1] == ["claude"] and "-p" not in cmd:
                ready.unlink()
            return result

        monkeypatch.setattr(subprocess, "run", fake_run)

        def stop_wait(*_args: object, **_kwargs: object) -> NoReturn:
            raise KeyboardInterrupt

        monkeypatch.setattr(_pl_watch, "wait_for_changes", stop_wait)

        with pytest.raises(SystemExit) as exc_info:
            atk.main(
                ["wi", "process-loop", "--target-repo", str(myrepo), "--no-update", "--no-alerts"],
                home=tmp_path,
            )

        assert exc_info.value.code == 0
        assert len(child_calls) == 1


@pytest.mark.parametrize("deprecated_option", ["--orchestrator=claude", "--model=opus"])
def test_process_loop_rejects_removed_options(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    deprecated_option: str,
) -> None:
    """廃止したオーケストレーター・モデル指定を公開CLI境界でexit 2にする。"""
    handler_calls: list[object] = []
    monkeypatch.setattr(_process_loop, "cmd_process_loop", lambda *_args: handler_calls.append(True))
    with pytest.raises(SystemExit) as exc_info:
        atk.main(["wi", "process-loop", deprecated_option], home=tmp_path)
    assert exc_info.value.code == 2
    assert not handler_calls


class TestResolveRepoId:
    """_resolve_repo_id: URL・ローカルパス・Noneの各入力からリポジトリIDを取得する。"""

    def test_url_input_resolved_directly(self) -> None:
        """URL形式の入力はgit呼び出しなしで正規化されること。"""
        result = _repo.resolve_repo_id(  # pylint: disable=protected-access  # noqa: SLF001
            "https://github.com/owner/repo.git",
        )
        assert result == "github.com/owner/repo"

    def test_normalized_url_input_resolved_directly(self) -> None:
        """`host/owner/repo`形式の入力はgit呼び出しなしで正規化されること。"""
        result = _repo.resolve_repo_id("github.com/owner/repo")  # pylint: disable=protected-access  # noqa: SLF001
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
        result = _repo.resolve_repo_id(str(myrepo))  # pylint: disable=protected-access  # noqa: SLF001
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
        result = _repo.resolve_repo_id(None)  # pylint: disable=protected-access  # noqa: SLF001
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
            _repo.resolve_repo_id(str(myrepo))  # pylint: disable=protected-access  # noqa: SLF001
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
            _repo.resolve_repo_id(None)  # pylint: disable=protected-access  # noqa: SLF001
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
            _repo.resolve_repo_id(None)  # pylint: disable=protected-access  # noqa: SLF001
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
        ローカルパスを指定する必要があるとstderrへ出力してexit 2する。
        """
        _setup_notes(tmp_path)

        monkeypatch.setattr(subprocess, "run", lambda *_a, **_kw: subprocess.CompletedProcess([], 0, "", ""))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "process-loop", "--target-repo", "github.com/example/foo"], home=tmp_path)
        assert exc_info.value.code == 2

    def test_prompt_keeps_dotfiles_goal_free_of_internal_publish_steps(self) -> None:
        prompt = _pl_session.build_process_loop_prompt()  # pylint: disable=protected-access  # noqa: SLF001
        assert "git worktree内で起動" not in prompt
        assert "現在のHEADを`origin/master`へ反映" not in prompt
        assert "git push" not in prompt
        assert "push" not in prompt

    @pytest.mark.parametrize("remote_url", ["https://github.com/ak110/dotfiles.git\n", "https://github.com/example/repo.git\n"])
    def test_worktree_cwd_depends_on_target_repo(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        remote_url: str,
    ) -> None:
        """dotfilesでは作成済みworktreeをcwdに使い、CLIのworktree指定を使わない。"""
        _setup_notes(tmp_path)
        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()
        claude_calls: list[dict[str, Any]] = []
        counts = iter([1, 0])

        def fake_run(cmd: list[str], *_args: object, **kwargs: object) -> subprocess.CompletedProcess[Any]:
            if cmd[:1] == ["claude"] and "-p" in cmd:
                return subprocess.CompletedProcess(cmd, returncode=0, stdout="OK\n", stderr="")
            if cmd[:1] == ["claude"]:
                claude_calls.append({"cmd": list(cmd), "cwd": kwargs.get("cwd")})
                return subprocess.CompletedProcess(cmd, returncode=0, stdout="", stderr="")
            if cmd == ["git", "-C", str(myrepo), "remote", "get-url", "origin"]:
                return subprocess.CompletedProcess(cmd, returncode=0, stdout=remote_url, stderr="")
            return subprocess.CompletedProcess(cmd, returncode=0, stdout="", stderr="")

        monkeypatch.setattr(subprocess, "run", fake_run)
        monkeypatch.setattr(_wi_readiness, "count_pending_entries", lambda *_a, **_kw: next(counts))

        def fake_wait_for_changes(private_notes: pathlib.Path, target_repo_id: str | None) -> None:
            del private_notes, target_repo_id
            raise KeyboardInterrupt

        monkeypatch.setattr(_pl_watch, "wait_for_changes", fake_wait_for_changes)
        worktree_path = myrepo / ".claude" / "worktrees" / "process-loop"
        sync_calls: list[tuple[pathlib.Path, str]] = []

        def fake_sync(local_path: pathlib.Path, worktree_name: str) -> pathlib.Path:
            sync_calls.append((local_path, worktree_name))
            return worktree_path

        monkeypatch.setattr(
            _pl_worktree,
            "_sync_worktree_with_upstream",
            fake_sync,
        )

        with pytest.raises(SystemExit):
            atk.main(["wi", "process-loop", f"--target-repo={myrepo}", "--no-update"], home=tmp_path)

        assert len(claude_calls) == 1
        hook_debug_log(claude_calls[0]["cmd"])
        assert "--worktree=process-loop" not in claude_calls[0]["cmd"]
        expected_cwd = worktree_path if "ak110/dotfiles" in remote_url else myrepo
        assert claude_calls[0]["cwd"] == expected_cwd
        expected_sync_calls = [(myrepo, "process-loop")] if "ak110/dotfiles" in remote_url else []
        assert sync_calls == expected_sync_calls
