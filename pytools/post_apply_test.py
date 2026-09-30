"""pytools.post_apply のテスト。

各ステップが呼ばれること、先行工程の順序と並列実行、途中ステップが例外を送出しても他が継続すること、
画面と永続ログへの出力の振り分け、失敗時の exit code を検証する。
"""

import dataclasses
import io
import json
import logging
import logging.handlers
import os
import re
import subprocess
import threading
import time
from pathlib import Path

import pytest

from pytools import post_apply
from pytools._internal import post_apply_outcome

# 配布先cleanup契約の定数を直接検証する。
# pylint: disable=protected-access


@pytest.fixture(autouse=True)
def _isolate_update_log(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """post-applyの永続ログを実利用者のstate directoryから隔離する。"""
    monkeypatch.setattr(post_apply, "_UPDATE_LOG_PATH", tmp_path / "update-dotfiles.log")


@pytest.fixture(autouse=True, name="sync_report_path")
def _isolate_sync_report(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """同期結果の記録先を一時領域へ向け、実行環境の状態ディレクトリを書き換えないようにする。"""
    report_path = tmp_path / "state" / "sync-report.json"
    monkeypatch.setattr(post_apply.sync_report, "REPORT_PATH", report_path)
    return report_path


@pytest.mark.parametrize(("argv", "exit_code"), [(["--help"], 0), (["--unknown"], 2)])
def test_main_rejects_nondefault_arguments_before_side_effects(
    argv: list[str],
    exit_code: int,
    capsys: pytest.CaptureFixture[str],
    sync_report_path: Path,
) -> None:
    """確認引数と未知引数は更新処理や永続記録へ到達しない。"""
    called = False

    def runner() -> tuple[list[post_apply._StepResult], list[str]]:  # noqa: SLF001
        nonlocal called
        called = True
        return [], []

    with pytest.raises(SystemExit) as exc_info:
        post_apply.main(argv, runner=runner)

    assert exc_info.value.code == exit_code
    assert not called
    assert not post_apply._UPDATE_LOG_PATH.exists()  # noqa: SLF001
    assert not sync_report_path.exists()
    captured = capsys.readouterr()
    if exit_code == 0:
        assert "usage:" in captured.out
        assert captured.err == ""
    else:
        assert "usage:" in captured.err
        assert "unrecognized arguments" in captured.err


def test_main_help_without_runner_skips_default_steps(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], sync_report_path: Path
) -> None:
    """公開CLIと同じ引数形で既定ステップを起動しない。"""

    def unexpected_run() -> tuple[list[post_apply._StepResult], list[str]]:  # noqa: SLF001
        pytest.fail("--helpで既定ステップへ到達した")

    monkeypatch.setattr(post_apply, "run", unexpected_run)
    with pytest.raises(SystemExit) as exc_info:
        post_apply.main(["--help"])

    assert exc_info.value.code == 0
    assert "usage:" in capsys.readouterr().out
    assert not post_apply._UPDATE_LOG_PATH.exists()  # noqa: SLF001
    assert not sync_report_path.exists()


def test_linked_worktree_is_rejected_before_any_step_or_record(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    sync_report_path: Path,
) -> None:
    """複製作業ツリーの既定実行は全段と永続記録へ到達しない。"""
    root = tmp_path / "linked"
    canonical_root = tmp_path / "main"
    monkeypatch.setattr(post_apply.claude_common, "find_dotfiles_root", lambda: root)

    def fake_git(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        assert cmd == ["git", "-C", str(root), "rev-parse", "--path-format=absolute", "--git-common-dir"]
        assert not kwargs
        return subprocess.CompletedProcess(cmd, 0, f"{canonical_root / '.git'}\n", "")

    def unexpected_run() -> tuple[list[post_apply._StepResult], list[str]]:  # noqa: SLF001
        pytest.fail("linked worktreeで既定ステップへ到達した")

    monkeypatch.setattr(post_apply.claude_common, "run_subprocess", fake_git)
    monkeypatch.setattr(post_apply, "run", unexpected_run)

    with pytest.raises(SystemExit) as exc_info:
        post_apply.main([])

    assert exc_info.value.code == 2
    assert not post_apply._UPDATE_LOG_PATH.exists()  # noqa: SLF001
    assert not sync_report_path.exists()
    captured = capsys.readouterr()
    assert captured.out == ""
    assert str(root) in captured.err
    assert str(canonical_root) in captured.err


@pytest.mark.parametrize("allow_non_canonical", [False, True])
def test_canonical_root_or_explicit_override_runs_steps(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    allow_non_canonical: bool,
) -> None:
    """正規ルートと明示解除では既存の全段実行経路へ進む。"""
    canonical_root = tmp_path / "main"
    root = tmp_path / "linked" if allow_non_canonical else canonical_root
    monkeypatch.setattr(post_apply.claude_common, "find_dotfiles_root", lambda: root)

    def fake_git(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        assert cmd == ["git", "-C", str(root), "rev-parse", "--path-format=absolute", "--git-common-dir"]
        assert not kwargs
        return subprocess.CompletedProcess(cmd, 0, f"{canonical_root / '.git'}\n", "")

    calls: list[str] = []
    monkeypatch.setattr(post_apply.claude_common, "run_subprocess", fake_git)
    monkeypatch.setattr(
        post_apply,
        "_DEFAULT_STEPS",
        [("first", _make_step("first", calls)), ("second", _make_step("second", calls))],
    )
    argv = ["--allow-non-canonical-root"] if allow_non_canonical else []

    with pytest.raises(SystemExit) as exc_info:
        post_apply.main(argv)

    assert exc_info.value.code == 0
    assert sorted(calls) == ["first", "second"]


def test_sync_report_records_failed_step_reason_and_detail(sync_report_path: Path) -> None:
    """失敗したステップの名前、例外の内容、tracebackの末尾を同期結果の記録へ残す。"""
    steps = [("success", lambda: True), ("failure", lambda: (_ for _ in ()).throw(RuntimeError("boom")))]

    with pytest.raises(SystemExit):
        post_apply.main(runner=lambda: post_apply.run(steps=steps))

    post_apply_report = json.loads(sync_report_path.read_text(encoding="utf-8"))["post_apply"]
    assert post_apply_report["updated"] == 1
    assert post_apply_report["skipped"] == 0
    assert post_apply_report["failed"] == 1
    failed_step = post_apply_report["failed_steps"][0]
    assert failed_step["name"] == "failure"
    assert failed_step["reason"] == "RuntimeError: boom"
    assert "RuntimeError: boom" in failed_step["detail"]


def test_sync_report_preserves_report_of_the_same_run(monkeypatch: pytest.MonkeyPatch, sync_report_path: Path) -> None:
    """同じ実行の記録がある場合は、その記録へpost-apply段の結果を足す。"""
    monkeypatch.setenv("UPDATE_DOTFILES_RUN_ID", "run-1")
    post_apply.sync_report.write_start("run-1", "2026-09-22T00:00:00+00:00")

    with pytest.raises(SystemExit):
        post_apply.main(runner=lambda: post_apply.run(steps=[("success", lambda: True)]))

    report = json.loads(sync_report_path.read_text(encoding="utf-8"))
    assert report["run_id"] == "run-1"
    assert report["status"] == "running"
    assert report["post_apply"]["failed"] == 0


def test_configure_logging_preserves_cp932_record_with_unencodable_character(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CP932で表現できない文字を代替表現へ変換し、ログレコードを保持する。"""
    stdout_buffer = io.BytesIO()
    stderr_buffer = io.BytesIO()
    stdout = io.TextIOWrapper(stdout_buffer, encoding="cp932")
    stderr = io.TextIOWrapper(stderr_buffer, encoding="cp932")
    monkeypatch.setattr(post_apply.sys, "stdout", stdout)
    monkeypatch.setattr(post_apply.sys, "stderr", stderr)
    root_logger = logging.getLogger()
    previous_handlers, previous_level, persistent_log_ready = post_apply._configure_logging()  # noqa: SLF001
    try:
        logging.getLogger("cp932-test").info("符号化不能文字: ✓")
        stdout.flush()
        stderr.flush()
        output = stdout_buffer.getvalue().decode("cp932") + stderr_buffer.getvalue().decode("cp932")
    finally:
        current_handlers = root_logger.handlers.copy()
        root_logger.handlers[:] = previous_handlers
        root_logger.setLevel(previous_level)
        for handler in current_handlers:
            handler.close()

    assert stdout.encoding == "cp932"
    assert persistent_log_ready
    assert "符号化不能文字" in output
    assert r"\u2713" in output
    assert "--- Logging error ---" not in output


def test_main_records_steps_and_failure_in_persistent_log() -> None:
    """post-applyの各ステップと最終失敗を同じ永続ログへ記録する。"""
    steps = [("success", lambda: False), ("failure", lambda: (_ for _ in ()).throw(RuntimeError("boom")))]

    with pytest.raises(SystemExit) as exc_info:
        post_apply.main(runner=lambda: post_apply.run(steps=steps))

    assert exc_info.value.code == 1
    log_text = post_apply._UPDATE_LOG_PATH.read_text(encoding="utf-8")  # noqa: SLF001
    assert "post-apply開始" in log_text
    assert "[1/2] success" in log_text
    assert "[2/2] failure" in log_text
    assert "post-apply終了: exit=1" in log_text


def test_persistent_log_uses_size_limited_rotation() -> None:
    """永続ログの総容量を固定世代数で制限する。"""
    root_logger = logging.getLogger()
    previous_handlers, previous_level, persistent_log_ready = post_apply._configure_logging()  # noqa: SLF001
    current_handlers = root_logger.handlers.copy()
    try:
        file_handlers = [handler for handler in current_handlers if isinstance(handler, logging.handlers.RotatingFileHandler)]
        assert persistent_log_ready
        assert len(file_handlers) == 1
        assert file_handlers[0].maxBytes == post_apply._UPDATE_LOG_MAX_BYTES  # noqa: SLF001
        assert file_handlers[0].backupCount == post_apply._UPDATE_LOG_BACKUP_COUNT  # noqa: SLF001
    finally:
        root_logger.handlers[:] = previous_handlers
        root_logger.setLevel(previous_level)
        for handler in current_handlers:
            handler.close()


def test_removed_session_review_skill_paths_cover_claude_and_codex() -> None:
    """旧個人スキルをClaude CodeとCodexの両配布先からcleanupする。"""
    relative = Path("skills/session-review-dotfiles")
    assert relative in post_apply._REMOVED_PATHS[Path.home() / ".claude"]  # noqa: SLF001
    assert relative in post_apply._REMOVED_PATHS[Path.home() / ".codex"]  # noqa: SLF001


def test_removed_sync_cross_project_paths_cover_claude_and_codex() -> None:
    """改名前の個人プロジェクト運用スキルを両配布先からcleanupする。"""
    relative = Path("skills/sync-cross-project")
    assert relative in post_apply._REMOVED_PATHS[Path.home() / ".claude"]  # noqa: SLF001
    assert relative in post_apply._REMOVED_PATHS[Path.home() / ".codex"]  # noqa: SLF001


def test_legacy_reference_directory_is_cleanup_target() -> None:
    """スキル配下以外の旧`references/`を配布先から削除する。"""
    assert Path("references") in post_apply._REMOVED_PATHS[Path.home() / ".claude"]  # noqa: SLF001


def test_removes_legacy_plans_viewer_config_and_shim() -> None:
    """旧計画ビューアーの設定・CLI・Windowsスタートアップ用shimを配布先から除去する。"""
    assert Path("pytools/claude-plans-viewer.toml") in post_apply._REMOVED_PATHS[Path.home() / ".config"]  # noqa: SLF001
    local_bin = post_apply._REMOVED_PATHS[Path.home() / ".local" / "bin"]  # noqa: SLF001
    assert Path("claude-plans-viewer") in local_bin
    assert Path("claude-plans-viewer.exe") in local_bin
    startup = Path.home() / "AppData" / "Roaming" / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
    assert Path("claude-plans-viewer.cmd") in post_apply._REMOVED_PATHS_IF_CONTENT[startup]  # noqa: SLF001
    # 旧systemd unitは停止と無効化を経てから削除するため、この一括削除の対象へ含めない。
    for paths in post_apply._REMOVED_PATHS.values():  # noqa: SLF001
        assert not [path for path in paths if "claude-plans-viewer.service" in path.name]


def test_removes_legacy_atk_launcher_but_keeps_current_wrappers() -> None:
    """作業ツリー版を覆い隠す旧atkランチャーを登録し、現行のサービス用・hook用ラッパーは登録しない。"""
    local_bin = post_apply._REMOVED_PATHS[Path.home() / ".local" / "bin"]  # noqa: SLF001
    assert Path("atk") in local_bin
    assert Path("atk.cmd") in local_bin
    for kept in ("atk-serve", "atk-hook", "atk-hook.cmd"):
        assert Path(kept) not in local_bin


def test_removes_flag_files_of_retired_steps_but_keeps_current_config() -> None:
    """廃止した工程のフラグファイルを登録し、現行のagent-toolkit設定は登録しない。"""
    config = post_apply._REMOVED_PATHS[Path.home() / ".config"]  # noqa: SLF001
    assert Path("agent-toolkit/feedback-inbox.enabled") in config
    assert Path("agent-toolkit/review-balance-mode.claude-heavy") in config
    for kept in ("agent-toolkit/config.json", "agent-toolkit/serve.toml"):
        assert Path(kept) not in config


@pytest.mark.parametrize(
    ("base", "removed", "kept"),
    [
        (
            Path(".local") / "bin",
            ("atk", "atk.cmd"),
            ("atk-serve", "atk-hook", "atk-hook.cmd", "uv"),
        ),
        (
            Path(".config"),
            ("agent-toolkit/feedback-inbox.enabled", "agent-toolkit/review-balance-mode.claude-heavy"),
            ("agent-toolkit/config.json", "agent-toolkit/serve.toml"),
        ),
    ],
)
def test_cleanup_applies_registered_legacy_paths_without_touching_current_files(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    base: Path,
    removed: tuple[str, ...],
    kept: tuple[str, ...],
) -> None:
    """実際の登録内容を一時ホームへ適用し、旧生成物だけが削除されることを確かめる。"""
    registered = post_apply._REMOVED_PATHS[Path.home() / base]  # noqa: SLF001
    home_dir = tmp_path / "home"
    target_dir = home_dir / base
    monkeypatch.setenv("HOME", str(home_dir))
    monkeypatch.setenv("USERPROFILE", str(home_dir))
    monkeypatch.setattr(post_apply, "_REMOVED_PATHS", {target_dir: registered})
    monkeypatch.setattr(post_apply, "_REMOVED_PATHS_IF_CONTENT", {})
    for name in (*removed, *kept):
        (target_dir / name).parent.mkdir(parents=True, exist_ok=True)
        (target_dir / name).write_text("x\n", encoding="utf-8")

    changed = post_apply._cleanup_removed_paths()  # noqa: SLF001

    assert changed is True
    for name in removed:
        assert not (target_dir / name).exists()
    for name in kept:
        assert (target_dir / name).is_file()


def test_removed_ipython_profile_is_limited_to_profile_default() -> None:
    """旧IPythonプロファイルのcleanup対象に利用中のprofile_ipyを含めない。"""
    paths = post_apply._REMOVED_PATHS[Path.home() / ".ipython"]  # noqa: SLF001
    assert Path("profile_default/startup/README") in paths
    assert not any(path.is_relative_to("profile_ipy") for path in paths)


def _redirect_removed_paths_to(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> Path:
    """旧配布物削除先を一時ホームへ限定し、IPython配布ファイルだけを登録する。"""
    home_dir = tmp_path / "home"
    ipython_dir = home_dir / ".ipython"
    monkeypatch.setenv("HOME", str(home_dir))
    monkeypatch.setenv("USERPROFILE", str(home_dir))
    monkeypatch.setattr(
        post_apply,
        "_REMOVED_PATHS",
        {ipython_dir: [Path("profile_default/startup/README")]},
    )
    monkeypatch.setattr(post_apply, "_REMOVED_PATHS_IF_CONTENT", {})
    return ipython_dir


def test_removed_ipython_profile_cleanup_removes_empty_parents(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """配布済みREADMEの削除後、空になった親ディレクトリだけを深い順で除去する。"""
    ipython_dir = _redirect_removed_paths_to(monkeypatch, tmp_path)
    default_readme = ipython_dir / "profile_default/startup/README"
    default_readme.parent.mkdir(parents=True)
    default_readme.write_text("old\n", encoding="utf-8")

    changed = post_apply._cleanup_removed_paths()  # noqa: SLF001

    assert changed is True
    assert not (ipython_dir / "profile_default").exists()


def test_removed_ipython_profile_cleanup_preserves_user_file_in_startup(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """startupに利用者ファイルが残る場合はprofile_defaultまで保持する。"""
    ipython_dir = _redirect_removed_paths_to(monkeypatch, tmp_path)
    default_readme = ipython_dir / "profile_default/startup/README"
    default_readme.parent.mkdir(parents=True)
    default_readme.write_text("old\n", encoding="utf-8")
    user_script = default_readme.parent / "00-user.py"
    user_script.write_text("print('user')\n", encoding="utf-8")

    changed = post_apply._cleanup_removed_paths()  # noqa: SLF001

    assert changed is True
    assert not default_readme.exists()
    assert user_script.read_text(encoding="utf-8") == "print('user')\n"
    assert (ipython_dir / "profile_default/startup").is_dir()
    assert (ipython_dir / "profile_default").is_dir()


def test_removed_ipython_profile_cleanup_preserves_user_file_in_profile_root(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """profile_default直下に利用者ファイルが残る場合はルートだけを保持する。"""
    ipython_dir = _redirect_removed_paths_to(monkeypatch, tmp_path)
    default_readme = ipython_dir / "profile_default/startup/README"
    default_readme.parent.mkdir(parents=True)
    default_readme.write_text("old\n", encoding="utf-8")
    user_config = ipython_dir / "profile_default/ipython_config.py"
    user_config.write_text("c = get_config()\n", encoding="utf-8")
    active_config = ipython_dir / "profile_ipy/ipython_config.py"
    active_config.parent.mkdir(parents=True)
    active_config.write_text("active\n", encoding="utf-8")

    changed = post_apply._cleanup_removed_paths()  # noqa: SLF001

    assert changed is True
    assert not default_readme.exists()
    assert not (ipython_dir / "profile_default/startup").exists()
    assert user_config.read_text(encoding="utf-8") == "c = get_config()\n"
    assert (ipython_dir / "profile_default").is_dir()
    assert active_config.read_text(encoding="utf-8") == "active\n"


def test_removed_ipython_profile_cleanup_skips_missing_profile(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """配布済みファイルも親ディレクトリも存在しない場合は変更なしとする。"""
    _redirect_removed_paths_to(monkeypatch, tmp_path)

    changed = post_apply._cleanup_removed_paths()  # noqa: SLF001

    assert changed is False


def test_removed_ipython_profile_cleanup_preserves_symlink_target(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """profile_defaultが外部リンクの場合はリンク先の空ディレクトリを削除しない。"""
    ipython_dir = _redirect_removed_paths_to(monkeypatch, tmp_path)
    outside_profile = tmp_path / "outside-profile"
    outside_startup = outside_profile / "startup"
    outside_startup.mkdir(parents=True)
    ipython_dir.mkdir(parents=True)
    profile_link = ipython_dir / "profile_default"
    profile_link.symlink_to(outside_profile, target_is_directory=True)

    changed = post_apply._cleanup_removed_paths()  # noqa: SLF001

    assert changed is False
    assert profile_link.is_symlink()
    assert outside_startup.is_dir()


def _make_step(name: str, calls: list[str], changed: bool = False):
    """呼び出し記録を残すステップ関数を返すヘルパー。"""

    def fn() -> bool:
        calls.append(name)
        return changed

    return fn


def _make_broken_step(name: str, calls: list[str], message: str = "boom"):
    """例外を送出するステップ関数を返すヘルパー。"""

    def fn() -> bool:
        calls.append(name)
        raise RuntimeError(message)

    return fn


def _make_plugin_step(recommendations: list[str]):
    """推奨コマンドリストを返すステップ関数を返すヘルパー。"""

    def fn() -> tuple[bool, list[str]]:
        return True, recommendations

    return fn


def _make_outcome_step(*, changed: bool, notices: tuple[post_apply_outcome.PostApplyNotice, ...]):
    """構造化したpost-apply結果を返すステップ関数を返す。"""

    def fn() -> post_apply_outcome.PostApplyOutcome:
        return post_apply_outcome.PostApplyOutcome(changed=changed, notices=notices)

    return fn


class TestRun:
    """post_apply.run() の振る舞い。"""

    def test_all_steps_succeed(self):
        """全ステップ成功時、ok=True のリストが返る。"""
        calls: list[str] = []
        steps: list[tuple[str, post_apply.Callable[[], post_apply.StepReturn]]] = [
            ("Claude 設定", _make_step("claude", calls, changed=True)),
            ("VSCode 設定", _make_step("vscode", calls)),
            ("SSH config", _make_step("ssh", calls)),
            ("旧配布物の削除", _make_step("cleanup", calls)),
            ("npm/pnpm サプライチェーン対策", _make_step("npmrc", calls, changed=True)),
            ("mise セットアップ", _make_step("mise", calls)),
            ("Claude Code plugin のインストール", _make_step("plugins", calls)),
            ("旧Codex User scope MCP登録の移行", _make_step("codex-migration", calls)),
            ("libarchive (Windows)", _make_step("libarchive", calls)),
            ("atk serve 再起動 (Linux)", _make_step("atk-serve-restart-linux", calls)),
        ]

        results, recommendations = post_apply.run(steps=steps)

        assert sorted(calls) == sorted(
            [
                "claude",
                "vscode",
                "ssh",
                "cleanup",
                "npmrc",
                "mise",
                "plugins",
                "codex-migration",
                "libarchive",
                "atk-serve-restart-linux",
            ]
        )
        assert all(r.ok for r in results)
        assert [r.changed for r in results] == [
            True,
            False,
            False,
            False,
            True,
            False,
            False,
            False,
            False,
            False,
        ]
        assert not recommendations

    def test_failing_step_does_not_stop_others(self):
        """途中ステップが例外を送出しても後続は実行される。"""
        calls: list[str] = []
        steps: list[tuple[str, post_apply.Callable[[], post_apply.StepReturn]]] = [
            ("Claude 設定", _make_step("claude", calls)),
            ("VSCode 設定", _make_step("vscode", calls)),
            ("SSH config", _make_broken_step("broken", calls)),
            ("旧配布物の削除", _make_step("cleanup", calls)),
            ("npm/pnpm サプライチェーン対策", _make_step("npmrc", calls)),
            ("mise セットアップ", _make_step("mise", calls)),
            ("Claude Code plugin のインストール", _make_step("plugins", calls)),
            ("旧Codex User scope MCP登録の移行", _make_step("codex-migration", calls)),
            ("libarchive (Windows)", _make_step("libarchive", calls)),
            ("atk serve 再起動 (Linux)", _make_step("atk-serve-restart-linux", calls)),
        ]

        results, _ = post_apply.run(steps=steps)

        assert sorted(calls) == sorted(
            [
                "claude",
                "vscode",
                "broken",
                "cleanup",
                "npmrc",
                "mise",
                "plugins",
                "codex-migration",
                "libarchive",
                "atk-serve-restart-linux",
            ]
        )
        ok_flags = [r.ok for r in results]
        assert ok_flags == [True, True, False, True, True, True, True, True, True, True]

    def test_foreground_completion_reports_step_duration(self, caplog: pytest.LogCaptureFixture) -> None:
        """前景ステップの完了行へステップ本体の所要時間を表示する。"""

        def foreground() -> bool:
            time.sleep(0.3)
            return False

        caplog.set_level(logging.INFO)
        post_apply.run([post_apply._StepSpec("前景", foreground)])  # noqa: SLF001

        completion = next(record.getMessage() for record in caplog.records if record.getMessage().startswith("[1/1] 前景 ("))
        match = re.fullmatch(r"\[1/1] 前景 \(([0-9]+\.[0-9])秒\)", completion)
        assert match is not None
        assert float(match.group(1)) >= 0.2

    def test_independent_steps_overlap_and_predecessor_blocks_successor(self) -> None:
        """先行工程を持たないステップは同時に実行し、後続ステップは先行工程の完了後に開始する。"""
        first_started = threading.Event()
        second_started = threading.Event()
        events: list[str] = []
        lock = threading.Lock()

        def record(event: str) -> None:
            with lock:
                events.append(event)

        def first() -> bool:
            record("first-start")
            first_started.set()
            # 2番目のステップが同時に動いていなければ、待機が上限に達して失敗する。
            assert second_started.wait(timeout=5)
            time.sleep(0.1)
            record("first-end")
            return False

        def second() -> bool:
            second_started.set()
            assert first_started.wait(timeout=5)
            return False

        def successor() -> bool:
            record("successor-start")
            return False

        results, _ = post_apply.run(
            [
                post_apply._StepSpec("first", first),  # noqa: SLF001
                post_apply._StepSpec("second", second),  # noqa: SLF001
                post_apply._StepSpec("successor", successor, after=("first",)),  # noqa: SLF001
            ]
        )

        assert all(result.ok for result in results)
        assert events.index("first-end") < events.index("successor-start")

    def test_failed_predecessor_does_not_stop_successor(self) -> None:
        """先行工程が失敗しても後続ステップを実行し、失敗をそのステップの結果にとどめる。"""
        calls: list[str] = []
        results, _ = post_apply.run(
            [
                post_apply._StepSpec("先行", _make_broken_step("before", calls)),  # noqa: SLF001
                post_apply._StepSpec("後続", _make_step("after", calls, changed=True), after=("先行",)),  # noqa: SLF001
            ]
        )

        assert calls == ["before", "after"]
        assert [(result.name, result.ok, result.changed) for result in results] == [
            ("先行", False, False),
            ("後続", True, True),
        ]

    def test_after_all_preceding_waits_for_every_earlier_step(self) -> None:
        """列挙順で前にある全ステップの完了後に開始する。"""
        finished: list[str] = []
        observed: list[list[str]] = []

        def slow() -> bool:
            time.sleep(0.2)
            finished.append("slow")
            return False

        def last() -> bool:
            observed.append(list(finished))
            return False

        post_apply.run(
            [
                post_apply._StepSpec("slow", slow),  # noqa: SLF001
                post_apply._StepSpec("fast", _make_step("fast", finished)),  # noqa: SLF001
                post_apply._StepSpec("last", last, after_all_preceding=True),  # noqa: SLF001
            ]
        )

        assert sorted(observed[0]) == ["fast", "slow"]

    def test_output_and_results_follow_enumeration_order(self, caplog: pytest.LogCaptureFixture) -> None:
        """後に完了した先頭ステップの出力を、先に完了した後続ステップより前へまとめて出力する。"""
        second_done = threading.Event()

        def first() -> bool:
            assert second_done.wait(timeout=5)
            logging.getLogger("order-test").info("一のログ")
            return False

        def second() -> tuple[bool, list[str]]:
            logging.getLogger("order-test").info("二のログ")
            second_done.set()
            return True, ["cmd-2"]

        caplog.set_level(logging.INFO)
        results, recommendations = post_apply.run(
            [
                post_apply._StepSpec("一", first),  # noqa: SLF001
                post_apply._StepSpec("二", second),  # noqa: SLF001
            ]
        )

        assert [result.name for result in results] == ["一", "二"]
        assert recommendations == ["cmd-2"]
        messages = [record.getMessage() for record in caplog.records]
        first_heading = next(message for message in messages if message.startswith("[1/2] 一 ("))
        second_heading = next(message for message in messages if message.startswith("[2/2] 二 ("))
        order = [messages.index(item) for item in (first_heading, "一のログ", second_heading, "二のログ")]
        assert order == sorted(order)

    @pytest.mark.parametrize(
        ("steps", "message"),
        [
            ([post_apply._StepSpec("a", lambda: False, after=("missing",))], "先行工程が見つかりません"),  # noqa: SLF001
            (
                [
                    post_apply._StepSpec("a", lambda: False, after=("b",)),  # noqa: SLF001
                    post_apply._StepSpec("b", lambda: False, after=("a",)),  # noqa: SLF001
                ],
                "先行工程が循環しています",
            ),
        ],
    )
    def test_invalid_predecessor_declaration_is_rejected(self, steps: list[post_apply._StepSpec], message: str) -> None:  # noqa: SLF001
        """未知の先行工程と循環は実行前に拒否する。"""
        with pytest.raises(ValueError, match=message):
            post_apply.run(steps)

    def test_screen_hides_noise_lines_and_persistent_log_keeps_them(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """HTTP Request行、claude CLIの実行記録、対象外OSのステップ、開始行を画面から外し、永続ログへ残す。"""
        monkeypatch.setattr(post_apply.sys, "platform", "linux")
        monkeypatch.setattr(post_apply.claude_common, "resolve_executable", lambda name, **_kwargs: Path(name))
        monkeypatch.setattr(
            post_apply.claude_common,
            "run_subprocess",
            lambda cmd, **_kwargs: subprocess.CompletedProcess(cmd, 0, "", ""),
        )
        windows_calls: list[str] = []

        def http_step() -> bool:
            logging.getLogger("httpx").info('HTTP Request: GET https://example.invalid "HTTP/1.1 200 OK"')
            logging.getLogger("result-test").info("    http: 変更なし")
            return False

        def claude_step() -> bool:
            post_apply.claude_common.run_claude(["plugin", "list"])
            return False

        steps = [
            post_apply._StepSpec("HTTPを使う工程", http_step),  # noqa: SLF001
            post_apply._StepSpec("claudeを使う工程", claude_step),  # noqa: SLF001
            post_apply._StepSpec(  # noqa: SLF001
                "Windows専用工程", _make_step("windows", windows_calls), platforms=("win32",)
            ),
        ]

        with pytest.raises(SystemExit) as exc_info:
            post_apply.main(runner=lambda: post_apply.run(steps))

        assert exc_info.value.code == 0
        assert not windows_calls
        out = capsys.readouterr().out
        assert "HTTP Request:" not in out
        assert "claude: exec:" not in out
        assert "claude: exit" not in out
        assert "Windows専用工程" not in out
        lines = out.splitlines()
        assert "  [1/3] HTTPを使う工程" not in lines
        assert "  [2/3] claudeを使う工程" not in lines
        heading = next(index for index, line in enumerate(lines) if line.startswith("  [1/3] HTTPを使う工程 ("))
        assert lines[heading + 1] == "      http: 変更なし"
        assert any(line.startswith("  [2/3] claudeを使う工程 (") for line in lines)
        assert "完了: 更新 0 件 / スキップ 3 件 / 失敗 0 件" in out
        log_text = post_apply._UPDATE_LOG_PATH.read_text(encoding="utf-8")  # noqa: SLF001
        assert "HTTP Request: GET" in log_text
        assert "claude: exec: plugin list" in log_text
        assert "claude: exit 0: plugin list" in log_text
        assert "[1/3] HTTPを使う工程\n" in log_text
        assert "[3/3] Windows専用工程: 実行中のOSは対象外のため実行しない" in log_text

    def test_main_exits_1_on_failure(self):
        """失敗があれば main() は SystemExit(1) で終了する。"""
        calls: list[str] = []
        steps: list[tuple[str, post_apply.Callable[[], post_apply.StepReturn]]] = [
            ("ok", _make_step("ok", calls)),
            ("broken", _make_broken_step("broken", calls)),
        ]
        with pytest.raises(SystemExit) as exc_info:
            post_apply.main(runner=lambda: post_apply.run(steps=steps))
        assert exc_info.value.code == 1

    def test_cli_install_failure_marks_step_failed_continues_and_exits_1(self):
        """CLI導入失敗を失敗結果へ変換し、後続実行後に終了コード1とする。"""
        calls: list[str] = []
        steps: list[tuple[str, post_apply.Callable[[], post_apply.StepReturn]]] = [
            ("Codex CLI の導入と更新", _make_broken_step("codex", calls)),
            ("後続ステップ", _make_step("later", calls)),
        ]

        results, _ = post_apply.run(steps=steps)

        assert sorted(calls) == ["codex", "later"]
        assert [(result.ok, result.changed) for result in results] == [(False, False), (True, False)]
        with pytest.raises(SystemExit) as exc_info:
            post_apply.main(runner=lambda: (results, []))
        assert exc_info.value.code == 1

    def test_statusline_development_failure_marks_step_failed_continues_and_exits_1(self):
        """statusline開発版の導入失敗を失敗結果へ変換し、後続実行後に終了コード1とする。"""
        calls: list[str] = []
        steps: list[tuple[str, post_apply.Callable[[], post_apply.StepReturn]]] = [
            ("claude-statusline バイナリの取得", _make_broken_step("statusline", calls)),
            ("後続ステップ", _make_step("later", calls)),
        ]

        results, _ = post_apply.run(steps=steps)

        assert sorted(calls) == ["later", "statusline"]
        assert [(result.ok, result.changed) for result in results] == [(False, False), (True, False)]
        with pytest.raises(SystemExit) as exc_info:
            post_apply.main(runner=lambda: (results, []))
        assert exc_info.value.code == 1

    def test_main_exits_0_on_success(self):
        """全て成功なら main() は SystemExit(0) で正常終了する。"""
        calls: list[str] = []
        steps: list[tuple[str, post_apply.Callable[[], post_apply.StepReturn]]] = [
            ("ok", _make_step("ok", calls)),
        ]
        with pytest.raises(SystemExit) as exc_info:
            post_apply.main(runner=lambda: post_apply.run(steps=steps))
        assert exc_info.value.code == 0

    def test_main_splits_info_and_errors_between_streams(
        self,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """正常な状態表示はstdout、失敗一覧はstderrへ出力する。"""
        calls: list[str] = []
        steps: list[tuple[str, post_apply.Callable[[], post_apply.StepReturn]]] = [
            ("ok", _make_step("ok", calls)),
            ("broken", _make_broken_step("broken", calls)),
        ]

        with pytest.raises(SystemExit) as exc_info:
            post_apply.main(runner=lambda: post_apply.run(steps=steps))

        assert exc_info.value.code == 1
        assert sorted(calls) == ["broken", "ok"]
        captured = capsys.readouterr()
        assert "完了: 更新 0 件 / スキップ 1 件 / 失敗 1 件" in captured.out
        assert "失敗したステップ" not in captured.out
        assert "失敗したステップ: broken" in captured.err
        assert "完了:" not in captured.err

    def test_structured_outcome_preserves_changed_and_notices(self) -> None:
        """構造化結果の変更有無と案内をステップ結果へ保持する。"""
        notice = post_apply_outcome.PostApplyNotice("Codex pluginを更新しました。")
        steps: list[tuple[str, post_apply.Callable[[], post_apply.StepReturn]]] = [
            ("Codex plugin", _make_outcome_step(changed=True, notices=(notice,))),
        ]

        results, recommendations = post_apply.run(steps=steps)

        assert not recommendations
        assert results[0].changed is True
        assert results[0].notices == (notice,)

    def test_main_prints_deduplicated_notice_without_command(self, capsys: pytest.CaptureFixture[str]) -> None:
        """commandを持たない重複案内をstderrへ1回表示する。"""
        notice = post_apply_outcome.PostApplyNotice("Codex pluginを更新しました。")
        steps: list[tuple[str, post_apply.Callable[[], post_apply.StepReturn]]] = [
            ("first", _make_outcome_step(changed=True, notices=(notice,))),
            ("second", _make_outcome_step(changed=True, notices=(notice,))),
        ]

        with pytest.raises(SystemExit) as exc_info:
            post_apply.main(runner=lambda: post_apply.run(steps=steps))

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "codex app-server daemon restart" not in captured.out
        assert captured.err.count("Codex pluginを更新しました。") == 1
        assert captured.err.splitlines()[-1].endswith("Codex pluginを更新しました。")

    def test_main_keeps_notice_on_later_failure(self, capsys: pytest.CaptureFixture[str]) -> None:
        """案内の発生後に後続が失敗しても非0終了と案内を両立する。"""
        calls: list[str] = []
        notice = post_apply_outcome.PostApplyNotice("Codex pluginを更新しました。")
        steps: list[tuple[str, post_apply.Callable[[], post_apply.StepReturn]]] = [
            ("Codex plugin", _make_outcome_step(changed=True, notices=(notice,))),
            ("broken", _make_broken_step("broken", calls)),
        ]

        with pytest.raises(SystemExit) as exc_info:
            post_apply.main(runner=lambda: post_apply.run(steps=steps))

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "失敗したステップ: broken" in captured.err
        assert captured.err.splitlines()[-1].endswith("Codex pluginを更新しました。")

    def test_main_keeps_notice_from_failed_codex_step(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Codex plugin処理が案内後に失敗しても案内と後続実行を保持する。"""
        calls: list[str] = []
        notice = post_apply_outcome.PostApplyNotice(
            "外部pluginを更新しました。",
            "codex app-server daemon restart",
        )
        assert notice.command is not None
        failure = f"local pluginの確定に失敗\n{notice.message}\n{notice.command}"
        steps: list[tuple[str, post_apply.Callable[[], post_apply.StepReturn]]] = [
            ("Codex plugin", _make_broken_step("codex", calls, failure)),
            ("later", _make_step("later", calls)),
        ]

        with pytest.raises(SystemExit) as exc_info:
            post_apply.main(runner=lambda: post_apply.run(steps=steps))

        assert exc_info.value.code == 1
        assert sorted(calls) == ["codex", "later"]
        captured = capsys.readouterr()
        assert captured.err.count(notice.message) == 1
        assert captured.err.count(notice.command) == 1


class TestSubstitutedHome:
    """HOMEを差し替えた実行で、HOMEの外にある実機の共有資源を操作するステップを実行しない契約。

    `systemctl --user`や`/dev/shm`はHOMEで解決されないため、手動観測やテストでHOMEだけを差し替えても
    実機の`atk-serve.service`の再起動や共有メモリー上のファイルの削除が起こる。
    """

    _HOST_RESOURCE_STEPS = (
        "Codex 診断ログの通常ストレージ復元 (Linux)",
        "atk serve 自動起動セットアップ (Linux)",
        "dotfiles自動更新タイマー セットアップ (Linux)",
    )

    def _default_steps_with_recorders(self, calls: list[str]) -> list[post_apply._StepSpec]:  # noqa: SLF001
        """既定の3ステップの宣言を保ったまま、`run`だけを呼び出しの記録へ差し替える。"""
        steps = [step for step in post_apply._DEFAULT_STEPS if step.name in self._HOST_RESOURCE_STEPS]  # noqa: SLF001
        assert [step.name for step in steps] == list(self._HOST_RESOURCE_STEPS)
        return [dataclasses.replace(step, after=(), run=_make_step(step.name, calls)) for step in steps]

    def test_substituted_home_skips_host_resource_steps(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        monkeypatch.setattr(post_apply.sys, "platform", "linux")
        monkeypatch.setenv("HOME", str(tmp_path / "home"))
        systemctl_calls: list[object] = []
        monkeypatch.setattr(subprocess, "run", lambda *args, **_kwargs: systemctl_calls.append(args))
        calls: list[str] = []

        with caplog.at_level(logging.INFO):
            results, _ = post_apply.run(self._default_steps_with_recorders(calls))

        assert not calls
        assert not systemctl_calls
        assert [(result.ok, result.changed) for result in results] == [(True, False)] * 3
        for index, name in enumerate(self._HOST_RESOURCE_STEPS, start=1):
            record = next(r for r in caplog.records if r.getMessage().startswith(f"[{index}/3] {name}: "))
            assert "HOMEが実行ユーザーのホームと異なるため" in record.getMessage()
            # 手動観測の実行者が画面で確認できるよう、画面から外す印を付けない。
            assert not post_apply.log_format.is_log_only(record)

    def test_actual_home_runs_host_resource_steps(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import pwd  # noqa: PLC0415  # pylint: disable=import-outside-toplevel

        monkeypatch.setattr(post_apply.sys, "platform", "linux")
        monkeypatch.setenv("HOME", pwd.getpwuid(os.getuid()).pw_dir)
        calls: list[str] = []

        post_apply.run(self._default_steps_with_recorders(calls))

        assert sorted(calls) == sorted(self._HOST_RESOURCE_STEPS)


class TestPytoolsInstallNotices:
    """テンプレートから渡されたpytools再導入状態の最終案内。"""

    def test_unset_state_does_not_print_notice(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """状態が未設定なら案内を表示しない。"""
        monkeypatch.delenv("DOTFILES_PYTOOLS_INSTALL_STATE", raising=False)
        monkeypatch.delenv("DOTFILES_PYTOOLS_INSTALL_DETAIL", raising=False)

        with pytest.raises(SystemExit) as exc_info:
            post_apply.main(runner=lambda: ([], []))

        assert exc_info.value.code == 0
        assert "pytoolsの再インストール" not in capsys.readouterr().err

    def test_empty_state_does_not_print_notice(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """状態が空文字列なら案内を表示しない。"""
        monkeypatch.setenv("DOTFILES_PYTOOLS_INSTALL_STATE", "")
        monkeypatch.setenv("DOTFILES_PYTOOLS_INSTALL_DETAIL", "ignored")

        with pytest.raises(SystemExit) as exc_info:
            post_apply.main(runner=lambda: ([], []))

        assert exc_info.value.code == 0
        assert "pytoolsの再インストール" not in capsys.readouterr().err

    @pytest.mark.parametrize(
        ("state", "expected"),
        [
            ("deferred", "pytoolsの再インストールを延期しました。"),
            ("failed", "pytoolsの再インストールに失敗しました。"),
        ],
    )
    def test_known_state_prints_notice(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        state: str,
        expected: str,
    ) -> None:
        """延期と失敗を最終案内へ表示する。"""
        monkeypatch.setenv("DOTFILES_PYTOOLS_INSTALL_STATE", state)
        monkeypatch.delenv("DOTFILES_PYTOOLS_INSTALL_DETAIL", raising=False)

        with pytest.raises(SystemExit) as exc_info:
            post_apply.main(runner=lambda: ([], []))

        assert exc_info.value.code == 0
        assert expected in capsys.readouterr().err

    def test_notice_includes_detail(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """補足がある場合は状態案内の本文へ含める。"""
        monkeypatch.setenv("DOTFILES_PYTOOLS_INSTALL_STATE", "failed")
        monkeypatch.setenv("DOTFILES_PYTOOLS_INSTALL_DETAIL", "uv tool install error")

        with pytest.raises(SystemExit):
            post_apply.main(runner=lambda: ([], []))

        assert "詳細: uv tool install error" in capsys.readouterr().err

    def test_unknown_state_raises_value_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """送信契約にない状態を黙って無視しない。"""
        monkeypatch.setenv("DOTFILES_PYTOOLS_INSTALL_STATE", "unknown")

        with pytest.raises(ValueError, match="未知のpytools再導入状態"):
            post_apply.main(runner=lambda: ([], []))


class TestDefaultSteps:
    """`_DEFAULT_STEPS`の登録内容と先行工程・対象OSの宣言を検証する。"""

    # 共有資源と実行ファイルの前提から定めた先行工程。宣言の欠落は同時実行による競合を招く。
    _EXPECTED_PREDECESSORS: dict[str, set[str]] = {
        "mise セットアップ": {"npm/pnpm サプライチェーン対策", "bin PATH 登録 (Windows)"},
        "Codex CLI の導入と更新": {"mise セットアップ", "Codex 診断ログの通常ストレージ復元 (Linux)"},
        "Codex 診断ログの通常ストレージ復元 (Linux)": {"Codex リンクの同期"},
        "Codex の Claude MCP 登録削除": {"Codex CLI の導入と更新"},
        "Claude Code CLI の導入と更新": {"mise セットアップ"},
        "Codex plugin snapshot の生成": {"Claude Code plugin のインストール"},
        "Codex plugin のインストール": {
            "Codex の Claude MCP 登録削除",
            "Codex plugin snapshot の生成",
            "Codex リンクの同期",
            "旧配布物の削除",
        },
        "agent-toolkit ルールの同期": {"旧配布物の削除"},
        "Claude Code plugin のインストール": {"Claude Code CLI の導入と更新"},
        "Claude Code plugin cache の旧版削除": {"Claude Code plugin のインストール"},
        "agents_serverのuv環境ウォームアップ": {"Claude Code plugin のインストール", "Codex plugin のインストール"},
        "hookスクリプトのuv環境ウォームアップ": {"agents_serverのuv環境ウォームアップ"},
        "pyfltr MCPのuv環境ウォームアップ": {"Claude Code plugin のインストール", "Codex plugin のインストール"},
        "旧Codex User scope MCP登録の移行": {"Claude Code CLI の導入と更新", "Codex plugin のインストール"},
        "Claude 設定": {"Claude Code plugin のインストール", "旧Codex User scope MCP登録の移行"},
        "claude-statusline バイナリの取得": {"Codex CLI の導入と更新"},
        "libarchive (Windows)": {"mise セットアップ"},
        "atk serve 自動起動セットアップ (Linux)": {"Claude Code plugin のインストール"},
        "dotfiles自動更新タイマー セットアップ (Linux)": {"atk serve 自動起動セットアップ (Linux)"},
    }
    _WINDOWS_STEPS = {
        "bin PATH 登録 (Windows)",
        "MSYS 環境変数 (Windows)",
        "libarchive (Windows)",
        "Windowsレジストリ設定",
        "SendTo ショートカット (Windows)",
        "メディアリモコン自動起動 (Windows/stheno)",
        "ユーザー PATH 整理 (Windows)",
    }
    _LINUX_STEPS = {
        "Codex 診断ログの通常ストレージ復元 (Linux)",
        "tmux プラグインの導入 (Linux)",
        "atk serve 自動起動セットアップ (Linux)",
        "dotfiles自動更新タイマー セットアップ (Linux)",
    }

    def test_step_names_are_unique_and_declarations_resolve(self) -> None:
        """ステップ名は一意であり、全ての先行工程の宣言が既存ステップを指し循環しない。"""
        steps = post_apply._DEFAULT_STEPS  # noqa: SLF001
        names = [step.name for step in steps]
        assert len(names) == len(set(names))
        post_apply._resolve_predecessors(steps)  # noqa: SLF001

    def test_predecessor_declarations(self) -> None:
        """共有資源を扱うステップの組へ先行工程を宣言する。"""
        declared = {step.name: set(step.after) for step in post_apply._DEFAULT_STEPS if step.after}  # noqa: SLF001
        assert declared == self._EXPECTED_PREDECESSORS

    def test_user_path_cleanup_follows_all_other_steps(self) -> None:
        """ユーザーPATHの整理は他の全ステップの後に実行する。"""
        steps = post_apply._DEFAULT_STEPS  # noqa: SLF001
        assert steps[-1].name == "ユーザー PATH 整理 (Windows)"
        assert [step.name for step in steps if step.after_all_preceding] == ["ユーザー PATH 整理 (Windows)"]

    def test_platform_declarations(self) -> None:
        """実行中のOSでは何もしないステップへ対象OSを宣言する。"""
        steps = post_apply._DEFAULT_STEPS  # noqa: SLF001
        assert {step.name for step in steps if step.platforms == ("win32",)} == self._WINDOWS_STEPS
        assert {step.name for step in steps if step.platforms == ("linux",)} == self._LINUX_STEPS
        assert not [step.name for step in steps if step.platforms not in ((), ("win32",), ("linux",))]

    def test_pyfltr_mcp_warmup_registered_once(self) -> None:
        """pyfltr MCPのウォームアップを1回だけ登録する。"""
        names = [step.name for step in post_apply._DEFAULT_STEPS]  # noqa: SLF001
        assert names.count("pyfltr MCPのuv環境ウォームアップ") == 1

    def test_statusline_binary_step_registered(self):
        """claude-statuslineバイナリ取得ステップを1回登録する。"""
        names = [step.name for step in post_apply._DEFAULT_STEPS]  # pylint: disable=protected-access  # noqa: SLF001
        assert names.count("claude-statusline バイナリの取得") == 1

    def test_removed_steps_are_not_registered(self) -> None:
        """廃止済みの計画移行と旧計画ビューアーの自動起動を登録しない。"""
        names = [step.name for step in post_apply._DEFAULT_STEPS]  # noqa: SLF001
        assert not [name for name in names if "claude-plans-viewer" in name or "計画" in name]


class TestPluginRecommendations:
    """``install_claude_plugins.run()`` の推奨コマンド戻り値による案内出力。"""

    def test_prints_single_recommendation_without_continuation(
        self,
        capsys: pytest.CaptureFixture[str],
    ):
        """推奨コマンドが 1 件のみなら && も継続記号も付けず単一行で出力する。"""
        fake_recommendations = ["claude plugin install a --scope=user"]
        steps: list[tuple[str, post_apply.Callable[[], post_apply.StepReturn]]] = [
            ("plugins", _make_plugin_step(fake_recommendations)),
        ]
        with pytest.raises(SystemExit):
            post_apply.main(runner=lambda: post_apply.run(steps=steps))
        stdout_lines = capsys.readouterr().out.splitlines()
        assert any("推奨プラグイン設定" in line for line in stdout_lines)
        assert "claude plugin install a --scope=user" in stdout_lines
        # コマンド行は cmd.exe での貼り付け失敗を避けるため行頭インデントを付けない。
        assert not any(line.startswith(" ") for line in stdout_lines if "claude plugin" in line)
        assert not any("&&" in line for line in stdout_lines)

    def test_prints_multiple_recommendations_bash(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ):
        """bash 系では && \\ で連結し、最終行のみ継続記号なしで出力する。"""
        monkeypatch.setattr(post_apply.sys, "platform", "linux")
        fake_recommendations = [
            "claude plugin install a --scope=user",
            "claude plugin install b --scope=user",
            "claude plugin disable c --scope=user",
        ]
        steps: list[tuple[str, post_apply.Callable[[], post_apply.StepReturn]]] = [
            ("plugins", _make_plugin_step(fake_recommendations)),
        ]
        with pytest.raises(SystemExit):
            post_apply.main(runner=lambda: post_apply.run(steps=steps))
        stdout_lines = capsys.readouterr().out.splitlines()
        assert any("推奨プラグイン設定" in line for line in stdout_lines)
        assert "claude plugin install a --scope=user && \\" in stdout_lines
        assert "claude plugin install b --scope=user && \\" in stdout_lines
        assert "claude plugin disable c --scope=user" in stdout_lines
        # コマンド行は cmd.exe での貼り付け失敗を避けるため行頭インデントを付けない。
        assert not any(line.startswith(" ") for line in stdout_lines if "claude plugin" in line)

    def test_prints_multiple_recommendations_windows(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
        capsys: pytest.CaptureFixture[str],
    ):
        """Windows では && ^ で連結し、最終行のみ継続記号なしで出力する。"""
        monkeypatch.setattr(post_apply.sys, "platform", "win32")
        fake_recommendations = [
            "claude plugin install a --scope=user",
            "claude plugin disable b --scope=user",
        ]
        steps: list[tuple[str, post_apply.Callable[[], post_apply.StepReturn]]] = [
            ("plugins", _make_plugin_step(fake_recommendations)),
        ]
        with caplog.at_level("INFO", logger=post_apply.logger.name), pytest.raises(SystemExit):
            post_apply.main(runner=lambda: post_apply.run(steps=steps))
        stdout_lines = capsys.readouterr().out.splitlines()
        assert "claude plugin install a --scope=user && ^" in stdout_lines
        assert "claude plugin disable b --scope=user" in stdout_lines
        # cmd.exe では `^` 継続後の行頭空白が解析エラーを起こすため、コマンド行は無インデントとする。
        assert not any(line.startswith(" ") for line in stdout_lines if "claude plugin" in line)

    def test_no_output_when_no_recommendations(
        self,
        caplog: pytest.LogCaptureFixture,
        capsys: pytest.CaptureFixture[str],
    ):
        """推奨コマンドが空なら案内を出力しない。"""
        steps: list[tuple[str, post_apply.Callable[[], post_apply.StepReturn]]] = [
            ("ok", _make_step("ok", [], changed=False)),
        ]
        with caplog.at_level("INFO", logger=post_apply.logger.name), pytest.raises(SystemExit):
            post_apply.main(runner=lambda: post_apply.run(steps=steps))
        messages = [record.getMessage() for record in caplog.records]
        assert not any("推奨プラグイン設定" in m for m in messages)
        stdout_lines = capsys.readouterr().out.splitlines()
        assert not any("claude plugin" in line for line in stdout_lines)
