"""自動コードレビュー監査の判定済み記録を検証する。"""

import argparse
import json
import pathlib

import pytest

from _atk import review_audit


@pytest.fixture(name="record_path", autouse=True)
def _record_path(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> pathlib.Path:
    state_dir = tmp_path / "state"
    monkeypatch.setattr(review_audit._config, "state_dir", lambda: state_dir)  # pylint: disable=protected-access
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(pathlib.Path, "home", lambda: home)
    return state_dir / "review-audit.json"


def _dispatch(subcommand: str, repository: str, *identifiers: str) -> int:
    args = argparse.Namespace(review_audit_subcommand=subcommand, repo=repository, identifiers=list(identifiers))
    return review_audit.dispatch(args)


def test_list_returns_nothing_for_unrecorded_repository(capsys: pytest.CaptureFixture[str]) -> None:
    assert _dispatch("list", "owner/repo") == 0
    assert capsys.readouterr().out == ""


def test_list_outputs_recorded_ids_in_ascending_order(capsys: pytest.CaptureFixture[str]) -> None:
    assert _dispatch("mark", "owner/repo", "100", "9", "10") == 0
    capsys.readouterr()

    assert _dispatch("list", "owner/repo") == 0
    assert capsys.readouterr().out == "9\n10\n100\n"


def test_mark_records_ids_without_duplication(
    record_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert _dispatch("mark", "owner/repo", "9") == 0
    first_records = json.loads(record_path.read_text(encoding="utf-8"))
    capsys.readouterr()
    assert _dispatch("mark", "owner/repo", "9") == 0

    assert capsys.readouterr().out == "9\n"
    records = json.loads(record_path.read_text(encoding="utf-8"))
    assert records == first_records
    assert list(records["owner/repo"]) == ["9"]


def test_mark_keeps_other_repository_records(
    record_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert _dispatch("mark", "owner/first", "9") == 0
    capsys.readouterr()
    assert _dispatch("mark", "owner/second", "10") == 0

    records = json.loads(record_path.read_text(encoding="utf-8"))
    assert set(records) == {"owner/first", "owner/second"}
    assert set(records["owner/first"]) == {"9"}
    assert set(records["owner/second"]) == {"10"}


@pytest.mark.parametrize("repository", ("owner", "owner/repo/extra", ""))
def test_invalid_repository_is_rejected(repository: str) -> None:
    with pytest.raises(ValueError, match="<owner>/<repo>"):
        _dispatch("list", repository)


@pytest.mark.parametrize("identifier", ("0", "-1", "abc"))
def test_non_positive_id_is_rejected(identifier: str) -> None:
    with pytest.raises(ValueError, match="正の整数"):
        _dispatch("mark", "owner/repo", identifier)


@pytest.mark.parametrize("content", ("{broken", "[]"))
def test_broken_record_file_is_treated_as_empty(
    content: str,
    record_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    record_path.parent.mkdir(parents=True)
    record_path.write_text(content, encoding="utf-8")

    assert _dispatch("list", "owner/repo") == 0
    assert capsys.readouterr().out == ""
    assert _dispatch("mark", "owner/repo", "9") == 0
    assert capsys.readouterr().out == "9\n"
    assert set(json.loads(record_path.read_text(encoding="utf-8"))["owner/repo"]) == {"9"}
