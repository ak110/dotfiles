"""宣言解析内の解決再利用と、要求境界での再取得を検証する。"""

import contextlib
import os
import pathlib
import time
import types
from typing import Any

import pytest

from agent_toolkit._common import read_triggers, read_triggers_resolution


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


def _os_with(**overrides: Any) -> types.SimpleNamespace:
    """列挙が使うOS操作のうち、指定したものだけを差し替える。"""
    names = ("stat", "scandir", "fspath", "fsencode", "path")
    return types.SimpleNamespace(**{name: overrides.get(name, getattr(os, name)) for name in names})


def _skill_tree(root: pathlib.Path, skills: range) -> None:
    for index in skills:
        (root / f"skill{index}" / "references").mkdir(parents=True, exist_ok=True)
        (root / f"skill{index}" / "SKILL.md").write_text("# 本文\n", encoding="utf-8")
        (root / f"skill{index}" / "references" / "guide.md").write_text("# 資料\n", encoding="utf-8")


def _settle(root: pathlib.Path, seconds_ago: int) -> None:
    """全ディレクトリの最後の変更から、時刻の刻み（ext3は1秒）より十分に時間が過ぎた状態にする。"""
    settled = (time.time_ns() // 1_000_000_000 - seconds_ago) * 1_000_000_000
    for directory in [root, *(path for path in root.rglob("*") if path.is_dir())]:
        os.utime(directory, ns=(settled, settled))


def test_unchanged_skill_tree_is_not_listed_again_regardless_of_size(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """変化の無いhookでは文書数を倍にしてもディレクトリを列挙せず、一覧は列挙した結果と一致する。"""
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "state"))
    listed: list[str] = []

    def counting_scandir(path):
        listed.append(os.fspath(path))
        return os.scandir(path)

    monkeypatch.setattr(read_triggers_resolution, "os", _os_with(scandir=counting_scandir))
    root = tmp_path / "skills"
    for count, seconds_ago in ((20, 60), (40, 30)):
        _skill_tree(root, range(count))
        _settle(root, seconds_ago)
        expected = sorted(root.rglob("*.md"))
        rounds = []
        for _hook in range(3):
            listed.clear()
            assert read_triggers.canonical_markdown_files(root) == expected
            rounds.append(len(listed))
        assert rounds == [1 + 2 * count, 0, 0]


def test_document_added_within_the_same_timestamp_tick_is_listed(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """更新時刻の刻みが粗いファイルシステムで、直前のhookと同じ刻みの中に追加した文書を次のhookで列挙する。"""
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "state"))
    tick = time.time_ns() // 1_000_000_000 * 1_000_000_000

    def coarse_stat(path):
        status = os.stat(path)
        return types.SimpleNamespace(st_ino=status.st_ino, st_mtime_ns=tick, st_ctime_ns=tick)

    monkeypatch.setattr(read_triggers_resolution, "os", _os_with(stat=coarse_stat))
    root = tmp_path / "skills"
    _skill_tree(root, range(2))
    assert len(read_triggers.canonical_markdown_files(root)) == 4
    added = root / "skill0" / "references" / "added.md"
    added.write_text("# 追加\n", encoding="utf-8")
    assert added in read_triggers.canonical_markdown_files(root)
