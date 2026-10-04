"""公開操作の観測メタデータ設定・解除とprocessingの本文保護を実Gitで確認する。"""

import json
import pathlib
import subprocess

import pytest

from agent_toolkit import atk
from agent_toolkit._atk.wi import frontmatter


def git(path: pathlib.Path, *args: str) -> str:
    """所有する一時repoのGitを実行する。"""
    return subprocess.run(["git", *args], cwd=path, check=True, capture_output=True, text=True, timeout=30).stdout.strip()


def test_public_set_clear_and_body_protection(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """実装OIDを解決して保存・一覧で受領し、本文置換と不正OIDは保存前に拒否する。"""
    target, notes = tmp_path / "target", tmp_path / "notes"
    for repo in (target, notes):
        repo.mkdir()
        git(repo, "init")
        git(repo, "config", "user.name", "Test")
        git(repo, "config", "user.email", "test@example.invalid")
        git(repo, "commit", "--allow-empty", "-m", "base")
    git(target, "remote", "add", "origin", "https://github.com/example/project.git")
    remote = tmp_path / "notes-remote.git"
    git(tmp_path, "init", "--bare", str(remote))
    git(notes, "remote", "add", "origin", str(remote))
    git(notes, "push", "-u", "origin", "HEAD")
    filename = "20260930-175957-001.md"
    processing = notes / "processing"
    processing.mkdir()
    path = processing / filename
    body = "# 既存要求\n\n## 完成条件\n- 候補0件の短絡を観測する\n"
    path.write_text(
        frontmatter.serialize_frontmatter({"type": "awi", "target_repo": "github.com/example/project"}, body), encoding="utf-8"
    )
    git(notes, "add", ".")
    git(notes, "commit", "-m", "input")
    monkeypatch.setenv("AGENT_TOOLKIT_PRIVATE_NOTES", str(notes))
    monkeypatch.setenv("AI_AGENT", "1")
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    common = ["wi", "set-observation-wait", filename, "--target-repo", str(target)]
    settings = ["--condition", "selection-empty", "--plan-file", "30-1849_process-wi_レーン02.md", "--commit", "HEAD"]
    with pytest.raises(SystemExit, match="0"):
        atk.main([*common, *settings])
    assert capsys.readouterr().out.startswith("成功: ")
    parsed = frontmatter.parse_frontmatter(path.read_text(encoding="utf-8"))
    assert parsed is not None and parsed[1] == body
    assert parsed[0]["observation_wait"]["commit"] == git(target, "rev-parse", "HEAD")
    with pytest.raises(SystemExit, match="0"):
        atk.main(["wi", "list", "--status=processable", "--target-repo", str(target), "--skip-pull"])
    listed = json.loads(capsys.readouterr().out)
    assert not listed["ready"] and listed["blocked_reason"] == "observation-wait"
    assert listed["observation_wait"] == parsed[0]["observation_wait"]
    saved = path.read_bytes()
    with pytest.raises(SystemExit, match="2"):
        atk.main([*common, *settings[:-1], "missing-commit"])
    assert path.read_bytes() == saved
    capsys.readouterr()
    replacement = tmp_path / "replacement.md"
    replacement.write_text("別の本文", encoding="utf-8")
    with pytest.raises(SystemExit, match="2"):
        atk.main(["wi", "edit", filename, "--body-file", str(replacement), "--target-repo", str(target)])
    assert path.read_bytes() == saved
    capsys.readouterr()
    with pytest.raises(SystemExit, match="0"):
        atk.main([*common, "--clear"])
    parsed = frontmatter.parse_frontmatter(path.read_text(encoding="utf-8"))
    assert parsed is not None and parsed[1] == body and "observation_wait" not in parsed[0]
