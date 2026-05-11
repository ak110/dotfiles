"""codexizeモジュールのテスト。"""

import logging
import os
from pathlib import Path

import pytest

from pytools.codexize import _codexize

_CLAUDE_BODY = "# CLAUDE.md\n本文\n"
_ADAPTER_BODY = "# CLAUDE.md\n\n@AGENTS.md\n"
_LEGACY_ADAPTER_BODY = "@AGENTS.md\n"


def _setup_dir(tmp_path: Path, *, with_skills: bool = True) -> Path:
    """対象ディレクトリを作成し、必要に応じて.claude/skillsも配置する。"""
    target = tmp_path / "project"
    target.mkdir()
    if with_skills:
        skills_dir = target / ".claude" / "skills"
        skills_dir.mkdir(parents=True)
        (skills_dir / "dummy.md").write_text("# dummy\n", encoding="utf-8")
    return target


def _setup_state(target: Path, state: str) -> None:
    """指定された状態のAGENTS.md／CLAUDE.mdを配置する。"""
    agents = target / "AGENTS.md"
    claude = target / "CLAUDE.md"
    if state == "applied":
        agents.write_text(_CLAUDE_BODY, encoding="utf-8")
        claude.write_text(_ADAPTER_BODY, encoding="utf-8")
    elif state == "legacy_symlink":
        claude.write_text(_CLAUDE_BODY, encoding="utf-8")
        agents.symlink_to("CLAUDE.md")
    elif state == "unapplied":
        claude.write_text(_CLAUDE_BODY, encoding="utf-8")
    elif state == "partial":
        agents.write_text(_CLAUDE_BODY, encoding="utf-8")
    else:
        raise ValueError(f"unknown state: {state}")


def _assert_applied(target: Path) -> None:
    """新方式適用済みの状態を満たすことを確認する。"""
    agents = target / "AGENTS.md"
    claude = target / "CLAUDE.md"
    assert agents.is_file() and not agents.is_symlink()
    assert agents.read_text(encoding="utf-8") == _CLAUDE_BODY
    assert claude.is_file() and not claude.is_symlink()
    assert claude.read_text(encoding="utf-8") == _ADAPTER_BODY


def _assert_unapplied(target: Path) -> None:
    """未適用状態（CLAUDE.md単体実体）を満たすことを確認する。"""
    agents = target / "AGENTS.md"
    claude = target / "CLAUDE.md"
    assert not agents.exists()
    assert claude.is_file() and not claude.is_symlink()
    assert claude.read_text(encoding="utf-8") == _CLAUDE_BODY


_UNSUPPORTED_SCENARIOS = [
    "both_missing",
    "both_independent_regular",
    "claude_is_symlink",
    "agents_is_other_symlink",
    "agents_missing_claude_adapter",
]


def _apply_unsupported_scenario(target: Path, scenario: str) -> None:
    """自動回復対象外の状態をテスト用に配置する。"""
    agents = target / "AGENTS.md"
    claude = target / "CLAUDE.md"
    if scenario == "both_missing":
        return
    if scenario == "both_independent_regular":
        agents.write_text("# AGENTS 独立本文\n", encoding="utf-8")
        claude.write_text("# CLAUDE 独立本文\n", encoding="utf-8")
        return
    if scenario == "claude_is_symlink":
        agents.write_text(_CLAUDE_BODY, encoding="utf-8")
        claude.symlink_to("AGENTS.md")
        return
    if scenario == "agents_is_other_symlink":
        agents.symlink_to("wrong-target")
        claude.write_text(_CLAUDE_BODY, encoding="utf-8")
        return
    if scenario == "agents_missing_claude_adapter":
        claude.write_text(_LEGACY_ADAPTER_BODY, encoding="utf-8")
        return
    raise ValueError(f"unknown scenario: {scenario}")


class TestCodexize:
    """`codexize`実行による状態遷移。"""

    @pytest.mark.parametrize("state", ["applied", "legacy_symlink", "unapplied", "partial"])
    def test_transitions_to_applied(self, tmp_path: Path, state: str):
        """4種の入力状態のいずれからも新方式適用済みへ収束する。"""
        target = _setup_dir(tmp_path)
        _setup_state(target, state)

        _codexize(target)

        _assert_applied(target)
        skills_link = target / ".agents" / "skills"
        assert skills_link.is_symlink()
        assert os.readlink(skills_link) == "../.claude/skills"

    def test_idempotent(self, tmp_path: Path):
        """新方式適用済みの状態で再実行しても変化しない。"""
        target = _setup_dir(tmp_path)
        _setup_state(target, "unapplied")

        _codexize(target)
        _codexize(target)

        _assert_applied(target)

    def test_legacy_adapter_is_normalized(self, tmp_path: Path):
        """旧形式（`@AGENTS.md`1行のみ）のCLAUDE.mdは新形式の正規本文へ整形される。"""
        target = _setup_dir(tmp_path, with_skills=False)
        (target / "AGENTS.md").write_text(_CLAUDE_BODY, encoding="utf-8")
        (target / "CLAUDE.md").write_text(_LEGACY_ADAPTER_BODY, encoding="utf-8")

        _codexize(target)

        _assert_applied(target)

    def test_skills_missing_skipped(self, tmp_path: Path, caplog: pytest.LogCaptureFixture):
        """.claude/skillsが無い場合は.agents/skillsを作成しない。"""
        caplog.set_level(logging.INFO)
        target = _setup_dir(tmp_path, with_skills=False)
        _setup_state(target, "unapplied")

        _codexize(target)

        _assert_applied(target)
        assert not (target / ".agents").exists()
        assert any("スキップ" in r.message for r in caplog.records)

    def test_existing_skills_directory_errors(self, tmp_path: Path):
        """.agents/skillsとして実ディレクトリが存在する場合はエラー終了する。"""
        target = _setup_dir(tmp_path)
        _setup_state(target, "unapplied")
        (target / ".agents" / "skills").mkdir(parents=True)

        with pytest.raises(SystemExit):
            _codexize(target)

    @pytest.mark.parametrize("scenario", _UNSUPPORTED_SCENARIOS)
    def test_unsupported_states_exit(self, tmp_path: Path, scenario: str):
        """自動回復対象外の状態は非ゼロ終了する。"""
        target = _setup_dir(tmp_path, with_skills=False)
        _apply_unsupported_scenario(target, scenario)

        with pytest.raises(SystemExit):
            _codexize(target)


class TestClean:
    """`--clean`による状態遷移。"""

    @pytest.mark.parametrize("state", ["applied", "legacy_symlink", "unapplied", "partial"])
    def test_transitions_to_unapplied(self, tmp_path: Path, state: str):
        """4種の入力状態のいずれからもCLAUDE.md単体実体の状態へ戻る。"""
        target = _setup_dir(tmp_path)
        _setup_state(target, state)

        _codexize(target, clean=True)

        _assert_unapplied(target)
        assert not (target / ".agents").exists()

    def test_unapplied_logs_noop(self, tmp_path: Path, caplog: pytest.LogCaptureFixture):
        """未適用状態では削除対象なしのログのみ出る。"""
        caplog.set_level(logging.INFO)
        target = _setup_dir(tmp_path, with_skills=False)
        _setup_state(target, "unapplied")

        _codexize(target, clean=True)

        _assert_unapplied(target)
        assert any("削除対象なし" in r.message for r in caplog.records)

    @pytest.mark.parametrize("scenario", _UNSUPPORTED_SCENARIOS)
    def test_unsupported_states_exit(self, tmp_path: Path, scenario: str):
        """自動回復対象外の状態は非ゼロ終了する。"""
        target = _setup_dir(tmp_path, with_skills=False)
        _apply_unsupported_scenario(target, scenario)

        with pytest.raises(SystemExit):
            _codexize(target, clean=True)
