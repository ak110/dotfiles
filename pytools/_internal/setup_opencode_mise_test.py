import pytest

from pytools._internal import common, setup_opencode_cli


def test_minimal_does_not_resolve_or_install(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_DOTFILES_MODE", "minimal")

    def unexpected(*_args: object, **_kwargs: object) -> None:
        pytest.fail("minimalで導入経路へ到達した")

    monkeypatch.setattr(setup_opencode_cli, "find_launcher", unexpected)
    monkeypatch.setattr(common, "run_subprocess", unexpected)
    result = setup_opencode_cli.run()
    assert not result.changed
    assert result.failure is None
