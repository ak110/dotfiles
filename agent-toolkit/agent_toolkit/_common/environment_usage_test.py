import errno
from pathlib import Path

import pytest

from agent_toolkit._common import environment_usage

pytestmark = pytest.mark.windows


@pytest.mark.parametrize("write_error", [errno.EACCES, errno.EROFS])
def test_existing_read_only_lock_still_protects_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, write_error: int
) -> None:
    environment = tmp_path / "prepared"
    path = tmp_path / "prepared.usage-lock"
    path.touch()
    normal_open = Path.open

    def limited_open(self: Path, mode: str = "r", /, **kwargs):
        if self == path and mode == "a+b":
            raise OSError(write_error, "書き込み不可", self)
        return normal_open(self, mode, **kwargs)

    with monkeypatch.context() as denied:
        denied.setattr(Path, "open", limited_open)
        with pytest.raises(OSError) as denied_exclusive, environment_usage.lock(environment, exclusive=True):
            pytest.fail("排他ロックを読み取り専用で取得した")
        assert denied_exclusive.value.errno == write_error
        with environment_usage.lock(environment) as reader:
            assert not reader.writable()
            denied.undo()
            with pytest.raises(OSError), environment_usage.lock(environment, exclusive=True):
                pytest.fail("共有ロック中に排他ロックを取得した")
    with environment_usage.lock(environment, exclusive=True):
        assert path.is_file()


def test_no_read_only_fallback_if_lock_does_not_exist(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    environment = tmp_path / "not-prepared"
    path = tmp_path / "not-prepared.usage-lock"
    normal_open = Path.open

    def limited_open(self: Path, mode: str = "r", /, **kwargs):
        if self == path and mode == "a+b":
            raise PermissionError(errno.EACCES, "書き込み不可", self)
        return normal_open(self, mode, **kwargs)

    monkeypatch.setattr(Path, "open", limited_open)
    with pytest.raises(PermissionError), environment_usage.lock(environment):
        pytest.fail("存在しないロックを開いた")
    assert not path.exists()
