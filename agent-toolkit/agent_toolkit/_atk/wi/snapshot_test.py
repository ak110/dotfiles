"""開始時集合の保存・新着抽出の公開一覧操作を検証する。"""

import json
import pathlib
import subprocess

import pytest

from agent_toolkit import atk
from agent_toolkit._atk import config
from agent_toolkit.atk_test import _make_subprocess_fake, _setup_notes, _write_awi_file, _write_uwi_file


@pytest.mark.parametrize("configured", [False, True])
def test_list_snapshot_precedes_filters(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], configured: bool
) -> None:
    """全状態・全種別を同じ取得から保存し、既存状態変更と時刻順によらず新着を区別する。"""
    notes = _setup_notes(tmp_path)
    for state in ("inbox", "processing", "hold", "adopted", "rejected"):
        path = _write_awi_file(notes, f"{state}.md", source="human")
        path.write_text(path.read_text(encoding="utf-8").replace("state: inbox", f"state: {state}"), encoding="utf-8")
    _write_uwi_file(notes, "unanswered.md", answer="")
    _write_awi_file(notes, "20261110-123456-001.md")
    _write_awi_file(notes, "20261110-083456-001.md")
    _write_awi_file(notes, "other.md", target_repo="github.com/example/other")
    monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))
    target = ["--configured-repos"] if configured else ["--target-repo=github.com/example/foo"]
    if configured:
        repository = config.ConfiguredRepository("github.com/example/foo", tmp_path, tmp_path / ".git")
        monkeypatch.setattr(config, "resolve_configured_repositories", lambda **_kwargs: (repository,))
    baseline = tmp_path / "baseline.json"
    with pytest.raises(SystemExit) as result:
        atk.main(
            [
                "wi",
                "list",
                "--skip-pull",
                "--count",
                "--state=inbox",
                "--type=awi",
                "--source=human",
                *target,
                "--snapshot-output",
                str(baseline),
            ],
            home=tmp_path,
        )
    assert result.value.code == 0
    assert capsys.readouterr().out == "1\n"
    saved = baseline.read_bytes()
    records = json.loads(saved)
    names = {item["filename"] for item in records}
    assert names == {
        "inbox.md",
        "processing.md",
        "hold.md",
        "adopted.md",
        "rejected.md",
        "unanswered.md",
        "20261110-123456-001.md",
        "20261110-083456-001.md",
    } | (set() if configured else {"other.md"})
    assert all(set(item) == {"target_repo", "filename"} for item in records)
    for path in notes.rglob("*.md"):
        text = path.read_text(encoding="utf-8")
        for state in ("processing", "hold", "adopted", "rejected"):
            text = text.replace(f"state: {state}", "state: inbox")
        path.write_text(text, encoding="utf-8")
    new_names = {"20261110-123456-002.md", "20261109-233456-001.md"}
    for name in new_names:
        _write_awi_file(notes, name, source="human")
    _write_uwi_file(notes, "new-uwi.md", answer="回答", source="human")
    _write_awi_file(notes, "new-agent.md", source="agent")
    for output in ("--jsonl", "--summary-only", "--count", "--no-jsonl"):
        with pytest.raises(SystemExit) as result:
            atk.main(
                [
                    "wi",
                    "list",
                    "--skip-pull",
                    output,
                    *target,
                    "--state=inbox",
                    "--type=awi",
                    "--source=human",
                    "--new-since",
                    str(baseline),
                ],
                home=tmp_path,
            )
        assert result.value.code == 0
        captured = capsys.readouterr()
        assert not captured.err
        if output == "--count":
            assert captured.out == "2\n"
        elif output == "--no-jsonl":
            assert all(name in captured.out for name in new_names)
            assert "adopted.md" not in captured.out
        else:
            assert {json.loads(line)["filename"] for line in captured.out.splitlines()} == new_names
        assert baseline.read_bytes() == saved


@pytest.mark.parametrize(
    "content",
    [
        None,
        "{",
        "{}",
        '[{"filename":"x.md"}]',
        '[{"target_repo":3,"filename":"x.md"}]',
        '[{"target_repo":"bad","filename":"x.md"}]',
        '[{"target_repo":"","filename":"../x.md"}]',
    ],
)
def test_list_rejects_invalid_baseline(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], content: str | None
) -> None:
    """不正な基準と読取不能は空の新着として扱わず、保存物を保持して診断する。"""
    _setup_notes(tmp_path)
    monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))
    baseline = tmp_path / "baseline.json"
    if content is not None:
        baseline.write_text(content, encoding="utf-8")
    with pytest.raises(SystemExit) as result:
        atk.main(["wi", "list", "--skip-pull", "--new-since", str(baseline)], home=tmp_path)
    assert result.value.code != 0
    captured = capsys.readouterr()
    assert "開始時集合" in captured.err and str(baseline) in captured.err
    assert not captured.out
    if content is not None:
        assert baseline.read_text(encoding="utf-8") == content
    else:
        assert not baseline.exists()


def test_list_snapshot_normalizes_repo_and_preserves_broken_entries(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """URL表記の変更と破損項目を初回集合に保持し、空集合にも対応する。"""
    notes = _setup_notes(tmp_path)
    monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))
    baseline = tmp_path / "baseline.json"
    command = ["wi", "list", "--skip-pull", "--target-repo=all", "--count"]
    with pytest.raises(SystemExit) as result:
        atk.main([*command, "--snapshot-output", str(baseline)], home=tmp_path)
    assert result.value.code == 0
    assert not json.loads(baseline.read_text(encoding="utf-8"))
    capsys.readouterr()
    item = _write_awi_file(notes, "url.md", target_repo="https://GitHub.com/Example/Foo.git")
    broken = _write_awi_file(notes, "broken.md")
    broken.write_text("---\n[broken\n", encoding="utf-8")
    with pytest.raises(SystemExit) as result:
        atk.main([*command, "--snapshot-output", str(baseline)], home=tmp_path)
    assert result.value.code == 0
    assert {tuple(sorted(record.items())) for record in json.loads(baseline.read_text(encoding="utf-8"))} == {
        (("filename", "url.md"), ("target_repo", "github.com/example/foo")),
        (("filename", "broken.md"), ("target_repo", "")),
    }
    capsys.readouterr()
    item.write_text(
        item.read_text(encoding="utf-8").replace("https://GitHub.com/Example/Foo.git", "github.com/example/foo"),
        encoding="utf-8",
    )
    with pytest.raises(SystemExit) as result:
        atk.main([*command, "--new-since", str(baseline)], home=tmp_path)
    assert result.value.code == 0
    assert capsys.readouterr().out == "0\n"
