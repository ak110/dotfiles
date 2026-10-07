"""計画バンドルの所有記録（`_plan/owner_records.py`）を検証する。"""

import pathlib

import pytest

from agent_toolkit._plan import owner_records as _owner_records


@pytest.fixture
def _owner_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """所有セッションの解決に使う環境変数を未設定の状態から始める。"""
    monkeypatch.delenv("AGENT_TOOLKIT_OWNER_SESSION", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)


@pytest.mark.usefixtures("_owner_environment")
def test_owner_session_prefers_delegating_session_over_own_session(monkeypatch: pytest.MonkeyPatch) -> None:
    """委譲元から渡された識別子を、実行中セッション自身の識別子より優先する。"""
    monkeypatch.setenv("AGENT_TOOLKIT_OWNER_SESSION", "owner")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "delegate")

    assert _owner_records.resolve_owner_session_id() == "owner"


@pytest.mark.usefixtures("_owner_environment")
def test_owner_session_falls_back_to_own_session_when_delegating_value_is_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """委譲元の識別子が空文字列の場合は実行中セッション自身の識別子を使う。"""
    monkeypatch.setenv("AGENT_TOOLKIT_OWNER_SESSION", "")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "own")

    assert _owner_records.resolve_owner_session_id() == "own"


@pytest.mark.usefixtures("_owner_environment")
def test_owner_session_is_unresolved_without_any_session_identifier() -> None:
    """いずれの識別子も無い環境では所有セッションを解決しない。"""
    assert _owner_records.resolve_owner_session_id() is None


def test_owner_record_round_trips_session_identifier(tmp_path: pathlib.Path) -> None:
    """出力した所有記録から同じセッション識別子を取得できる。"""
    main = tmp_path / "30-所有記録-a1b2.md"
    main.write_text("# main\n", encoding="utf-8")

    written = _owner_records.write_owner_record(main, session_id="session-a")

    assert written == tmp_path / "30-所有記録-a1b2.owner.json"
    assert _owner_records.read_owner_session_id(main) == "session-a"
    _owner_records.remove_owner_record(main)
    assert not written.exists()
    assert _owner_records.read_owner_session_id(main) is None


@pytest.mark.parametrize("record", ["{不正なJSON", '{"recorded_at": "2026-09-03T00:00:00+09:00"}', '{"session_id": ""}', "[]"])
def test_owner_record_without_usable_session_identifier_is_unresolved(tmp_path: pathlib.Path, record: str) -> None:
    """JSONとして解釈できない記録と`session_id`が無い記録は所有を確定しない。"""
    main = tmp_path / "30-不正記録-a1b2.md"
    main.write_text("# main\n", encoding="utf-8")
    _owner_records.owner_record_path(main).write_text(record, encoding="utf-8")

    assert _owner_records.read_owner_session_id(main) is None


@pytest.mark.usefixtures("_owner_environment")
def test_record_plan_owner_writes_nothing_without_session_identifier(tmp_path: pathlib.Path) -> None:
    """所有セッションを解決できない場合は記録を書かない。"""
    main = tmp_path / "30-記録なし-a1b2.md"
    main.write_text("# main\n", encoding="utf-8")

    assert _owner_records.record_plan_owner(main) is None
    assert not _owner_records.owner_record_path(main).exists()


@pytest.mark.usefixtures("_owner_environment")
@pytest.mark.parametrize(
    ("owner", "own", "expected"),
    [
        ("mcp-0123456789abcdef0123456789abcdef", "delegate-session", None),
        ("conversation-session", "delegate-session", "conversation-session"),
        (None, "own-session", "own-session"),
    ],
    ids=["process-root", "conversation", "own-session"],
)
def test_record_plan_owner_excludes_process_root(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, owner: str | None, own: str, expected: str | None
) -> None:
    """`agents_server`のプロセス専用のrootは会話へ対応しないため所有記録を書かず、実在する会話の識別子は記録する。

    プロセス専用のrootを記録すると、計画の一覧が会話として存在しない所有者を表示する。
    委譲先自身の識別子は委譲元の会話を表さないため、プロセス専用のrootを除いた後もそちらへ戻らない。
    """
    if owner is not None:
        monkeypatch.setenv("AGENT_TOOLKIT_OWNER_SESSION", owner)
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", own)
    main = tmp_path / "30-所有判定-a1b2.md"
    main.write_text("# main\n", encoding="utf-8")

    written = _owner_records.record_plan_owner(main)

    assert _owner_records.resolve_conversation_session_id() == expected
    assert (written is None) is (expected is None)
    assert _owner_records.read_owner_session_id(main) == expected
