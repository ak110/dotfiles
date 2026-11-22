"""反復失敗の起票と、利用できないキューの扱いを検証する。"""

import json
import pathlib
import subprocess
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import update_dotfiles_report as report  # noqa: E402  # pylint: disable=wrong-import-position
import update_dotfiles_runtime as runtime  # noqa: E402  # pylint: disable=wrong-import-position


@pytest.mark.parametrize("available", [True, False])
def test_repeated_failure_reports_once(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, available: bool) -> None:
    """3回連続の失敗だけを投入し、成功した回があれば連続回数を戻す。"""
    monkeypatch.setattr(report.shutil, "which", lambda _name: "/fake/atk" if available else None)
    monkeypatch.setattr(report.private_notes, "default_private_notes", lambda: tmp_path)
    calls: list[list[str]] = []
    monkeypatch.setattr(report, "_pending", lambda _notes, _key: bool(calls))

    def run(argv: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        body = pathlib.Path(argv[argv.index("--body-file") + 1]).read_text(encoding="utf-8")
        assert "表示専用" in body
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(report.process_tree, "run_with_timeout", run)
    state = tmp_path / "state" / "failure.json"
    failure = [("表示専用", "missing executable")]
    report.report(failure, state, tmp_path)
    report.report(failure, state, tmp_path)
    assert not calls
    report.report([], state, tmp_path)
    for _ in range(4):
        report.report(failure, state, tmp_path)
    assert len(calls) == int(available)


def test_queue_error_does_not_interrupt_update(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """起票子プロセスが時間切れでも、更新の失敗表示を妨げない。"""
    monkeypatch.setattr(report.shutil, "which", lambda _name: "/fake/atk")
    monkeypatch.setattr(report.private_notes, "default_private_notes", lambda: tmp_path)

    def run(*_args: object, **_kwargs: object) -> None:
        raise subprocess.TimeoutExpired("atk", 120)

    monkeypatch.setattr(report.process_tree, "run_with_timeout", run)
    for _ in range(3):
        report.report([("表示専用", "failed")], tmp_path / "failure.json", tmp_path)
    assert not list(tmp_path.glob("update-failure-*.md"))


@pytest.mark.parametrize("state_name", ["inbox", "processing", "hold", "adopted", "rejected"])
def test_pending_queue_entry_prevents_duplicate(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, state_name: str
) -> None:
    """既存キューの状態を読み、未完了の同じキーだけを重複抑止する。"""
    note = tmp_path / "existing.md"
    state = tmp_path / "failure.json"
    monkeypatch.setattr(report.shutil, "which", lambda _name: "/fake/atk")
    monkeypatch.setattr(report.private_notes, "default_private_notes", lambda: tmp_path)
    monkeypatch.setattr(report.wi_layout, "iter_entry_paths", lambda _root, **_kwargs: [note] if note.exists() else [])
    calls: list[list[str]] = []

    def run(argv: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(report.process_tree, "run_with_timeout", run)
    failure = [("表示専用", "failed")]
    report.report(failure, state, tmp_path)
    (key,) = json.loads(state.read_text(encoding="utf-8"))
    note.write_text(f"---\nstate: {state_name}\nupdate_dotfiles_failure_key: {key}\n---\n", encoding="utf-8")
    report.report(failure, state, tmp_path)
    report.report(failure, state, tmp_path)
    assert len(calls) == (0 if state_name in {"inbox", "processing", "hold"} else 1)


@pytest.mark.parametrize("body_exit,expected", [(0, 1), (7, 7)])
@pytest.mark.parametrize("transport", ["env", "file"])
def test_finish_aggregates_bootstrap_failures_and_preserves_body_failure(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    body_exit: int,
    expected: int,
    transport: str,
) -> None:
    """外側の失敗も一覧と1へ集約し、本体が失敗した場合はその終了値を保つ。"""
    recorded: list[tuple[str, str]] = []

    def collect(failures: list[tuple[str, str]], _state: pathlib.Path, _root: pathlib.Path) -> None:
        recorded.extend(failures)

    monkeypatch.setattr(report, "report", collect)
    monkeypatch.setattr(runtime.sync_report, "REPORT_PATH", tmp_path / "sync-report.json")
    monkeypatch.setattr(runtime, "current_run_id", "test")
    monkeypatch.setattr(runtime, "continued_failures", [])
    monkeypatch.setattr(runtime, "current_stage_title", "chezmoi apply")
    monkeypatch.setattr(runtime, "last_stderr_tail", "本体の診断")
    monkeypatch.setattr(runtime, "LOG_PATH", tmp_path / "update.log")
    if transport == "env":
        monkeypatch.setenv("UPDATE_DOTFILES_BOOTSTRAP_FAILURES", "管理Pythonの更新: exit=17\n")
    else:
        payload = tmp_path / "update-bootstrap-11-22.json"
        payload.write_text(json.dumps([{"title": "管理Pythonの更新", "detail": "exit=17"}]), encoding="utf-8-sig")
        monkeypatch.setenv("UPDATE_DOTFILES_BOOTSTRAP_REPORT", str(payload))
    assert runtime.finish(body_exit) == expected
    assert ("管理Pythonの更新", "exit=17") in recorded
    assert "管理Pythonの更新" in capsys.readouterr().err


def test_bootstrap_failures_redacts_before_truncating(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    payload = tmp_path / "update-bootstrap-11-22.json"
    prefix = "x" * 4060 + " https://"
    detail = f"{prefix}{'A' * 40}@example.org"
    payload.write_text(json.dumps([{"title": "更新失敗", "detail": detail}]), encoding="utf-8")
    monkeypatch.setenv("UPDATE_DOTFILES_BOOTSTRAP_REPORT", str(payload))

    assert report.bootstrap_failures(tmp_path) == [("更新失敗", f"{prefix}[REDACTED]@example.org")]


def test_sync_report_redacts_before_taking_tail() -> None:
    token = "A" * 40 + "synthetic"
    tail = "z" * 1965
    raw = f"https://{token}@example.org\n{tail}"

    assert runtime.sync_report.truncate_tail(raw) == f"https://[REDACTED]@example.org\n{tail}"


def test_sync_report_finish_redacts_every_entry(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """共通の抜粋を通さずに渡された終了時の標準エラーも、保存の直前で伏せて上限を守る。"""
    monkeypatch.setattr(runtime.sync_report, "REPORT_PATH", tmp_path / "sync-report.json")
    raw = "更新ロックの例外 password=synthetic-secret-0123456789\n" + "z" * 5000
    runtime.sync_report.write_finish(
        "run-1", status="failed", exit_code=1, failed_stage="更新ロックの取得", stderr_tail=raw, finished_at="now"
    )

    saved = runtime.sync_report.read()["stderr_tail"]
    assert "synthetic-secret" not in saved
    assert len(saved) <= runtime.sync_report.STDERR_TAIL_MAX_CHARS
