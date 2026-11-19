"""宣言解析内の解決再利用と、要求境界での再取得を検証する。"""

import contextlib
import pathlib
from typing import Any

import pytest

from agent_toolkit._common import read_triggers


def test_resolution_cache_is_request_local(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """同じ宣言内の再読を省き、次の宣言では削除・差し替え・新規作成を反映する。"""
    source = tmp_path / "source.md"
    source.write_text("# 本文\n", encoding="utf-8")
    original = pathlib.Path.read_text
    reads: list[pathlib.Path] = []

    def observe(path: pathlib.Path, *args: Any, **kwargs: Any) -> str:
        reads.append(path)
        return original(path, *args, **kwargs)

    monkeypatch.setattr(pathlib.Path, "read_text", observe)
    for _ in range(2):
        with read_triggers.resolution_scope():
            for _ in range(3):
                assert read_triggers.resolve_read_source(source, tmp_path) == source
    assert reads == [source, source]
    source.write_text(f"<!-- read-source: {'agent-toolkit'}:missing.md -->\n", encoding="utf-8")
    with read_triggers.resolution_scope(), pytest.raises(ValueError, match="実体が無い"):
        read_triggers.resolve_read_source(source, tmp_path)
    source.unlink()
    with read_triggers.resolution_scope():
        assert read_triggers.resolve_read_source(source, tmp_path) == source
    source.write_text("# 再作成\n", encoding="utf-8")
    with read_triggers.resolution_scope():
        assert read_triggers.resolve_read_source(source, tmp_path) == source
    assert reads[-1] == source


def test_cached_source_keeps_cycle_detection(tmp_path: pathlib.Path) -> None:
    """同じ資料を既に解決しても、訪問済み資料を別入力として循環を検査する。"""
    source = tmp_path / "source.md"
    source.write_text("# 本文\n", encoding="utf-8")
    with read_triggers.resolution_scope():
        assert read_triggers.resolve_read_source(source, tmp_path) == source
        with pytest.raises(ValueError, match="循環"):
            read_triggers.resolve_read_source(source, tmp_path, seen=(source,))


def test_canonical_markdown_files_returns_real_paths(tmp_path: pathlib.Path) -> None:
    """リンクの起点とリンクのファイルも実体のパスで返す。

    宣言の列挙の利用側は、この契約を前提に返されたパスを解決し直さない。
    """
    real = tmp_path / "real"
    (real / "sub").mkdir(parents=True)
    plain = real / "sub" / "plain.md"
    plain.write_text("# 本文\n", encoding="utf-8")
    target = tmp_path / "target.md"
    target.write_text("# 実体\n", encoding="utf-8")
    root = tmp_path / "root"
    try:
        root.symlink_to(real, target_is_directory=True)
        (real / "linked.md").symlink_to(target)
    except OSError as error:
        pytest.skip(f"このホストでリンク作成権限が無い: {error}")
    assert read_triggers.canonical_markdown_files(root) == [target.resolve(), plain.resolve()]


@pytest.mark.parametrize("scoped", [True, False], ids=["request", "standalone"])
def test_user_side_source_requires_skill_home_outside_plugins(tmp_path: pathlib.Path, scoped: bool) -> None:
    """スキル置き場の文書だけをユーザー側とし、祖先にplugin manifestを持つ文書は除く。要求内外で同じ判定にする。"""
    plugin = tmp_path / "plugin"
    (plugin / ".claude-plugin").mkdir(parents=True)
    (plugin / ".claude-plugin/plugin.json").write_text("{}", encoding="utf-8")
    cases = {
        tmp_path / "project/.claude/skills/example/SKILL.md": True,
        tmp_path / "home/.codex/skills/example/references/guide.md": True,
        plugin / ".claude/skills/example/SKILL.md": False,
        tmp_path / "project/docs/skills/guide.md": False,
        tmp_path / "project/AGENTS.md": False,
    }
    with read_triggers.resolution_scope() if scoped else contextlib.nullcontext():
        assert {path: read_triggers.user_side_source(path) for path in cases} == cases
