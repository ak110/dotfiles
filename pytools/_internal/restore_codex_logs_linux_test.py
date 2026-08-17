"""Codex診断ログDBを通常ストレージへ復元する処理のテスト。"""

import os
import pathlib
import stat
from types import SimpleNamespace

import pytest

from pytools._internal import restore_codex_logs_linux

# 復元処理の安全境界を構成する内部関数と定数を直接検証する。
# pylint: disable=protected-access


def _prepare(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    *,
    stop_codex: bool = True,
) -> tuple[pathlib.Path, pathlib.Path, tuple[tuple[pathlib.Path, pathlib.Path], ...]]:
    """Linuxのホームディレクトリ、共有メモリー相当のパス、3組のパスを返す。

    `stop_codex`が真なら稼働判定をCodex停止中へ固定し、偽なら判定処理をそのまま実行させる。
    """
    monkeypatch.setattr(restore_codex_logs_linux.sys, "platform", "linux")
    if stop_codex:
        monkeypatch.setattr(restore_codex_logs_linux, "_running_codex_processes", lambda: ())
    home = tmp_path / "home"
    shm_root = tmp_path / "shm"
    codex_dir = home / ".codex"
    codex_dir.mkdir(parents=True)
    shm_root.mkdir()
    return home, shm_root, restore_codex_logs_linux._database_pairs(codex_dir, shm_root)


def _write_targets(
    pairs: tuple[tuple[pathlib.Path, pathlib.Path], ...],
) -> dict[pathlib.Path, bytes]:
    """識別可能な内容を持つ共有メモリー側の3ファイルを作成する。"""
    contents = {target_path: f"target-{index}".encode() for index, (_, target_path) in enumerate(pairs)}
    for target_path, content in contents.items():
        target_path.write_bytes(content)
    return contents


def _link_all(pairs: tuple[tuple[pathlib.Path, pathlib.Path], ...]) -> None:
    """ホームディレクトリ側の3つのパスを、対応する管理対象`target`へのsymlinkにする。"""
    for home_path, target_path in pairs:
        home_path.symlink_to(target_path)


def test_non_linux_is_noop(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """Linux以外ではホームディレクトリを作成せず何もしない。"""
    monkeypatch.setattr(restore_codex_logs_linux.sys, "platform", "win32")
    home = tmp_path / "home"

    assert restore_codex_logs_linux.run(home_dir=home, shm_root=tmp_path / "shm") is False
    assert not home.exists()


def test_running_at_start_defers_without_changes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """開始時にCodexが稼働中なら管理linkとtargetを保持する。"""
    home, shm_root, pairs = _prepare(monkeypatch, tmp_path)
    contents = _write_targets(pairs)
    _link_all(pairs)
    monkeypatch.setattr(restore_codex_logs_linux, "_running_codex_processes", lambda: ("codex mcp-server",))

    assert restore_codex_logs_linux.run(home_dir=home, shm_root=shm_root) is False
    assert all(home_path.is_symlink() for home_path, _ in pairs)
    assert all(target_path.read_bytes() == contents[target_path] for _, target_path in pairs)


def test_running_after_copy_discards_only_temporary_files(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """コピー後の再確認で起動を検知したら未配置一時ファイルだけを除く。"""
    home, shm_root, pairs = _prepare(monkeypatch, tmp_path)
    _write_targets(pairs)
    _link_all(pairs)
    results = iter(((), ("codex mcp-server",)))

    def running_codex_processes() -> tuple[str, ...]:
        return next(results)

    monkeypatch.setattr(restore_codex_logs_linux, "_running_codex_processes", running_codex_processes)

    assert restore_codex_logs_linux.run(home_dir=home, shm_root=shm_root) is False
    assert all(home_path.is_symlink() for home_path, _ in pairs)
    assert not list((home / ".codex").glob(".*.restore-*"))
    assert all(target_path.exists() for _, target_path in pairs)


def test_write_after_second_check_is_detected_as_conflict_on_retry(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """2回目確認直後のtarget更新は次回に不一致として保存し、自動削除しない。"""
    home, shm_root, pairs = _prepare(monkeypatch, tmp_path)
    original = _write_targets(pairs)
    _link_all(pairs)
    calls = 0

    def check_and_write() -> tuple[str, ...]:
        nonlocal calls
        calls += 1
        if calls == 2:
            pairs[0][1].write_bytes(b"written-after-check")
        return ()

    monkeypatch.setattr(restore_codex_logs_linux, "_running_codex_processes", check_and_write)
    assert restore_codex_logs_linux.run(home_dir=home, shm_root=shm_root) is True
    assert pairs[0][0].read_bytes() == original[pairs[0][1]]

    assert restore_codex_logs_linux.run(home_dir=home, shm_root=shm_root) is False
    assert pairs[0][1].read_bytes() == b"written-after-check"
    assert list((home / ".codex").glob("logs_2-restore-conflict-*"))


def test_unrelated_symlink_defers_entire_set(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """1件でも管理対象外symlinkがあれば他の管理linkも変更しない。"""
    home, shm_root, pairs = _prepare(monkeypatch, tmp_path)
    _write_targets(pairs)
    _link_all(pairs)
    pairs[1][0].unlink()
    pairs[1][0].symlink_to(tmp_path / "unrelated")

    assert restore_codex_logs_linux.run(home_dir=home, shm_root=shm_root) is False
    assert all(home_path.is_symlink() for home_path, _ in pairs)
    assert all(target_path.exists() for _, target_path in pairs)


def test_insufficient_capacity_preserves_links_and_targets(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """通常ストレージの空き容量不足時はコピーを開始しない。"""
    home, shm_root, pairs = _prepare(monkeypatch, tmp_path)
    _write_targets(pairs)
    _link_all(pairs)
    monkeypatch.setattr(restore_codex_logs_linux.shutil, "disk_usage", lambda _path: SimpleNamespace(free=0))

    assert restore_codex_logs_linux.run(home_dir=home, shm_root=shm_root) is False
    assert all(home_path.is_symlink() for home_path, _ in pairs)
    assert all(target_path.exists() for _, target_path in pairs)


def test_restore_keeps_targets_until_later_matching_run(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """初回はホームディレクトリへ復元して`target`を保持し、次回に一致を検証して回収する。"""
    home, shm_root, pairs = _prepare(monkeypatch, tmp_path)
    contents = _write_targets(pairs)
    _link_all(pairs)
    unrelated_target = shm_root / f"codex-{os.getuid()}-other.sqlite"
    unrelated_target.write_bytes(b"unrelated")

    assert restore_codex_logs_linux.run(home_dir=home, shm_root=shm_root) is True
    for home_path, target_path in pairs:
        assert not home_path.is_symlink()
        assert home_path.read_bytes() == contents[target_path]
        assert stat.S_IMODE(home_path.stat().st_mode) == 0o600
        assert target_path.exists()

    assert restore_codex_logs_linux.run(home_dir=home, shm_root=shm_root) is True
    assert all(not target_path.exists() for _, target_path in pairs)
    assert unrelated_target.read_bytes() == b"unrelated"


def test_dangling_managed_links_are_removed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """targetを失った旧管理linkを除き、Codexが通常ファイルを生成できる状態にする。"""
    home, shm_root, pairs = _prepare(monkeypatch, tmp_path)
    _link_all(pairs)

    assert restore_codex_logs_linux.run(home_dir=home, shm_root=shm_root) is True
    assert all(not home_path.is_symlink() and not home_path.exists() for home_path, _ in pairs)


def test_partial_restore_is_retryable_and_later_cleans_targets(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """regular・missing・管理linkの混在を復元し、次回に全targetを回収する。"""
    home, shm_root, pairs = _prepare(monkeypatch, tmp_path)
    contents = _write_targets(pairs)
    pairs[0][0].write_bytes(contents[pairs[0][1]])
    pairs[2][0].symlink_to(pairs[2][1])

    assert restore_codex_logs_linux.run(home_dir=home, shm_root=shm_root) is True
    assert all(home_path.read_bytes() == contents[target_path] for home_path, target_path in pairs)
    assert all(target_path.exists() for _, target_path in pairs)

    assert restore_codex_logs_linux.run(home_dir=home, shm_root=shm_root) is True
    assert all(not target_path.exists() for _, target_path in pairs)


def test_copy_failure_removes_temporary_files_only(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """コピー失敗時はホームディレクトリの管理対象symlinkと`target`を保持し、一時ファイルを除く。"""
    home, shm_root, pairs = _prepare(monkeypatch, tmp_path)
    _write_targets(pairs)
    _link_all(pairs)

    def fail_copy(*_args: object, **_kwargs: object) -> None:
        raise OSError("copy failed")

    monkeypatch.setattr(restore_codex_logs_linux.shutil, "copyfileobj", fail_copy)
    with pytest.raises(OSError, match="copy failed"):
        restore_codex_logs_linux.run(home_dir=home, shm_root=shm_root)

    assert all(home_path.is_symlink() for home_path, _ in pairs)
    assert all(target_path.exists() for _, target_path in pairs)
    assert not list((home / ".codex").glob(".*.restore-*"))


def test_replace_failure_preserves_unplaced_links_and_all_targets(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """置換途中の失敗でも未配置linkと全targetを保持し、一時ファイルを除く。"""
    home, shm_root, pairs = _prepare(monkeypatch, tmp_path)
    _write_targets(pairs)
    _link_all(pairs)
    real_replace = os.replace
    calls = 0

    def fail_second_replace(source: pathlib.Path, destination: pathlib.Path) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("replace failed")
        real_replace(source, destination)

    monkeypatch.setattr(restore_codex_logs_linux.os, "replace", fail_second_replace)
    with pytest.raises(OSError, match="replace failed"):
        restore_codex_logs_linux.run(home_dir=home, shm_root=shm_root)

    assert pairs[0][0].is_file() and not pairs[0][0].is_symlink()
    assert all(home_path.is_symlink() for home_path, _ in pairs[1:])
    assert all(target_path.exists() for _, target_path in pairs)
    assert not list((home / ".codex").glob(".*.restore-*"))


def test_mismatch_creates_owner_only_content_addressed_snapshot_and_warning(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """不一致時は3集合を保持したsnapshotと手動復旧手順を警告する。"""
    home, shm_root, pairs = _prepare(monkeypatch, tmp_path)
    target_contents = _write_targets(pairs)
    for index, (home_path, _target_path) in enumerate(pairs):
        home_path.write_bytes(f"home-{index}".encode())
    home_before = {home_path: home_path.read_bytes() for home_path, _ in pairs}

    with caplog.at_level("WARNING", logger=restore_codex_logs_linux.logger.name):
        assert restore_codex_logs_linux.run(home_dir=home, shm_root=shm_root) is False

    snapshots = list((home / ".codex").glob("logs_2-restore-conflict-*"))
    assert len(snapshots) == 1
    snapshot = snapshots[0]
    assert stat.S_IMODE(snapshot.stat().st_mode) == 0o700
    for home_path, target_path in pairs:
        snapshot_file = snapshot / target_path.name
        assert home_path.read_bytes() == home_before[home_path]
        assert target_path.read_bytes() == target_contents[target_path]
        assert snapshot_file.read_bytes() == target_contents[target_path]
        assert stat.S_IMODE(snapshot_file.stat().st_mode) == 0o600

    warning = "\n".join(record.getMessage() for record in caplog.records)
    for home_path, target_path in pairs:
        assert str(home_path) in warning
        assert str(target_path) in warning
    assert str(snapshot) in warning
    assert "復元未完了" in warning
    assert "Codexを停止" in warning
    assert "DB・WAL・SHMを一組" in warning
    assert "手動で回収" in warning


def test_same_conflict_digest_reuses_existing_identical_snapshot(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """同じ競合集合の再試行では既存snapshotを検証して再利用する。"""
    home, shm_root, pairs = _prepare(monkeypatch, tmp_path)
    _write_targets(pairs)
    for home_path, _ in pairs:
        home_path.write_bytes(b"home")

    assert restore_codex_logs_linux.run(home_dir=home, shm_root=shm_root) is False
    assert restore_codex_logs_linux.run(home_dir=home, shm_root=shm_root) is False

    codex_dir = home / ".codex"
    assert len(list(codex_dir.glob("logs_2-restore-conflict-*"))) == 1
    assert not list(codex_dir.glob(".logs_2-restore-conflict-*"))


def test_different_conflict_contents_create_distinct_visible_snapshots(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """異なる競合集合は内容別の可視snapshotへ保存し、同一内容だけを再利用する。"""
    home, shm_root, pairs = _prepare(monkeypatch, tmp_path)
    _write_targets(pairs)
    for home_path, _ in pairs:
        home_path.write_bytes(b"home")

    assert restore_codex_logs_linux.run(home_dir=home, shm_root=shm_root) is False
    first_snapshots = set((home / ".codex").glob("logs_2-restore-conflict-*"))
    assert len(first_snapshots) == 1

    pairs[0][1].write_bytes(b"different-conflict")
    assert restore_codex_logs_linux.run(home_dir=home, shm_root=shm_root) is False
    second_snapshots = set((home / ".codex").glob("logs_2-restore-conflict-*"))
    assert len(second_snapshots) == 2
    assert first_snapshots < second_snapshots

    assert restore_codex_logs_linux.run(home_dir=home, shm_root=shm_root) is False
    assert set((home / ".codex").glob("logs_2-restore-conflict-*")) == second_snapshots
    assert not list((home / ".codex").glob(".logs_2-restore-conflict-*"))


def test_existing_different_snapshot_is_not_overwritten(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """同じdigest名に異なる内容があれば既存物を上書きせず新規一時名で保存する。"""
    home, shm_root, pairs = _prepare(monkeypatch, tmp_path)
    _write_targets(pairs)
    for home_path, _ in pairs:
        home_path.write_bytes(b"home")
    destination = home / ".codex" / "logs_2-restore-conflict-fixed"
    destination.mkdir()
    existing_file = destination / pairs[0][1].name
    existing_file.write_bytes(b"existing")
    monkeypatch.setattr(restore_codex_logs_linux, "_snapshot_digest", lambda _snapshot, _pairs: "fixed")

    assert restore_codex_logs_linux.run(home_dir=home, shm_root=shm_root) is False

    assert existing_file.read_bytes() == b"existing"
    alternatives = list((home / ".codex").glob(".logs_2-restore-conflict-*"))
    assert len(alternatives) == 1
    assert alternatives[0].is_dir()


class _FakeProcess:
    """psutil.Process.infoとpidだけを提供するテスト用process。"""

    def __init__(self, info: dict[str, object], pid: int = 1000) -> None:
        self.info = info
        self.pid = pid


_OWN_UID = os.getuid()
_OTHER_UID = _OWN_UID + 1
_DENIED = restore_codex_logs_linux._ACCESS_DENIED


def _uids(uid: int) -> SimpleNamespace:
    """psutilの戻り値と同じく`.real`で参照できるuid情報を生成する。"""
    return SimpleNamespace(real=uid, effective=uid, saved=uid)


def _patch_process_iter(monkeypatch: pytest.MonkeyPatch, processes: list[_FakeProcess]) -> None:
    """`psutil.process_iter`を固定のプロセス集合へ差し替える。"""

    def process_iter(_attrs: list[str], **_kwargs: object) -> list[_FakeProcess]:
        return processes

    monkeypatch.setattr(restore_codex_logs_linux.psutil, "process_iter", process_iter)


@pytest.mark.parametrize(
    ("processes", "restored"),
    [
        pytest.param(
            [_FakeProcess({"name": "codex", "exe": None, "cmdline": [], "uids": _uids(_OWN_UID)})],
            False,
            id="own-codex-launcher",
        ),
        pytest.param(
            [
                _FakeProcess(
                    {
                        "name": "node",
                        "exe": "/usr/bin/node",
                        "cmdline": ["node", "/opt/node_modules/@openai/codex/bin/codex.js"],
                        "uids": _uids(_OWN_UID),
                    }
                )
            ],
            False,
            id="own-codex-package",
        ),
        pytest.param(
            [_FakeProcess({"name": _DENIED, "exe": _DENIED, "cmdline": _DENIED, "uids": _uids(_OWN_UID)})],
            False,
            id="own-all-attributes-unavailable",
        ),
        pytest.param(
            [_FakeProcess({"name": _DENIED, "exe": _DENIED, "cmdline": _DENIED, "uids": _uids(_OTHER_UID)})],
            True,
            id="other-user-all-attributes-unavailable",
        ),
        pytest.param(
            [_FakeProcess({"name": _DENIED, "exe": _DENIED, "cmdline": _DENIED, "uids": _DENIED})],
            True,
            id="owner-unavailable",
        ),
        pytest.param(
            [_FakeProcess({"name": "sshd", "exe": _DENIED, "cmdline": ["sshd: user@pts/0"], "uids": _uids(_OWN_UID)})],
            True,
            id="own-executable-path-unavailable",
        ),
        pytest.param(
            [
                _FakeProcess(
                    {"name": "python", "exe": "/usr/bin/python", "cmdline": ["python", "worker.py"], "uids": _uids(_OWN_UID)}
                )
            ],
            True,
            id="own-unrelated-process",
        ),
        pytest.param(
            [
                _FakeProcess(
                    {
                        "name": "textlint",
                        "exe": "/usr/bin/node",
                        "cmdline": ["node", "scripts/codex-agents-base.md"],
                        "uids": _uids(_OWN_UID),
                    }
                )
            ],
            True,
            id="own-process-with-codex-prefixed-argument",
        ),
    ],
)
def test_restore_proceeds_unless_own_codex_process_exists(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    processes: list[_FakeProcess],
    restored: bool,
) -> None:
    """自ユーザー所有のCodexと判定材料を欠くプロセスだけが復元を延期させる。"""
    home, shm_root, pairs = _prepare(monkeypatch, tmp_path, stop_codex=False)
    contents = _write_targets(pairs)
    _link_all(pairs)
    _patch_process_iter(monkeypatch, processes)

    assert restore_codex_logs_linux.run(home_dir=home, shm_root=shm_root) is restored
    for home_path, target_path in pairs:
        assert home_path.is_symlink() is not restored
        assert target_path.read_bytes() == contents[target_path]


def test_running_warning_aggregates_labels_without_argument_values(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """延期の警告はラベルごとの件数を示し、実行ファイルパスとオプション値を含めない。"""
    home, shm_root, pairs = _prepare(monkeypatch, tmp_path, stop_codex=False)
    _write_targets(pairs)
    _link_all(pairs)
    processes = [
        _FakeProcess(
            {
                "name": "codex",
                "exe": f"/opt/codex-{index}/bin/codex",
                "cmdline": ["/opt/codex/bin/codex", "mcp-server", "--config", f"model=secret-{index}"],
                "uids": _uids(_OWN_UID),
            },
            pid=1000 + index,
        )
        for index in (1, 2)
    ]
    _patch_process_iter(monkeypatch, processes)

    with caplog.at_level("WARNING", logger=restore_codex_logs_linux.logger.name):
        assert restore_codex_logs_linux.run(home_dir=home, shm_root=shm_root) is False

    warning = "\n".join(record.getMessage() for record in caplog.records)
    assert "Codexが稼働中のため通常ストレージへの復元を延期: codex mcp-server (2件)" in warning
    assert "/opt/codex" not in warning
    assert "--config" not in warning
    assert "secret-1" not in warning


def test_normal_launch_label_excludes_user_prompt(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """通常起動の第2要素はプロンプトになり得るため、ラベルを実行名だけにする。"""
    home, shm_root, pairs = _prepare(monkeypatch, tmp_path, stop_codex=False)
    _write_targets(pairs)
    _link_all(pairs)
    prompt = "診断ログの状態を調べて"
    process = _FakeProcess(
        {"name": "codex", "exe": "/usr/bin/codex", "cmdline": ["codex", prompt], "uids": _uids(_OWN_UID)},
        pid=2000,
    )
    _patch_process_iter(monkeypatch, [process])

    with caplog.at_level("WARNING", logger=restore_codex_logs_linux.logger.name):
        assert restore_codex_logs_linux.run(home_dir=home, shm_root=shm_root) is False

    warning = "\n".join(record.getMessage() for record in caplog.records)
    assert "Codexが稼働中のため通常ストレージへの復元を延期: codex (1件)" in warning
    assert prompt not in warning


@pytest.mark.parametrize(
    ("info", "label"),
    [
        pytest.param(
            {"name": _DENIED, "exe": "/usr/bin/codex", "cmdline": _DENIED},
            "codex",
            id="executable-path-only",
        ),
        pytest.param(
            {"name": _DENIED, "exe": _DENIED, "cmdline": ["/opt/codex/bin/codex", "mcp-server", "--config", "model=x"]},
            "codex mcp-server",
            id="command-line-only",
        ),
    ],
)
def test_label_uses_executable_name_when_process_name_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    caplog: pytest.LogCaptureFixture,
    info: dict[str, object],
    label: str,
) -> None:
    """実行名を取得できなくても、Codexを識別できた実行ファイル名を警告のラベルへ用いる。"""
    home, shm_root, pairs = _prepare(monkeypatch, tmp_path, stop_codex=False)
    _write_targets(pairs)
    _link_all(pairs)
    _patch_process_iter(monkeypatch, [_FakeProcess({**info, "uids": _uids(_OWN_UID)}, pid=3000)])

    with caplog.at_level("WARNING", logger=restore_codex_logs_linux.logger.name):
        assert restore_codex_logs_linux.run(home_dir=home, shm_root=shm_root) is False

    warning = "\n".join(record.getMessage() for record in caplog.records)
    assert f"Codexが稼働中のため通常ストレージへの復元を延期: {label} (1件)" in warning
    assert "pid 3000" not in warning
    assert "/opt/codex" not in warning
    assert "--config" not in warning
