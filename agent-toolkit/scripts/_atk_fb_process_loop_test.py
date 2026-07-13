"""atk (agent-toolkit `atk fb`) のprocess-loopサブコマンド・リポジトリID解決のテスト。

process-loopサブコマンド（常駐ループ）、リモートURL正規化（`_normalize_remote_url`）、
リポジトリID解決（`_resolve_repo_id`）の単体テストを集約する。
既存サブコマンドの残テストは`atk_test.py`に、他サブコマンドの分割先は`_atk_fb_show_test.py`・
`_atk_fb_mutations_test.py`に分離する。共通ヘルパーは`atk_test.py`から再利用する。
"""

import os
import pathlib
import subprocess
import sys
import threading
from typing import Any

import pytest
import watchdog.events

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import _atk_fb_process_loop as _process_loop  # noqa: E402  # pylint: disable=wrong-import-position
import _atk_fb_repo as _repo  # noqa: E402  # pylint: disable=wrong-import-position
import atk  # noqa: E402  # pylint: disable=wrong-import-position
from atk_test import _setup_flag_and_notes  # noqa: E402  # pylint: disable=wrong-import-position


def _fake_run_with_remote_url(
    myrepo: pathlib.Path,
    claude_calls: list[dict[str, Any]],
    claude_returncode: int,
) -> Any:
    """claude呼び出し（コマンド・環境変数）を記録し、`git remote get-url origin`にはダミーURLを返すfake_runを構築する。"""

    def fake_run(cmd: list[str], *_args: object, **kwargs: object) -> subprocess.CompletedProcess[Any]:
        if cmd[:1] == ["claude"]:
            claude_calls.append({"cmd": list(cmd), "env": kwargs.get("env"), "cwd": kwargs.get("cwd")})
            empty: Any = "" if kwargs.get("text") else b""
            return subprocess.CompletedProcess(cmd, returncode=claude_returncode, stdout=empty, stderr=empty)
        if cmd == ["git", "-C", str(myrepo), "remote", "get-url", "origin"]:
            stdout: Any = (
                "https://github.com/example/myrepo.git\n" if kwargs.get("text") else b"https://github.com/example/myrepo.git\n"
            )
            return subprocess.CompletedProcess(cmd, returncode=0, stdout=stdout, stderr="" if kwargs.get("text") else b"")
        empty = "" if kwargs.get("text") else b""
        return subprocess.CompletedProcess(cmd, returncode=0, stdout=empty, stderr=empty)

    return fake_run


class TestProcessLoopIncludesProcessingInCount:
    """process-loopがfeedback inbox・processing双方を検知件数に含めることを公開CLI経由で検証する。"""

    def test_inbox_and_processing_entries_are_both_counted(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """inbox・processing双方に`.md`を配置した状態でprocess-loopを起動し、
        検知メッセージ`{count}件のfeedback/回答済みTBDを検知`の件数が合算値になること。
        """
        _setup_flag_and_notes(tmp_path)
        private_notes = tmp_path / "private-notes"
        inbox_dir = private_notes / "feedback" / "inbox"
        processing_dir = private_notes / "feedback" / "processing"
        processing_dir.mkdir(parents=True)
        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()
        # `_fake_run_with_remote_url`が返す正規化後IDと一致させる。
        target_repo_id = "github.com/example/myrepo"
        (inbox_dir / "a.md").write_text(
            f"---\ntarget_repo: {target_repo_id}\n---\n\n本文A\n",
            encoding="utf-8",
        )
        (processing_dir / "b.md").write_text(
            f"---\ntarget_repo: {target_repo_id}\n---\n\n本文B\n",
            encoding="utf-8",
        )

        base_fake_run = _fake_run_with_remote_url(myrepo, [], 0)

        def fake_run(cmd: list[str], *_args: object, **kwargs: object) -> subprocess.CompletedProcess[Any]:
            # claude実行を模したのちファイルを削除し、次反復で件数0とすることで
            # `_wait_for_changes`経路へ進めてループを終了させる。
            if cmd[:1] == ["claude"]:
                (inbox_dir / "a.md").unlink(missing_ok=True)
                (processing_dir / "b.md").unlink(missing_ok=True)
            return base_fake_run(cmd, *_args, **kwargs)

        monkeypatch.setattr(subprocess, "run", fake_run)

        def fake_wait(*_a: object, **_kw: object) -> None:
            raise KeyboardInterrupt

        monkeypatch.setattr(_process_loop, "_wait_for_changes", fake_wait)

        with pytest.raises(SystemExit):
            atk.main(
                ["fb", "process-loop", "--target-repo", str(myrepo), "--no-update"],
                home=tmp_path,
            )

        captured = capsys.readouterr()
        assert "2件のfeedback/回答済みTBDを検知" in captured.out


class TestChangeHandler:
    """_ChangeHandler.on_any_event: 監視対象イベント判定の実動作を検証する。"""

    def test_md_file_created_event_sets_change_event(self) -> None:
        """`.md`拡張子・非ディレクトリのFileCreatedEventでchange_eventがsetされること。"""
        change_event = threading.Event()
        handler = _process_loop._ChangeHandler(change_event)  # pylint: disable=protected-access  # noqa: SLF001
        event = watchdog.events.FileCreatedEvent("/tmp/dummy/inbox/entry.md")

        handler.on_any_event(event)

        assert change_event.is_set()

    def test_directory_event_ignored(self) -> None:
        """イベント種別フィルタを通過してもディレクトリイベントは無視されること。"""
        change_event = threading.Event()
        handler = _process_loop._ChangeHandler(change_event)  # pylint: disable=protected-access  # noqa: SLF001
        event = watchdog.events.FileCreatedEvent("/tmp/dummy/inbox/subdir")
        event.is_directory = True  # WATCHED_EVENT_TYPES判定を通過させたうえでディレクトリ判定分岐に到達させる

        handler.on_any_event(event)

        assert not change_event.is_set()

    def test_non_md_file_event_ignored(self) -> None:
        """`.md`以外の拡張子のファイルイベントは無視されchange_eventがsetされないこと。"""
        change_event = threading.Event()
        handler = _process_loop._ChangeHandler(change_event)  # pylint: disable=protected-access  # noqa: SLF001
        event = watchdog.events.FileCreatedEvent("/tmp/dummy/inbox/entry.txt")

        handler.on_any_event(event)

        assert not change_event.is_set()


class TestWaitForChanges:
    """_wait_for_changes: watchdog監視の実動作（タイムアウト・変更検知・デバウンス）を検証する。"""

    @staticmethod
    def _make_private_notes(tmp_path: pathlib.Path) -> pathlib.Path:
        private_notes = tmp_path / "private-notes"
        (private_notes / "feedback" / "inbox").mkdir(parents=True)
        (private_notes / "tbd" / "inbox").mkdir(parents=True)
        return private_notes

    def test_missing_inbox_dirs_are_created(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """inboxディレクトリ未作成でも監視前に作成され、タイムアウト経路が動作すること。"""
        private_notes = tmp_path / "private-notes"
        monkeypatch.setattr(_process_loop, "_POLL_INTERVAL_SEC", 0.1)
        monkeypatch.setattr(_process_loop, "_DEBOUNCE_SEC", 0.1)
        pull_calls: list[pathlib.Path] = []
        monkeypatch.setattr(_process_loop, "_pull", pull_calls.append)

        _process_loop._wait_for_changes(private_notes, None)  # pylint: disable=protected-access  # noqa: SLF001

        assert (private_notes / "feedback" / "inbox").is_dir()
        assert (private_notes / "tbd" / "inbox").is_dir()
        assert pull_calls == [private_notes]

    def test_timeout_triggers_pull(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """変更検知イベント無しでタイムアウトに達した場合、`_pull`が呼ばれること。"""
        private_notes = self._make_private_notes(tmp_path)
        monkeypatch.setattr(_process_loop, "_POLL_INTERVAL_SEC", 0.1)
        monkeypatch.setattr(_process_loop, "_DEBOUNCE_SEC", 0.1)
        pull_calls: list[pathlib.Path] = []
        monkeypatch.setattr(_process_loop, "_pull", pull_calls.append)

        _process_loop._wait_for_changes(private_notes, None)  # pylint: disable=protected-access  # noqa: SLF001

        assert pull_calls == [private_notes]

    def test_change_event_skips_pull(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """タイムアウト前に`.md`ファイル変更を検知した場合、`_pull`が呼ばれないこと。"""
        private_notes = self._make_private_notes(tmp_path)
        inbox = private_notes / "feedback" / "inbox"
        monkeypatch.setattr(_process_loop, "_POLL_INTERVAL_SEC", 2.0)
        monkeypatch.setattr(_process_loop, "_DEBOUNCE_SEC", 0.1)
        pull_calls: list[pathlib.Path] = []
        monkeypatch.setattr(_process_loop, "_pull", pull_calls.append)

        timer = threading.Timer(0.05, lambda: (inbox / "entry.md").write_text("x", encoding="utf-8"))
        timer.start()
        try:
            _process_loop._wait_for_changes(private_notes, None)  # pylint: disable=protected-access  # noqa: SLF001
        finally:
            timer.cancel()

        assert not pull_calls

    def test_debounce_folds_additional_events(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """デバウンス窓内の追加イベントが`clear`→`wait(timeout=_DEBOUNCE_SEC)`ループで畳み込まれること。"""
        private_notes = self._make_private_notes(tmp_path)
        inbox = private_notes / "feedback" / "inbox"
        monkeypatch.setattr(_process_loop, "_POLL_INTERVAL_SEC", 2.0)
        monkeypatch.setattr(_process_loop, "_DEBOUNCE_SEC", 0.3)
        monkeypatch.setattr(
            _process_loop,
            "_pull",
            lambda _path: pytest.fail("デバウンス経路では_pullを呼ばないこと"),
        )

        wait_calls: list[float | None] = []
        real_wait = threading.Event.wait

        def counting_wait(self: threading.Event, timeout: float | None = None) -> bool:
            wait_calls.append(timeout)
            return real_wait(self, timeout)

        monkeypatch.setattr(threading.Event, "wait", counting_wait)

        timer1 = threading.Timer(0.05, lambda: (inbox / "entry1.md").write_text("x", encoding="utf-8"))
        timer2 = threading.Timer(0.2, lambda: (inbox / "entry2.md").write_text("y", encoding="utf-8"))
        timer1.start()
        timer2.start()
        try:
            _process_loop._wait_for_changes(private_notes, None)  # pylint: disable=protected-access  # noqa: SLF001
        finally:
            timer1.cancel()
            timer2.cancel()

        debounce_waits = [t for t in wait_calls if t == 0.3]
        assert len(debounce_waits) >= 2


class TestProcessLoopPromptAndEnv:
    """process-loopサブコマンド: claude起動プロンプトと環境変数、正常終了時の反復継続を検証する。"""

    def test_invokes_claude_with_prompt_env_and_continues_loop(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """プロンプトに`/process-feedbacks`と`/agent-toolkit:exit-session`を含み、
        `DOTFILES_AUTONOMOUS_EXIT_REQUIRED=1`が付与され、`returncode=0`後は反復継続すること。
        件数0到達後は`_wait_for_changes`が呼ばれ、待機解除後に件数再チェックへ戻ること。
        2回目の`_wait_for_changes`呼び出しで`KeyboardInterrupt`を送出し常駐ループを正常終了する。
        """
        _setup_flag_and_notes(tmp_path)
        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()
        claude_calls: list[dict[str, Any]] = []

        monkeypatch.setattr(subprocess, "run", _fake_run_with_remote_url(myrepo, claude_calls, 0))

        # 件数: 1回目は1件（claude起動）、2回目以降は0件（待機ループへ）
        count_calls: list[int] = []

        def fake_count_pending_entries(private_notes: pathlib.Path, target_repo: str | None = None) -> int:
            del private_notes, target_repo
            count_calls.append(len(count_calls))
            return 1 if len(count_calls) == 1 else 0

        wait_calls: list[int] = []

        def fake_wait_for_changes(private_notes: pathlib.Path, target_repo_id: str | None) -> None:
            del private_notes, target_repo_id
            wait_calls.append(len(wait_calls))
            if len(wait_calls) >= 2:
                raise KeyboardInterrupt

        monkeypatch.setattr(_process_loop, "_count_pending_entries", fake_count_pending_entries)
        monkeypatch.setattr(_process_loop, "_wait_for_changes", fake_wait_for_changes)

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["fb", "process-loop", f"--target-repo={myrepo}", "--no-update"], home=tmp_path)

        assert exc_info.value.code == 0
        assert len(claude_calls) == 1
        prompt = claude_calls[0]["cmd"][-1]
        assert "/process-feedbacks" in prompt
        assert "process-feedbacks-finish" in prompt
        # cwdをmyrepoへ固定し、claudeセッション内のcwd依存コマンドの解決先を対象リポジトリへ揃える。
        assert claude_calls[0]["cwd"] == myrepo
        assert claude_calls[0]["cmd"][:4] == ["claude", "--permission-mode=auto", "--model", "opus"]
        assert claude_calls[0]["env"]["DOTFILES_AUTONOMOUS_EXIT_REQUIRED"] == "1"
        assert len(wait_calls) == 2
        captured = capsys.readouterr()
        assert "Ctrl+Cを検知しました" in captured.out

    def test_prompt_emphasizes_completion_over_exit_session(self) -> None:
        """プロンプトが取得した全件の完遂を主目標として明示し、作業量・所要時間を判断材料化しない旨を含むこと。"""
        prompt = _process_loop._build_process_loop_prompt(  # pylint: disable=protected-access  # noqa: SLF001
            pathlib.Path("/repo"),
            "github.com/example/repo",
        )
        assert "主目標" in prompt
        assert "完遂" in prompt
        assert "時間がかかるのは正常" in prompt
        assert "作業量" in prompt
        # 追加文言: 工程列挙が実施順序の定義である旨と、後続工程の到達要求を先行工程の縮退の根拠に解釈しない旨を明示する。
        assert "工程列挙は実施順序の定義であり作業量の見積りの根拠ではありません" in prompt
        assert "本プロンプトの完遂順序の列挙全体がユーザー明示指示を構成します" in prompt
        assert "後続工程" in prompt
        assert "縮退の根拠に" in prompt

    def test_prompt_references_process_feedbacks_finish(self) -> None:
        """プロンプトが後続工程の集約先としてprocess-feedbacks-finishスキルを参照すること。"""
        prompt = _process_loop._build_process_loop_prompt(  # pylint: disable=protected-access  # noqa: SLF001
            pathlib.Path("/repo"),
            "github.com/example/repo",
        )
        assert "process-feedbacks-finish" in prompt

    def test_prompt_includes_target_repo(self) -> None:
        """プロンプトが`--target-repo`限定指示と正規化リモートURLを本文へ含める。

        LLM起動プロンプトでcwd由来の暗黙解決を排除するため、target_repo_idを
        プロンプト本文へ明示埋め込みする。
        """
        target_repo_id = "github.com/example/repo"
        prompt = _process_loop._build_process_loop_prompt(  # pylint: disable=protected-access  # noqa: SLF001
            pathlib.Path("/repo"),
            target_repo_id,
        )
        assert f"--target-repo={target_repo_id}" in prompt
        assert "/repo" in prompt

    def test_model_override(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """`--model`引数の値がclaude起動コマンドへ反映される。"""
        _setup_flag_and_notes(tmp_path)
        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()
        claude_calls: list[dict[str, Any]] = []
        monkeypatch.setattr(subprocess, "run", _fake_run_with_remote_url(myrepo, claude_calls, 0))
        count_calls: list[int] = []

        def fake_count_pending_entries(private_notes: pathlib.Path, target_repo: str | None = None) -> int:
            del private_notes, target_repo
            count_calls.append(len(count_calls))
            return 1 if len(count_calls) == 1 else 0

        def fake_wait_for_changes(private_notes: pathlib.Path, target_repo_id: str | None) -> None:
            del private_notes, target_repo_id
            raise KeyboardInterrupt

        monkeypatch.setattr(_process_loop, "_count_pending_entries", fake_count_pending_entries)
        monkeypatch.setattr(_process_loop, "_wait_for_changes", fake_wait_for_changes)

        with pytest.raises(SystemExit):
            atk.main(
                ["fb", "process-loop", f"--target-repo={myrepo}", "--no-update", "--model=sonnet"],
                home=tmp_path,
            )

        assert len(claude_calls) == 1
        assert claude_calls[0]["cmd"][:4] == ["claude", "--permission-mode=auto", "--model", "sonnet"]


class TestProcessLoopClaudeReturncode:
    """process-loopサブコマンド: claudeのreturncode判定（正常/異常）を検証する。"""

    @pytest.mark.parametrize("returncode", [0, -15, 15, 143])
    def test_normal_returncode_continues_loop(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        returncode: int,
    ) -> None:
        """`returncode`が`0`・`-15`・`15`・`143`のいずれかなら反復継続し、
        次の待機で`KeyboardInterrupt`が送出されると正常終了すること。
        """
        _setup_flag_and_notes(tmp_path)
        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()
        claude_calls: list[dict[str, Any]] = []

        monkeypatch.setattr(subprocess, "run", _fake_run_with_remote_url(myrepo, claude_calls, returncode))
        monkeypatch.setattr(_process_loop, "_count_pending_entries", lambda *_a, **_kw: 1 if len(claude_calls) == 0 else 0)

        def fake_wait_for_changes(private_notes: pathlib.Path, target_repo_id: str | None) -> None:
            del private_notes, target_repo_id
            raise KeyboardInterrupt

        monkeypatch.setattr(_process_loop, "_wait_for_changes", fake_wait_for_changes)

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["fb", "process-loop", f"--target-repo={myrepo}", "--no-update"], home=tmp_path)

        assert exc_info.value.code == 0
        assert len(claude_calls) == 1

    def test_abnormal_returncode_exits_with_same_code(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """`returncode`が正常集合外なら、CLI自体が同じexit codeで終了すること。"""
        _setup_flag_and_notes(tmp_path)
        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()
        claude_calls: list[dict[str, Any]] = []

        monkeypatch.setattr(subprocess, "run", _fake_run_with_remote_url(myrepo, claude_calls, 42))
        monkeypatch.setattr(_process_loop, "_count_pending_entries", lambda *_a, **_kw: 1)

        def fake_wait_for_changes(*_a: object, **_kw: object) -> None:
            raise AssertionError("異常終了時は_wait_for_changesを呼ばないこと")

        monkeypatch.setattr(_process_loop, "_wait_for_changes", fake_wait_for_changes)

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["fb", "process-loop", f"--target-repo={myrepo}", "--no-update"], home=tmp_path)

        assert exc_info.value.code == 42
        captured = capsys.readouterr()
        assert "claudeがexit code 42で異常終了しました" in captured.err


class TestProcessLoopUpdateAndRestart:
    """1反復後のupdate-dotfiles実行と自身再起動の挙動を検証する。"""

    def test_update_and_execv_called_by_default(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """`--no-update`未指定でclaude正常終了時にupdate-dotfilesと`os.execv`が呼ばれること。"""
        myrepo = tmp_path / "repo"
        myrepo.mkdir()
        _setup_flag_and_notes(tmp_path)
        subprocess_calls: list[list[str]] = []
        base_fake_run = _fake_run_with_remote_url(myrepo, [], 0)

        def fake_run(cmd: list[str], *_args: object, **kwargs: object) -> subprocess.CompletedProcess[Any]:
            subprocess_calls.append(list(cmd))
            return base_fake_run(cmd, *_args, **kwargs)

        monkeypatch.setattr(subprocess, "run", fake_run)
        monkeypatch.setattr(
            _process_loop,
            "_count_pending_entries",
            lambda *_a, **_kw: 1,
        )
        execv_calls: list[tuple[str, list[str]]] = []

        def fake_execvp(path: str, argv: list[str]) -> None:
            execv_calls.append((path, list(argv)))
            raise SystemExit(0)

        monkeypatch.setattr(os, "execvp", fake_execvp)
        with pytest.raises(SystemExit):
            atk.main(
                ["fb", "process-loop", "--target-repo", str(myrepo)],
                home=tmp_path,
            )
        assert execv_calls
        assert execv_calls[0][0] == "uv"
        assert execv_calls[0][1][:4] == ["uv", "run", "--no-project", "--script"]
        assert any(cmd[0] == "update-dotfiles" for cmd in subprocess_calls)
        captured = capsys.readouterr()
        assert "update-dotfilesを実行して" in captured.out

    def test_no_update_skips_restart(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """`--no-update`指定時にupdate-dotfilesと`os.execv`のいずれも呼ばれないこと。"""
        myrepo = tmp_path / "repo"
        myrepo.mkdir()
        _setup_flag_and_notes(tmp_path)
        counts = iter([1, 0])
        subprocess_calls: list[list[str]] = []
        base_fake_run = _fake_run_with_remote_url(myrepo, [], 0)

        def fake_run(cmd: list[str], *_args: object, **kwargs: object) -> subprocess.CompletedProcess[Any]:
            subprocess_calls.append(list(cmd))
            return base_fake_run(cmd, *_args, **kwargs)

        monkeypatch.setattr(subprocess, "run", fake_run)
        monkeypatch.setattr(
            _process_loop,
            "_count_pending_entries",
            lambda *_a, **_kw: next(counts),
        )

        def fake_wait(*_a: object, **_kw: object) -> None:
            raise KeyboardInterrupt

        monkeypatch.setattr(_process_loop, "_wait_for_changes", fake_wait)
        execv_calls: list[tuple[str, list[str]]] = []
        monkeypatch.setattr(
            os,
            "execvp",
            lambda p, a: execv_calls.append((p, list(a))),
        )
        with pytest.raises(SystemExit):
            atk.main(
                ["fb", "process-loop", "--target-repo", str(myrepo), "--no-update"],
                home=tmp_path,
            )
        assert not execv_calls
        assert not any(cmd[0] == "update-dotfiles" for cmd in subprocess_calls)


class TestProcessLoopWaitMessage:
    """0件検知時の待機メッセージ出力を検証する。"""

    def test_wait_message_printed_before_wait(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """0件検知時に`_wait_for_changes`呼び出し直前で待機メッセージが出力されること。"""
        myrepo = tmp_path / "repo"
        myrepo.mkdir()
        _setup_flag_and_notes(tmp_path)
        monkeypatch.setattr(subprocess, "run", _fake_run_with_remote_url(myrepo, [], 0))
        monkeypatch.setattr(
            _process_loop,
            "_count_pending_entries",
            lambda *_a, **_kw: 0,
        )

        def fake_wait(*_a: object, **_kw: object) -> None:
            raise KeyboardInterrupt

        monkeypatch.setattr(_process_loop, "_wait_for_changes", fake_wait)
        with pytest.raises(SystemExit):
            atk.main(
                ["fb", "process-loop", "--target-repo", str(myrepo), "--no-update"],
                home=tmp_path,
            )
        captured = capsys.readouterr()
        assert "0件のため変更検知を待機します。" in captured.out


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
        result = _repo._resolve_repo_id(  # pylint: disable=protected-access  # noqa: SLF001
            "https://github.com/owner/repo.git",
        )
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
            atk.main(["fb", "process-loop", "--target-repo", "github.com/example/foo"], home=tmp_path)
        assert exc_info.value.code == 2
