"""`_tbd_completion.py`のTBD全件回答済み遷移を検証する。"""

import json
import pathlib
import subprocess
import tempfile

import _tbd_completion
import _tbd_scan
import pytest

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
    monkeypatch.setattr(_tbd_completion, "resolve_target_repo", lambda _cwd: _REPO)
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
    """未回答件数の1件以上から0件への遷移だけを通知する。"""

    def test_initial_observation_only_records_count(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _make_private_notes(tmp_path, monkeypatch, unanswered=2, answered=0)
        assert _tbd_completion.build_notice("initial", "/dummy") is None
        state = json.loads((tmp_path / "claude-agent-toolkit-initial.json").read_text(encoding="utf-8"))
        assert state[_tbd_completion.STATE_KEY] == {_REPO: 2}

    def test_notifies_on_transition_to_zero(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        root = _make_private_notes(tmp_path, monkeypatch, unanswered=1, answered=0)
        assert _tbd_completion.build_notice("transition", "/dummy") is None
        _answer_all(root)
        notice = _tbd_completion.build_notice("transition", "/dummy")
        assert notice is not None
        assert _REPO in notice
        assert "answered: 1" in notice
        assert "unanswered-0.md" in notice

    def test_does_not_notify_when_zero_remains_zero(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _make_private_notes(tmp_path, monkeypatch, unanswered=0, answered=1)
        assert _tbd_completion.build_notice("stable-zero", "/dummy") is None
        assert _tbd_completion.build_notice("stable-zero", "/dummy") is None

    def test_notifies_again_after_second_transition(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        root = _make_private_notes(tmp_path, monkeypatch, unanswered=1, answered=0)
        assert _tbd_completion.build_notice("repeat", "/dummy") is None
        _answer_all(root)
        assert _tbd_completion.build_notice("repeat", "/dummy") is not None
        (root / "inbox" / "unanswered-new.md").write_text(_entry(), encoding="utf-8")
        assert _tbd_completion.build_notice("repeat", "/dummy") is None
        (root / "inbox" / "unanswered-new.md").write_text(_entry(answer="回答\n"), encoding="utf-8")
        assert _tbd_completion.build_notice("repeat", "/dummy") is not None

    def test_does_not_notify_when_all_entries_disappear(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        root = _make_private_notes(tmp_path, monkeypatch, unanswered=1, answered=0)
        assert _tbd_completion.build_notice("removed", "/dummy") is None
        (root / "inbox" / "unanswered-0.md").unlink()
        assert _tbd_completion.build_notice("removed", "/dummy") is None

    def test_ignores_other_repositories(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _make_private_notes(tmp_path, monkeypatch, unanswered=1, answered=0, other_repo=2)
        assert _tbd_completion.build_notice("other", "/dummy") is None
        state = json.loads((tmp_path / "claude-agent-toolkit-other.json").read_text(encoding="utf-8"))
        assert state[_tbd_completion.STATE_KEY] == {_REPO: 1}

    def test_missing_root_does_not_write_state(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(_tbd_scan, "private_notes_root", lambda: None)
        assert _tbd_completion.build_notice("missing-root", "/dummy") is None
        assert not (tmp_path / "claude-agent-toolkit-missing-root.json").exists()

    def test_unresolved_repository_does_not_write_state(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _make_private_notes(tmp_path, monkeypatch, unanswered=1, answered=0)
        monkeypatch.setattr(_tbd_completion, "resolve_target_repo", lambda _cwd: None)
        assert _tbd_completion.build_notice("missing-repo", "/dummy") is None
        assert not (tmp_path / "claude-agent-toolkit-missing-repo.json").exists()

    def test_incomplete_scan_does_not_update_state_or_notify(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _make_private_notes(tmp_path, monkeypatch, unanswered=0, answered=1)
        monkeypatch.setattr(
            _tbd_scan,
            "scan_active_tbds",
            lambda _root, _repo: _tbd_scan.ActiveTbdScan([_tbd_scan.ActiveTbd("answered.md", True)], False),
        )
        assert _tbd_completion.build_notice("incomplete", "/dummy") is None
        assert not (tmp_path / "claude-agent-toolkit-incomplete.json").exists()

    def test_state_write_failure_suppresses_notice(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        root = _make_private_notes(tmp_path, monkeypatch, unanswered=1, answered=0)
        assert _tbd_completion.build_notice("write-failure", "/dummy") is None
        _answer_all(root)
        monkeypatch.setattr(_tbd_completion, "update_state", lambda _session_id, mutator: mutator({}) is not None and False)
        assert _tbd_completion.build_notice("write-failure", "/dummy") is None


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
        assert _tbd_completion.resolve_target_repo("/dummy") is None

    @pytest.mark.parametrize("error", [OSError("git missing"), subprocess.TimeoutExpired("git", 5)])
    def test_returns_none_for_execution_errors(self, error: Exception, monkeypatch: pytest.MonkeyPatch) -> None:
        def _run(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
            raise error

        monkeypatch.setattr(subprocess, "run", _run)
        assert _tbd_completion.resolve_target_repo("/dummy") is None

    def test_normalizes_origin(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def _run(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess([], 0, stdout="git@github.com:ak110/dotfiles.git\n", stderr="")

        monkeypatch.setattr(subprocess, "run", _run)
        assert _tbd_completion.resolve_target_repo("/dummy") == _REPO
