"""pytools._internal.claude_common のテスト。"""

import pytest

from pytools._internal import claude_common


class TestIsEuryale:
    """``is_euryale``のplatform・大文字小文字・FQDN接尾辞の扱いを検証する。"""

    @pytest.mark.parametrize(
        ("platform", "hostname", "expected"),
        [
            pytest.param("linux", "euryale", True, id="exact-match"),
            pytest.param("linux", "EURYALE", True, id="uppercase"),
            pytest.param("linux", "Euryale.example.test", True, id="fqdn-suffix-stripped"),
            pytest.param("linux", "other-host", False, id="other-host"),
            pytest.param("win32", "euryale", False, id="non-linux"),
        ],
    )
    def test_matches_only_linux_euryale(
        self,
        monkeypatch: pytest.MonkeyPatch,
        platform: str,
        hostname: str,
        expected: bool,
    ) -> None:
        monkeypatch.setattr(claude_common.sys, "platform", platform)
        monkeypatch.setattr(claude_common.socket, "gethostname", lambda: hostname)
        assert claude_common.is_euryale() is expected
