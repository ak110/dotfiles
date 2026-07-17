"""agent-toolkit/scripts/posttooluse.py の対象ファイル一覧行数警告関連テスト。

共通ヘルパー（`_run`・`_build_valid_plan`・`_prepare_plan_home`・`_write_plan`・`_parse_hook_output`）は
`posttooluse_plan_format_test.py`と複製で持つ。
"""

import json
import os
import pathlib
import subprocess

import _fork_runner
import _plan_format
import pytest

_SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "posttooluse.py"


def _run(
    payload: dict | str,
    *,
    state_dir: pathlib.Path | None = None,
    home_dir: pathlib.Path | None = None,
    plan_mode_skill_invoked: bool = False,
) -> subprocess.CompletedProcess[str]:
    text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    env = os.environ.copy()
    if state_dir is not None:
        env["TMPDIR"] = str(state_dir)
        env["TEMP"] = str(state_dir)
        env["TMP"] = str(state_dir)
    if home_dir is not None:
        env["HOME"] = str(home_dir)
    if plan_mode_skill_invoked and state_dir is not None and isinstance(payload, dict):
        sid = payload.get("session_id", "")
        if isinstance(sid, str) and sid:
            state_dir.mkdir(parents=True, exist_ok=True)
            (state_dir / f"claude-agent-toolkit-{sid}.json").write_text(
                json.dumps({"plan_mode_skill_invoked": True}, ensure_ascii=False),
                encoding="utf-8",
            )
    return _fork_runner.run_script(_SCRIPT, input=text, env=env)


_PLAN_BODY: dict[str, str] = {
    "変更履歴": "- 初版",
    "背景": "説明。",
    "対応方針": "### ユーザー合意済み事項\n\n- a",
    "調査結果": "- x",
    "変更内容": "### 対象ファイル一覧\n\n- y",
    "実行方法": "- w",
    "進捗ログ": "初版時点では実装未着手のため空欄。",
    "計画ファイル（本ファイル）のパス": "`~/.claude/plans/xxx.md`",
}


def _build_valid_plan(
    omit: tuple[str, ...] = (),
    *,
    overrides: dict[str, str] | None = None,
    prefix: str = "",
) -> str:
    """必須セクション順序に従い妥当なplan file内容を生成する。"""
    section_order: tuple[str, ...] = _plan_format.PLAN_REQUIRED_H2
    overrides = overrides or {}
    parts: list[str] = ["# タイトル", ""]
    for h2 in section_order:
        if h2 in omit:
            continue
        parts.append(f"## {h2}")
        parts.append("")
        parts.append(overrides.get(h2, _PLAN_BODY[h2]))
        parts.append("")
    return prefix + "\n".join(parts) + "\n"


def _prepare_plan_home(home_dir: pathlib.Path) -> pathlib.Path:
    """`<home>/.claude/plans`を作成してパスを返す。"""
    plans = home_dir / ".claude" / "plans"
    plans.mkdir(parents=True, exist_ok=True)
    return plans


def _write_plan(plans_dir: pathlib.Path, name: str, content: str) -> pathlib.Path:
    path = plans_dir / name
    path.write_text(content, encoding="utf-8")
    return path


def _parse_hook_output(stdout: str) -> dict | None:
    stdout = stdout.strip()
    if not stdout:
        return None
    return json.loads(stdout)


class TestPlanFormatTargetFileLineCount:
    """計画ファイル対象ファイル一覧の行数警告検査。"""

    def _home(self, tmp_path: pathlib.Path) -> tuple[pathlib.Path, pathlib.Path]:
        home = tmp_path / "home"
        plans = _prepare_plan_home(home)
        return home, plans

    def _make_over_limit_file(self, path: pathlib.Path) -> None:
        """221行の対象ファイルを生成する（`> 220`の警告閾値超過を発火させる）。"""
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(f"line {i}" for i in range(1, 222)), encoding="utf-8")

    def _plan_with_files(self, rel_paths: list[str]) -> str:
        items = "\n".join(f"- [ ] {p}" for p in rel_paths)
        return _build_valid_plan(overrides={"変更内容": f"### 対象ファイル一覧\n\n{items}"})

    def test_empty_target_file_list_passes_silently(self, tmp_path: pathlib.Path):
        """対象ファイル一覧のチェックボックスが0件の場合は警告されない。"""
        home, plans = self._home(tmp_path)
        content = _build_valid_plan(overrides={"変更内容": "### 対象ファイル一覧\n\n（対象ファイルなし）"})
        plan = _write_plan(plans, "empty-list.md", content)
        result = _run(
            {
                "session_id": "line-empty-list",
                "tool_name": "Write",
                "tool_input": {"file_path": str(plan), "content": content},
                "cwd": str(home),
            },
            state_dir=tmp_path / "state",
            home_dir=home,
            plan_mode_skill_invoked=True,
        )
        assert result.returncode == 0
        assert "does not conform" not in result.stdout

    def test_under_limit_passes_silently(self, tmp_path: pathlib.Path):
        """219行以下の対象種別ファイルは警告されない。"""
        home, plans = self._home(tmp_path)
        agents_md = home / "AGENTS.md"
        agents_md.write_text("\n".join(f"line {i}" for i in range(1, 220)), encoding="utf-8")
        content = self._plan_with_files(["AGENTS.md"])
        plan = _write_plan(plans, "under-limit.md", content)
        result = _run(
            {
                "session_id": "line-under",
                "tool_name": "Write",
                "tool_input": {"file_path": str(plan), "content": content},
                "cwd": str(home),
            },
            state_dir=tmp_path / "state",
            home_dir=home,
            plan_mode_skill_invoked=True,
        )
        assert result.returncode == 0
        assert "does not conform" not in result.stdout

    def test_nonexistent_file_passes_silently(self, tmp_path: pathlib.Path):
        """対象ファイルが存在しない場合は警告されない。"""
        home, plans = self._home(tmp_path)
        content = self._plan_with_files(["AGENTS.md"])
        plan = _write_plan(plans, "nonexist.md", content)
        result = _run(
            {
                "session_id": "line-nonexist",
                "tool_name": "Write",
                "tool_input": {"file_path": str(plan), "content": content},
                "cwd": str(home),
            },
            state_dir=tmp_path / "state",
            home_dir=home,
            plan_mode_skill_invoked=True,
        )
        assert result.returncode == 0
        assert "does not conform" not in result.stdout

    def test_non_agent_facing_file_passes_silently(self, tmp_path: pathlib.Path):
        """Pythonファイルなど対象種別外は220行以上でも警告されない。"""
        home, plans = self._home(tmp_path)
        self._make_over_limit_file(home / "src" / "main.py")
        content = self._plan_with_files(["src/main.py"])
        plan = _write_plan(plans, "non-agent.md", content)
        result = _run(
            {
                "session_id": "line-non-agent",
                "tool_name": "Write",
                "tool_input": {"file_path": str(plan), "content": content},
                "cwd": str(home),
            },
            state_dir=tmp_path / "state",
            home_dir=home,
            plan_mode_skill_invoked=True,
        )
        assert result.returncode == 0
        assert "does not conform" not in result.stdout

    def test_mixed_only_agent_facing_warned(self, tmp_path: pathlib.Path):
        """対象種別ファイルのみ警告され、対象種別外は警告されない。"""
        home, plans = self._home(tmp_path)
        self._make_over_limit_file(home / "agent-toolkit" / "rules" / "01-agent.md")
        self._make_over_limit_file(home / "src" / "main.py")
        content = self._plan_with_files(["agent-toolkit/rules/01-agent.md", "src/main.py"])
        plan = _write_plan(plans, "mixed.md", content)
        result = _run(
            {
                "session_id": "line-mixed",
                "tool_name": "Write",
                "tool_input": {"file_path": str(plan), "content": content},
                "cwd": str(home),
            },
            state_dir=tmp_path / "state",
            home_dir=home,
            plan_mode_skill_invoked=True,
        )
        output = _parse_hook_output(result.stdout)
        assert output is not None
        msg = output["hookSpecificOutput"]["additionalContext"]
        assert "plan file contains target files exceeding 220 lines" in msg
        assert "agent-toolkit/rules/01-agent.md" in msg
        assert "src/main.py" not in msg

    def test_both_checkbox_variants_extracted(self, tmp_path: pathlib.Path):
        """`- [ ]`と`- [x]`の両形式のチェックボックスからパスが抽出される。"""
        home, plans = self._home(tmp_path)
        self._make_over_limit_file(home / "AGENTS.md")
        self._make_over_limit_file(home / "CLAUDE.md")
        overrides = {"変更内容": "### 対象ファイル一覧\n\n- [ ] AGENTS.md\n- [x] CLAUDE.md"}
        content = _build_valid_plan(overrides=overrides)
        plan = _write_plan(plans, "checkbox-variants.md", content)
        result = _run(
            {
                "session_id": "line-checkbox",
                "tool_name": "Write",
                "tool_input": {"file_path": str(plan), "content": content},
                "cwd": str(home),
            },
            state_dir=tmp_path / "state",
            home_dir=home,
            plan_mode_skill_invoked=True,
        )
        output = _parse_hook_output(result.stdout)
        assert output is not None
        msg = output["hookSpecificOutput"]["additionalContext"]
        assert "plan file contains target files exceeding 220 lines" in msg
        assert "AGENTS.md" in msg
        assert "CLAUDE.md" in msg

    def test_skipped_when_skill_not_invoked(self, tmp_path: pathlib.Path):
        """plan_mode_skill_invokedが偽の場合は行数警告を出力しない。"""
        home, plans = self._home(tmp_path)
        self._make_over_limit_file(home / "AGENTS.md")
        content = self._plan_with_files(["AGENTS.md"])
        plan = _write_plan(plans, "no-skill.md", content)
        result = _run(
            {
                "session_id": "line-no-skill",
                "tool_name": "Write",
                "tool_input": {"file_path": str(plan), "content": content},
                "cwd": str(home),
            },
            state_dir=tmp_path / "state",
            home_dir=home,
            plan_mode_skill_invoked=False,
        )
        assert result.returncode == 0
        assert "does not conform" not in result.stdout

    @pytest.mark.parametrize(
        ("rel_path", "test_id"),
        [
            ("AGENTS.md", "root-agents-md"),
            ("CLAUDE.md", "root-claude-md"),
            ("agent-toolkit/rules/01-agent.md", "rules-direct"),
            (".chezmoi-source/dot_claude/rules/agent-toolkit/03-claude-code.md", "rules-nested"),
            ("agent-toolkit/skills/plan-mode/SKILL.md", "skills-skill-md"),
            ("agent-toolkit/skills/plan-mode/references/norm-revision-checklist.md", "skills-references"),
            ("agent-toolkit/agents/plan-impl-reviewer.md", "agents"),
        ],
    )
    def test_agent_facing_type_220_lines_is_warned(self, tmp_path: pathlib.Path, rel_path: str, test_id: str):
        """対象種別の各パターンで220行以上のファイルが警告される。"""
        home, plans = self._home(tmp_path)
        self._make_over_limit_file(home / pathlib.Path(rel_path))
        content = self._plan_with_files([rel_path])
        plan = _write_plan(plans, f"{test_id}.md", content)
        result = _run(
            {
                "session_id": f"line-type-{test_id}",
                "tool_name": "Write",
                "tool_input": {"file_path": str(plan), "content": content},
                "cwd": str(home),
            },
            state_dir=tmp_path / "state",
            home_dir=home,
            plan_mode_skill_invoked=True,
        )
        output = _parse_hook_output(result.stdout)
        assert output is not None
        msg = output["hookSpecificOutput"]["additionalContext"]
        assert "plan file contains target files exceeding 220 lines" in msg
        assert rel_path in msg

    def test_reduction_heading_excludes_full_path(self, tmp_path: pathlib.Path):
        """`#### 縮減対象（<完全パス>）`H4見出しが存在する場合は警告対象から除外される。"""
        home, plans = self._home(tmp_path)
        self._make_over_limit_file(home / "AGENTS.md")
        overrides = {
            "変更内容": "### 対象ファイル一覧\n\n- [ ] AGENTS.md\n\n#### 縮減対象（AGENTS.md）\n\n- 具体的な縮減方針",
        }
        content = _build_valid_plan(overrides=overrides)
        plan = _write_plan(plans, "reduction-fullpath.md", content)
        result = _run(
            {
                "session_id": "line-reduction-full",
                "tool_name": "Write",
                "tool_input": {"file_path": str(plan), "content": content},
                "cwd": str(home),
            },
            state_dir=tmp_path / "state",
            home_dir=home,
            plan_mode_skill_invoked=True,
        )
        assert result.returncode == 0
        assert "does not conform" not in result.stdout

    def test_repeat_write_suppresses_line_count_warning(self, tmp_path: pathlib.Path):
        """同一計画ファイルへの2回目のWriteでは行数超過警告のみ抑止される（FB2）。"""
        home, plans = self._home(tmp_path)
        self._make_over_limit_file(home / "AGENTS.md")
        content = self._plan_with_files(["AGENTS.md"])
        plan = _write_plan(plans, "repeat-suppress.md", content)
        state_dir = tmp_path / "state"
        payload = {
            "session_id": "line-repeat-suppress",
            "tool_name": "Write",
            "tool_input": {"file_path": str(plan), "content": content},
            "cwd": str(home),
        }
        first = _run(payload, state_dir=state_dir, home_dir=home, plan_mode_skill_invoked=True)
        first_output = _parse_hook_output(first.stdout)
        assert first_output is not None
        first_msg = first_output["hookSpecificOutput"]["additionalContext"]
        assert "plan file contains target files exceeding 220 lines" in first_msg

        second = _run(payload, state_dir=state_dir, home_dir=home)
        second_output = _parse_hook_output(second.stdout)
        if second_output is not None:
            second_msg = second_output["hookSpecificOutput"]["additionalContext"]
            assert "plan file contains target files exceeding 220 lines" not in second_msg

    def test_different_plan_path_warns_independently(self, tmp_path: pathlib.Path):
        """異なる計画パスへは行数超過警告が独立に発火する（辞書型フラグの分離性、FB2）。"""
        home, plans = self._home(tmp_path)
        self._make_over_limit_file(home / "AGENTS.md")
        content = self._plan_with_files(["AGENTS.md"])
        plan_a = _write_plan(plans, "repeat-a.md", content)
        plan_b = _write_plan(plans, "repeat-b.md", content)
        state_dir = tmp_path / "state"
        session_id = "line-repeat-independent"
        payload_a = {
            "session_id": session_id,
            "tool_name": "Write",
            "tool_input": {"file_path": str(plan_a), "content": content},
            "cwd": str(home),
        }
        payload_b = {
            "session_id": session_id,
            "tool_name": "Write",
            "tool_input": {"file_path": str(plan_b), "content": content},
            "cwd": str(home),
        }
        _run(payload_a, state_dir=state_dir, home_dir=home, plan_mode_skill_invoked=True)
        second = _run(payload_b, state_dir=state_dir, home_dir=home)
        second_output = _parse_hook_output(second.stdout)
        assert second_output is not None
        second_msg = second_output["hookSpecificOutput"]["additionalContext"]
        assert "plan file contains target files exceeding 220 lines" in second_msg

    def test_replacement_pair_diff_excludes_target(self, tmp_path: pathlib.Path):
        """`[現行]`/`[置換後]`ペアで縮減量が計上済みの場合はH4見出しがなくても警告対象から除外される。"""
        home, plans = self._home(tmp_path)
        self._make_over_limit_file(home / "AGENTS.md")
        overrides = {
            "変更内容": (
                "### 対象ファイル一覧\n\n- [ ] AGENTS.md\n\n"
                "### `AGENTS.md`\n\n"
                "```text\n[現行]\nold1\nold2\nold3\n```\n\n"
                "```text\n[置換後]\nnew1\n```\n"
            ),
        }
        content = _build_valid_plan(overrides=overrides)
        plan = _write_plan(plans, "replacement-pair.md", content)
        result = _run(
            {
                "session_id": "line-replacement-pair",
                "tool_name": "Write",
                "tool_input": {"file_path": str(plan), "content": content},
                "cwd": str(home),
            },
            state_dir=tmp_path / "state",
            home_dir=home,
            plan_mode_skill_invoked=True,
        )
        assert result.returncode == 0
        assert "does not conform" not in result.stdout

    def test_reduction_heading_excludes_basename(self, tmp_path: pathlib.Path):
        """`#### 縮減対象（<basename>）`H4見出しが存在する場合も警告対象から除外される。"""
        home, plans = self._home(tmp_path)
        self._make_over_limit_file(home / "agent-toolkit" / "rules" / "01-agent.md")
        overrides = {
            "変更内容": (
                "### 対象ファイル一覧\n\n- [ ] agent-toolkit/rules/01-agent.md\n\n"
                "#### 縮減対象（01-agent.md）\n\n- 具体的な縮減方針"
            ),
        }
        content = _build_valid_plan(overrides=overrides)
        plan = _write_plan(plans, "reduction-basename.md", content)
        result = _run(
            {
                "session_id": "line-reduction-basename",
                "tool_name": "Write",
                "tool_input": {"file_path": str(plan), "content": content},
                "cwd": str(home),
            },
            state_dir=tmp_path / "state",
            home_dir=home,
            plan_mode_skill_invoked=True,
        )
        assert result.returncode == 0
        assert "does not conform" not in result.stdout
