from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import time
import types

import check_update_dotfiles_upgrade as upgrade
import pytest
from agent_toolkit._agents_server import service_forward

from pytools._internal import (
    antigravity_plugin,
    codex_runtime,
    install_codex_plugins,
    log_format,
    setup_herdr_cli,
    warmup_agents_server,
)

_SCRIPT = pathlib.Path(__file__).with_name("check_update_dotfiles_upgrade.py")
_CODEX_TRUST_MESSAGE = install_codex_plugins._CODEX_HOOK_TRUST_NOTICE.message  # pylint: disable=protected-access


def _write_old_script(checkout: pathlib.Path, requirement: str) -> None:
    script = checkout / "libexec" / "update_dotfiles.py"
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text(f'# /// script\n# requires-python = "{requirement}"\n# dependencies = []\n# ///\n', encoding="utf-8")


@pytest.fixture(autouse=True)
def fake_preflight_probes(monkeypatch: pytest.MonkeyPatch) -> None:
    run = upgrade.run_command

    def fake_run(arguments, *, cwd=None, env=None, runner=subprocess.run):
        if runner is not subprocess.run and cwd is not None and env is not None:
            if tuple(arguments[:3]) == ("git", "remote", "get-url"):
                return subprocess.CompletedProcess(arguments, 0, stdout=str(cwd.parents[1] / "remote.git"), stderr="")
            if tuple(arguments[1:]) == ("symbolic-ref", "HEAD"):
                _write_old_script(cwd, ">=3.15")
                return subprocess.CompletedProcess(arguments, 0, stdout="refs/heads/upgrade-check", stderr="")
            if arguments[1] == "for-each-ref":
                return subprocess.CompletedProcess(arguments, 0, stdout="origin refs/heads/upgrade-check", stderr="")
            if tuple(arguments[1:3]) == ("python", "find"):
                python = pathlib.Path(env["UV_PYTHON_INSTALL_DIR"]) / "python"
                return subprocess.CompletedProcess(arguments, 0, stdout=str(python), stderr="")
        return run(arguments, cwd=cwd, env=env, runner=runner)

    monkeypatch.setattr(upgrade, "run_command", fake_run)


@pytest.fixture(autouse=True)
def mock_hosted_runner(monkeypatch: pytest.MonkeyPatch) -> None:
    """Windowsの更新工程を模す既存テストは使い捨てrunner相当で実行する。"""
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("RUNNER_ENVIRONMENT", "github-hosted")
    monkeypatch.setenv("RUNNER_OS", "Windows")


@pytest.mark.parametrize("escaped", [None, "HOME", "XDG_CONFIG_HOME", "checkout登録", "上流ブランチ", "Gitの上流", "Python"])
def test_preflight_checks_resolved_paths(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, escaped: str | None) -> None:
    root = tmp_path / "root"
    home = root / "home"
    checkout = home / "dotfiles"
    bare = root / "remote.git"
    outside = tmp_path / "outside"
    env = {
        "HOME": str(home if escaped != "HOME" else outside),
        "USERPROFILE": str(home),
        "XDG_CONFIG_HOME": str(home / ".config" if escaped != "XDG_CONFIG_HOME" else outside),
        "UV_PYTHON_INSTALL_DIR": str(home / "python"),
        "UV_CACHE_DIR": str(home / "cache"),
        "UV_TOOL_BIN_DIR": str(home / "bin"),
        "UV_PYTHON": str(outside / "bin" / "python"),
        "VIRTUAL_ENV": str(outside),
    }
    _write_old_script(checkout, ">=3.16,<3.18")
    upgrade.register_upgrade_checkout(checkout if escaped != "checkout登録" else outside, env, "linux")

    def run(arguments, **kwargs):
        if arguments[1] == "symbolic-ref":
            value = "refs/heads/upgrade-check"
        elif arguments[1] == "for-each-ref":
            value = "origin refs/heads/upgrade-check" if escaped != "上流ブランチ" else "outside refs/heads/upgrade-check"
        elif arguments[0] == "git":
            value = bare if escaped != "Gitの上流" else outside
        else:
            assert tuple(arguments) == (
                home / "bin" / "uv",
                "python",
                "find",
                "--no-project",
                "--system",
                "--",
                ">=3.16,<3.18",
            )
            assert "UV_PYTHON" not in kwargs["env"]
            assert kwargs["env"]["UV_MANAGED_PYTHON"] == "1"
            value = home / "python" / "python" if escaped != "Python" else home / ".venv" / "bin" / "python"
        return subprocess.CompletedProcess(arguments, 0, stdout=str(value), stderr="")

    monkeypatch.setattr(upgrade, "run_command", run)
    if escaped is None:
        upgrade.verify_upgrade_inputs(root.resolve(), checkout, bare, env, "linux", runner=subprocess.run)
        assert env["UV_MANAGED_PYTHON"] == "1"
        assert env["UV_PYTHON"] == str(home / "python" / "python")
    else:
        with pytest.raises(upgrade.UpgradeCheckError, match=escaped):
            upgrade.verify_upgrade_inputs(root.resolve(), checkout, bare, env, "linux", runner=subprocess.run)


def test_public_upgrade_refuses_to_launch_with_foreign_home(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    uv = tmp_path / "uv"
    uv.write_bytes(b"fixture-uv")
    monkeypatch.setenv("UV", str(uv))
    isolated_env = upgrade.isolated_environment

    def escaped_home(*args):
        return {**isolated_env(*args), "HOME": str(tmp_path / "usual-home")}

    def runner(arguments, **_kwargs):
        assert arguments[0] not in {"chezmoi", "bash"}, "隔離外のhomeに対して初期適用や更新を起動してはならない"
        return _git_answers(arguments)

    monkeypatch.setattr(upgrade, "isolated_environment", escaped_home)
    with pytest.raises(upgrade.UpgradeCheckError, match="検証領域外のHOME"):
        upgrade.run_upgrade_check(tmp_path, "linux", runner=runner, profile_env=_profile_env(tmp_path))


@pytest.mark.parametrize(
    ("platform_name", "actions", "environment", "runner_os"),
    [
        ("windows", "", "", ""),
        ("windows", "true", "self-hosted", "Windows"),
        ("windows", "true", "github-hosted", "Linux"),
        ("linux", "", "", ""),
    ],
)
def test_refuses_windows_upgrade_before_any_work_outside_hosted_windows(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    platform_name: str,
    actions: str,
    environment: str,
    runner_os: str,
) -> None:
    monkeypatch.setenv("GITHUB_ACTIONS", actions)
    monkeypatch.setenv("RUNNER_ENVIRONMENT", environment)
    monkeypatch.setenv("RUNNER_OS", runner_os)
    monkeypatch.setenv("UV", "not-used")
    monkeypatch.setattr(upgrade, "sys", types.SimpleNamespace(platform="win32"))

    def runner(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        pytest.fail("実ユーザー上ではGitもセットアップも起動してはならない")

    with pytest.raises(upgrade.UpgradeCheckError, match=r"WinGet.*ユーザー単位.*GitHub-hosted.*Windows"):
        upgrade.run_upgrade_check(tmp_path, platform_name, runner=runner)


def test_blocked_windows_upgrade_does_not_offer_a_local_retry(tmp_path: pathlib.Path) -> None:
    """公開CLIは続けてよい環境を示し、同じ危険な環境での再試行へ誘導しない。"""
    result = subprocess.run(
        [sys.executable, str(_SCRIPT), "--platform", "windows", "--repo", str(tmp_path)],
        env={**os.environ, "RUNNER_ENVIRONMENT": "self-hosted", "PYTHONIOENCODING": "utf-8"},
        capture_output=True,
        check=False,
        text=True,
        encoding="utf-8",
        timeout=30,
    )
    assert result.returncode == 1
    assert "WinGet" in result.stderr and "GitHub-hosted Windows CI" in result.stderr
    assert "再実行" not in result.stderr


@pytest.mark.parametrize("outcome", [0, 23, "起動失敗"])
def test_old_python_pin_reports_command_timing(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    outcome: int | str,
) -> None:
    """成功・子の失敗・起動失敗の所要を診断へ残し、子の出力と例外を保つ。"""
    ticks = iter((10.0, 10.25))
    monkeypatch.setattr(time, "perf_counter", lambda: next(ticks))

    def runner(arguments: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        if outcome == "起動失敗":
            raise FileNotFoundError("git")
        assert isinstance(outcome, int)
        return subprocess.CompletedProcess(arguments, outcome, stdout="", stderr="子の診断\n")

    if outcome == 0:
        assert upgrade.old_python_pin(tmp_path, "old", runner=runner) is None
    else:
        with pytest.raises(upgrade.UpgradeCheckError):
            upgrade.old_python_pin(tmp_path, "old", runner=runner)
    stdout, stderr = capsys.readouterr()
    assert not stdout
    assert "所要=0.250秒" in stderr
    assert f"終了状態={outcome}" in stderr
    assert "ls-tree" in stderr and str(tmp_path) in stderr
    assert ("子の診断\n" in stderr) == (outcome == 23)


@pytest.fixture(name="chezmoi_bin", autouse=True)
def _chezmoi_bin(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> pathlib.Path:
    """検証が検証homeへ複製するchezmoiを、実ホームの外のディレクトリから`PATH`で解決できるようにする。

    CIのpytestジョブにはchezmoiが無く、実機のchezmoiを複製の元にすると試験が実行環境に依存するため、偽の実行ファイルを置く。
    """
    directory = tmp_path_factory.mktemp("chezmoi-bin")
    chezmoi = directory / "chezmoi"
    chezmoi.write_bytes(b"fake-chezmoi")
    chezmoi.chmod(0o755)
    monkeypatch.setenv("PATH", os.pathsep.join((str(directory), os.environ.get("PATH", ""))))
    return directory


@pytest.mark.parametrize("level", ["WARNING", "ERROR", "CRITICAL"])
def test_update_warning_fails_even_when_command_succeeded(tmp_path: pathlib.Path, level: str) -> None:
    """終了0で継続する警告も永続ログから検証失敗へ接続する。"""
    log = tmp_path / "update-dotfiles.log"
    log.write_text(f"2026-10-28 12:00:00 run=check {level} warmup failed\n", encoding="utf-8")
    with pytest.raises(upgrade.UpgradeCheckError, match="warmup failed"):
        upgrade.verify_update_logs(tmp_path)


@pytest.mark.parametrize(
    ("platform_name", "level", "source", "record", "tail", "accepted"),
    [
        ("windows", "WARNING", "installed", "same", "", True),
        ("windows", "WARNING", "installed", "separate-log", "", True),
        ("windows", "WARNING", "installed", "absent", "", False),
        ("windows", "WARNING", "installed", "other-run", "", False),
        ("windows", "WARNING", "installed", "other-target", "", False),
        ("windows", "WARNING", "installed", "without-publish", "", False),
        ("linux", "WARNING", "installed", "same", "", False),
        ("windows", "ERROR", "installed", "same", "", False),
        ("windows", "WARNING", "other", "same", "", False),
        ("windows", "WARNING", "missing", "same", "", False),
        ("windows", "WARNING", "installed", "same", "\n別の未確認診断", False),
    ],
)
def test_windows_job_warning_requires_published_mcp_from_same_run(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    platform_name: str,
    level: str,
    source: str,
    record: str,
    tail: str,
    accepted: bool,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    plugin = home / ".claude" / "plugins" / "cache" / "ak110-dotfiles" / "agent-toolkit" / "2.290.1" / "agent_toolkit"
    script = (tmp_path if source == "other" else plugin) / "agents_server_mcp.py"
    script.parent.mkdir(parents=True, exist_ok=True)
    if source != "missing":
        script.touch()
    target = script if record != "other-target" else script.with_name("other.py")
    published = "" if record == "without-publish" else "・管理サービスの公開"
    run = "other" if record == "other-run" else "check"
    status = log_format.format_status(
        "agent-toolkit warmup", f"MCPのinitialize・tools/list・終了{published}を確認 (0.2秒): {target}"
    )
    success = f"2026-10-28 12:00:00 run={run} INFO {status}\n" if record != "absent" else ""
    if record == "separate-log":
        (home / "update-dotfiles.log.1").write_text(success, encoding="utf-8")
        success = ""
    (home / "update-dotfiles.log").write_text(
        f"{success}2026-10-28 12:00:01 run=check {level} {script}: {service_forward.INHERITED_JOB_WARNING}{tail}\n",
        encoding="utf-8",
    )
    if accepted:
        upgrade.verify_update_logs(home, platform_name=platform_name)
        assert service_forward.INHERITED_JOB_WARNING in capsys.readouterr().err
    else:
        with pytest.raises(upgrade.UpgradeCheckError, match="起動元のWindows Jobは離脱を許可していません"):
            upgrade.verify_update_logs(home, platform_name=platform_name)


def test_update_warning_includes_following_command_output(tmp_path: pathlib.Path) -> None:
    (tmp_path / "update-dotfiles.log").write_text(
        "2026-10-28 12:00:00,000 run=check WARNING     mise: `install` に失敗\n"
        "mise ERROR failed to download tool\n"
        "  caused by: HTTP 403\n"
        "2026-10-28 12:00:01,000 run=check INFO 次の工程\n",
        encoding="utf-8",
    )
    with pytest.raises(upgrade.UpgradeCheckError) as caught:
        upgrade.verify_update_logs(tmp_path)
    assert "mise ERROR failed to download tool\n  caused by: HTTP 403" in str(caught.value)
    assert "次の工程" not in str(caught.value)


def test_update_info_log_succeeds(tmp_path: pathlib.Path) -> None:
    (tmp_path / "update-dotfiles.log").write_text("2026-10-28 12:00:00 run=check INFO WARNING設定を確認\n", encoding="utf-8")
    upgrade.verify_update_logs(tmp_path)


def test_expected_update_warnings_succeed(tmp_path: pathlib.Path) -> None:
    """正常な更新の案内、隔離homeへ展開されたPATH項目、uvの複製切替は検証失敗にしない。"""
    missing = tmp_path / ".dotnet" / "tools"
    (tmp_path / "update-dotfiles.log").write_text(
        f"2026-10-28 12:00:00,000 run=check WARNING {antigravity_plugin.UPDATED_NOTICE}\n"
        f"2026-10-28 12:00:00,000 run=check WARNING {_CODEX_TRUST_MESSAGE}\n"
        "2026-10-28 12:00:00,000 run=check WARNING     PATH 整理: ユーザー PATH に存在しないエントリーを検出: "
        f"%USERPROFILE%\\.dotnet\\tools (展開: {missing})。"
        "再導入の予定が無ければWindowsの「環境変数」でユーザーのPathからこのエントリーを削除する\n",
        encoding="utf-8",
    )
    upgrade.verify_update_logs(
        tmp_path,
        stderr="warning: Failed to hardlink files; falling back to full copy. This may lead to degraded performance.\n",
    )


# test-copy-contract: 更新案内の別の生成側とログ検収側の許容定義を一致させ、正常更新の誤失敗を防ぐ。
def test_allowed_notice_matches_post_apply_source() -> None:
    assert upgrade._ANTIGRAVITY_UPDATED_NOTICE == antigravity_plugin.UPDATED_NOTICE  # pylint: disable=protected-access
    assert upgrade._CODEX_HOOK_TRUST_NOTICE == _CODEX_TRUST_MESSAGE  # pylint: disable=protected-access


@pytest.mark.parametrize(
    "message",
    [
        "pytoolsの再インストールに失敗しました。失敗理由を確認してupdate-dotfilesを再実行してください。",
        "PATH 整理: ユーザー PATH に存在しないエントリーを検出: C:\\outside\\bin (展開: C:\\outside\\bin)。削除する",
        f"{antigravity_plugin.UPDATED_NOTICE} 追加の文言",
        f"{_CODEX_TRUST_MESSAGE} 追加の文言",
        "    codex plugins: app-server daemonの自動再起動に失敗 (exit 9: daemon restart detail)。"
        "実行中のCodexセッションを終了し、codex app-server daemon restartを実行してください。",
    ],
)
def test_other_update_warnings_still_fail(tmp_path: pathlib.Path, message: str) -> None:
    """個別に許容した出力以外の警告は、失敗を伝える案内や隔離home外のPATH項目を含めて検出する。"""
    (tmp_path / "update-dotfiles.log").write_text(f"2026-10-28 12:00:00,000 run=check WARNING {message}\n", encoding="utf-8")
    with pytest.raises(upgrade.UpgradeCheckError, match="警告以上"):
        upgrade.verify_update_logs(tmp_path)


@pytest.mark.parametrize("level", ["ERROR", "CRITICAL"])
@pytest.mark.parametrize(
    "message",
    [antigravity_plugin.UPDATED_NOTICE, _CODEX_TRUST_MESSAGE],
)
def test_allowed_notice_error_level_fails(tmp_path: pathlib.Path, message: str, level: str) -> None:
    (tmp_path / "update-dotfiles.log").write_text(f"2026-10-28 12:00:00,000 run=check {level} {message}\n", encoding="utf-8")
    with pytest.raises(upgrade.UpgradeCheckError, match="警告以上"):
        upgrade.verify_update_logs(tmp_path)


@pytest.mark.parametrize("message", ["plugin contents changed; hook approval required", "hook failed", "gcloud update failed"])
def test_hook_trust_notice_keeps_other_warning_failures(tmp_path: pathlib.Path, message: str) -> None:
    """承認案内と同居しても、実hook失敗や別ツールの警告は検出する。"""
    (tmp_path / "update-dotfiles.log").write_text(
        f"2026-10-28 12:00:00,000 run=check WARNING {_CODEX_TRUST_MESSAGE}\n"
        f"2026-10-28 12:00:00,000 run=check WARNING {message}\n",
        encoding="utf-8",
    )
    with pytest.raises(upgrade.UpgradeCheckError, match=message):
        upgrade.verify_update_logs(tmp_path)


def test_other_uv_warning_still_fails(tmp_path: pathlib.Path) -> None:
    (tmp_path / "update-dotfiles.log").write_text("run=test INFO 更新完了\n", encoding="utf-8")
    with pytest.raises(upgrade.UpgradeCheckError, match="Failed to build"):
        upgrade.verify_update_logs(tmp_path, stderr="warning: Failed to build package\n")


def test_missing_update_log_fails(tmp_path: pathlib.Path) -> None:
    with pytest.raises(upgrade.UpgradeCheckError, match="永続ログが作成されず"):
        upgrade.verify_update_logs(tmp_path)


@pytest.mark.parametrize("platform_name", ["linux", "windows"])
def test_public_upgrade_rejects_child_stderr_warning(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, platform_name: str
) -> None:
    """公開更新が終了0でも子ツールの標準エラーの警告を検証失敗へ伝える。"""
    uv = tmp_path / "uv"
    uv.write_bytes(b"fake-uv")
    monkeypatch.setenv("UV", str(uv))

    def runner(arguments, **_kwargs):
        result = _git_answers(arguments)
        if arguments[0] in {"bash", "cmd.exe"}:
            result.stderr = "warning: child tool could not initialize\n"
        return result

    with pytest.raises(upgrade.UpgradeCheckError, match="child tool could not initialize"):
        upgrade.run_upgrade_check(
            tmp_path, platform_name, runner=runner, registry=_FakeRegistry(), profile_env=_profile_env(tmp_path)
        )


def test_normal_child_progress_is_not_warning(tmp_path: pathlib.Path) -> None:
    (tmp_path / "update-dotfiles.log").write_text("run=test INFO 更新完了\n", encoding="utf-8")
    upgrade.verify_update_logs(tmp_path, stdout="warning settings enabled", stderr="Downloading package (1/2)\n")


class _FakeRegistry:
    def __init__(self, values: dict[str, tuple[object, int]] | None = None) -> None:
        self.values = dict(values or {})
        self.operations: list[str] = []

    def read(self) -> dict[str, tuple[object, int]]:
        self.operations.append("read")
        return dict(self.values)

    def write(self, name: str, value: object, kind: int) -> None:
        self.operations.append(f"write:{name}")
        self.values[name] = (value, kind)

    def delete(self, name: str) -> None:
        self.operations.append(f"delete:{name}")
        del self.values[name]


def _profile_env(tmp_path: pathlib.Path) -> dict[str, str]:
    profile = tmp_path / "normal-profile"
    (profile / ".local" / "bin").mkdir(parents=True, exist_ok=True)
    (profile / ".local" / "share").mkdir(parents=True, exist_ok=True)
    (profile / "AppData" / "Local" / "Programs").mkdir(parents=True, exist_ok=True)
    (profile / "AppData" / "Roaming").mkdir(parents=True, exist_ok=True)
    return {
        "HOME": str(profile),
        "USERPROFILE": str(profile),
        "LOCALAPPDATA": str(profile / "AppData" / "Local"),
        "APPDATA": str(profile / "AppData" / "Roaming"),
    }


def _git_answers(arguments: list[str]) -> subprocess.CompletedProcess[str]:
    _write_update_log(arguments)
    values = {"rev-parse": "current-oid", "show": "1000000", "rev-list": "old-oid"}
    output = next((value for option, value in values.items() if option in arguments), "")
    return subprocess.CompletedProcess(arguments, 0, stdout=output, stderr="")


def _write_update_log(arguments: list[str]) -> None:
    if arguments[0] in {"bash", "cmd.exe"}:
        home = pathlib.Path(arguments[-1]).parents[2]
        (home / "update-dotfiles.log").write_text("run=test INFO 更新完了\n", encoding="utf-8")


def test_cli_outputs_japanese_when_default_stream_encoding_is_not_utf8() -> None:
    """標準ストリームの文字コードがUTF-8でない環境でも日本語のCLI出力を維持する。"""
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "cp1252"
    result = subprocess.run(
        [sys.executable, str(_SCRIPT), "--help"],
        check=False,
        capture_output=True,
        env=env,
    )
    assert result.returncode == 0
    assert isinstance(result.stdout, bytes)
    assert result.stdout.decode("utf-8").find("約3日前のdotfiles") >= 0
    assert not result.stderr


def test_resolve_old_commit_uses_head_timestamp_minus_72_hours(tmp_path: pathlib.Path) -> None:
    calls: list[list[str]] = []

    def runner(arguments, **_kwargs):
        calls.append(arguments)
        output = "1000000\n" if "show" in arguments else "old-oid\n"
        return subprocess.CompletedProcess(arguments, 0, stdout=output, stderr="")

    assert upgrade.resolve_old_commit(tmp_path, "current-oid", runner=runner) == "old-oid"
    assert "--before=@740800" in calls[1]


def test_resolve_old_commit_rejects_missing_history(tmp_path: pathlib.Path) -> None:
    """対象時刻以前の履歴が無い場合は検証不能として失敗する。"""
    outputs = iter(("1000000\n", ""))

    def runner(arguments, **_kwargs):
        return subprocess.CompletedProcess(arguments, 0, stdout=next(outputs), stderr="")

    with pytest.raises(upgrade.UpgradeCheckError, match="値を返さなかった"):
        upgrade.resolve_old_commit(tmp_path, "current-oid", runner=runner)


def test_resolve_old_commit_reports_command_when_process_cannot_start(tmp_path: pathlib.Path) -> None:
    """子プロセスの起動前失敗へ実行コマンドを付加する。"""

    def runner(arguments, **_kwargs):
        raise FileNotFoundError(2, "No such file or directory", arguments[0])

    with pytest.raises(upgrade.UpgradeCheckError, match="子プロセスを起動できなかった") as exc_info:
        upgrade.resolve_old_commit(tmp_path, "current-oid", runner=runner)

    expected = ["git", "-C", str(tmp_path), "show", "-s", "--format=%ct", "current-oid"]
    assert repr(expected) in str(exc_info.value)
    assert isinstance(exc_info.value.__cause__, FileNotFoundError)


@pytest.mark.parametrize(
    ("platform_name", "expected"),
    (("linux", "update-dotfiles"), ("windows", "update-dotfiles.cmd")),
)
def test_platform_entrypoint_selects_real_launcher(tmp_path: pathlib.Path, platform_name: str, expected: str) -> None:
    assert pathlib.Path(upgrade.platform_entrypoint(tmp_path, platform_name)[-1]).name == expected


@pytest.mark.parametrize("platform_name", ["linux", "windows"])
def test_upgrade_check_isolates_uv_tools_and_child_output(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, platform_name: str
) -> None:
    """CIのtool配置を引き継がず、初期適用と更新へ隔離した配置とUTF-8を渡す。"""
    # 検証homeへ置くuvは、pytestを起動したuv本体を指す`UV`から取る。
    uv_executable = os.environ.get("UV")
    assert uv_executable, "uv run経由でpytestを起動する"
    runner_temp = tmp_path / "runner-temp"
    runner_temp.mkdir()
    monkeypatch.setenv("TMPDIR", str(runner_temp))

    monkeypatch.setattr(upgrade.tempfile, "tempdir", None)
    monkeypatch.setenv("UV_TOOL_BIN_DIR", str(tmp_path / "ci-tool-bin"))
    monkeypatch.setenv("UV_TOOL_DIR", str(tmp_path / "ci-tools"))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "ci-codex-home"))
    monkeypatch.setenv("CODEX_INSTALL_DIR", str(tmp_path / "ci-codex-bin"))
    profile = tmp_path / "ci-profile"
    monkeypatch.setenv("LOCALAPPDATA", str(profile / "AppData" / "Local"))
    monkeypatch.setenv("WIN_PD_OVERRIDE_LOCAL_APPDATA", str(profile / "platformdirs-local"))
    monkeypatch.setenv("APPDATA", str(profile / "AppData" / "Roaming"))
    monkeypatch.setenv("HERDR_HOME", str(profile / ".herdr"))
    monkeypatch.setenv("HERDR_INSTALL_DIR", str(profile / "herdr-bin"))
    observations: list[tuple[dict[str, str], pathlib.Path, pathlib.Path]] = []

    def runner(arguments: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        _write_update_log(arguments)
        env = kwargs.get("env")
        if env is not None:
            assert isinstance(env, dict)
            home = pathlib.Path(env["HOME"])
            assert home.is_relative_to(runner_temp)
            config_base = pathlib.Path(env["LOCALAPPDATA" if platform_name == "windows" else "XDG_CONFIG_HOME"])
            config = config_base / "agent-toolkit" / "config.json"
            assert config.is_relative_to(home)
            if arguments[0] == "chezmoi":
                (home / "dotfiles").mkdir()
                config.parent.mkdir(parents=True, exist_ok=True)
                config.write_text('{"language": "ja"}', encoding="utf-8")
            else:
                checkout = (home / "dotfiles").resolve()
                settings = json.loads(config.read_text(encoding="utf-8"))
                assert settings["language"] == "ja"
                assert json.loads(settings["repositories"]) == {
                    "github.com/ak110/dotfiles": {"path": str(checkout), "edit": "allow"}
                }
                if (platform_name == "windows") == (os.name == "nt"):
                    config_env = "WIN_PD_OVERRIDE_LOCAL_APPDATA" if platform_name == "windows" else "XDG_CONFIG_HOME"
                    with monkeypatch.context() as context:
                        context.setenv(config_env, env[config_env])
                        registered = warmup_agents_server._registered_checkout()  # pylint: disable=protected-access
                        assert registered == checkout
                        assert warmup_agents_server._checkout_skip(checkout, registered) is None  # pylint: disable=protected-access
                        script = checkout / "agent-toolkit" / "agent_toolkit" / "agents_server_mcp.py"
                        approved = warmup_agents_server._approved_scripts(registered, [])  # pylint: disable=protected-access
                        assert script in approved
            assert env["TMPDIR"] == str(runner_temp)
            assert env["CODEX_HOME"] == str(home / ".codex")
            assert pathlib.Path(env["CODEX_HOME"]).is_dir()
            shared_bin = home / ".local" / "bin"
            codex_bin = home / ".local" / "share" / "codex" / "bin" if platform_name == "windows" else shared_bin
            assert env["CODEX_INSTALL_DIR"] == str(codex_bin)
            # Windowsのinstallerが専用binを作成・置換できるよう、事前に作成しない。
            assert platform_name != "windows" or not codex_bin.exists()
            uv_name = "uv.exe" if platform_name == "windows" else "uv"
            assert (shared_bin / uv_name).is_file()
            assert env["HERDR_HOME"] == str(home / ".herdr")
            assert "HERDR_INSTALL_DIR" not in env
            launcher, codex_visible = _resolve_consumer_paths(monkeypatch, env, platform_name)
            assert codex_visible == codex_bin
            if platform_name == "windows":
                local_app_data = home / "AppData" / "Local"
                assert env["LOCALAPPDATA"] == str(local_app_data)
                assert env["WIN_PD_OVERRIDE_LOCAL_APPDATA"] == str(local_app_data)
                assert env["APPDATA"] == str(home / "AppData" / "Roaming")
                assert local_app_data.is_dir() and pathlib.Path(env["APPDATA"]).is_dir()
                herdr_bin = local_app_data / "Programs" / "Herdr" / "bin"
                assert launcher == herdr_bin / "herdr.exe"
                visible_bins = list(dict.fromkeys((str(codex_bin), str(shared_bin), str(herdr_bin))))
            else:
                # Linuxの消費側はAppDataを参照しないため、親の値を変更しない。
                assert env["LOCALAPPDATA"] == os.environ["LOCALAPPDATA"]
                assert env["APPDATA"] == os.environ["APPDATA"]
                assert launcher == shared_bin / "herdr"
                visible_bins = list(dict.fromkeys((str(codex_bin), str(shared_bin))))
            assert launcher.is_relative_to(home)
            assert not launcher.is_relative_to(profile)
            assert env["PATH"].split(os.pathsep)[: len(visible_bins)] == visible_bins
            bin_result = subprocess.run(
                [uv_executable, "tool", "dir", "--bin"],
                env=env,
                check=True,
                capture_output=True,
                encoding="utf-8",
                timeout=20,
            )
            tools_result = subprocess.run(
                [uv_executable, "tool", "dir"],
                env=env,
                check=True,
                capture_output=True,
                encoding="utf-8",
                timeout=20,
            )
            observations.append((env, pathlib.Path(bin_result.stdout.strip()), pathlib.Path(tools_result.stdout.strip())))
        values = {"rev-parse": "current-oid", "show": "1000000", "rev-list": "old-oid"}
        output = next((value for option, value in values.items() if option in arguments), "")
        return subprocess.CompletedProcess(arguments, 0, stdout=output, stderr="")

    upgrade.run_upgrade_check(
        tmp_path, platform_name, runner=runner, registry=_FakeRegistry(), profile_env=_profile_env(tmp_path)
    )

    assert len(observations) == (3 if platform_name == "windows" else 2)
    for env, bin_dir, tools_dir in observations:
        home = pathlib.Path(env["HOME"])
        assert env["USERPROFILE"] == str(home)
        assert bin_dir == home / ".local" / "bin"
        assert tools_dir.is_relative_to(home)
        assert tools_dir != pathlib.Path(os.environ["UV_TOOL_DIR"])
        assert env["PYTHONIOENCODING"] == "utf-8"


def _resolve_consumer_paths(
    monkeypatch: pytest.MonkeyPatch, env: dict[str, str], platform_name: str
) -> tuple[pathlib.Path, pathlib.Path]:
    """検証環境を受け取ったpost-applyの消費側が解決するHerdrランチャーとCodex可視binを返す。"""
    platform = types.SimpleNamespace(platform="win32" if platform_name == "windows" else "linux")
    with monkeypatch.context() as context:
        context.setattr(os, "environ", env)
        context.setattr(setup_herdr_cli, "sys", platform)
        context.setattr(codex_runtime, "sys", platform)
        return setup_herdr_cli._launcher_path(), codex_runtime.visible_bin_dir()  # pylint: disable=protected-access


@pytest.mark.parametrize(
    ("failed_upgrade", "failed_cleanup", "keep"),
    [(False, False, False), (True, False, False), (False, False, True), (True, True, False)],
)
def test_upgrade_reclaims_before_removing_root(tmp_path, monkeypatch, failed_upgrade, failed_cleanup, keep):
    roots = []

    def perform(*_args, temp_name, **_kwargs):
        root = pathlib.Path(temp_name)
        roots.append(root)
        (root / "evidence").touch()
        if failed_upgrade:
            raise upgrade.UpgradeCheckError("更新の診断")

    def stop(root):
        assert (root / "evidence").exists()
        if failed_cleanup:
            raise upgrade.UpgradeCheckError("回収の診断")

    monkeypatch.setattr(upgrade.tempfile, "tempdir", str(tmp_path))
    monkeypatch.setattr(upgrade, "_perform_isolated_upgrade", perform)
    monkeypatch.setattr(upgrade, "_stop_upgrade_services", stop)

    def run_upgrade():
        return upgrade._run_isolated_upgrade(  # pylint: disable=protected-access
            tmp_path, "linux", "old", "new", tmp_path, runner=subprocess.run, keep_verification_root=keep
        )

    if failed_upgrade:
        with pytest.raises(upgrade.UpgradeCheckError, match="更新の診断") as raised:
            run_upgrade()
        if failed_cleanup:
            assert "回収の診断" in str(raised.value)
    else:
        run_upgrade()
    assert roots[0].exists() is (keep or failed_cleanup)


def test_reclaims_only_services_from_the_upgrade_root(tmp_path):
    script = tmp_path / "agent_toolkit" / "_agents_server" / "service.py"
    script.parent.mkdir(parents=True)
    script.write_text("import time\ntime.sleep(30)\n", encoding="utf-8")
    first = tmp_path / "first"
    sibling = tmp_path / "sibling"

    def start(root):
        namespace = root / "agent-toolkit" / "services" / "agents-server"
        return subprocess.Popen([sys.executable, str(script), str(namespace), "test-generation"])

    owned, other = start(first), start(sibling)
    try:
        upgrade._stop_upgrade_services(first)  # pylint: disable=protected-access
        assert owned.poll() is not None
        assert other.poll() is None
    finally:
        for process in (owned, other):
            if process.poll() is None:
                process.terminate()
            process.wait(timeout=5)
