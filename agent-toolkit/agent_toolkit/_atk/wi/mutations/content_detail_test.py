import pathlib
import subprocess

import pytest

from agent_toolkit import atk
from agent_toolkit._atk.wi import frontmatter as frontmatter_parser
from agent_toolkit._atk.wi import processing_owner
from agent_toolkit._atk.wi.mutations import content
from agent_toolkit._testing.wi_mutations_content_support import _edit_body_args
from agent_toolkit.atk_test import _make_subprocess_fake, _setup_notes

_OWNER = processing_owner.Owner("host-a", "session-a")


def _processing_entry(
    notes: pathlib.Path, *, source: str = "process-wi", entry_type: str = "awi", owner: str = "session-a"
) -> pathlib.Path:
    directory = notes / "2026" / "10"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "fb-001.md"
    path.write_text(
        "---\n"
        "state: processing\n"
        "target_repo: github.com/example/foo\n"
        f"type: {entry_type}\n"
        f"source: {source}\n"
        "processing_owner:\n"
        "  host: host-a\n"
        f"  session: {owner}\n"
        "---\n\n編集前\n",
        encoding="utf-8",
    )
    return path


@pytest.fixture(name="agent_env")
def fixture_agent_env(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> pathlib.Path:
    monkeypatch.setenv("AI_AGENT", "1")
    monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))
    monkeypatch.setattr(processing_owner, "current", lambda: _OWNER)
    return _setup_notes(tmp_path)


def _detail(tmp_path: pathlib.Path, text: str) -> int:
    with pytest.raises(SystemExit) as exc_info:
        atk.main([*_edit_body_args(tmp_path, "fb-001.md", text), "--detail"], home=tmp_path)
    return int(exc_info.value.code or 0)


def test_owner_details_once_and_second_replacement_is_rejected(agent_env: pathlib.Path, tmp_path: pathlib.Path) -> None:
    path = _processing_entry(agent_env)

    assert _detail(tmp_path, "# 詳細化した要求\n\n本文\n") == 0
    parsed = frontmatter_parser.parse_frontmatter(path.read_text(encoding="utf-8"))
    assert parsed is not None
    assert parsed[0]["detailed"] in (True, "true")
    assert "詳細化した要求" in parsed[1]

    saved = path.read_text(encoding="utf-8")
    assert _detail(tmp_path, "# 二回目\n") == 2
    assert path.read_text(encoding="utf-8") == saved


@pytest.mark.parametrize(
    ("source", "owner"),
    [
        ("process-wi", "session-b"),
        ("add-awi-by-user", "session-a"),
        ("process-user-comments", "session-a"),
        ("implement", "session-a"),
    ],
    ids=["other-conversation", "add-awi-by-user", "process-user-comments", "implement"],
)
def test_detail_is_rejected_for_other_conversation_and_exempt_sources(
    agent_env: pathlib.Path, tmp_path: pathlib.Path, source: str, owner: str
) -> None:
    path = _processing_entry(agent_env, source=source, owner=owner)
    original = path.read_text(encoding="utf-8")

    assert _detail(tmp_path, "# 置換\n") == 2
    assert path.read_text(encoding="utf-8") == original


def test_detail_is_rejected_for_uwi(agent_env: pathlib.Path, tmp_path: pathlib.Path) -> None:
    path = _processing_entry(agent_env, entry_type="uwi")
    original = path.read_text(encoding="utf-8")

    assert _detail(tmp_path, "# 置換\n") != 0
    assert path.read_text(encoding="utf-8") == original


def test_replacement_without_detail_stays_rejected(agent_env: pathlib.Path, tmp_path: pathlib.Path) -> None:
    path = _processing_entry(agent_env)
    original = path.read_text(encoding="utf-8")

    with pytest.raises(SystemExit) as exc_info:
        atk.main(_edit_body_args(tmp_path, "fb-001.md", "# 置換\n"), home=tmp_path)

    assert exc_info.value.code == 2
    assert path.read_text(encoding="utf-8") == original


def test_body_frontmatter_cannot_set_detailed(agent_env: pathlib.Path, tmp_path: pathlib.Path) -> None:
    path = _processing_entry(agent_env)
    original = path.read_text(encoding="utf-8")

    assert _detail(tmp_path, "---\ndetailed: true\n---\n# 置換\n") != 0
    assert path.read_text(encoding="utf-8") == original


def _hold_entry(notes: pathlib.Path, header: str) -> pathlib.Path:
    path = notes / "2026" / "10" / "fb-001.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\nstate: hold\ntarget_repo: github.com/example/foo\ntype: awi\n{header}---\n\n編集前\n", encoding="utf-8"
    )
    return path


@pytest.mark.parametrize(
    ("header", "frozen"),
    [
        ("source: process-wi\ndetailed: true\n", True),
        ("source: implement\n", True),
        ("source: add-awi-by-user\n", False),
    ],
    ids=["detailed", "implement", "add-awi-by-user"],
)
def test_hold_awi_body_freeze(agent_env: pathlib.Path, tmp_path: pathlib.Path, header: str, frozen: bool) -> None:
    path = _hold_entry(agent_env, header)
    saved = path.read_text(encoding="utf-8")
    with pytest.raises(SystemExit) as exc_info:
        atk.main(_edit_body_args(tmp_path, "fb-001.md", "# 置換\n"), home=tmp_path)
    if frozen:
        assert exc_info.value.code == 2
        assert path.read_text(encoding="utf-8") == saved
    else:
        assert (exc_info.value.code or 0) == 0
        assert "置換" in path.read_text(encoding="utf-8")


def test_implement_hold_awi_accepts_merge_update(agent_env: pathlib.Path) -> None:
    path = _hold_entry(agent_env, "source: implement\n")
    merge_update = "---\ntype: awi\nmerge_branch: feat/example\nmerge_commit: " + "a" * 40 + "\n---\n\n# 本文\n"
    content.reject_frozen_hold_edit(path, merge_update)
    with pytest.raises(SystemExit):
        content.reject_frozen_hold_edit(path, "---\ntype: awi\n---\n\n# 本文\n")
