"""`_uwi_completion.py`のTBD回答ファイル差分通知を検証する。"""

import json
import pathlib
import subprocess
import tempfile

import _uwi_completion
import _uwi_scan
import pytest
from _test_helpers import SESSION_STATE_FILENAME_TEMPLATE

_REPO = "github.com/ak110/dotfiles"


def _entry(*, answer: str = "", target_repo: str = _REPO) -> str:
    """テスト用TBDエントリ本文を返す。"""
    return (
        "---\n"
        f"target_repo: {target_repo}\n"
        "type: tbd\n"
        "---\n\n"
        "## 質問\n\n本文\n\n"
        "## 回答\n\n"
        "<!-- ユーザーはこの行以降に回答を追記する -->\n"
        f"{answer}"
    )


def _make_private_notes(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    unanswered: int,
    answered: int,
    other_repo: int = 0,
) -> pathlib.Path:
    """一時保存先へ指定件数のTBDを配置する。"""
    root = tmp_path / "private-notes"
    inbox = root / "inbox"
    processing = root / "processing"
    inbox.mkdir(parents=True)
    processing.mkdir()
    for index in range(unanswered):
        (inbox / f"unanswered-{index}.md").write_text(_entry(), encoding="utf-8")
    for index in range(answered):
        (processing / f"answered-{index}.md").write_text(_entry(answer="回答\n"), encoding="utf-8")
    for index in range(other_repo):
        (inbox / f"other-{index}.md").write_text(_entry(target_repo="example.com/x/y"), encoding="utf-8")
    monkeypatch.setenv("AGENT_TOOLKIT_PRIVATE_NOTES", str(root))
    monkeypatch.setattr(_uwi_completion, "resolve_target_repo", lambda _cwd: _REPO)
    return root


def _answer_all(root: pathlib.Path) -> None:
    """未回答TBDの回答欄へ回答本文を追記する。"""
    for path in (root / "inbox").glob("unanswered-*.md"):
        path.write_text(path.read_text(encoding="utf-8") + "回答\n", encoding="utf-8")


@pytest.fixture(autouse=True)
def _isolate_state(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """セッション状態ファイルをテスト用一時ディレクトリへ隔離する。"""
    for name in ("TMPDIR", "TEMP", "TMP"):
        monkeypatch.setenv(name, str(tmp_path))
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))


class TestBuildNotice:
    """初回観測後に新しく回答されたファイル名だけを通知する。"""

    def test_initial_observation_only_records_answered_filenames(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _make_private_notes(tmp_path, monkeypatch, unanswered=2, answered=0)
        assert _uwi_completion.build_notice("initial", "/dummy") is None
        state_path = tmp_path / SESSION_STATE_FILENAME_TEMPLATE.format(session_id="initial")
        state = json.loads(state_path.read_text(encoding="utf-8"))
        assert state[_uwi_completion.STATE_KEY_ANSWERED] == {"main": {_REPO: []}}

    def test_notifies_new_answer_while_another_tbd_remains_unanswered(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        root = _make_private_notes(tmp_path, monkeypatch, unanswered=2, answered=0)
        assert _uwi_completion.build_notice("new-answer", "/dummy") is None
        path = root / "inbox/unanswered-0.md"
        path.write_text(path.read_text(encoding="utf-8") + "回答\n", encoding="utf-8")

        notice = _uwi_completion.build_notice("new-answer", "/dummy")

        assert notice is not None
        assert _REPO in notice
        assert "unanswered-0.md" in notice
        assert "unanswered-1.md" not in notice

    def test_initial_answered_state_does_not_notify(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _make_private_notes(tmp_path, monkeypatch, unanswered=0, answered=1)
        assert _uwi_completion.build_notice("stable-zero", "/dummy") is None
        assert _uwi_completion.build_notice("stable-zero", "/dummy") is None

    def test_notifies_multiple_new_answers_together(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        root = _make_private_notes(tmp_path, monkeypatch, unanswered=2, answered=0)
        assert _uwi_completion.build_notice("multiple", "/dummy") is None
        _answer_all(root)

        notice = _uwi_completion.build_notice("multiple", "/dummy")

        assert notice is not None
        assert "unanswered-0.md" in notice
        assert "unanswered-1.md" in notice

    def test_answer_cancellation_does_not_notify_and_reanswer_does(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        root = _make_private_notes(tmp_path, monkeypatch, unanswered=0, answered=1)
        assert _uwi_completion.build_notice("cancel", "/dummy") is None
        path = root / "processing/answered-0.md"
        path.write_text(_entry(), encoding="utf-8")
        assert _uwi_completion.build_notice("cancel", "/dummy") is None
        path.write_text(_entry(answer="再回答\n"), encoding="utf-8")
        notice = _uwi_completion.build_notice("cancel", "/dummy")
        assert notice is not None
        assert "answered-0.md" in notice

    def test_same_fingerprint_skips_rescan(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _make_private_notes(tmp_path, monkeypatch, unanswered=1, answered=0)
        assert _uwi_completion.build_notice("same-state", "/dummy") is None
        monkeypatch.setattr(
            _uwi_scan,
            "scan_active_uwis",
            lambda *_args: pytest.fail("指紋が同じ状態で再走査された"),
        )
        assert _uwi_completion.build_notice("same-state", "/dummy") is None

    def test_ignores_other_repositories(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _make_private_notes(tmp_path, monkeypatch, unanswered=1, answered=0, other_repo=2)
        assert _uwi_completion.build_notice("other", "/dummy") is None
        state_path = tmp_path / SESSION_STATE_FILENAME_TEMPLATE.format(session_id="other")
        state = json.loads(state_path.read_text(encoding="utf-8"))
        assert state[_uwi_completion.STATE_KEY_ANSWERED] == {"main": {_REPO: []}}

    def test_missing_root_does_not_write_state(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(_uwi_scan, "private_notes_root", lambda: None)
        assert _uwi_completion.build_notice("missing-root", "/dummy") is None
        assert not (tmp_path / SESSION_STATE_FILENAME_TEMPLATE.format(session_id="missing-root")).exists()

    def test_unresolved_repository_does_not_write_state(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _make_private_notes(tmp_path, monkeypatch, unanswered=1, answered=0)
        monkeypatch.setattr(_uwi_completion, "resolve_target_repo", lambda _cwd: None)
        assert _uwi_completion.build_notice("missing-repo", "/dummy") is None
        assert not (tmp_path / SESSION_STATE_FILENAME_TEMPLATE.format(session_id="missing-repo")).exists()

    def test_incomplete_scan_does_not_update_state_or_notify(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _make_private_notes(tmp_path, monkeypatch, unanswered=0, answered=1)
        monkeypatch.setattr(
            _uwi_scan,
            "scan_active_uwis",
            lambda _root, _repo: _uwi_scan.ActiveUwiScan([_uwi_scan.ActiveUwi("answered.md", True)], False),
        )
        assert _uwi_completion.build_notice("incomplete", "/dummy") is None
        assert not (tmp_path / SESSION_STATE_FILENAME_TEMPLATE.format(session_id="incomplete")).exists()

    def test_state_write_failure_suppresses_notification(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """新規回答があっても通知状態を保存できなければ通知しない。

        保存できないまま通知すると、同じ回答を次回以降も繰り返し通知する。
        指紋照合を無効化して`update_state`の呼び出しを回答記録の1回に限定し、
        呼び出し順に依存せず書き込み失敗だけを模擬する。
        """
        root = _make_private_notes(tmp_path, monkeypatch, unanswered=1, answered=0)
        assert _uwi_completion.build_notice("write-failure", "/dummy") is None
        _answer_all(root)
        monkeypatch.setattr(_uwi_scan, "active_fingerprint", lambda _root: None)
        monkeypatch.setattr(_uwi_completion, "update_state", lambda _session_id, _mutator: False)
        assert _uwi_completion.build_notice("write-failure", "/dummy") is None

    def test_missing_cwd_does_not_notify(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """作業ディレクトリを取得できないpayloadでは走査も状態記録も行わない。"""
        _make_private_notes(tmp_path, monkeypatch, unanswered=1, answered=0)
        assert _uwi_completion.build_notice("missing-cwd", "") is None
        assert not (tmp_path / SESSION_STATE_FILENAME_TEMPLATE.format(session_id="missing-cwd")).exists()

    def test_subagent_call_does_not_consume_main_transition(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """サブエージェントの呼び出しがメインへの回答差分通知を消費しない。

        メインとサブエージェントのフック呼び出しは同一`session_id`で届くため、
        差分の判定をエージェント識別子で分けないと先に観測した側だけが通知を受け取る。
        """
        root = _make_private_notes(tmp_path, monkeypatch, unanswered=1, answered=0)
        assert _uwi_completion.build_notice("shared", "/dummy") is None
        assert _uwi_completion.build_notice("shared", "/dummy", "abc123") is None
        _answer_all(root)
        assert _uwi_completion.build_notice("shared", "/dummy", "abc123") is not None
        assert _uwi_completion.build_notice("shared", "/dummy") is not None


class TestResolveTargetRepo:
    """Git実行またはURL正規化に失敗した場合は解決不能として扱う。"""

    @pytest.mark.parametrize(
        "completed",
        [
            subprocess.CompletedProcess([], 1, stdout="", stderr="error"),
            subprocess.CompletedProcess([], 0, stdout="invalid", stderr=""),
        ],
    )
    def test_returns_none_for_invalid_result(
        self, completed: subprocess.CompletedProcess[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _run(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
            return completed

        monkeypatch.setattr(subprocess, "run", _run)
        assert _uwi_completion.resolve_target_repo("/dummy") is None

    @pytest.mark.parametrize("error", [OSError("git missing"), subprocess.TimeoutExpired("git", 5)])
    def test_returns_none_for_execution_errors(self, error: Exception, monkeypatch: pytest.MonkeyPatch) -> None:
        def _run(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
            raise error

        monkeypatch.setattr(subprocess, "run", _run)
        assert _uwi_completion.resolve_target_repo("/dummy") is None

    def test_normalizes_origin(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def _run(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess([], 0, stdout="git@github.com:ak110/dotfiles.git\n", stderr="")

        monkeypatch.setattr(subprocess, "run", _run)
        assert _uwi_completion.resolve_target_repo("/dummy") == _REPO
