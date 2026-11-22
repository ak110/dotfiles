from __future__ import annotations

import os
import pathlib
import stat
import subprocess
import sys
import tempfile

import pytest

from agent_toolkit._common import windows_security

pytestmark = pytest.mark.windows

_LEGACY_DACL = "D:P(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)(A;OICI;FA;;;OW)"
"""旧版がWindowsの`mkdir(mode=0o700)`と`tempfile.mkdtemp`で付けたDACL。"""


@pytest.mark.skipif(os.name == "nt", reason="Windows以外の挙動を確かめる")
def test_restore_inheritance_leaves_posix_directories_unchanged(tmp_path: pathlib.Path) -> None:
    """Windows以外では昇格を判定せず、ディレクトリのmodeも変えない。"""
    directory = tmp_path / "state"
    directory.mkdir(mode=0o700)
    assert windows_security.restore_inheritance(tmp_path) == windows_security.InheritanceRepair()
    assert not windows_security.is_elevated()
    assert not windows_security.is_elevated_above_user_processes()
    assert stat.S_IMODE(directory.stat().st_mode) == 0o700


@pytest.fixture(name="profile")
def _profile(tmp_path: pathlib.Path) -> pathlib.Path:
    if sys.platform != "win32":
        pytest.skip("Windowsのtokenと継承ACLを実APIで確かめる")
    if not windows_security.is_elevated():
        pytest.skip("昇格したtokenで実行した場合だけ、所有者がAdministratorsのファイルを作成できる")
    from agent_toolkit._testing import windows_tokens  # noqa: PLC0415  # pylint: disable=import-outside-toplevel

    base = tmp_path / "local-app-data"
    windows_tokens.create_profile_directory(base)
    return base


def test_restore_inheritance_reopens_legacy_directories(profile: pathlib.Path) -> None:
    from agent_toolkit._testing import windows_tokens  # noqa: PLC0415  # pylint: disable=import-outside-toplevel

    legacy = profile / "services"
    os.mkdir(legacy, 0o700)
    nested = pathlib.Path(tempfile.mkdtemp(prefix="session-", dir=legacy))
    files = (legacy / "generations.json", nested / "state.json")
    for path in files:
        path.write_text("{}", encoding="utf-8")
    assert not any(windows_tokens.readable_without_administrators(path) for path in files)

    result = windows_security.restore_inheritance(profile)

    assert not result.blocked
    assert set(result.restored) == {legacy, nested}
    for path in files:
        assert windows_tokens.readable_without_administrators(path), windows_security.dacl_sddl(path)
    assert windows_security.restore_inheritance(profile) == windows_security.InheritanceRepair()


def test_restore_inheritance_reopens_owned_items_closed_to_the_user_without_administrators(profile: pathlib.Path) -> None:
    """所有者がユーザーでSYSTEMとAdministratorsだけを許す項目を、非昇格のプロセスが継承するACLへ戻す。

    2026-11-19、sthenoで世代索引など4,985件がこの形（保護付きの`(A;;FA;;;SY)(A;;FA;;;BA)`と、
    その形のディレクトリから継承した子）になり、非昇格の管理サービスが世代索引を読めずに起動できなかった。
    OWNER RIGHTSのACEが無ければ所有者は暗黙のREAD_CONTROLとWRITE_DACを持つため、非昇格でもDACLを戻せる。
    """
    from agent_toolkit._testing import windows_tokens  # noqa: PLC0415  # pylint: disable=import-outside-toplevel

    user = windows_security.user_sid()
    services = profile / "services"
    windows_tokens.create_directory(services, f"O:{user}{_LEGACY_DACL}")
    generations = services / "generations.json"
    windows_tokens.create_file(generations, f"O:{user}D:P(A;;FA;;;SY)(A;;FA;;;BA)")
    closed = profile / "closed"
    windows_tokens.create_directory(closed, f"O:{user}D:P(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)")
    inherited = closed / "state.json"
    windows_tokens.create_file(inherited, f"O:{user}")
    files = (generations, inherited)
    assert not any(windows_tokens.readable_without_administrators(path) for path in files)

    with windows_tokens.without_administrators():
        result = windows_security.restore_inheritance(profile)

    assert not result.blocked
    assert set(result.restored) == {services, generations, closed}
    for path in files:
        assert windows_tokens.readable_without_administrators(path), windows_security.dacl_sddl(path)
    with windows_tokens.without_administrators():
        assert windows_security.restore_inheritance(profile) == windows_security.InheritanceRepair()


def test_restore_inheritance_ignores_owner_rights_inherited_from_outside_root(
    profile: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """root外の親から継承したOWNER RIGHTSのACEは対象にせず、明示したディレクトリだけを1回で戻す。

    WindowsのCIでは、更新検証のhomeが`tempfile.mkdtemp`の配下にあり、状態ディレクトリの全配下がOWNER RIGHTSを継承した。
    継承したACEも対象にすると、戻した後も全配下を対象と誤認し、配下ごとに走査と継承の再計算を繰り返した。
    """
    # 通常のプロセスも昇格したtokenで動く環境（WindowsのCI）として判定する。
    # UACの承認モードでは所有者がAdministratorsのrootを通常のプロセスが読めず、この試験の前提にならない。
    monkeypatch.setattr(windows_security, "is_elevated_above_user_processes", lambda: False)
    outside = profile / "outside"
    os.mkdir(outside, 0o700)
    root = outside / "agent-toolkit"
    os.mkdir(root, 0o700)
    (root / "services" / "agents-server").mkdir(parents=True)

    assert windows_security.restore_inheritance(root) == windows_security.InheritanceRepair(restored=(root,))
    assert windows_security.restore_inheritance(root) == windows_security.InheritanceRepair()


def test_restore_inheritance_leaves_items_owned_by_administrators_for_elevated_run(
    profile: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """非昇格のプロセスは所有者がAdministratorsで読めない項目を変えずに返し、UACの環境で昇格した実行が戻す。

    非昇格のtokenで親の保護を外しても、OSは書けない配下を更新しないまま成功を返す。
    残った配下は読めない項目として次の走査でも見つかる。
    """
    from agent_toolkit._testing import windows_tokens  # noqa: PLC0415  # pylint: disable=import-outside-toplevel

    legacy = profile / "services"
    # 非昇格のプロセスが旧版で作成したディレクトリ（所有者がユーザー）を再現する。
    windows_tokens.create_directory(legacy, f"O:{windows_security.user_sid()}{_LEGACY_DACL}")
    written = legacy / "generations.json"
    written.write_text("{}", encoding="utf-8")
    assert not windows_tokens.readable_without_administrators(written)

    with windows_tokens.without_administrators():
        limited = windows_security.restore_inheritance(profile)

    assert limited.restored == (legacy,)
    assert [path for path, _reason in limited.blocked] == [written]
    assert not windows_tokens.readable_without_administrators(written)

    # WindowsのCIは組み込みAdministratorでUACの承認モードを適用しないため、通常のプロセスも昇格する環境として判定される。
    monkeypatch.setattr(windows_security, "is_elevated_above_user_processes", lambda: True)
    elevated = windows_security.restore_inheritance(profile)

    assert elevated == windows_security.InheritanceRepair(restored=(written,))
    assert windows_tokens.readable_without_administrators(written)


def test_restore_inheritance_does_not_follow_junctions(profile: pathlib.Path) -> None:
    from agent_toolkit._testing import windows_tokens  # noqa: PLC0415  # pylint: disable=import-outside-toplevel

    marked = profile / "marked"
    windows_tokens.create_directory(marked, _LEGACY_DACL)
    shared = profile / "shared"
    user = windows_security.user_sid()
    windows_tokens.create_directory(shared, f"D:(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)(A;OICI;FA;;;{user})(A;OICI;FR;;;LS)")
    inheriting = shared / "inheriting"
    inheriting.mkdir()
    holder = profile / "legacy-link-holder"
    os.mkdir(holder, 0o700)
    for name, target in (("marked", marked), ("inheriting", inheriting)):
        created = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(holder / name), str(target)],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        assert created.returncode == 0, created.stderr
    before = {path: windows_security.dacl_sddl(path) for path in (marked, inheriting)}
    assert "ID;FR;;;LS)" in before[inheriting]

    result = windows_security.restore_inheritance(holder)

    assert result == windows_security.InheritanceRepair(restored=(holder,))
    assert {path: windows_security.dacl_sddl(path) for path in before} == before
