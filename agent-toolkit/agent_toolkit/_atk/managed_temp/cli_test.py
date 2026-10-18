"""`atk managed-temp`のCLIの引数、結果行と終了コードを検証する。"""

# pylint: disable=protected-access

from __future__ import annotations

import datetime
import json
import os
import pathlib
import subprocess
import sys
import threading
import types
from collections.abc import Callable

import pytest

from agent_toolkit._atk import managed_temp as subject
from agent_toolkit._atk.managed_temp import cli as cli_subject
from agent_toolkit._atk.managed_temp import inventory as inventory_subject
from agent_toolkit._atk.managed_temp import registry as managed_temp_registry
from agent_toolkit._atk.managed_temp import windows_security as managed_temp_windows_security
from agent_toolkit._atk.managed_temp.long_tree_test_support import create_long_posix_tree
from agent_toolkit._common import plugin_root_leases as lease_subject
from agent_toolkit._common import state_paths
from agent_toolkit._testing.git_repository import commit_all, git_output, run_git
from agent_toolkit._testing.managed_temp_support import (
    _install_windows_security_doubles,
    _isolated_cli_environment,
    setattr_in_managed_temp_modules,
)

_SCRIPT = pathlib.Path(__file__).resolve().parents[2] / "_managed_temp.py"
_MARKER_NAME = ".agent-toolkit-managed-temp.json"


@pytest.mark.skipif(os.name != "posix", reason="POSIX descriptor相対の長い木")
@pytest.mark.parametrize("state", ["normal", "recover", "consuming", "quarantine", "child", "child-resume", "retry"])
def test_cleanup_removes_long_relative_tree(
    state: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """長い木の通常・復元・中断再開を公開cleanupで終端する。"""
    parent = subject.create_managed_temp("long-tree", session_id="long-tree")
    target = parent
    if state.startswith("child"):
        assert subject.main(["create", "--prefix", "work", "--session-root", str(parent)]) == 0
        target = pathlib.Path(capsys.readouterr().out.splitlines()[-1])
    create_long_posix_tree(target)
    registry = managed_temp_registry._registry_path(parent)
    record = json.loads(registry.read_text(encoding="utf-8"))
    consuming = registry.with_name(f"{registry.name}.consuming-{record['nonce']}")
    quarantine = parent.parent / f".agent-toolkit-cleanup-{record['nonce']}"
    if state == "recover":
        registry.unlink()
    elif state in ("consuming", "quarantine"):
        registry.replace(consuming)
        if state == "quarantine":
            target.rename(quarantine)
    elif state in ("child-resume", "retry"):

        def interrupt(*_args: object, **_kwargs: object) -> None:
            raise OSError("削除開始時の中断")

        with monkeypatch.context() as patch:
            patch.setattr(inventory_subject, "_clear_directory", interrupt)
            assert subject.main(["cleanup", "--path", str(target)]) == 2
        assert target.is_dir()
        assert registry.is_file()
    arguments = ["cleanup", "--path", str(target)]
    if state == "recover":
        arguments.append("--recover-registry")
    assert subject.main(arguments) == 0, capsys.readouterr().err
    assert not target.exists()
    assert not consuming.exists()
    assert not quarantine.exists()
    if state.startswith("child"):
        assert parent.is_dir()
        assert not managed_temp_registry._registry_path(target).with_suffix(".child-cleanup").exists()
        subject.cleanup_managed_temp(parent)
    assert not registry.exists()


@pytest.mark.parametrize(
    ("entries", "expected_listed", "expected_operation"),
    [
        ([], [], None),
        ([{"path": "/tmp/first"}], [], "atk managed-temp cleanup --path /tmp/first"),
        (
            [{"path": "/tmp/first"}, {"path": "/tmp/second"}],
            ["/tmp/first", "/tmp/second"],
            "atk managed-temp cleanup --path",
        ),
    ],
)
def test_cleanup_without_path_reports_managed_targets(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    entries: list[managed_temp_registry._ManagedTempEntry],
    expected_listed: list[str],
    expected_operation: str | None,
) -> None:
    """path欠落時は管理対象の件数に応じた再実行情報を次の操作の行で示す。"""
    setattr_in_managed_temp_modules(monkeypatch, "list_managed_temp", lambda: entries)
    setattr_in_managed_temp_modules(
        monkeypatch, "cleanup_managed_temp", lambda *_args, **_kwargs: pytest.fail("cleanupを呼んだ")
    )

    assert subject.main(["cleanup"]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    lines = captured.err.splitlines()
    assert lines[0].startswith("失敗: --pathを指定してください。")
    assert lines[1 : 1 + len(expected_listed)] == expected_listed
    next_action = lines[1 + len(expected_listed)]
    assert next_action.startswith("次の操作: ")
    if expected_operation is not None:
        assert expected_operation in next_action


def test_cleanup_passes_force_remove_to_inventory(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """公開CLIの`--force-remove`を後始末処理へ渡す。"""
    calls: list[tuple[pathlib.Path, bool, bool]] = []

    def cleanup(path: pathlib.Path, *, recover_registry: bool, force_remove: bool) -> None:
        calls.append((path, recover_registry, force_remove))

    monkeypatch.setattr(cli_subject, "cleanup_managed_temp", cleanup)

    assert subject.main(["cleanup", "--path", str(tmp_path / "target"), "--force-remove"]) == 0
    assert calls == [(tmp_path / "target", False, True)]


def test_session_id_create_is_idempotent_and_cleanup_resolves_target(tmp_path: pathlib.Path) -> None:
    """同じsession_idの作成は既存pathを返し、session_id指定で回収できる。"""
    env, _ = _isolated_cli_environment(tmp_path)
    command = [sys.executable, str(_SCRIPT), "create", "--prefix", "session", "--session-id", "session-1"]

    first = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", check=False, env=env)
    second = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", check=False, env=env)
    target = pathlib.Path(first.stdout.strip())
    cleaned = subprocess.run(
        [sys.executable, str(_SCRIPT), "cleanup", "--session-id", "session-1"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        env=env,
    )

    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    assert second.stdout == first.stdout
    assert cleaned.returncode == 0, cleaned.stderr
    assert not target.exists()


def test_cli_writes_result_lines_under_a_non_utf8_stdio_encoding(tmp_path: pathlib.Path) -> None:
    """標準入出力の符号化に日本語を扱えない値（`PYTHONIOENCODING=cp1252`）を指定した環境でも結果行を送出する。"""
    env, _ = _isolated_cli_environment(tmp_path)
    env["PYTHONIOENCODING"] = "cp1252"

    created = subprocess.run(
        [sys.executable, str(_SCRIPT), "create", "--prefix", "session", "--session-id", "session-1"],
        capture_output=True,
        check=False,
        env=env,
    )
    cleaned = subprocess.run(
        [sys.executable, str(_SCRIPT), "cleanup", "--session-id", "session-1"],
        capture_output=True,
        check=False,
        env=env,
    )

    assert created.returncode == 0, created.stderr
    assert cleaned.returncode == 0, cleaned.stderr
    assert "成功: セッションのmanaged-tempを回収した" in cleaned.stdout.decode("utf-8")


def test_cleanup_rejects_path_with_session_id(tmp_path: pathlib.Path) -> None:
    """cleanupの対象指定はpathとsession_idのいずれか一方に限る。"""
    with pytest.raises(SystemExit) as captured:
        subject.main(
            [
                "cleanup",
                "--path",
                str(tmp_path / "target"),
                "--session-id",
                "session-1",
            ]
        )

    assert captured.value.code == 2


def test_create_with_session_root_returns_unregistered_child() -> None:
    """`--session-root`は親の配下へ個別登録を持たないmanaged-temp直下の作業ディレクトリを作成する。"""
    session_root = subject.create_managed_temp("session", session_id="session-1")

    assert subject.main(["create", "--prefix", "work", "--session-root", str(session_root)]) == 0

    entries = subject.list_managed_temp()
    assert len(entries) == 1
    assert entries[0]["path"] == str(session_root)
    subject.cleanup_managed_temp(session_root)


def test_cleanup_removes_child_of_registered_temp(capsys: pytest.CaptureFixture[str]) -> None:
    """`--session-root`で作成したmanaged-temp直下の作業ディレクトリは、個別の管理情報が無くても回収できる。"""
    session_root = subject.create_managed_temp("session", session_id="session-1")
    assert subject.main(["create", "--prefix", "work", "--session-root", str(session_root)]) == 0
    child = pathlib.Path(capsys.readouterr().out.splitlines()[-1])
    assert child.is_dir()
    assert not (child / _MARKER_NAME).exists()

    assert subject.main(["cleanup", "--path", str(child)]) == 0

    assert not child.exists()
    assert session_root.is_dir()
    assert str(session_root) in capsys.readouterr().err
    subject.cleanup_managed_temp(session_root)


def test_cleanup_rejects_path_outside_registered_temp(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    """登録済み領域の配下に無い管理情報なしのパスは、現行どおり終了コード2で拒否する。"""
    outside = tmp_path / "outside"
    outside.mkdir()

    assert subject.main(["cleanup", "--path", str(outside)]) == 2

    assert outside.is_dir()
    assert "失敗: " in capsys.readouterr().err


def test_cleanup_working_copy_preserves_session_evidence(
    capsys: pytest.CaptureFixture[str],
    host_environ: Callable[[], dict[str, str]],
) -> None:
    """実Git fixtureとcloneの個別登録を回収し、別session本体の証拠・引継ぎ・入力を保持する。"""
    assert subject.main(["create", "--prefix", "session", "--session-id", "keep-evidence"]) == 0
    parent = pathlib.Path(capsys.readouterr().out.splitlines()[-1])
    work_roots = []
    for prefix in ("git-fixture", "git-clone"):
        assert subject.main(["create", "--prefix", prefix]) == 0
        work_roots.append(pathlib.Path(capsys.readouterr().out.splitlines()[-1]))
    environment = host_environ()
    fixture = work_roots[0] / "fixture"
    fixture.mkdir()
    run_git(fixture, "init", "--quiet", env=environment)
    (fixture / "source.txt").write_text("再生成可能なfixture", encoding="utf-8")
    oid = commit_all(fixture, "検証用fixture", env=environment)
    clone = work_roots[1] / "clone"
    run_git(work_roots[1], "clone", "--no-local", str(fixture), str(clone), env=environment)
    assert (fixture / ".git").is_dir() and (clone / ".git").is_dir()
    assert git_output(clone, "rev-parse", "HEAD", env=environment) == oid
    assert (clone / "source.txt").read_text(encoding="utf-8") == "再生成可能なfixture"
    assert {entry["path"] for entry in subject.list_managed_temp()} == {str(parent), *map(str, work_roots)}
    evidence = {"commit": oid, "fixture": str(fixture), "clone": str(clone), "git_processes_completed": True}
    for name in ("evidence.txt", "handoff.json", "resume.txt"):
        (parent / name).write_text(json.dumps(evidence), encoding="utf-8")

    # 同期Git呼出しは全て終了済みであり、作業用登録の回収後も別sessionの保存物を読める。
    for root in work_roots:
        assert subject.main(["cleanup", "--path", str(root)]) == 0
        assert not root.exists()
    assert subject.list_managed_temp(session_id="keep-evidence")[0]["path"] == str(parent)
    for name in ("evidence.txt", "handoff.json", "resume.txt"):
        assert json.loads((parent / name).read_text(encoding="utf-8")) == evidence
    with capsys.disabled():
        print("S01: " + json.dumps({**evidence, "work_roots_removed": True, "session_preserved": str(parent)}))


def test_child_cleanup_resumes_after_isolation(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """子の隔離直後に失敗しても同じCLI操作で再開し、親の保存物を保持する。"""
    parent = subject.create_managed_temp("session", session_id="child-resume")
    (parent / "evidence").write_text("保持", encoding="utf-8")
    assert subject.main(["create", "--prefix", "extract", "--session-root", str(parent)]) == 0
    child = pathlib.Path(capsys.readouterr().out.splitlines()[-1])
    (child / "payload").write_text("再生成可能", encoding="utf-8")

    def interrupt(
        _root: pathlib.Path,
        validated: managed_temp_registry._ValidatedTemp,
        quarantine: pathlib.Path,
        _expected_tree: object,
        **_kwargs: object,
    ) -> None:
        validated.path.rename(quarantine)
        raise OSError("隔離直後の失敗")

    with monkeypatch.context() as patch:
        patch.setattr(inventory_subject, "_cleanup_posix" if os.name == "posix" else "_cleanup_windows", interrupt)
        assert subject.main(["cleanup", "--path", str(child)]) == 2
    assert not child.exists()
    assert list(parent.glob(".agent-toolkit-cleanup-*"))

    assert subject.main(["cleanup", "--path", str(child)]) == 0
    assert not list(parent.glob(".agent-toolkit-cleanup-*"))
    assert (parent / "evidence").read_text(encoding="utf-8") == "保持"


def test_cleanup_nested_working_directory_preserves_siblings(capsys: pytest.CaptureFixture[str]) -> None:
    """既存の孫ディレクトリ指定も、各親の検証を通して回収できる。"""
    parent = subject.create_managed_temp("session", session_id="nested-child")
    assert subject.main(["create", "--prefix", "extract", "--session-root", str(parent)]) == 0
    child = pathlib.Path(capsys.readouterr().out.splitlines()[-1])
    nested = child / "comparison"
    nested.mkdir(mode=0o700)
    if os.name == "nt":
        managed_temp_windows_security._windows_secure_path(nested, directory=True)
    (nested / "regenerated").write_text("再生成可能", encoding="utf-8")
    (child / "evidence").write_text("保持", encoding="utf-8")

    assert subject.main(["cleanup", "--path", str(nested)]) == 0

    assert not nested.exists()
    assert (child / "evidence").read_text(encoding="utf-8") == "保持"


@pytest.mark.skipif(os.name != "posix", reason="POSIX symlinkの非追跡検証")
def test_child_cleanup_preserves_external_symlink_target(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """子の配下のリンク先を追跡せず、子自体がリンクなら拒否する。"""
    parent = subject.create_managed_temp("session", session_id="child-links")
    external = tmp_path / "outside"
    external.mkdir()
    (external / "saved").write_text("保持", encoding="utf-8")
    assert subject.main(["create", "--prefix", "extract", "--session-root", str(parent)]) == 0
    child = pathlib.Path(capsys.readouterr().out.splitlines()[-1])
    (child / "link").symlink_to(external, target_is_directory=True)
    assert subject.main(["cleanup", "--path", str(child)]) == 0
    assert (external / "saved").read_text(encoding="utf-8") == "保持"
    linked_child = parent / "linked-child"
    linked_child.symlink_to(external, target_is_directory=True)
    assert subject.main(["cleanup", "--path", str(linked_child)]) == 2
    assert (external / "saved").read_text(encoding="utf-8") == "保持"


@pytest.mark.parametrize("conflict", ["--awi=20260913-221409-001.md", "--session-id=session-2"])
def test_create_with_session_root_rejects_registration_options(conflict: str) -> None:
    """セッションのmanaged-temp直下の作業ディレクトリへ個別登録用の引数を併用しない。"""
    session_root = subject.create_managed_temp("session", session_id="session-1")

    assert subject.main(["create", "--prefix", "work", "--session-root", str(session_root), conflict]) == 2

    subject.cleanup_managed_temp(session_root)


@pytest.mark.parametrize(
    ("directory", "existing_owner", "full_open_error", "expected_handle", "owner_changed"),
    [
        (False, b"current-owner", None, 101, False),
        (True, b"administrator-owner", None, 101, True),
        (False, b"current-owner", managed_temp_windows_security._WINDOWS_ERROR_ACCESS_DENIED, 202, False),
        (True, b"current-owner", managed_temp_windows_security._WINDOWS_ERROR_ACCESS_DENIED, 202, False),
    ],
)
def test_secure_path_uses_adopted_handle_for_owner_check_and_update(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    directory: bool,
    existing_owner: bytes,
    full_open_error: int | None,
    expected_handle: int,
    owner_changed: bool,
) -> None:
    """採用ハンドルを所有者判定からセキュリティ更新まで再利用する契約を確認する。"""
    calls = _install_windows_security_doubles(
        monkeypatch,
        directory=directory,
        existing_owner=existing_owner,
        full_open_error=full_open_error,
    )

    managed_temp_windows_security._windows_secure_path(tmp_path / "target", directory=directory)

    full_access = (
        managed_temp_windows_security._WINDOWS_READ_CONTROL
        | managed_temp_windows_security._WINDOWS_WRITE_DAC
        | managed_temp_windows_security._WINDOWS_WRITE_OWNER
    )
    minimal_access = managed_temp_windows_security._WINDOWS_READ_CONTROL | managed_temp_windows_security._WINDOWS_WRITE_DAC
    expected_opens = [full_access, minimal_access] if full_open_error is not None else [full_access]
    expected_information = (
        managed_temp_windows_security._WINDOWS_DACL_SECURITY_INFORMATION
        | managed_temp_windows_security._WINDOWS_PROTECTED_DACL_SECURITY_INFORMATION
    )
    expected_owner = None
    if owner_changed:
        expected_information |= managed_temp_windows_security._WINDOWS_OWNER_SECURITY_INFORMATION
        expected_owner = b"current-owner"
    assert calls.opens == expected_opens
    assert calls.security_reads == [expected_handle]
    assert calls.updates == [(expected_handle, expected_information, expected_owner)]


@pytest.mark.parametrize("quarantine", [False, True])
def test_cli_resumes_an_interrupted_cleanup(tmp_path: pathlib.Path, quarantine: bool) -> None:
    """公開CLIは消費直後と隔離直後の中断を同じcleanupで回収する。"""
    env, state_root = _isolated_cli_environment(tmp_path)
    created = subprocess.run(
        [sys.executable, str(_SCRIPT), "create", "--prefix", "cli-resume"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        env=env,
    )
    target = pathlib.Path(created.stdout.strip())
    registry = state_root / managed_temp_registry._registry_name(target)
    record = json.loads(registry.read_text(encoding="utf-8"))
    nonce = record["nonce"]
    consuming = registry.with_name(f"{registry.name}.consuming-{nonce}")
    registry.replace(consuming)
    quarantine_path = target.parent / f".agent-toolkit-cleanup-{nonce}"
    (target / "content.txt").write_text("remove", encoding="utf-8")
    if quarantine:
        target.replace(quarantine_path)

    cleaned = subprocess.run(
        [sys.executable, str(_SCRIPT), "cleanup", "--path", str(target)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        env=env,
    )

    assert created.returncode == 0
    assert cleaned.returncode == 0, cleaned.stderr
    assert not target.exists()
    assert not quarantine_path.exists()
    assert not registry.exists()
    assert not consuming.exists()


@pytest.mark.skipif(os.name != "posix", reason="POSIXの権限検証を再現する")
def test_create_with_unsafe_root_names_owner_and_mode_check(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    """明示したrootの権限が不安全な場合は、所有者と0700の確認を次の操作として示す。"""
    root = tmp_path / "unsafe-root"
    root.mkdir()
    root.chmod(0o777)

    assert subject.main(["create", "--prefix", "work", "--root", str(root)]) == 2

    lines = capsys.readouterr().err.splitlines()
    assert lines[0].startswith("失敗: ")
    assert lines[1].startswith("次の操作: ")
    assert "所有者" in lines[1]
    assert "0700" in lines[1]


def test_replacement_detected_during_cleanup_names_rerun_and_report(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """後始末中に置換を検出した失敗は、再実行と繰り返す場合の報告を次の操作として示す。"""
    target = subject.create_managed_temp("replaced")
    snapshots = iter([{}, {"changed": ("file", 0, 0)}])
    monkeypatch.setattr(inventory_subject, "_tree_snapshot", lambda _path: next(snapshots))

    assert subject.main(["cleanup", "--path", str(target)]) == 2

    next_action = next(line for line in capsys.readouterr().err.splitlines() if line.startswith("次の操作: "))
    assert "再実行" in next_action
    assert "ユーザーへ報告" in next_action


def test_live_plugin_root_survives_expired_sweep() -> None:
    """保持期限を超えても生存プロセスが参照する配布物を自動掃引から保護する。"""
    area = subject.create_managed_temp("agents-server-plugin-root")
    (area / "payload").write_text("使用中", encoding="utf-8")
    with lease_subject.plugin_root_lock(area):
        lease_subject.register_plugin_root_use(area)

    deleted = subject.sweep_expired_managed_temp(now=datetime.datetime.now(datetime.UTC) + datetime.timedelta(days=8))

    assert area not in deleted
    assert (area / "payload").read_text(encoding="utf-8") == "使用中"


@pytest.mark.parametrize("invalid", ["broken-json", "pid-reused", "access-denied"])
def test_lease_observation_preserves_unknown_and_reclaims_reused_pid(
    monkeypatch: pytest.MonkeyPatch,
    invalid: str,
) -> None:
    """取得不能な使用記録を保ち、PIDの再利用は作成時刻で使用終了と判定する。"""
    area = subject.create_managed_temp("lease-check")
    with lease_subject.plugin_root_lock(area):
        lease_subject.register_plugin_root_use(area)
    records = list((state_paths.state_dir() / "plugin-root-leases").glob("*.json"))
    assert len(records) == 1
    if invalid == "broken-json":
        records[0].write_text("{", encoding="utf-8")
    elif invalid == "pid-reused":
        record = json.loads(records[0].read_text(encoding="utf-8"))
        record["created_at"] = 0
        records[0].write_text(json.dumps(record), encoding="utf-8")
    else:

        def denied(*_args: object, **_kwargs: object) -> object:
            raise lease_subject.psutil.AccessDenied()

        monkeypatch.setattr(lease_subject.psutil, "Process", denied)
    if invalid == "pid-reused":
        subject.cleanup_managed_temp(area)
        assert not area.exists()
    else:
        with pytest.raises(subject.ManagedTempError, match="使用中"):
            subject.cleanup_managed_temp(area, force_remove=True)
        assert area.is_dir()


_HOLD_LOCK_CODE = (
    "import sys\n"
    "from agent_toolkit._common import file_lock\n"
    "with open(sys.argv[1], 'a+', encoding='utf-8') as handle:\n"
    "    file_lock.acquire_lock(handle)\n"
    "    print('locked', flush=True)\n"
    "    sys.stdin.readline()\n"
)


def _lock_paths(area: pathlib.Path) -> dict[str, pathlib.Path]:
    """自動掃引が期限切れ領域の回収で取る外側のcleanup lockと内側の配布物ロックのパスを返す。"""
    registry_path = inventory_subject._registry_path(area)  # pylint: disable=protected-access
    inner = lease_subject._directory() / f"{lease_subject._key(area)}.lock"  # pylint: disable=protected-access
    return {"outer": registry_path.with_name(f"{registry_path.name}.cleanup.lock"), "inner": inner}


def _hold_lock(path: pathlib.Path) -> subprocess.Popen[str]:
    """別processにロックを保持させ、取得の完了を待ってから返す。"""
    holder = subprocess.Popen(  # pylint: disable=consider-using-with
        [sys.executable, "-c", _HOLD_LOCK_CODE, str(path)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )
    assert holder.stdout is not None
    assert holder.stdout.readline().strip() == "locked"
    return holder


def _release(holder: subprocess.Popen[str]) -> None:
    holder.communicate("\n", timeout=10)
    assert holder.returncode == 0


def _run_atk_within(argv: list[str], now: datetime.datetime, timeout: float) -> int | None:
    """`atk.main`を別threadで実行し、`timeout`秒以内に終われば`SystemExit`の終了コード（戻った場合は0）、終わらなければNoneを返す。"""
    from agent_toolkit import atk  # pylint: disable=import-outside-toplevel

    result: list[int] = []

    def run() -> None:
        try:
            atk.main(argv, now=now)
            result.append(0)
        except SystemExit as error:
            result.append(error.code if isinstance(error.code, int) else 1)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    thread.join(timeout)
    return result[0] if result else None


@pytest.mark.parametrize("held", ["outer", "inner"])
def test_sweep_skips_inner_lock_conflict_without_waiting(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], held: str
) -> None:
    """期限切れ領域のロックを別processが保持していても、通常の操作は解放を待たずに本来の結果を返す。"""
    area = subject.create_managed_temp("sweep-conflict")
    (area / "payload").write_text("保持", encoding="utf-8")
    target = tmp_path / "read-target.txt"
    target.write_text("本文の行\n", encoding="utf-8")
    now = datetime.datetime.now(datetime.UTC) + datetime.timedelta(days=8)
    holder = _hold_lock(_lock_paths(area)[held])
    try:
        assert _run_atk_within(["read-file", "--", str(target)], now, timeout=20) == 0
        assert "本文の行" in capsys.readouterr().out
        assert _run_atk_within(["config", "show"], now, timeout=20) == 0
        assert (area / "payload").read_text(encoding="utf-8") == "保持"
        assert inventory_subject._registry_path(area).exists()  # pylint: disable=protected-access
    finally:
        _release(holder)


def test_conflicted_area_is_retained_then_collected() -> None:
    """内側ロックの競合中は掃引が領域を保持し、競合の解消後の掃引は既存の規則で回収する。"""
    area = subject.create_managed_temp("sweep-retained")
    (area / "payload").write_text("保持", encoding="utf-8")
    now = datetime.datetime.now(datetime.UTC) + datetime.timedelta(days=8)
    holder = _hold_lock(_lock_paths(area)["inner"])
    try:
        assert area not in subject.sweep_expired_managed_temp(now=now)
        assert (area / "payload").is_file()
    finally:
        _release(holder)
    assert area in subject.sweep_expired_managed_temp(now=now)
    assert not area.exists()


@pytest.mark.parametrize("registered", [False, True])
@pytest.mark.parametrize("expired", [False, True])
def test_list_does_not_offer_cleanup_for_retained_areas(
    capsys: pytest.CaptureFixture[str], registered: bool, expired: bool
) -> None:
    """生存leaseを持つ共有配布物は期限超過時だけ保持理由と再判定条件を示す。"""
    area = subject.create_managed_temp("agents-server-plugin-root-shared")
    with lease_subject.plugin_root_lock(area):
        lease_subject.register_plugin_root_use(area)
    if not registered:
        managed_temp_registry._registry_path(area).unlink()
    if expired:
        expires = (datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=8)).timestamp()
        for path in (*area.rglob("*"), area):
            os.utime(path, (expires, expires))
    assert subject.main(["list"]) == (0 if registered else 1)
    error = capsys.readouterr().err
    assert "cleanup --path" not in error
    assert ("領域別lease" in error) is expired
    assert ("利用プロセスの終了後" in error) is expired
    assert area.is_dir()


def _listed_paths(stdout: str) -> set[str]:
    """`list`の標準出力の各行のJSONから一覧のパスを取り出す。"""
    return {json.loads(line)["path"] for line in stdout.splitlines() if line.strip()}


@pytest.mark.parametrize("prefix", ["session", "atk-output", "agents-server-plugin-rootx"])
def test_list_keeps_non_plugin_area_without_scanning(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], prefix: str
) -> None:
    """読取権限の無い配下を持つ通常領域も、期限走査をせず一覧へ返す。"""
    area = subject.create_managed_temp(prefix)
    restricted = area / "container-data"
    restricted.mkdir()
    if os.name == "posix":
        restricted.chmod(0)

    def unexpected_scan(_path: pathlib.Path):
        pytest.fail("配布物以外の登録済み領域を走査した")

    monkeypatch.setattr(inventory_subject, "_latest_update_and_git_paths", unexpected_scan)
    try:
        assert subject.main(["list"]) == 0
        output = capsys.readouterr()
        assert str(area) in _listed_paths(output.out)
        assert "管理対象を列挙できない" not in output.err
        assert "cleanup --path" not in output.err
        assert not output.err
    finally:
        if os.name == "posix":
            restricted.chmod(0o700)


@pytest.mark.parametrize("prefix", ["agents-server-plugin-root", "agents-server-plugin-root-shared"])
@pytest.mark.parametrize("error_type", [PermissionError, subject.ManagedTempError])
def test_list_keeps_plugin_area_when_expiry_scan_fails(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    prefix: str,
    error_type: type[Exception],
) -> None:
    """配布物の期限走査で読取拒否・置換を観測しても、真正な登録は一覧へ返す。"""
    area = subject.create_managed_temp(prefix)
    calls: list[pathlib.Path] = []

    def failed_scan(path: pathlib.Path):
        calls.append(path)
        raise error_type("期限判定用の走査に失敗した")

    def unexpected_retention(_path: pathlib.Path):
        pytest.fail("期限不明の領域で保持判定した")

    monkeypatch.setattr(inventory_subject, "_latest_update_and_git_paths", failed_scan)
    monkeypatch.setattr(inventory_subject, "_report_retained_plugin_root", unexpected_retention)
    assert subject.main(["list"]) == 0
    assert calls == [area]
    output = capsys.readouterr()
    assert str(area) in _listed_paths(output.out)
    assert "管理対象を列挙できない" not in output.err
    assert "cleanup --path" not in output.err
    assert not output.err
    assert area.is_dir() and managed_temp_registry._registry_path(area).is_file()


def test_retention_summary_once_per_state_change(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """同じ保持状態は初回だけ要約対象とし、明示listは対象別の理由と再判定条件を返す。"""
    area = subject.create_managed_temp("agents-server-plugin-root")
    with lease_subject.plugin_root_lock(area):
        lease_subject.register_plugin_root_use(area)
    expires = (datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=8)).timestamp()
    for path in (*area.rglob("*"), area):
        os.utime(path, (expires, expires))
    now = datetime.datetime.now(datetime.UTC)
    first = subject.sweep_managed_temp(now=now)

    def repeated_judgement(*_args: object, **_kwargs: object) -> None:
        pytest.fail("保持を再判定した")

    with monkeypatch.context() as patch:
        patch.setattr(inventory_subject, "_require_unused_plugin_root", repeated_judgement)
        second = subject.sweep_managed_temp(now=now)
    assert first.retention_changed
    assert not second.retention_changed
    assert first.retained == second.retained
    assert subject.main(["list"]) == 0
    error = capsys.readouterr().err
    assert str(area) in error
    assert "利用プロセスの終了後" in error


@pytest.mark.parametrize("registered", [False, True])
def test_list_checks_expired_plugin_root_under_lock(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], registered: bool
) -> None:
    """登録の有無によらずlistの保持判定は配布物ロックを取り、競合時は案内を省く。"""
    area = subject.create_managed_temp("agents-server-plugin-root-shared")
    if not registered:
        managed_temp_registry._registry_path(area).unlink()
    expires = (datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=8)).timestamp()
    for path in (*area.rglob("*"), area):
        os.utime(path, (expires, expires))
    calls: list[pathlib.Path] = []

    def check_lock(path: pathlib.Path) -> None:
        with pytest.raises(OSError), lease_subject.plugin_root_lock(path, blocking=False):
            pytest.fail("保持判定がロックを取得していない")
        calls.append(path)

    monkeypatch.setattr(inventory_subject, "_require_unused_plugin_root", check_lock)
    subject.main(["list"])
    assert calls == [area]
    capsys.readouterr()
    calls.clear()
    with lease_subject.plugin_root_lock(area):
        subject.main(["list"])
    assert not calls
    assert "警告:" not in capsys.readouterr().err


@pytest.mark.parametrize("quarantined", [False, True])
def test_list_and_sweep_skip_concurrent_cleanup(
    quarantined: bool,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """別processが後始末中の消費・隔離状態は通知せず、中断後は元の対象から再開する。"""
    area = subject.create_managed_temp("concurrent-cleanup")
    expires = (datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=8)).timestamp()
    for path in (*area.rglob("*"), area):
        os.utime(path, (expires, expires), follow_symlinks=False)
    registry = managed_temp_registry._registry_path(area)
    nonce = json.loads(registry.read_text(encoding="utf-8"))["nonce"]
    consuming = registry.with_name(f"{registry.name}.consuming-{nonce}")
    quarantine = area.parent / f".agent-toolkit-cleanup-{nonce}"
    registry.replace(consuming)
    if quarantined:
        area.rename(quarantine)
    holder = _hold_lock(_lock_paths(area)["outer"])
    try:
        assert subject.main(["list"]) == 1
        result = subject.sweep_managed_temp(now=datetime.datetime.now(datetime.UTC))
        assert not result.deleted
        assert not result.stale_unregistered
        assert "警告:" not in capsys.readouterr().err
    finally:
        _release(holder)
    assert subject.main(["list"]) == 1
    error = capsys.readouterr().err
    assert str(area) in error
    assert "--force-remove" not in error
    subject.cleanup_managed_temp(area)
    assert not area.exists() and not quarantine.exists() and not consuming.exists()


@pytest.mark.parametrize("shared", [False, True])
@pytest.mark.parametrize(
    "observation",
    ["capability", "matching-lease", "pid-reused", "exited", "arguments-denied", "log-denied", "lease-read-denied"],
)
def test_plugin_root_observation_recovers_on_next_sweep(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], shared: bool, observation: str
) -> None:
    """POSIX/Windowsの実領域で能力・PID・取得不能の保持と解消後の回収を確かめる。

    OSのprocess観測と読取拒否だけを注入し、領域・lease・ロック・真正性検証・削除は実操作する。
    WindowsはCIのtest-windowsが本ファイルをpytestで実行する。
    """
    prefix = "agents-server-plugin-root-shared" if shared else "agents-server-plugin-root"
    area = subject.create_managed_temp(prefix)
    (area / "payload").write_text("保持する内容", encoding="utf-8")
    other = subject.create_managed_temp("agents-server-plugin-root-shared")
    (other / "payload").write_text("別領域の内容", encoding="utf-8")
    process = lease_subject.psutil.Process()
    if observation == "capability":
        lease_subject.register_lease_capability()
    if observation == "matching-lease":
        with lease_subject.plugin_root_lock(other):
            lease_subject.register_plugin_root_use(other)
    lease = None
    if observation in {"pid-reused", "exited", "lease-read-denied"}:
        with lease_subject.plugin_root_lock(area):
            lease_subject.register_plugin_root_use(area)
        lease = next(lease_subject._directory().glob(f"{lease_subject._key(area)}-*.json"))
        record = json.loads(lease.read_text(encoding="utf-8"))
        record["created_at"] = 0
        if observation == "exited":
            record["pid"] = 2**30
        lease.write_text(json.dumps(record), encoding="utf-8")
    expires = (datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=8)).timestamp()
    for path in (*area.rglob("*"), area):
        os.utime(path, (expires, expires))
    protected = observation in {"arguments-denied", "lease-read-denied"} or observation == "log-denied" and not shared

    def denied(*_args: object, **_kwargs: object):
        raise lease_subject.psutil.AccessDenied(process.pid)

    original = types.SimpleNamespace(read_text=pathlib.Path.read_text)

    def read_lease(
        path: pathlib.Path, encoding: str | None = None, errors: str | None = None, newline: str | None = None
    ) -> str:
        if path == lease:
            raise PermissionError("使用記録の取得拒否")
        return original.read_text(path, encoding=encoding, errors=errors, newline=newline)

    with monkeypatch.context() as patch:
        patch.setattr(
            lease_subject.psutil,
            "process_iter",
            lambda: [process] if observation in {"capability", "matching-lease", "arguments-denied", "log-denied"} else [],
        )
        patch.setattr(
            type(process),
            "cmdline",
            denied
            if observation == "arguments-denied"
            else lambda self: ["python", "/source/agent_toolkit/agents_server_mcp.py"],
        )
        if observation == "log-denied":
            patch.setattr(type(process), "open_files", denied)
        if observation == "lease-read-denied":
            patch.setattr(pathlib.Path, "read_text", read_lease)
        result = subject.sweep_managed_temp(now=datetime.datetime.now(datetime.UTC))
        assert bool(result.retained) is protected
        assert area.exists() is protected
        if protected:
            assert (area / "payload").read_text(encoding="utf-8") == "保持する内容"
            assert subject.main(["cleanup", "--path", str(area)]) == 2
            error = capsys.readouterr().err
            assert str(area) in error and "次の操作:" in error
    monkeypatch.setattr(lease_subject.psutil, "process_iter", lambda: [])
    if protected:
        assert area in subject.sweep_managed_temp(now=datetime.datetime.now(datetime.UTC)).deleted
    assert not area.exists()
    assert not managed_temp_registry._registry_path(area).exists()
    assert (other / "payload").read_text(encoding="utf-8") == "別領域の内容"


def test_list_preserves_registration_for_interrupted_quarantine(capsys: pytest.CaptureFixture[str]) -> None:
    """隔離先が残る登録を実体不在の登録として回収せず、同じcleanupへ渡す。"""
    area = subject.create_managed_temp("interrupted-quarantine")
    registry = managed_temp_registry._registry_path(area)
    nonce = json.loads(registry.read_text(encoding="utf-8"))["nonce"]
    quarantine = area.parent / f".agent-toolkit-cleanup-{nonce}"
    area.rename(quarantine)
    subject.list_managed_temp(report_recovery_candidates=True)
    assert registry.exists()
    assert str(area) in capsys.readouterr().err
    subject.cleanup_managed_temp(area)
    assert not quarantine.exists() and not registry.exists()
