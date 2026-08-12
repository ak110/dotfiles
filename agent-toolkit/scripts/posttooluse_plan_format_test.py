"""計画書き込み後の案内と状態記録を検証する。"""

import json
import os
import pathlib
import subprocess

import _fork_runner
import _plan_format

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
    """人間向け固定領域と実装者向け領域を持つ正規形の計画を返す。"""
    return """# 計画の主題

## 概要

成果。

### 計画メタ情報

- 起動経路: `agent-toolkit:plan-mode`
- 対象リポジトリ: `/repo`
- 作業種別: 通常変更
- ベースコミット: `0123456789012345678901234567890123456789`

## 実施内容

| 実施内容 | ユーザー指示との関係 | 根拠 |
| --- | --- | --- |
| 対象を更新する | 指示どおり | P-001 |

## 提示素材

P-001:

```text
対象を更新してほしい。
```

## 変更履歴

| ID | 起点 | 指摘内容 | 採否・現在の結論 | 同期先 |
| --- | --- | --- | --- | --- |
| H-001 | ユーザー発言 | P-001 | 採用した。 | `## 実施内容` |

## 恒久化・リファクタリング内容

### 恒久化

| 項目 | 内容 |
| --- | --- |
| 観測事象 | 対象が古い。 |
| 根本原因 | 更新経路が無い。 |
| 反映先 | 対象。 |
| 反映内容 | 更新する。 |
| 対策強度 | 検査する。 |
| 設計意図の記録先 | 設計文書。 |

### リファクタリング

| 項目 | 内容 |
| --- | --- |
| 対象 | 対象。 |
| 現状の問題 | 重複。 |
| 対応 | 統合する。 |
| 本計画に含めるか | 含める。 |

### 類似見直し

| 項目 | 内容 |
| --- | --- |
| 母集団 | 全体。 |
| 点検観点 | 同種。 |
| 該当件数と箇所 | 1件、対象。 |

## 実装資料

### 変更説明

対象を更新する。

## 完了条件

検証成功。

## 進捗ログ

| 日時 | 完了した工程 | 結果・特記事項 |
| --- | --- | --- |
| 2026-08-09 12:00 | 初版起草 | レビュー待ち。 |
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

    def test_implementation_material_h3_is_not_constrained(self, tmp_path: pathlib.Path) -> None:
        """実装資料配下のH3を変えてもPostToolUseは形式警告を返さない。"""
        home = tmp_path / "home"
        state_dir = tmp_path / "state"
        plan = _prepare_plan_home(home) / "flexible.md"
        content = _plan_content().replace("### 変更説明", "### 実行方法\n\n手順。\n\n### 変更説明")
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


def test_plan_format_exposes_design_intent_row() -> None:
    """通常変更の恒久化表は設計意図の記録先を持つ。"""
    assert "設計意図の記録先" in _plan_format.PLAN_PERMANENCE_TABLE_ROWS
