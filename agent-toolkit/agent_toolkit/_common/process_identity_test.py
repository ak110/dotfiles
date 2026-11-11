"""時計補正後も同じ世代を保持し、別世代への作用を防ぐ。"""

import dataclasses
import json
import os
import pathlib
import sys

import psutil
import pytest

from agent_toolkit._common import process_identity


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linuxのprocfs境界")
def test_clock_correction_and_legacy_records(monkeypatch):
    """壁時計だけが変わっても、新形式の識別と旧形式の資源保護を保つ。"""
    process = psutil.Process(os.getpid())
    saved = process_identity.ProcessIdentity.capture(process)
    legacy_payload = json.loads(json.dumps(saved.payload(legacy=True)))
    stable_payload = json.loads(json.dumps(saved.payload()))
    restored = process_identity.ProcessIdentity.from_payload(legacy_payload, stable=stable_payload)
    assert not process_identity.ProcessIdentity.from_payload(
        {**legacy_payload, "create_time": saved.create_time - 1}, stable=stable_payload
    ).matches(process)
    created = process.create_time()

    def shifted_create_time(self, **kwargs):
        del self, kwargs
        return created + 9

    monkeypatch.setattr(psutil.Process, "create_time", shifted_create_time)
    assert restored.exists()
    assert restored.matches(psutil.Process(process.pid))
    assert restored == process_identity.ProcessIdentity.capture(psutil.Process(process.pid))
    legacy = process_identity.ProcessIdentity(saved.pid, saved.create_time)
    assert legacy.exists()
    assert not legacy.matches(psutil.Process(process.pid))
    assert legacy.compatible_with(saved)
    assert not legacy.compatible_with(process_identity.ProcessIdentity.capture(psutil.Process(process.pid)))


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linuxのprocfs境界")
def test_ancestor_traversal_ignores_wall_clock_changes(monkeypatch):
    """親の壁時計時刻が子より後に見えても、PPIDと不変な起動順序から祖先を返す。"""
    process = psutil.Process(os.getpid())
    expected = [item.pid for item in process_identity.parents(process)]
    assert expected and expected[0] == process.ppid()
    original = psutil.Process.create_time

    def corrected(self, **kwargs):
        del kwargs
        return original(self) + (0 if self.pid == process.pid else 10**12)

    monkeypatch.setattr(psutil.Process, "create_time", corrected)
    assert [item.pid for item in process_identity.parents(psutil.Process(process.pid))] == expected


@pytest.mark.parametrize("changed", ["boot", "start"])
@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linuxのprocfs境界")
def test_reused_pid_or_reboot_is_not_same_process(tmp_path, monkeypatch, changed):
    """同じPIDでも起動ticksかboot IDが異なれば、保存した世代の生存を否定する。"""
    process = psutil.Process(os.getpid())
    saved = process_identity.ProcessIdentity.capture(process)
    assert saved.start_id is not None
    boot = tmp_path / "sys/kernel/random/boot_id"
    boot.parent.mkdir(parents=True)
    boot.write_text("another-boot" if changed == "boot" else saved.start_id.split(":")[1], encoding="ascii")
    stat = tmp_path / str(process.pid) / "stat"
    stat.parent.mkdir()
    value = pathlib.Path(f"/proc/{process.pid}/stat").read_text(encoding="utf-8")
    comm, fields = value.rsplit(")", 1)
    parts = fields.split()
    if changed == "start":
        parts[19] = str(int(parts[19]) + 1)
    stat.write_text(comm + ") " + " ".join(parts), encoding="utf-8")
    monkeypatch.setattr(process_identity, "_PROC_ROOT", tmp_path)
    assert not saved.exists()
    assert not saved.matches(psutil.Process(process.pid))


def test_current_process_and_missing_pid():
    """実OSの同世代と消滅済みPIDを識別する。"""
    saved = process_identity.ProcessIdentity.of(os.getpid())
    assert saved is not None and saved.exists()
    assert not dataclasses.replace(saved, pid=2147483647).exists()
