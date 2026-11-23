import pathlib
import subprocess

import pytest

from agent_toolkit import atk
from agent_toolkit._testing.wi_mutations_content_support import _edit_body_args
from agent_toolkit.atk_test import _make_subprocess_fake, _setup_notes, _write_awi_file

_COMMENT = "1行目の指摘\n\n> 引用を含む2行目"
_QUOTE_HEAD = "整理後の本文\n\n## ユーザー指摘の逐語引用\n\n"
_MOVED_BODY = "整理後の本文\n\n## ユーザー指摘の逐語引用\n\n> 1行目の指摘\n>\n> > 引用を含む2行目\n\n> 対話での回答\n"


def _prepare(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, comment: str = _COMMENT) -> pathlib.Path:
    notes = _setup_notes(tmp_path)
    path = _write_awi_file(notes, "fb.md", body=f"整理前の本文\n\n## ユーザーコメント\n\n{comment}")
    monkeypatch.setenv("AI_AGENT", "1")
    monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))
    return path


def _move_args(tmp_path: pathlib.Path, body: str, read_comment: str = _COMMENT) -> list[str]:
    expected = tmp_path / "read-comment.md"
    expected.write_text(read_comment, encoding="utf-8")
    return [*_edit_body_args(tmp_path, "fb.md", body), "--move-user-comment", str(expected)]


def _run(args: list[str], tmp_path: pathlib.Path) -> int | str | None:
    with pytest.raises(SystemExit) as exc_info:
        atk.main(args, home=tmp_path)
    return exc_info.value.code


def test_move_removes_comment_quoted_in_body(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = _prepare(tmp_path, monkeypatch)

    assert _run(_move_args(tmp_path, _MOVED_BODY), tmp_path) == 0

    text = path.read_text(encoding="utf-8")
    assert "## ユーザーコメント" not in text
    assert text.endswith(_MOVED_BODY)


@pytest.mark.parametrize(
    "body",
    [
        pytest.param("整理後の本文\n\n## ユーザー指摘の逐語引用\n\n> 1行目の指摘\n", id="missing-line"),
        pytest.param("整理後の本文\n\n> 1行目の指摘\n>\n> > 引用を含む2行目\n", id="outside-quote-section"),
        pytest.param("整理後の本文\n\n## ユーザー指摘の逐語引用\n\n> > 引用を含む2行目\n>\n> 1行目の指摘\n", id="reordered"),
        pytest.param(_QUOTE_HEAD + "> 1行目の指摘\n> > 引用を含む2行目\n", id="paragraph-merged"),
    ],
)
def test_move_rejects_body_without_whole_comment(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], body: str
) -> None:
    path = _prepare(tmp_path, monkeypatch)
    original = path.read_bytes()

    assert _run(_move_args(tmp_path, body), tmp_path) == 1

    assert path.read_bytes() == original
    err = capsys.readouterr().err
    assert "ユーザー指摘の逐語引用" in err
    assert "atk wi show fb.md --section ユーザーコメント" in err


@pytest.mark.parametrize(
    "stored",
    [
        pytest.param(_COMMENT + "\n\n後から追記した指摘", id="appended"),
        pytest.param("1行目の指摘", id="deleted"),
        pytest.param("1行目の指摘\n\n> 書き換えた2行目", id="rewritten"),
    ],
)
def test_move_rejects_comment_changed_after_reading(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], stored: str
) -> None:
    path = _prepare(tmp_path, monkeypatch, comment=stored)
    original = path.read_bytes()

    assert _run(_move_args(tmp_path, _MOVED_BODY), tmp_path) == 1

    assert path.read_bytes() == original
    assert "整理に使ったコメントと一致しない" in capsys.readouterr().err


def test_move_rejects_entry_without_comment(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    notes = _setup_notes(tmp_path)
    path = _write_awi_file(notes, "fb.md", body="本文")
    monkeypatch.setenv("AI_AGENT", "1")
    monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))
    original = path.read_bytes()

    assert _run(_move_args(tmp_path, _MOVED_BODY), tmp_path) == 1

    assert path.read_bytes() == original
    err = capsys.readouterr().err
    assert "atk wi show fb.md --section ユーザーコメント" in err
    assert "--move-user-commentを外して" not in err


_TRAILING_COMMENT = "強制改行の行  \n次の行"


def test_move_keeps_trailing_spaces_of_last_quoted_line(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = _prepare(tmp_path, monkeypatch, comment="指摘の末行  ")
    body = _QUOTE_HEAD + "> 指摘の末行  \n"

    assert _run(_move_args(tmp_path, body, read_comment="指摘の末行  "), tmp_path) == 0

    assert path.read_text(encoding="utf-8").endswith("> 指摘の末行  \n")


def test_move_rejects_trailing_spaces_added_to_last_quoted_line(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _prepare(tmp_path, monkeypatch, comment="指摘の末行")
    original = path.read_bytes()
    body = _QUOTE_HEAD + "> 指摘の末行  \n"

    assert _run(_move_args(tmp_path, body, read_comment="指摘の末行"), tmp_path) == 1

    assert path.read_bytes() == original


def test_move_rejects_trailing_spaces_dropped_from_last_quoted_line(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _prepare(tmp_path, monkeypatch, comment=_TRAILING_COMMENT)
    original = path.read_bytes()
    body = _QUOTE_HEAD + "> 強制改行の行\n> 次の行\n"

    assert _run(_move_args(tmp_path, body, read_comment=_TRAILING_COMMENT), tmp_path) == 1

    assert path.read_bytes() == original


def test_move_rejects_unreadable_expected_file(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = _prepare(tmp_path, monkeypatch)
    original = path.read_bytes()
    args = [*_edit_body_args(tmp_path, "fb.md", _MOVED_BODY), "--move-user-comment", str(tmp_path / "missing.md")]

    assert _run(args, tmp_path) == 1

    assert path.read_bytes() == original


def test_move_dry_run_validates_without_saving(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = _prepare(tmp_path, monkeypatch)
    original = path.read_bytes()

    assert _run([*_move_args(tmp_path, _MOVED_BODY), "--dry-run"], tmp_path) == 0
    assert path.read_bytes() == original

    assert _run([*_move_args(tmp_path, "整理後の本文"), "--dry-run"], tmp_path) == 1
    assert path.read_bytes() == original


@pytest.mark.parametrize("variant", ["append", "without-body-file"])
def test_move_rejects_invalid_combinations(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, variant: str) -> None:
    path = _prepare(tmp_path, monkeypatch)
    original = path.read_bytes()
    expected = tmp_path / "read-comment.md"
    expected.write_text(_COMMENT, encoding="utf-8")
    args = (
        [*_edit_body_args(tmp_path, "fb.md", _MOVED_BODY, append=True), "--move-user-comment", str(expected)]
        if variant == "append"
        else ["wi", "edit", "fb.md", "--move-user-comment", str(expected)]
    )

    assert _run(args, tmp_path) == 2

    assert path.read_bytes() == original


def test_edit_without_move_still_preserves_comment(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = _prepare(tmp_path, monkeypatch)

    assert _run(_edit_body_args(tmp_path, "fb.md", _MOVED_BODY), tmp_path) == 0

    assert path.read_text(encoding="utf-8").endswith(f"## ユーザーコメント\n\n{_COMMENT}\n")
