"""振り返り素材AWIを生成して投入する準備スクリプトを、公開入口から利用シナリオで検証する。"""

from __future__ import annotations

import datetime
import json
import os
import pathlib
import subprocess

import pytest
import session_review_decisions as decisions_module  # noqa: E402  # pylint: disable=wrong-import-position,import-error
import session_review_prepare as prepare  # noqa: E402  # pylint: disable=wrong-import-position,import-error
import session_review_report as report  # noqa: E402  # pylint: disable=wrong-import-position,import-error

_FIXED_NOW = datetime.datetime(2026, 9, 6, 12, 34, 56, tzinfo=datetime.UTC)
_ORIGINAL_PATH = os.environ.get("PATH", "")
_LONG_INTERVENTION = "そうじゃなくて、対象は全部です。" + "理由の説明。" * 400 + "最後まで読んで。"


def _install_atk_stub(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, *, add_fails: bool = False) -> pathlib.Path:
    """`wi list`と`wi add`の呼び出しを記録する`atk`スタブをPATHの先頭へ置き、記録先を返す。"""
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
record = {"arguments": arguments}
if arguments[:2] == ["wi", "add"]:
    body_file = arguments[arguments.index("--body-file") + 1]
    record["body"] = pathlib.Path(body_file).read_text(encoding="utf-8")
with log_path.open("a", encoding="utf-8") as stream:
    stream.write(json.dumps(record, ensure_ascii=False) + "\\n")
if arguments[:2] == ["wi", "list"]:
    if "--state=active" in arguments:
        print(json.dumps({"filename": "20260901-000000-001.md", "summary": "既存の要求"}, ensure_ascii=False))
    raise SystemExit(0)
if arguments[:2] == ["wi", "add"]:
    if os.environ.get("ATK_STUB_ADD_FAILS") == "1":
        print("失敗: 投入を拒否した", file=sys.stderr)
        raise SystemExit(1)
    if "--dry-run" in arguments:
        print("成功: 投入前の検証が成立した（--dry-runのため保存していない）")
        raise SystemExit(0)
    print("成功: 1件をinboxへ投入した")
    print("  ~/private-notes/wi/inbox/20260906-123456-001.md")
    raise SystemExit(0)
raise SystemExit(9)
""",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    log_path = tmp_path / "atk-calls.jsonl"
    log_path.write_text("", encoding="utf-8")
    monkeypatch.setenv("PATH", f"{executable_dir}{os.pathsep}{_ORIGINAL_PATH}")
    monkeypatch.setenv("ATK_STUB_LOG", str(log_path))
    monkeypatch.setenv("ATK_STUB_ADD_FAILS", "1" if add_fails else "0")
    return log_path


def _calls(log_path: pathlib.Path) -> list[dict]:
    return [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]


def _work_dir(tmp_path: pathlib.Path, observations: str = "- 同じ資料を2回読み直した（調査工程）\n") -> pathlib.Path:
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    (work_dir / prepare.MAIN_OBSERVATIONS_FILENAME).write_text(observations, encoding="utf-8")
    return work_dir


def _write_claude_transcript(tmp_path: pathlib.Path) -> pathlib.Path:
    """初期要求、失敗したコマンド、2000文字を超えるユーザー介入を持つtranscriptを書き込む。"""
    entries = [
        {"type": "user", "timestamp": "2026-09-06T12:00:00Z", "message": {"role": "user", "content": "初期要求"}},
        {
            "type": "assistant",
            "timestamp": "2026-09-06T12:00:10Z",
            "message": {
                "role": "assistant",
                "content": [{"type": "tool_use", "name": "Bash", "id": "toolu_fail", "input": {"command": "make lint"}}],
            },
        },
        {
            "type": "user",
            "timestamp": "2026-09-06T12:00:20Z",
            "message": {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "toolu_fail", "is_error": True, "content": "Error: 検査に失敗した"}
                ],
            },
        },
        {"type": "user", "timestamp": "2026-09-06T12:01:00Z", "message": {"role": "user", "content": _LONG_INTERVENTION}},
    ]
    transcript = tmp_path / "11111111-2222-3333-4444-555555555555.jsonl"
    transcript.write_text("".join(json.dumps(entry, ensure_ascii=False) + "\n" for entry in entries), encoding="utf-8")
    return transcript


def _git_repository(path: pathlib.Path) -> pathlib.Path:
    path.mkdir()
    subprocess.run(["git", "-C", str(path), "init"], check=True, capture_output=True)
    (path / "tracked.txt").write_text("tracked\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(path), "add", "tracked.txt"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(path), "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-m", "initial"],
        check=True,
        capture_output=True,
    )
    return path


def test_prepare_submits_material_awi_claude(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Claude Codeの記録から素材AWIを1回投入し、投入結果と件数を1行JSONで返す。"""
    log_path = _install_atk_stub(monkeypatch, tmp_path)
    work_dir = _work_dir(tmp_path)
    target_repo = _git_repository(tmp_path / "repo")

    exit_code = prepare.main(
        [
            "--transcript",
            str(_write_claude_transcript(tmp_path)),
            "--work-dir",
            str(work_dir),
            "--target-repo",
            str(target_repo),
        ],
        now=_FIXED_NOW,
    )

    assert exit_code == 0
    stdout = capsys.readouterr().out
    assert len(stdout.splitlines()) == 1
    record = json.loads(stdout)
    assert record["submitted"] is True
    assert record["awi_filename"] == "20260906-123456-001.md"
    assert record["candidate_counts"] == {"tool-failure": 1, "user-intervention": 1}
    assert record["candidate_total"] == 2
    assert record["main_observation_count"] == 1
    assert record["elapsed_seconds"] == 60
    adds = [call for call in _calls(log_path) if call["arguments"][:2] == ["wi", "add"]]
    assert len(adds) == 1
    assert adds[0]["arguments"][2:4] == ["--source", "session-review"]
    assert f"--target-repo={target_repo.resolve()}" in adds[0]["arguments"]
    assert "--dry-run" not in adds[0]["arguments"]
    assert adds[0]["body"] == pathlib.Path(record["material_path"]).read_text(encoding="utf-8")


def test_material_body_contract(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """素材AWIは元記録を開かずに判定できる本文を持ち、記録位置を持たず、レーンの判定入力と報告生成へそのまま渡せる。"""
    _install_atk_stub(monkeypatch, tmp_path)
    work_dir = _work_dir(tmp_path)
    target_repo = _git_repository(tmp_path / "repo")

    assert (
        prepare.main(
            [
                "--transcript",
                str(_write_claude_transcript(tmp_path)),
                "--work-dir",
                str(work_dir),
                "--target-repo",
                str(target_repo),
                "--dry-run",
            ],
            now=_FIXED_NOW,
        )
        == 0
    )
    record = json.loads(capsys.readouterr().out)
    assert record["submitted"] is False
    assert record["awi_filename"] is None
    material_path = pathlib.Path(record["material_path"])
    body = material_path.read_text(encoding="utf-8")

    headings = [line for line in body.splitlines() if line.startswith("## ")]
    assert headings == [
        "## 反映内容と反映先",
        "## 適用範囲",
        "## 実現性",
        "## 完成条件",
        "## 問題候補",
        "## メイン由来の改善点",
        "## セッション統計",
        "## 既存キュー項目",
        "## 参考情報",
    ]
    assert _LONG_INTERVENTION in body
    assert "Error: 検査に失敗した" in body
    assert '"command": "make lint"' in body
    assert "- 同じ資料を2回読み直した（調査工程）" in body
    assert "- 20260901-000000-001.md: 既存の要求" in body
    assert "main:" not in body
    assert "analysis_group_hint" not in body
    assert "candidate-evidence" not in body
    assert "make test" not in body

    decisions_path = tmp_path / "decisions.json"
    assert decisions_module.main(["--material", str(material_path), "--output", str(decisions_path)]) == 0
    pending = json.loads(decisions_path.read_text(encoding="utf-8"))
    decided = [
        {**item, "disposition": "excluded", "reason": "検査の失敗は同じ工程で修正済み"}
        if item["candidate_kind"] == "tool-failure"
        else {**item, "disposition": "analyzed", "analysis_id": "a1", "defect": "欠陥"}
        for item in pending
    ]
    decisions_path.write_text(json.dumps(decided, ensure_ascii=False), encoding="utf-8")
    analyses = tmp_path / "analyses.json"
    analyses.write_text(
        json.dumps(
            {
                "a1": {
                    "observation": "対象の範囲をユーザーが是正した",
                    "root_cause": "開放列挙を閉じた",
                    "measures": "全件を処理した",
                    "prevention": [{"kind": "implemented", "ref": "commit abc1234", "summary": "確認対象へ加えた"}],
                    "artifacts": ["agent-toolkit/rules/01-agent.md"],
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    uwi = tmp_path / "uwi.md"
    capsys.readouterr()
    arguments = [
        "--material",
        str(material_path),
        "--decisions",
        str(decisions_path),
        "--analyses",
        str(analyses),
        "--output",
        str(uwi),
    ]
    assert report.main(["generate", *arguments]) == 0
    assert report.main(["check", *arguments]) == 0


def test_prepare_submits_material_awi_codex(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Codexのthread IDからも同じ経路で素材AWIを投入する。候補が無くても改善点があれば投入する。"""
    log_path = _install_atk_stub(monkeypatch, tmp_path)
    codex_home = tmp_path / "codex-home"
    rollout_dir = codex_home / "sessions" / "2026" / "09" / "06"
    rollout_dir.mkdir(parents=True)
    thread_id = "019900aa-bbbb-7ccc-8ddd-eeeeeeeeeeee"
    (rollout_dir / f"rollout-test-{thread_id}.jsonl").write_text(
        json.dumps({"timestamp": "2026-09-06T12:00:00Z", "payload": {"type": "token_count", "info": None}}) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    work_dir = _work_dir(tmp_path)
    target_repo = _git_repository(tmp_path / "repo")

    exit_code = prepare.main(
        ["--codex-thread-id", thread_id, "--work-dir", str(work_dir), "--target-repo", str(target_repo)], now=_FIXED_NOW
    )

    assert exit_code == 0, capsys.readouterr().err
    record = json.loads(capsys.readouterr().out)
    assert record["submitted"] is True
    assert record["candidate_total"] == 0
    adds = [call for call in _calls(log_path) if call["arguments"][:2] == ["wi", "add"]]
    assert len(adds) == 1
    assert f"# セッションCodex {thread_id}の振り返り素材" in adds[0]["body"]


def test_prepare_skips_submission_without_material(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """候補も改善点も無いセッションでは投入せず、省略の理由を返す。対象リポジトリの無い準備も投入しない。"""
    log_path = _install_atk_stub(monkeypatch, tmp_path)
    transcript = tmp_path / "empty.jsonl"
    transcript.write_text(
        json.dumps({"type": "user", "message": {"role": "user", "content": "初期要求"}}) + "\n", encoding="utf-8"
    )
    target_repo = _git_repository(tmp_path / "repo")
    work_dir = _work_dir(tmp_path, observations="")

    assert prepare.main(["--transcript", str(transcript), "--work-dir", str(work_dir), "--target-repo", str(target_repo)]) == 0
    record = json.loads(capsys.readouterr().out)
    assert (record["submitted"], record["skipped_reason"]) == (False, "no-candidates")
    assert not [call for call in _calls(log_path) if call["arguments"][:2] == ["wi", "add"]]

    other = tmp_path / "other"
    other.mkdir()
    assert prepare.main(["--transcript", str(_write_claude_transcript(other)), "--work-dir", str(_work_dir(other))]) == 0
    record = json.loads(capsys.readouterr().out)
    assert record["submitted"] is False
    assert pathlib.Path(record["material_path"]).is_file()
    assert not [call for call in _calls(log_path) if call["arguments"][:2] == ["wi", "add"]]


@pytest.mark.parametrize("linked_worktree", [False, True])
def test_prepare_resolves_reference_document_from_main_worktree_name(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    *,
    linked_worktree: bool,
) -> None:
    """通常checkoutとlinked worktreeは同じ参照文書へ解決し、素材AWIの参考情報へ載せる。"""
    _install_atk_stub(monkeypatch, tmp_path)
    main_worktree = _git_repository(tmp_path / "dotfiles")
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

    assert (
        prepare.main(
            [
                "--transcript",
                str(_write_claude_transcript(tmp_path)),
                "--work-dir",
                str(_work_dir(tmp_path)),
                "--target-repo",
                str(target_repo),
            ],
            now=_FIXED_NOW,
        )
        == 0
    )

    record = json.loads(capsys.readouterr().out)
    assert f"- 振り返りの参照文書: `{document}`" in pathlib.Path(record["material_path"]).read_text(encoding="utf-8")


def test_prepare_reports_missing_items(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """記録、改善点のファイル、抽出器の同一性又は投入が成立しない場合は、不足項目を返して標準出力を空に保つ。"""
    log_path = _install_atk_stub(monkeypatch, tmp_path, add_fails=True)
    transcript = _write_claude_transcript(tmp_path)
    work_dir = _work_dir(tmp_path)
    target_repo = _git_repository(tmp_path / "repo")

    assert prepare.main(["--transcript", str(tmp_path / "missing.jsonl"), "--work-dir", str(work_dir)]) == 2
    captured = capsys.readouterr()
    assert (captured.out, captured.err) == ("", "不足: transcript_path\n")

    bare = tmp_path / "bare"
    bare.mkdir()
    assert prepare.main(["--transcript", str(transcript), "--work-dir", str(bare)]) == 2
    captured = capsys.readouterr()
    assert not captured.out
    assert captured.err.startswith("不足: main-observations\n")

    assert prepare.main(["--transcript", str(transcript), "--work-dir", str(work_dir), "--target-repo", str(target_repo)]) == 2
    captured = capsys.readouterr()
    assert not captured.out
    assert captured.err.startswith("不足: atk wi add\n")
    assert "投入を拒否した" in captured.err
    assert len([call for call in _calls(log_path) if call["arguments"][:2] == ["wi", "add"]]) == 1

    monkeypatch.setattr(prepare, "__file__", str(tmp_path / "detached" / "session_review_prepare.py"))
    assert prepare.main(["--transcript", str(transcript), "--work-dir", str(work_dir)]) == 2
    captured = capsys.readouterr()
    assert (captured.out, captured.err) == ("", "不足: evidence_script\n")
