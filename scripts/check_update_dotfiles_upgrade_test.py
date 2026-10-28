"""check_update_dotfiles_upgrade.pyの単体テスト。"""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys
import types

import check_update_dotfiles_upgrade as upgrade
import pytest

from pytools._internal import antigravity_plugin, setup_codex_cli, setup_herdr_cli

_SCRIPT = pathlib.Path(__file__).with_name("check_update_dotfiles_upgrade.py")


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


def test_update_info_log_succeeds(tmp_path: pathlib.Path) -> None:
    """通常の進捗とメッセージ中のWARNINGという単語は警告扱いにしない。"""
    (tmp_path / "update-dotfiles.log").write_text("2026-10-28 12:00:00 run=check INFO WARNING設定を確認\n", encoding="utf-8")
    upgrade.verify_update_logs(tmp_path)


def test_expected_update_warnings_succeed(tmp_path: pathlib.Path) -> None:
    """正常な更新の案内、隔離homeへ展開されたPATH項目、uvの複製切替は検証失敗にしない。"""
    missing = tmp_path / ".dotnet" / "tools"
    (tmp_path / "update-dotfiles.log").write_text(
        f"2026-10-28 12:00:00,000 run=check WARNING {antigravity_plugin.UPDATED_NOTICE}\n"
        "2026-10-28 12:00:00,000 run=check WARNING     PATH 整理: ユーザー PATH に存在しないエントリーを検出: "
        f"%USERPROFILE%\\.dotnet\\tools (展開: {missing})。"
        "再導入の予定が無ければWindowsの「環境変数」でユーザーのPathからこのエントリーを削除する\n",
        encoding="utf-8",
    )
    upgrade.verify_update_logs(
        tmp_path,
        stderr="warning: Failed to hardlink files; falling back to full copy. This may lead to degraded performance.\n",
    )


def test_allowed_notice_matches_post_apply_source() -> None:
    """許容する更新案内は、post-applyが実際に出す文言と一致する。"""
    assert upgrade._ANTIGRAVITY_UPDATED_NOTICE == antigravity_plugin.UPDATED_NOTICE  # pylint: disable=protected-access


@pytest.mark.parametrize(
    "message",
    [
        "pytoolsの再インストールに失敗しました。失敗理由を確認してupdate-dotfilesを再実行してください。",
        "PATH 整理: ユーザー PATH に存在しないエントリーを検出: C:\\outside\\bin (展開: C:\\outside\\bin)。削除する",
        f"{antigravity_plugin.UPDATED_NOTICE} 追加の文言",
    ],
)
def test_other_update_warnings_still_fail(tmp_path: pathlib.Path, message: str) -> None:
    """個別に許容した出力以外の警告は、失敗を伝える案内や隔離home外のPATH項目を含めて検出する。"""
    (tmp_path / "update-dotfiles.log").write_text(f"2026-10-28 12:00:00,000 run=check WARNING {message}\n", encoding="utf-8")
    with pytest.raises(upgrade.UpgradeCheckError, match="警告以上"):
        upgrade.verify_update_logs(tmp_path)


def test_allowed_notice_error_level_fails(tmp_path: pathlib.Path) -> None:
    """許容はWARNINGに限り、同じ文言でもERROR以上は検出する。"""
    (tmp_path / "update-dotfiles.log").write_text(
        f"2026-10-28 12:00:00,000 run=check ERROR {antigravity_plugin.UPDATED_NOTICE}\n", encoding="utf-8"
    )
    with pytest.raises(upgrade.UpgradeCheckError, match="警告以上"):
        upgrade.verify_update_logs(tmp_path)


def test_other_uv_warning_still_fails(tmp_path: pathlib.Path) -> None:
    """uvの複製切替以外のuv警告は検出する。"""
    (tmp_path / "update-dotfiles.log").write_text("run=test INFO 更新完了\n", encoding="utf-8")
    with pytest.raises(upgrade.UpgradeCheckError, match="Failed to build"):
        upgrade.verify_update_logs(tmp_path, stderr="warning: Failed to build package\n")


def test_missing_update_log_fails(tmp_path: pathlib.Path) -> None:
    """更新の警告観測が成立しない場合を成功にしない。"""
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
    """標準エラーの通常進捗と警告機能の説明はseverity診断として扱わない。"""
    (tmp_path / "update-dotfiles.log").write_text("run=test INFO 更新完了\n", encoding="utf-8")
    upgrade.verify_update_logs(tmp_path, stdout="warning settings enabled", stderr="Downloading package (1/2)\n")


class _FakeRegistry:
    """`HKCU\\Environment`の代わりに値を保持し、操作を記録する偽の実装。"""

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
    """通常profileの代わりに`tmp_path`配下を指す環境変数を返す。"""
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
    """検証が問い合わせるGitの値を返す。"""
    _write_update_log(arguments)
    values = {"rev-parse": "current-oid", "show": "1000000", "rev-list": "old-oid"}
    output = next((value for option, value in values.items() if option in arguments), "")
    return subprocess.CompletedProcess(arguments, 0, stdout=output, stderr="")


def _write_update_log(arguments: list[str]) -> None:
    """偽の公開更新も実更新と同じ永続ログを残す。"""
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
    """旧commit探索の基準を現行commit時刻の72時間前に固定する。"""
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
    """各OSで公開される実ランチャーを旧checkoutから選択する。"""
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
    # 既に他テストが解決した一時配置先を戻し、このテストのTMPDIRから再解決する。
    monkeypatch.setattr(upgrade.tempfile, "tempdir", None)
    monkeypatch.setenv("UV_TOOL_BIN_DIR", str(tmp_path / "ci-tool-bin"))
    monkeypatch.setenv("UV_TOOL_DIR", str(tmp_path / "ci-tools"))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "ci-codex-home"))
    monkeypatch.setenv("CODEX_INSTALL_DIR", str(tmp_path / "ci-codex-bin"))
    profile = tmp_path / "ci-profile"
    monkeypatch.setenv("LOCALAPPDATA", str(profile / "AppData" / "Local"))
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

    assert len(observations) == 2
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
        context.setattr(setup_codex_cli, "sys", platform)
        return setup_herdr_cli._launcher_path(), setup_codex_cli._visible_bin_dir()  # pylint: disable=protected-access


def test_initial_apply_failure_preserves_child_output_and_stops_update(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """初期適用の失敗を出力と終了コード付きで返し、公開更新を開始しない。"""
    uv = tmp_path / "uv"
    uv.write_bytes(b"test-uv")
    monkeypatch.setenv("UV", str(uv))
    calls: list[list[str]] = []

    def runner(arguments, **_kwargs):
        calls.append(arguments)
        if arguments[0] == "chezmoi":
            return subprocess.CompletedProcess(arguments, 23, stdout="child-out\n", stderr="child-error\n")
        values = {"rev-parse": "current-oid", "show": "1000000", "rev-list": "old-oid"}
        output = next((value for option, value in values.items() if option in arguments), "")
        return subprocess.CompletedProcess(arguments, 0, stdout=output, stderr="")

    with pytest.raises(upgrade.UpgradeCheckError, match="終了コード23"):
        upgrade.run_upgrade_check(
            tmp_path, "windows", runner=runner, registry=_FakeRegistry(), profile_env=_profile_env(tmp_path)
        )
    stdout, stderr = capsys.readouterr()
    assert "child-out\n" in stdout
    assert stderr == "child-error\n"
    assert calls[-1][0] == "chezmoi"
    assert sum("update-ref" in arguments for arguments in calls) == 1


@pytest.mark.parametrize(("old_pin", "expected"), [("3.13\n", "3.13"), (None, None)])
def test_initial_apply_pins_old_python_version(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, old_pin: str | None, expected: str | None
) -> None:
    """初期適用だけ旧commitの`.python-version`へ固定し、更新は固定せずに実行する。"""
    uv = tmp_path / "uv"
    uv.write_bytes(b"test-uv")
    monkeypatch.setenv("UV", str(uv))
    monkeypatch.delenv("UV_PYTHON", raising=False)
    child_envs: dict[str, dict[str, str]] = {}

    def runner(arguments, **kwargs):
        if "ls-tree" in arguments:
            return subprocess.CompletedProcess(arguments, 0, stdout=".python-version\n" if old_pin else "", stderr="")
        if "old-oid:.python-version" in arguments:
            assert old_pin is not None
            return subprocess.CompletedProcess(arguments, 0, stdout=old_pin, stderr="")
        if arguments[0] == "chezmoi":
            child_envs["initial"] = kwargs["env"]
        elif arguments[0] in {"bash", "cmd.exe"}:
            child_envs["upgrade"] = kwargs["env"]
        return _git_answers(arguments)

    upgrade.run_upgrade_check(tmp_path, "linux", runner=runner, profile_env=_profile_env(tmp_path))

    assert child_envs["initial"].get("UV_PYTHON") == expected
    assert "UV_PYTHON" not in child_envs["upgrade"]


def test_verify_updated_oid_rejects_mismatch() -> None:
    """更新後OIDが検証開始時の現行OIDと異なる場合は失敗する。"""
    with pytest.raises(upgrade.UpgradeCheckError, match="expected=current actual=other"):
        upgrade.verify_updated_oid("other", "current")


def test_create_local_remote_can_advance_checkout(tmp_path: pathlib.Path) -> None:
    """bare remoteが旧・現行commitのobjectを持ち、旧checkoutを更新できる。"""

    def git(*arguments: str | pathlib.Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(["git", *map(str, arguments)], check=True, capture_output=True, text=True, encoding="utf-8")

    source_repo = tmp_path / "source"
    bare_repo = tmp_path / "remote.git"
    checkout = tmp_path / "checkout"
    git("init", source_repo)
    git("-C", source_repo, "config", "user.name", "upgrade-check-test")
    git("-C", source_repo, "config", "user.email", "upgrade-check-test@example.invalid")
    tracked = source_repo / "tracked.txt"
    tracked.write_text("old\n", encoding="utf-8")
    git("-C", source_repo, "add", "tracked.txt")
    git("-C", source_repo, "commit", "-m", "old")
    old_oid = git("-C", source_repo, "rev-parse", "HEAD").stdout.strip()
    tracked.write_text("current\n", encoding="utf-8")
    git("-C", source_repo, "commit", "-am", "current")
    current_oid = git("-C", source_repo, "rev-parse", "HEAD").stdout.strip()

    upgrade.create_local_remote(source_repo, bare_repo, old_oid)
    git("clone", "--branch", "upgrade-check", bare_repo, checkout)
    assert git("-C", checkout, "rev-parse", "HEAD").stdout.strip() == old_oid

    git("--git-dir", bare_repo, "update-ref", "refs/heads/upgrade-check", current_oid)
    git("-C", checkout, "pull", "--ff-only")
    assert git("-C", checkout, "rev-parse", "HEAD").stdout.strip() == current_oid


_REG_SZ = 1
_REG_EXPAND_SZ = 2


def _registry_writing_runner(registry: _FakeRegistry, *, fail: bool):
    """公開ランチャーの実行中にレジストリを書き換え、指定時は失敗する偽のrunnerを返す。"""

    def runner(arguments: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        if arguments[0] == "cmd.exe":
            registry.values["LIBARCHIVE"] = ("C:\\temp\\home\\libarchive.dll", _REG_SZ)
            registry.values["Path"] = ("%USERPROFILE%\\bin;C:\\temp\\home\\.herdr", _REG_EXPAND_SZ)
            del registry.values["MSYS"]
            if fail:
                return subprocess.CompletedProcess(arguments, 9, stdout="", stderr="")
        return _git_answers(arguments)

    return runner


@pytest.mark.parametrize("fail", [False, True])
def test_windows_registry_restored(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    fail: bool,
) -> None:
    """検証中に追加・変更・削除された値を、成功時と子プロセス失敗時の双方で退避前の値と値型へ戻す。"""
    monkeypatch.setenv("UV", str(tmp_path / "uv"))
    (tmp_path / "uv").write_bytes(b"uv")
    saved: dict[str, tuple[object, int]] = {
        "Path": ("%USERPROFILE%\\bin", _REG_EXPAND_SZ),
        "MSYS": ("winsymlinks:nativestrict", _REG_SZ),
    }
    registry = _FakeRegistry(saved)

    runner = _registry_writing_runner(registry, fail=fail)
    if fail:
        with pytest.raises(upgrade.UpgradeCheckError, match="終了コード9"):
            upgrade.run_upgrade_check(tmp_path, "windows", runner=runner, registry=registry, profile_env=_profile_env(tmp_path))
    else:
        upgrade.run_upgrade_check(tmp_path, "windows", runner=runner, registry=registry, profile_env=_profile_env(tmp_path))

    assert registry.values == saved
    stdout = capsys.readouterr().out
    for name in ("LIBARCHIVE", "MSYS", "Path"):
        assert f"検証前の値へ戻したユーザー環境変数: {name}" in stdout


def test_linux_does_not_touch_registry(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Linuxではレジストリの実装を呼ばない。"""
    monkeypatch.setenv("UV", str(tmp_path / "uv"))
    (tmp_path / "uv").write_bytes(b"uv")
    registry = _FakeRegistry({"Path": ("x", _REG_SZ)})

    upgrade.run_upgrade_check(
        tmp_path,
        "linux",
        runner=lambda arguments, **_kwargs: _git_answers(arguments),
        registry=registry,
        profile_env=_profile_env(tmp_path),
    )

    assert not registry.operations


@pytest.mark.parametrize(
    ("platform_name", "relative"),
    [
        ("linux", ".local/bin/codex"),
        ("windows", "AppData/Local/Programs/Herdr"),
        ("windows", "AppData/Roaming/leaked"),
    ],
)
def test_profile_leak_fails_check(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, platform_name: str, relative: str
) -> None:
    """検証中に通常profile側の監視ディレクトリへ項目が作成されると、そのパスを含む失敗にする。

    失敗文は検証の子プロセスに加え、ランナー側の常駐プロセス・予約タスクとその対処先も確認先として示す。
    """
    monkeypatch.setenv("UV", str(tmp_path / "uv"))
    (tmp_path / "uv").write_bytes(b"uv")
    env = _profile_env(tmp_path)
    leaked = pathlib.Path(env["HOME"]) / relative

    def runner(arguments: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        if arguments[0] in ("cmd.exe", "bash"):
            leaked.mkdir(parents=True)
        return _git_answers(arguments)

    with pytest.raises(upgrade.UpgradeCheckError) as exc_info:
        upgrade.run_upgrade_check(tmp_path, platform_name, runner=runner, registry=_FakeRegistry(), profile_env=env)

    message = str(exc_info.value)
    assert str(leaked) in message
    assert "_isolated_env" in message
    assert "予約タスク" in message
    assert "update-dotfiles-upgrade-windows" in message


def test_profile_unchanged_passes(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """既存ファイルの内容が変わっても、項目名が変わらなければ成功する。"""
    monkeypatch.setenv("UV", str(tmp_path / "uv"))
    (tmp_path / "uv").write_bytes(b"uv")
    env = _profile_env(tmp_path)
    existing = pathlib.Path(env["HOME"]) / ".bashrc"
    existing.write_text("before\n", encoding="utf-8")

    def runner(arguments: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        if arguments[0] == "cmd.exe":
            existing.write_text("after\n", encoding="utf-8")
        return _git_answers(arguments)

    upgrade.run_upgrade_check(tmp_path, "windows", runner=runner, registry=_FakeRegistry(), profile_env=env)


def test_profile_leak_reported_with_original_failure(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """子プロセスが失敗した検証では元の例外を保ち、通常profileの差分を標準エラーへ書く。"""
    monkeypatch.setenv("UV", str(tmp_path / "uv"))
    (tmp_path / "uv").write_bytes(b"uv")
    env = _profile_env(tmp_path)
    leaked = pathlib.Path(env["HOME"]) / ".local" / "share" / "uv"

    def runner(arguments: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        if arguments[0] == "bash":
            leaked.mkdir(parents=True)
            return subprocess.CompletedProcess(arguments, 5, stdout="", stderr="")
        return _git_answers(arguments)

    with pytest.raises(upgrade.UpgradeCheckError, match="終了コード5"):
        upgrade.run_upgrade_check(tmp_path, "linux", runner=runner, registry=None, profile_env=env)

    assert str(leaked) in capsys.readouterr().err


def _fake_mise_shim(mise_dir: pathlib.Path) -> pathlib.Path:
    """uv本体ではない起動スクリプトを`uv`として置いたmiseの`shims`を返す。"""
    shims = mise_dir / "shims"
    shims.mkdir(parents=True)
    shim = shims / "uv"
    shim.write_text("#!/bin/sh\necho fake-mise-shim\n", encoding="utf-8")
    shim.chmod(0o755)
    (mise_dir / "installs" / "uv" / "1.0.0" / "bin").mkdir(parents=True)
    return shims


@pytest.mark.parametrize("location", ["MISE_DATA_DIR", "XDG_DATA_HOME", "HOME", "LOCALAPPDATA"])
def test_upgrade_check_ignores_parent_mise_shims(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, location: str, chezmoi_bin: pathlib.Path
) -> None:
    """親のmiseのshimが`PATH`の先頭にあっても、検証homeへ`UV`のuv本体を置き、子の`PATH`から親のmise配下を除く。

    `uv`の名前解決で選んだshimを複製するか、親の`PATH`を全て継承すると、検証homeでshimとの間のexecが往復して停止する。
    """
    profile_env = _profile_env(tmp_path)
    parent = tmp_path / "parent"
    for name in ("MISE_DATA_DIR", "XDG_DATA_HOME"):
        monkeypatch.delenv(name, raising=False)
    if location == "MISE_DATA_DIR":
        mise_dir = parent / "custom-mise"
        monkeypatch.setenv("MISE_DATA_DIR", str(mise_dir))
    elif location == "XDG_DATA_HOME":
        mise_dir = parent / "xdg-data" / "mise"
        monkeypatch.setenv("XDG_DATA_HOME", str(mise_dir.parent))
    elif location == "HOME":
        mise_dir = parent / "home" / ".local" / "share" / "mise"
        monkeypatch.setenv("HOME", str(parent / "home"))
    else:
        # Windowsでは既定の配置に加え、`%LOCALAPPDATA%\mise`も親のmiseのデータディレクトリとして扱う。
        mise_dir = parent / "local-app-data" / "mise"
        monkeypatch.setenv("LOCALAPPDATA", str(mise_dir.parent))
    platform_name = "windows" if location == "LOCALAPPDATA" else "linux"
    shims = _fake_mise_shim(mise_dir)
    installs_bin = mise_dir / "installs" / "uv" / "1.0.0" / "bin"
    native_bin = tmp_path / "native-bin"
    native_bin.mkdir()
    monkeypatch.setenv("PATH", os.pathsep.join((str(shims), str(installs_bin), str(native_bin), str(chezmoi_bin))))
    real_uv = tmp_path / "real-uv"
    real_uv.write_bytes(b"real-uv-binary")
    monkeypatch.setenv("UV", str(real_uv))
    child_envs: list[dict[str, str]] = []

    def runner(arguments: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        env = kwargs.get("env")
        if isinstance(env, dict):
            uv_name = "uv.exe" if platform_name == "windows" else "uv"
            assert (pathlib.Path(env["HOME"]) / ".local" / "bin" / uv_name).read_bytes() == b"real-uv-binary"
            child_envs.append(env)
        return _git_answers(arguments)

    upgrade.run_upgrade_check(tmp_path, platform_name, runner=runner, registry=_FakeRegistry(), profile_env=profile_env)

    assert len(child_envs) == 2
    for env in child_envs:
        entries = [pathlib.Path(entry) for entry in env["PATH"].split(os.pathsep) if entry]
        assert not any(entry.is_relative_to(mise_dir) for entry in entries), entries
        assert native_bin in entries


def test_cli_without_uv_variable_asks_to_restart_with_uv_run(tmp_path: pathlib.Path) -> None:
    """`UV`の無い起動は、`uv run --script`で起動し直すよう案内して終了コード1で終わる。"""
    env = {name: value for name, value in os.environ.items() if name != "UV"}

    result = subprocess.run(
        [sys.executable, str(_SCRIPT), "--platform", "linux", "--repo", str(tmp_path)],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        timeout=60,
    )

    assert result.returncode == 1
    assert result.stdout == ""
    assert len(result.stderr.splitlines()) == 1
    assert "uv run --script" in result.stderr


@pytest.mark.parametrize("platform_name", ["linux", "windows"])
def test_upgrade_check_cuts_routes_to_real_home(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, platform_name: str
) -> None:
    """検証homeの子へ、実ホームを探索起点・参照先とする経路を渡さない。

    実ホーム配下の一時領域から祖先探索したmiseが実ホームの設定を読むこと、
    親の`PATH`と`NVM_DIR`を通して更新処理が実ホームのツール配置を走査することを防ぐ。
    実ホームの`~/.local/bin`にだけあるchezmoiは、検証homeへ複製して名前で起動できるようにする。
    """
    real_home = tmp_path / "real-home"
    home_bin = real_home / ".local" / "bin"
    home_bin.mkdir(parents=True)
    (home_bin / "chezmoi").write_bytes(b"real-home-chezmoi")
    (home_bin / "chezmoi").chmod(0o755)
    nvm_bin = real_home / ".nvm" / "versions" / "node" / "v1" / "bin"
    nvm_bin.mkdir(parents=True)
    system_bin = tmp_path / "system-bin"
    system_bin.mkdir()
    for name in ("MISE_DATA_DIR", "XDG_DATA_HOME"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("HOME", str(real_home))
    monkeypatch.setenv("USERPROFILE", str(real_home))
    monkeypatch.setenv("NVM_DIR", str(real_home / ".nvm"))
    # 親の値を引き継いだだけで探索上限が検証rootの外を指す状態を作り、検証rootへの設定を区別する。
    monkeypatch.setenv("MISE_CEILING_PATHS", str(real_home))
    monkeypatch.setenv("PATH", os.pathsep.join((str(home_bin), str(nvm_bin), str(system_bin))))
    uv_dir = tmp_path / "uv-dist"
    uv_dir.mkdir()
    uv_suffix = ".exe" if platform_name == "windows" else ""
    (uv_dir / f"uv{uv_suffix}").write_bytes(b"uv")
    (uv_dir / f"uvx{uv_suffix}").write_bytes(b"uvx")
    monkeypatch.setenv("UV", str(uv_dir / f"uv{uv_suffix}"))
    monkeypatch.setenv("NPM_CONFIG_PREFIX", str(real_home / ".nvm" / "versions" / "node" / "v1"))
    child_envs: list[dict[str, str]] = []

    def runner(arguments: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        env = kwargs.get("env")
        if isinstance(env, dict):
            child_envs.append(dict(env))
            shared_bin = pathlib.Path(env["HOME"]) / ".local" / "bin"
            assert (shared_bin / "chezmoi").read_bytes() == b"real-home-chezmoi"
            assert (shared_bin / f"uvx{uv_suffix}").read_bytes() == b"uvx"
        return _git_answers(arguments)

    upgrade.run_upgrade_check(
        tmp_path, platform_name, runner=runner, registry=_FakeRegistry(), profile_env=_profile_env(tmp_path)
    )

    assert len(child_envs) == 2
    for env in child_envs:
        verification_home = pathlib.Path(env["HOME"])
        ceiling = pathlib.Path(env["MISE_CEILING_PATHS"])
        assert verification_home.is_relative_to(ceiling)
        assert not ceiling.is_relative_to(real_home)
        entries = [pathlib.Path(entry) for entry in env["PATH"].split(os.pathsep) if entry]
        assert not any(entry.is_relative_to(real_home) for entry in entries), entries
        assert system_bin in entries
        assert "NVM_DIR" not in env
        if platform_name == "linux":
            assert env["NPM_CONFIG_PREFIX"] == str(verification_home / ".local")
        else:
            assert "NPM_CONFIG_PREFIX" not in env
            assert pathlib.Path(env["APPDATA"]).is_relative_to(verification_home)


def test_upgrade_check_reports_missing_chezmoi(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """複製元のchezmoiを解決できない場合は、導入とPATHの確認を案内して止まる。"""
    empty_bin = tmp_path / "empty-bin"
    empty_bin.mkdir()
    monkeypatch.setenv("PATH", str(empty_bin))
    uv = tmp_path / "uv-binary"
    uv.write_bytes(b"uv")
    monkeypatch.setenv("UV", str(uv))

    def runner(arguments: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return _git_answers(arguments)

    with pytest.raises(upgrade.UpgradeCheckError, match="chezmoiが見つからない"):
        upgrade.run_upgrade_check(tmp_path, "linux", runner=runner, registry=None, profile_env=_profile_env(tmp_path))
