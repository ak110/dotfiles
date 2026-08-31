"""計画書き込み後の案内と状態記録を検証する。"""

import json
import os
import pathlib
import subprocess

import _fork_runner
import _plan_format
import pytest
from _test_helpers import SESSION_STATE_FILENAME_TEMPLATE, _read_state
from quality_checkpoint import QUALITY_CHECKPOINT_NOTICE

_SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "hook.py"


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
            (state_dir / SESSION_STATE_FILENAME_TEMPLATE.format(session_id=sid)).write_text(
                json.dumps({"plan_mode_skill_invoked": True}, ensure_ascii=False),
                encoding="utf-8",
            )
    return _fork_runner.run_script(_SCRIPT, argv=("posttooluse",), input=text, env=env)


def _prepare_plan_home(home_dir: pathlib.Path) -> pathlib.Path:
    """計画ディレクトリを作成する。"""
    plans = home_dir / ".claude" / "plans"
    plans.mkdir(parents=True, exist_ok=True)
    return plans


def _codex_patch(*sections: str) -> str:
    """Codex apply_patch入力を組み立てる。"""
    return "*** Begin Patch\n" + "".join(sections) + "*** End Patch\n"


def _codex_add_section(path: pathlib.Path, content: str = "content\n") -> str:
    lines = "".join(f"+{line}\n" for line in content.splitlines())
    return f"*** Add File: {path}\n{lines}"


def _run_codex_patch(
    session_id: str,
    command: str,
    *,
    state_dir: pathlib.Path,
    home_dir: pathlib.Path,
    plan_mode_skill_invoked: bool = True,
) -> subprocess.CompletedProcess[str]:
    return _run(
        {
            "session_id": session_id,
            "tool_name": "apply_patch",
            "tool_input": {"command": command},
            "cwd": "/repo",
            "turn_id": "turn-1",
        },
        state_dir=state_dir,
        home_dir=home_dir,
        plan_mode_skill_invoked=plan_mode_skill_invoked,
    )


def _plan_content(detail_name: str) -> str:
    """人間向け計画ファイル（メイン）の正規形の計画を返す。"""
    return f"""# 計画の主題

## 概要

成果。

### 計画メタ情報

- 起動経路: `agent-toolkit:plan-mode`
- 対象リポジトリ: `/repo`
- 作業種別: 通常変更
- ベースコミット: `作成時点の参照値`
- 計画ファイル（詳細）: `{detail_name}`

## 実施内容

| 実施内容 | 由来 | 採否 | 根拠 |
| --- | --- | --- | --- |
| 対象を更新する | ユーザー指示 | 採用 | - |
| 対象外の類似箇所は維持する | エージェント提案 | 対象外 | 公開契約への影響が無いため。 |

## 提示素材

なし

## 変更履歴

### ユーザー発言: 本セッションの直接指示

```text
対象を更新する。
```

### レビューで確定した変更

レビューで確定した対象を反映した。

## 検証区分

| 区分 | 検証コマンド |
| --- | --- |
| レーン内検証 | `pytest` |
| 統合後検証 | `make test` |

## 終端工程

なし

## 進捗ログ

| 日時 | 完了した工程 | 結果・特記事項 |
| --- | --- | --- |
"""


def _detail_content() -> str:
    """人間向け計画ファイル（詳細）の正規形を返す。"""
    return """## 恒久化・リファクタリング内容

### 恒久化

| 知見 | 出所 | 反映先 | 根拠 |
| --- | --- | --- | --- |
| 更新経路を恒久化する | 実装時調査 | 対象モジュール | 公開契約の境界を維持するため。 |

### リファクタリング

| 項目 | 内容 |
| --- | --- |
| 対象 | 対象モジュール。 |
| 現状の問題 | 更新経路が分散している。 |
| 対応 | 判定を統合する。 |
| 本計画に含めるか | 含める。 |

### 類似見直し

| 項目 | 内容 |
| --- | --- |
| 母集団 | 対象モジュール。 |
| 点検観点 | 公開契約への影響。 |
| 該当箇所 | 該当なし。 |

## 実装資料

### 実装単位

| 実装単位 | 目的 | 先行依存 | 統合順 | 近接検証 |
| --- | --- | --- | --- | --- |
| 契約境界の更新 | 公開契約の判定を更新する | なし | 1 | `pytest` |

### 調査結果

検索母集団は対象モジュールと関連テストである。
検索コマンド: `rg -n "公開契約|境界" agent-toolkit`
検索結果: 一致は2箇所で、対象外の接続面は不一致として除外した。

### 確定文面

```markdown
公開契約の判定は対象境界に限定する。
```

## 完了条件

近接検証と統合後検証が成功し、確定文面を対象ファイルへ反映する。
"""


class TestPlanPostWrite:
    """計画ファイルへの書き込み後処理を検証する。"""

    def test_write_emits_check_notice_and_records_current_path(self, tmp_path: pathlib.Path) -> None:
        """Write後にstandalone checker案内と現在パスを記録する。"""
        home = tmp_path / "home"
        state_dir = tmp_path / "state"
        plan = _prepare_plan_home(home) / "sample.md"
        content = _plan_content("sample.detail.md")
        plan.write_text(content, encoding="utf-8")
        plan.with_name("sample.detail.md").write_text(_detail_content(), encoding="utf-8")
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
        content = _plan_content("flexible.detail.md")
        plan.write_text(content, encoding="utf-8")
        detail = plan.with_name("flexible.detail.md")
        detail_content = _detail_content().replace("### 実装単位", "### 実行方法\n\n手順。\n\n### 実装単位")
        detail.write_text(detail_content, encoding="utf-8")
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
        for target in (
            tmp_path / "other.md",
            plans / "sample.bugs.md",
            plans / "sample.review.md",
            plans / "sample.codex.log",
        ):
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

    @pytest.mark.parametrize(
        ("first_name", "second_name"),
        [
            ("sample.md", "sample.detail.md"),
            ("sample.detail.md", "sample.md"),
            ("plan with spaces.md", "plan with spaces.detail.md"),
        ],
    )
    def test_codex_whole_write_emits_quality_notice_only_after_pair_exists(
        self,
        tmp_path: pathlib.Path,
        first_name: str,
        second_name: str,
    ) -> None:
        """Codexのwhole-writeは計画メイン/detailの両方がそろった後だけ通知する。"""
        home = tmp_path / "home"
        plans = _prepare_plan_home(home)
        state_dir = tmp_path / "state"
        first = plans / first_name
        second = plans / second_name
        first.write_text("content\n", encoding="utf-8")
        first_result = _run_codex_patch(
            "codex-pair",
            _codex_patch(_codex_add_section(first)),
            state_dir=state_dir,
            home_dir=home,
        )
        second.write_text("content\n", encoding="utf-8")
        second_result = _run_codex_patch(
            "codex-pair",
            _codex_patch(_codex_add_section(second)),
            state_dir=state_dir,
            home_dir=home,
        )

        assert QUALITY_CHECKPOINT_NOTICE not in first_result.stdout
        assert second_result.stdout.count(QUALITY_CHECKPOINT_NOTICE) == 1

    def test_codex_patch_with_both_pair_files_emits_one_quality_notice(self, tmp_path: pathlib.Path) -> None:
        """1回のapply_patchで両ファイルを追加しても通知は1件にまとめる。"""
        home = tmp_path / "home"
        plans = _prepare_plan_home(home)
        main = plans / "both.md"
        detail = plans / "both.detail.md"
        main.write_text("content\n", encoding="utf-8")
        detail.write_text("content\n", encoding="utf-8")
        result = _run_codex_patch(
            "codex-both",
            _codex_patch(_codex_add_section(main), _codex_add_section(detail)),
            state_dir=tmp_path / "state",
            home_dir=home,
        )

        assert result.stdout.count(QUALITY_CHECKPOINT_NOTICE) == 1

    def test_codex_existing_pair_rewhole_write_emits_one_quality_notice(self, tmp_path: pathlib.Path) -> None:
        """完成済みペアのCodex whole-writeでも実行ごとに1件だけ通知する。"""
        home = tmp_path / "home"
        plans = _prepare_plan_home(home)
        main = plans / "existing-pair.md"
        detail = plans / "existing-pair.detail.md"
        main.write_text("content\n", encoding="utf-8")
        detail.write_text("content\n", encoding="utf-8")
        result = _run_codex_patch(
            "codex-existing-pair",
            _codex_patch(_codex_add_section(main)),
            state_dir=tmp_path / "state",
            home_dir=home,
        )

        assert result.stdout.count(QUALITY_CHECKPOINT_NOTICE) == 1

    @pytest.mark.parametrize(
        ("command_builder", "plan_mode_skill_invoked"),
        [
            (lambda main: _codex_patch(_codex_add_section(main)), False),
            (lambda main: _codex_patch(f"*** Update File: {main}\n"), True),
            (lambda main: _codex_patch(f"*** Delete File: {main}\n"), True),
        ],
    )
    def test_codex_non_completion_operations_are_silent(
        self,
        tmp_path: pathlib.Path,
        command_builder,
        plan_mode_skill_invoked: bool,
    ) -> None:
        """計画モード外、部分更新、削除では品質通知を出力しない。"""
        home = tmp_path / "home"
        plans = _prepare_plan_home(home)
        main = plans / "existing.md"
        detail = plans / "existing.detail.md"
        main.write_text("content\n", encoding="utf-8")
        detail.write_text("content\n", encoding="utf-8")
        result = _run_codex_patch(
            "codex-silent",
            command_builder(main),
            state_dir=tmp_path / "state",
            home_dir=home,
            plan_mode_skill_invoked=plan_mode_skill_invoked,
        )

        assert QUALITY_CHECKPOINT_NOTICE not in result.stdout

    def test_apply_patch_without_codex_turn_id_does_not_emit_quality_notice(self, tmp_path: pathlib.Path) -> None:
        """Codex識別情報が欠落したapply_patchは品質通知経路への追加対象外とする。"""
        home = tmp_path / "home"
        plans = _prepare_plan_home(home)
        main = plans / "without-turn.md"
        detail = plans / "without-turn.detail.md"
        main.write_text("content\n", encoding="utf-8")
        detail.write_text("content\n", encoding="utf-8")
        result = _run(
            {
                "session_id": "without-turn",
                "tool_name": "apply_patch",
                "tool_input": {"command": _codex_patch(_codex_add_section(main))},
                "cwd": "/repo",
            },
            state_dir=tmp_path / "state",
            home_dir=home,
            plan_mode_skill_invoked=True,
        )

        assert QUALITY_CHECKPOINT_NOTICE not in result.stdout

    def test_codex_sidecar_is_silent(self, tmp_path: pathlib.Path) -> None:
        """計画の副次ファイルはペア完成の対象外とする。"""
        home = tmp_path / "home"
        plans = _prepare_plan_home(home)
        main = plans / "sample.md"
        detail = plans / "sample.detail.md"
        sidecar = plans / "sample.review.md"
        main.write_text("content\n", encoding="utf-8")
        detail.write_text("content\n", encoding="utf-8")
        sidecar.write_text("content\n", encoding="utf-8")
        result = _run_codex_patch(
            "codex-sidecar",
            _codex_patch(_codex_add_section(sidecar)),
            state_dir=tmp_path / "state",
            home_dir=home,
        )

        assert QUALITY_CHECKPOINT_NOTICE not in result.stdout

    def test_codex_plan_path_with_spaces_keeps_pair_detection(self, tmp_path: pathlib.Path) -> None:
        """空白を含む計画パスでも対応する計画ファイル（詳細）を機械的に検出する。"""
        home = tmp_path / "home"
        plans = _prepare_plan_home(home)
        main = plans / "plan with spaces.md"
        detail = plans / "plan with spaces.detail.md"
        main.write_text("content\n", encoding="utf-8")
        detail.write_text("content\n", encoding="utf-8")
        result = _run_codex_patch(
            "codex-spaces",
            _codex_patch(_codex_add_section(main)),
            state_dir=tmp_path / "state",
            home_dir=home,
        )

        assert result.stdout.count(QUALITY_CHECKPOINT_NOTICE) == 1

    @pytest.mark.parametrize("tool_name", ["Write", "Edit", "MultiEdit"])
    def test_claude_edit_tools_do_not_emit_codex_quality_notice(
        self,
        tmp_path: pathlib.Path,
        tool_name: str,
    ) -> None:
        """Claude Codeの既存編集経路は品質通知を追加しない。"""
        home = tmp_path / "home"
        plans = _prepare_plan_home(home)
        main = plans / "claude.md"
        detail = plans / "claude.detail.md"
        main.write_text("content\n", encoding="utf-8")
        detail.write_text("content\n", encoding="utf-8")
        if tool_name == "Write":
            tool_input = {"file_path": str(main), "content": "content\n"}
        elif tool_name == "Edit":
            tool_input = {"file_path": str(main), "old_string": "content", "new_string": "changed"}
        else:
            tool_input = {
                "file_path": str(main),
                "edits": [{"old_string": "content", "new_string": "changed"}],
            }
        result = _run(
            {
                "session_id": f"claude-{tool_name}",
                "tool_name": tool_name,
                "tool_input": tool_input,
                "cwd": "/repo",
            },
            state_dir=tmp_path / "state",
            home_dir=home,
            plan_mode_skill_invoked=True,
        )

        assert QUALITY_CHECKPOINT_NOTICE not in result.stdout


def test_plan_format_exposes_permanence_destination_column() -> None:
    """通常変更の恒久化表は反映先列を持つ。"""
    assert "反映先" in _plan_format.PLAN_PERMANENCE_TABLE_HEADER
