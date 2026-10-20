"""隔離した公開CLIを別プロセスで起動し、権限制限木の回収経路を検証する。"""

import json
import os
import pathlib
import stat
import subprocess
import sys

import pytest


def _run(environment: dict[str, str], *arguments: str, expected: int = 0) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        [sys.executable, "-m", "agent_toolkit.atk", *arguments],
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
        check=False,
    )
    assert result.returncode == expected, result.stdout + result.stderr
    assert "Traceback" not in result.stderr
    return result


def _registration(environment: dict[str, str], target: pathlib.Path) -> pathlib.Path:
    matches = [
        path
        for path in pathlib.Path(environment["XDG_STATE_HOME"]).rglob("*.json")
        if json.loads(path.read_text(encoding="utf-8")).get("path") == str(target)
    ]
    assert len(matches) == 1
    return matches[0]


def _restricted_tree(target: pathlib.Path, *, recent: bool = False, git: bool = False) -> list[tuple[pathlib.Path, int]]:
    directories = []
    parent = target
    for number, mode in enumerate((0, 0o100, 0o300, 0o500, 0o555)):
        parent = parent / f"restricted-{number}"
        parent.mkdir()
        payload = parent / "payload"
        payload.write_bytes(b"owned")
        payload.chmod(0)
        os.utime(payload, (1_000_000_000, 1_000_000_000))
        directories.append((parent, mode))
    if git:
        (parent / ".git").mkdir()
        os.utime(parent / ".git", (1_000_000_000, 1_000_000_000))
    if recent:
        (parent / "recent").write_text("使用中", encoding="utf-8")
    for directory, mode in reversed(directories):
        os.utime(directory, (1_000_000_000, 1_000_000_000))
        directory.chmod(mode)
    for child in target.iterdir():
        os.utime(child, (1_000_000_000, 1_000_000_000))
    os.utime(target, (1_000_000_000, 1_000_000_000))
    return directories


@pytest.mark.skipif(os.name != "posix", reason="POSIX権限木と隔離XDG環境の公開CLI検証")
@pytest.mark.parametrize("route", ["cleanup", "force", "recover", "child", "quarantine", "sweep", "unregistered"])
def test_public_cli_reclaims_restricted_tree(tmp_path: pathlib.Path, route: str) -> None:
    """同じmode列の木を、別プロセスの明示回収と起動時掃引で回収する。"""
    environment = {**os.environ, "XDG_CACHE_HOME": str(tmp_path / "cache"), "XDG_STATE_HOME": str(tmp_path / "state")}
    created = _run(environment, "managed-temp", "create", "--prefix", "public-permissions")
    target = pathlib.Path(created.stdout.strip())
    registration = _registration(environment, target)
    removed = target
    if route == "child":
        removed = target / "child"
        removed.mkdir()
    directories = _restricted_tree(removed)
    quarantine = None
    consuming = None
    try:
        if route in {"recover", "unregistered"}:
            registration.unlink()
        if route == "force":
            (target / ".agent-toolkit-managed-temp.json").unlink()
        if route == "quarantine":
            nonce = json.loads(registration.read_text(encoding="utf-8"))["nonce"]
            consuming = registration.with_name(f"{registration.name}.consuming-{nonce}")
            registration.replace(consuming)
            quarantine = target.parent / f".agent-toolkit-cleanup-{nonce}"
            target.replace(quarantine)
        if route in {"sweep", "unregistered"}:
            # 新規作成という通常コマンドの開始時に、期限超過の旧候補が掃引される。
            result = _run(environment, "managed-temp", "create", "--prefix", "after-sweep")
            after_sweep = pathlib.Path(result.stdout.strip())
            _run(environment, "managed-temp", "cleanup", "--path", str(after_sweep))
        else:
            # 明示回収の前に自動掃引へ回らないよう、登録対象の最上位だけを新しくする。
            os.utime(quarantine if quarantine is not None else target, None)
            flags = {"force": ["--force-remove"], "recover": ["--recover-registry"]}.get(route, [])
            result = _run(environment, "managed-temp", "cleanup", "--path", str(removed), *flags)
        assert not removed.exists()
        if route == "force":
            assert "検証を省いて" in result.stderr
        else:
            assert "警告:" not in result.stderr
        if route == "child":
            assert target.exists() and registration.exists()
            _run(environment, "managed-temp", "cleanup", "--path", str(target))
        assert not target.exists() and not registration.exists()
        assert quarantine is None or not quarantine.exists()
        assert consuming is None or not consuming.exists()
    finally:
        for directory, _mode in directories:
            if directory.exists():
                directory.chmod(0o700)


@pytest.mark.skipif(os.name != "posix", reason="POSIXの保持対象のmodeを公開CLIで検証する")
@pytest.mark.parametrize("reason", ["recent", "git"])
def test_public_startup_preserves_restricted_retained_tree(tmp_path: pathlib.Path, reason: str) -> None:
    """新しい子と.gitの保持根拠を権限制限木の中に置き、掃引後の元modeと警告不在を確認する。"""
    environment = {**os.environ, "XDG_CACHE_HOME": str(tmp_path / "cache"), "XDG_STATE_HOME": str(tmp_path / "state")}
    target = pathlib.Path(_run(environment, "managed-temp", "create", "--prefix", "retained").stdout.strip())
    registration = _registration(environment, target)
    directories = _restricted_tree(target, recent=reason == "recent", git=reason == "git")
    try:
        result = _run(environment, "managed-temp", "create", "--prefix", "after-retained")
        assert target.exists() and registration.exists()
        for directory, mode in directories:
            # 制限された祖先を開く前に、上から順に保持modeを確認する。
            assert stat.S_IMODE(directory.stat().st_mode) == mode
            directory.chmod(0o700)
        assert "警告:" not in result.stderr
        _run(environment, "managed-temp", "cleanup", "--path", result.stdout.strip())
    finally:
        for directory, _mode in directories:
            directory.chmod(0o700)
        _run(environment, "managed-temp", "cleanup", "--path", str(target))
