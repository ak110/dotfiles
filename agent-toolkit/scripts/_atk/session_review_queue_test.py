"""前のセッションの振り返り待ちを記録するサブコマンドを検証する。"""

from __future__ import annotations

import argparse
import json
import pathlib

import pytest

from _atk import session_review_queue as queue

_REPOSITORY = "github.com/ak110/dotfiles"


def _prepare(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> pathlib.Path:
    record_path = tmp_path / "state" / "session-review-queue.json"
    monkeypatch.setattr(queue, "_record_path", lambda: record_path)
    monkeypatch.setattr(queue, "_lock_path", lambda: tmp_path / "locks" / "session-review-queue.lock")
    monkeypatch.setattr(queue, "resolve_repo_id", lambda value: _REPOSITORY if value is None else value)
    return record_path


def _args(**values: object) -> argparse.Namespace:
    defaults: dict[str, object] = {
        "session_review_queue_subcommand": "claim",
        "target_repo": None,
        "transcript": None,
        "codex_thread_id": None,
        "session_ids": None,
    }
    defaults.update(values)
    return argparse.Namespace(**defaults)


def _output(capsys: pytest.CaptureFixture[str]) -> list[dict[str, str]]:
    return [json.loads(line) for line in capsys.readouterr().out.splitlines()]


def test_claim_returns_prior_entries_then_registers_itself(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """claimは自身を返さず、登録日時順で返した後の記録を新規読込みで取得できる。"""
    record_path = _prepare(monkeypatch, tmp_path)
    record_path.parent.mkdir()
    record_path.write_text(
        json.dumps(
            {
                _REPOSITORY: {
                    "later": {"engine": "codex", "registered_at": "2026-09-07T02:00:00+00:00"},
                    "earlier": {"engine": "claude", "registered_at": "2026-09-07T01:00:00+00:00"},
                }
            }
        ),
        encoding="utf-8",
    )

    assert queue.dispatch(_args(codex_thread_id="current")) == 0

    assert _output(capsys) == [
        {"engine": "claude", "session_id": "earlier", "registered_at": "2026-09-07T01:00:00+00:00"},
        {"engine": "codex", "session_id": "later", "registered_at": "2026-09-07T02:00:00+00:00"},
    ]
    stored = json.loads(record_path.read_text(encoding="utf-8"))
    assert set(stored[_REPOSITORY]) == {"earlier", "later", "current"}, "claim後の記録を新規読込みで取得できない"


def test_claim_preserves_existing_engine_and_timestamp(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """同じ識別子の再登録は最初のengineとregistered_atを保持して自身を返さない。"""
    record_path = _prepare(monkeypatch, tmp_path)
    record_path.parent.mkdir()
    existing = {"engine": "claude", "registered_at": "2026-09-07T01:00:00+00:00"}
    record_path.write_text(json.dumps({_REPOSITORY: {"same": existing}}), encoding="utf-8")

    assert queue.dispatch(_args(codex_thread_id="same")) == 0

    assert _output(capsys) == []
    stored = json.loads(record_path.read_text(encoding="utf-8"))
    assert stored[_REPOSITORY]["same"] == existing


@pytest.mark.parametrize("initial", ["not-json", "[]"])
def test_claim_treats_invalid_record_as_empty(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    initial: str,
) -> None:
    """解釈不能又は最上位が辞書でない記録は空として自身だけを登録する。"""
    record_path = _prepare(monkeypatch, tmp_path)
    record_path.parent.mkdir()
    record_path.write_text(initial, encoding="utf-8")

    assert queue.dispatch(_args(transcript="/records/claude-session.jsonl")) == 0

    assert _output(capsys) == []
    stored = json.loads(record_path.read_text(encoding="utf-8"))
    assert stored[_REPOSITORY]["claude-session"]["engine"] == "claude"


def test_done_removes_only_named_entries_and_is_idempotent(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """doneは指定外を残し、存在しない識別子も正常終了する。"""
    record_path = _prepare(monkeypatch, tmp_path)
    record_path.parent.mkdir()
    remaining = {"engine": "codex", "registered_at": "2026-09-07T02:00:00+00:00"}
    record_path.write_text(
        json.dumps(
            {
                _REPOSITORY: {
                    "remove": {"engine": "claude", "registered_at": "2026-09-07T01:00:00+00:00"},
                    "keep": remaining,
                }
            }
        ),
        encoding="utf-8",
    )

    arguments = _args(session_review_queue_subcommand="done", session_ids=["remove", "missing"])
    assert queue.dispatch(arguments) == 0
    assert _output(capsys) == [{"engine": "codex", "session_id": "keep", "registered_at": remaining["registered_at"]}]
    assert queue.dispatch(arguments) == 0
    assert _output(capsys) == [{"engine": "codex", "session_id": "keep", "registered_at": remaining["registered_at"]}]


@pytest.mark.parametrize(
    "initial",
    [
        None,
        '{"github.com/ak110/other": {"other": {"engine": "codex", "registered_at": "2026-09-07T01:00:00+00:00"}}}',
    ],
)
def test_done_does_not_write_when_session_id_is_unregistered(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    initial: str | None,
) -> None:
    """未登録の対象リポジトリと識別子に対するdoneは記録を変更しない。"""
    record_path = _prepare(monkeypatch, tmp_path)
    if initial is not None:
        record_path.parent.mkdir()
        record_path.write_text(initial, encoding="utf-8")

    assert queue.dispatch(_args(session_review_queue_subcommand="done", session_ids=["missing"])) == 0

    assert _output(capsys) == []
    if initial is None:
        assert not record_path.exists()
    else:
        assert record_path.read_text(encoding="utf-8") == initial


def test_claim_isolates_entries_by_target_repository(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """claimは対象リポジトリごとの未処理エントリーだけを返す。"""
    record_path = _prepare(monkeypatch, tmp_path)
    first_repository = "github.com/ak110/first"
    second_repository = "github.com/ak110/second"

    assert queue.dispatch(_args(target_repo=first_repository, codex_thread_id="first")) == 0
    assert _output(capsys) == []
    assert queue.dispatch(_args(target_repo=second_repository, codex_thread_id="second")) == 0
    assert _output(capsys) == []
    stored = json.loads(record_path.read_text(encoding="utf-8"))

    assert queue.dispatch(_args(target_repo=first_repository, codex_thread_id="first-current")) == 0
    assert _output(capsys) == [
        {
            "engine": "codex",
            "session_id": "first",
            "registered_at": stored[first_repository]["first"]["registered_at"],
        }
    ]
    assert queue.dispatch(_args(target_repo=second_repository, codex_thread_id="second-current")) == 0
    assert _output(capsys) == [
        {
            "engine": "codex",
            "session_id": "second",
            "registered_at": stored[second_repository]["second"]["registered_at"],
        }
    ]


def test_writes_records_with_fsync(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """振り返り待ちの記録はfsyncを有効にして永続化する。"""
    record_path = _prepare(monkeypatch, tmp_path)
    observed: list[tuple[pathlib.Path, bool]] = []

    def write(path: pathlib.Path, content: str, *, fsync: bool = False) -> None:
        observed.append((path, fsync))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    monkeypatch.setattr(queue, "atomic_write", write)

    assert queue.dispatch(_args(codex_thread_id="thread")) == 0

    assert observed == [(record_path, True)]


@pytest.mark.parametrize("value", ["", "nested/id", "nested\\id"])
def test_session_id_type_rejects_empty_and_path_separators(value: str) -> None:
    """セッション識別子の空値とパス区切り文字を終了コード2相当で拒否する。"""
    with pytest.raises(argparse.ArgumentTypeError):
        queue._session_id(value)  # pylint: disable=protected-access
