"""公開設定操作からengine別の解除待機判断へ設定を引き渡す。"""

import pathlib

import pytest

from agent_toolkit import atk
from agent_toolkit._atk import config, usage_limit_policy


@pytest.mark.parametrize("engine", ["claude", "codex"])
@pytest.mark.parametrize(
    "policy,expected",
    [("never", (False, False, False)), ("five-hour", (True, False, False)), ("always", (True, True, True))],
)
def test_configured_usage_wait_policy(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, engine, policy, expected):
    """保存と環境上書きで片方のengineだけの待機方針を変更する。"""
    monkeypatch.setattr(config.platformdirs, "user_config_dir", lambda _name, **_kwargs: str(tmp_path))
    for name in config.config_environment_variable_names():
        monkeypatch.delenv(name, raising=False)
    key = f"{engine}_usage_limit_wait"
    with pytest.raises(SystemExit, match="0"):
        atk.main(["config", "set", key, policy], home=tmp_path)
    assert tuple(usage_limit_policy.should_wait(engine, kind) for kind in ("five_hour", "seven_day", None)) == expected
    other = "codex" if engine == "claude" else "claude"
    assert usage_limit_policy.should_wait(other, "five_hour")
    assert not usage_limit_policy.should_wait(other, "seven_day")
    monkeypatch.setenv(f"AGENT_TOOLKIT_CONFIG_{key.upper()}", "always")
    assert usage_limit_policy.should_wait(engine, "seven_day_sonnet")
    before = (tmp_path / "config.json").read_bytes()
    with pytest.raises(SystemExit, match="2"):
        atk.main(["config", "set", key, "invalid"], home=tmp_path)
    assert (tmp_path / "config.json").read_bytes() == before
