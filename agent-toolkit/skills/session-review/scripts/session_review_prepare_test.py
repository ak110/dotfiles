"""セッション振り返りの準備項目を一括取得するスクリプトを検証する。"""

from __future__ import annotations

import datetime
import json
import os
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import session_review_prepare as prepare  # noqa: E402  # pylint: disable=wrong-import-position,import-error

_FIXED_NOW = datetime.datetime(2026, 9, 6, 12, 34, 56, tzinfo=datetime.UTC)
_ORIGINAL_PATH = os.environ.get("PATH", "")


def _install_atk_stub(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    *,
    pending_items: list[dict[str, str]] | None = None,
    pending_output: str | None = None,
    create_fails: bool = False,
    list_fails: bool = False,
) -> tuple[pathlib.Path, pathlib.Path]:
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
if arguments == ["managed-temp", "create", "--prefix", "session-review"]:
    if os.environ.get("ATK_STUB_CREATE_FAILS") == "1":
        raise SystemExit(1)
    managed_temp.mkdir()
    print(managed_temp)
elif arguments[:2] == ["wi", "list"]:
    if os.environ.get("ATK_STUB_LIST_FAILS") == "1":
        raise SystemExit(1)
    output = os.environ.get("ATK_STUB_PENDING_ITEMS", "")
    if output:
        print(output)
elif arguments[:3] == ["managed-temp", "cleanup", "--path"]:
    pathlib.Path(arguments[3]).rmdir()
else:
    raise SystemExit(9)
""",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    log_path = tmp_path / "atk-calls.jsonl"
    managed_temp = tmp_path / "managed-temp"
    monkeypatch.setenv("PATH", f"{executable_dir}{os.pathsep}{_ORIGINAL_PATH}")
    monkeypatch.setenv("ATK_STUB_LOG", str(log_path))
    monkeypatch.setenv("ATK_STUB_TEMP", str(managed_temp))
    monkeypatch.setenv(
        "ATK_STUB_PENDING_ITEMS",
        pending_output
        if pending_output is not None
        else "\n".join(json.dumps(item, ensure_ascii=False) for item in pending_items or []),
    )
    monkeypatch.setenv("ATK_STUB_CREATE_FAILS", "1" if create_fails else "0")
    monkeypatch.setenv("ATK_STUB_LIST_FAILS", "1" if list_fails else "0")
    return managed_temp, log_path


def _write_transcript(tmp_path: pathlib.Path) -> pathlib.Path:
    """実在確認用の最小transcriptを作成する。"""
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text("{}\n", encoding="utf-8")
    return transcript


def _calls(log_path: pathlib.Path) -> list[list[str]]:
    """スタブが記録した引数列を返す。"""
    return [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]


@pytest.mark.parametrize(
    "pending_items",
    [
        [],
        [{"filename": "one.md", "summary": "1件"}],
        [
            {"filename": "awi.md", "summary": "AWI"},
            {"filename": "uwi.md", "summary": "UWI"},
        ],
    ],
)
def test_prepare_outputs_all_items(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    pending_items: list[dict[str, str]],
) -> None:
    """対象リポジトリ指定時は全準備項目と未処理項目を1行で返す。"""
    managed_temp, log_path = _install_atk_stub(monkeypatch, tmp_path, pending_items=pending_items)
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
        "transcript_path": str(transcript.resolve()),
        "codex_thread_id": None,
        "managed_temp": str(managed_temp),
        "observation_boundary": "2026-09-06T12:34:56Z",
        "target_repo": str(target_repo.resolve()),
        "pending_items": pending_items,
    }
    assert _calls(log_path) == [
        ["managed-temp", "create", "--prefix", "session-review"],
        [
            "wi",
            "list",
            f"--target-repo={target_repo.resolve()}",
            "--status=active",
            "--summary-only",
            "--skip-pull",
        ],
    ]


def test_prepare_omits_target_repo(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """対象リポジトリ未指定時は一覧を取得せず関連2項目をnullにする。"""
    _, log_path = _install_atk_stub(monkeypatch, tmp_path)

    assert prepare.main(["--codex-thread-id", "thread-1"], now=_FIXED_NOW) == 0

    captured = capsys.readouterr()
    assert not captured.err
    record = json.loads(captured.out)
    assert record["transcript_path"] is None
    assert record["codex_thread_id"] == "thread-1"
    assert record["target_repo"] is None
    assert record["pending_items"] is None
    assert _calls(log_path) == [["managed-temp", "create", "--prefix", "session-review"]]


def test_prepare_reports_missing_items(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """記録の識別子と抽出器が成立しない場合は不足項目を返す。"""
    transcript = _write_transcript(tmp_path)

    for arguments in (
        [],
        ["--transcript", str(transcript), "--codex-thread-id", "thread-1"],
        ["--transcript", str(tmp_path / "missing.jsonl")],
    ):
        assert prepare.main(arguments, now=_FIXED_NOW) == 2
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

    _, log_path = _install_atk_stub(monkeypatch, tmp_path, create_fails=True)

    assert prepare.main(["--transcript", str(transcript)], now=_FIXED_NOW) == 2

    captured = capsys.readouterr()
    assert not captured.out
    assert captured.err == "不足: managed_temp\n"
    assert _calls(log_path) == [["managed-temp", "create", "--prefix", "session-review"]]


@pytest.mark.parametrize(
    ("list_fails", "pending_output"),
    [
        (True, None),
        (False, "not-json"),
        (False, '{"filename":"item.md","summary":"要約","extra":true}'),
    ],
)
def test_prepare_cleans_up_on_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    list_fails: bool,
    pending_output: str | None,
) -> None:
    """一覧の実行結果が成立しない場合は作成済みの管理対象一時領域を回収する。"""
    managed_temp, log_path = _install_atk_stub(
        monkeypatch,
        tmp_path,
        list_fails=list_fails,
        pending_output=pending_output,
    )
    transcript = _write_transcript(tmp_path)
    target_repo = tmp_path / "target-repo"
    target_repo.mkdir()

    assert (
        prepare.main(
            ["--transcript", str(transcript), "--target-repo", str(target_repo)],
            now=_FIXED_NOW,
        )
        == 2
    )

    captured = capsys.readouterr()
    assert not captured.out
    assert captured.err == "不足: pending_items\n"
    assert not managed_temp.exists()
    assert _calls(log_path)[-1] == ["managed-temp", "cleanup", "--path", str(managed_temp)]
