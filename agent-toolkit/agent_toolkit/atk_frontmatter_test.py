"""atk frontmatterの契約を検証する。"""

from __future__ import annotations

import pathlib
import subprocess

import pytest

from agent_toolkit import atk
from agent_toolkit._atk.wi import add as _add
from agent_toolkit._testing import wi_bodies as _wi_bodies
from agent_toolkit._testing.atk_support import (
    _FIXED_DT,
    _body_file_args,
    _isolate_agent_and_managed_temp_environment,
    _setup_notes,
)
from agent_toolkit._testing.git_fakes import make_git_remote_fake as _make_git_remote_fake

__all__ = ["_isolate_agent_and_managed_temp_environment"]


class TestParseLeadingFrontmatter:
    def test_leading_frontmatter_overrides_target_repo(self) -> None:
        message = "---\ntarget_repo: github.com/x/y\n---\n\n本文"
        fm, body = _add._parse_leading_frontmatter(message)  # pylint: disable=protected-access  # noqa: SLF001
        assert fm == {"target_repo": "github.com/x/y"}
        assert body == "\n本文"

    def test_leading_frontmatter_source_priority(self) -> None:
        message = "---\ntarget_repo: github.com/x/y\nsource: session-review\n---\n\n本文"
        fm, body = _add._parse_leading_frontmatter(message)  # pylint: disable=protected-access  # noqa: SLF001
        assert fm == {"target_repo": "github.com/x/y", "source": "session-review"}
        assert body == "\n本文"

    def test_without_frontmatter_returns_original(self) -> None:
        message = "普通の本文\n2行目"
        fm, body = _add._parse_leading_frontmatter(message)  # pylint: disable=protected-access  # noqa: SLF001
        assert not fm
        assert body == message

    def test_body_horizontal_rule_not_treated_as_frontmatter(self) -> None:
        message = "---\n\n本文開始\n\n---\n\n本文継続"
        fm, body = _add._parse_leading_frontmatter(message)  # pylint: disable=protected-access  # noqa: SLF001
        assert not fm
        assert body == message

    def test_frontmatter_without_closing_returns_original(self) -> None:
        message = "---\ntarget_repo: github.com/x/y\n本文継続なし"
        fm, body = _add._parse_leading_frontmatter(message)  # pylint: disable=protected-access  # noqa: SLF001
        assert not fm
        assert body == message


class TestAddFrontmatterOverride:
    def test_message_frontmatter_overrides_cli_target_repo(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        notes = _setup_notes(tmp_path)
        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()

        monkeypatch.setattr(subprocess, "run", _make_git_remote_fake(myrepo))

        message = f"---\ntarget_repo: github.com/other/repo\nsource: session-review\n---\n\n{_wi_bodies.AGENT_AWI_BODY}"
        argv = ["wi", "add", str(myrepo), "--source", "cli-source", *_body_file_args(tmp_path, message)]

        with pytest.raises(SystemExit) as exc_info:
            atk.main(argv, home=tmp_path, now=_FIXED_DT)
        assert exc_info.value.code == 0

        files = list((notes / "2024/01").iterdir())
        assert len(files) == 1
        content = files[0].read_text(encoding="utf-8")
        assert "target_repo: github.com/other/repo" in content
        assert "source: session-review" in content
        body = content.split("---\n\n", 1)[1]
        assert body == _wi_bodies.AGENT_AWI_BODY

    def test_multiple_messages_mixed_frontmatter(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        notes = _setup_notes(tmp_path)
        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()

        monkeypatch.setattr(subprocess, "run", _make_git_remote_fake(myrepo))

        msg_with_fm = "---\ntarget_repo: github.com/override/repo\n---\n\nfm付き本文"
        msg_plain = "frontmatter無し本文"

        with pytest.raises(SystemExit) as exc_info:
            atk.main(
                ["wi", "add", str(myrepo), *_body_file_args(tmp_path, msg_with_fm, msg_plain)], home=tmp_path, now=_FIXED_DT
            )
        assert exc_info.value.code == 0

        inbox = notes / "2024/01"
        files = sorted(inbox.iterdir())
        assert len(files) == 2
        content_first = files[0].read_text(encoding="utf-8")
        content_second = files[1].read_text(encoding="utf-8")
        assert "target_repo: github.com/override/repo" in content_first
        assert content_first.split("---\n\n", 1)[1] == "fm付き本文"
        assert "target_repo: github.com/example/myrepo" in content_second
        assert content_second.split("---\n\n", 1)[1] == msg_plain + "\n"

    def test_frontmatter_target_repo_only_falls_back_to_cli_source(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        notes = _setup_notes(tmp_path)
        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()

        monkeypatch.setattr(subprocess, "run", _make_git_remote_fake(myrepo))

        message = f"---\ntarget_repo: github.com/other/repo\n---\n\n{_wi_bodies.AGENT_AWI_BODY}"

        with pytest.raises(SystemExit) as exc_info:
            atk.main(
                [
                    "wi",
                    "add",
                    str(myrepo),
                    "--source",
                    "cli-source",
                    *_body_file_args(tmp_path, message),
                ],
                home=tmp_path,
                now=_FIXED_DT,
            )
        assert exc_info.value.code == 0

        inbox = notes / "2024/01"
        files = list(inbox.iterdir())
        assert len(files) == 1
        content = files[0].read_text(encoding="utf-8")
        assert "target_repo: github.com/other/repo" in content
        assert "source: cli-source" in content
