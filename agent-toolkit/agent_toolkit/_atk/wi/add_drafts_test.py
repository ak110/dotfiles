import pathlib
from collections.abc import Callable

import pytest

from agent_toolkit import atk
from agent_toolkit._atk.wi import add as wi_add
from agent_toolkit._atk.wi import add_cli, add_drafts, frontmatter, layout
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


def test_cli_rejects_detailed_marker_in_draft(
    drafts: tuple[pathlib.Path, Callable[..., int]],
    capsys: pytest.CaptureFixture[str],
) -> None:
    notes, add = drafts
    draft = _draft("A").replace("---\n", "---\ndetailed: true\n", 1)

    assert add(draft) == 1
    assert "--detailed" in capsys.readouterr().err
    assert not list(layout.iter_entry_paths(notes))


def test_cli_saves_detailed_awi_on_hold(drafts: tuple[pathlib.Path, Callable[..., int]]) -> None:
    notes, add = drafts

    assert add(_draft("A"), options=("--detailed", "--hold")) == 0

    _, metadata, _ = _saved(notes)["A"]
    assert metadata["type"] == "awi"
    assert metadata["state"] == "hold"
    assert metadata["detailed"] == "true"


@pytest.mark.parametrize("proposal", ["answer/20261101-000000-001.md", "implement/plan-list/1"])
def test_cli_retry_reports_existing_proposal_without_saving_again(
    drafts: tuple[pathlib.Path, Callable[..., int]],
    capsys: pytest.CaptureFixture[str],
    proposal: str,
) -> None:
    notes, add = drafts
    draft = _draft("A", proposal=proposal)

    assert add(draft) == 0
    (saved,) = list(layout.iter_entry_paths(notes))
    original = saved.read_bytes()
    assert _saved(notes)["A"][1]["proposal_id"] == proposal
    capsys.readouterr()

    assert add(draft) == 0

    output = capsys.readouterr()
    assert "0件をinboxへ投入した" in output.out
    assert f"登録済みのため再登録しなかった: {saved.name}" in output.err
    assert list(layout.iter_entry_paths(notes)) == [saved]
    assert saved.read_bytes() == original


def _unkeyed(title: str) -> str:
    return f"# 原稿 {title}\n\n原稿の確認。\n\n## 反映内容と反映先\n\n対象を変更する。\n"


@pytest.fixture(name="namespace")
def _namespace(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> pathlib.Path:
    work = tmp_path / "work"
    work.mkdir()
    (work / "work-contract.md").touch()
    monkeypatch.setattr(wi_add, "resolve_submitter_session", lambda: "draft-test")
    monkeypatch.setattr(add_cli.managed_temp, "find_session_temp", lambda _: work)
    return work


@pytest.mark.usefixtures("namespace")
def test_cli_retries_seven_unkeyed_drafts_without_saving_again(
    drafts: tuple[pathlib.Path, Callable[..., int]],
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    notes, add = drafts
    messages = tuple(_unkeyed(str(index)) for index in range(7))
    monkeypatch.setattr(wi_add, "resolve_add_target", lambda _: ("github.com/example/repo", notes))
    monkeypatch.setattr(wi_add, "resolve_head_commit", lambda *_args, **_kwargs: "a" * 40)
    assert add(*messages) == 0
    before = _saved(notes)
    assert len(before) == 7
    assert all(data["target_commit"] == "a" * 40 and add_drafts.RETRY_FINGERPRINT in data for _, data, _ in before.values())
    capsys.readouterr()

    monkeypatch.setattr(wi_add, "resolve_head_commit", lambda *_args, **_kwargs: "b" * 40)
    assert add(*messages, options=("--dry-run",)) == 0
    preview = capsys.readouterr().err
    assert _saved(notes) == before
    assert add(*messages) == 0
    saved = capsys.readouterr()
    assert "0件をinboxへ投入した" in saved.out
    for name, _, _ in before.values():
        warning = f"同じ原稿は登録済みのため再登録しなかった: {name}"
        assert preview.count(warning) == saved.err.count(warning) == 1
    assert _saved(notes) == before


@pytest.mark.usefixtures("namespace")
@pytest.mark.parametrize(
    ("body_changes", "options"),
    [
        (True, ()),
        (False, ("--target-repo=github.com/example/other",)),
        (False, ("--depends-on=20261101-000000-001.md",)),
        (False, ("--detailed",)),
        (False, ("--hold",)),
        (False, ("--cooldown-until=2099-01-01T00:00:00+00:00",)),
    ],
)
def test_unkeyed_retry_distinguishes_saved_data(
    drafts: tuple[pathlib.Path, Callable[..., int]], body_changes: bool, options: tuple[str, ...]
) -> None:
    notes, add = drafts
    draft = _unkeyed("A")
    assert add(draft) == 0
    assert add(draft.replace("確認", "確証") if body_changes else draft, options=options) == 0
    assert len(list(layout.iter_entry_paths(notes))) == 2


def test_unkeyed_retry_namespace_and_explicit_id_are_independent(
    drafts: tuple[pathlib.Path, Callable[..., int]],
    namespace: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    notes, add = drafts
    draft = _unkeyed("A")
    assert add(draft, draft) == 0
    (path,) = list(layout.iter_entry_paths(notes))
    parsed = frontmatter.parse_frontmatter(path.read_text(encoding="utf-8"))
    assert parsed is not None
    assert add_drafts.FINGERPRINT not in parsed[0]
    assert f"再登録しなかった: {path.name}" in capsys.readouterr().err
    assert add(f"---\nproposal_id: {parsed[0][add_drafts.RETRY_FINGERPRINT]}\n---\n{draft}") == 0
    assert len(list(layout.iter_entry_paths(notes))) == 2

    other = namespace.with_name("other")
    other.mkdir()
    (other / "work-contract.md").touch()
    monkeypatch.setattr(add_cli.managed_temp, "find_session_temp", lambda _: other)
    assert add(draft) == 0
    assert len(list(layout.iter_entry_paths(notes))) == 3


@pytest.mark.parametrize("is_uwi", [False, True])
def test_unkeyed_add_without_automatic_retry(
    drafts: tuple[pathlib.Path, Callable[..., int]], namespace: pathlib.Path, is_uwi: bool
) -> None:
    notes, add = drafts
    if is_uwi:
        options = ("--type=uwi", "--question-type=yes-no")
    else:
        (namespace / "work-contract.md").unlink()
        options = ()
    assert add(_unkeyed("A"), options=options) == 0
    assert add(_unkeyed("A"), options=options) == 0
    saved = list(layout.iter_entry_paths(notes))
    assert len(saved) == 2
    assert all(add_drafts.RETRY_FINGERPRINT not in path.read_text(encoding="utf-8") for path in saved)


@pytest.mark.usefixtures("namespace")
def test_retry_fingerprint_cannot_be_supplied_or_changed(
    drafts: tuple[pathlib.Path, Callable[..., int]], tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    notes, add = drafts
    reserved = add_drafts.RETRY_FINGERPRINT
    proposal = f"---\n{reserved}: invalid\n---\n{_unkeyed('A')}"
    assert add(proposal) == 1
    assert reserved in capsys.readouterr().err
    assert not list(layout.iter_entry_paths(notes))

    assert add(_unkeyed("A")) == 0
    (saved,) = list(layout.iter_entry_paths(notes))
    before = saved.read_bytes()
    edit = tmp_path / "edit.md"
    edit.write_text(proposal, encoding="utf-8")
    with pytest.raises(SystemExit) as exit_info:
        atk.main(["wi", "edit", saved.name, "--target-repo=github.com/example/repo", "--body-file", str(edit)], home=tmp_path)
    assert exit_info.value.code == 1
    assert reserved in capsys.readouterr().err
    assert saved.read_bytes() == before
