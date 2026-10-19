"""`atk wi process-loop`の常駐ループの制御（処理対象の件数、対象リポジトリの解決、URL形式の入力）のテスト。"""

import os
import pathlib
import subprocess
from typing import Any, NoReturn

import pytest

from agent_toolkit import atk
from agent_toolkit._atk import lane as _lane
from agent_toolkit._atk import review_audit as _review_audit
from agent_toolkit._atk.wi import alerts as _wi_alerts
from agent_toolkit._atk.wi import process_loop as _process_loop
from agent_toolkit._atk.wi import process_loop_launch_gate as _pl_gate
from agent_toolkit._atk.wi import process_loop_log as _loop_log
from agent_toolkit._atk.wi import process_loop_session as _pl_session
from agent_toolkit._atk.wi import process_loop_watch as _pl_watch
from agent_toolkit._atk.wi import process_loop_worktree as _pl_worktree
from agent_toolkit._atk.wi import readiness as _wi_readiness
from agent_toolkit._atk.wi import repo as _repo
from agent_toolkit._atk.wi import sync as _wi_sync
from agent_toolkit._atk.wi import uwi as _uwi
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
        counts = iter([1, 1, 0])

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


def _start_entry(
    notes: pathlib.Path,
    name: str,
    *,
    state: str = "inbox",
    repo: str = "github.com/example/myrepo",
    answer: str | None = None,
    timestamp: float = 1000.0,
    kind: str = "uwi",
) -> pathlib.Path:
    path = notes / state / name
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "\n## 回答\n<!-- ユーザーはこの行以降に回答を追記する -->\n" + (answer or "") + "\n" if kind == "uwi" else ""
    path.write_text(f"---\ntype: {kind}\ntarget_repo: {repo}\n---\n\n質問\n{body}", encoding="utf-8")
    os.utime(path, (timestamp, timestamp))
    return path


@pytest.mark.parametrize("state", ["inbox", "processing", "hold"])
@pytest.mark.parametrize("release", ["deadline", "all-answered", "abort", "candidate-removed"])
def test_uwi_start_deadline(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    state: str,
    release: str,
) -> None:
    """公開CLIは期限前の未回答を待ち、全回答・期限・中断を再評価する。"""
    notes = _setup_notes(tmp_path)
    ready = _start_entry(notes, "ready.md", kind="awi")
    question = _start_entry(notes, "question.md", state=state)
    repo = tmp_path / "repo"
    repo.mkdir()
    clock = [1599.0]
    monkeypatch.setattr(_pl_watch.time, "time", lambda: clock[0])
    monkeypatch.setattr(_uwi, "is_agent_environment", lambda: False)
    monkeypatch.setattr(_wi_sync, "commit_and_push", lambda *_a, **_kw: None)
    children: list[dict[str, Any]] = []
    fake_run = fake_run_with_remote_url(repo, children, 0)

    def run(cmd: list[str], *args: Any, **kwargs: Any) -> subprocess.CompletedProcess[Any]:
        result = fake_run(cmd, *args, **kwargs)
        if children:
            raise KeyboardInterrupt
        return result

    monkeypatch.setattr(subprocess, "run", run)
    waited = False

    def wait(*_args: Any, **_kwargs: Any) -> bool:
        nonlocal waited
        assert not children
        if waited:
            raise KeyboardInterrupt
        waited = True
        if release == "deadline":
            clock[0] = 1600.0
        elif release == "all-answered":
            with pytest.raises(SystemExit) as answer_exit:
                atk.main(["wi", "answer", question.name, "承認"], home=tmp_path)
            assert answer_exit.value.code == 0
        elif release == "candidate-removed":
            ready.unlink()
            question.unlink()
        else:
            raise KeyboardInterrupt
        return True

    monkeypatch.setattr(_pl_watch, "wait_for_changes", wait)
    with pytest.raises(SystemExit) as exc:
        atk.main(["wi", "process-loop", "--target-repo", str(repo), "--no-update", "--no-alerts"], home=tmp_path)
    assert exc.value.code == 0
    assert waited
    assert len(children) == int(release in {"deadline", "all-answered"})


def test_uwi_updates_across_states(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """一部回答とadopted移動後の更新を期限へ含め、再起動でmtimeを更新しない。"""
    notes = _setup_notes(tmp_path)
    monkeypatch.setattr(_uwi, "is_agent_environment", lambda: False)
    monkeypatch.setattr(_wi_sync, "pull", lambda *_a, **_kw: None)
    monkeypatch.setattr(_wi_sync, "commit_and_push", lambda *_a, **_kw: None)
    monkeypatch.setattr(subprocess, "run", fake_run_with_remote_url(tmp_path, [], 0))
    _start_entry(notes, "remaining.md", state="hold")
    answered = _start_entry(notes, "answered.md", state="hold")
    with pytest.raises(SystemExit) as answer_exit:
        atk.main(["wi", "answer", answered.name, "承認"], home=tmp_path)
    assert answer_exit.value.code == 0
    os.utime(answered, (1100, 1100))
    gate = _pl_watch.StartGate(notes)
    decision = gate.evaluate({"github.com/example/myrepo"}, activity=False, now=1600)
    assert decision.deadline == 1700
    adopted = notes / "adopted" / answered.name
    adopted.parent.mkdir(exist_ok=True)
    answered.write_text(
        answered.read_text(encoding="utf-8").replace("type: uwi", "type: uwi\nquestion_type: post-approval"),
        encoding="utf-8",
    )
    with pytest.raises(SystemExit) as adopted_exit:
        atk.main(["wi", "answer", answered.name, "その対応で問題無い"], home=tmp_path)
    assert adopted_exit.value.code == 0
    assert not answered.exists()
    os.utime(adopted, (1200, 1200))
    assert gate.evaluate({"github.com/example/myrepo"}, activity=False, now=1700).deadline == 1800
    restarted = _pl_watch.StartGate(notes)
    assert restarted.evaluate({"github.com/example/myrepo"}, activity=False, now=1799).deadline == 1800
    assert not restarted.evaluate({"github.com/example/myrepo"}, activity=False, now=1800).reasons
    assert adopted.stat().st_mtime == 1200


def test_activity_grace_extends_on_new_wi(tmp_path: pathlib.Path) -> None:
    """対象外を含む新規到着ごとに検出から猶予を延長し、累計上限を置かない。"""
    notes = _setup_notes(tmp_path)
    gate = _pl_watch.StartGate(notes)
    assert gate.evaluate({"github.com/example/myrepo"}, activity=True, now=1000).deadline == 1600
    for index, now in enumerate([1599, 2198, 2797]):
        _start_entry(notes, f"arrival-{index}.md", repo="github.com/other/repo", timestamp=now, kind="awi")
        decision = gate.evaluate({"github.com/example/myrepo"}, activity=True, now=now)
        assert decision.extended
        assert decision.deadline == now + 600
    assert not gate.evaluate({"github.com/example/myrepo"}, activity=True, now=3397).reasons
    gate.started()
    assert gate.evaluate({"github.com/example/myrepo"}, activity=True, now=3400).deadline == 4000


def test_grace_ignores_edits_and_recounts(tmp_path: pathlib.Path) -> None:
    """編集・状態移動・同名の再出現は到着と数えず、無活動なら早期解除する。"""
    notes = _setup_notes(tmp_path)
    entry = _start_entry(notes, "existing.md", kind="awi")
    gate = _pl_watch.StartGate(notes)
    gate.evaluate({"github.com/example/myrepo"}, activity=True, now=1000)
    entry.write_text(entry.read_text(encoding="utf-8") + "編集\n", encoding="utf-8")
    moved = notes / "processing" / entry.name
    moved.parent.mkdir(exist_ok=True)
    entry.rename(moved)
    decision = gate.evaluate({"github.com/example/myrepo"}, activity=True, now=1500)
    assert not decision.extended
    assert decision.deadline == 1600
    moved.unlink()
    gate.evaluate({"github.com/example/myrepo"}, activity=True, now=1510)
    _start_entry(notes, "existing.md", kind="awi")
    assert not gate.evaluate({"github.com/example/myrepo"}, activity=True, now=1520).extended
    assert not gate.evaluate({"github.com/example/myrepo"}, activity=False, now=1520).reasons


def test_start_scope_excludes_foreign_uwi(tmp_path: pathlib.Path) -> None:
    """登録集合の外の未回答UWIは抑止せず、対象内run-skillは抑止する。"""
    notes = _setup_notes(tmp_path)
    _start_entry(notes, "foreign.md", repo="github.com/other/repo")
    gate = _pl_watch.StartGate(notes)
    assert not gate.evaluate({"github.com/example/myrepo"}, activity=False, now=1000).reasons
    skill = _start_entry(notes, "skill.md", state="hold")
    skill.write_text(skill.read_text(encoding="utf-8").replace("type: uwi", "type: uwi\nsource: run-skill"), encoding="utf-8")
    os.utime(skill, (1000, 1000))
    decision = gate.evaluate({"github.com/example/myrepo"}, activity=False, now=1000)
    assert decision.reasons
    assert decision.deadline == 1600


@pytest.mark.parametrize("abort", [False, True])
def test_grace_abort_and_output(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    abort: bool,
) -> None:
    """公開CLIで到着延長を表示し、中断なら子を起動せず期限なら開始する。"""
    notes = _setup_notes(tmp_path)
    _start_entry(notes, "ready.md", kind="awi")
    repo = tmp_path / "repo"
    repo.mkdir()
    clock = [1000.0]
    monkeypatch.setattr(_pl_watch.time, "time", lambda: clock[0])
    monkeypatch.setattr(_pl_watch, "_submission_active", lambda _targets: True)
    children: list[dict[str, Any]] = []
    fake_run = fake_run_with_remote_url(repo, children, 0)

    def run(cmd: list[str], *args: Any, **kwargs: Any) -> subprocess.CompletedProcess[Any]:
        result = fake_run(cmd, *args, **kwargs)
        if children:
            raise KeyboardInterrupt
        return result

    monkeypatch.setattr(subprocess, "run", run)
    waits = 0

    def wait(*_args: Any, **_kwargs: Any) -> bool:
        nonlocal waits
        assert not children
        waits += 1
        assert waits <= 2
        if waits == 1:
            clock[0] = 1599
            _start_entry(notes, "new.md", kind="awi", timestamp=1599)
        elif abort:
            with pytest.raises(SystemExit) as abort_exit:
                atk.main(["wi", "process-loop", "abort"], home=tmp_path)
            assert abort_exit.value.code == 0
        else:
            clock[0] = 2199
        return True

    monkeypatch.setattr(_pl_watch, "wait_for_changes", wait)
    with pytest.raises(SystemExit) as exc:
        atk.main(["wi", "process-loop", "--target-repo", str(repo), "--no-update", "--no-alerts"], home=tmp_path)
    assert exc.value.code == 0
    assert waits == 2
    assert len(children) == int(not abort)
    output = capsys.readouterr().out
    assert "活動" in output and "期限" in output and "延長" in output
    if not abort:
        assert "委譲" in output


@pytest.mark.parametrize("returncode", [0, 1])
@pytest.mark.parametrize("outcome", ["retained", "error"])
def test_session_end_uses_common_lane_cleanup(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    returncode: int,
    outcome: str,
) -> None:
    """単一起動の子終了後も、all-reposと同じ共通の終了後処理で回収・記録・再回収の案内を行い、終了判定より前に済ませる。"""
    notes = _setup_notes(tmp_path)
    myrepo = tmp_path / "myrepo"
    myrepo.mkdir()
    ready = notes / "inbox" / "ready.md"
    ready.write_text("---\ntarget_repo: github.com/example/myrepo\ntype: awi\n---\n\n本文\n", encoding="utf-8")
    base_fake_run = fake_run_with_remote_url(myrepo, [], returncode)

    def fake_run(cmd: list[str], *args: Any, **kwargs: Any) -> subprocess.CompletedProcess[Any]:
        if cmd[:1] == ["claude"] and "-p" not in cmd:
            ready.unlink(missing_ok=True)
        return base_fake_run(cmd, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", fake_run)
    judged: list[str | None] = []

    def cleanup(session_id: str | None, **_kwargs: Any) -> dict[str, Any]:
        judged.append(session_id)
        if outcome == "error":
            raise OSError("回収先へ書けない")
        return {"session_id": session_id, "removed": [], "retained": [{"reason": "未統合"}], "recovered": []}

    monkeypatch.setattr(_lane, "cleanup_session", cleanup)
    recorded: list[tuple[str, dict[str, Any]]] = []
    original_append = _loop_log.append

    def append(event: str, *, session_id: str | None = None, **fields: Any) -> None:
        recorded.append((event, fields))
        original_append(event, session_id=session_id, **fields)

    monkeypatch.setattr(_loop_log, "append", append)

    def stop_wait(*_args: object, **_kwargs: object) -> NoReturn:
        raise KeyboardInterrupt

    monkeypatch.setattr(_pl_watch, "wait_for_changes", stop_wait)
    with pytest.raises(SystemExit) as ended:
        atk.main(["wi", "process-loop", "--target-repo", str(myrepo), "--no-update", "--no-alerts"], home=tmp_path)
    assert ended.value.code == returncode
    assert len(judged) == 1 and judged[0]
    cleanups = [fields for event, fields in recorded if event == "lane_cleanup"]
    assert len(cleanups) == 1 and cleanups[0]["lane_session_id"] == judged[0] and "target_repo" not in cleanups[0]
    assert ("error" in cleanups[0]) is (outcome == "error")
    assert f"atk lane delete --session-id {judged[0]}" in capsys.readouterr().err
    events = [event for event, _fields in recorded]
    assert events.index("lane_cleanup") < events.index("session_classified")


_TARGET = "github.com/example/myrepo"


def _awi(notes: pathlib.Path, name: str, body: str = "本文", *, state: str = "inbox") -> pathlib.Path:
    path = notes / state / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\ntarget_repo: {_TARGET}\ntype: awi\n---\n\n{body}\n", encoding="utf-8")
    return path


def _run_single_loop(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    on_child: Any = None,
    on_wait: tuple[Any, ...] = (),
    extra_argv: tuple[str, ...] = ("--no-alerts",),
) -> list[dict[str, Any]]:
    """公開CLIの単一起動を`--no-update`で回し、変更待機ごとに`on_wait`を順に適用し、尽きたら終了させる。"""
    myrepo = tmp_path / "myrepo"
    myrepo.mkdir(exist_ok=True)
    child_calls: list[dict[str, Any]] = []
    base_fake_run = fake_run_with_remote_url(myrepo, child_calls, 0)

    def fake_run(cmd: list[str], *args: Any, **kwargs: Any) -> subprocess.CompletedProcess[Any]:
        result = base_fake_run(cmd, *args, **kwargs)
        if cmd[:1] == ["claude"] and "-p" not in cmd and on_child is not None:
            on_child(len(child_calls))
        return result

    monkeypatch.setattr(subprocess, "run", fake_run)
    waits = iter(on_wait)

    def fake_wait(*_args: object, **_kwargs: object) -> bool:
        action = next(waits, None)
        if action is None:
            raise KeyboardInterrupt
        action()
        return True

    monkeypatch.setattr(_pl_watch, "wait_for_changes", fake_wait)
    with pytest.raises(SystemExit) as ended:
        atk.main(["wi", "process-loop", "--target-repo", str(myrepo), "--no-update", *extra_argv], home=tmp_path)
    assert ended.value.code == 0
    return child_calls


@pytest.mark.parametrize("resolution", ["none", "partial"])
def test_unresolved_session_skips_relaunch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], resolution: str
) -> None:
    """正常終了した子セッションが候補を1件も解消しなければ、同じ入力の間は次のセッションを起動しない。"""
    notes = _setup_notes(tmp_path)
    first = _awi(notes, "first.md")
    _awi(notes, "second.md")

    def child(call: int) -> None:
        if resolution == "partial" and call == 1:
            first.unlink()

    calls = _run_single_loop(tmp_path, monkeypatch, on_child=child)
    # 一部を解消した回は記録せずに次の反復で起動し、その次の無進捗の回で見送りへ移る。
    assert len(calls) == (2 if resolution == "partial" else 1)
    out = capsys.readouterr().out
    assert "担当の起動を見送ります" in out and "新しいWIの到着" in out
    assert "起動を見送ったため変更検知を待機します。" in out


@pytest.mark.parametrize("change", ["new-wi", "body", "state", "new-alert"])
def test_skipped_target_relaunches_on_change(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, change: str) -> None:
    """見送り中に起動入力が変われば次の反復で起動し、起動した時点で記録を削除する。"""
    notes = _setup_notes(tmp_path)
    task = _awi(notes, "task.md")
    alerts: list[dict[str, Any]] = [{"number": 1}]
    monkeypatch.setattr(_wi_alerts, "check_and_submit_alerts", lambda *_a, **_k: _wi_alerts.AlertCheckResult(0, ()))
    monkeypatch.setattr(_review_audit, "dependabot_pending", lambda _repository: {"status": "available", "alerts": alerts})

    def apply() -> None:
        if change == "new-wi":
            _awi(notes, "added.md")
        elif change == "body":
            task.write_text(task.read_text(encoding="utf-8") + "\n## ユーザーコメント\n\n回答\n", encoding="utf-8")
        elif change == "state":
            (notes / "processing").mkdir(exist_ok=True)
            task.rename(notes / "processing" / task.name)
        else:
            alerts.append({"number": 2})

    # 1回目の子は何も解消せず、見送りの待機中に入力が変わり、2回目の子の後は再び見送る。
    calls = _run_single_loop(tmp_path, monkeypatch, on_wait=(apply,), extra_argv=("--alert-interval=0",))
    assert len(calls) == 2


def test_wi_added_during_session_is_launched_next(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """子セッションの稼働中に加わったWIは記録に含めず、次の反復の起動対象にする。"""
    notes = _setup_notes(tmp_path)
    _awi(notes, "task.md")

    def child(call: int) -> None:
        if call == 1:
            _awi(notes, "arrived.md")

    assert len(_run_single_loop(tmp_path, monkeypatch, on_child=child)) == 2


def test_resume_bypasses_skip(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """無進捗記録があっても`--resume`の指定は起動する。"""
    notes = _setup_notes(tmp_path)
    _awi(notes, "task.md")
    current = _pl_gate.target_input(notes, _TARGET, None)
    _pl_gate.record_no_progress(_TARGET, current, current)
    assert not _pl_gate.decide(_TARGET, current).launch
    calls = _run_single_loop(tmp_path, monkeypatch, extra_argv=("--no-alerts", "--resume"))
    assert len(calls) == 1 and "--resume" in calls[0]["cmd"]
