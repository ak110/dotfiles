# pylint: disable=useless-import-alias  # pytestのfixtureを試験モジュールの名前空間へ登録するため。
"""update_dotfilesの基本動作の試験。"""

# pylint: disable=protected-access

import contextlib
import json
import pathlib
import subprocess
import sys
from typing import Any

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import update_dotfiles  # noqa: E402  # pylint: disable=wrong-import-position
import update_dotfiles_process  # noqa: E402  # pylint: disable=wrong-import-position
import update_dotfiles_runtime  # noqa: E402  # pylint: disable=wrong-import-position
from update_dotfiles_test_helpers import (  # noqa: E402  # pylint: disable=wrong-import-position
    _REAL_PREPARE_AGENT_TOOLKIT_ENVIRONMENT,
    _fake_popen,
    _fake_run,
    _FakePopen,
    _TimeoutPopen,
)
from update_dotfiles_test_helpers import _isolate_sync_report as _isolate_sync_report  # noqa: E402  # pylint: disable=wrong-import-position
from update_dotfiles_test_helpers import _logs_environment as _logs_environment  # noqa: E402  # pylint: disable=wrong-import-position
from update_dotfiles_test_helpers import _no_codex_processes as _no_codex_processes  # noqa: E402  # pylint: disable=wrong-import-position
from update_dotfiles_test_helpers import _own_git_process_scan as _own_git_process_scan  # noqa: E402  # pylint: disable=wrong-import-position
from update_dotfiles_test_helpers import _real_stash_runtime as _real_stash_runtime  # noqa: E402  # pylint: disable=wrong-import-position
from update_dotfiles_test_helpers import _separate_git_recovery as _separate_git_recovery  # noqa: E402  # pylint: disable=wrong-import-position
from update_dotfiles_test_helpers import _skip_agent_toolkit_prepare as _skip_agent_toolkit_prepare  # noqa: E402  # pylint: disable=wrong-import-position


def test_plugin_pulls_precede_chezmoi(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """本体pull後・init前にGitプラグインだけを名前順に更新する。"""
    for name in ("20-last", "10-first", "plain"):
        plugin = tmp_path / "plugins" / name
        plugin.mkdir(parents=True)
        if name != "plain":
            (plugin / ".git").write_text("gitdir: fixture", encoding="utf-8")
    calls: list[list[str]] = []
    timeouts: list[int | None] = []
    monkeypatch.setattr(update_dotfiles_runtime, "DOTFILES_ROOT", tmp_path)
    monkeypatch.setattr(update_dotfiles_runtime, "LOCK_PATH", tmp_path / "update.lock")
    monkeypatch.setenv("UPDATE_DOTFILES_GIT_TIMEOUT_SEC", "17")
    monkeypatch.setattr(subprocess, "Popen", _fake_popen({}, calls, communicate_timeouts=timeouts))
    monkeypatch.setattr(subprocess, "run", _fake_run({}, calls))

    assert update_dotfiles.main() == 0
    assert calls[0][:2] == ["chezmoi", "git"]
    assert calls[1:3] == [
        ["git", "-C", str(tmp_path / "plugins" / name), "-c", "submodule.recurse=false", "pull", "--rebase"]
        for name in ("10-first", "20-last")
    ]
    assert calls[3][:2] == ["chezmoi", "init"]
    assert timeouts == [17, 17, 17]


@pytest.mark.parametrize("timeout", [False, True])
def test_plugin_pull_failure_continues_update(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    sync_report_path: pathlib.Path,
    timeout: bool,
) -> None:
    """失敗とtimeoutを対象名・Git出力付きで記録し、次のpullと以降の段を続けて終了コード1で終える。"""
    for name in ("10-fail", "20-next"):
        (tmp_path / "plugins" / name / ".git").mkdir(parents=True)
    monkeypatch.setattr(update_dotfiles_runtime, "DOTFILES_ROOT", tmp_path)
    monkeypatch.setattr(update_dotfiles_runtime, "LOCK_PATH", tmp_path / "update.lock")
    calls: list[list[str]] = []

    def popen(argv: list[str], **kwargs: Any) -> _FakePopen:
        if argv[0] == "chezmoi":
            return _FakePopen(argv, calls, **kwargs)
        if timeout:
            return _TimeoutPopen(argv, calls, error_text="partial plugin error")
        return _FakePopen(argv, calls, returncode=7, output_text="plugin stdout", error_text="plugin stderr", **kwargs)

    monkeypatch.setattr(subprocess, "Popen", popen)
    monkeypatch.setattr(subprocess, "run", _fake_run({}, calls))
    monkeypatch.setattr(update_dotfiles_process, "kill_process_tree", lambda _process: None)
    assert update_dotfiles.main() == 1
    assert [call[:2] for call in calls if call[0] == "chezmoi"] == [
        ["chezmoi", "git"],
        ["chezmoi", "init"],
        ["chezmoi", "status"],
        ["chezmoi", "diff"],
        ["chezmoi", "apply"],
    ]
    assert any("20-next" in " ".join(call) for call in calls)
    report = json.loads(sync_report_path.read_text(encoding="utf-8"))
    assert report["status"] == "failed"
    assert "10-fail" in report["failed_stage"]
    assert "20-next" in report["failed_stage"]
    assert "10-fail" in report["stderr_tail"]
    assert ("partial plugin error" if timeout else "plugin stderr") in report["stderr_tail"]
    if not timeout:
        assert "plugin stdout" in report["stderr_tail"]


@pytest.mark.parametrize("mode", ["full", "minimal"])
def test_public_mode_argument_persists_and_reaches_every_update_stage(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, mode: str
) -> None:
    """更新の公開入力から保存値を変え、Git・テンプレート・post-applyへ同じ実効値を渡す。"""
    configuration = tmp_path / "config.json"
    configuration.write_text(
        json.dumps({"dotfiles_mode": "minimal" if mode == "full" else "full", "other": 42}), encoding="utf-8"
    )
    monkeypatch.setattr(update_dotfiles.dotfiles_mode, "configuration_path", lambda: configuration)
    monkeypatch.setenv(update_dotfiles.dotfiles_mode.MODE_ENV, "full")
    monkeypatch.setattr(update_dotfiles_runtime, "DOTFILES_ROOT", tmp_path)
    monkeypatch.setattr(update_dotfiles_runtime, "LOCK_PATH", tmp_path / "update.lock")
    calls: list[list[str]] = []
    environments: list[dict[str, str]] = []
    monkeypatch.setattr(subprocess, "Popen", _fake_popen({}, calls, environments=environments))
    monkeypatch.setattr(subprocess, "run", _fake_run({}, calls, environments=environments))
    assert update_dotfiles.main(["--mode", mode]) == 0
    assert json.loads(configuration.read_text(encoding="utf-8")) == {"dotfiles_mode": mode, "other": 42}
    assert environments
    assert all(environment[update_dotfiles.dotfiles_mode.MODE_ENV] == mode for environment in environments)
    assert [call[:2] for call in calls if call[0] == "chezmoi"] == [
        ["chezmoi", "git"],
        ["chezmoi", "init"],
        ["chezmoi", "status"],
        ["chezmoi", "diff"],
        ["chezmoi", "apply"],
    ]


@pytest.mark.parametrize("prepare_code", [0, 1])
def test_agent_toolkit_environment_is_prepared_right_after_git_update(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], prepare_code: int
) -> None:
    """主checkoutのGit更新の直後、plugin更新と`chezmoi init`より前に共有依存環境を準備し、失敗しても後続の段を続ける。

    準備には主checkoutのlaunch.pyを使う。

    依存の固定内容が変わる更新で、準備までの間に起動した主checkoutの`atk`が未準備で終了する区間を縮めるためである。
    """
    monkeypatch.setattr(update_dotfiles, "_prepare_agent_toolkit_environment", _REAL_PREPARE_AGENT_TOOLKIT_ENVIRONMENT)
    monkeypatch.setattr(update_dotfiles_runtime, "DOTFILES_ROOT", tmp_path)
    monkeypatch.setattr(update_dotfiles_runtime, "LOCK_PATH", tmp_path / "update.lock")
    plugin = tmp_path / "plugins" / "extra"
    (plugin / ".git").mkdir(parents=True)
    calls: list[list[str]] = []
    monkeypatch.setattr(subprocess, "Popen", _fake_popen({}, calls))
    monkeypatch.setattr(subprocess, "run", _fake_run({"uv": prepare_code}, calls, stderr_by_command={"uv": "準備失敗の診断"}))
    assert update_dotfiles.main([]) == prepare_code
    launcher = str(tmp_path / "agent-toolkit" / "scripts" / "launch.py")
    prepare = [index for index, call in enumerate(calls) if launcher in call]
    assert len(prepare) == 1
    assert calls[prepare[0]][-2:] == ["--prepare", "agent_toolkit/atk.py"]
    stage_index = {tuple(call[:2]): index for index, call in enumerate(calls) if call[0] == "chezmoi"}
    plugin_pull = [index for index, call in enumerate(calls) if call[:3] == ["git", "-C", str(plugin)]]
    assert len(plugin_pull) == 1
    assert stage_index["chezmoi", "git"] < prepare[0] < plugin_pull[0] < stage_index["chezmoi", "init"]
    assert stage_index["chezmoi", "init"] < stage_index["chezmoi", "apply"]
    if prepare_code:
        assert "agent-toolkitの共有依存環境の準備" in capsys.readouterr().err


class TestFiveStepsInOrder:
    """diffを含む5処理が順に呼ばれ、成功時にexit code 0を返すことを検証する。"""

    def test_all_steps_succeed(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        calls: list[list[str]] = []
        monkeypatch.setattr(subprocess, "Popen", _fake_popen({}, calls))
        monkeypatch.setattr(
            subprocess,
            "run",
            _fake_run(
                {},
                calls,
                stdout_by_command={
                    "status": " M private-status-value\n",
                    "diff": "private-diff-value\n",
                },
            ),
        )
        monkeypatch.setattr(update_dotfiles_runtime, "LOCK_PATH", tmp_path / "locks" / "update-dotfiles.lock")

        assert update_dotfiles.main() == 0
        assert calls[0][:2] == ["chezmoi", "git"]
        assert calls[1][:2] == ["chezmoi", "init"]
        assert calls[2][:2] == ["chezmoi", "status"]
        assert calls[3][:2] == ["chezmoi", "diff"]
        assert calls[4][:2] == ["chezmoi", "apply"]
        assert "--quiet" in calls[0]
        assert "--force" in calls[4]
        log_text = update_dotfiles_runtime.LOG_PATH.read_text(encoding="utf-8")  # noqa: SLF001
        assert "update-dotfiles開始" in log_text
        expected_stages = [
            "1/5 git pull",
            "2/5 chezmoi init (テンプレート再展開)",
            "3/5 chezmoi status (apply予定のファイル)",
            "4/5 chezmoi diff (上書き前の差分)",
            "5/5 chezmoi apply (post-apply実行)",
        ]
        assert [line.partition("stage開始: ")[2] for line in log_text.splitlines() if "stage開始: " in line] == expected_stages
        assert [
            line.partition("stage終了: ")[2].partition(" exit=")[0] for line in log_text.splitlines() if "stage終了: " in line
        ] == expected_stages
        assert "update-dotfiles終了: exit=0" in log_text
        assert "private-status-value" not in log_text
        assert "private-diff-value" in log_text
        output = capsys.readouterr().out
        assert "=== [4/4] chezmoi apply" in output
        assert "=== [4/4] chezmoi diff" not in output

    def test_diff_precedes_forced_apply(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        calls: list[list[str]] = []
        monkeypatch.setattr(subprocess, "Popen", _fake_popen({}, calls))
        monkeypatch.setattr(
            subprocess,
            "run",
            _fake_run({}, calls, stdout_by_command={"diff": "diff output\n"}),
        )
        monkeypatch.setattr(update_dotfiles_runtime, "LOCK_PATH", tmp_path / "locks" / "update-dotfiles.lock")

        assert update_dotfiles.main() == 0
        assert [call[1] for call in calls] == ["git", "init", "status", "diff", "apply"]
        assert calls[3] == ["chezmoi", "diff", "--no-pager"]
        assert calls[4] == ["chezmoi", "apply", "--force"]
        captured = capsys.readouterr()
        assert "chezmoi diff" not in captured.out
        assert "diff output\n" not in captured.out
        assert "=== [4/4] chezmoi apply" in captured.out
        assert "diff output\n" in update_dotfiles_runtime.LOG_PATH.read_text(encoding="utf-8")  # noqa: SLF001
        assert not captured.err


def test_child_python_output_uses_utf8(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PYTHONIOENCODING", "cp932")

    assert update_dotfiles_runtime.child_env()["PYTHONIOENCODING"] == "utf-8:replace"  # noqa: SLF001


def test_missing_persistent_log_continues_updates(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, sync_report_path: pathlib.Path
) -> None:
    """永続ログを開始できなくても更新を続け、終了コード1と失敗した段を残す。"""
    calls: list[list[str]] = []
    monkeypatch.setattr(update_dotfiles_runtime, "configure_persistent_log", lambda _run_id: contextlib.nullcontext(None))
    monkeypatch.setattr(subprocess, "Popen", _fake_popen({}, calls))
    monkeypatch.setattr(subprocess, "run", _fake_run({}, calls))
    monkeypatch.setattr(update_dotfiles_runtime, "LOCK_PATH", tmp_path / "locks" / "update-dotfiles.lock")

    assert update_dotfiles.main() == 1
    assert [call[1] for call in calls] == ["git", "init", "status", "diff", "apply"]
    assert json.loads(sync_report_path.read_text(encoding="utf-8"))["failed_stage"] == "永続ログの開始"


@pytest.mark.parametrize("argument", ["--force", "--unknown"])
def test_removed_or_unknown_argument_exits_2(argument: str) -> None:
    """廃止済みまたは未知の引数はargparseの終了コード2で拒否する。"""
    with pytest.raises(SystemExit) as exc_info:
        update_dotfiles.main([argument])
    assert exc_info.value.code == 2


class TestFilterApplyPending:
    """`chezmoi status`出力の2列目フィルタを公開インターフェース経由で検証する。"""

    def test_second_column_non_space_lines_are_kept(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        status_output = "\n".join(["  .chezmoiroot", "A  .gitignore", "RM bin/foo"])
        calls: list[list[str]] = []
        monkeypatch.setattr(subprocess, "Popen", _fake_popen({}, calls))
        monkeypatch.setattr(
            subprocess,
            "run",
            _fake_run({}, calls, stdout_by_command={"status": status_output}),
        )
        monkeypatch.setattr(update_dotfiles_runtime, "LOCK_PATH", tmp_path / "locks" / "update-dotfiles.lock")

        assert update_dotfiles.main() == 0
        captured = capsys.readouterr()
        assert "RM bin/foo" in captured.out
        assert "  .chezmoiroot" not in captured.out
        assert "A  .gitignore" not in captured.out

    def test_short_lines_are_excluded(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        calls: list[list[str]] = []
        monkeypatch.setattr(subprocess, "Popen", _fake_popen({}, calls))
        monkeypatch.setattr(
            subprocess,
            "run",
            _fake_run({}, calls, stdout_by_command={"status": "A"}),
        )
        monkeypatch.setattr(update_dotfiles_runtime, "LOCK_PATH", tmp_path / "locks" / "update-dotfiles.lock")

        assert update_dotfiles.main() == 0
        assert "\nA\n" not in f"\n{capsys.readouterr().out}\n"


def test_stage_heading_precedes_child_output_when_stdout_is_not_a_terminal() -> None:
    """標準出力がパイプの場合も、段見出しがその段の子プロセスの出力より前に並ぶ。

    自動更新サービスのjournalや保存した出力では標準出力がブロックバッファになり、
    子プロセスが同じ出力先へ直接書くと、見出しが後段の出力の後にまとまって現れる。
    """
    libexec_dir = pathlib.Path(__file__).resolve().parent
    code = (
        "import sys\n"
        f"sys.path.insert(0, {str(libexec_dir)!r})\n"
        "import update_dotfiles\n"
        "update_dotfiles._run_step(1, 4, 'stage', [sys.executable, '-c', 'print(\"child\", flush=True)'])\n"
        "update_dotfiles._run_step(2, 4, 'next', [sys.executable, '-c', 'print(\"child2\", flush=True)'])\n"
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, check=False, encoding="utf-8")

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == ["=== [1/4] stage ===", "child", "=== [2/4] next ===", "child2"]
