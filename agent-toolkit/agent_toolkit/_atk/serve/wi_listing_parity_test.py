"""`atk wi list`とWI画面の一覧APIが同じ限定条件で同じ項目集合を返すことのテスト。"""

import json
import pathlib

import pytest

from agent_toolkit import atk
from agent_toolkit._atk.serve import wi_operations
from agent_toolkit._atk.wi import entries as _wi_entries
from agent_toolkit._atk.wi import sync as _wi_sync

_REPO = "github.com/example/repo"
_OTHER_REPO = "github.com/example/other"
_UNRESOLVABLE_REPO = "/missing/legacy-repo"
"""正規化できない保存値。実在しないローカルパスは`origin`を取得できないため識別子へ解決されない。"""


def _write(
    notes: pathlib.Path, state: str, filename: str, *, kind: str, target_repo: str, source: str | None, answered: bool
) -> None:
    """限定条件の各値を持つ項目を書く。"""
    lines = ["---", f"type: {kind}", f"target_repo: {target_repo}"]
    if source is not None:
        lines.append(f"source: {source}")
    if kind == "uwi":
        lines.append("question_type: yes-no")
    lines.extend(["---", ""])
    if kind == "uwi":
        answer = "はい\n" if answered else ""
        lines.extend(["## 質問", "", "続けるか", "", "## 回答", "", "<!-- ユーザーはこの行以降に回答を追記する -->", answer])
    else:
        lines.extend(["本文", ""])
    (notes / state).mkdir(parents=True, exist_ok=True)
    (notes / state / filename).write_text("\n".join(lines), encoding="utf-8")


@pytest.fixture(name="notes")
def _notes(tmp_path: pathlib.Path) -> pathlib.Path:
    """状態・種別・回答状況・source・target_repoの組を網羅する項目を持つprivate-notesを返す。"""
    notes = tmp_path / "private-notes"
    _write(notes, "inbox", "20260101-000000-001.md", kind="awi", target_repo=_REPO, source="session-review", answered=False)
    _write(notes, "inbox", "20260101-000000-002.md", kind="awi", target_repo=_OTHER_REPO, source=None, answered=False)
    _write(notes, "processing", "20260101-000000-003.md", kind="uwi", target_repo=_REPO, source=None, answered=True)
    _write(notes, "hold", "20260101-000000-004.md", kind="uwi", target_repo=_REPO, source="session-review", answered=False)
    _write(notes, "adopted", "20260101-000000-005.md", kind="awi", target_repo=_REPO, source="session-review", answered=False)
    _write(notes, "rejected", "20260101-000000-006.md", kind="uwi", target_repo=_OTHER_REPO, source=None, answered=True)
    _write(notes, "inbox", "20260101-000000-007.md", kind="awi", target_repo=_UNRESOLVABLE_REPO, source=None, answered=False)
    return notes


def _cli_filenames(
    notes: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    filters: dict[str, str],
) -> set[str]:
    """`atk wi list`のJSON Linesの出力から項目のファイル名の集合を返す。"""
    monkeypatch.setattr(_wi_sync, "ensure_environment", lambda _home: notes)
    argv = ["wi", "list", "--skip-pull", "--jsonl", f"--state={filters['status']}", f"--answered={filters['answered']}"]
    if "source" in filters:
        argv.append(f"--source={filters['source']}")
    argv.append(f"--target-repo={filters.get('target_repo', 'all')}")
    capsys.readouterr()
    with pytest.raises(SystemExit) as exc_info:
        atk.main(argv, home=notes.parent)
    assert exc_info.value.code == 0
    return {json.loads(line)["filename"] for line in capsys.readouterr().out.splitlines() if line}


def _web_filenames(notes: pathlib.Path, filters: dict[str, str]) -> set[str]:
    """WI画面の一覧APIが返す項目のファイル名の集合を返す。"""
    entries, warnings = wi_operations.Operations(notes).entries_with_warnings({"type": "all", **filters})
    assert not warnings
    return {str(entry["filename"]) for entry in entries}


_FILTERS = [
    {"status": status, "answered": answered, **extra}
    for status in ("active", "processable", "all", "hold")
    for answered in ("all", "yes", "no")
    for extra in (
        {},
        {"source": "session-review"},
        {"source": "!session-review"},
        {"target_repo": _REPO},
        {"target_repo": _OTHER_REPO, "source": "!session-review"},
    )
]


@pytest.mark.parametrize("filters", _FILTERS, ids=lambda filters: ",".join(f"{k}={v}" for k, v in filters.items()))
def test_cli_and_web_list_return_same_entries(
    notes: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    filters: dict[str, str],
) -> None:
    """状態（単一値と集合の指定）、回答状況、source（否定指定を含む）、target_repoの組で項目集合が一致する。"""
    assert _cli_filenames(notes, monkeypatch, capsys, filters) == _web_filenames(notes, filters)


@pytest.mark.parametrize("status", ["active", "all"])
def test_unresolvable_target_repo_matches_stored_value_in_cli_selection_and_web(notes: pathlib.Path, status: str) -> None:
    """正規化できない`target_repo`の指定値は、CLIの一覧の選択とWI画面の双方で保存値と原値のまま比べる。

    CLIは`--target-repo`の値を識別子へ解決してから選択へ渡すため、この値はCLIの引数からは届かない。
    選択の実装が画面と同じ規則であることを、`atk wi list`が使う選択の関数で確かめる。
    """
    selected = _wi_entries.select_entries(
        notes, status=[status], target_repo=[_UNRESOLVABLE_REPO], entry_type=["all"], answered=["all"], source=None
    )
    web = _web_filenames(notes, {"status": status, "answered": "all", "target_repo": _UNRESOLVABLE_REPO})
    assert {entry[0].name for entry in selected} == web == {"20260101-000000-007.md"}
