"""`uwi_completion.py`が、会話の投入したUWIへ付いた回答だけを通知することを検証する。"""

import pathlib

import pytest

from agent_toolkit._atk.wi import uwi_scan as _uwi_scan
from agent_toolkit._common import session_state, uwi_watch
from agent_toolkit._hooks import uwi_completion as _uwi_completion

_REPO = "github.com/ak110/dotfiles"
_SESSION = "self-session"


def _name(index: int) -> str:
    return f"20261101-000000-{index:03d}.md"


def _put(
    root: pathlib.Path,
    index: int,
    *,
    answer: str = "",
    state: str = "inbox",
    target_repo: str = _REPO,
    submitter: str = _SESSION,
) -> str:
    name = _name(index)
    path = root / "2026" / "11" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "---\n"
        f"state: {state}\n"
        f"target_repo: {target_repo}\n"
        "type: uwi\n"
        f"submitter_session: {submitter}\n"
        "---\n\n"
        "## 質問\n\n本文\n\n"
        "## 回答\n\n"
        "<!-- ユーザーはこの行以降に回答を追記する -->\n"
        f"{answer}",
        encoding="utf-8",
    )
    return name


def _awaiting() -> list[str]:
    return uwi_watch.awaiting(session_state.read_state(_SESSION))


@pytest.fixture(name="root", autouse=True)
def _root(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> pathlib.Path:
    root = tmp_path / "private-notes"
    root.mkdir()
    monkeypatch.setenv("AGENT_TOOLKIT_PRIVATE_NOTES", str(root))
    return root


class TestBuildNotice:
    def test_session_without_record_does_not_read_private_notes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def _fail(*_args: object, **_kwargs: object) -> None:
            pytest.fail("記録の無い会話がWIの保存先を参照した")

        monkeypatch.setattr(_uwi_scan, "private_notes_root", _fail)
        assert _uwi_completion.build_notice(_SESSION) is None

    def test_notifies_answered_uwi_once_and_keeps_unanswered(self, root: pathlib.Path) -> None:
        answered = _put(root, 1, answer="回答\n", state="processing")
        unanswered = _put(root, 2)

        foreign = _put(root, 3, answer="回答\n", submitter="other-session")
        assert uwi_watch.add(_SESSION, [answered, unanswered])

        notice = _uwi_completion.build_notice(_SESSION)

        assert notice is not None
        assert _REPO in notice
        assert answered in notice
        assert unanswered not in notice
        assert foreign not in notice

        assert "`agent-toolkit:atk-mode/references/answer-transitions.md`「回答後の状態遷移」" in notice
        assert _awaiting() == [unanswered]
        assert _uwi_completion.build_notice(_SESSION) is None

    def test_notifies_answers_of_every_target_repository(self, root: pathlib.Path) -> None:
        first = _put(root, 1, answer="回答\n")
        second = _put(root, 2, answer="回答\n", target_repo="example.com/x/y")
        assert uwi_watch.add(_SESSION, [first, second])

        notice = _uwi_completion.build_notice(_SESSION)

        assert notice is not None
        for target_repo, name in ((_REPO, first), ("example.com/x/y", second)):
            assert f"{target_repo}に新たに回答されたUWIがある: {name}。" in notice
        assert not _awaiting()

    @pytest.mark.parametrize("state", ["adopted", "rejected"])
    def test_finished_uwi_leaves_record_without_notice(self, root: pathlib.Path, state: str) -> None:
        assert uwi_watch.add(_SESSION, [_put(root, 1, answer="回答\n", state=state)])

        assert _uwi_completion.build_notice(_SESSION) is None
        assert not _awaiting()

    @pytest.mark.parametrize("case", ["hold", "missing", "broken"])
    def test_unsettled_uwi_stays_in_record(self, root: pathlib.Path, case: str) -> None:
        """保留中のUWIと、同期の途中などで読めないUWIは、後の呼び出しで確かめ直せるよう記録へ残す。"""
        name = _name(1)
        if case == "hold":
            _put(root, 1, answer="回答\n", state="hold")
        elif case == "broken":
            path = root / "2026" / "11" / name
            path.parent.mkdir(parents=True)
            path.write_text("frontmatterの無い本文\n", encoding="utf-8")
        assert uwi_watch.add(_SESSION, [name])

        assert _uwi_completion.build_notice(_SESSION) is None
        assert _awaiting() == [name]

    def test_does_not_notify_when_record_cannot_be_updated(self, root: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        assert uwi_watch.add(_SESSION, [_put(root, 1, answer="回答\n")])

        def _update_state(*_args: object, **_kwargs: object) -> bool:
            return False

        monkeypatch.setattr(_uwi_completion, "update_state", _update_state)
        assert _uwi_completion.build_notice(_SESSION) is None


class TestAdoptScanState:
    @pytest.mark.parametrize("key", ["uwi_answered_by_repo", "tbd_answered_by_repo"])
    def test_adopts_own_pending_uwis_and_notifies_unnotified_answer(self, root: pathlib.Path, key: str) -> None:
        waiting = _put(root, 1)
        unnotified = _put(root, 2, answer="回答\n")
        notified = _put(root, 3, answer="回答\n")
        foreign = _put(root, 4, submitter="other-session")

        unobserved = _put(root, 5, target_repo="example.com/x/y")
        recorded = _put(root, 6)
        fingerprint_key = key.replace("answered", "fingerprint")
        legacy = {key: {"main": {_REPO: [notified]}}, fingerprint_key: {"main": {"/cwd": "digest"}}}
        assert session_state.update_state(_SESSION, lambda _state: legacy)
        assert uwi_watch.add(_SESSION, [recorded])

        notice = _uwi_completion.build_notice(_SESSION)

        assert notice is not None
        assert unnotified in notice
        for name in (waiting, notified, foreign, unobserved, recorded):
            assert name not in notice
        state = session_state.read_state(_SESSION)
        assert key not in state
        assert fingerprint_key not in state
        assert uwi_watch.awaiting(state) == [waiting, recorded]

    @pytest.mark.parametrize("failure", ["unresolved-root", "unreadable-entry"])
    def test_keeps_scan_state_until_scan_completes(
        self, root: pathlib.Path, monkeypatch: pytest.MonkeyPatch, failure: str
    ) -> None:
        unnotified = _put(root, 1, answer="回答\n")
        broken = root / "2026" / "11" / _name(9)
        legacy: dict[str, object] = {"uwi_answered_by_repo": {"main": {_REPO: []}}}
        assert session_state.update_state(_SESSION, lambda _state: legacy)
        monkeypatch.setattr(_uwi_completion, "_ADOPT_RETRY_SECONDS", 0.0)

        with monkeypatch.context() as patch:
            if failure == "unresolved-root":
                patch.setattr(_uwi_scan, "private_notes_root", lambda: None)
            else:
                broken.write_text("frontmatterの無い本文\n", encoding="utf-8")
            assert _uwi_completion.build_notice(_SESSION) is None
        state = session_state.read_state(_SESSION)
        assert state["uwi_answered_by_repo"] == {"main": {_REPO: []}}
        assert uwi_watch.awaiting(state) == []

        broken.unlink(missing_ok=True)
        notice = _uwi_completion.build_notice(_SESSION)

        assert notice is not None and unnotified in notice
        state = session_state.read_state(_SESSION)
        assert "uwi_answered_by_repo" not in state and "uwi_adopt_retry_at" not in state

    def test_does_not_renotify_answer_notified_while_adoption_is_pending(
        self, root: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        recorded = _put(root, 1, answer="回答\n")
        broken = root / "2026" / "11" / _name(9)
        broken.write_text("frontmatterの無い本文\n", encoding="utf-8")
        assert session_state.update_state(_SESSION, lambda _state: {"uwi_answered_by_repo": {"main": {_REPO: []}}})
        assert uwi_watch.add(_SESSION, [recorded])
        monkeypatch.setattr(_uwi_completion, "_ADOPT_RETRY_SECONDS", 0.0)

        notice = _uwi_completion.build_notice(_SESSION)
        assert notice is not None and recorded in notice
        broken.unlink()

        assert _uwi_completion.build_notice(_SESSION) is None
        state = session_state.read_state(_SESSION)
        assert "uwi_answered_by_repo" not in state
        assert not uwi_watch.awaiting(state)

    def test_waits_before_scanning_again_after_incomplete_scan(
        self, root: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        (root / "2026" / "11").mkdir(parents=True)
        (root / "2026" / "11" / _name(9)).write_text("frontmatterの無い本文\n", encoding="utf-8")
        recorded = _put(root, 1, answer="回答\n")
        assert session_state.update_state(_SESSION, lambda _state: {"uwi_answered_by_repo": {"main": {_REPO: []}}})
        assert _uwi_completion.build_notice(_SESSION) is None
        assert uwi_watch.add(_SESSION, [recorded])

        def _fail(*_args: object, **_kwargs: object) -> None:
            raise AssertionError("やり直しの時刻より前に全WIを走査した")

        monkeypatch.setattr(_uwi_scan, "scan_active_uwis", _fail)
        notice = _uwi_completion.build_notice(_SESSION)

        assert notice is not None and recorded in notice
        assert "uwi_answered_by_repo" in session_state.read_state(_SESSION)
