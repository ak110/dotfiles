"""agent-toolkit/scripts/posttooluse.py のplan file形式検査関連テスト。

`posttooluse_test.py`本体から計画ファイル形式検査・codex-review.md読み込み検出を分割した。
共通ヘルパー（`_run`・`_read_state`）は分割先で複製する。
ハンドラ網羅と機械チェック上限のバランス確保が分割の動機。
H2節順検査（必須H2欠落・順序違反・予期せぬH2）はPreToolUseへ移管済み（`_plan_format.py`・`pretooluse.py`参照）。
"""

import json
import os
import pathlib
import subprocess
import sys

import _plan_format

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
    # plan file形式検査はplan_mode_skill_invokedが真の場合のみ実行されるため、
    # 形式検査を期待するテストでは事前に状態ファイルへ同フラグを書き込んでおく。
    if plan_mode_skill_invoked and state_dir is not None and isinstance(payload, dict):
        sid = payload.get("session_id", "")
        if isinstance(sid, str) and sid:
            state_dir.mkdir(parents=True, exist_ok=True)
            (state_dir / f"claude-agent-toolkit-{sid}.json").write_text(
                json.dumps({"plan_mode_skill_invoked": True}, ensure_ascii=False),
                encoding="utf-8",
            )
    return subprocess.run(
        [sys.executable, str(_SCRIPT)],
        input=text,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


def _read_state(state_dir: pathlib.Path, session_id: str) -> dict:
    path = state_dir / f"claude-agent-toolkit-{session_id}.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


# plan file形式検査で使う各種Markdown断片。テスト全体で共用する。
# `## 対応方針`配下には判断材料H3を含めて妥当なplan構造を再現する。
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
    """必須セクション順序に従い妥当なplan file内容を生成する。

    - `omit`: 指定したH2セクションを省略する（必須セクション欠落の検証用）。
    - `overrides`: 指定したH2セクションの本文を差し替える
      （コードフェンス・HTMLコメントなど特定本文での無視判定検証用）。
    - `prefix`: 戻り値の先頭に連結する文字列（YAMLフロントマターなどの検証用）。
    """
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


_VALID_PLAN = _build_valid_plan()


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


class TestPlanFormatCheck:
    """plan file形式検査。"""

    def _home(self, tmp_path: pathlib.Path) -> tuple[pathlib.Path, pathlib.Path]:
        home = tmp_path / "home"
        plans = _prepare_plan_home(home)
        return home, plans

    def test_valid_plan_passes_silently(self, tmp_path: pathlib.Path):
        home, plans = self._home(tmp_path)
        plan = _write_plan(plans, "sample.md", _VALID_PLAN)
        result = _run(
            {
                "session_id": "plan-ok",
                "tool_name": "Write",
                "tool_input": {"file_path": str(plan), "content": _VALID_PLAN},
            },
            state_dir=tmp_path / "state",
            home_dir=home,
            plan_mode_skill_invoked=True,
        )
        assert result.returncode == 0
        assert result.stdout.strip() == ""

    def test_review_md_is_skipped(self, tmp_path: pathlib.Path):
        home, plans = self._home(tmp_path)
        content = "# レビュー\n\nなにか書く。\n"
        plan = _write_plan(plans, "sample.review.md", content)
        result = _run(
            {
                "session_id": "plan-review",
                "tool_name": "Write",
                "tool_input": {"file_path": str(plan), "content": content},
            },
            state_dir=tmp_path / "state",
            home_dir=home,
            plan_mode_skill_invoked=True,
        )
        assert result.stdout.strip() == ""

    def test_codex_log_is_skipped(self, tmp_path: pathlib.Path):
        home, plans = self._home(tmp_path)
        log_path = plans / "sample.codex.log"
        log_path.write_text("codex output...", encoding="utf-8")
        result = _run(
            {
                "session_id": "plan-log",
                "tool_name": "Write",
                "tool_input": {"file_path": str(log_path), "content": "codex output..."},
            },
            state_dir=tmp_path / "state",
            home_dir=home,
            plan_mode_skill_invoked=True,
        )
        assert result.stdout.strip() == ""

    def test_non_plans_path_is_skipped(self, tmp_path: pathlib.Path):
        home = tmp_path / "home"
        home.mkdir()
        other = tmp_path / "other.md"
        other.write_text("# 無関係\n", encoding="utf-8")
        result = _run(
            {
                "session_id": "plan-other",
                "tool_name": "Write",
                "tool_input": {"file_path": str(other), "content": "# 無関係\n"},
            },
            state_dir=tmp_path / "state",
            home_dir=home,
            plan_mode_skill_invoked=True,
        )
        assert result.stdout.strip() == ""

    def test_subdirectory_plan_is_skipped(self, tmp_path: pathlib.Path):
        """`~/.claude/plans/` のサブディレクトリ配下は対象外 (直下のみ検査)。"""
        home, plans = self._home(tmp_path)
        sub = plans / "archive"
        sub.mkdir()
        plan = sub / "old.md"
        plan.write_text("# 古い計画\n", encoding="utf-8")
        result = _run(
            {
                "session_id": "plan-sub",
                "tool_name": "Write",
                "tool_input": {"file_path": str(plan), "content": "# 古い計画\n"},
            },
            state_dir=tmp_path / "state",
            home_dir=home,
            plan_mode_skill_invoked=True,
        )
        assert result.stdout.strip() == ""

    def test_skipped_when_skill_not_invoked(self, tmp_path: pathlib.Path):
        """``plan_mode_skill_invoked`` 未設定時は plan file の構造検査をスキップする。

        PreToolUse 側で plan-mode スキル先行呼び出しが既に促されているため、
        構造検査の二重警告を避ける。
        """
        home, plans = self._home(tmp_path)
        # 必須セクションが欠落した plan を書いても、フラグ未設定なら警告しない。
        content = "# タイトル\n\n## 背景\n\n説明。\n"
        plan = _write_plan(plans, "no-skill.md", content)
        result = _run(
            {
                "session_id": "plan-no-skill",
                "tool_name": "Write",
                "tool_input": {"file_path": str(plan), "content": content},
            },
            state_dir=tmp_path / "state",
            home_dir=home,
        )
        assert result.returncode == 0
        assert result.stdout.strip() == ""

    def test_bash_does_not_emit_plan_check(self, tmp_path: pathlib.Path):
        """Bash ツールでは plan check が実行されず stdout が空のまま。"""
        result = _run(
            {
                "session_id": "bash-silent",
                "tool_name": "Bash",
                "tool_input": {"command": "pytest"},
            },
            state_dir=tmp_path,
        )
        assert result.stdout == ""

    def test_valid_plan_with_h3_passes_silently(self, tmp_path: pathlib.Path):
        """## 変更内容 配下の先頭H3が「対象ファイル一覧」のとき違反なし。"""
        home, plans = self._home(tmp_path)
        content = _build_valid_plan(
            overrides={"変更内容": "### 対象ファイル一覧\n\n- z"},
        )
        plan = _write_plan(plans, "h3-valid.md", content)
        result = _run(
            {
                "session_id": "h3-valid",
                "tool_name": "Write",
                "tool_input": {"file_path": str(plan), "content": content},
            },
            state_dir=tmp_path / "state",
            home_dir=home,
            plan_mode_skill_invoked=True,
        )
        assert result.returncode == 0
        assert result.stdout.strip() == ""

    def test_no_h3_under_changes_section_is_warned(self, tmp_path: pathlib.Path):
        """## 変更内容 配下にH3が存在しないとき違反として警告される。"""
        home, plans = self._home(tmp_path)
        content = _build_valid_plan(
            overrides={"変更内容": "- no h3 here"},
        )
        plan = _write_plan(plans, "h3-missing.md", content)
        result = _run(
            {
                "session_id": "h3-miss",
                "tool_name": "Write",
                "tool_input": {"file_path": str(plan), "content": content},
            },
            state_dir=tmp_path / "state",
            home_dir=home,
            plan_mode_skill_invoked=True,
        )
        output = _parse_hook_output(result.stdout)
        assert output is not None
        msg = output["hookSpecificOutput"]["additionalContext"]
        assert "the first H3 under '## 変更内容' must be '対象ファイル一覧'" in msg
        assert "(no H3 present)" in msg

    def test_wrong_first_h3_under_changes_section_is_warned(self, tmp_path: pathlib.Path):
        """## 変更内容 配下の先頭H3が「対象ファイル一覧」以外のとき違反として警告される。"""
        home, plans = self._home(tmp_path)
        content = _build_valid_plan(
            overrides={"変更内容": "### 対象ファイル\n\n- z"},
        )
        plan = _write_plan(plans, "h3-wrong.md", content)
        result = _run(
            {
                "session_id": "h3-wrong",
                "tool_name": "Write",
                "tool_input": {"file_path": str(plan), "content": content},
            },
            state_dir=tmp_path / "state",
            home_dir=home,
            plan_mode_skill_invoked=True,
        )
        output = _parse_hook_output(result.stdout)
        assert output is not None
        msg = output["hookSpecificOutput"]["additionalContext"]
        assert "the first H3 under '## 変更内容' must be '対象ファイル一覧'" in msg
        found_section = msg.split("found:", 1)[1]
        assert "'対象ファイル'" in found_section
        assert "'対象ファイル一覧'" not in found_section


class TestCodexReviewReadTracking:
    """codex-review.md読み込み追跡。"""

    def test_read_codex_review_sets_flag(self, tmp_path: pathlib.Path):
        """codex-review.mdをReadすると状態フラグが設定される。"""
        sid = "codex-review-read"
        result = _run(
            {
                "tool_name": "Read",
                "tool_input": {"file_path": "/fake/skills/plan-mode/references/codex-review.md"},
                "session_id": sid,
            },
            state_dir=tmp_path,
        )
        assert result.returncode == 0
        state = json.loads((tmp_path / f"claude-agent-toolkit-{sid}.json").read_text(encoding="utf-8"))
        assert state["codex_review_read"] is True

    def test_read_other_file_does_not_set_flag(self, tmp_path: pathlib.Path):
        """codex-review.md以外のファイルでは状態フラグが設定されない。"""
        sid = "other-read"
        result = _run(
            {
                "tool_name": "Read",
                "tool_input": {"file_path": "/fake/rules/01-agent.md"},
                "session_id": sid,
            },
            state_dir=tmp_path,
        )
        assert result.returncode == 0
        state_file = tmp_path / f"claude-agent-toolkit-{sid}.json"
        if state_file.exists():
            state = json.loads(state_file.read_text(encoding="utf-8"))
            assert not state.get("codex_review_read", False)

    def test_read_flag_idempotent(self, tmp_path: pathlib.Path):
        """フラグが既に設定されている場合は冗長な書き込みをしない。"""
        sid = "codex-review-idem"
        state_file = tmp_path / f"claude-agent-toolkit-{sid}.json"
        state_file.write_text(json.dumps({"codex_review_read": True}), encoding="utf-8")
        result = _run(
            {
                "tool_name": "Read",
                "tool_input": {"file_path": "/path/to/codex-review.md"},
                "session_id": sid,
            },
            state_dir=tmp_path,
        )
        assert result.returncode == 0


class TestTextlintViolationsReadTracking:
    """textlint-violations.md読み込み追跡。"""

    def test_read_textlint_violations_sets_flag(self, tmp_path: pathlib.Path):
        """配布元パスのtextlint-violations.mdをReadすると状態フラグが設定される。"""
        sid = "textlint-read-source"
        result = _run(
            {
                "tool_name": "Read",
                "tool_input": {
                    "file_path": "/home/foo/dotfiles/agent-toolkit/skills/writing-standards/references/textlint-violations.md"
                },
                "session_id": sid,
            },
            state_dir=tmp_path,
        )
        assert result.returncode == 0
        state = json.loads((tmp_path / f"claude-agent-toolkit-{sid}.json").read_text(encoding="utf-8"))
        assert state["textlint_violations_read"] is True

    def test_read_textlint_violations_distributed_path_sets_flag(self, tmp_path: pathlib.Path):
        """配布先パスのtextlint-violations.mdをReadすると状態フラグが設定される。"""
        sid = "textlint-read-dist"
        result = _run(
            {
                "tool_name": "Read",
                "tool_input": {"file_path": "/home/foo/.claude/skills/writing-standards/references/textlint-violations.md"},
                "session_id": sid,
            },
            state_dir=tmp_path,
        )
        assert result.returncode == 0
        state = json.loads((tmp_path / f"claude-agent-toolkit-{sid}.json").read_text(encoding="utf-8"))
        assert state["textlint_violations_read"] is True

    def test_read_other_file_does_not_set_flag(self, tmp_path: pathlib.Path):
        """textlint-violations.md以外のファイルでは状態フラグが設定されない。"""
        sid = "textlint-other-read"
        result = _run(
            {
                "tool_name": "Read",
                "tool_input": {"file_path": "/fake/rules/01-agent.md"},
                "session_id": sid,
            },
            state_dir=tmp_path,
        )
        assert result.returncode == 0
        state_file = tmp_path / f"claude-agent-toolkit-{sid}.json"
        if state_file.exists():
            state = json.loads(state_file.read_text(encoding="utf-8"))
            assert not state.get("textlint_violations_read", False)
