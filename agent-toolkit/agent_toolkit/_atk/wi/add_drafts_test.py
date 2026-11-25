import pathlib
from collections.abc import Callable

import pytest

from agent_toolkit import atk
from agent_toolkit._atk.wi import frontmatter, layout
from agent_toolkit._testing import git_repository
from agent_toolkit.atk_test import _FIXED_DT, _setup_notes


@pytest.fixture(name="drafts")
def _drafts_fixture(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> tuple[pathlib.Path, Callable[..., int]]:
    notes = _setup_notes(tmp_path)
    git_repository.init_repository(notes)
    monkeypatch.setenv("AGENT_TOOLKIT_PRIVATE_NOTES", str(notes))

    def add(*messages: str, options: tuple[str, ...] = ()) -> int:
        argv = ["wi", "add", "--target-repo=github.com/example/repo", "--source=agent", *options]
        for index, message in enumerate(messages):
            path = tmp_path / f"draft-{index}.md"
            path.write_text(message, encoding="utf-8")
            argv.extend(("--body-file", str(path)))
        with pytest.raises(SystemExit) as result:
            atk.main(argv, home=tmp_path, now=_FIXED_DT)
        assert isinstance(result.value.code, int)
        return result.value.code

    return notes, add


def _draft(key: str, *, needs: str = "", proposal: str | None = None, reference: str = "") -> str:
    metadata: dict[str, object] = {"key": key}
    if needs:
        metadata["needs"] = [needs]
    if proposal is not None:
        metadata["proposal_id"] = proposal
    return frontmatter.serialize_frontmatter(
        metadata, f"\n# 原稿 {key}\n\n原稿の確認。{reference}\n\n## 反映内容と反映先\n\n対象を変更する。\n"
    )


def _saved(notes: pathlib.Path) -> dict[str, tuple[str, dict[str, object], str]]:
    result = {}
    for path in layout.iter_entry_paths(notes):
        parsed = frontmatter.parse_frontmatter(path.read_text(encoding="utf-8"))
        assert parsed is not None
        data, body = parsed
        result[body.split("# 原稿 ", 1)[1].splitlines()[0]] = (path.name, data, body)
    return result


def test_cli_drafts_resolve_a_three_item_chain_and_retry(
    drafts: tuple[pathlib.Path, Callable[..., int]],
    capsys: pytest.CaptureFixture[str],
) -> None:
    notes, add = drafts
    a = _draft("A", proposal="project/A", needs="B", reference="{{key:B}}")
    b = _draft("B", proposal="project/B", needs="C", reference="{{key:C}}")
    c = _draft("C", proposal="project/C")
    assert add(a, b, c, options=("--dry-run",)) == 0
    assert "A → 保存時に決まる" in capsys.readouterr().out
    assert not _saved(notes)

    assert add(c) == 0
    original_name = _saved(notes)["C"][0]
    assert add(a, b, c) == 0
    saved = _saved(notes)
    a_name, a_data, a_body = saved["A"]
    b_name, b_data, b_body = saved["B"]
    assert saved["C"][0] == original_name
    assert a_data["depends_on"] == [b_name]
    assert b_data["depends_on"] == [original_name]
    assert b_name in a_body and original_name in b_body
    assert "{{key:" not in a_body + b_body
    assert all("key" not in data and "needs" not in data for _, data, _ in saved.values())
    assert a_name != b_name

    assert add(a, b, c) == 0
    assert _saved(notes) == saved
    assert add(_draft("new"), c.replace("確認。", "変更。")) == 1
    diagnostic = capsys.readouterr().err
    assert original_name in diagnostic and "atk wi edit" in diagnostic
    assert _saved(notes) == saved


def test_cli_saves_three_new_drafts_and_replaces_both_links(
    drafts: tuple[pathlib.Path, Callable[..., int]],
) -> None:
    notes, add = drafts
    assert not _saved(notes)
    assert add(_draft("A", needs="B", reference="{{key:B}}"), _draft("B", needs="C", reference="{{key:C}}"), _draft("C")) == 0
    saved = _saved(notes)
    assert set(saved) == {"A", "B", "C"}
    assert len({filename for filename, _, _ in saved.values()}) == 3
    for key, dependency in (("A", "B"), ("B", "C")):
        _, metadata, body = saved[key]
        target = saved[dependency][0]
        assert metadata["depends_on"] == [target]
        assert target in body
        assert "{{key:" not in body


@pytest.mark.parametrize(
    ("first", "second_key", "second_needs", "options", "diagnostic"),
    [
        ("A", "A", "", (), "重複"),
        ("A", "B", "absent", (), "指定したキーまたはWIが無い"),
        ("B", "B", "A", (), "循環"),
        ("A", "B", "A", ("--type=uwi", "--question-type=yes-no"), "UWIはneeds"),
    ],
)
def test_cli_rejects_invalid_draft_sets_before_writing(
    drafts: tuple[pathlib.Path, Callable[..., int]],
    capsys: pytest.CaptureFixture[str],
    first: str,
    second_key: str,
    second_needs: str,
    options: tuple[str, ...],
    diagnostic: str,
) -> None:
    notes, add = drafts
    assert add(_draft("A", needs=first if first == "B" else ""), _draft(second_key, needs=second_needs), options=options) == 1
    assert diagnostic in capsys.readouterr().err
    assert not _saved(notes)


def test_legacy_proposal_can_be_skipped_but_not_used_as_a_key(
    drafts: tuple[pathlib.Path, Callable[..., int]],
    capsys: pytest.CaptureFixture[str],
) -> None:
    notes, add = drafts
    old_file = layout.entry_path(notes, "20261201-101010-001.md")
    old_file.parent.mkdir(parents=True, exist_ok=True)
    old_file.write_text(
        frontmatter.serialize_frontmatter(
            {"type": "awi", "state": "inbox", "target_repo": "github.com/example/repo", "proposal_id": "legacy"},
            "\n# 原稿 legacy\n\n旧形式\n",
        ),
        encoding="utf-8",
    )
    old = _draft("legacy", proposal="legacy")
    assert add(old) == 0
    assert "原稿の指紋が無い" in capsys.readouterr().err
    assert add(old, _draft("B", needs="legacy")) == 1
    assert old_file.name in capsys.readouterr().err
    assert set(_saved(notes)) == {"legacy"}
