"""セッション振り返りの準備項目を一括取得するスクリプトを検証する。"""

from __future__ import annotations

import datetime
import json
import os
import pathlib
import subprocess

import pytest
import session_review_prepare as prepare  # noqa: E402  # pylint: disable=wrong-import-position,import-error

_FIXED_NOW = datetime.datetime(2026, 9, 6, 12, 34, 56, tzinfo=datetime.UTC)
_ORIGINAL_PATH = os.environ.get("PATH", "")


def _install_atk_stub(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    *,
    create_fails: bool = False,
    output_create_fails: bool = False,
) -> tuple[pathlib.Path, pathlib.Path, pathlib.Path]:
    """呼び出しを記録する`atk`スタブをPATHの先頭へ置く。"""
    executable_dir = tmp_path / "bin"
    executable_dir.mkdir()
    executable = executable_dir / "atk"
    executable.write_text(
        """#!/usr/bin/env python3
import json
import os
import pathlib
import sys

arguments = sys.argv[1:]
log_path = pathlib.Path(os.environ["ATK_STUB_LOG"])
with log_path.open("a", encoding="utf-8") as stream:
    stream.write(json.dumps(arguments, ensure_ascii=False) + "\\n")
managed_temp = pathlib.Path(os.environ["ATK_STUB_TEMP"])
output_temp = pathlib.Path(os.environ["ATK_STUB_OUTPUT_TEMP"])
if arguments == ["managed-temp", "create", "--prefix", "session-review"]:
    if os.environ.get("ATK_STUB_CREATE_FAILS") == "1":
        raise SystemExit(1)
    managed_temp.mkdir(exist_ok=False)
    print(managed_temp)
elif arguments == ["managed-temp", "create", "--prefix", "session-review-output"]:
    if os.environ.get("ATK_STUB_OUTPUT_CREATE_FAILS") == "1":
        raise SystemExit(1)
    output_temp.mkdir(exist_ok=False)
    print(output_temp)
else:
    raise SystemExit(9)
""",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    log_path = tmp_path / "atk-calls.jsonl"
    managed_temp = tmp_path / "managed-temp"
    output_temp = tmp_path / "output-temp"
    monkeypatch.setenv("PATH", f"{executable_dir}{os.pathsep}{_ORIGINAL_PATH}")
    monkeypatch.setenv("ATK_STUB_LOG", str(log_path))
    monkeypatch.setenv("ATK_STUB_TEMP", str(managed_temp))
    monkeypatch.setenv("ATK_STUB_OUTPUT_TEMP", str(output_temp))
    monkeypatch.setenv("ATK_STUB_CREATE_FAILS", "1" if create_fails else "0")
    monkeypatch.setenv("ATK_STUB_OUTPUT_CREATE_FAILS", "1" if output_create_fails else "0")
    return managed_temp, output_temp, log_path


def _write_transcript(tmp_path: pathlib.Path) -> pathlib.Path:
    """実在確認用の最小transcriptを作成する。"""
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text("{}\n", encoding="utf-8")
    return transcript


def _calls(log_path: pathlib.Path) -> list[list[str]]:
    """スタブが記録した引数列を返す。"""
    return [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]


def test_prepare_does_not_read_queue(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """対象リポジトリ指定時もキューを読まず、準備項目だけを1行で返す。"""
    managed_temp, output_temp, log_path = _install_atk_stub(monkeypatch, tmp_path)
    transcript = _write_transcript(tmp_path)
    target_repo = tmp_path / "target-repo"
    target_repo.mkdir()

    assert (
        prepare.main(
            ["--transcript", str(transcript), "--target-repo", str(target_repo)],
            now=_FIXED_NOW,
        )
        == 0
    )

    captured = capsys.readouterr()
    assert not captured.err
    assert len(captured.out.splitlines()) == 1
    assert json.loads(captured.out) == {
        "evidence_script": str(pathlib.Path(prepare.__file__).resolve().with_name("session_review_evidence.py")),
        "report_script": str(pathlib.Path(prepare.__file__).resolve().with_name("session_review_report.py")),
        "plugin_root": str(pathlib.Path(prepare.__file__).resolve().parents[3]),
        "transcript_path": str(transcript.resolve()),
        "codex_thread_id": None,
        "managed_temp": str(managed_temp),
        "bundle_dir": str(managed_temp / "bundle"),
        "manifest_path": str(output_temp / "prepare-manifest.json"),
        "output_temp": str(output_temp),
        "output_file": str(output_temp / "session-review.md"),
        "observation_boundary": "2026-09-06T12:34:56Z",
        "target_repo": str(target_repo.resolve()),
        "reference_document": None,
    }
    assert (managed_temp / "bundle").is_dir()
    manifest_path = pathlib.Path(json.loads(captured.out)["manifest_path"])
    output_file = pathlib.Path(json.loads(captured.out)["output_file"])
    assert manifest_path.parent == output_file.parent == output_temp
    assert json.loads(manifest_path.read_text(encoding="utf-8")) == json.loads(captured.out)
    assert _calls(log_path) == [
        ["managed-temp", "create", "--prefix", "session-review"],
        ["managed-temp", "create", "--prefix", "session-review-output"],
    ]


def test_prepare_omits_target_repo(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """対象リポジトリ未指定時は対象項目をnullにする。"""
    _, _, log_path = _install_atk_stub(monkeypatch, tmp_path)

    assert prepare.main(["--codex-thread-id", "thread-1"], now=_FIXED_NOW) == 0

    captured = capsys.readouterr()
    assert not captured.err
    record = json.loads(captured.out)
    assert record["transcript_path"] is None
    assert record["codex_thread_id"] == "thread-1"
    assert record["target_repo"] is None
    assert _calls(log_path) == [
        ["managed-temp", "create", "--prefix", "session-review"],
        ["managed-temp", "create", "--prefix", "session-review-output"],
    ]


@pytest.mark.parametrize("linked_worktree", [False, True])
def test_prepare_resolves_reference_document_from_main_worktree_name(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    *,
    linked_worktree: bool,
) -> None:
    """通常checkoutとlinked worktreeは同じ参照文書へ解決する。"""
    _install_atk_stub(monkeypatch, tmp_path)
    transcript = _write_transcript(tmp_path)
    main_worktree = tmp_path / "dotfiles"
    main_worktree.mkdir()
    subprocess.run(["git", "-C", str(main_worktree), "init"], check=True, capture_output=True)
    (main_worktree / "tracked.txt").write_text("tracked\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(main_worktree), "add", "tracked.txt"], check=True, capture_output=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(main_worktree),
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "-m",
            "initial",
        ],
        check=True,
        capture_output=True,
    )
    target_repo = main_worktree
    if linked_worktree:
        target_repo = tmp_path / "process-loop"
        subprocess.run(
            ["git", "-C", str(main_worktree), "worktree", "add", "--detach", str(target_repo)],
            check=True,
            capture_output=True,
        )
    home = tmp_path / "home"
    document = home / ".claude" / "docs" / "session-review-dotfiles.md"
    document.parent.mkdir(parents=True)
    document.write_text("# 観点\n", encoding="utf-8")
    monkeypatch.setattr(pathlib.Path, "home", classmethod(lambda _cls: home))

    assert prepare.main(["--transcript", str(transcript), "--target-repo", str(target_repo)], now=_FIXED_NOW) == 0

    record = json.loads(capsys.readouterr().out)
    assert record["target_repo"] == str(target_repo.resolve())
    assert record["reference_document"] == str(document)


def test_prepare_reports_missing_items(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """記録の識別子と抽出器が成立しない場合は不足項目を返す。"""
    transcript = _write_transcript(tmp_path)

    assert prepare.main(["--transcript", str(tmp_path / "missing.jsonl")], now=_FIXED_NOW) == 2
    captured = capsys.readouterr()
    assert not captured.out
    assert captured.err == "不足: transcript_path\n"

    monkeypatch.setattr(prepare, "__file__", str(tmp_path / "detached" / "session_review_prepare.py"))

    assert prepare.main(["--transcript", str(transcript)], now=_FIXED_NOW) == 2

    captured = capsys.readouterr()
    assert not captured.out
    assert captured.err == "不足: evidence_script\n"


def test_prepare_reports_managed_temp_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`atk`を解決又は管理対象一時領域を作成できない場合は不足項目を返す。"""
    transcript = _write_transcript(tmp_path)
    empty_path = tmp_path / "empty-path"
    empty_path.mkdir()
    monkeypatch.setenv("PATH", str(empty_path))

    assert prepare.main(["--transcript", str(transcript)], now=_FIXED_NOW) == 2
    captured = capsys.readouterr()
    assert not captured.out
    assert captured.err == "不足: managed_temp\n"

    _, _, log_path = _install_atk_stub(monkeypatch, tmp_path, create_fails=True)

    assert prepare.main(["--transcript", str(transcript)], now=_FIXED_NOW) == 2

    captured = capsys.readouterr()
    assert not captured.out
    assert captured.err == "不足: managed_temp\n"
    assert _calls(log_path) == [["managed-temp", "create", "--prefix", "session-review"]]

    retry_path = tmp_path / "retry"
    retry_path.mkdir()
    _, _, log_path = _install_atk_stub(monkeypatch, retry_path, output_create_fails=True)

    assert prepare.main(["--transcript", str(transcript)], now=_FIXED_NOW) == 2

    captured = capsys.readouterr()
    assert not captured.out
    assert captured.err == "不足: output_temp\n"
    assert _calls(log_path) == [
        ["managed-temp", "create", "--prefix", "session-review"],
        ["managed-temp", "create", "--prefix", "session-review-output"],
    ]
