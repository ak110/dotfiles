"""計画書き込み後の案内と状態記録を検証する。"""

import json
import os
import pathlib
import subprocess

import _fork_runner

_SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "claude_hook.py"


def _run(
    payload: dict | str,
    *,
    state_dir: pathlib.Path | None = None,
    home_dir: pathlib.Path | None = None,
    plan_mode_skill_invoked: bool = False,
) -> subprocess.CompletedProcess[str]:
    """PostToolUseフックを隔離環境で実行する。"""
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
    return _fork_runner.run_script(_SCRIPT, argv=("posttooluse",), input=text, env=env)


def _read_state(state_dir: pathlib.Path, session_id: str) -> dict:
    """セッション状態を読み込む。"""
    path = state_dir / f"claude-agent-toolkit-{session_id}.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def _prepare_plan_home(home_dir: pathlib.Path) -> pathlib.Path:
    """計画ディレクトリを作成する。"""
    plans = home_dir / ".claude" / "plans"
    plans.mkdir(parents=True, exist_ok=True)
    return plans


def _plan_content() -> str:
    """新しい意味アンカーを持つ計画を返す。"""
    return """## 目的

成果。

## 実装契約

### 計画メタ情報

- 対象リポジトリ: `/repo`
- ベースコミット: `0123456789012345678901234567890123456789`
- 作業種別: 通常変更

### 対象ファイル一覧

- `file.py`

## 完了条件

検証成功。

## 進捗ログ

未着手。
"""


class TestPlanPostWrite:
    """計画ファイルへの書き込み後処理を検証する。"""

    def test_write_emits_check_notice_and_records_current_path(self, tmp_path: pathlib.Path) -> None:
        """Write後にstandalone checker案内と現在パスを記録する。"""
        home = tmp_path / "home"
        state_dir = tmp_path / "state"
        plan = _prepare_plan_home(home) / "sample.md"
        content = _plan_content()
        plan.write_text(content, encoding="utf-8")
        result = _run(
            {
                "session_id": "plan-write",
                "tool_name": "Write",
                "tool_input": {"file_path": str(plan), "content": content},
                "cwd": "/repo",
            },
            state_dir=state_dir,
            home_dir=home,
            plan_mode_skill_invoked=True,
        )
        assert result.returncode == 0
        assert "post-write checks" in result.stdout
        assert "does not conform" not in result.stdout
        assert _read_state(state_dir, "plan-write")["current_plan_file_path"] == str(plan)

    def test_arbitrary_h2_order_and_additional_h2_are_not_constrained(self, tmp_path: pathlib.Path) -> None:
        """任意順序と追加H2でもPostToolUseは形式警告を返さない。"""
        home = tmp_path / "home"
        state_dir = tmp_path / "state"
        plan = _prepare_plan_home(home) / "flexible.md"
        content = _plan_content().replace(
            "## 完了条件\n\n検証成功。\n\n## 進捗ログ",
            "## 進捗ログ\n\n未着手。\n\n## 補足\n\n追加。\n\n## 完了条件",
        )
        plan.write_text(content, encoding="utf-8")
        result = _run(
            {
                "session_id": "plan-flexible",
                "tool_name": "Write",
                "tool_input": {"file_path": str(plan), "content": content},
                "cwd": "/repo",
            },
            state_dir=state_dir,
            home_dir=home,
            plan_mode_skill_invoked=True,
        )
        assert result.returncode == 0
        assert "does not conform" not in result.stdout
        assert "post-write checks" in result.stdout

    def test_read_textlint_reference_does_not_record_plan_state(self, tmp_path: pathlib.Path) -> None:
        """文章lint資料のReadは計画用状態フラグを記録しない。"""
        result = _run(
            {
                "session_id": "read-reference",
                "tool_name": "Read",
                "tool_input": {"file_path": "/repo/references/textlint-violations.md"},
            },
            state_dir=tmp_path,
        )
        assert result.returncode == 0
        assert not _read_state(tmp_path, "read-reference").get("textlint_violations_read", False)

    def test_non_plan_and_sidecar_paths_are_skipped(self, tmp_path: pathlib.Path) -> None:
        """計画外パスと副次ファイルには案内を返さない。"""
        home = tmp_path / "home"
        plans = _prepare_plan_home(home)
        for target in (tmp_path / "other.md", plans / "sample.review.md", plans / "sample.codex.log"):
            target.write_text("content\n", encoding="utf-8")
            result = _run(
                {
                    "session_id": f"skip-{target.suffix}",
                    "tool_name": "Write",
                    "tool_input": {"file_path": str(target), "content": "content\n"},
                },
                state_dir=tmp_path / "state",
                home_dir=home,
                plan_mode_skill_invoked=True,
            )
            assert result.stdout.strip() == ""
