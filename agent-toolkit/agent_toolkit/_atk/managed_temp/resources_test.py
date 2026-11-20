"""再生成指定と使用参照の境界を確認する。回収の副作用と実Git・子プロセスは実行しない。"""

from __future__ import annotations

import pathlib
from types import SimpleNamespace

import pytest

from agent_toolkit._atk.managed_temp import resources


@pytest.mark.parametrize("kind", ["venv", "node", "rust", "cache"])
@pytest.mark.parametrize("state", ["unused", "missing-source", "busy", "unknown", "tracked", "link"])
def test_cleanup_selection_preserves_sources_and_live_resources(tmp_path, monkeypatch, kind, state):
    """生成指定の欠落、使用中・不明・Git原本・リンクを、未使用の再生成物から区別する。"""
    project = tmp_path / "owned"
    project.mkdir()
    name, source, marker = {
        "venv": ("custom-venv", "uv.lock", "pyvenv.cfg"),
        "node": ("node_modules", "package-lock.json", None),
        "rust": ("target", "Cargo.lock", None),
        "cache": ("custom-cache", None, "CACHEDIR.TAG"),
    }[kind]
    target = project / name
    target.mkdir()
    (project / "package.json").write_text("{}", encoding="utf-8")
    (project / "Cargo.toml").write_text("[package]", encoding="utf-8")
    if source is not None:
        (project / source).write_text("rebuild input", encoding="utf-8")
    if marker is not None:
        (target / marker).write_text(
            "Signature: 8a477f597d28d172789f06886806bc55\n" if kind == "cache" else "home=python\n",
            encoding="ascii",
        )
    if state == "missing-source":
        (project / source if source else target / marker).unlink()
    if state == "link":
        moved = tmp_path / "outside"
        target.rename(moved)
        target.symlink_to(moved, target_is_directory=True)
    if state == "tracked":
        (project / ".git").write_text("fixture", encoding="ascii")

        def git_result(argv, *_args, **_kwargs):
            return SimpleNamespace(stdout="tracked-source" if argv[0] == "ls-files" else "", returncode=0)

        monkeypatch.setattr(resources.git_command, "run", git_result)
    removed: list[pathlib.Path] = []
    monkeypatch.setattr(resources, "shutil", SimpleNamespace(rmtree=removed.append))
    references = None if state == "unknown" else [project / "running.py"] if state == "busy" else []
    result = resources.cleanup_regenerable(project, references=lambda: references)
    assert removed == ([target] if state == "unused" else [])
    assert result["removed"] == [str(path) for path in removed]
    assert not any(item.get("failed") for item in result["retained"])
    if state in {"tracked", "link"}:
        assert result["retained"] and all(not item["retryable"] for item in result["retained"])
    elif state in {"busy", "unknown"}:
        assert result["retained"] and all(item["retryable"] for item in result["retained"])
    assert target.exists()  # 副作用を実行せず、判定対象を原本のまま保つ。
