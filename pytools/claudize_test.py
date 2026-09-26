"""claudizeモジュールのテスト。"""

import logging
import subprocess
import sys
from pathlib import Path

import pytest

from pytools.claudize import claudize, main

# テスト用テンプレート本文
AGENT_TEMPLATE = "# カスタム指示\n\n## 基本原則\n\n- ルール1\n"
PROJECT_INSTRUCTIONS = "# プロジェクト指示\n"
# CLAUDE.local.mdがある場合に生成されるアダプターの期待値。Claude Codeが@AGENTS.mdを取り込み、markdownlintのMD041も満たす形。
ADAPTER = "# CLAUDE.md\n\n@AGENTS.md\n"


def _setup_template(tmp_path: Path) -> Path:
    """テンプレートディレクトリを作成し、配布対象ファイルを配置する。"""
    template_dir = tmp_path / "dotfiles" / "agent-toolkit" / "rules"
    template_dir.mkdir(parents=True)
    (template_dir / "01-agent.md").write_text(AGENT_TEMPLATE, encoding="utf-8")
    return template_dir


class TestRuleDistribution:
    """ルール配布の基本動作。"""

    def test_main_uses_repository_rules_dir(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """CLI経路でもリポジトリ内の配布元ルールを使用する。"""
        target = tmp_path / "project"
        target.mkdir()
        monkeypatch.chdir(target)
        monkeypatch.setattr(sys, "argv", ["claudize"])

        with pytest.raises(SystemExit) as excinfo:
            main()

        assert excinfo.value.code == 0
        rules_dir = target / ".claude" / "rules" / "agent-toolkit"
        assert (rules_dir / "01-agent.md").exists()

    def test_basic_deployment(self, tmp_path: Path) -> None:
        """配布元のファイルがそのまま配布先へコピーされる。"""
        template_dir = _setup_template(tmp_path)
        target = tmp_path / "project"
        target.mkdir()

        claudize(target, template_dir)

        rules_dir = target / ".claude" / "rules" / "agent-toolkit"
        assert (rules_dir / "01-agent.md").read_text(encoding="utf-8") == AGENT_TEMPLATE

    def test_extra_files_are_removed(self, tmp_path: Path) -> None:
        """配布先に余分なファイルがあっても同期で削除される。"""
        template_dir = _setup_template(tmp_path)
        target = tmp_path / "project"
        target.mkdir()

        rules_dir = target / ".claude" / "rules" / "agent-toolkit"
        rules_dir.mkdir(parents=True)
        stale = rules_dir / "obsolete.md"
        stale.write_text("# 旧ルール\n", encoding="utf-8")

        claudize(target, template_dir)

        assert not stale.exists(), "配布元に存在しないファイルが削除されていない"
        assert (rules_dir / "01-agent.md").exists()

    def test_legacy_agent_basics_dir_removed(self, tmp_path: Path) -> None:
        """旧 agent-basics ディレクトリが存在する場合は削除される。"""
        template_dir = _setup_template(tmp_path)
        target = tmp_path / "project"
        target.mkdir()

        legacy_dir = target / ".claude" / "rules" / "agent-basics"
        legacy_dir.mkdir(parents=True)
        (legacy_dir / "01-agent.md").write_text("# 旧配布\n", encoding="utf-8")

        claudize(target, template_dir)

        assert not legacy_dir.exists(), "旧 agent-basics ディレクトリが削除されていない"
        assert (target / ".claude" / "rules" / "agent-toolkit" / "01-agent.md").exists()

    def test_idempotent(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        """2回実行しても同じ結果になる。"""
        caplog.set_level(logging.INFO)
        template_dir = _setup_template(tmp_path)
        target = tmp_path / "project"
        target.mkdir()

        claudize(target, template_dir)
        expected_agent = (target / ".claude" / "rules" / "agent-toolkit" / "01-agent.md").read_text(encoding="utf-8")

        claudize(target, template_dir)
        actual_agent = (target / ".claude" / "rules" / "agent-toolkit" / "01-agent.md").read_text(encoding="utf-8")

        assert actual_agent == expected_agent

    def test_missing_template_exits(self, tmp_path: Path) -> None:
        """配布元が無ければ非ゼロ終了する。"""
        template_dir = tmp_path / "nonexistent"
        target = tmp_path / "project"
        target.mkdir()

        with pytest.raises(SystemExit):
            claudize(target, template_dir)


@pytest.fixture(autouse=True)
def _isolate_git_repository(monkeypatch: pytest.MonkeyPatch) -> None:
    """Gitのhook内で実行された場合も、テストのgitとclaudizeが一時リポジトリだけを対象にするようにする。"""
    for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR"):
        monkeypatch.delenv(name, raising=False)


def _git(*args: str | Path) -> str:
    """テスト用にgitを実行し、標準出力を返す。"""
    return subprocess.run(["git", *map(str, args)], capture_output=True, text=True, check=True, encoding="utf-8").stdout


def _init_repo(path: Path) -> Path:
    """空のGitリポジトリを作成する。"""
    path.mkdir(parents=True)
    _git("-C", path, "init", "-q")
    return path


def _exclude_lines(repo: Path) -> list[str]:
    """リポジトリの`info/exclude`の行を返す。"""
    return (repo / ".git" / "info" / "exclude").read_text(encoding="utf-8").splitlines()


class TestProjectInstructionMigration:
    """`AGENTS.md`への指示ファイル移行と、CLAUDE.local.mdがある場合の`CLAUDE.md`アダプター。"""

    @pytest.mark.parametrize("adapter", ["@AGENTS.md\n", "# CLAUDE.md\n\n@AGENTS.md\n"])
    def test_removes_known_adapter_without_local_instructions(self, tmp_path: Path, adapter: str) -> None:
        """CLAUDE.local.mdが無ければ既知のCLAUDE.mdアダプターを削除する。"""
        template_dir = _setup_template(tmp_path)
        target = tmp_path / "project"
        target.mkdir()
        (target / "AGENTS.md").write_text(PROJECT_INSTRUCTIONS, encoding="utf-8")
        (target / "CLAUDE.md").write_text(adapter, encoding="utf-8")

        claudize(target, template_dir)

        assert (target / "AGENTS.md").read_text(encoding="utf-8") == PROJECT_INSTRUCTIONS
        assert not (target / "CLAUDE.md").exists()

    def test_creates_untracked_adapter_when_local_instructions_exist(self, tmp_path: Path) -> None:
        """CLAUDE.local.mdがあればアダプターを置き、info/excludeで追跡対象から外す。再実行でも行を重ねない。"""
        template_dir = _setup_template(tmp_path)
        target = _init_repo(tmp_path / "project")
        (target / "AGENTS.md").write_text(PROJECT_INSTRUCTIONS, encoding="utf-8")
        (target / "CLAUDE.local.md").write_text("# 個人メモ\n", encoding="utf-8")

        claudize(target, template_dir)
        claudize(target, template_dir)

        assert (target / "AGENTS.md").read_text(encoding="utf-8") == PROJECT_INSTRUCTIONS
        assert (target / "CLAUDE.md").read_bytes() == ADAPTER.encode("utf-8")
        assert _exclude_lines(target).count("/CLAUDE.md") == 1
        assert _git("-C", target, "status", "--porcelain", "--", "CLAUDE.md") == ""

    def test_excludes_adapter_by_path_from_repository_root(self, tmp_path: Path) -> None:
        """サブディレクトリのプロジェクトでは、ルートからのパスで除外し、既存の除外行を保つ。"""
        template_dir = _setup_template(tmp_path)
        repo = _init_repo(tmp_path / "repo")
        (repo / ".git" / "info" / "exclude").write_text("*.log", encoding="utf-8")
        target = repo / "sub"
        target.mkdir()
        (target / "AGENTS.md").write_text(PROJECT_INSTRUCTIONS, encoding="utf-8")
        (target / "CLAUDE.local.md").write_text("# 個人メモ\n", encoding="utf-8")

        claudize(target, template_dir)

        assert _exclude_lines(repo) == ["*.log", "/sub/CLAUDE.md"]
        assert _git("-C", repo, "status", "--porcelain", "--", "sub/CLAUDE.md") == ""

    def test_excludes_adapter_in_linked_worktree_via_common_dir(self, tmp_path: Path) -> None:
        """linked worktreeで実行しても、全worktreeが共有するinfo/excludeへ書く。"""
        template_dir = _setup_template(tmp_path)
        repo = _init_repo(tmp_path / "repo")
        (repo / "AGENTS.md").write_text(PROJECT_INSTRUCTIONS, encoding="utf-8")
        _git("-C", repo, "add", "AGENTS.md")
        _git("-C", repo, "commit", "-q", "-m", "init")
        worktree = tmp_path / "wt"
        _git("-C", repo, "worktree", "add", "-q", worktree)
        (worktree / "CLAUDE.local.md").write_text("# 個人メモ\n", encoding="utf-8")

        claudize(worktree, template_dir)

        assert (worktree / "CLAUDE.md").read_text(encoding="utf-8") == ADAPTER
        assert "/CLAUDE.md" in _exclude_lines(repo)
        assert _git("-C", worktree, "status", "--porcelain", "--", "CLAUDE.md") == ""

    def test_creates_adapter_outside_git_repository(self, tmp_path: Path) -> None:
        """Gitの作業ツリー外でも、CLAUDE.local.mdがあればアダプターを置いて正常終了する。"""
        template_dir = _setup_template(tmp_path)
        target = tmp_path / "project"
        target.mkdir()
        (target / "AGENTS.md").write_text(PROJECT_INSTRUCTIONS, encoding="utf-8")
        (target / "CLAUDE.local.md").write_text("# 個人メモ\n", encoding="utf-8")

        claudize(target, template_dir)

        assert (target / "CLAUDE.md").read_text(encoding="utf-8") == ADAPTER

    @pytest.mark.parametrize("adapter", ["@AGENTS.md\n", "# CLAUDE.md\n\n@AGENTS.md\n"])
    def test_keeps_known_adapter_with_local_instructions(self, tmp_path: Path, adapter: str) -> None:
        """CLAUDE.local.mdがあれば既知のアダプターは本文を変えずに残す。"""
        template_dir = _setup_template(tmp_path)
        target = tmp_path / "project"
        target.mkdir()
        (target / "AGENTS.md").write_text(PROJECT_INSTRUCTIONS, encoding="utf-8")
        (target / "CLAUDE.md").write_text(adapter, encoding="utf-8")
        (target / "CLAUDE.local.md").write_text("# 個人メモ\n", encoding="utf-8")

        claudize(target, template_dir)

        assert (target / "CLAUDE.md").read_text(encoding="utf-8") == adapter

    def test_renames_claude_only_project(self, tmp_path: Path) -> None:
        """CLAUDE.md単体は本文を保ってAGENTS.mdへリネームする。"""
        template_dir = _setup_template(tmp_path)
        target = tmp_path / "project"
        target.mkdir()
        (target / "CLAUDE.md").write_text(PROJECT_INSTRUCTIONS, encoding="utf-8")

        claudize(target, template_dir)

        assert (target / "AGENTS.md").read_text(encoding="utf-8") == PROJECT_INSTRUCTIONS
        assert not (target / "CLAUDE.md").exists()

    def test_renames_claude_only_project_and_places_adapter_with_local_instructions(self, tmp_path: Path) -> None:
        """CLAUDE.local.mdがあれば、リネームと同じ実行でアダプターを置く。"""
        template_dir = _setup_template(tmp_path)
        target = tmp_path / "project"
        target.mkdir()
        (target / "CLAUDE.md").write_text(PROJECT_INSTRUCTIONS, encoding="utf-8")
        (target / "CLAUDE.local.md").write_text("# 個人メモ\n", encoding="utf-8")

        claudize(target, template_dir)

        assert (target / "AGENTS.md").read_text(encoding="utf-8") == PROJECT_INSTRUCTIONS
        assert (target / "CLAUDE.md").read_text(encoding="utf-8") == ADAPTER

    def test_migrates_legacy_agents_symlink(self, tmp_path: Path) -> None:
        """CLAUDE.md実体へのAGENTS.mdリンクはAGENTS.md実体へ置き換える。"""
        template_dir = _setup_template(tmp_path)
        target = tmp_path / "project"
        target.mkdir()
        (target / "CLAUDE.md").write_text(PROJECT_INSTRUCTIONS, encoding="utf-8")
        (target / "AGENTS.md").symlink_to("CLAUDE.md")

        claudize(target, template_dir)

        assert not (target / "AGENTS.md").is_symlink()
        assert (target / "AGENTS.md").read_text(encoding="utf-8") == PROJECT_INSTRUCTIONS
        assert not (target / "CLAUDE.md").exists()

    def test_removes_claude_symlink_to_agents(self, tmp_path: Path) -> None:
        """AGENTS.mdへのCLAUDE.mdリンクは削除する。"""
        template_dir = _setup_template(tmp_path)
        target = tmp_path / "project"
        target.mkdir()
        (target / "AGENTS.md").write_text(PROJECT_INSTRUCTIONS, encoding="utf-8")
        (target / "CLAUDE.md").symlink_to("AGENTS.md")

        claudize(target, template_dir)

        assert (target / "AGENTS.md").read_text(encoding="utf-8") == PROJECT_INSTRUCTIONS
        assert not (target / "CLAUDE.md").exists()

    def test_replaces_claude_symlink_with_adapter_file_with_local_instructions(self, tmp_path: Path) -> None:
        """CLAUDE.local.mdがあれば、AGENTS.mdへのCLAUDE.mdリンクを通常ファイルのアダプターへ置き換える。"""
        template_dir = _setup_template(tmp_path)
        target = tmp_path / "project"
        target.mkdir()
        (target / "AGENTS.md").write_text(PROJECT_INSTRUCTIONS, encoding="utf-8")
        (target / "CLAUDE.md").symlink_to("AGENTS.md")
        (target / "CLAUDE.local.md").write_text("# 個人メモ\n", encoding="utf-8")

        claudize(target, template_dir)

        assert not (target / "CLAUDE.md").is_symlink()
        assert (target / "CLAUDE.md").read_text(encoding="utf-8") == ADAPTER

    def test_leaves_agents_only_project_without_claude_md(self, tmp_path: Path) -> None:
        """CLAUDE.local.mdが無ければ、AGENTS.mdだけのプロジェクトへCLAUDE.mdを生成しない。"""
        template_dir = _setup_template(tmp_path)
        target = tmp_path / "project"
        target.mkdir()
        (target / "AGENTS.md").write_text(PROJECT_INSTRUCTIONS, encoding="utf-8")

        claudize(target, template_dir)

        assert (target / "AGENTS.md").read_text(encoding="utf-8") == PROJECT_INSTRUCTIONS
        assert not (target / "CLAUDE.md").exists()

    @pytest.mark.parametrize("scenario", ["independent", "wrong_symlink", "directory"])
    def test_rejects_unsupported_without_modifying_instructions(self, tmp_path: Path, scenario: str) -> None:
        """独立本文・誤リンク・ディレクトリは指示ファイルを変更せず失敗する。"""
        template_dir = _setup_template(tmp_path)
        target = tmp_path / "project"
        target.mkdir()
        agents = target / "AGENTS.md"
        claude = target / "CLAUDE.md"
        agents.write_text(PROJECT_INSTRUCTIONS, encoding="utf-8")
        if scenario == "independent":
            claude.write_text("# 独立指示\n", encoding="utf-8")
        elif scenario == "wrong_symlink":
            claude.symlink_to("OTHER.md")
        else:
            claude.mkdir()

        before_agents = agents.read_text(encoding="utf-8")
        with pytest.raises(SystemExit):
            claudize(target, template_dir)

        assert agents.read_text(encoding="utf-8") == before_agents
        assert claude.exists() or claude.is_symlink()


class TestClean:
    """`--clean` での削除動作。"""

    def test_clean_removes_agent_toolkit_and_legacy(self, tmp_path: Path) -> None:
        """--clean で agent-toolkit と旧 agent-basics の両方が削除される。"""
        template_dir = _setup_template(tmp_path)
        target = tmp_path / "project"
        target.mkdir()

        claudize(target, template_dir)

        # 旧ディレクトリも生成して削除対象に含める
        legacy_dir = target / ".claude" / "rules" / "agent-basics"
        legacy_dir.mkdir(parents=True)
        (legacy_dir / "01-agent.md").write_text("# 旧配布\n", encoding="utf-8")

        claudize(target, template_dir, clean=True)

        assert not (target / ".claude" / "rules" / "agent-toolkit").exists()
        assert not legacy_dir.exists()
        # 空になった rules/ と .claude/ も削除されている
        assert not (target / ".claude" / "rules").exists()
        assert not (target / ".claude").exists()

    def test_clean_when_absent_is_noop(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        """配布先が存在しなくてもエラーにならない。"""
        caplog.set_level(logging.INFO)
        template_dir = _setup_template(tmp_path)
        target = tmp_path / "project"
        target.mkdir()

        claudize(target, template_dir, clean=True)

        assert any("削除対象なし" in r.message for r in caplog.records)

    def test_clean_preserves_project_instructions(self, tmp_path: Path) -> None:
        """--cleanはAGENTS.mdとCLAUDE.mdに触れない。"""
        template_dir = _setup_template(tmp_path)
        target = tmp_path / "project"
        target.mkdir()
        (target / "AGENTS.md").write_text(PROJECT_INSTRUCTIONS, encoding="utf-8")
        (target / "CLAUDE.md").write_text("# 独立指示\n", encoding="utf-8")

        claudize(target, template_dir, clean=True)

        assert (target / "AGENTS.md").read_text(encoding="utf-8") == PROJECT_INSTRUCTIONS
        assert (target / "CLAUDE.md").read_text(encoding="utf-8") == "# 独立指示\n"
