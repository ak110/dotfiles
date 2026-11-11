"""WIのmutations content試験で共有する準備処理。"""

import pathlib
import subprocess

import pytest

from agent_toolkit import atk
from agent_toolkit._atk.wi import user_comment
from agent_toolkit._testing.wi_bodies import AGENT_AWI_BODY
from agent_toolkit._testing.wi_mutations_support import _write_uwi_entry
from agent_toolkit.atk_test import _write_awi_file

_AGENT_ENVIRONMENT_VARIABLES = ("AI_AGENT", "CODEX_CI", "CLAUDECODE", "CURSOR_AGENT")


_USER_COMMENT_ERROR = "失敗: " + user_comment.AGENT_USER_COMMENT_EDIT_ERROR


def _edit_body_args(tmp_path: pathlib.Path, filename: str, message: str, *, append: bool = False) -> list[str]:
    """本文をファイルへ保存し、`atk wi edit`の引数列を返す。"""
    body_path = tmp_path / f"{filename}.body.md"
    body_path.write_text(message, encoding="utf-8")
    args = ["wi", "edit", filename]
    if append:
        args.append("--append")
    return [*args, "--body-file", str(body_path)]


def _next_action_lines(err: str) -> list[str]:
    """標準エラーから次の操作の行だけを返す。"""
    return [line for line in err.splitlines() if line.startswith("次の操作: ")]


_DRY_RUN_SUCCESS = "成功: 編集前の検証が成立した（--dry-runのため保存していない）"


def _git_output(directory: pathlib.Path, *args: str) -> str:
    """隔離したprivate-notesまたはremoteでGitを実行して標準出力を返す。"""
    return subprocess.run(
        ["git", "-C", str(directory), *args],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    ).stdout.strip()


def _prepare_reserved_key(notes: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> tuple[str, str]:
    del monkeypatch
    _write_awi_file(notes, "fb-001.md")
    return "fb-001.md", f"---\ntarget_commit: {'b' * 40}\n---\n\n編集後"


def _prepare_type_change(notes: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> tuple[str, str]:
    del monkeypatch
    _write_awi_file(notes, "fb-001.md")
    return "fb-001.md", "---\ntype: uwi\n---\n\n編集後"


def _prepare_user_comment(notes: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> tuple[str, str]:
    monkeypatch.setenv("AI_AGENT", "1")
    _write_awi_file(notes, "fb-001.md", source="test")
    return "fb-001.md", "編集後\n\n## ユーザーコメント\n\nコメント\n"


def _prepare_missing_sections(notes: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> tuple[str, str]:
    monkeypatch.setenv("AI_AGENT", "1")
    _write_awi_file(notes, "fb-001.md", body=AGENT_AWI_BODY, source="test")
    return "fb-001.md", "必須節を持たない本文"


def _prepare_undetermined_cause(notes: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> tuple[str, str]:
    monkeypatch.setenv("AI_AGENT", "1")
    _write_uwi_entry(notes, "fb-001.md")
    return "fb-001.md", "編集後\n\n| 項目 | 内容 |\n| --- | --- |\n| 直接的原因 | 調査中 |\n"


def _prepare_processing(notes: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> tuple[str, str]:
    monkeypatch.setenv("AI_AGENT", "1")
    processing = notes / "2026" / "10"
    processing.mkdir(parents=True, exist_ok=True)
    (processing / "fb-001.md").write_text(
        "---\nstate: processing\ntarget_repo: github.com/example/foo\ntype: awi\n---\n\n編集前\n", encoding="utf-8"
    )
    return "fb-001.md", "編集後"


def _prepare_legacy_body(notes: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> tuple[str, str]:
    monkeypatch.setenv("AI_AGENT", "1")
    _write_awi_file(notes, "fb-001.md", body="必須節を持たない旧書式の本文", source="test")
    return "fb-001.md", "旧書式のまま直した本文"


def _prepare_colloquial_body(notes: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> tuple[str, str]:
    del monkeypatch
    _write_awi_file(notes, "fb-001.md")
    return "fb-001.md", "説明—補足\n口語っぽいけど直す"


def _run_edit(
    home: pathlib.Path, argv: list[str], capsys: pytest.CaptureFixture[str]
) -> tuple[int | str | None, str, list[str]]:
    with pytest.raises(SystemExit) as result:
        atk.main(argv, home=home)
    output = capsys.readouterr()
    return result.value.code, output.err.replace(str(home), "<HOME>"), output.out.splitlines()
