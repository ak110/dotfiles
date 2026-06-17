"""agent-toolkit/scripts/posttooluse.py のplan file形式検査関連テスト。

`posttooluse_test.py`本体から計画ファイル形式検査・SSOT検査・codex-review.md読み込み検出を分割した。
共通ヘルパー（`_run`・`_read_state`・`_load_posttooluse_module`）は分割先で複製する。
ハンドラ網羅と機械チェック上限のバランス確保が分割の動機。
"""

import functools
import importlib.util
import json
import os
import pathlib
import re
import subprocess
import sys
import types

import pytest

_SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "posttooluse.py"
_SKILL_MD = pathlib.Path(__file__).resolve().parents[1] / "skills" / "plan-mode" / "SKILL.md"
_PLAN_FILE_REF = pathlib.Path(__file__).resolve().parents[1] / "skills" / "plan-mode" / "references" / "plan-file-guidelines.md"


@functools.cache
def _load_posttooluse_module() -> types.ModuleType:
    """`scripts/posttooluse.py`を`importlib`で動的にインポートする。

    `TestPlanFormatSsot`で本体スクリプトの定数（`_PLAN_REQUIRED_H2`等）と
    外部ドキュメントの整合性を検査するために使う。
    引数注入では到達不能なモジュール内部状態の検査のため、importlibによる直接参照を例外的に許容する。
    """
    spec = importlib.util.spec_from_file_location("posttooluse", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_POSTTOOLUSE_MODULE = _load_posttooluse_module()


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
    "変更内容": "- y",
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
    # 引数注入では到達不能なモジュール内部定数の参照のため直接アクセスする。
    section_order: tuple[str, ...] = _POSTTOOLUSE_MODULE._PLAN_REQUIRED_H2  # noqa: SLF001  # pylint: disable=protected-access  # SSOT: posttooluse._PLAN_REQUIRED_H2と同期
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

    def test_missing_required_section_is_warned(self, tmp_path: pathlib.Path):
        home, plans = self._home(tmp_path)
        # 調査結果セクションを欠落させた変種。
        content = _build_valid_plan(omit=("調査結果",))
        plan = _write_plan(plans, "missing.md", content)
        result = _run(
            {
                "session_id": "plan-miss",
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
        assert "missing required H2 sections" in msg
        assert "調査結果" in msg
        assert "[auto-generated: agent-toolkit/posttooluse][warn]" in msg

    def test_missing_response_policy_is_warned(self, tmp_path: pathlib.Path):
        """``対応方針`` セクション欠落も必須セクション違反として警告される。"""
        home, plans = self._home(tmp_path)
        content = _build_valid_plan(omit=("対応方針",))
        plan = _write_plan(plans, "missing-policy.md", content)
        result = _run(
            {
                "session_id": "plan-miss-policy",
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
        assert "missing required H2 sections" in msg
        assert "対応方針" in msg

    def test_missing_progress_log_is_warned(self, tmp_path: pathlib.Path):
        """``進捗ログ`` セクション欠落も必須セクション違反として警告される。"""
        home, plans = self._home(tmp_path)
        content = _build_valid_plan(omit=("進捗ログ",))
        plan = _write_plan(plans, "missing-progress.md", content)
        result = _run(
            {
                "session_id": "plan-miss-progress",
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
        assert "missing required H2 sections" in msg
        assert "進捗ログ" in msg

    def test_out_of_order_is_warned(self, tmp_path: pathlib.Path):
        home, plans = self._home(tmp_path)
        # 変更内容と調査結果を入れ替える。
        content = (
            "# タイトル\n\n"
            "## 背景\n\n説明。\n\n"
            "## 対応方針\n\n- a\n\n"
            "## 変更内容\n\n- y\n\n"
            "## 調査結果\n\n- x\n\n"
            "## 実行方法\n\n- w\n"
        )
        plan = _write_plan(plans, "order.md", content)
        result = _run(
            {
                "session_id": "plan-order",
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
        assert "out of order" in msg

    def test_unexpected_section_is_warned(self, tmp_path: pathlib.Path):
        home, plans = self._home(tmp_path)
        content = _VALID_PLAN + "\n## 備考\n\n自由記述。\n"
        plan = _write_plan(plans, "extra.md", content)
        result = _run(
            {
                "session_id": "plan-extra",
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
        assert "unexpected H2" in msg
        assert "備考" in msg

    def test_history_not_at_top_is_warned(self, tmp_path: pathlib.Path):
        home, plans = self._home(tmp_path)
        # 変更履歴を中間に置いた変種。
        content = (
            "# タイトル\n\n"
            "## 背景\n\n説明。\n\n"
            "## 対応方針\n\n- a\n\n"
            "## 調査結果\n\n- x\n\n"
            "## 変更履歴\n\n1. 仮\n\n"
            "## 変更内容\n\n- y\n\n"
            "## 実行方法\n\n- w\n"
        )
        plan = _write_plan(plans, "hist.md", content)
        result = _run(
            {
                "session_id": "plan-hist-mid",
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
        assert "変更履歴" in msg

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

    def test_edit_tool_triggers_check(self, tmp_path: pathlib.Path):
        home, plans = self._home(tmp_path)
        # 崩れたplanを生成し、EditツールからのHook通知を検証する。
        content = "# タイトル\n\n## 背景\n\nx\n"
        plan = _write_plan(plans, "edit.md", content)
        result = _run(
            {
                "session_id": "plan-edit",
                "tool_name": "Edit",
                "tool_input": {"file_path": str(plan), "old_string": "x", "new_string": "y"},
            },
            state_dir=tmp_path / "state",
            home_dir=home,
            plan_mode_skill_invoked=True,
        )
        output = _parse_hook_output(result.stdout)
        assert output is not None
        assert output["hookSpecificOutput"]["hookEventName"] == "PostToolUse"

    def test_multiedit_tool_triggers_check(self, tmp_path: pathlib.Path):
        home, plans = self._home(tmp_path)
        content = "# タイトル\n"
        plan = _write_plan(plans, "multi.md", content)
        result = _run(
            {
                "session_id": "plan-multi",
                "tool_name": "MultiEdit",
                "tool_input": {
                    "file_path": str(plan),
                    "edits": [{"old_string": "foo", "new_string": "bar"}],
                },
            },
            state_dir=tmp_path / "state",
            home_dir=home,
            plan_mode_skill_invoked=True,
        )
        output = _parse_hook_output(result.stdout)
        assert output is not None
        assert output["hookSpecificOutput"]["hookEventName"] == "PostToolUse"

    def test_code_fence_h2_is_ignored(self, tmp_path: pathlib.Path):
        """コードフェンス内の `## 見出し` は見出しとしてカウントしない。"""
        home, plans = self._home(tmp_path)
        content = _build_valid_plan(
            overrides={
                "背景": "```markdown\n## 予期せぬ見出し\n```",
            }
        )
        plan = _write_plan(plans, "fence.md", content)
        result = _run(
            {
                "session_id": "plan-fence",
                "tool_name": "Write",
                "tool_input": {"file_path": str(plan), "content": content},
            },
            state_dir=tmp_path / "state",
            home_dir=home,
            plan_mode_skill_invoked=True,
        )
        assert result.stdout.strip() == ""

    @pytest.mark.parametrize(
        ("outer", "inner"),
        [
            ("````", "```"),  # バックティック同士 (長さ違い)
            ("~~~~", "```"),  # 外側チルダ・内側バックティック (字種一致チェックの回帰)
        ],
    )
    def test_nested_code_fence_h2_is_ignored(self, tmp_path: pathlib.Path, outer: str, inner: str):
        """外側フェンスが同字種・同長以上でのみ閉じ、内部の `##` を見出し扱いしない。"""
        home, plans = self._home(tmp_path)
        content = _build_valid_plan(
            overrides={
                "背景": (f"{outer}markdown\n{inner}markdown\n## 予期せぬ見出し\n{inner}\n{outer}"),
            }
        )
        plan = _write_plan(plans, "nested-fence.md", content)
        result = _run(
            {
                "session_id": "plan-nested-fence",
                "tool_name": "Write",
                "tool_input": {"file_path": str(plan), "content": content},
            },
            state_dir=tmp_path / "state",
            home_dir=home,
            plan_mode_skill_invoked=True,
        )
        assert result.stdout.strip() == ""

    def test_html_comment_h2_is_ignored(self, tmp_path: pathlib.Path):
        """複数行 HTML コメント内の `## 見出し` は見出しとしてカウントしない。"""
        home, plans = self._home(tmp_path)
        content = _build_valid_plan(
            overrides={
                "背景": ("<!--\n## ダミー\nコメントなので無視される想定。\n-->"),
            }
        )
        plan = _write_plan(plans, "html-comment.md", content)
        result = _run(
            {
                "session_id": "plan-html-comment",
                "tool_name": "Write",
                "tool_input": {"file_path": str(plan), "content": content},
            },
            state_dir=tmp_path / "state",
            home_dir=home,
            plan_mode_skill_invoked=True,
        )
        assert result.stdout.strip() == ""

    @pytest.mark.parametrize("closer", ["---", "..."])
    def test_frontmatter_h2_is_ignored(self, tmp_path: pathlib.Path, closer: str):
        """ファイル先頭 YAML フロントマター内の `## 見出し` は見出しとしてカウントしない。"""
        home, plans = self._home(tmp_path)
        content = _build_valid_plan(prefix=f"---\ntitle: sample\nnote: |\n  ## ダミー\n{closer}\n\n")
        plan = _write_plan(plans, "frontmatter.md", content)
        result = _run(
            {
                "session_id": "plan-frontmatter",
                "tool_name": "Write",
                "tool_input": {"file_path": str(plan), "content": content},
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


class TestPlanFormatSsot:
    """期待セクション一覧が`plan-mode/references/plan-file-guidelines.md`に全て登場することを検査する。"""

    def test_required_and_optional_h2_appear_in_plan_file_ref(self):
        text = _PLAN_FILE_REF.read_text(encoding="utf-8")
        # 引数注入では到達不能なモジュール内部定数の整合検査のため直接参照する。
        plan_required_h2: tuple[str, ...] = _POSTTOOLUSE_MODULE._PLAN_REQUIRED_H2  # noqa: SLF001  # pylint: disable=protected-access
        for heading in plan_required_h2:
            assert f"## {heading}" in text, f"plan-file-guidelines.md に `## {heading}` が無い"

    def test_section_definition_order_matches_required_h2(self):
        """`plan-file-guidelines.md`のセクション定義H3と`_PLAN_REQUIRED_H2`の順序が一致することを検査する。

        セクション定義H3は`### XXX（`## YYY`）`形式で記述されており、
        バッククォート内のH2名（YYY）が登場順に`_PLAN_REQUIRED_H2`と完全一致するべき。
        記述例コードブロック内のH2や、サブH3定義（`### XXX（`### YYY`）`形式）は
        パターン上マッチしないため誤検出しない。
        """
        text = _PLAN_FILE_REF.read_text(encoding="utf-8")
        # 引数注入では到達不能なモジュール内部定数の整合検査のため直接参照する。
        plan_required_h2: tuple[str, ...] = _POSTTOOLUSE_MODULE._PLAN_REQUIRED_H2  # noqa: SLF001  # pylint: disable=protected-access
        # 行頭H3のうち、丸括弧内のインラインコードがH2（`## ...`）形式のものだけ抽出。
        pattern = re.compile(r"^### .+?（`## ([^`]+)`）", re.MULTILINE)
        defined_h2 = tuple(pattern.findall(text))
        assert defined_h2 == plan_required_h2, (
            f"plan-file-guidelines.md のセクション定義順 {defined_h2} が _PLAN_REQUIRED_H2 {plan_required_h2} と一致しない"
        )


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
                "tool_input": {"file_path": "/fake/rules/agent.md"},
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
