"""atk (agent-toolkit `atk mq`) のprocess-loopサブコマンド・リポジトリID解決のテスト。

process-loopサブコマンド（常駐ループ）、リモートURL正規化（`_normalize_remote_url`）、
リポジトリID解決（`_resolve_repo_id`）の単体テストを集約する。
既存サブコマンドの残テストは`atk_test.py`に、他サブコマンドの分割先は`_atk_mq_show_test.py`・
`_atk_mq_mutations_test.py`に分離する。共通ヘルパーは`atk_test.py`から再利用する。
"""

import contextlib
import os
import pathlib
import stat
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from typing import Any, NoReturn

import pytest
import watchdog.events

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import _atk_config as _config  # noqa: E402  # pylint: disable=wrong-import-position
import _atk_mq_process_loop as _process_loop  # noqa: E402  # pylint: disable=wrong-import-position
import _atk_mq_repo as _repo  # noqa: E402  # pylint: disable=wrong-import-position
import atk  # noqa: E402  # pylint: disable=wrong-import-position
from atk_test import _setup_notes  # noqa: E402  # pylint: disable=wrong-import-position

_PROCESS_LOOP_SESSION_ENV = "AGENT_TOOLKIT_PROCESS_LOOP_SESSION"
_LEGACY_PROCESS_LOOP_SESSION_ENV = "DOTFILES_AUTONOMOUS_EXIT_REQUIRED"
_PULL_PRIVATE_NOTES_IMPL = _process_loop._pull_private_notes  # pylint: disable=protected-access  # noqa: SLF001
_DOTFILES_REPO_ID = _process_loop._DOTFILES_REPO_ID  # pylint: disable=protected-access  # noqa: SLF001


@pytest.fixture(autouse=True)
def _resolve_process_loop_commands(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """外部コマンドとClaude設定を利用者環境から分離する。"""
    monkeypatch.setattr(_config.platformdirs, "user_config_dir", lambda _name, **_kwargs: str(tmp_path / "config"))
    monkeypatch.setattr(_process_loop.shutil, "which", lambda command: f"/resolved/{command}")
    monkeypatch.setattr(_process_loop, "_pull_private_notes", lambda _path: True)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / ".claude"))


def _command_was_called(calls: list[list[str]], command: str) -> bool:
    """呼び出し配列の先頭要素を基底名で照合する。"""
    return any(pathlib.Path(call[0]).stem.lower() == command for call in calls)


def _set_orchestrate_model(tmp_path: pathlib.Path, value: str) -> None:
    """テスト用の`orchestrate_model`を公開設定CLI経由で保存する。"""
    with pytest.raises(SystemExit) as exc_info:
        atk.main(["config", "set", "orchestrate_model", value], home=tmp_path)
    assert exc_info.value.code == 0


def _hook_debug_log(command: list[str]) -> pathlib.Path:
    """Claude起動コマンドからhook診断ログを取得し、共通契約を検証する。"""
    assert command[:3] == ["claude", "--debug=hooks", "--debug-file"]
    debug_log = pathlib.Path(command[3])
    assert debug_log.is_absolute()
    assert debug_log.is_file()
    return debug_log


def _fake_run_with_remote_url(
    myrepo: pathlib.Path,
    claude_calls: list[dict[str, Any]],
    claude_returncode: int,
) -> Any:
    """対話セッション呼び出しを記録し、リモートURL取得にはダミー値を返すfake_runを構築する。"""

    def fake_run(cmd: list[str], *_args: object, **kwargs: object) -> subprocess.CompletedProcess[Any]:
        if cmd[:1] in (["claude"], ["codex"]):
            claude_calls.append(
                {
                    "cmd": list(cmd),
                    "env": kwargs.get("env"),
                    "cwd": kwargs.get("cwd"),
                    "kwargs": dict(kwargs),
                }
            )
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


def _raise_system_exit_0(*_a: object, **_kw: object) -> NoReturn:  # os.execvの代替として無条件にSystemExit(0)を送出する。
    raise SystemExit(0)


class TestProcessLoopIncludesProcessingInCount:
    """`process-loop`がフィードバックの`inbox`・`processing`双方を検知件数に含めることを公開CLI経由で検証する。"""

    def test_inbox_and_processing_entries_are_both_counted(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """inbox・processing双方に`.md`を配置した状態でprocess-loopを起動し、
        検知メッセージ`{count}件のフィードバック/回答済みTBDを検知`の件数が合算値になること。
        """
        _setup_notes(tmp_path)
        private_notes = tmp_path / "private-notes"
        inbox_dir = private_notes / "inbox"
        processing_dir = private_notes / "processing"
        processing_dir.mkdir(parents=True)
        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()
        # `_fake_run_with_remote_url`が返す正規化後IDと一致させる。
        target_repo_id = "github.com/example/myrepo"
        (inbox_dir / "a.md").write_text(
            f"---\ntarget_repo: {target_repo_id}\ntype: feedback\n---\n\n本文A\n",
            encoding="utf-8",
        )
        (processing_dir / "b.md").write_text(
            f"---\ntarget_repo: {target_repo_id}\ntype: feedback\n---\n\n本文B\n",
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
                ["mq", "process-loop", "--target-repo", str(myrepo), "--no-update"],
                home=tmp_path,
            )

        captured = capsys.readouterr()
        assert "2件のフィードバック/回答済みTBDを検知" in captured.out

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
            f"---\ntarget_repo: {target_repo}\ntype: feedback\ncooldown_until: '2999-01-01T00:00:00+00:00'\n---\n\n本文\n",
            encoding="utf-8",
        )
        child_calls: list[dict[str, Any]] = []
        monkeypatch.setattr(subprocess, "run", _fake_run_with_remote_url(myrepo, child_calls, 0))

        def stop_wait(*_args: object, **_kwargs: object) -> NoReturn:
            raise KeyboardInterrupt

        monkeypatch.setattr(_process_loop, "_wait_for_changes", stop_wait)

        with pytest.raises(SystemExit) as exc_info:
            atk.main(
                ["mq", "process-loop", "--target-repo", str(myrepo), "--no-update", "--no-alerts"],
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
            f"---\ntarget_repo: {target_repo}\ntype: feedback\ncooldown_until: '2999-01-01T00:00:00+00:00'\n---\n\n本文\n",
            encoding="utf-8",
        )

        ready = inbox / "ready.md"
        ready.write_text(
            f"---\ntarget_repo: {target_repo}\ntype: feedback\n---\n\n本文\n",
            encoding="utf-8",
        )
        child_calls: list[dict[str, Any]] = []
        base_fake_run = _fake_run_with_remote_url(myrepo, child_calls, 0)

        def fake_run(cmd: list[str], *_args: object, **kwargs: object) -> subprocess.CompletedProcess[Any]:
            result = base_fake_run(cmd, *_args, **kwargs)
            if cmd[:1] == ["claude"]:
                ready.unlink()
            return result

        monkeypatch.setattr(subprocess, "run", fake_run)

        def stop_wait(*_args: object, **_kwargs: object) -> NoReturn:
            raise KeyboardInterrupt

        monkeypatch.setattr(_process_loop, "_wait_for_changes", stop_wait)

        with pytest.raises(SystemExit) as exc_info:
            atk.main(
                ["mq", "process-loop", "--target-repo", str(myrepo), "--no-update", "--no-alerts"],
                home=tmp_path,
            )

        assert exc_info.value.code == 0
        assert len(child_calls) == 1


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
        (private_notes / "processing").mkdir(parents=True)
        (private_notes / "inbox").mkdir(parents=True)
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

        assert (private_notes / "processing").is_dir()
        assert (private_notes / "inbox").is_dir()
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

    def test_pull_failure_is_caught_and_warned(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """タイムアウト時に`_pull`が`subprocess.CalledProcessError`を送出しても、
        例外を送出せずstderr警告を出力して復帰すること。"""
        private_notes = self._make_private_notes(tmp_path)
        monkeypatch.setattr(_process_loop, "_POLL_INTERVAL_SEC", 0.1)
        monkeypatch.setattr(_process_loop, "_DEBOUNCE_SEC", 0.1)

        def fake_pull(_path: pathlib.Path) -> None:
            raise subprocess.CalledProcessError(1, ["git", "merge", "--ff-only", "@{u}"])

        monkeypatch.setattr(_process_loop, "_pull", fake_pull)

        _process_loop._wait_for_changes(private_notes, None)  # pylint: disable=protected-access  # noqa: SLF001

        assert "remote同期に失敗" in capsys.readouterr().err

    def test_change_event_skips_pull(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """タイムアウト前に`.md`ファイル変更を検知した場合、`_pull`が呼ばれないこと。"""
        private_notes = self._make_private_notes(tmp_path)
        inbox = private_notes / "inbox"
        # 並列実行時のCPU競合でタイマー発火とイベント配送が遅延してもタイムアウト経路へ移らないよう、
        # 待機上限（10秒）をタイマーの発火時刻（0.05秒）より十分大きく取る。
        # 変更を検知した時点で戻るため、上限を大きくしても通常の所要時間は延びない。
        monkeypatch.setattr(_process_loop, "_POLL_INTERVAL_SEC", 10.0)
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
        inbox = private_notes / "inbox"
        # 並列実行時のCPU競合でタイマー発火が遅延してもデバウンス窓内へ収まるよう、
        # 窓幅（1.5秒）を2本のタイマーの発火間隔（0.4秒）より十分大きく取る。
        monkeypatch.setattr(_process_loop, "_POLL_INTERVAL_SEC", 10.0)
        monkeypatch.setattr(_process_loop, "_DEBOUNCE_SEC", 1.5)
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

        timer1 = threading.Timer(0.1, lambda: (inbox / "entry1.md").write_text("x", encoding="utf-8"))
        timer2 = threading.Timer(0.5, lambda: (inbox / "entry2.md").write_text("y", encoding="utf-8"))
        timer1.start()
        timer2.start()
        try:
            _process_loop._wait_for_changes(private_notes, None)  # pylint: disable=protected-access  # noqa: SLF001
        finally:
            timer1.cancel()
            timer2.cancel()

        debounce_waits = [t for t in wait_calls if t == 1.5]
        assert len(debounce_waits) >= 2


class TestProcessLoopPromptAndEnv:
    """process-loopサブコマンド: claude起動プロンプトと環境変数、正常終了時の反復継続を検証する。"""

    def test_invokes_claude_with_prompt_env_and_continues_loop(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """新規セッションのプロンプトが単一の`/goal`条件であり、
        `AGENT_TOOLKIT_PROCESS_LOOP_SESSION=1`が付与され、`returncode=0`後は反復継続すること。
        件数0到達後は`_wait_for_changes`が呼ばれ、待機解除後に件数再チェックへ戻ること。
        2回目の`_wait_for_changes`呼び出しで`KeyboardInterrupt`を送出し常駐ループを正常終了する。
        ランチャーとの再起動要求の受け渡しファイルを指す環境変数は子セッションへ渡さないことも確認する。
        """
        _setup_notes(tmp_path)
        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()
        claude_calls: list[dict[str, Any]] = []

        # ランチャーとの再起動要求の受け渡しファイルは自プロセス専用であり、子孫セッションへは渡さない。
        monkeypatch.setenv("AGENT_TOOLKIT_RESTART_SPEC", str(tmp_path / "restart-spec"))
        monkeypatch.setenv("CLAUDE_CODE_DEBUG_LOGS_DIR", str(tmp_path / "ignored-debug.log"))
        monkeypatch.setenv(_PROCESS_LOOP_SESSION_ENV, "new-original")
        monkeypatch.setenv(_LEGACY_PROCESS_LOOP_SESSION_ENV, "legacy-original")
        monkeypatch.setattr(subprocess, "run", _fake_run_with_remote_url(myrepo, claude_calls, 0))
        closed_descriptors: list[int] = []
        real_close = os.close

        def record_close(descriptor: int) -> None:
            closed_descriptors.append(descriptor)
            real_close(descriptor)

        monkeypatch.setattr(_process_loop.os, "close", record_close)

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
            atk.main(["mq", "process-loop", f"--target-repo={myrepo}", "--no-update"], home=tmp_path)

        assert exc_info.value.code == 0
        assert len(claude_calls) == 1
        prompt = claude_calls[0]["cmd"][-1]
        assert prompt.startswith("/goal ")
        assert "agent-toolkit:process-feedbacks" in prompt
        # cwdをmyrepoへ固定し、claudeセッション内のcwd依存コマンドの解決先を対象リポジトリへ揃える。
        assert claude_calls[0]["cwd"] == myrepo
        command = claude_calls[0]["cmd"]
        debug_log = _hook_debug_log(command)
        assert debug_log.parent == tmp_path / ".claude" / "debug"
        if os.name != "nt":
            assert stat.S_IMODE(debug_log.stat().st_mode) == 0o600
        assert command[4:11] == [
            "--settings",
            '{"askUserQuestionTimeout": "5m"}',
            "--permission-mode=auto",
            "--model",
            "opus[1m]",
            "--effort",
            "medium",
        ]
        assert "--autocompact" not in command
        assert claude_calls[0]["env"][_PROCESS_LOOP_SESSION_ENV] == "1"
        assert claude_calls[0]["env"][_LEGACY_PROCESS_LOOP_SESSION_ENV] == "1"
        assert "AGENT_TOOLKIT_RESTART_SPEC" not in claude_calls[0]["env"]
        assert len(wait_calls) == 2
        captured = capsys.readouterr()
        assert "Ctrl+Cを検知しました" in captured.out
        assert f"Claude hook診断ログ: {debug_log}" in captured.out
        assert len(closed_descriptors) == 1
        assert os.environ[_PROCESS_LOOP_SESSION_ENV] == "new-original"
        assert os.environ[_LEGACY_PROCESS_LOOP_SESSION_ENV] == "legacy-original"
        with pytest.raises(OSError):
            os.fstat(closed_descriptors[0])

    def test_hook_debug_log_uses_home_when_config_dir_is_unset(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """`CLAUDE_CONFIG_DIR`未設定時は利用者ホーム配下`.claude/debug/`へ保存する。"""
        _setup_notes(tmp_path)
        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()
        claude_calls: list[dict[str, Any]] = []
        monkeypatch.delenv(_PROCESS_LOOP_SESSION_ENV, raising=False)
        monkeypatch.delenv(_LEGACY_PROCESS_LOOP_SESSION_ENV, raising=False)
        monkeypatch.delenv("CLAUDE_CONFIG_DIR")
        monkeypatch.setattr(pathlib.Path, "home", classmethod(lambda _cls: tmp_path))
        monkeypatch.setattr(subprocess, "run", _fake_run_with_remote_url(myrepo, claude_calls, 0))
        counts = iter((1, 0))
        monkeypatch.setattr(_process_loop, "_count_pending_entries", lambda *_a, **_kw: next(counts))

        def fake_wait_for_changes(*_args: object, **_kwargs: object) -> None:
            raise KeyboardInterrupt

        monkeypatch.setattr(_process_loop, "_wait_for_changes", fake_wait_for_changes)

        with pytest.raises(SystemExit):
            atk.main(
                ["mq", "process-loop", f"--target-repo={myrepo}", "--no-update", "--no-alerts"],
                home=tmp_path,
            )

        assert len(claude_calls) == 1
        assert _hook_debug_log(claude_calls[0]["cmd"]).parent == tmp_path / ".claude" / "debug"
        assert _PROCESS_LOOP_SESSION_ENV not in os.environ
        assert _LEGACY_PROCESS_LOOP_SESSION_ENV not in os.environ

    @pytest.mark.skipif(os.name == "nt", reason="POSIXの権限設定失敗時だけに適用する契約")
    def test_hook_debug_log_descriptor_closes_when_permission_setting_fails(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """`fchmod`失敗時もfile descriptorを閉じ、Claudeを起動しない。"""
        _setup_notes(tmp_path)
        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()
        claude_calls: list[dict[str, Any]] = []
        monkeypatch.setattr(subprocess, "run", _fake_run_with_remote_url(myrepo, claude_calls, 0))
        monkeypatch.setattr(_process_loop, "_count_pending_entries", lambda *_a, **_kw: 1)
        permission_descriptors: list[int] = []
        closed_descriptors: list[int] = []
        real_close = os.close

        def fail_fchmod(descriptor: int, _mode: int) -> None:
            permission_descriptors.append(descriptor)
            raise PermissionError("権限設定失敗")

        def record_close(descriptor: int) -> None:
            closed_descriptors.append(descriptor)
            real_close(descriptor)

        monkeypatch.setattr(_process_loop.os, "fchmod", fail_fchmod)
        monkeypatch.setattr(_process_loop.os, "close", record_close)

        with pytest.raises(PermissionError, match="権限設定失敗"):
            atk.main(
                ["mq", "process-loop", f"--target-repo={myrepo}", "--no-update", "--no-alerts"],
                home=tmp_path,
            )

        assert permission_descriptors == closed_descriptors
        assert len(closed_descriptors) == 1
        with pytest.raises(OSError):
            os.fstat(closed_descriptors[0])
        assert not claude_calls

    def test_removes_inherited_virtual_env(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """起動元ツールの仮想環境を`VIRTUAL_ENV`と`PATH`の双方から取り除いて子セッションへ渡す。

        `uv run`は`VIRTUAL_ENV`の設定と同時に当該環境のコマンド格納ディレクトリを`PATH`先頭へ挿入する。
        `VIRTUAL_ENV`だけを除いても`PATH`側が残ると`python`等の解決先が起動元ツールの環境のままになる。
        `PATH`の他要素と`AGENT_TOOLKIT_PROCESS_LOOP_SESSION`が残ることも同時に確認し、過剰除去を防ぐ。
        """
        _setup_notes(tmp_path)
        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()
        claude_calls: list[dict[str, Any]] = []
        venv_root = "/home/user/.cache/uv/environments-v2/atk-0123456789abcdef"
        monkeypatch.setenv("VIRTUAL_ENV", venv_root)
        monkeypatch.setenv("PATH", os.pathsep.join((f"{venv_root}/bin", f"{venv_root}/bin", "/usr/local/bin", "/usr/bin")))
        monkeypatch.setattr(subprocess, "run", _fake_run_with_remote_url(myrepo, claude_calls, 0))
        counts = iter((1, 0))

        def fake_count_pending_entries(private_notes: pathlib.Path, target_repo: str | None = None) -> int:
            del private_notes, target_repo
            return next(counts)

        def fake_wait_for_changes(private_notes: pathlib.Path, target_repo_id: str | None) -> NoReturn:
            del private_notes, target_repo_id
            raise KeyboardInterrupt

        monkeypatch.setattr(_process_loop, "_count_pending_entries", fake_count_pending_entries)
        monkeypatch.setattr(_process_loop, "_wait_for_changes", fake_wait_for_changes)

        with pytest.raises(SystemExit) as exc_info:
            atk.main(
                ["mq", "process-loop", f"--target-repo={myrepo}", "--no-update", "--no-alerts"],
                home=tmp_path,
            )

        assert exc_info.value.code == 0
        assert len(claude_calls) == 1
        assert "VIRTUAL_ENV" not in claude_calls[0]["env"]
        assert claude_calls[0]["env"]["PATH"] == os.pathsep.join(("/usr/local/bin", "/usr/bin"))
        assert claude_calls[0]["env"][_PROCESS_LOOP_SESSION_ENV] == "1"
        assert claude_calls[0]["env"][_LEGACY_PROCESS_LOOP_SESSION_ENV] == "1"

    def test_empty_path_entries_are_preserved(self) -> None:
        """`PATH`の空要素を除去対象に含めないこと。

        POSIXの`PATH`では空要素がカレントディレクトリを表すため、除去すると解決順序が宣言外に変わる。
        """
        venv_root = "/tmp/venv"
        env = {
            "VIRTUAL_ENV": venv_root,
            "PATH": os.pathsep.join((f"{venv_root}/bin", "", "/usr/bin", "")),
        }
        _process_loop._strip_inherited_venv(env)  # pylint: disable=protected-access  # noqa: SLF001
        assert env["PATH"] == os.pathsep.join(("", "/usr/bin", ""))

    def test_prompt_is_short_goal_with_workflow_boundary(self) -> None:
        """新規セッションの目的文が対象と起動スキルだけを伝えること。"""
        prompt = _process_loop._build_process_loop_prompt(  # pylint: disable=protected-access  # noqa: SLF001
            pathlib.Path("/repo"),
            "github.com/example/repo",
            "claude",
        )
        assert prompt == (
            "/goal `agent-toolkit:process-feedbacks`を起動し、"
            f"`{pathlib.Path('/repo')}`で対象リポジトリ`github.com/example/repo`の"
            "フィードバック処理を完遂してください。"
        )

        forbidden_details = (
            "atk mq list",
            "atk mq show",
            "frontmatter",
            "worker",
            "レビュー",
            "commit",
            "push",
            "CI",
            "atk mq adopt",
            "session-review",
            "exit-session",
        )
        assert all(detail not in prompt for detail in forbidden_details)

    def test_prompt_references_process_feedbacks(self) -> None:
        """プロンプトが後続工程の集約先としてprocess-feedbacksスキルを参照すること。"""
        prompt = _process_loop._build_process_loop_prompt(  # pylint: disable=protected-access  # noqa: SLF001
            pathlib.Path("/repo"),
            "github.com/example/repo",
            "claude",
        )
        assert "agent-toolkit:process-feedbacks" in prompt

    def test_codex_prompt_declares_continuous_ready_processing(self) -> None:
        """Codexの目的文だけが追加`ready`項目の連続処理と終了判定を明示する。"""
        prompt = _process_loop._build_process_loop_prompt(  # pylint: disable=protected-access  # noqa: SLF001
            pathlib.Path("/repo"),
            "github.com/example/repo",
            "codex",
        )

        assert prompt.startswith("/goal ")
        assert prompt.count("/goal ") == 1
        assert "開始後に追加されたready項目も同じセッションで順次処理" in prompt
        assert "ready項目がなくなった時点で既存の終了工程" in prompt
        assert "exit-session" not in prompt
        assert "/exit" not in prompt
        assert "Git" not in prompt
        assert "計画状態" not in prompt
        assert "session-review" not in prompt

    def test_prompt_includes_target_repo(self) -> None:
        """プロンプトが`--target-repo`限定指示と正規化リモートURLを本文へ含める。

        LLM起動プロンプトでcwd由来の暗黙解決を排除するため、target_repo_idを
        プロンプト本文へ明示埋め込みする。
        """
        target_repo_id = "github.com/example/repo"
        prompt = _process_loop._build_process_loop_prompt(  # pylint: disable=protected-access  # noqa: SLF001
            pathlib.Path("/repo"),
            target_repo_id,
            "claude",
        )
        assert target_repo_id in prompt
        assert "/repo" in prompt

    def test_dotfiles_prompt_declares_publish_destination_without_internal_command(self) -> None:
        """dotfiles固有worktreeだけに公開先を伝え、内部commandをpromptへ複製しない。"""
        prompt = _process_loop._build_process_loop_prompt(  # pylint: disable=protected-access  # noqa: SLF001
            pathlib.Path("/repo/.claude/worktrees/process-loop"),
            "github.com/ak110/dotfiles",
            "claude",
            publish_destination="origin/master",
        )

        assert "現在のHEADを`origin/master`へ反映" in prompt
        assert "git push" not in prompt
        assert "commit" not in prompt
        assert "レビュー" not in prompt

    def test_dotfiles_publish_destination_dry_run_targets_master(self, tmp_path: pathlib.Path) -> None:
        """branch名が異なるworktreeでも、明示した公開先がremoteのmasterへ到達する。"""
        remote = tmp_path / "remote.git"
        worktree = tmp_path / "worktree"
        subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True, text=True)
        subprocess.run(
            ["git", "init", "--initial-branch=worktree-process-loop", str(worktree)],
            check=True,
            capture_output=True,
            text=True,
        )
        subprocess.run(["git", "-C", str(worktree), "config", "user.name", "test"], check=True)
        subprocess.run(["git", "-C", str(worktree), "config", "user.email", "test@example.invalid"], check=True)
        (worktree / "tracked.txt").write_text("payload\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(worktree), "add", "tracked.txt"], check=True)
        subprocess.run(["git", "-C", str(worktree), "commit", "-m", "test"], check=True, capture_output=True, text=True)
        subprocess.run(["git", "-C", str(worktree), "remote", "add", "origin", str(remote)], check=True)
        subprocess.run(["git", "-C", str(worktree), "config", "push.default", "simple"], check=True)
        subprocess.run(
            ["git", "-C", str(worktree), "config", "branch.worktree-process-loop.remote", "origin"],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(worktree), "config", "branch.worktree-process-loop.merge", "refs/heads/master"],
            check=True,
        )

        implicit = subprocess.run(
            ["git", "-C", str(worktree), "push", "--dry-run", "--porcelain"],
            check=False,
            capture_output=True,
            text=True,
        )
        explicit = subprocess.run(
            ["git", "-C", str(worktree), "push", "--dry-run", "--porcelain", "origin", "HEAD:master"],
            check=False,
            capture_output=True,
            text=True,
        )

        assert implicit.returncode != 0
        assert explicit.returncode == 0
        assert "HEAD:refs/heads/master" in explicit.stdout

    @pytest.mark.parametrize(
        ("config_value", "expected_model", "expected_effort"),
        [("claude:sonnet/high", "sonnet", "high"), ("claude:sonnet", "sonnet", "medium")],
    )
    def test_configured_model_and_effort_are_passed_to_claude(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
        config_value: str,
        expected_model: str,
        expected_effort: str,
    ) -> None:
        """`orchestrate_model`設定のmodelとeffortがClaude起動コマンドへ反映される。"""
        _setup_notes(tmp_path)
        _set_orchestrate_model(tmp_path, config_value)
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
                ["mq", "process-loop", f"--target-repo={myrepo}", "--no-update"],
                home=tmp_path,
            )

        assert len(claude_calls) == 1
        command = claude_calls[0]["cmd"]
        _hook_debug_log(command)
        assert command[4:11] == [
            "--settings",
            '{"askUserQuestionTimeout": "5m"}',
            "--permission-mode=auto",
            "--model",
            expected_model,
            "--effort",
            expected_effort,
        ]

    def test_invalid_saved_orchestrate_model_exits_before_starting_session(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """保存済み設定の書式不正はセッションを起動せずexit 2とする。"""
        _setup_notes(tmp_path)
        config_file = tmp_path / "config" / "config.json"
        config_file.parent.mkdir(parents=True)
        config_file.write_text('{"orchestrate_model": "invalid"}\n', encoding="utf-8")
        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()
        session_calls: list[list[str]] = []

        def fail_if_started(cmd: list[str], *_args: object, **_kwargs: object) -> subprocess.CompletedProcess[Any]:
            session_calls.append(cmd)
            raise AssertionError("不正な設定ではセッションを起動しないこと")

        monkeypatch.setattr(subprocess, "run", fail_if_started)

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "process-loop", f"--target-repo={myrepo}"], home=tmp_path)

        assert exc_info.value.code == 2
        assert not session_calls
        stderr = capsys.readouterr().err
        assert "現在の設定値: invalid" in stderr
        assert "atk config set orchestrate_model claude:opus[1m]/medium" in stderr

    def test_resume_applied_to_first_session_only(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """`--resume`指定時は初回だけ再開指定のみで起動する。"""
        _setup_notes(tmp_path)
        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()
        claude_calls: list[dict[str, Any]] = []
        monkeypatch.setattr(subprocess, "run", _fake_run_with_remote_url(myrepo, claude_calls, 0))
        counts = iter([1, 1, 0])
        monkeypatch.setattr(_process_loop, "_count_pending_entries", lambda *_a, **_kw: next(counts))

        def fake_wait_for_changes(*_args: object, **_kwargs: object) -> None:
            raise KeyboardInterrupt

        monkeypatch.setattr(_process_loop, "_wait_for_changes", fake_wait_for_changes)

        with pytest.raises(SystemExit):
            atk.main(
                ["mq", "process-loop", f"--target-repo={myrepo}", "--no-update", "--resume"],
                home=tmp_path,
            )

        assert len(claude_calls) == 2
        first_command = claude_calls[0]["cmd"]
        second_command = claude_calls[1]["cmd"]
        first_debug_log = _hook_debug_log(first_command)
        second_debug_log = _hook_debug_log(second_command)
        assert first_debug_log != second_debug_log
        assert first_command[4:] == ["--settings", '{"askUserQuestionTimeout": "5m"}', "--resume"]
        assert "--model" not in first_command
        assert "--effort" not in first_command
        assert second_command[4:11] == [
            "--settings",
            '{"askUserQuestionTimeout": "5m"}',
            "--permission-mode=auto",
            "--model",
            "opus[1m]",
            "--effort",
            "medium",
        ]
        assert "--resume" not in second_command
        assert "--continue" not in second_command
        assert second_command[-1].startswith("/goal ")

    @pytest.mark.parametrize("resume_argv", [["--resume=session-id"], ["--resume", "session-id"]])
    def test_resume_session_id_is_normalized(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
        resume_argv: list[str],
    ) -> None:
        """空白区切りと等号区切りのセッションIDをClaudeの等号区切りへ正規化する。"""
        _setup_notes(tmp_path)
        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()
        claude_calls: list[dict[str, Any]] = []
        monkeypatch.setattr(subprocess, "run", _fake_run_with_remote_url(myrepo, claude_calls, 0))
        counts = iter([1, 1, 0])
        monkeypatch.setattr(_process_loop, "_count_pending_entries", lambda *_a, **_kw: next(counts))

        def fake_wait_for_changes(*_args: object, **_kwargs: object) -> None:
            raise KeyboardInterrupt

        monkeypatch.setattr(_process_loop, "_wait_for_changes", fake_wait_for_changes)

        with pytest.raises(SystemExit):
            atk.main(
                ["mq", "process-loop", f"--target-repo={myrepo}", "--no-update", "--no-alerts", *resume_argv],
                home=tmp_path,
            )

        assert len(claude_calls) == 2
        first_command = claude_calls[0]["cmd"]
        second_command = claude_calls[1]["cmd"]
        assert _hook_debug_log(first_command) != _hook_debug_log(second_command)
        assert first_command[4:] == ["--settings", '{"askUserQuestionTimeout": "5m"}', "--resume=session-id"]
        assert second_command[4:11] == [
            "--settings",
            '{"askUserQuestionTimeout": "5m"}',
            "--permission-mode=auto",
            "--model",
            "opus[1m]",
            "--effort",
            "medium",
        ]
        assert second_command[-1].startswith("/goal ")

    def test_dotfiles_resume_defers_worktree_until_next_session(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """dotfilesの初回再開ではworktree同期を行わず、後続の新規起動時に行う。"""
        _setup_notes(tmp_path)
        myrepo = tmp_path / "dotfiles"
        myrepo.mkdir()
        claude_calls: list[dict[str, Any]] = []
        base_fake_run = _fake_run_with_remote_url(myrepo, claude_calls, 0)

        def fake_run(cmd: list[str], *_args: object, **kwargs: object) -> subprocess.CompletedProcess[Any]:
            if cmd == ["git", "-C", str(myrepo), "remote", "get-url", "origin"]:
                stdout: Any = (
                    "https://github.com/ak110/dotfiles.git\n"
                    if kwargs.get("text")
                    else b"https://github.com/ak110/dotfiles.git\n"
                )
                return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="" if kwargs.get("text") else b"")
            return base_fake_run(cmd, *_args, **kwargs)

        monkeypatch.setattr(subprocess, "run", fake_run)
        counts = iter([1, 1, 0])
        monkeypatch.setattr(_process_loop, "_count_pending_entries", lambda *_a, **_kw: next(counts))

        def fake_wait_for_changes(*_args: object, **_kwargs: object) -> None:
            raise KeyboardInterrupt

        monkeypatch.setattr(_process_loop, "_wait_for_changes", fake_wait_for_changes)
        sync_calls: list[tuple[pathlib.Path, str]] = []

        def fake_sync_worktree(local_path: pathlib.Path, worktree_name: str) -> tuple[pathlib.Path, str]:
            sync_calls.append((local_path, worktree_name))
            return local_path / ".claude" / "worktrees" / worktree_name, "origin/master"

        monkeypatch.setattr(_process_loop, "_sync_worktree_with_upstream", fake_sync_worktree)

        with pytest.raises(SystemExit):
            atk.main(
                [
                    "mq",
                    "process-loop",
                    f"--target-repo={myrepo}",
                    "--no-update",
                    "--no-alerts",
                    "--worktree=custom",
                    "--resume",
                ],
                home=tmp_path,
            )

        assert _hook_debug_log(claude_calls[0]["cmd"]) != _hook_debug_log(claude_calls[1]["cmd"])
        assert claude_calls[0]["cmd"][4:] == ["--settings", '{"askUserQuestionTimeout": "5m"}', "--resume"]
        assert "--worktree=process-loop" not in claude_calls[1]["cmd"]
        assert claude_calls[1]["cwd"] == myrepo / ".claude" / "worktrees" / "custom"
        assert sync_calls == [(myrepo, "custom")]

    def test_resume_absent_without_option(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """`--resume`未指定時はClaudeセッションを継続しない。"""
        _setup_notes(tmp_path)
        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()
        claude_calls: list[dict[str, Any]] = []
        monkeypatch.setattr(subprocess, "run", _fake_run_with_remote_url(myrepo, claude_calls, 0))
        counts = iter([1, 0])
        monkeypatch.setattr(_process_loop, "_count_pending_entries", lambda *_a, **_kw: next(counts))

        def fake_wait_for_changes(*_args: object, **_kwargs: object) -> None:
            raise KeyboardInterrupt

        monkeypatch.setattr(_process_loop, "_wait_for_changes", fake_wait_for_changes)

        with pytest.raises(SystemExit):
            atk.main(
                ["mq", "process-loop", f"--target-repo={myrepo}", "--no-update"],
                home=tmp_path,
            )

        assert len(claude_calls) == 1
        _hook_debug_log(claude_calls[0]["cmd"])
        assert "--continue" not in claude_calls[0]["cmd"]
        assert "--resume" not in claude_calls[0]["cmd"]

    @pytest.mark.parametrize(
        ("config_value", "expected_model", "expected_effort"),
        [("codex:gpt-5.6-sol/medium", "gpt-5.6-sol", "medium"), ("codex:gpt-5.6-sol/high", "gpt-5.6-sol", "high")],
    )
    def test_codex_new_session_uses_interactive_cli(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
        config_value: str,
        expected_model: str,
        expected_effort: str,
    ) -> None:
        """Codex新規起動は設定値を対話CLIへ渡し、標準入出力を捕捉しない。"""
        _setup_notes(tmp_path)
        _set_orchestrate_model(tmp_path, config_value)
        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()
        codex_calls: list[dict[str, Any]] = []
        monkeypatch.setattr(subprocess, "run", _fake_run_with_remote_url(myrepo, codex_calls, 0))
        counts = iter((1, 0))
        monkeypatch.setattr(_process_loop, "_count_pending_entries", lambda *_a, **_kw: next(counts))

        def fake_wait_for_changes(*_args: object, **_kwargs: object) -> NoReturn:
            raise KeyboardInterrupt

        monkeypatch.setattr(_process_loop, "_wait_for_changes", fake_wait_for_changes)

        with pytest.raises(SystemExit) as exc_info:
            atk.main(
                [
                    "mq",
                    "process-loop",
                    f"--target-repo={myrepo}",
                    "--no-update",
                    "--no-alerts",
                ],
                home=tmp_path,
            )

        assert exc_info.value.code == 0
        assert len(codex_calls) == 1
        call = codex_calls[0]
        command = call["cmd"]
        assert command[0] == "codex"
        assert "--approve-for-me" not in command
        assert command[1:5] == ["--model", expected_model, "-c", f"model_reasoning_effort={expected_effort}"]
        assert command[-1].startswith("/goal ")
        assert command[-1].count("/goal ") == 1
        assert "exec" not in command
        assert "--debug=hooks" not in command
        assert "--autocompact" not in command
        assert call["cwd"] == myrepo
        assert all(key not in call["kwargs"] for key in ("stdin", "stdout", "stderr", "capture_output"))
        expected_flags = 0x00000200 if os.name == "nt" else 0
        assert call["kwargs"]["creationflags"] == expected_flags

    @pytest.mark.parametrize(
        ("resume_argv", "expected_tail"),
        [
            (["--resume"], []),
            (["--resume", "session-id"], ["session-id"]),
        ],
    )
    def test_codex_resume_uses_interactive_cli_without_prompt(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
        resume_argv: list[str],
        expected_tail: list[str],
    ) -> None:
        """Codex再開は対話の選択画面又はIDを使い、新しい目的文を渡さない。"""
        _setup_notes(tmp_path)
        _set_orchestrate_model(tmp_path, "codex:gpt-5.6-sol/high")
        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()
        codex_calls: list[dict[str, Any]] = []
        monkeypatch.setattr(subprocess, "run", _fake_run_with_remote_url(myrepo, codex_calls, 0))
        counts = iter((1, 0))
        monkeypatch.setattr(_process_loop, "_count_pending_entries", lambda *_a, **_kw: next(counts))

        def fake_wait_for_changes(*_args: object, **_kwargs: object) -> NoReturn:
            raise KeyboardInterrupt

        monkeypatch.setattr(_process_loop, "_wait_for_changes", fake_wait_for_changes)

        with pytest.raises(SystemExit):
            atk.main(
                [
                    "mq",
                    "process-loop",
                    f"--target-repo={myrepo}",
                    "--no-update",
                    "--no-alerts",
                    *resume_argv,
                ],
                home=tmp_path,
            )

        assert len(codex_calls) == 1
        command = codex_calls[0]["cmd"]
        assert command == [
            "codex",
            "resume",
            "--model",
            "gpt-5.6-sol",
            "-c",
            "model_reasoning_effort=high",
            *expected_tail,
        ]
        assert all(not arg.startswith("/goal ") for arg in command)
        assert "exec" not in command


class TestCodexWindowsSessionConfiguration:
    """Windows Codexだけに適用するプロセスグループとhook互換PATHを検証する。"""

    def test_windows_codex_uses_new_process_group(self) -> None:
        """Windows Codexだけが親と別のコンソール制御グループで起動すること。"""
        assert _process_loop._session_creation_flags("codex", platform="nt") == 0x00000200  # pylint: disable=protected-access  # noqa: SLF001
        assert _process_loop._session_creation_flags("claude", platform="nt") == 0  # pylint: disable=protected-access  # noqa: SLF001
        assert _process_loop._session_creation_flags("codex", platform="posix") == 0  # pylint: disable=protected-access  # noqa: SLF001

    def test_windows_codex_alone_prepends_bash_shim(self) -> None:
        """共通環境を変更せず、Windows Codex用コピーだけへshimを追加すること。"""
        inherited = {"PATH": os.pathsep.join(("first", "second")), "KEEP": "value"}

        codex_env = _process_loop._session_env(inherited, "codex", platform="nt")  # pylint: disable=protected-access  # noqa: SLF001
        claude_env = _process_loop._session_env(inherited, "claude", platform="nt")  # pylint: disable=protected-access  # noqa: SLF001
        posix_env = _process_loop._session_env(inherited, "codex", platform="posix")  # pylint: disable=protected-access  # noqa: SLF001

        shim_dir = pathlib.Path(_process_loop.__file__).resolve().parent / "windows-shims"
        assert pathlib.Path(codex_env["PATH"].split(os.pathsep, maxsplit=1)[0]) == shim_dir
        assert claude_env == inherited
        assert posix_env == inherited
        assert inherited["PATH"] == os.pathsep.join(("first", "second"))

    @pytest.mark.skipif(os.name != "nt", reason="Windows用cmd shimの実機検証")
    def test_bash_shim_converts_windows_script_path_and_preserves_stdin_and_exit_code(
        self,
        tmp_path: pathlib.Path,
    ) -> None:
        """Git BashへWindowsパスを渡し、hookの入出力契約を保持すること。"""
        script = tmp_path / "hook.sh"
        script.write_text(
            "read -r value || exit 31\n"
            '[ "$value" = "ok" ] || exit 32\n'
            '[ "$#" -eq 12 ] || exit 33\n'
            '[ "${10}" = "ten value" ] || exit 34\n'
            "exit 23\n",
            encoding="utf-8",
        )
        shim = pathlib.Path(_process_loop.__file__).resolve().parent / "windows-shims" / "bash.cmd"
        env = os.environ.copy()
        env["PATH"] = os.pathsep.join((str(shim.parent), env.get("PATH", "")))

        result = subprocess.run(
            [
                "cmd.exe",
                "/d",
                "/c",
                "bash",
                str(script),
                "one",
                "two",
                "three",
                "four",
                "five",
                "six",
                "seven",
                "eight",
                "nine",
                "ten value",
                "eleven",
                "twelve",
            ],
            input=b"ok\n",
            text=False,
            check=False,
            env=env,
        )

        assert result.returncode == 23


class TestProcessLoopSessionPreparation:
    """ready項目の処理前同期と失敗時のfail-closed動作を検証する。"""

    def test_private_notes_pull_runs_under_repo_lock(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """private-notesのpullが同一リポジトリのlock保持中だけ実行されること。"""
        events: list[str] = []

        @contextlib.contextmanager
        def fake_lock(path: pathlib.Path) -> Iterator[None]:
            assert path == tmp_path
            events.append("lock-enter")
            yield
            events.append("lock-exit")

        monkeypatch.setattr(_process_loop, "_repo_lock", fake_lock)
        monkeypatch.setattr(_process_loop, "_pull", lambda path: events.append(f"pull:{path.name}"))

        assert _PULL_PRIVATE_NOTES_IMPL(tmp_path)
        assert events == ["lock-enter", f"pull:{tmp_path.name}", "lock-exit"]

    @pytest.mark.parametrize("failure", ["missing", "exit"])
    def test_update_failure_does_not_repull_or_start_session(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        failure: str,
    ) -> None:
        """更新コマンドの未解決又は失敗時は再pullへ進まずFalseを返すこと。"""
        monkeypatch.setattr(
            _process_loop,
            "_resolve_executable",
            lambda _name: None if failure == "missing" else "update-dotfiles",
        )
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda cmd, **_kwargs: subprocess.CompletedProcess(cmd, 1),
        )
        monkeypatch.setattr(
            _process_loop,
            "_pull_private_notes",
            lambda _path: pytest.fail("更新失敗後は再pullしないこと"),
        )

        assert _process_loop._update_before_session(  # pylint: disable=protected-access  # noqa: SLF001
            tmp_path,
            None,
            None,
            ["atk"],
            {},
        ) == (False, False)

    def test_initial_ready_session_runs_pull_update_repull_before_codex(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """初回ready処理はpull・update・再pull・再集計後にCodexを起動すること。"""
        _setup_notes(tmp_path)
        _set_orchestrate_model(tmp_path, "codex:gpt-5.6-sol/medium")
        myrepo = tmp_path / "repo"
        myrepo.mkdir()
        events: list[str] = []
        counts = iter((1, 1))

        def fake_pull(_path: pathlib.Path) -> bool:
            events.append("pull")
            return True

        def fake_count(*_args: object, **_kwargs: object) -> int:
            events.append("count")
            return next(counts)

        def fake_update(*_args: object, **_kwargs: object) -> tuple[bool, bool]:
            events.extend(("update", "repull"))
            return True, True

        monkeypatch.setattr(_process_loop, "_pull_private_notes", fake_pull)
        monkeypatch.setattr(_process_loop, "_count_pending_entries", fake_count)
        monkeypatch.setattr(_process_loop, "_update_before_session", fake_update)
        base_fake_run = _fake_run_with_remote_url(myrepo, [], 7)

        def fake_run(cmd: list[str], *_args: object, **kwargs: object) -> subprocess.CompletedProcess[Any]:
            if cmd[:1] == ["codex"]:
                events.append("session")
            return base_fake_run(cmd, *_args, **kwargs)

        monkeypatch.setattr(subprocess, "run", fake_run)

        with pytest.raises(SystemExit) as exc_info:
            atk.main(
                [
                    "mq",
                    "process-loop",
                    f"--target-repo={myrepo}",
                    "--no-alerts",
                ],
                home=tmp_path,
            )

        assert exc_info.value.code == 7
        assert events == ["pull", "count", "update", "repull", "count", "session"]

    def test_worktree_preparation_failure_returns_to_wait(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """dotfiles用worktreeを準備できない場合はプロセスを異常終了せず待機へ戻る。"""
        _setup_notes(tmp_path)
        myrepo = tmp_path / "dotfiles"
        myrepo.mkdir()
        session_calls: list[dict[str, Any]] = []
        monkeypatch.setattr(subprocess, "run", _fake_run_with_remote_url(myrepo, session_calls, 0))
        monkeypatch.setattr(_process_loop, "_resolve_local_worktree", lambda _value: myrepo)
        monkeypatch.setattr(_process_loop, "_resolve_repo_id", lambda *_args, **_kwargs: _DOTFILES_REPO_ID)
        monkeypatch.setattr(_process_loop, "_count_pending_entries", lambda *_args, **_kwargs: 1)
        monkeypatch.setattr(_process_loop, "_sync_worktree_with_upstream", lambda *_args: None)
        wait_calls: list[pathlib.Path] = []

        def stop_wait(private_notes: pathlib.Path, _target_repo_id: str | None) -> None:
            wait_calls.append(private_notes)
            raise KeyboardInterrupt

        monkeypatch.setattr(_process_loop, "_wait_for_changes", stop_wait)

        with pytest.raises(SystemExit) as exc_info:
            atk.main(
                ["mq", "process-loop", f"--target-repo={myrepo}", "--no-update", "--no-alerts"],
                home=tmp_path,
            )

        assert exc_info.value.code == 0
        assert wait_calls == [tmp_path / "private-notes"]
        assert not session_calls
        assert "worktree準備を再試行するまで変更検知を待機します。" in capsys.readouterr().out


@pytest.mark.parametrize("deprecated_option", ["--orchestrator=claude", "--model=opus"])
def test_process_loop_rejects_removed_options(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    deprecated_option: str,
) -> None:
    """廃止したオーケストレーター・モデル指定を公開CLI境界でexit 2にする。"""
    handler_calls: list[object] = []
    monkeypatch.setattr(_process_loop, "_cmd_process_loop", lambda *_args: handler_calls.append(True))
    with pytest.raises(SystemExit) as exc_info:
        atk.main(["mq", "process-loop", deprecated_option], home=tmp_path)
    assert exc_info.value.code == 2
    assert not handler_calls


def test_process_loop_internal_mise_refreshed_contract(capsys: pytest.CaptureFixture[str]) -> None:
    """内部専用オプションの既定値・指定時の値・help非露出をargparse境界で固定する。"""
    parser = atk._build_parser()  # pylint: disable=protected-access  # noqa: SLF001
    assert parser.parse_args(["mq", "process-loop"]).internal_mise_refreshed is False
    assert parser.parse_args(["mq", "process-loop", "--internal-mise-refreshed"]).internal_mise_refreshed is True
    with pytest.raises(SystemExit) as help_exit:
        parser.parse_args(["mq", "process-loop", "--help"])
    assert help_exit.value.code == 0
    assert "--internal-mise-refreshed" not in capsys.readouterr().out


def test_process_loop_worktree_option_reaches_public_handler(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """公開CLIのworktree指定がprocess-loopハンドラへ伝わること。"""
    _setup_notes(tmp_path)
    received: list[str | None] = []

    def fake_process_loop(args: Any, _private_notes: pathlib.Path) -> NoReturn:
        received.append(args.worktree)
        raise SystemExit(0)

    monkeypatch.setattr(_process_loop, "_cmd_process_loop", fake_process_loop)
    for extra_argv, expected in (([], None), (["--worktree"], "process-loop"), (["--worktree=custom"], "custom")):
        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "process-loop", *extra_argv], home=tmp_path)
        assert exc_info.value.code == 0
        assert received[-1] == expected


@pytest.mark.parametrize("value", ["/tmp/worktree", "name/../other", "", ".hidden", "-leading"])
def test_process_loop_rejects_invalid_worktree_name(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    value: str,
) -> None:
    """公開CLIが配置先を逸脱するworktree名を拒否すること。"""
    called = False

    def fake_process_loop(_args: Any, _private_notes: pathlib.Path) -> NoReturn:
        nonlocal called
        called = True
        raise SystemExit(0)

    monkeypatch.setattr(_process_loop, "_cmd_process_loop", fake_process_loop)
    with pytest.raises(SystemExit) as exc_info:
        atk.main(["mq", "process-loop", f"--worktree={value}"], home=tmp_path)
    assert exc_info.value.code == 2
    assert not called


class TestProcessLoopWorktreeOption:
    """公開CLIのworktree指定とセッションの実行先を検証する。"""

    @pytest.mark.parametrize(
        ("worktree_argv", "expected_name"),
        [(["--worktree"], "process-loop"), (["--worktree=custom"], "custom")],
    )
    def test_non_dotfiles_worktree_option_changes_session_cwd_and_prompt(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        worktree_argv: list[str],
        expected_name: str,
    ) -> None:
        """非dotfiles対象でもworktree指定が同期先・cwd・公開先文へ反映されること。"""
        _setup_notes(tmp_path)
        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()
        claude_calls: list[dict[str, Any]] = []
        monkeypatch.setattr(subprocess, "run", _fake_run_with_remote_url(myrepo, claude_calls, 0))
        counts = iter((1, 0))
        monkeypatch.setattr(_process_loop, "_count_pending_entries", lambda *_a, **_kw: next(counts))

        def stop_wait(*_args: object, **_kwargs: object) -> NoReturn:
            raise KeyboardInterrupt

        monkeypatch.setattr(_process_loop, "_wait_for_changes", stop_wait)
        worktree_path = myrepo / ".claude" / "worktrees" / expected_name
        sync_calls: list[tuple[pathlib.Path, str]] = []

        def fake_sync(local_path: pathlib.Path, worktree_name: str) -> tuple[pathlib.Path, str]:
            sync_calls.append((local_path, worktree_name))
            return worktree_path, "origin/main"

        monkeypatch.setattr(_process_loop, "_sync_worktree_with_upstream", fake_sync)

        with pytest.raises(SystemExit) as exc_info:
            atk.main(
                ["mq", "process-loop", f"--target-repo={myrepo}", "--no-update", "--no-alerts", *worktree_argv],
                home=tmp_path,
            )

        assert exc_info.value.code == 0
        assert sync_calls == [(myrepo, expected_name)]
        assert len(claude_calls) == 1
        assert claude_calls[0]["cwd"] == worktree_path
        assert "現在のHEADを`origin/main`へ反映" in claude_calls[0]["cmd"][-1]
        assert "--worktree=" not in " ".join(claude_calls[0]["cmd"])

    def test_dotfiles_worktree_name_can_be_overridden(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """dotfilesの自動worktree名が明示指定だけで上書きされること。"""
        _setup_notes(tmp_path)
        myrepo = tmp_path / "dotfiles"
        myrepo.mkdir()
        claude_calls: list[dict[str, Any]] = []
        base_fake_run = _fake_run_with_remote_url(myrepo, claude_calls, 0)

        def fake_run(cmd: list[str], *_args: object, **kwargs: object) -> subprocess.CompletedProcess[Any]:
            if cmd == ["git", "-C", str(myrepo), "remote", "get-url", "origin"]:
                empty: Any = (
                    "https://github.com/ak110/dotfiles.git\n"
                    if kwargs.get("text")
                    else b"https://github.com/ak110/dotfiles.git\n"
                )
                return subprocess.CompletedProcess(cmd, 0, empty, "" if kwargs.get("text") else b"")
            return base_fake_run(cmd, *_args, **kwargs)

        monkeypatch.setattr(subprocess, "run", fake_run)
        counts = iter((1, 0))
        monkeypatch.setattr(_process_loop, "_count_pending_entries", lambda *_a, **_kw: next(counts))
        monkeypatch.setattr(_process_loop, "_wait_for_changes", lambda *_a, **_kw: (_ for _ in ()).throw(KeyboardInterrupt))
        worktree_path = myrepo / ".claude" / "worktrees" / "custom"
        sync_calls: list[tuple[pathlib.Path, str]] = []

        def fake_sync(local_path: pathlib.Path, worktree_name: str) -> tuple[pathlib.Path, str]:
            sync_calls.append((local_path, worktree_name))
            return worktree_path, "origin/master"

        monkeypatch.setattr(
            _process_loop,
            "_sync_worktree_with_upstream",
            fake_sync,
        )

        with pytest.raises(SystemExit):
            atk.main(
                ["mq", "process-loop", f"--target-repo={myrepo}", "--no-update", "--no-alerts", "--worktree=custom"],
                home=tmp_path,
            )

        assert sync_calls == [(myrepo, "custom")]
        assert claude_calls[0]["cwd"] == worktree_path
        assert "現在のHEADを`origin/master`へ反映" in claude_calls[0]["cmd"][-1]


class TestMiseLatestRefresh:
    """dotfiles向けprocess-loopが所有するmise latest再評価を検証する。"""

    @staticmethod
    def _run_idle_loop(
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        *,
        extra_argv: list[str],
        wait_results: list[bool | BaseException],
        monotonic_values: list[float] | None = None,
    ) -> list[pathlib.Path]:
        _setup_notes(tmp_path)
        target = tmp_path / "dotfiles-target"
        target.mkdir()
        dotfiles_root = tmp_path / "dotfiles-root"
        dotfiles_root.mkdir()
        monkeypatch.setattr(_process_loop, "_resolve_local_worktree", lambda _value: target)
        monkeypatch.setattr(_process_loop, "_resolve_repo_id", lambda *_args, **_kwargs: _DOTFILES_REPO_ID)
        monkeypatch.setattr(_process_loop, "_resolve_dotfiles_root", lambda: dotfiles_root)
        monkeypatch.setattr(_process_loop, "_count_pending_entries", lambda *_args, **_kwargs: 0)
        refresh_calls: list[pathlib.Path] = []

        def fake_refresh(root: pathlib.Path) -> bool:
            refresh_calls.append(root)
            return True

        monkeypatch.setattr(_process_loop, "_refresh_mise_tools", fake_refresh)
        waits = iter(wait_results)

        def fake_wait(*_args: object, **_kwargs: object) -> bool:
            result = next(waits)
            if isinstance(result, BaseException):
                raise result
            return result

        monkeypatch.setattr(_process_loop, "_wait_for_changes", fake_wait)
        if monotonic_values is not None:
            times = iter(monotonic_values)
            monkeypatch.setattr(_process_loop.time, "monotonic", lambda: next(times))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(
                ["mq", "process-loop", f"--target-repo={target}", "--no-alerts", *extra_argv],
                home=tmp_path,
            )

        assert exc_info.value.code == 0
        return refresh_calls

    @pytest.mark.parametrize(
        ("extra_argv", "expected_calls"),
        [
            ([], 1),
            (["--internal-mise-refreshed"], 0),
            (["--no-update"], 0),
        ],
    )
    def test_startup_refresh_contract(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        extra_argv: list[str],
        expected_calls: int,
    ) -> None:
        """手動起動、正常再起動済み、更新無効の各起動契機を区別する。"""
        calls = self._run_idle_loop(
            monkeypatch,
            tmp_path,
            extra_argv=extra_argv,
            wait_results=[KeyboardInterrupt()],
            monotonic_values=[0.0] if expected_calls or "--internal-mise-refreshed" in extra_argv else None,
        )

        assert len(calls) == expected_calls

    def test_wait_loop_refreshes_at_24_hours_but_not_before(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """期限前の待機復帰では省き、24時間到達時に一度だけ再評価する。"""
        dotfiles_root = tmp_path / "dotfiles-root"
        calls = self._run_idle_loop(
            monkeypatch,
            tmp_path,
            extra_argv=[],
            wait_results=[True, True, KeyboardInterrupt()],
            monotonic_values=[0.0, 86399.0, 86400.0, 86401.0],
        )

        assert calls == [dotfiles_root, dotfiles_root]

    def test_successful_update_without_restart_resets_deadline(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """update-dotfiles成功後は起動時刻ではなく成功後から次の24時間を数える。"""
        _setup_notes(tmp_path)
        target = tmp_path / "dotfiles-target"
        target.mkdir()
        dotfiles_root = tmp_path / "dotfiles-root"
        dotfiles_root.mkdir()
        monkeypatch.setattr(_process_loop, "_resolve_local_worktree", lambda _value: target)
        monkeypatch.setattr(_process_loop, "_resolve_repo_id", lambda *_args, **_kwargs: _DOTFILES_REPO_ID)
        monkeypatch.setattr(_process_loop, "_resolve_dotfiles_root", lambda: dotfiles_root)
        counts = iter((1, 0, 0))
        monkeypatch.setattr(_process_loop, "_count_pending_entries", lambda *_args, **_kwargs: next(counts))
        monkeypatch.setattr(_process_loop, "_update_before_session", lambda *_args, **_kwargs: (False, True))
        refresh_calls: list[pathlib.Path] = []

        def fake_refresh(root: pathlib.Path) -> bool:
            refresh_calls.append(root)
            return True

        monkeypatch.setattr(_process_loop, "_refresh_mise_tools", fake_refresh)
        waits = iter((True, True, KeyboardInterrupt()))

        def fake_wait(*_args: object, **_kwargs: object) -> bool:
            result = next(waits)
            if isinstance(result, BaseException):
                raise result
            return result

        monkeypatch.setattr(_process_loop, "_wait_for_changes", fake_wait)
        times = iter((0.0, 100.0, 86450.0))
        monkeypatch.setattr(_process_loop.time, "monotonic", lambda: next(times))

        with pytest.raises(SystemExit):
            atk.main(["mq", "process-loop", f"--target-repo={target}", "--no-alerts"], home=tmp_path)

        assert refresh_calls == [dotfiles_root]

    def test_failed_update_without_restart_keeps_deadline(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """update-dotfiles失敗時は起動時から数えた24時間の期限を維持する。"""
        _setup_notes(tmp_path)
        target = tmp_path / "dotfiles-target"
        target.mkdir()
        dotfiles_root = tmp_path / "dotfiles-root"
        dotfiles_root.mkdir()
        monkeypatch.setattr(_process_loop, "_resolve_local_worktree", lambda _value: target)
        monkeypatch.setattr(_process_loop, "_resolve_repo_id", lambda *_args, **_kwargs: _DOTFILES_REPO_ID)
        monkeypatch.setattr(_process_loop, "_resolve_dotfiles_root", lambda: dotfiles_root)
        counts = iter((1, 0, 0))
        monkeypatch.setattr(_process_loop, "_count_pending_entries", lambda *_args, **_kwargs: next(counts))
        monkeypatch.setattr(_process_loop, "_update_before_session", lambda *_args, **_kwargs: (False, False))
        refresh_calls: list[pathlib.Path] = []

        def fake_refresh(root: pathlib.Path) -> bool:
            refresh_calls.append(root)
            return True

        monkeypatch.setattr(_process_loop, "_refresh_mise_tools", fake_refresh)
        waits = iter((True, True, KeyboardInterrupt()))

        def fake_wait(*_args: object, **_kwargs: object) -> bool:
            result = next(waits)
            if isinstance(result, BaseException):
                raise result
            return result

        monkeypatch.setattr(_process_loop, "_wait_for_changes", fake_wait)
        times = iter((0.0, 86400.0, 86400.0))
        monkeypatch.setattr(_process_loop.time, "monotonic", lambda: next(times))

        with pytest.raises(SystemExit):
            atk.main(["mq", "process-loop", f"--target-repo={target}", "--no-alerts"], home=tmp_path)

        assert refresh_calls == [dotfiles_root, dotfiles_root]

    @pytest.mark.parametrize(("update_returncode", "expected_marker"), [(0, True), (9, False)])
    def test_session_restart_marks_only_successful_update(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        update_returncode: int,
        expected_marker: bool,
    ) -> None:
        """セッション後更新の成否を次の起動に渡し、失敗時はmise再評価を省かない。"""
        _setup_notes(tmp_path)
        target = tmp_path / "dotfiles-target"
        target.mkdir()
        dotfiles_root = tmp_path / "dotfiles-root"
        dotfiles_root.mkdir()
        monkeypatch.setattr(_process_loop, "_resolve_local_worktree", lambda _value: target)
        monkeypatch.setattr(_process_loop, "_resolve_repo_id", lambda *_args, **_kwargs: _DOTFILES_REPO_ID)
        monkeypatch.setattr(_process_loop, "_resolve_dotfiles_root", lambda: dotfiles_root)
        monkeypatch.setattr(_process_loop, "_count_pending_entries", lambda *_args, **_kwargs: 1)
        monkeypatch.setattr(_process_loop, "_update_before_session", lambda *_args, **_kwargs: (True, True))
        monkeypatch.setattr(_process_loop, "_refresh_mise_tools", lambda _root: True)
        monkeypatch.setattr(_process_loop, "_sync_worktree_with_upstream", lambda *_args: (target, "origin/master"))
        monkeypatch.setattr(_process_loop.time, "monotonic", lambda: 0.0)

        def fake_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
            returncode = 0 if command[0] == "claude" else update_returncode
            return subprocess.CompletedProcess(command, returncode, "", "")

        monkeypatch.setattr(_process_loop.subprocess, "run", fake_run)
        restart_kwargs: list[dict[str, object]] = []

        def fake_restart(*_args: object, **kwargs: object) -> NoReturn:
            restart_kwargs.append(kwargs)
            raise SystemExit(0)

        monkeypatch.setattr(_process_loop, "_restart_process_loop", fake_restart)

        with pytest.raises(SystemExit):
            atk.main(["mq", "process-loop", f"--target-repo={target}", "--no-alerts"], home=tmp_path)

        assert restart_kwargs[-1]["mise_refreshed"] is expected_marker

    def test_mise_command_uses_dotfiles_root_and_quiet_mode(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """miseをdotfiles rootで静音実行し、長時間導入用の上限を指定する。"""
        calls: list[tuple[list[str], dict[str, object]]] = []

        def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            calls.append((command, kwargs))
            return subprocess.CompletedProcess(command, 0, "", "")

        monkeypatch.setattr(_process_loop.subprocess, "run", fake_run)

        assert _process_loop._refresh_mise_tools(tmp_path)  # pylint: disable=protected-access  # noqa: SLF001
        command, kwargs = calls[0]
        assert command == ["/resolved/mise", "install", "--quiet"]
        assert kwargs["cwd"] == tmp_path
        assert kwargs["timeout"] == 600
        assert kwargs["capture_output"] is True

    @pytest.mark.parametrize(("failure", "expected_detail"), [("nonzero", "exit code 7"), ("timeout", "途中出力")])
    def test_mise_failure_warns_and_returns_false(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
        failure: str,
        expected_detail: str,
    ) -> None:
        """0以外の終了とtimeoutを警告へ変換し、常駐処理へ失敗を送出しない。"""

        def fake_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
            if failure == "timeout":
                raise subprocess.TimeoutExpired(command, 600, stderr="途中出力")
            return subprocess.CompletedProcess(command, 7, "", "registry error")

        monkeypatch.setattr(_process_loop.subprocess, "run", fake_run)

        assert not _process_loop._refresh_mise_tools(tmp_path)  # pylint: disable=protected-access  # noqa: SLF001
        warning = capsys.readouterr().err
        assert "process-loopを継続します" in warning
        assert expected_detail in warning


class TestProcessLoopReturncode:
    """process-loopサブコマンドのオーケストレーター別returncode判定を検証する。"""

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
        _setup_notes(tmp_path)
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
            atk.main(["mq", "process-loop", f"--target-repo={myrepo}", "--no-update"], home=tmp_path)

        assert exc_info.value.code == 0
        assert len(claude_calls) == 1

    def test_abnormal_returncode_exits_with_same_code(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """`returncode`が正常集合外なら、CLI自体が同じexit codeで終了すること。"""
        _setup_notes(tmp_path)
        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()
        claude_calls: list[dict[str, Any]] = []

        monkeypatch.setattr(subprocess, "run", _fake_run_with_remote_url(myrepo, claude_calls, 42))
        monkeypatch.setattr(_process_loop, "_count_pending_entries", lambda *_a, **_kw: 1)

        def fake_wait_for_changes(*_a: object, **_kw: object) -> None:
            raise AssertionError("異常終了時は_wait_for_changesを呼ばないこと")

        monkeypatch.setattr(_process_loop, "_wait_for_changes", fake_wait_for_changes)

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "process-loop", f"--target-repo={myrepo}", "--no-update"], home=tmp_path)

        assert exc_info.value.code == 42
        captured = capsys.readouterr()
        assert "claudeがexit code 42で異常終了しました" in captured.err

    def test_codex_accepts_only_zero_returncode(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """CodexではClaude Code用SIGTERM終了コードを正常扱いしない。"""
        _setup_notes(tmp_path)
        _set_orchestrate_model(tmp_path, "codex:gpt-5.6-sol/medium")
        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()
        codex_calls: list[dict[str, Any]] = []
        monkeypatch.setattr(subprocess, "run", _fake_run_with_remote_url(myrepo, codex_calls, 15))
        counts = iter((1, 0))
        monkeypatch.setattr(_process_loop, "_count_pending_entries", lambda *_a, **_kw: next(counts))

        def fake_wait_for_changes(*_args: object, **_kwargs: object) -> NoReturn:
            raise KeyboardInterrupt

        monkeypatch.setattr(_process_loop, "_wait_for_changes", fake_wait_for_changes)

        with pytest.raises(SystemExit) as exc_info:
            atk.main(
                ["mq", "process-loop", f"--target-repo={myrepo}", "--no-update"],
                home=tmp_path,
            )

        assert exc_info.value.code == 15
        assert "codexがexit code 15で異常終了しました" in capsys.readouterr().err


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
        _setup_notes(tmp_path)
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

        def fake_execv(path: str, argv: list[str]) -> None:
            execv_calls.append((path, list(argv)))
            raise SystemExit(0)

        monkeypatch.setattr(os, "execv", fake_execv)
        with pytest.raises(SystemExit):
            atk.main(
                ["mq", "process-loop", "--target-repo", str(myrepo)],
                home=tmp_path,
            )
        assert execv_calls
        assert execv_calls[0][0] == "/resolved/uv"
        assert pathlib.Path(execv_calls[0][1][0]).name == "uv"
        assert execv_calls[0][1][1:4] == ["run", "--no-project", "--script"]
        assert _command_was_called(subprocess_calls, "update-dotfiles")
        captured = capsys.readouterr()
        assert "update-dotfilesを実行して" in captured.out
        # テスト実行環境（非TTY）ではコンソールタイトル制御文字を一切出力しないこと。
        assert "\033]2;" not in captured.out
        assert "\033]2;" not in captured.err

    def test_update_dotfiles_receives_stripped_env(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """セッション終了後の`update-dotfiles`起動へ、仮想環境を除去した環境を渡すこと。

        `update-dotfiles`は`chezmoi apply`を経て作業対象リポジトリのuvベースのパッケージ操作へ至るため、
        起動元ツールのエフェメラル仮想環境を引き継がせない。
        """
        myrepo = tmp_path / "repo"
        myrepo.mkdir()
        _setup_notes(tmp_path)
        venv_root = "/home/user/.cache/uv/environments-v2/atk-0123456789abcdef"
        monkeypatch.setenv("VIRTUAL_ENV", venv_root)
        monkeypatch.setenv("PATH", os.pathsep.join((f"{venv_root}/bin", "/usr/bin")))
        update_envs: list[Any] = []
        base_fake_run = _fake_run_with_remote_url(myrepo, [], 0)

        def fake_run(cmd: list[str], *_args: object, **kwargs: object) -> subprocess.CompletedProcess[Any]:
            if pathlib.Path(cmd[0]).stem.lower() == "update-dotfiles":
                update_envs.append(kwargs.get("env"))
            return base_fake_run(cmd, *_args, **kwargs)

        monkeypatch.setattr(subprocess, "run", fake_run)
        monkeypatch.setattr(_process_loop, "_count_pending_entries", lambda *_a, **_kw: 1)
        monkeypatch.setattr(os, "execv", _raise_system_exit_0)

        with pytest.raises(SystemExit):
            atk.main(["mq", "process-loop", "--target-repo", str(myrepo)], home=tmp_path)

        assert len(update_envs) == 2
        for child_env in update_envs:
            assert child_env is not None
            assert "VIRTUAL_ENV" not in child_env
            assert child_env["PATH"] == "/usr/bin"

    def test_wait_loop_update_dotfiles_receives_stripped_env(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """待機ループ復帰時の`update-dotfiles`起動へも、仮想環境を除去した環境を渡すこと。"""
        venv_root = "/home/user/.cache/uv/environments-v2/atk-0123456789abcdef"
        monkeypatch.setenv("VIRTUAL_ENV", venv_root)
        monkeypatch.setenv("PATH", os.pathsep.join((f"{venv_root}/bin", "/usr/bin")))
        update_envs: list[Any] = []

        def fake_run(cmd: list[str], *_a: object, **kwargs: object) -> subprocess.CompletedProcess[Any]:
            if pathlib.Path(cmd[0]).stem.lower() == "update-dotfiles":
                update_envs.append(kwargs.get("env"))
            stdout = "1\n" if "rev-list" in cmd else ""
            return subprocess.CompletedProcess(cmd, 0, stdout, "")

        monkeypatch.setattr(subprocess, "run", fake_run)
        monkeypatch.setattr(_process_loop, "_code_hash", lambda _d: "same-hash")  # 再起動へ進ませない
        _process_loop._check_and_restart_on_update(tmp_path, "same-hash", ["argv0"])  # pylint: disable=protected-access  # noqa: SLF001

        assert len(update_envs) == 1
        assert update_envs[0] is not None
        assert "VIRTUAL_ENV" not in update_envs[0]
        assert update_envs[0]["PATH"] == "/usr/bin"

    def test_no_update_skips_restart(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """`--no-update`指定時にupdate-dotfilesと`os.execv`のいずれも呼ばれないこと。"""
        myrepo = tmp_path / "repo"
        myrepo.mkdir()
        _setup_notes(tmp_path)
        counts = iter([1, 1, 0])
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
            "execv",
            lambda p, a: execv_calls.append((p, list(a))),
        )
        with pytest.raises(SystemExit):
            atk.main(
                ["mq", "process-loop", "--target-repo", str(myrepo), "--no-update"],
                home=tmp_path,
            )
        assert not execv_calls
        assert not _command_was_called(subprocess_calls, "update-dotfiles")

    def test_missing_update_command_reports_error_and_continues_loop(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """update-dotfilesを解決できない場合も次反復へ進み、待機を継続すること。"""
        myrepo = tmp_path / "repo"
        myrepo.mkdir()
        _setup_notes(tmp_path)
        counts = iter([1, 0])
        monkeypatch.setattr(subprocess, "run", _fake_run_with_remote_url(myrepo, [], 0))
        monkeypatch.setattr(_process_loop, "_count_pending_entries", lambda *_a, **_kw: next(counts))
        monkeypatch.setattr(
            _process_loop.shutil,
            "which",
            lambda command: None if command == "update-dotfiles" else f"/resolved/{command}",
        )

        def fake_wait(*_a: object, **_kw: object) -> None:
            raise KeyboardInterrupt

        monkeypatch.setattr(_process_loop, "_wait_for_changes", fake_wait)

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "process-loop", "--target-repo", str(myrepo)], home=tmp_path)

        assert exc_info.value.code == 0
        assert "update-dotfilesコマンドを利用できない" in capsys.readouterr().err

    def test_missing_uv_command_reports_error_and_continues_loop(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """再起動用uvを解決できない場合も次反復へ進み、待機を継続すること。"""
        myrepo = tmp_path / "repo"
        myrepo.mkdir()
        _setup_notes(tmp_path)
        counts = iter([1, 1, 0])
        monkeypatch.setattr(subprocess, "run", _fake_run_with_remote_url(myrepo, [], 0))
        monkeypatch.setattr(_process_loop, "_count_pending_entries", lambda *_a, **_kw: next(counts))
        monkeypatch.setattr(
            _process_loop.shutil,
            "which",
            lambda command: None if command == "uv" else f"/resolved/{command}",
        )

        def fake_wait(*_a: object, **_kw: object) -> None:
            raise KeyboardInterrupt

        monkeypatch.setattr(_process_loop, "_wait_for_changes", fake_wait)
        monkeypatch.setattr(os, "execv", lambda *_a, **_kw: pytest.fail("uv未解決時はexecvを呼ばないこと"))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "process-loop", "--target-repo", str(myrepo)], home=tmp_path)

        assert exc_info.value.code == 0
        assert "uvコマンドを利用できない" in capsys.readouterr().err


class TestConsoleTitleReset:
    """常駐区間のコンソールタイトル制御を検証する（再起動先は`TestProcessLoopUpdateAndRestart`の既存argv検証が担う）。"""

    def test_helper_functions_reset_title_after_each_run(self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
        """`_sync_worktree_with_upstream`・`_check_and_restart_on_update`配下の各subprocess.run直後にタイトルを再設定すること。"""
        local_path = tmp_path / "repo"
        (local_path / ".claude" / "worktrees" / "process-loop").mkdir(parents=True)
        calls: list[str] = []
        monkeypatch.setattr(_process_loop._console_title, "set_console_title", calls.append)  # pylint: disable=protected-access  # noqa: SLF001

        def fake_run(cmd: list[str], *_a: object, **_kwargs: object) -> subprocess.CompletedProcess[Any]:
            if cmd == ["git", "check-ignore", "-q", ".claude/worktrees/"]:
                return subprocess.CompletedProcess(cmd, 0, "", "")
            if cmd == ["git", "check-ref-format", "--branch", "worktree-process-loop"]:
                return subprocess.CompletedProcess(cmd, 0, "", "")
            if cmd == ["git", "rev-parse", "--git-common-dir"]:
                return subprocess.CompletedProcess(cmd, 0, str(local_path / ".git"), "")
            if cmd == ["git", "rev-parse", "--show-toplevel"]:
                return subprocess.CompletedProcess(cmd, 0, str(local_path / ".claude" / "worktrees" / "process-loop"), "")
            if cmd == ["git", "symbolic-ref", "--short", "HEAD"]:
                return subprocess.CompletedProcess(cmd, 0, "worktree-process-loop", "")
            if cmd == ["git", "symbolic-ref", "--short", "refs/remotes/origin/HEAD"]:
                return subprocess.CompletedProcess(cmd, 0, "origin/master\n", "")
            stdout = "1\n" if "rev-list" in cmd else ""
            return subprocess.CompletedProcess(cmd, 0, stdout, "")

        monkeypatch.setattr(subprocess, "run", fake_run)
        monkeypatch.setattr(_process_loop, "_code_hash", lambda _d: "same-hash")  # 再起動へ進ませない
        _process_loop._sync_worktree_with_upstream(local_path, "process-loop")  # pylint: disable=protected-access  # noqa: SLF001
        _process_loop._check_and_restart_on_update(tmp_path, "same-hash", ["argv0"])  # pylint: disable=protected-access  # noqa: SLF001
        assert calls
        assert all(call == "atk mq process-loop" for call in calls)


class TestWorktreeWriterGate:
    """実装セッション起動前のclean判定と上流追随のfail-closed契約を検証する。"""

    def test_missing_worktree_is_created_from_upstream(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """未作成のworktreeを専用ブランチと上流ブランチから作成する。"""
        local_path = tmp_path / "repo"
        local_path.mkdir()
        worktree_path = local_path / ".claude" / "worktrees" / "process-loop"
        calls: list[list[str]] = []
        monkeypatch.setattr(_process_loop, "_git_output", lambda *_args, **_kwargs: "origin/main")
        monkeypatch.setattr(_process_loop, "_worktree_is_clean", lambda path: path == worktree_path)

        def fake_run(cmd: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
            calls.append(cmd)
            if cmd[1:3] == ["worktree", "add"]:
                worktree_path.mkdir(parents=True)
            returncode = 1 if "show-ref" in cmd else 0
            return subprocess.CompletedProcess(cmd, returncode, "", "")

        monkeypatch.setattr(subprocess, "run", fake_run)

        assert _process_loop._sync_worktree_with_upstream(local_path, "process-loop") == (worktree_path, "origin/main")  # pylint: disable=protected-access  # noqa: SLF001
        assert ["git", "fetch", "origin"] in calls
        assert [
            "git",
            "worktree",
            "add",
            "-b",
            "worktree-process-loop",
            str(worktree_path),
            "origin/main",
        ] in calls
        assert ["git", "rebase", "origin/main"] in calls

    @pytest.mark.parametrize("dirty_command", ["diff", "cached", "untracked"])
    def test_worktree_is_clean_rejects_each_dirty_kind(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        dirty_command: str,
    ) -> None:
        """unstaged・staged・未追跡の各差分を個別に拒否する。"""

        def fake_run(cmd: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
            if dirty_command == "diff" and cmd == ["git", "diff", "--quiet"]:
                return subprocess.CompletedProcess(cmd, 1, "", "")
            if dirty_command == "cached" and cmd == ["git", "diff", "--cached", "--quiet"]:
                return subprocess.CompletedProcess(cmd, 1, "", "")
            stdout = "new.txt\n" if dirty_command == "untracked" and "ls-files" in cmd else ""
            return subprocess.CompletedProcess(cmd, 0, stdout, "")

        monkeypatch.setattr(subprocess, "run", fake_run)
        assert not _process_loop._worktree_is_clean(tmp_path)  # pylint: disable=protected-access  # noqa: SLF001

    @pytest.mark.parametrize("failed_step", ["fetch", "rebase"])
    def test_sync_failure_returns_false(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        failed_step: str,
    ) -> None:
        """fetch又はrebase失敗時は実装セッションを起動可能と判定しない。"""
        local_path = tmp_path / "repo"
        (local_path / ".claude" / "worktrees" / "process-loop").mkdir(parents=True)
        worktree_path = local_path / ".claude" / "worktrees" / "process-loop"
        monkeypatch.setattr(_process_loop, "_worktree_is_clean", lambda _path: True)
        monkeypatch.setattr(_process_loop, "_ensure_worktree_excluded", lambda _path: True)

        def fake_git_output(args: list[str], cwd: pathlib.Path) -> str:
            del cwd
            if args == ["symbolic-ref", "--short", "refs/remotes/origin/HEAD"]:
                return "origin/main"
            if args == ["rev-parse", "--git-common-dir"]:
                return str(local_path / ".git")
            if args == ["rev-parse", "--show-toplevel"]:
                return str(worktree_path)
            if args == ["symbolic-ref", "--short", "HEAD"]:
                return "worktree-process-loop"
            raise AssertionError(args)

        monkeypatch.setattr(_process_loop, "_git_output", fake_git_output)

        def fake_run(cmd: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
            command = cmd[1] if len(cmd) > 1 else ""
            failed = command == failed_step
            return subprocess.CompletedProcess(cmd, 1 if failed else 0, "", "failure" if failed else "")

        monkeypatch.setattr(subprocess, "run", fake_run)
        assert _process_loop._sync_worktree_with_upstream(local_path, "process-loop") is None  # pylint: disable=protected-access  # noqa: SLF001

    def test_title_set_at_start_and_after_runs(self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
        """process-loop開始時にタイトルを設定し、claude起動・update-dotfiles実行の直後にも再設定すること。"""
        myrepo = tmp_path / "repo"
        myrepo.mkdir()
        _setup_notes(tmp_path)
        entered: list[str] = []
        title_calls: list[str] = []
        monkeypatch.setattr(subprocess, "run", _fake_run_with_remote_url(myrepo, [], 0))
        monkeypatch.setattr(_process_loop, "_count_pending_entries", lambda *_a, **_kw: 1)

        @contextlib.contextmanager
        def fake_console_title(title: str) -> Iterator[None]:
            entered.append(title)
            yield

        monkeypatch.setattr(_process_loop._console_title, "console_title", fake_console_title)  # pylint: disable=protected-access  # noqa: SLF001
        monkeypatch.setattr(_process_loop._console_title, "set_console_title", title_calls.append)  # pylint: disable=protected-access  # noqa: SLF001
        monkeypatch.setattr(os, "execv", _raise_system_exit_0)
        with pytest.raises(SystemExit):
            atk.main(["mq", "process-loop", "--target-repo", str(myrepo)], home=tmp_path)
        assert entered == ["atk mq process-loop"]
        # 処理前更新・claude起動・終了後更新の3回のsubprocess.runそれぞれに1回ずつ続く。
        assert title_calls == ["atk mq process-loop"] * 3
        # 非TTY下での制御文字抑止は`TestProcessLoopUpdateAndRestart.test_update_and_execv_called_by_default`が検証する。


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
        _setup_notes(tmp_path)
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
                ["mq", "process-loop", "--target-repo", str(myrepo), "--no-update"],
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


class TestAlertMonitoring:
    """process-loop常駐ループへのアラート自動検出統合。"""

    def test_alert_check_invoked_when_pending_zero(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """件数0の反復でアラート確認が呼ばれ、投入0件なら待機へ進む。"""
        _setup_notes(tmp_path)
        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()
        monkeypatch.setattr(subprocess, "run", _fake_run_with_remote_url(myrepo, [], 0))
        monkeypatch.setattr(_process_loop, "_count_pending_entries", lambda *_a, **_k: 0)
        calls: list[str] = []

        def fake_check(*_args: object, **_kwargs: object) -> int:
            calls.append("checked")
            return 0

        monkeypatch.setattr(  # pylint: disable=protected-access
            _process_loop._alerts,  # pylint: disable=protected-access
            "check_and_submit_alerts",
            fake_check,
        )

        def fake_wait(*_args: object, **_kwargs: object) -> None:
            raise KeyboardInterrupt

        monkeypatch.setattr(_process_loop, "_wait_for_changes", fake_wait)
        with pytest.raises(SystemExit):
            atk.main(["mq", "process-loop", f"--target-repo={myrepo}", "--no-update"], home=tmp_path)
        assert calls == ["checked"]

    def test_no_alerts_flag_skips_check(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """--no-alerts指定時はアラート確認を呼ばない。"""
        _setup_notes(tmp_path)
        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()
        monkeypatch.setattr(subprocess, "run", _fake_run_with_remote_url(myrepo, [], 0))
        monkeypatch.setattr(_process_loop, "_count_pending_entries", lambda *_a, **_k: 0)

        def fail_check(*_args: object, **_kwargs: object) -> int:
            raise AssertionError("アラート確認を呼ばないはず")

        monkeypatch.setattr(  # pylint: disable=protected-access
            _process_loop._alerts,  # pylint: disable=protected-access
            "check_and_submit_alerts",
            fail_check,
        )

        def fake_wait(*_args: object, **_kwargs: object) -> None:
            raise KeyboardInterrupt

        monkeypatch.setattr(_process_loop, "_wait_for_changes", fake_wait)
        with pytest.raises(SystemExit):
            atk.main(
                ["mq", "process-loop", f"--target-repo={myrepo}", "--no-update", "--no-alerts"],
                home=tmp_path,
            )

    def test_alert_submission_triggers_immediate_reiteration(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """投入件数が正なら待機せず次反復のclaude起動へ進む。"""
        _setup_notes(tmp_path)
        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()
        counts = iter([0, 1])
        claude_calls: list[dict[str, Any]] = []
        monkeypatch.setattr(subprocess, "run", _fake_run_with_remote_url(myrepo, claude_calls, 2))
        monkeypatch.setattr(_process_loop, "_count_pending_entries", lambda *_a, **_k: next(counts))
        monkeypatch.setattr(  # pylint: disable=protected-access
            _process_loop._alerts,  # pylint: disable=protected-access
            "check_and_submit_alerts",
            lambda *_a, **_k: 1,
        )

        def fail_wait(*_args: object, **_kwargs: object) -> None:
            raise AssertionError("待機ループへ入らないはず")

        monkeypatch.setattr(_process_loop, "_wait_for_changes", fail_wait)
        with pytest.raises(SystemExit):
            atk.main(["mq", "process-loop", f"--target-repo={myrepo}", "--no-update"], home=tmp_path)
        assert len(claude_calls) == 1

    def test_alert_interval_suppresses_repeated_checks(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """指定間隔未経過の反復ではアラート確認を呼ばない。"""
        _setup_notes(tmp_path)
        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()
        monkeypatch.setattr(subprocess, "run", _fake_run_with_remote_url(myrepo, [], 0))
        monkeypatch.setattr(_process_loop, "_count_pending_entries", lambda *_a, **_k: 0)
        times = iter([1000.0, 1010.0])
        monkeypatch.setattr(time, "monotonic", lambda: next(times))
        calls: list[str] = []

        def fake_check(*_args: object, **_kwargs: object) -> int:
            calls.append("checked")
            return 0

        monkeypatch.setattr(  # pylint: disable=protected-access
            _process_loop._alerts,  # pylint: disable=protected-access
            "check_and_submit_alerts",
            fake_check,
        )
        wait_calls: list[int] = []

        def fake_wait(*_args: object, **_kwargs: object) -> None:
            wait_calls.append(1)
            if len(wait_calls) >= 2:
                raise KeyboardInterrupt

        monkeypatch.setattr(_process_loop, "_wait_for_changes", fake_wait)
        with pytest.raises(SystemExit):
            atk.main(
                [
                    "mq",
                    "process-loop",
                    f"--target-repo={myrepo}",
                    "--no-update",
                    "--alert-interval=3600",
                ],
                home=tmp_path,
            )
        assert calls == ["checked"]


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
        _setup_notes(tmp_path)

        monkeypatch.setattr(subprocess, "run", lambda *_a, **_kw: subprocess.CompletedProcess([], 0, "", ""))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "process-loop", "--target-repo", "github.com/example/foo"], home=tmp_path)
        assert exc_info.value.code == 2

    def test_prompt_keeps_dotfiles_goal_free_of_internal_publish_steps(self) -> None:
        prompt = _process_loop._build_process_loop_prompt(  # pylint: disable=protected-access  # noqa: SLF001
            pathlib.Path("/repo"),
            "github.com/ak110/dotfiles",
            "claude",
            publish_destination="origin/master",
        )
        assert "git worktree内で起動" not in prompt
        assert "現在のHEADを`origin/master`へ反映" in prompt
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
            if cmd[:1] == ["claude"]:
                claude_calls.append({"cmd": list(cmd), "cwd": kwargs.get("cwd")})
                return subprocess.CompletedProcess(cmd, returncode=0, stdout="", stderr="")
            if cmd == ["git", "-C", str(myrepo), "remote", "get-url", "origin"]:
                return subprocess.CompletedProcess(cmd, returncode=0, stdout=remote_url, stderr="")
            return subprocess.CompletedProcess(cmd, returncode=0, stdout="", stderr="")

        monkeypatch.setattr(subprocess, "run", fake_run)
        monkeypatch.setattr(_process_loop, "_count_pending_entries", lambda *_a, **_kw: next(counts))

        def fake_wait_for_changes(private_notes: pathlib.Path, target_repo_id: str | None) -> None:
            del private_notes, target_repo_id
            raise KeyboardInterrupt

        monkeypatch.setattr(_process_loop, "_wait_for_changes", fake_wait_for_changes)
        worktree_path = myrepo / ".claude" / "worktrees" / "process-loop"
        sync_calls: list[tuple[pathlib.Path, str]] = []

        def fake_sync(local_path: pathlib.Path, worktree_name: str) -> tuple[pathlib.Path, str]:
            sync_calls.append((local_path, worktree_name))
            return worktree_path, "origin/master"

        monkeypatch.setattr(
            _process_loop,
            "_sync_worktree_with_upstream",
            fake_sync,
        )

        with pytest.raises(SystemExit):
            atk.main(["mq", "process-loop", f"--target-repo={myrepo}", "--no-update"], home=tmp_path)

        assert len(claude_calls) == 1
        _hook_debug_log(claude_calls[0]["cmd"])
        assert "--worktree=process-loop" not in claude_calls[0]["cmd"]
        expected_cwd = worktree_path if "ak110/dotfiles" in remote_url else myrepo
        assert claude_calls[0]["cwd"] == expected_cwd
        expected_sync_calls = [(myrepo, "process-loop")] if "ak110/dotfiles" in remote_url else []
        assert sync_calls == expected_sync_calls
