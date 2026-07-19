#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["pyfltr>=3.14.1", "platformdirs>=4.0"]
# ///
# pylint: disable=too-many-lines  # ハンドラ網羅のためチェック実装が多く、分割するとモジュール間の依存関係が複雑化するため許容する
r"""Claude Code plugin agent-toolkit: PreToolUse統合フック。

任意ツールの実行前に以下のチェックを順に実行する。
block系checkは1プロセスで直列実行し、最初の違反でexit 2する。
warn種別のcheckはstderrまたはstdoutに警告を表示しつつ処理を継続する。
auto-fix種別のcheckは`updatedInput`でツール入力を自動書き換えする。
関連チェック項目は初回で一括開示する（反復サイクル防止のため）。

統合しているチェック:

任意ツール:

- メインエージェント応答の日本語文字比率が閾値未満の場合の警告/ブロック (warn/block)
- plan-modeスキル未起動のままのplan file編集（Write/Edit/MultiEdit）のブロック (block)
- plan-modeスキル起動後、計画ファイル未作成のままagent-toolkit配下の直接編集連続のブロック (warn/block)
- plan file編集前の必須リファレンス（textlint-violations.md / plan-file-guidelines.md）未読のブロック (block)
- plan fileのWriteで文書サイズ上限対象ファイルのwc -l実測値記録漏れのブロック (block)
- 規範対象ドキュメントへのメタ規範新設編集時、計画ファイルの遡及スキャン結果記録未整備のブロック (block)
- plan fileのWrite/Edit/MultiEditでH2見出し順序違反のブロック (block)
- plan fileのWrite/Edit/MultiEditで絶対の行番号参照違反のブロック (block)
- plan fileのWrite/Edit/MultiEditで対象ファイル一覧とH3見出しの1対1対応違反のブロック (block)
- plan fileのWrite/Edit/MultiEditで`## 変更履歴`記載内容と`## 変更内容`側対象ファイル一覧・
  H3見出しとの対応欠落のブロック (block)
- plan fileのWrite/Edit/MultiEditで`## 変更内容`配下H3のtext/diffコードブロック欠落のブロック (block)
- plan fileのWrite/Edit/MultiEditで末尾の`## 計画ファイル（本ファイル）のパス`節配下パス値と`file_path`不一致のブロック (block)
- plan fileのWriteでワークアラウンド語検出時の事前検討メモ未整備の警告 (warn)
- plan fileのWrite/Edit/MultiEditで対象ファイル一覧に`agent-toolkit/`配下パスを含むが
  `## 実行方法`本文に`agent_toolkit_bump.py`ステップが記載されていない場合の警告 (warn)
- plan fileのWrite/Edit/MultiEditで`## 実行方法`本文にbump stepが記載されているが
  対象ファイル一覧にmanifest（`agent-toolkit/.claude-plugin/plugin.json`・
  `.claude-plugin/marketplace.json`）が含まれていない場合の警告 (warn)
- plan fileのWrite/Edit/MultiEditで対象ファイル一覧に絶対パスまたは親ディレクトリ参照を検出した場合の警告 (warn)

EnterPlanMode:

- `process-feedbacks`・`plan-and-add-feedback`スキル経由
  （`process_feedbacks_skill_invoked`または`plan_and_add_feedback_skill_invoked`真）でのEnterPlanMode発行のブロック (block)

ExitPlanMode:

- `plan-file-creator`の整合性チェック（plan-reviewer / codexレビュー、
  対象ファイル一覧にコーディングエージェント向け文書を含む計画では条件付きでagent-doc-validatorも追加）
  完了未達のブロック (block)

mcp__codex__codex:

- codex-review.md未読時のブロック (block)
- `plan-codex-reviewer`サブエージェント経由の実施履歴（`plan_codex_reviewer_invoked`・
  `plan_codex_reviewer_blocked`のいずれかが真）が無い直接呼び出しのブロック (block)
- `sandbox`未指定時のみ`danger-full-access`へ既定昇格。
  明示指定（`read-only`, `workspace-write`, `danger-full-access`）は尊重する (auto-fix)
- 全チェック通過時の強制承認 (auto-approve)

mcp__codex__codex-reply:

- `threadId`が`recorded_codex_thread_id`と不一致かつ
  `plan-codex-reviewer`経由の実施履歴が無い場合のブロック (block)
- `threadId`一致または`plan-codex-reviewer`経由の実施履歴がある場合の強制承認 (auto-approve)

Bash:

- git amend / rebase直前に`git log`未確認のブロック (block)
- git push実行時のamend後dirty状態のブロック (block)
- 非Pythonプロジェクトでの`uv run python <path>`形式起動のブロック (block)
- `git commit`未検証警告 (warn)
- `agent-toolkit/`配下のコミット時のversion bump漏れ警告 (warn)
- `git log --decorate`の自動付与 (auto-fix)
- `codex exec`の未決事項念押し (warn)
- 一括ステージ実行時の自セッション編集対象外ファイル警告 (warn)

AskUserQuestion:

- 縮退誘発フレーズ（作業量・残コンテキスト等を根拠とした分割可否相談・進め方確認）の検出 (block)

Skill:

- `agent-toolkit:plan-mode`起動時の`plan-file-creator`の整合性チェック完了フラグリセット（新計画着手の合図） (auto-fix)

Agent / Task:

- 規範非読込型サブエージェント起動時の、規範の明示引用漏れ警告 (warn)
- `plan-impl-executor`起動時、起動プロンプトが現行計画パスを指す場合の
  `plan-file-creator`の整合性チェック完了未達のブロック (block)
- `_TRACKED_SUBAGENT_TYPES`対象種別起動時の`_process_loop_log`への起動時刻記録 (side-effect)

Write / Edit / MultiEdit:

- 文字化け（U+FFFD）検出 (block)
- `.ps1` / `.ps1.tmpl`へのLF-only書き込み検出 (block)
- lockfile / 生成物ディレクトリの直接編集 (block)
- シークレット / 鍵ファイルの直接編集 (block)
- `agent-toolkit/rules/`配下・`agent-toolkit/skills/**/SKILL.md`・計画ファイルへの
  scope-escalationフレーズ転記検出 (block)
- named subagent定義への`SendMessage`ツール登録欠落 (block)
- manifestファイルの手編集 (warn)
- ホームディレクトリの絶対パス混入 (warn)
- 口語的な日本語表現の混入 (warn)
- 「Xを根拠にYしない」「Xを理由にYしない」形式のメタ規範文言の増加 (warn)
- .md規範文書のWrite/Edit/MultiEditでfrontmatter同期注記の本体該当語句の実在検証warn (warn)

各チェックの詳細仕様（対象パターン・エラー文言・例外条件）は対応する実装関数のdocstringを参照する。
block系checkの検査対象は「新規に書き込まれる側」（`content` / `new_string`）を基本とする。
`old_string`は既存内容の修正・削除を妨げないため単独では検査対象としない。
Edit/MultiEditのscope-escalation checkは既存ファイル本文を読み込み、
各edit適用前後の全文をフェンス除外込みで比較しフレーズ出現回数の増加のみを検出する。
既存保持時の誤検出を解消し、フェンス開始・終了行がold/new_string外にある場合の除外漏れも解消する。
"""

import datetime
import json
import pathlib
import re
import shlex
import subprocess
import sys
import tempfile
import traceback
from collections.abc import Iterable, Iterator

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import _git_status  # noqa: E402  # pylint: disable=wrong-import-position,import-error
import _plan_format  # noqa: E402  # pylint: disable=wrong-import-position,import-error
import _process_loop_log  # noqa: E402  # pylint: disable=wrong-import-position,import-error
import _response_language_check  # noqa: E402  # pylint: disable=wrong-import-position,import-error
from _bash_command_parser import (  # noqa: E402  # pylint: disable=wrong-import-position,import-error
    GitEvent,
    extract_git_events,
)
from _file_lock import rotate_if_needed as _rotate_if_needed  # noqa: E402  # pylint: disable=wrong-import-position,import-error
from _message_format import llm_notice as _llm_notice_base  # noqa: E402  # pylint: disable=wrong-import-position,import-error
from _plan_file import is_plan_file  # noqa: E402  # pylint: disable=wrong-import-position,import-error
from _scope_escalation import (  # noqa: E402  # pylint: disable=wrong-import-position,import-error
    _SCOPE_ESCALATION_ALTERNATIVES,
    _SCOPE_ESCALATION_PHRASES,
    _apply_category_exclusions,
    _match_scope_escalation,
)
from _session_state import read_state, update_state  # noqa: E402  # pylint: disable=wrong-import-position,import-error
from pyfltr.colloquial import check as _colloquial_check  # noqa: E402  # pylint: disable=wrong-import-position

# U+FFFD（REPLACEMENT CHARACTER）: UTF-8デコード失敗時の代替文字
_REPLACEMENT_CHAR = "\ufffd"

# メインエージェントからの直接Readを禁じる隔離指定リファレンス。
# `agent-toolkit:agent-standards`「コンテキスト汚染の回避」節が指定する隔離リファレンスと同一SSOTとする。
# `isSidechain`真の呼び出しは通過させ、`agent-toolkit-edit`スキル起動セッションも例外とする。
_ISOLATED_READ_TARGETS: tuple[str, ...] = ("agent-toolkit/skills/agent-standards/references/_scope_escalation_test_inputs.txt",)

# 規範文書を自動読み込みしないサブエージェントタイプ。
# `agent-toolkit/skills/agent-standards/references/subagent-collaboration.md`
# 「必要な規範スキルの明示」節のSSOTに従い、
# `claude`と`Explore`は独立コンテキストで規範を読み込まないため、
# 起動プロンプトへの明示引用を求める。
_NORM_SKIPPING_SUBAGENT_TYPES: frozenset[str] = frozenset({"claude", "Explore"})

# 起動プロンプトが規範を明示引用していると判定するキーワード。
# 上記の規範非読込型サブエージェントを起動する場合、プロンプト本文に少なくとも1つを含めること。
_NORM_REFERENCE_KEYWORDS: tuple[str, ...] = (
    "agent-toolkit:agent-standards",
    "agent-toolkit:coding-standards",
    "agent-toolkit:writing-standards",
    "01-agent.md",
    "02-collaboration.md",
    "03-claude-code.md",
)


# このスクリプトの hook 識別子。
_HOOK_ID = "agent-toolkit/pretooluse"


def _truncate_matched_phrase(phrase: str) -> str:
    """scope-escalationマッチ文言をブロックメッセージ表示用に整形する。

    禁止語彙のクイズ化を避けるため、ブロック契機となったマッチテキストそのものを
    利用者が判読可能な形で通知する（`agent-toolkit:agent-standards`「コンテキスト汚染の回避」節）。
    CR/LFを除去し、通知の肥大化を避けるため先頭50文字までに切り詰める。
    """
    return phrase.replace("\r", "").replace("\n", "")[:50]


def _format_scope_escalation_alternatives(category: str) -> str:
    """scope-escalationカテゴリに対応する代替表現例を1行文字列で返す。

    エラーメッセージ末尾へ添えるための整形ヘルパー。
    対応するカテゴリが存在しない場合は空文字列を返す。
    """
    alternatives = _SCOPE_ESCALATION_ALTERNATIVES.get(category)
    if not alternatives:
        return ""
    joined = " / ".join(f"`{item}`" for item in alternatives)
    return f" Alternative expressions: {joined}."


def _scope_escalation_agent_md_reference(category: str) -> str:
    """scope-escalationカテゴリに対応する参照先規範節の文言を返す。

    `mitigation-in-adoption`は反映内容の縮小をフィードバック採否の場面で扱うため
    `agent-toolkit/skills/process-feedbacks/references/review-checklists.md`
    「批判的検討チェックリスト」節の「採用時の反映内容の縮小禁止」項を参照する。
    `subagent-hesitation`はサブエージェント委譲可否の判断保留を扱うため
    `agent-toolkit/rules/03-claude-code.md`「サブエージェントの活用」節を参照する。
    他カテゴリは`agent-toolkit/rules/01-agent.md`「完遂原則」項を参照する。
    """
    if category == "mitigation-in-adoption":
        return "agent-toolkit/skills/process-feedbacks/references/review-checklists.md '採用時の反映内容の縮小禁止' item"
    if category == "subagent-hesitation":
        return "agent-toolkit/rules/03-claude-code.md 'サブエージェントの活用' section"
    return "agent-toolkit/rules/01-agent.md '完遂原則' item"


def _llm_notice(body: str, *, tag: str = "") -> str:
    """コーディングエージェント宛てメッセージを標準プレフィックス/サフィックス付きで整形する。"""
    return _llm_notice_base(body, _HOOK_ID, tag=tag)


def _is_isolated_reference(file_path: str) -> bool:
    r"""`_ISOLATED_READ_TARGETS`のいずれかに末尾一致するかを判定する。

    `file_path`は相対・絶対の双方を受け取り、Windowsの`\\`区切りも正規化する。
    """
    if not file_path:
        return False
    posix = pathlib.Path(file_path).as_posix()
    return any(posix.endswith(target) for target in _ISOLATED_READ_TARGETS)


def _check_read_isolated_reference(tool_input: dict, session_id: str, is_sidechain: bool) -> str | None:
    """メインエージェントからの隔離指定リファレンスへの直接Readを検出する。

    `isSidechain`真（サブエージェント経由）は通過させる。
    `agent-toolkit-edit`スキル起動セッション（`agent_toolkit_edit_skill_invoked`フラグ）も
    編集目的の直接Readを許容する例外とする。
    それ以外の場合はブロック用の`_llm_notice`文言を返す。
    """
    if is_sidechain:
        return None
    file_path_raw = tool_input.get("file_path")
    if not isinstance(file_path_raw, str) or not _is_isolated_reference(file_path_raw):
        return None
    state = read_state(session_id)
    if state.get("agent_toolkit_edit_skill_invoked"):
        return None
    return _llm_notice(
        f"blocked: direct Read of isolated reference by main agent is prohibited. "
        f"Use Explore subagent to check, subagent_type=claude to fix, "
        f"or invoke agent-toolkit-edit for edit purpose. "
        f"Target: {file_path_raw}"
    )


def _check_agent_norm_reference(tool_input: dict) -> str | None:
    """規範非読込型サブエージェント起動時に規範の明示引用が無い場合の警告文言を返す。

    `subagent_type`が`_NORM_SKIPPING_SUBAGENT_TYPES`のいずれかで、
    かつ`prompt`本文に`_NORM_REFERENCE_KEYWORDS`のいずれも含まれない場合に警告文言を返す。
    それ以外は`None`を返す。
    """
    subagent_type = tool_input.get("subagent_type")
    if subagent_type not in _NORM_SKIPPING_SUBAGENT_TYPES:
        return None
    prompt = tool_input.get("prompt")
    if not isinstance(prompt, str):
        return None
    if any(kw in prompt for kw in _NORM_REFERENCE_KEYWORDS):
        return None
    return _llm_notice(
        f"warning: subagent_type={subagent_type!r} does not load norms. "
        "Include explicit reference to agent-toolkit:agent-standards or 01-agent.md in prompt.",
        tag="warn",
    )


def _language_notice(body: str) -> str:
    """言語警告専用の整形ヘルパー。

    共通サフィックスの関連性評価を促す英語文が英語化を助長し
    警告効果を弱めるため、プレフィックスのみ付与してサフィックスを省く。
    """
    return f"[auto-generated: {_HOOK_ID}][warn] {body}"


def main() -> int:
    """エントリポイント。

    exit code契約:

    - exit 0: 通過（違反なし / スキップ対象ツール / 想定外入力 / warnのみ）
    - exit 2: block違反検出（stderrに理由を出力）

    予期せぬ例外は0にフォールバックする（pluginのhookが破損して編集できなくなる事故を避けるため）。
    """
    try:
        payload = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, ValueError):
        # 想定外入力ではフックを無効化（実処理の破損を避ける安全側の判定）
        return 0

    tool_name = payload.get("tool_name", "")
    tool_input = payload.get("tool_input") or {}
    if not isinstance(tool_input, dict):
        return 0
    session_id_raw = payload.get("session_id", "")
    session_id = session_id_raw if isinstance(session_id_raw, str) else ""

    # 直前メインエージェント応答の日本語比率警告（任意ツール）。
    # 他warn系checkがJSONを返す場合はadditionalContextの末尾へ追記し、それ以外は単独でJSON出力する。
    exit_code, language_warning_body = _handle_language_check(payload, session_id)
    if exit_code == 2:
        return 2

    def emit_json(result: dict) -> None:
        nonlocal language_warning_body
        if language_warning_body is not None:
            _append_additional_context(result, _language_notice(language_warning_body))
            language_warning_body = None
        print(json.dumps(result, ensure_ascii=False))

    def flush_pending_language_warning() -> None:
        nonlocal language_warning_body
        if language_warning_body is None:
            return
        body = language_warning_body
        language_warning_body = None
        print(
            json.dumps(
                {
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "permissionDecision": "allow",
                        "additionalContext": _language_notice(body),
                    },
                },
                ensure_ascii=False,
            ),
        )

    # plan mode下でplan-modeスキル未起動のままplan fileを編集しようとした場合はブロック
    if _check_plan_mode_skill_first(tool_name, tool_input, session_id):
        return 2

    # plan-modeスキル起動後、計画ファイル未作成のままagent-toolkit配下の直接編集連続をブロック
    if _check_direct_agent_toolkit_edits_after_plan_mode(tool_name, tool_input, session_id):
        return 2

    # plan fileWrite検査のblock系check3関数を統合報告する。
    # 各関数は違反メッセージ`str`または`None`を返す。既存の呼び出し順序（required-reads→retroactive-scan→
    # no-deferral）を保持しつつ、warn系check群を間に置いて実行し、末尾で蓄積された違反メッセージを一括printしてreturn 2する。
    # warn系check群の戻り値契約・呼び出し順序は変更しない。
    blocking_errors: list[str] = []

    # plan file編集前の必須リファレンス未読の場合はブロック
    blocking_errors.append(_check_plan_file_required_reads_first(tool_name, tool_input, session_id) or "")

    # plan fileのWriteで文書サイズ上限対象ファイルのwc -l実測値記録漏れがある場合はwarn降格
    # （ExitPlanMode/plan-impl-executor起動時までのブロック検出は`plan-reviewer`・`plan-impl-reviewer`等の
    # サブエージェント目視レビューへ委譲する）
    _check_plan_file_size_limit_target_wc_l_recorded(tool_name, tool_input)

    # 規範対象ドキュメントへのメタ規範新設編集時、計画ファイルの遡及スキャン記録未整備をブロック
    blocking_errors.append(_check_plan_file_retroactive_scan_recorded(tool_name, tool_input, session_id) or "")

    # 内容・形式系検査群はwarn降格（ExitPlanMode/plan-impl-executor起動時までのブロック集約は
    # `plan-reviewer`・`plan-impl-reviewer`等のサブエージェント目視レビューへ委譲する）
    _check_plan_file_h2_section_order(tool_name, tool_input)
    _check_plan_file_target_files_h3_correspondence(tool_name, tool_input)
    _check_plan_file_history_content_sync(tool_name, tool_input)
    _check_plan_file_change_h3_has_code_block(tool_name, tool_input)
    _check_plan_file_absolute_line_numbers(tool_name, tool_input)
    _check_plan_file_path_section_matches_file_path(tool_name, tool_input)
    _check_workaround_memo_gate(tool_name, tool_input)
    _check_plan_file_bump_step_when_agent_toolkit_target(tool_name, tool_input)
    _check_plan_file_manifest_when_bump_step(tool_name, tool_input)
    _check_plan_file_target_file_paths_relative(tool_name, tool_input)

    # plan file `## 変更内容`・`### エージェント判断`配下の先送り含意動詞連結をブロック
    blocking_errors.append(_check_plan_file_no_deferral_expression(tool_name, tool_input) or "")

    # 蓄積された違反メッセージを統合報告する。1件でもあればreturn 2する。
    non_empty_errors = [msg for msg in blocking_errors if msg]
    if non_empty_errors:
        print("\n".join(non_empty_errors), file=sys.stderr)
        return 2

    # plan mode準備スキル経由の起動下でのEnterPlanMode発行は規範違反のためブロック
    if _check_plan_prep_skills_block_enter_plan_mode(tool_name, session_id):
        return 2

    # ExitPlanMode: `plan-file-creator`の整合性チェック（2サブエージェント/codexレビュー）の完了未達をブロック
    if tool_name == "ExitPlanMode":
        if _check_process7_completion_before_exit_plan_mode(session_id):
            return 2
        flush_pending_language_warning()
        return 0

    # Skill: plan-mode起動時は`plan-file-creator`の整合性チェック完了フラグをリセット
    if tool_name == "Skill":
        skill_name = tool_input.get("skill")
        if isinstance(skill_name, str) and skill_name in _PLAN_MODE_SKILL_NAMES:
            _reset_process7_completion_flags(session_id)
        flush_pending_language_warning()
        return 0

    # AskUserQuestion: 縮退誘発フレーズ検出
    if tool_name == "AskUserQuestion":
        match_result = _check_askuserquestion_scope_escalation(tool_input)
        if match_result is not None:
            category, matched = match_result
            print(
                _llm_notice(
                    f"blocked: AskUserQuestion contains a scope-escalation phrase (category: {category})."
                    f" matched: {_truncate_matched_phrase(matched)}."
                    f" See {_scope_escalation_agent_md_reference(category)}."
                    f" Category definitions are documented in `agent-toolkit:agent-standards`"
                    f" `references/scope-escalation-phrases.md` (isolated reference)."
                    f"{_format_scope_escalation_alternatives(category)}"
                    f" To pre-validate candidate phrases before re-issuing, run"
                    f" `echo '<candidate>' | python agent-toolkit/scripts/_scope_escalation.py`"
                    f" and match by exit code and category identifier (0 = pass, 2 = block).",
                ),
                file=sys.stderr,
            )
            return 2
        flush_pending_language_warning()
        return 0

    # mcp__codex__codex: codex-review.md未読ブロック + sandbox自動修正 + 強制承認
    # `isSidechain`が真（サブエージェント内部からの呼び出し）の場合は実装用途の呼び出しのため
    # codex-review.md未読ブロックを回避する。
    if tool_name == "mcp__codex__codex":
        _record_iss_sidechain_probe(session_id, tool_name, payload)
        if payload.get("isSidechain") is not True:
            state = read_state(session_id)
            if _check_codex_review_not_read(state):
                return 2
            # plan-codex-reviewerサブエージェント経由の実施履歴が無ければブロックする。
            if _check_codex_mcp_via_plan_codex_reviewer(state, tool_name=tool_name):
                return 2
        emit_json(_check_codex_mcp_sandbox(tool_input))
        return 0

    # mcp__codex__codex-reply: 強制承認（threadId不一致時はplan-codex-reviewer経由検査へ回す）
    if tool_name == "mcp__codex__codex-reply":
        _record_iss_sidechain_probe(session_id, tool_name, payload)
        if payload.get("isSidechain") is not True:
            state = read_state(session_id)
            thread_id_arg = tool_input.get("threadId")
            recorded = state.get("recorded_codex_thread_id")
            thread_id_matches = isinstance(thread_id_arg, str) and thread_id_arg == recorded
            if not thread_id_matches and _check_codex_mcp_via_plan_codex_reviewer(state, tool_name=tool_name):
                return 2
        emit_json(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "allow",
                },
            }
        )
        return 0

    # Bashは専用ハンドラ
    if tool_name == "Bash":
        command = tool_input.get("command")
        if not isinstance(command, str):
            flush_pending_language_warning()
            return 0
        cwd_raw = payload.get("cwd", "")
        cwd = cwd_raw if isinstance(cwd_raw, str) else ""
        # git amend / rebaseは直前にgit logを確認していなければブロック
        if _check_bash_amend_rebase_without_log(command, session_id, cwd):
            return 2
        # git push実行前にamend後の未コミット差分残置を機械的にブロック
        if _check_bash_git_push_after_amend_with_dirty_status(command, session_id, cwd):
            return 2
        # 一括ステージ実行時にセッション未編集の変更が含まれる場合の警告
        result = _check_bash_bulk_stage_with_unedited_files(command, session_id, cwd)
        if result is not None:
            emit_json(result)
            return 0
        # uv run python <path>形式の起動は非Pythonプロジェクトでブロック
        if _check_bash_uv_run_python(command, cwd):
            return 2
        # git commit未検証警告
        result = _check_bash_git_commit(command, session_id, cwd)
        if result is not None:
            emit_json(result)
            return 0
        # agent-toolkit/配下のコミット時にversion bump漏れを警告
        result = _check_bash_agent_toolkit_version_bump(command, cwd)
        if result is not None:
            emit_json(result)
            return 0
        # git log --decorate自動付与
        result = _check_bash_git_log_decorate(command, tool_input)
        if result is not None:
            emit_json(result)
            return 0
        # codex exec未決事項の念押し
        result = _check_bash_codex_exec(command)
        if result is not None:
            emit_json(result)
            return 0
        flush_pending_language_warning()
        return 0

    # Read: メインエージェントからの隔離指定リファレンスへの直接Readをブロック
    if tool_name == "Read":
        message = _check_read_isolated_reference(tool_input, session_id, payload.get("isSidechain") is True)
        if message is not None:
            print(message, file=sys.stderr)
            flush_pending_language_warning()
            return 2
        flush_pending_language_warning()
        return 0

    # Agent/Task: plan-impl-executor起動時の`plan-file-creator`の整合性チェック完了未達ブロック +
    # 規範非読込型サブエージェント起動時の、規範の明示引用漏れ警告 +
    # process-loop観測用のサブエージェント起動時刻記録 (fb-1)
    if tool_name in ("Agent", "Task"):
        subagent_type = tool_input.get("subagent_type")
        if isinstance(subagent_type, str) and subagent_type in _TRACKED_SUBAGENT_TYPES:
            _process_loop_log.append("subagent_start", type=subagent_type)
        if (
            isinstance(subagent_type, str)
            and subagent_type in _PLAN_IMPL_EXECUTOR_SUBAGENT_TYPES
            and _check_process7_completion_for_plan_impl_executor_agent(session_id, tool_input)
        ):
            return 2
        message = _check_agent_norm_reference(tool_input)
        if message is not None:
            print(message, file=sys.stderr)
        flush_pending_language_warning()
        return 0

    # Write/Edit/MultiEdit以外は全スキップ
    fields = _collect_new_fields(tool_name, tool_input)
    if fields is None:
        flush_pending_language_warning()
        return 0

    file_path_raw = tool_input.get("file_path")
    file_path = file_path_raw if isinstance(file_path_raw, str) else ""

    # --- block系check（最初の違反でexit 2）---
    if _check_mojibake(tool_name, fields):
        return 2
    # Edit/MultiEditは内部的にCRLFを透過的に維持するためチェック不要。
    # WriteのみLFで書き込むためEOLチェックを実行する。
    if tool_name == "Write" and _is_ps1(file_path) and _check_ps1_eol(tool_name, fields, file_path):
        return 2
    if _check_lockfiles(tool_name, file_path):
        return 2
    if _check_secrets(tool_name, file_path):
        return 2
    if _check_scope_escalation_in_doc_edit(tool_name, tool_input, file_path):
        return 2
    if _check_named_subagent_sendmessage_registered(tool_name, tool_input, file_path):
        return 2

    # --- warn系check（stderrに警告のみ、exit codeは0のまま）---
    _check_manifest(tool_name, file_path)
    _check_home_path(tool_name, fields, file_path)
    _check_colloquial(tool_name, fields, file_path)
    _check_style_negation(tool_name, tool_input, file_path)
    _check_frontmatter_sync_note_body_exists(tool_name, tool_input, file_path)

    flush_pending_language_warning()
    return 0


def _handle_language_check(payload: dict, session_id: str) -> tuple[int | None, str | None]:
    """直前メインエージェント応答の言語検査を実行し、セッション状態でエスカレーションを管理する。

    Returns:
        (exit code, 警告本文)のタプル。
        exit code 2: ブロック（stderrに出力済み）。
        exit code None + 本文あり: 警告（呼び出し側でadditionalContextに追記）。
        exit code None + 本文None: 対象外。

    セッション状態キー:
    - english_warning_count: 連続英語ターンのカウンタ（int）
    - english_warning_msg_id: 前回検出時のmessage ID（str）

    エスカレーションロジック:
    - WARN: message IDが前回と異なればカウンタ+1、同一なら据え置き。カウンタ≧2でブロック
    - PASS: カウンタを0にリセット
    - SKIP: カウンタ変更なし
    - ブロック後はカウンタを1に設定する（日本語に切り替わるまで毎ターンブロックを継続）
    """
    transcript_path = payload.get("transcript_path", "")
    if not isinstance(transcript_path, str) or not transcript_path:
        return (None, None)
    if payload.get("isSidechain") is True:
        return (None, None)

    outcome, body, msg_id = _response_language_check.detailed_check(transcript_path)

    if outcome is _response_language_check.CheckOutcome.SKIP:
        return (None, None)

    if outcome is _response_language_check.CheckOutcome.PASS:
        if session_id:

            def _reset_count(current: dict) -> dict | None:
                if current.get("english_warning_count", 0) == 0:
                    return None
                current["english_warning_count"] = 0
                return current

            update_state(session_id, _reset_count)
        return (None, None)

    # WARN
    if not session_id:
        return (None, body)

    # update_stateがOSErrorで失敗した場合、_incrementは実行されずcountは初期値0のまま残る。
    # この場合はブロックしない方向（安全側）にフォールバックする。
    count = 0

    def _increment(current: dict) -> dict | None:
        nonlocal count
        prev_id = current.get("english_warning_msg_id", "")
        prev_count = current.get("english_warning_count", 0)
        if msg_id and prev_id == msg_id:
            count = prev_count
            return None
        count = prev_count + 1
        current["english_warning_count"] = count
        current["english_warning_msg_id"] = msg_id
        return current

    update_state(session_id, _increment)

    if count >= 2:

        def _set_threshold(current: dict) -> dict | None:
            current["english_warning_count"] = 1
            return current

        update_state(session_id, _set_threshold)
        print(_language_notice(_response_language_check.BLOCK_BODY), file=sys.stderr)
        return (2, None)

    return (None, body)


def _append_additional_context(result: dict, suffix: str) -> None:
    """既存JSON結果の`hookSpecificOutput.additionalContext`末尾へ警告本文を追記する。

    `hookSpecificOutput`が無い・`additionalContext`が文字列でない場合は新規に設定する。
    既存内容との境界には空行を出力する。
    """
    hook_specific = result.get("hookSpecificOutput")
    if not isinstance(hook_specific, dict):
        hook_specific = {"hookEventName": "PreToolUse", "permissionDecision": "allow"}
        result["hookSpecificOutput"] = hook_specific
    existing = hook_specific.get("additionalContext")
    if isinstance(existing, str) and existing:
        hook_specific["additionalContext"] = f"{existing}\n\n{suffix}"
    else:
        hook_specific["additionalContext"] = suffix


def _collect_new_fields(tool_name: str, tool_input: dict) -> list[tuple[str, str]] | None:
    """対象ツールの「新規書き込みフィールド」を（field名, 値）のリストで返す。

    対象外ツールの場合はNoneを返す。文字列でない値はスキップする。
    """
    if tool_name == "Write":
        value = tool_input.get("content")
        return [("content", value)] if isinstance(value, str) else []
    if tool_name == "Edit":
        value = tool_input.get("new_string")
        return [("new_string", value)] if isinstance(value, str) else []
    if tool_name == "MultiEdit":
        edits = tool_input.get("edits") or []
        if not isinstance(edits, list):
            return []
        result: list[tuple[str, str]] = []
        for index, edit in enumerate(edits):
            if not isinstance(edit, dict):
                continue
            new_string = edit.get("new_string")
            if isinstance(new_string, str):
                result.append((f"edits[{index}].new_string", new_string))
        return result
    return None


def _check_mojibake(tool_name: str, fields: list[tuple[str, str]]) -> bool:
    """U+FFFD（mojibake）を検出したらTrueを返す。"""
    for field, value in fields:
        position = value.find(_REPLACEMENT_CHAR)
        if position == -1:
            continue
        start = max(0, position - 10)
        end = min(len(value), position + 11)
        sample = value[start:end]
        print(
            _llm_notice(f"blocked: U+FFFD (mojibake) detected in {tool_name}.{field}. Context: {sample!r}"),
            file=sys.stderr,
        )
        return True
    return False


def _is_ps1(file_path: str) -> bool:
    """`.ps1` / `.ps1.tmpl`の場合に真を返す。"""
    lowered = file_path.lower()
    return lowered.endswith(".ps1") or lowered.endswith(".ps1.tmpl")


def _check_ps1_eol(tool_name: str, fields: list[tuple[str, str]], file_path: str) -> bool:
    """PowerShellスクリプトへのLF-only書き込みを検出したらTrueを返す。"""
    for field, value in fields:
        if "\n" not in value:
            continue
        if "\r\n" in value:
            continue
        print(
            _llm_notice(
                f"blocked: LF-only content detected in {tool_name}.{field}."
                f" PowerShell 5.1 cannot parse .ps1 files with LF line endings; CRLF is required."
                f" Use the Edit tool for existing files (it preserves CRLF transparently)."
                f" For new files, write via Bash with a UTF-8 BOM and CRLF line endings"
                f" (e.g., printf '\\xEF\\xBB\\xBF' > file.ps1 && ... | sed 's/$/\\r/' >> file.ps1)."
                f" Target: {file_path}"
            ),
            file=sys.stderr,
        )
        return True
    return False


# --- lockfile / 生成物ディレクトリcheck ---

# （label, regex, hint）のタプル。regexはfile_path全体に対するマッチ。
_LOCKFILE_RULES: tuple[tuple[str, re.Pattern[str], str], ...] = (
    ("uv.lock", re.compile(r"(^|/)uv\.lock$"), "Use `uv add` to add dependencies and `uv remove` to remove them."),
    (
        "pnpm-lock.yaml",
        re.compile(r"(^|/)pnpm-lock\.yaml$"),
        "Use `pnpm add` to add dependencies and `pnpm remove` to remove them.",
    ),
    ("package-lock.json", re.compile(r"(^|/)package-lock\.json$"), "Use `npm install <pkg>` to add dependencies."),
    ("yarn.lock", re.compile(r"(^|/)yarn\.lock$"), "Use `yarn add` to add dependencies."),
    ("Cargo.lock", re.compile(r"(^|/)Cargo\.lock$"), "Use `cargo add` to add dependencies."),
    ("mise.lock", re.compile(r"(^|/)mise\.lock$"), "Use `mise use` / `mise install` for tool management."),
    (
        ".venv/",
        re.compile(r"(^|/)\.venv/"),
        "Do not edit virtual environment files directly; rebuild with uv or similar.",
    ),
    (
        "node_modules/",
        re.compile(r"(^|/)node_modules/"),
        "node_modules is a generated directory; do not edit it directly.",
    ),
)


def _check_lockfiles(tool_name: str, file_path: str) -> bool:
    """lockfileや生成物ディレクトリへの直接編集を検出した場合に真を返す。"""
    if not file_path:
        return False
    normalized = file_path.replace("\\", "/")
    for label, pattern, hint in _LOCKFILE_RULES:
        if pattern.search(normalized):
            print(
                _llm_notice(f"blocked: direct edit of {label} is prohibited by {tool_name}. {hint} Target: {file_path}"),
                file=sys.stderr,
            )
            return True
    return False


# --- シークレット / 鍵ファイルcheck ---

_SECRETS_PATTERN = re.compile(
    r"(^|/)("
    r"\.env(\..+)?"
    r"|\.encrypt_key"
    r"|\.secret_key"
    r"|github_action(\.pub)?"
    r"|[^/]+\.(pem|key)"
    r")$"
)

_SECRETS_EXEMPT_SUFFIXES: tuple[str, ...] = (".example", ".sample", "-example", "-sample")


def _check_secrets(tool_name: str, file_path: str) -> bool:
    """シークレット / 鍵ファイルへの直接編集を検出した場合に真を返す。"""
    if not file_path:
        return False
    normalized = file_path.replace("\\", "/")
    if normalized.endswith(_SECRETS_EXEMPT_SUFFIXES):
        return False
    if _SECRETS_PATTERN.search(normalized):
        print(
            _llm_notice(
                f"blocked: direct edit of secret / key files is prohibited by {tool_name}."
                f" Accidental edits can cause service outages or data leaks. Target: {file_path}"
            ),
            file=sys.stderr,
        )
        return True
    return False


# --- scope-escalationフレーズ転記check (block) ---

# 対象ドキュメント判定パターン。
# `agent-toolkit/rules/`配下の.mdと`agent-toolkit/skills/**/SKILL.md`を対象とし、
# 途中に`references/`を含むパスは隔離ファイル扱いで除外する。
_SCOPE_ESCALATION_DOC_TARGET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(^|/)agent-toolkit/rules/[^/]+\.md$"),
    re.compile(r"(^|/)agent-toolkit/skills/(?:(?!.*/references/).)+/SKILL\.md$"),
)


def _is_scope_escalation_target_doc(file_path: str) -> bool:
    """対象ドキュメント（agent-toolkit/rules配下・SKILL.md・計画ファイル）への編集か判定する。"""
    if not file_path:
        return False
    normalized = file_path.replace("\\", "/")
    if any(p.search(normalized) is not None for p in _SCOPE_ESCALATION_DOC_TARGET_PATTERNS):
        return True
    return is_plan_file(file_path)


def _match_scope_escalation_increase(
    old: str,
    new: str,
    *,
    exclude_categories: Iterable[str] | None = None,
) -> tuple[str, str] | None:
    """new側でフレーズ出現回数がold側より増加したカテゴリと、そのマッチ文言を返す。

    `_SCOPE_ESCALATION_PHRASES`を走査し、各パターンのfindall件数を比較する。
    new側件数がold側件数を上回るカテゴリを最初に検出した時点で`(category, matched_phrase)`を返す。
    既存文字列の保持時はold=new同数となり通過する（既存保持部分での誤検出を防ぐ）。
    カテゴリ別除外は`_scope_escalation._apply_category_exclusions`をnew・old双方へ適用し、
    priority-consult他のカテゴリ別除外を両経路で共有する。
    `exclude_categories`を指定した場合は当該カテゴリ集合を照合対象から除外する。
    matched_phraseはnew側でのパターンマッチテキストそのまま。
    増加が無い場合はNoneを返す。
    """
    excluded = frozenset(exclude_categories) if exclude_categories is not None else frozenset()
    for category, pattern in _SCOPE_ESCALATION_PHRASES:
        if category in excluded:
            continue
        target_new = _apply_category_exclusions(new, category)
        target_old = _apply_category_exclusions(old, category)
        if len(pattern.findall(target_new)) > len(pattern.findall(target_old)):
            m = pattern.search(target_new)
            matched = m.group(0) if m is not None else ""
            return (category, matched)
    return None


def _extract_plan_scope_escalation_body(text: str, file_path: str) -> str:
    """計画ファイル対象時のみscope-escalation走査本文からフェンス等除外領域を取り除く。

    `_plan_format.iter_markdown_body_lines`（フロントマター・コードフェンス・
    複数行HTMLコメントを除外するSSOT実装）を計画ファイル検査経路専用に適用するヘルパー。
    テストfixture例を格納する`text`フェンス内の語彙が誤検出される問題（fb-7）を解消する。
    計画ファイル以外（`agent-toolkit/rules/`配下・SKILL.md等の規範文書本体）は
    検出精度を変えないため`text`をそのまま返す。
    """
    if not is_plan_file(file_path):
        return text
    return "\n".join(line for _, line in _plan_format.iter_markdown_body_lines(text))


def _apply_single_edit(base_content: str, edit_dict: dict, *, empty_base_fallback: bool = False) -> str | None:
    """単一Edit入力を既存本文へ適用した文字列を返す。

    `new_string`が文字列でない場合はNoneを返す。
    `old_string`が文字列でない場合は空文字列扱いとし、既存の緩和処理と同じくfail-closeで検査を継続する。
    `empty_base_fallback`が真で既存本文が空の場合は、Edit経路の読込失敗時検査漏れを避けるため`new_string`を返す。
    """
    old_string = edit_dict.get("old_string") or ""
    new_string = edit_dict.get("new_string")
    if not isinstance(new_string, str):
        return None
    old_string = old_string if isinstance(old_string, str) else ""
    if empty_base_fallback and not base_content:
        return new_string
    replace_all = bool(edit_dict.get("replace_all"))
    if replace_all:
        return base_content.replace(old_string, new_string)
    return base_content.replace(old_string, new_string, 1)


def _check_scope_escalation_in_doc_edit(tool_name: str, tool_input: dict, file_path: str) -> bool:
    """対象ドキュメントへの編集時、フレーズ出現回数の増加を検出した場合にblockする。

    対象は`agent-toolkit/rules/`配下・`agent-toolkit/skills/**/SKILL.md`（`references/`配下を除く）・
    計画ファイル（`~/.claude/plans/`直下）。
    Edit/MultiEditは既存ファイル本文を読み込み、各edit適用前後の全文を
    `_match_scope_escalation_increase`で比較してnew側件数 > old側件数のカテゴリを検出する。
    MultiEditは各edit単位で適用前後を比較し、同一MultiEdit内の除去と追加による相殺を検出漏れにしない。
    既存文字列の保持時は件数同数で通過する（誤検出解消）。
    Writeは`content`全文を検査する。
    計画ファイル対象時は`_extract_plan_scope_escalation_body`でフェンス・フロントマター・
    HTMLコメント区間を走査対象から除外する。
    Edit/MultiEditでも全文へ適用するため、フェンス開始・終了行がold/new_string外にある場合も除外境界を維持する。
    規範文書本体は対象外で検出精度を変えない。
    判定パターンは`_SCOPE_ESCALATION_PHRASES`を再利用しAskUserQuestion checkと同一の検出基準とする。
    `agent-toolkit:agent-standards`「コンテキスト汚染の回避」節に従い、hookブロックメッセージは
    利用者がブロック契機を特定できるようマッチ文言を含める（スキル本文・ルール本文・テストコードへの
    転記禁止とは別扱いとする）。
    """
    if not _is_scope_escalation_target_doc(file_path):
        return False
    # plan fileでは`plan-deferral-onset`をMarkdown除外領域（text/HTMLコメント/`## 背景`配下）を考慮する
    # `_check_plan_file_no_deferral_expression`が担当するため、本checkでは除外する。
    exclude_categories: frozenset[str] = frozenset({"plan-deferral-onset"}) if is_plan_file(file_path) else frozenset()
    detection: tuple[str, str, str] | None = None
    if tool_name == "Write":
        content = tool_input.get("content")
        if isinstance(content, str):
            body = _extract_plan_scope_escalation_body(content, file_path)
            match_result = _match_scope_escalation(body, exclude_categories=exclude_categories)
            if match_result is not None:
                category, matched = match_result
                detection = ("content", category, matched)
    elif tool_name == "Edit":
        try:
            pre_content = pathlib.Path(file_path).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            pre_content = ""
        post_content = _apply_single_edit(pre_content, tool_input, empty_base_fallback=True)
        if post_content is not None:
            pre_body = _extract_plan_scope_escalation_body(pre_content, file_path)
            post_body = _extract_plan_scope_escalation_body(post_content, file_path)
            match_result = _match_scope_escalation_increase(pre_body, post_body, exclude_categories=exclude_categories)
            if match_result is not None:
                category, matched = match_result
                detection = ("new_string", category, matched)
    elif tool_name == "MultiEdit":
        edits = tool_input.get("edits") or []
        if isinstance(edits, list):
            try:
                current = pathlib.Path(file_path).read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                current = ""
            for index, edit in enumerate(edits):
                if not isinstance(edit, dict):
                    continue
                next_current = _apply_single_edit(current, edit, empty_base_fallback=False)
                if next_current is None:
                    continue
                pre_body = _extract_plan_scope_escalation_body(current, file_path)
                post_body = _extract_plan_scope_escalation_body(next_current, file_path)
                match_result = _match_scope_escalation_increase(pre_body, post_body, exclude_categories=exclude_categories)
                if match_result is not None:
                    category, matched = match_result
                    detection = (f"edits[{index}].new_string", category, matched)
                    break
                current = next_current
    if detection is None:
        return False
    field, category, matched = detection
    print(
        _llm_notice(
            f"blocked: scope-escalation phrase (category: {category})"
            f" detected in {tool_name}.{field}. Target: {file_path}."
            f" matched: {_truncate_matched_phrase(matched)}."
            f" See {_scope_escalation_agent_md_reference(category)}."
            f" See agent-toolkit/skills/agent-standards/SKILL.md 'コンテキスト汚染の回避' section and"
            f" `references/scope-escalation-phrases.md` isolation rule."
            f" Do not transcribe the detected pattern body into skill body, rule body, or test code."
            f"{_format_scope_escalation_alternatives(category)}",
            tag="block",
        ),
        file=sys.stderr,
    )
    return True


# --- manifest手編集check (warn) ---

_MANIFEST_RULES: tuple[tuple[str, re.Pattern[str], str], ...] = (
    (
        "pyproject.toml",
        re.compile(r"(^|/)pyproject\.toml$"),
        (
            "For [project.dependencies] / [project.optional-dependencies],"
            " use `uv add` / `uv remove` (to keep uv.lock in sync)."
            " For [tool.*] or version edits, proceed as-is."
        ),
    ),
    (
        "package.json",
        re.compile(r"(^|/)package\.json$"),
        (
            "For dependency edits, use `pnpm add` / `pnpm remove`"
            " (to keep pnpm-lock.yaml in sync). For scripts or metadata edits, proceed as-is."
        ),
    ),
)


def _check_manifest(tool_name: str, file_path: str) -> bool:
    """manifest手編集を検出したら警告を表示して真を返す（warnのみ、exit codeは変えない）。"""
    if not file_path:
        return False
    normalized = file_path.replace("\\", "/")
    for label, pattern, hint in _MANIFEST_RULES:
        if pattern.search(normalized):
            print(
                _llm_notice(
                    f"editing {label} via {tool_name}. {hint}",
                    tag="warn",
                ),
                file=sys.stderr,
            )
            return True
    return False


# --- ホームディレクトリパス混入check (warn) ---

# 混入を許容するファイル末尾パターン（ローカル設定やログなど）
_HOME_PATH_SKIP_SUFFIXES: tuple[str, ...] = (
    ".local.md",
    ".local.json",
    ".local.yaml",
    ".local.yml",
    ".local.toml",
    ".jsonl",
    ".log",
)


def _check_home_path(tool_name: str, fields: list[tuple[str, str]], file_path: str) -> bool:
    """ホームディレクトリの絶対パス混入を検出したら警告を表示して真を返す。

    リポジトリ管理ファイルに`/home/user/...`のような環境依存パスが書き込まれると
    他環境での再現性が失われるため警告する。警告のみでeditは継続（warn）。
    """
    home_str = str(pathlib.Path.home())
    # ルートなど極端に短いパスは誤検出を避けてスキップ。
    if len(home_str) < 3:
        return False

    normalized_path = file_path.replace("\\", "/")
    if normalized_path.endswith(_HOME_PATH_SKIP_SUFFIXES):
        return False
    if normalized_path.endswith("/CLAUDE.local.md") or normalized_path == "CLAUDE.local.md":
        return False
    if normalized_path.endswith("/.claude/settings.local.json"):
        return False

    # POSIX正規化された両表記で検査（WindowsからPOSIX風パスが混入するケースに対応）
    candidates = {home_str, home_str.replace("\\", "/")}

    for field, value in fields:
        for home in candidates:
            position = value.find(home)
            if position == -1:
                continue
            start = max(0, position - 20)
            end = min(len(value), position + len(home) + 20)
            sample = value[start:end]
            print(
                _llm_notice(
                    f"home directory absolute path ({home}) detected in {tool_name}.{field}."
                    f" In version-controlled files, use `~`, `$HOME`, or `pathlib.Path.home()`"
                    f" instead to avoid environment-dependent paths."
                    f" Context: {sample!r}",
                    tag="warn",
                ),
                file=sys.stderr,
            )
            return True
    return False


# --- 口語表現混入check (warn) ---

# モジュールロード時に1回だけコンパイルする。
# 検出語そのものをコーディングエージェントのコンテキストへ持ち込まないよう、
# 本ファイルからパターンの実体を文字列で参照しない。
_COLLOQUIAL_DENY_PATTERNS = _colloquial_check.load_patterns(_colloquial_check.DENY_PATH)
_COLLOQUIAL_ALLOW_PATTERNS = _colloquial_check.load_patterns(_colloquial_check.ALLOW_PATH)


def _check_colloquial(tool_name: str, fields: list[tuple[str, str]], file_path: str) -> bool:
    """口語的な日本語表現の混入を検出して警告する（warn）。

    検出した語そのものは出力に含めない（コーディングエージェントのコンテキスト汚染防止）。
    allowlistに一致する部分を先に除去してからdenylistを適用し、
    複合動詞・複合名詞などの標準用語が誤検出されることを抑える。
    """
    for field, value in fields:
        if not value:
            continue
        if _colloquial_check.first_hit(value, _COLLOQUIAL_DENY_PATTERNS, _COLLOQUIAL_ALLOW_PATTERNS):
            print(
                _llm_notice(
                    f"colloquial Japanese expressions detected in {tool_name}.{field}."
                    f" Rewrite using formal written-style expressions"
                    f" (standard technical terminology, dictionary form,"
                    f" no metaphorical verbs) per 04-styles.md '日本語の品質を保つ' section."
                    f" Target: {file_path}",
                    tag="warn",
                ),
                file=sys.stderr,
            )
            return True
    return False


# --- 「Xを根拠にYしない」形式の増加検出 (warn, FB10) ---

# 04-styles.md「日本語の品質を保つ」節が指摘する誤読リスクのある禁止規定形式。
# 「Xでなければ`Y`してよい」と誤読される可能性があるため、全称否定形への書き換えを推奨する。
_STYLE_NEGATION_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"([^、\s]{1,20})を根拠に([^、\s]{1,20})しない"),
    re.compile(r"([^、\s]{1,20})を理由に([^、\s]{1,20})しない"),
)


def _is_style_negation_target_doc(file_path: str) -> bool:
    """対象ドキュメント（文書サイズ上限対象と同一の判定基準）への編集かを判定する。"""
    return _plan_format.is_agent_doc_target_file(file_path)


def _count_style_negation_matches(text: str) -> int:
    """`_STYLE_NEGATION_PATTERNS`の総マッチ件数を返す。"""
    return sum(len(pattern.findall(text)) for pattern in _STYLE_NEGATION_PATTERNS)


def _check_style_negation(tool_name: str, tool_input: dict, file_path: str) -> bool:
    """『Xを根拠にYしない』『Xを理由にYしない』形式の増加を検出したら警告を表示して真を返す（warn）。

    `_match_scope_escalation_increase`と同方式でold・new差分による増加時のみ警告する
    （既存文字列の保持時は件数同数で誤検出しない）。Writeは`content`全文のマッチ件数が
    1件以上であれば警告する。
    """
    if not _is_style_negation_target_doc(file_path):
        return False
    increased = False
    if tool_name == "Write":
        content = tool_input.get("content")
        if isinstance(content, str):
            increased = _count_style_negation_matches(content) > 0
    elif tool_name == "Edit":
        old_string = tool_input.get("old_string") or ""
        new_string = tool_input.get("new_string")
        if isinstance(new_string, str):
            old_string = old_string if isinstance(old_string, str) else ""
            increased = _count_style_negation_matches(new_string) > _count_style_negation_matches(old_string)
    elif tool_name == "MultiEdit":
        edits = tool_input.get("edits") or []
        if isinstance(edits, list):
            for edit in edits:
                if not isinstance(edit, dict):
                    continue
                old_string = edit.get("old_string") or ""
                new_string = edit.get("new_string")
                if not isinstance(new_string, str):
                    continue
                old_string = old_string if isinstance(old_string, str) else ""
                if _count_style_negation_matches(new_string) > _count_style_negation_matches(old_string):
                    increased = True
                    break
    if not increased:
        return False
    print(
        _llm_notice(
            f"detected an increase in meta-norm phrases of the form '`X`を根拠に`Y`しない' / '`X`を理由に`Y`しない'"
            f" via {tool_name}. Target: {file_path}."
            " Such phrasing risks being misread as 'if not X, then it is fine to Y'."
            " Consider rewriting to the universal-negation form"
            " ('いかなる理由（例: X）があっても`Y`しない')."
            " See 04-styles.md '日本語の品質を保つ' section.",
            tag="warn",
        ),
        file=sys.stderr,
    )
    return True


# --- frontmatter同期注記の本体該当語句の実在検証check (warn, feedback 2) ---

# 対象は`agent-toolkit/`・`.chezmoi-source/dot_claude/`配下の`.md`ファイル全般
# （`_plan_format.is_agent_doc_target_file`より対象範囲が広い専用判定）。
_FRONTMATTER_SYNC_TARGET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(^|/)agent-toolkit/.+\.md$"),
    re.compile(r"(^|/)\.chezmoi-source/dot_claude/.+\.md$"),
)

# frontmatter区間（`^---$`〜`^---$`）の抽出用。
_FRONTMATTER_BLOCK_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---(?:\r?\n|\Z)", re.DOTALL)

# 同期注記コメント行の判定トリガー。
# `# ...と意図的に重複させている` / `# ...と意図的に同期する` / `# 同期注記:`の3形式を検出する。
_SYNC_NOTE_TRIGGER_RE = re.compile(r"と意図的に(?:重複させている|同期する)|同期注記:")

# 注記本文からの参照ファイルパス抽出（`<name>.md`形式）。
_SYNC_NOTE_FILE_PATH_RE = re.compile(r"[\w.\-/]+\.md")

# 注記本文からの節名抽出。`「<節名>」節`形式とバッククォート囲み`<節名>節`形式の両方に対応する。
_SYNC_NOTE_SECTION_KAGI_RE = re.compile(r"「([^」]+)」節")
_SYNC_NOTE_SECTION_QUOTED_RE = re.compile(r"`([^`]+)`節")


def _is_frontmatter_sync_check_target(file_path: str) -> bool:
    """frontmatter同期注記検査の対象ファイルかを判定する。

    対象は`agent-toolkit/`・`.chezmoi-source/dot_claude/`配下の`.md`ファイル、
    および計画ファイル（`is_plan_file`が真のパス）。
    """
    if not file_path:
        return False
    normalized = file_path.replace("\\", "/")
    if any(p.search(normalized) is not None for p in _FRONTMATTER_SYNC_TARGET_PATTERNS):
        return True
    return is_plan_file(file_path)


def _extract_frontmatter_sync_notes(content: str) -> list[str]:
    """frontmatter区間から同期注記コメントブロックの本文一覧を抽出する。

    `#`始まり行が連続するコメントブロックを走査単位とし、ブロック内をさらに
    `_SYNC_NOTE_TRIGGER_RE`一致行を境界として複数の注記へ分離する
    （`_split_sync_note_block`参照）。トリガー語・参照先ファイルパスが別行に分かれる形式
    （1行目に参照先パス、後続行にトリガー語を含む宣言文）は同一注記として結合する一方、
    空行を置かず連続して書かれた独立した複数の同期注記宣言が1つの注記へ混在する事態を避ける。
    frontmatter未使用ファイル（先頭が`---`で始まらない）は空リストを返す
    （`## 背景`原文転記領域はfrontmatter区間の外側のため走査対象に含まれない）。
    """
    match = _FRONTMATTER_BLOCK_RE.match(content)
    if match is None:
        return []
    notes: list[str] = []
    current_block: list[str] = []
    for line in match.group(1).splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            current_block.append(stripped.lstrip("#").strip())
            continue
        notes.extend(_split_sync_note_block(current_block))
        current_block = []
    notes.extend(_split_sync_note_block(current_block))
    return notes


def _split_sync_note_block(block: list[str]) -> list[str]:
    """連続コメント行ブロックをトリガー行境界で複数の同期注記へ分離する。

    トリガー行（`_SYNC_NOTE_TRIGGER_RE`一致行）に到達するたびそこまでの蓄積行を1件の注記として確定し、
    次のトリガー行に向けて新たな蓄積を開始する。これにより「1行目に参照先パス、
    後続行にトリガー語を含む宣言文」形式は同一注記として結合されつつ、
    空行を置かず連続する独立した複数の同期注記宣言は別々の注記に分離される。
    最終トリガー行より後に続く行（後続の補足）はトリガーを含まないため、
    直前に確定した注記へ継続として統合する。ブロック全体にトリガー行が1つも無い場合は空リストを返す。
    """
    notes: list[list[str]] = []
    current: list[str] = []
    for body in block:
        current.append(body)
        if _SYNC_NOTE_TRIGGER_RE.search(body):
            notes.append(current)
            current = []
    if current:
        if notes:
            notes[-1].extend(current)
        else:
            return []
    return [" ".join(note) for note in notes]


def _extract_sync_note_references(note: str) -> tuple[list[str], list[str]]:
    """同期注記本文から参照ファイルパス一覧と節名一覧を抽出する。"""
    paths = _SYNC_NOTE_FILE_PATH_RE.findall(note)
    sections = _SYNC_NOTE_SECTION_KAGI_RE.findall(note) + _SYNC_NOTE_SECTION_QUOTED_RE.findall(note)
    return paths, sections


def _resolve_referenced_path(file_path: str, referenced: str) -> pathlib.Path | None:
    """`file_path`の祖先ディレクトリを起点に`referenced`（相対パス）の実ファイルを探索する。

    frontmatterの同期注記は同一ディレクトリまたは近隣ディレクトリの兄弟ファイルを
    裸ファイル名（例: `spec-driven-implementer.md`）で参照する形式が実運用で使われるため、
    `.git`を持つ祖先（リポジトリルート）を発見しても即確定とせず、以下の順に実在確認する。

    1. `file_path`の各祖先ディレクトリ（近い順。同一ディレクトリの兄弟ファイル参照に対応）
    2. リポジトリルート配下の`agent-toolkit/agents/`・`agent-toolkit/rules/`・
       `agent-toolkit/skills/`（近隣ディレクトリの参照に対応。`.git`祖先が見つかった場合のみ）

    いずれの経路でも実在しない場合は`None`を返す。
    """
    start = pathlib.Path(file_path).resolve().parent
    ancestors = (start, *start.parents)
    search_roots: list[pathlib.Path] = list(ancestors)

    repo_root: pathlib.Path | None = None
    for candidate in ancestors:
        if (candidate / ".git").exists():
            repo_root = candidate
            break
    if repo_root is not None:
        search_roots.extend(
            repo_root / neighbor for neighbor in ("agent-toolkit/agents", "agent-toolkit/rules", "agent-toolkit/skills")
        )

    for candidate in search_roots:
        resolved = candidate / referenced
        if resolved.exists():
            return resolved
    return None


def _check_frontmatter_sync_note_body_exists(tool_name: str, tool_input: dict, file_path: str) -> bool:
    r"""frontmatter同期注記が指す本体側の該当語句の実在を検査して警告する（warn）。

    対象は`_is_frontmatter_sync_check_target`が真のファイル。
    frontmatter区間から`# ...と意図的に重複させている`・`# ...と意図的に同期する`・
    `# 同期注記:`形式のコメント行（同期注記）を抽出し、注記本文が参照するファイルパス
    （`<name>.md`形式）と節名（`「<節名>」節`または`` `<節名>`節 ``形式）の実在を照合する。

    - 参照ファイルパスがリポジトリ内に実在しない場合は警告する
    - 節名は、自ファイルの適用後本文（frontmatter区間を除く）と実在する参照ファイル本文を
      連結した対象に対し見出し一致（`^#+\s*<節名>$`）または部分文字列一致のいずれかで照合し、
      いずれも一致しない場合は警告する

    表記揺れ（同旨表現の同義語形式）による誤検出を許容するためblock化しない。
    """
    if not _is_frontmatter_sync_check_target(file_path):
        return False
    content = _materialize_post_edit_content(tool_name, tool_input, file_path)
    if content is None:
        return False
    notes = _extract_frontmatter_sync_notes(content)
    if not notes:
        return False

    # 節名照合の自ファイル側corpusはfrontmatter区間を除いた本文のみとする。
    # frontmatter内の同期注記コメント自体が対象の節名文字列を引用形式で含むため、
    # frontmatterを含めたまま照合すると常に自明一致（誤検出解消の形骸化）してしまう。
    frontmatter_match = _FRONTMATTER_BLOCK_RE.match(content)
    self_body = content[frontmatter_match.end() :] if frontmatter_match is not None else content

    reasons: list[str] = []
    for note in notes:
        paths, sections = _extract_sync_note_references(note)
        referenced_bodies: list[str] = []
        for path in paths:
            resolved = _resolve_referenced_path(file_path, path)
            if resolved is None:
                reasons.append(f"referenced file path does not exist: {path}")
                continue
            try:
                referenced_bodies.append(resolved.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError):
                reasons.append(f"failed to read referenced file: {path}")
        # 節名は自ファイル本文内で完結する場合（自己参照）と、他ファイル参照を伴う場合の双方があるため、
        # 自ファイル本文（frontmatter除く）と参照先ファイル本文の双方を照合対象に含める。
        search_corpus = "\n".join([self_body, *referenced_bodies])
        for section in sections:
            heading_pattern = re.compile(rf"^#+\s*{re.escape(section)}\s*$", re.MULTILINE)
            if heading_pattern.search(search_corpus) is None and section not in search_corpus:
                reasons.append(f"section name does not exist: {section}")

    if not reasons:
        return False
    print(
        _llm_notice(
            "the body-side identifier referenced by the frontmatter sync note may not exist"
            f" ({tool_name}, target: {file_path}): {'; '.join(reasons)}."
            " See norm-revision-checklist.md '規範対象範囲の網羅確認' section and verify that the"
            " sync note body matches the target file and section name.",
            tag="warn",
        ),
        file=sys.stderr,
    )
    return True


# named background起動想定サブエージェント判定用のキーワード。
# frontmatterから`tools:`欄値行を除外した残りと本文全体を結合した判定対象範囲に
# これらのいずれかが出現するファイルを判定対象とし、
# frontmatter`tools:`欄への`SendMessage`登録有無を検査する。
_NAMED_SUBAGENT_MARKER_RE = re.compile(r"SendMessage|能動送付")

# `tools:`欄の値パースパターン（トークン境界: カンマ・空白・改行）。
# 完全一致比較のため値文字列をトークン集合へ分解する。
_TOOLS_TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z_0-9]*")

# frontmatter`tools:`欄の判定パターン。
# `tools:`に続いて値（インライン形式・ブロック形式）が指定されている場合を判定する。
_FRONTMATTER_TOOLS_LINE_RE = re.compile(r"^tools:[ \t]*(.*)$", re.MULTILINE)


def _is_named_subagent_definition_target(file_path: str) -> bool:
    """Named subagent SendMessage登録検査の対象ファイルかを判定する。

    対象は`agent-toolkit/agents/`配下の`.md`ファイル。
    絶対パス・相対パス（`agent-toolkit/agents/<name>.md`形式）の双方で検出する。
    """
    if not file_path:
        return False
    normalized = pathlib.PurePosixPath(file_path.replace("\\", "/")).as_posix()
    if not normalized.endswith(".md"):
        return False
    return "/agent-toolkit/agents/" in normalized or normalized.startswith("agent-toolkit/agents/")


def _extract_frontmatter_tools_field(content: str) -> tuple[bool, list[str], tuple[int, int] | None]:
    """frontmatter区間から`tools:`欄の存在有無・値トークン一覧・値行の(開始, 終了)位置を抽出する。

    Returns:
        (tools欄明示ありフラグ, 値トークン一覧, 値行のcontent内(開始, 終了)位置)。
        次のいずれの場合も(False, [], None)を返す。
        `tools:`行が存在しない場合、インライン値が空欄（例: `tools:`単独）の場合、
        ブロック形式で`- <name>`項目が0件の場合。
        値がインライン形式・ブロック形式のいずれの場合もトークン集合へ分解して返す。
        値行位置はfrontmatter外の本文検査から`tools:`欄値行を除外する用途で使う。
    """
    match = _FRONTMATTER_BLOCK_RE.match(content)
    if match is None:
        return False, [], None
    frontmatter_body = match.group(1)
    fb_offset = match.start(1)
    tools_match = _FRONTMATTER_TOOLS_LINE_RE.search(frontmatter_body)
    if tools_match is None:
        return False, [], None
    inline_value = tools_match.group(1).strip()
    value_start = fb_offset + tools_match.start()
    value_end = fb_offset + tools_match.end()
    if inline_value:
        tokens = _TOOLS_TOKEN_RE.findall(inline_value)
        return (bool(tokens), tokens, (value_start, value_end))
    block_tokens: list[str] = []
    after_tools_offset = tools_match.end()
    remaining = frontmatter_body[after_tools_offset:]
    consumed = 0
    for line in remaining.splitlines(keepends=True):
        stripped = line.lstrip()
        if stripped.strip() == "":
            consumed += len(line)
            continue
        if stripped.startswith("- "):
            block_tokens.append(stripped[2:].strip())
            consumed += len(line)
            continue
        break
    if not block_tokens:
        return False, [], None
    value_end = fb_offset + after_tools_offset + consumed
    return True, block_tokens, (value_start, value_end)


def _check_named_subagent_sendmessage_registered(tool_name: str, tool_input: dict, file_path: str) -> bool:
    """Named background起動想定のサブエージェント定義への`SendMessage`ツール登録欠落をblockする。

    対象は`agent-toolkit/agents/`配下の`.md`。
    frontmatterから`tools:`欄値行を除外した残りと本文全体を結合した判定対象範囲に
    `SendMessage`または「能動送付」の言及があり、
    かつfrontmatter`tools:`欄が明示的に指定されており（全ツール許容の暗黙状態ではない）、
    かつ`SendMessage`がツール名として完全一致で登録されていない場合にblockする。

    `tools:`欄自体が無いファイル（全ツール許容）と`tools:`欄が空欄・ブロック項目0件のファイルは、
    能動送付手段が暗黙付与または無指定として扱われ、いずれも合格扱いとする。
    """
    if not _is_named_subagent_definition_target(file_path):
        return False
    content = _materialize_post_edit_content(tool_name, tool_input, file_path)
    if content is None:
        return False
    tools_explicit, tools_tokens, tools_value_range = _extract_frontmatter_tools_field(content)
    if not tools_explicit:
        return False
    scan_target = (
        content[: tools_value_range[0]] + content[tools_value_range[1] :] if tools_value_range is not None else content
    )
    if _NAMED_SUBAGENT_MARKER_RE.search(scan_target) is None:
        return False
    if "SendMessage" in tools_tokens:
        return False
    print(
        _llm_notice(
            "blocked: named background subagent definition references SendMessage or 能動送付"
            f" but frontmatter tools field does not include SendMessage ({tool_name}, target: {file_path})."
            " Add SendMessage to the tools list so the subagent can actively send its completion report,"
            " per agent-toolkit/rules/03-claude-code.md 'サブエージェントの活用' section."
        ),
        file=sys.stderr,
    )
    return True


# --- plan mode中のplan file編集をplan-modeスキル未起動の場合にブロック ---

_PLAN_FILE_EDIT_TOOLS = frozenset({"Write", "Edit", "MultiEdit"})


def _check_plan_mode_skill_first(
    tool_name: str,
    tool_input: dict,
    session_id: str,
) -> bool:
    """plan-modeスキル未起動のままplan fileを編集しようとした場合にブロックする。

    判定条件:

    - `session_id`が空でない（空ならセッション状態を取得できず判定不能のためスキップ）
    - セッション状態の`plan_mode_skill_invoked`が偽
    - `tool_name`が`Write` / `Edit` / `MultiEdit`のいずれか
    - 対象の`file_path`が`~/.claude/plans/`直下の計画ファイル

    `permission_mode`の値に依らず適用する（plan mode外でも計画ファイル編集時には同様に違反が起こり得るため）。
    サブエージェント経由の呼び出しでも同一の判定が働く
    （本checkは`isSidechain`を参照せず、`permission_mode`とセッション状態のみで判定するため）。
    plan file編集に至るまでは警告を表示しない
    （`process-feedbacks`等の他スキル呼び出し・通常のRead・Bash操作は素通りする）。
    """
    if not session_id:
        return False
    if tool_name not in _PLAN_FILE_EDIT_TOOLS:
        return False
    file_path_raw = tool_input.get("file_path")
    if not isinstance(file_path_raw, str) or not is_plan_file(file_path_raw):
        return False
    state = read_state(session_id)
    if state.get("plan_mode_skill_invoked", False):
        return False
    print(
        _llm_notice(
            "blocked: attempting to edit a plan file without invoking `agent-toolkit:plan-mode` skill."
            " Invoke the skill first and restart from Phase 1 (Initial Understanding)"
            " before writing to the plan file.",
            tag="block",
        ),
        file=sys.stderr,
    )
    return True


# --- plan-modeスキル起動後、計画ファイル未作成のままagent-toolkit配下の直接編集連続をブロック ---


# `_check_direct_agent_toolkit_edits_after_plan_mode`専用の配布先追加パターン。
# 原本パス（`agent-toolkit/rules/`・`agent-toolkit/skills/.../SKILL.md`・
# `agent-toolkit/skills/.../references/`・`agent-toolkit/agents/`）は
# `_plan_format.is_agent_doc_target_file`のSSOTを再利用して判定するため本定数へ列挙しない。
# 本定数は原本パスから配布された実在経路のみを追加対象として保持する。
#
# 実在する配布経路は次の2系統である。
#
# - `agent-toolkit/rules/` → `~/.claude/rules/agent-toolkit/`
#   （`pytools/_internal/sync_agent_toolkit_rules.py`によるcopy sync）
# - `~/.claude/plugins/cache/<owner>-<repo>/agent-toolkit/`
#   （Claude Codeのプラグインマーケットプレイス経由の配布展開先）
#
# `.claude/skills/agent-toolkit*/`および`.chezmoi-source/dot_claude/`配下への
# agent-toolkit経由の配布経路は存在しないため本定数の対象に含めない。
# `AGENTS.md`・`CLAUDE.md`のbasename一致（`_plan_format.AGENT_DOC_TARGET_BASENAMES`）は
# プロジェクトごとの文書へ波及するため本checkの対象からは除外する
# （本checkはagent-toolkit本体への連続直接編集の抑止を目的とし、
# プロジェクトごとの`AGENTS.md`・`CLAUDE.md`編集は本目的の対象外）。
_DIRECT_AGENT_TOOLKIT_DISTRIBUTION_PATTERNS: tuple[re.Pattern[str], ...] = (
    # `agent-toolkit/rules/` → `~/.claude/rules/agent-toolkit/`
    # （`pytools/_internal/sync_agent_toolkit_rules.py`によるcopy sync）
    re.compile(r"(^|/)\.claude/rules/agent-toolkit/.+\.md$"),
    # `~/.claude/plugins/cache/<owner>-<repo>/agent-toolkit/`
    # （Claude Codeのプラグインマーケットプレイス経由の配布展開先）
    re.compile(r"(^|/)\.claude/plugins/cache/[^/]+/agent-toolkit/.+\.md$"),
)


def _is_direct_agent_toolkit_edit_target(file_path: str) -> bool:
    """`_check_direct_agent_toolkit_edits_after_plan_mode`の対象パス判定。

    原本パスは`_plan_format.is_agent_doc_target_file`のSSOTを再利用して判定する
    （`agent-toolkit/rules/`・`agent-toolkit/skills/.../SKILL.md`・
    `agent-toolkit/skills/.../references/`・`agent-toolkit/agents/`・
    `.chezmoi-source/dot_claude/rules/`を含む）。
    加えて、実在する配布経路（`~/.claude/rules/agent-toolkit/`・
    `~/.claude/plugins/cache/*/agent-toolkit/`）を
    `_DIRECT_AGENT_TOOLKIT_DISTRIBUTION_PATTERNS`で追加照合する。
    `AGENTS.md`・`CLAUDE.md`のbasename一致はプロジェクトごとの文書へも
    波及するため本checkの対象外とする。
    """
    if not isinstance(file_path, str) or not file_path:
        return False
    normalized = file_path.replace("\\", "/")
    # basename一致（AGENTS.md/CLAUDE.md）はプロジェクト文書波及のため除外する。
    if pathlib.Path(normalized).name in _plan_format.AGENT_DOC_TARGET_BASENAMES:
        return False
    if _plan_format.is_agent_doc_target_file(file_path):
        return True
    return any(pat.search(normalized) for pat in _DIRECT_AGENT_TOOLKIT_DISTRIBUTION_PATTERNS)


def _check_direct_agent_toolkit_edits_after_plan_mode(
    tool_name: str,
    tool_input: dict,
    session_id: str,
) -> bool:
    """plan-modeスキル起動後、計画ファイル未作成のまま`agent-toolkit`配下の直接編集連続を検知する。

    判定条件:

    - `session_id`が空でない
    - `tool_name`が`Write` / `Edit` / `MultiEdit`のいずれか
    - セッション状態の`plan_mode_skill_invoked`が真
    - セッション状態の`plan_file_written`が偽

    連続判定は`last_agent_toolkit_edit_path`と対象パスを比較し、
    直前と異なるパスのときのみ`direct_agent_toolkit_edit_count`をincrementする。
    `~/.claude/plans/`配下のWrite/Edit時は`plan_file_written`を真にしてカウンタをリセットする。
    対象外パスへの編集時もカウンタをリセットする。
    カウンタ2件目でwarn（stderr出力＋Falseを返して進行を継続）、
    3件目以上でblock（stderr出力＋Trueを返してツール呼び出しを中断）する。
    block時は`direct_agent_toolkit_edit_count`と`last_agent_toolkit_edit_path`を更新しない。
    block後にコーディングエージェントが同一パスを再試行した場合、
    直前パス一致条件によるカウンタ加算スキップで素通りする回避を防ぐため、
    カウンタは加算直前の値のまま保持し、再試行時に再度加算されblockが継続する。
    warn／blockの2段階はstderr出力のtagで区別し、
    ハンドラの戻り値は既存の`_check_plan_mode_skill_first`等と同じくbool型とする。
    """
    if not session_id:
        return False
    if tool_name not in _PLAN_FILE_EDIT_TOOLS:
        return False
    file_path_raw = tool_input.get("file_path")
    if not isinstance(file_path_raw, str) or not file_path_raw:
        return False
    state = read_state(session_id)
    if not state.get("plan_mode_skill_invoked", False):
        return False

    # 計画ファイル編集時は`plan_file_written`を真にしカウンタをリセットする。
    if is_plan_file(file_path_raw):

        def _mark_plan_written(current: dict) -> dict | None:
            changed = False
            if not current.get("plan_file_written", False):
                current["plan_file_written"] = True
                changed = True
            if current.get("direct_agent_toolkit_edit_count", 0) != 0:
                current["direct_agent_toolkit_edit_count"] = 0
                changed = True
            if current.get("last_agent_toolkit_edit_path") is not None:
                current["last_agent_toolkit_edit_path"] = None
                changed = True
            return current if changed else None

        update_state(session_id, _mark_plan_written)
        return False

    # 計画ファイルが既に作成済みの場合は本checkの対象外。
    if state.get("plan_file_written", False):
        return False

    # 対象外パスへの編集ならカウンタをリセットして通過。
    if not _is_direct_agent_toolkit_edit_target(file_path_raw):

        def _reset_counter(current: dict) -> dict | None:
            if current.get("direct_agent_toolkit_edit_count", 0) == 0 and current.get("last_agent_toolkit_edit_path") is None:
                return None
            current["direct_agent_toolkit_edit_count"] = 0
            current["last_agent_toolkit_edit_path"] = None
            return current

        update_state(session_id, _reset_counter)
        return False

    # 直前と同一パスの場合はincrementしない（連続判定は異なるファイルに対する編集を対象とする）。
    last_path = state.get("last_agent_toolkit_edit_path")
    if isinstance(last_path, str) and last_path == file_path_raw:
        return False

    # 並列edit時のlost update回避のため、都度ロック内で加算する。
    # `_mark_plan_written`・`_reset_counter`と同様、`update_state`のmutator内で
    # 現在値を再取得してから+1する。呼び出し元へは結果値を`captured`辞書経由で返す。
    captured: dict[str, int] = {"count": 0}

    def _increment(current: dict) -> dict | None:
        count = int(current.get("direct_agent_toolkit_edit_count", 0) or 0) + 1
        captured["count"] = count
        if count >= 3:
            # block時はstate更新をスキップする。
            # 直前パスとカウンタを更新してしまうと、コーディングエージェントが
            # 同一パスを再試行した際に「直前と同一パス」条件で
            # `_increment`到達前にreturn Falseとなりblockが素通りする。
            # 更新をスキップすることで再試行時も再度3件目としてblockが継続する。
            return None
        current["direct_agent_toolkit_edit_count"] = count
        current["last_agent_toolkit_edit_path"] = file_path_raw
        return current

    update_state(session_id, _increment)
    new_count = captured["count"]

    if new_count >= 3:
        print(
            _llm_notice(
                f"blocked: after invoking the plan-mode skill, {new_count} consecutive Write/Edit/MultiEdit"
                f" operations targeted files under agent-toolkit/ without first creating a plan file."
                " Create a plan file under `~/.claude/plans/` before editing any file under agent-toolkit/.",
                tag="block",
            ),
            file=sys.stderr,
        )
        return True
    if new_count == 2:
        print(
            _llm_notice(
                f"warn: after invoking the plan-mode skill, {new_count} consecutive Write/Edit/MultiEdit"
                f" operations targeted files under agent-toolkit/ without first creating a plan file."
                " The next such edit will be blocked."
                " Create a plan file under `~/.claude/plans/` first.",
                tag="warn",
            ),
            file=sys.stderr,
        )
    return False


# --- plan file編集前の必須リファレンス未読をブロック ---

# 各要素は(flag_name, skill_name, reference_path, purpose_sentence)の4タプル。
# 将来のリファレンス追加時は本タプルへの要素追加と、対応する`Read`検知で
# `flag_name`を真にする処理の`posttooluse.py`側への追加を同時に行う。
_PLAN_FILE_REQUIRED_READS: tuple[tuple[str, str, str, str], ...] = (
    (
        "textlint_violations_read",
        "agent-toolkit:writing-standards",
        "references/textlint-violations.md",
        "internalize frequent textlint violation patterns",
    ),
    (
        "plan_file_guidelines_read",
        "agent-toolkit:plan-mode",
        "references/plan-file-guidelines.md",
        "internalize plan file structure requirements",
    ),
)


def _check_plan_file_required_reads_first(
    tool_name: str,
    tool_input: dict,
    session_id: str,
) -> str | None:
    """Plan fileを編集しようとした際に`_PLAN_FILE_REQUIRED_READS`の未読要素がある場合の違反メッセージを返す。

    判定条件:

    - `session_id`が空でない（空ならセッション状態を取得できず判定不能のためスキップ）
    - `tool_name`が`Write` / `Edit` / `MultiEdit`のいずれか
    - 対象の`file_path`が`~/.claude/plans/`直下の計画ファイル
    - `_PLAN_FILE_REQUIRED_READS`のいずれかのフラグがセッション状態上で偽

    各リファレンスを一度Readするとフラグが設定され、以降の判定から除外される。
    ブロックメッセージには既読済みも含めた`_PLAN_FILE_REQUIRED_READS`全件を毎回列挙し
    （反復サイクル防止のため初回で全件を一括開示する）、既読済み項目には`(already read)`を付与する。
    未読要素が1件も無い場合は`None`を返す。
    `permission_mode`の値に依らず適用する（plan mode外でも計画ファイル編集時には同様に違反が起こり得るため）。
    戻り値契約: 違反メッセージ`str`または`None`。呼び出し元が統合報告する。
    """
    if not session_id:
        return None
    if tool_name not in _PLAN_FILE_EDIT_TOOLS:
        return None
    file_path_raw = tool_input.get("file_path")
    if not isinstance(file_path_raw, str) or not is_plan_file(file_path_raw):
        return None
    state = read_state(session_id)
    read_flags = [state.get(flag_name, False) for flag_name, _, _, _ in _PLAN_FILE_REQUIRED_READS]
    if all(read_flags):
        return None
    lines = [
        f"- `{skill_name}` reference `{reference_path}`: {purpose_sentence}" + (" (already read)" if is_read else "")
        for is_read, (_, skill_name, reference_path, purpose_sentence) in zip(
            read_flags, _PLAN_FILE_REQUIRED_READS, strict=True
        )
    ]
    return _llm_notice(
        "blocked: attempting to edit a plan file without reading required references.\n"
        "Read them first, then retry the plan file edit.\n"
        "This check fires only when editing plan files directly under `~/.claude/plans/`."
        " Read all references below before editing the plan file.\n" + "\n".join(lines),
        tag="block",
    )


# --- plan file edit適用後内容の構築（Write/Edit/MultiEdit共通）---


def _materialize_post_edit_content(tool_name: str, tool_input: dict, file_path: str) -> str | None:
    """Write/Edit/MultiEdit適用後の計画ファイル内容を構築して返す。

    - Write: `tool_input["content"]`が文字列ならそのまま返す。文字列でない場合はNoneを返す
    - Edit: 既存ファイル本文を読み、`old_string`を`new_string`へ置換した内容を返す
      `replace_all`が真なら全マッチを置換する
    - MultiEdit: 既存ファイル本文を読み、`edits[]`を順次適用した内容を返す

    既存ファイル読み込みに失敗した場合の挙動:

    - Edit: 既存内容が空のため、`new_string`を単独で返す
    - MultiEdit: 既存内容が空のため、`edits[]`を空文字列に対して順次適用した結果（通常は空文字列）を返す
    """
    if tool_name == "Write":
        content = tool_input.get("content")
        return content if isinstance(content, str) else None

    try:
        existing = pathlib.Path(file_path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        existing = ""

    if tool_name == "Edit":
        old_string = tool_input.get("old_string")
        new_string = tool_input.get("new_string")
        if not isinstance(old_string, str) or not isinstance(new_string, str):
            return None
        replace_all = bool(tool_input.get("replace_all"))
        if not existing:
            return new_string
        if replace_all:
            return existing.replace(old_string, new_string)
        return existing.replace(old_string, new_string, 1)

    if tool_name == "MultiEdit":
        edits = tool_input.get("edits") or []
        if not isinstance(edits, list):
            return None
        result = existing
        for edit in edits:
            if not isinstance(edit, dict):
                continue
            old_string = edit.get("old_string")
            new_string = edit.get("new_string")
            if not isinstance(old_string, str) or not isinstance(new_string, str):
                continue
            replace_all = bool(edit.get("replace_all"))
            result = result.replace(old_string, new_string) if replace_all else result.replace(old_string, new_string, 1)
        return result

    return None


# --- plan fileのH2節順検査 ---


def _check_plan_file_h2_section_order(
    tool_name: str,
    tool_input: dict,
) -> bool:
    """Plan fileのWrite/Edit/MultiEdit時にH2節順違反をブロックする。

    判定条件:

    - `tool_name`が`_PLAN_FILE_EDIT_TOOLS`に含まれる
    - 対象の`file_path`が`~/.claude/plans/`直下の計画ファイル
    - 適用後contentの構築に成功
    - `_plan_format.check_h2_order`が1件以上の違反を返す
    """
    if tool_name not in _PLAN_FILE_EDIT_TOOLS:
        return False
    file_path_raw = tool_input.get("file_path")
    if not isinstance(file_path_raw, str) or not is_plan_file(file_path_raw):
        return False
    content = _materialize_post_edit_content(tool_name, tool_input, file_path_raw)
    if content is None:
        return False
    violations = _plan_format.check_h2_order(content)
    if not violations:
        return False
    violation_str = " / ".join(violations)
    print(
        _llm_notice(
            f"warning: plan file H2 section order violation: {violation_str}"
            f" Required order: {list(_plan_format.PLAN_REQUIRED_H2)}."
            " Fix the section order before ExitPlanMode / plan-impl-executor invocation.",
            tag="warn",
        ),
        file=sys.stderr,
    )
    return True


# --- plan fileの対象ファイル一覧とH3見出しの1対1対応検査 ---

# `対象ファイル一覧`見出し自体は対象ファイル一覧の対応相手ではないため、H3集合から除外する。
_TARGET_FILE_LIST_HEADING = "対象ファイル一覧"


def _check_plan_file_target_files_h3_correspondence(
    tool_name: str,
    tool_input: dict,
) -> bool:
    """Plan fileのWrite/Edit/MultiEdit時に対象ファイル一覧とH3見出しの1対1対応違反をブロックする。

    判定条件:

    - `tool_name`が`_PLAN_FILE_EDIT_TOOLS`に含まれる
    - 対象の`file_path`が`~/.claude/plans/`直下の計画ファイル
    - 適用後contentの構築に成功
    - `## 変更内容`配下の対象ファイル一覧チェックボックスパス集合が1件以上存在する
    - 上記パス集合とH3見出し集合（`対象ファイル一覧`見出し自体は除外）が、
      strip・バッククォート除去後の正規化で不一致

    SSOTは`skills/plan-mode/references/plan-file-guidelines.md`
    「対象ファイル一覧のチェックボックス項目と各ファイル変更方針のH3見出しは1対1で対応させる」規定。
    """
    if tool_name not in _PLAN_FILE_EDIT_TOOLS:
        return False
    file_path_raw = tool_input.get("file_path")
    if not isinstance(file_path_raw, str) or not is_plan_file(file_path_raw):
        return False
    content = _materialize_post_edit_content(tool_name, tool_input, file_path_raw)
    if content is None:
        return False
    target_files = set(_plan_format.extract_target_files_from_changes(content))
    if not target_files:
        return False
    raw_h3s = [
        h.strip().strip("`")
        for h in _plan_format.extract_h3_headings_under_h2(content, "変更内容")
        if h.strip().strip("`") != _TARGET_FILE_LIST_HEADING
    ]
    # 「置換パターン: 」で始まるH3が存在する場合、当該計画は同一パターン置換の集約H3方式を採用しており、
    # 対象ファイル一覧との1対1対応検査は`plan-reviewer`側での sublist 対応照合へ委ねる。
    if any(h.startswith("置換パターン:") for h in raw_h3s):
        return False
    h3_headings = {h for h in raw_h3s if not h.startswith("置換パターン:")}
    missing_h3 = target_files - h3_headings
    extra_h3 = h3_headings - target_files
    if not missing_h3 and not extra_h3:
        return False
    parts = []
    if missing_h3:
        parts.append(f"target files without a corresponding H3 heading: {sorted(missing_h3)}")
    if extra_h3:
        parts.append(f"H3 headings not listed in the target file list: {sorted(extra_h3)}")
    print(
        _llm_notice(
            "warning: the target file list and H3 headings under plan file `## 変更内容` are not in one-to-one correspondence."
            f" {' '.join(parts)}."
            " Add an H3 heading for each target file, or remove the unmatched H3 headings / target file entries.",
            tag="warn",
        ),
        file=sys.stderr,
    )
    return True


# --- plan file 変更履歴と変更内容の対応照合検査 ---

# `## 変更履歴`本文からファイルパス・節名アンカーとして抽出するバッククォートトークンのパターン。
_HISTORY_BACKTICK_TOKEN_RE = re.compile(r"`([^`]+)`")

# ファイルパスらしいバッククォートトークンの判定対象拡張子。
_HISTORY_PATH_EXTENSIONS = (".py", ".md", ".json", ".toml", ".sh", ".ps1", ".yaml", ".yml", ".cmd", ".tmpl")

# 対象ファイル一覧・H3見出しとの対応関係を意図した参照であることを示す文脈語。
# これらを含まない行のバッククォートトークンは、単純な参考言及（例示・引用）として抽出対象から除外する。
_HISTORY_CORRESPONDENCE_CONTEXT_WORDS = ("対象", "反映", "同期", "更新")

# `## 変更履歴`の項目文言にこれらの語を含む場合、当該項目は却下・方針転換の履歴保持用途であり、
# `plan-file-guidelines.md`「変更履歴」節の規定上`## 変更内容`側への転記対象ではないため検査対象外とする。
_HISTORY_EXEMPT_ENTRY_WORDS = ("却下", "方針転換")

# `## 変更履歴`本文の項目境界を判定するパターン（トップレベル箇条書き行のみを項目開始とみなす）。
_HISTORY_ITEM_START_RE = re.compile(r"^-\s+")


def _iter_history_items(
    body: list[tuple[int, str]],
) -> Iterator[list[tuple[int, str]]]:
    """`## 変更履歴`本文行を項目（トップレベル箇条書き単位）へ分割して順に生成する。

    項目境界はインデント無しの`- `始まり行とし、継続行（インデント行・折返し文）は
    直前の項目へ含める。項目開始前に出現する行（節見出し直後の空行等）は無視する。
    """
    current: list[tuple[int, str]] = []
    for lineno, line in body:
        if _HISTORY_ITEM_START_RE.match(line):
            if current:
                yield current
            current = [(lineno, line)]
        elif current:
            current.append((lineno, line))
    if current:
        yield current


def _looks_like_history_path_reference(line: str, token: str) -> bool:
    """バッククォートトークンが対象ファイルパス・節名アンカー（H3見出し）への対応参照らしい形かを判定する。

    判定対象を「H3見出しへの参照または対象ファイル一覧行への参照」に限定するため、次の両方を要求する。

    - トークンがファイルパスの慣例（`/`区切り・対象拡張子終端）に合致する
      （関数名・変数名等の識別子`os.execv`等は対象外）
    - トークンを含む行に対応関係を意図した文脈語（`_HISTORY_CORRESPONDENCE_CONTEXT_WORDS`）を含む

    単純な参考言及（例示・引用としてのパス記載）は文脈語を伴わないため除外される。
    """
    is_path_like = "/" in token or any(token.endswith(ext) for ext in _HISTORY_PATH_EXTENSIONS)
    if not is_path_like:
        return False
    return any(word in line for word in _HISTORY_CORRESPONDENCE_CONTEXT_WORDS)


def _extract_history_referenced_paths(content: str) -> list[str]:
    """`## 変更履歴`配下の各項目本文からファイルパス・節名アンカーのバッククォートトークンを抽出する。

    却下・方針転換の履歴保持用途の項目（項目文言に「却下」「方針転換」を含むもの）は、
    `plan-file-guidelines.md`「変更履歴」節の規定上`## 変更内容`側への転記対象ではないため、
    項目単位で検査対象から除外する。
    """
    body = _plan_format.extract_h2_section_body(content, "変更履歴")
    paths: list[str] = []
    for item in _iter_history_items(body):
        item_text = "\n".join(line for _, line in item)
        if any(word in item_text for word in _HISTORY_EXEMPT_ENTRY_WORDS):
            continue
        for _, line in item:
            for token in _HISTORY_BACKTICK_TOKEN_RE.findall(line):
                stripped = token.strip()
                if stripped and _looks_like_history_path_reference(line, stripped):
                    paths.append(stripped)
    return paths


def _check_plan_file_history_content_sync(
    tool_name: str,
    tool_input: dict,
) -> bool:
    """Plan fileのWrite/Edit/MultiEdit時に変更履歴と変更内容の対応欠落をブロックする。

    判定条件:

    - `tool_name`が`_PLAN_FILE_EDIT_TOOLS`に含まれる
    - 対象の`file_path`が`~/.claude/plans/`直下の計画ファイル
    - 適用後contentの構築に成功（`Write`は`content`、`Edit`・`MultiEdit`は既存の共通ヘルパー
      `_materialize_post_edit_content`によるディスク読み込み後の置換適用結果を用いる）
    - `## 変更履歴`配下の項目本文に含まれるファイルパス・節名アンカーのバッククォートトークンのうち、
      `## 変更内容`側の対象ファイル一覧・H3見出しのいずれにも一致しないものが1件以上存在する

    SSOTは`skills/plan-mode/references/plan-file-guidelines.md`「変更履歴」節の規定
    （通常の指摘反映時は`## 変更内容`本文をSSOTとして直接更新し、変更履歴節への同時記録を必須としない運用）の要約。
    変更履歴にのみ記載されたファイル・節名の転記漏れ（`## 変更内容`側への未反映）を検出する。
    """
    if tool_name not in _PLAN_FILE_EDIT_TOOLS:
        return False
    file_path_raw = tool_input.get("file_path")
    if not isinstance(file_path_raw, str) or not is_plan_file(file_path_raw):
        return False
    content = _materialize_post_edit_content(tool_name, tool_input, file_path_raw)
    if content is None:
        return False

    referenced = _extract_history_referenced_paths(content)
    if not referenced:
        return False

    target_files = set(_plan_format.extract_target_files_from_changes(content))
    h3_headings = {h.strip("`") for h in _plan_format.extract_h3_headings_under_h2(content, "変更内容")}
    known = target_files | h3_headings
    missing = sorted({token for token in referenced if token not in known})
    if not missing:
        return False
    print(
        _llm_notice(
            f"warning: files/section names {missing} listed under plan file `## 変更履歴` have no"
            " corresponding entry in the target file list or H3 headings under `## 変更内容`."
            " The `## 変更履歴` section is reserved for recording direction changes, full revisions,"
            " and rejections; normal feedback reflection should be applied directly to the `## 変更内容` body.",
            tag="warn",
        ),
        file=sys.stderr,
    )
    return True


# --- plan file `## 変更内容`配下H3の text/diff コードブロック存在検査 ---

# `## 変更内容`配下のH3見出しのうち、以下は本検査の対象外とする。
# - `対象ファイル一覧`H3（既存の`_TARGET_FILE_LIST_HEADING`を再利用）
# - `置換パターン:`で始まるH3（同一パターン置換の集約H3方式）
# - `quality-sweep`配下計画の分担バッチH3（`fix-`プレフィックス）
_CHANGE_H3_CODE_BLOCK_EXCEPT_PREFIXES: tuple[str, ...] = (
    "置換パターン:",
    "fix-",
)

_TEXT_DIFF_FENCE_PATTERN = re.compile(r"^(`{3,}|~{3,})\s*(text|diff)\b", re.IGNORECASE)


def _has_text_or_diff_code_block(body_lines: list[tuple[int, str]]) -> bool:
    """H3配下の生body行から`text`/`diff`情報ストリング付きコードフェンス開始行の有無を判定する。"""
    return any(_TEXT_DIFF_FENCE_PATTERN.match(line.lstrip()) for _, line in body_lines)


def _check_plan_file_change_h3_has_code_block(
    tool_name: str,
    tool_input: dict,
) -> bool:
    """Plan fileのWrite/Edit/MultiEdit時に`## 変更内容`配下H3のコードブロック欠落をブロックする。

    SSOTは`skills/plan-mode/references/plan-file-guidelines.md`「変更内容」節の
    「変更後の最終文面または差分を`text`コードブロックで埋め込み、実装者が計画ファイル本文のみで
    変更を再現できる粒度で記述する」規定。
    """
    if tool_name not in _PLAN_FILE_EDIT_TOOLS:
        return False
    file_path_raw = tool_input.get("file_path")
    if not isinstance(file_path_raw, str) or not is_plan_file(file_path_raw):
        return False
    content = _materialize_post_edit_content(tool_name, tool_input, file_path_raw)
    if content is None:
        return False
    # 対象ファイル一覧が空の場合は本検査対象外（file-change H3が存在しない前提のため）。
    target_files = set(_plan_format.extract_target_files_from_changes(content))
    if not target_files:
        return False
    missing: list[str] = []
    for h3_heading, body_lines in _plan_format.iter_h3_sections_under_h2(content, "変更内容"):
        h3 = h3_heading.strip().strip("`")
        if h3 == _TARGET_FILE_LIST_HEADING:
            continue
        if any(h3.startswith(pfx) for pfx in _CHANGE_H3_CODE_BLOCK_EXCEPT_PREFIXES):
            continue
        # file-change H3 のみを検査対象とする（対象ファイル一覧に列挙されるパスに対応するH3）。
        if h3 not in target_files:
            continue
        if not _has_text_or_diff_code_block(body_lines):
            missing.append(h3)
    if not missing:
        return False
    print(
        _llm_notice(
            "warning: H3 sections under plan file `## 変更内容` are missing a text/diff code block."
            f" Affected H3: {sorted(missing)}."
            " Embed the final post-change text or diff in a `text` or `diff` code block."
            " SSOT: skills/plan-mode/references/plan-file-guidelines.md '変更内容' section.",
            tag="warn",
        ),
        file=sys.stderr,
    )
    return True


# --- plan file末尾の`## 計画ファイル（本ファイル）のパス`節配下パス値と`file_path`の一致検査 ---


def _check_plan_file_path_section_matches_file_path(
    tool_name: str,
    tool_input: dict,
) -> bool:
    """Plan file編集で末尾のパス節配下パス値がWrite/Edit/MultiEditの`file_path`と一致しない場合にブロックする。

    本文末尾の当該節が実際の書き込み先と異なるパス値のまま残存する事象を防ぐ。
    SSOTは`skills/plan-mode/references/plan-file-guidelines.md`「計画ファイル（本ファイル）のパス」節の規定。
    """
    if tool_name not in _PLAN_FILE_EDIT_TOOLS:
        return False
    file_path_raw = tool_input.get("file_path")
    if not isinstance(file_path_raw, str) or not is_plan_file(file_path_raw):
        return False
    content = _materialize_post_edit_content(tool_name, tool_input, file_path_raw)
    if content is None:
        return False
    body = _plan_format.extract_h2_section_body(content, "計画ファイル（本ファイル）のパス")
    if not body:
        return False
    candidate: str | None = None
    for _, line in body:
        stripped = line.strip().strip("-").strip().strip("`").strip()
        if stripped:
            candidate = stripped
            break
    if candidate is None:
        return False
    # candidateがパス表記でない場合はプレースホルダーとみなし対象外
    # （絶対パス`/...`・ホーム展開`~/...`のいずれかで始まる場合のみ検査対象）
    if not (candidate.startswith("/") or candidate.startswith("~")):
        return False
    try:
        recorded = pathlib.Path(candidate).expanduser().resolve()
        actual = pathlib.Path(file_path_raw).resolve()
    except (OSError, ValueError):
        return False
    if recorded == actual:
        return False
    print(
        _llm_notice(
            "warning: the path recorded under the plan file's trailing path section does not match the"
            " `file_path` of the Write/Edit/MultiEdit."
            f" Recorded value: {candidate}. Write path: {file_path_raw}."
            " Update the section value to match the actual write target."
            " SSOT: `skills/plan-mode/references/plan-file-guidelines.md` '計画ファイル（本ファイル）のパス' section.",
            tag="warn",
        ),
        file=sys.stderr,
    )
    return True


# --- plan file本文の絶対行番号トークン検査（PreToolUse移管） ---

# SSOTは`skills/plan-mode/references/plan-file-guidelines.md`「計画ファイル全体の遵守事項」節
# （改訂で変動する絶対数値の直書き禁止規範。`## 調査結果`配下は`_LINE_ALLOW_MARKER`付与行のみ対象外）。
# `(?<![A-Za-z])`は英字接頭の識別子（`GraphQL2`等）を除外するための負の後読み。
_LINE_NUMBER_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?<![A-Za-z])L\d+"),
    re.compile(r"\d+行目"),
    re.compile(r"\d+\s*-\s*\d+\s*行"),
    re.compile(r"\d+から\d+行"),
)
_LINE_ALLOW_MARKER = "<!-- line-ref-ok -->"
_INVESTIGATION_HEADING = "調査結果"


def _iter_absolute_line_number_violations(content: str) -> Iterator[tuple[int, str]]:
    """計画ファイル本文から行番号トークンを抽出する。

    `_plan_format.iter_markdown_body_lines`の出力を元にフロントマター・コードフェンス・
    複数行HTMLコメント内を除外する。`## 調査結果`配下かつ`_LINE_ALLOW_MARKER`付与行は
    抑止対象とし、`## 調査結果`外の節ではマーカー付与でも抑止しない。

    Yields:
        (行番号, マッチ文字列) のタプル。
    """
    current_h2: str | None = None
    for lineno, line in _plan_format.iter_markdown_body_lines(content):
        if line.startswith("## "):
            current_h2 = line[3:].strip()
            continue
        if current_h2 == _INVESTIGATION_HEADING and _LINE_ALLOW_MARKER in line:
            continue
        for pattern in _LINE_NUMBER_PATTERNS:
            m = pattern.search(line)
            if m:
                yield lineno, m.group()
                break


def _check_plan_file_absolute_line_numbers(
    tool_name: str,
    tool_input: dict,
) -> bool:
    r"""Plan fileのWrite/Edit/MultiEdit時に絶対行番号トークン直書きをブロックする。

    判定条件:

    - `tool_name`が`_PLAN_FILE_EDIT_TOOLS`に含まれる
    - 対象の`file_path`が`~/.claude/plans/`直下の計画ファイル
    - 適用後content（Write: `tool_input["content"]` / Edit・MultiEdit: 既存＋edit適用後）に
      絶対行番号トークン（`L\d+`等）が含まれる
      （`## 調査結果`配下で`_LINE_ALLOW_MARKER`が付与された行は除く）
    """
    if tool_name not in _PLAN_FILE_EDIT_TOOLS:
        return False
    file_path_raw = tool_input.get("file_path")
    if not isinstance(file_path_raw, str) or not is_plan_file(file_path_raw):
        return False
    content = _materialize_post_edit_content(tool_name, tool_input, file_path_raw)
    if content is None:
        return False
    matches = list(_iter_absolute_line_number_violations(content))
    if not matches:
        return False
    shown = matches[:5]
    shown_str = "; ".join(f"line {ln}: {s!r}" for ln, s in shown)
    overflow = len(matches) - len(shown)
    tail = f"; and {overflow} more" if overflow > 0 else ""
    print(
        _llm_notice(
            "warning: plan file body contains absolute line-number references"
            " (per plan-file-guidelines.md absolute-numbers norm)."
            " Use section names or heading references instead,"
            " or annotate the token with '<!-- line-ref-ok -->' under '## 調査結果'."
            f" Matches: {shown_str}{tail}.",
            tag="warn",
        ),
        file=sys.stderr,
    )
    return True


# --- plan file `## 変更内容`・`### エージェント判断`配下の先送り含意動詞連結検査 ---

# 走査対象H2見出し名（`## 変更内容`）。`### エージェント判断`は変更内容配下のH3で扱う。
_PLAN_DEFERRAL_TARGET_H2: frozenset[str] = frozenset({"変更内容"})

# `### エージェント判断`H3見出し名。H2直下ではなく`## 対応方針`等の配下に置かれる場合もあり、
# H3見出し自体の名前で判定する。
_PLAN_DEFERRAL_TARGET_H3: frozenset[str] = frozenset({"エージェント判断"})


def _iter_plan_deferral_target_lines(content: str) -> Iterator[tuple[int, str]]:
    """`## 変更内容`配下および任意H2下の`### エージェント判断`配下の本文行を生成する。

    `_plan_format.iter_markdown_body_lines`を経由してフロントマター・コードフェンス・
    複数行HTMLコメントは除外済み（`text`コードブロック内・HTMLコメント内は無条件除外）。
    """
    current_h2: str | None = None
    current_h3: str | None = None
    for lineno, line in _plan_format.iter_markdown_body_lines(content):
        if line.startswith("## "):
            current_h2 = line[3:].strip()
            current_h3 = None
            continue
        if line.startswith("### "):
            current_h3 = line[4:].strip().strip("`")
            continue
        in_change_content = current_h2 in _PLAN_DEFERRAL_TARGET_H2
        in_agent_decision = current_h3 in _PLAN_DEFERRAL_TARGET_H3
        if in_change_content or in_agent_decision:
            yield lineno, line


def _check_plan_file_no_deferral_expression(
    tool_name: str,
    tool_input: dict,
) -> str | None:
    """Plan fileのWrite/Edit/MultiEdit時に先送り含意動詞連結パターンの違反メッセージを返す。

    走査対象は`## 変更内容`配下および任意H2下の`### エージェント判断`配下の本文行。
    検出パターンは`_scope_escalation._SCOPE_ESCALATION_PHRASES`の`plan-deferral-onset`カテゴリ
    （「実装時／実装段階」直後の未確定動詞＋文末「〜で判断／決定／選定／確定する」連結）。
    `text`コードブロック内・HTMLコメント内・フロントマターは`iter_markdown_body_lines`が除外する。
    戻り値契約: 違反メッセージ`str`または`None`。呼び出し元が統合報告する。
    """
    if tool_name not in _PLAN_FILE_EDIT_TOOLS:
        return None
    file_path_raw = tool_input.get("file_path")
    if not isinstance(file_path_raw, str) or not is_plan_file(file_path_raw):
        return None
    content = _materialize_post_edit_content(tool_name, tool_input, file_path_raw)
    if content is None:
        return None

    matches: list[tuple[int, str]] = []
    for lineno, line in _iter_plan_deferral_target_lines(content):
        match_result = _match_scope_escalation(line, categories={"plan-deferral-onset"})
        if match_result is not None:
            matches.append((lineno, line.strip()))
    if not matches:
        return None
    shown = matches[:5]
    shown_str = "; ".join(f"line {ln}: {s!r}" for ln, s in shown)
    overflow = len(matches) - len(shown)
    tail = f"; and {overflow} more" if overflow > 0 else ""
    return _llm_notice(
        "blocked: deferral expressions were detected under plan file `## 変更内容` / `### エージェント判断`."
        " Rewrite phrases that defer decisions to the implementation phase into definitive execution statements"
        " (present-tense mandatory execution) or into observation records under `## 進捗ログ`."
        f" Matches: {shown_str}{tail}."
        f" Alternatives: {_format_scope_escalation_alternatives('plan-deferral-onset')}",
        tag="block",
    )


# --- plan fileのワークアラウンド語検出時の事前検討メモチェック ---

# 検出対象語。フォールバック・回避策的な対応の温存を検出する。
_WORKAROUND_TERMS: tuple[str, ...] = ("回避策", "迂回", "失敗時対処")
_WORKAROUND_FAILURE_PATTERN = re.compile(r"が失敗する場合は.{0,30}?する")
_WORKAROUND_REQUIRED_ITEMS: tuple[str, ...] = ("根本原因の候補", "根本対応が成立するか", "成立しない場合の理由")


def _workaround_memo_path(plan_file_path: str) -> pathlib.Path:
    """計画ファイルパスからワークアラウンド事前検討メモの恒久パスを導出する。

    計画ファイル自身のstem（拡張子を除いたbasename）をキーとして使い、
    `~/.claude/plans/<plan_file_stem>-workaround-check.md`を返す。
    計画ファイル1件につきメモ1件が対応するため、由来を問わず全ての計画ファイルへ一律適用できる。
    メモは計画ファイル本体ではないため`_plan_file.is_plan_file`の除外リストに含める。
    """
    stem = pathlib.Path(plan_file_path).stem
    return pathlib.Path.home() / ".claude" / "plans" / f"{stem}-workaround-check.md"


def _workaround_item_has_body(memo_content: str, item: str, all_items: tuple[str, ...]) -> bool:
    """メモ本文中で指定項目名の直後に本文（項目名以外の非空文字）が存在するかを判定する。

    判定範囲は項目名を先頭に持つ行を起点とし、次項目名を先頭に持つ行の直前または末尾までとする。
    「次項目」の判定は`all_items`のいずれかで始まる行の出現とする。
    範囲内に項目名以外の非空文字が1行以上存在すれば真を返す。
    項目名自体が現れない場合は偽を返す（欠落扱い）。
    """

    def _leading_item(line: str) -> str | None:
        stripped = line.lstrip()
        for candidate in all_items:
            if stripped.startswith(candidate):
                return candidate
        return None

    lines = memo_content.splitlines()
    start_idx: int | None = None
    for index, line in enumerate(lines):
        if _leading_item(line) == item:
            start_idx = index
            break
    if start_idx is None:
        return False

    # 開始行の項目名以降に本文が残っている場合は通過
    head_line = lines[start_idx].lstrip()
    tail = head_line[len(item) :].lstrip(":： 　\t")
    if tail.strip():
        return True

    # 次項目名の行または末尾までの範囲を検査
    for cursor in range(start_idx + 1, len(lines)):
        if _leading_item(lines[cursor]) is not None:
            break
        if lines[cursor].strip():
            return True
    return False


def _check_workaround_memo_gate(tool_name: str, tool_input: dict) -> bool:
    """Plan fileのWrite時、ワークアラウンド語検出に伴う事前検討メモの未整備を警告する。

    判定条件:

    - `tool_name`が`Write`
    - 対象の`file_path`が`~/.claude/plans/`直下の計画ファイル
    - `tool_input["content"]`が文字列
    - `## 変更内容`セクション本文にワークアラウンド語（`_WORKAROUND_TERMS`または`_WORKAROUND_FAILURE_PATTERN`）が出現する

    上記を満たす場合、`_workaround_memo_path`が計画ファイルパスから導出する
    `~/.claude/plans/<plan_file_stem>-workaround-check.md`の存在と
    必須3項目（`_WORKAROUND_REQUIRED_ITEMS`）の記入を検査する。
    ファイル不在、必須項目の欠落、または項目名の直後に本文（非空文字）が無い場合はwarn出力する
    （呼び出し元は戻り値を判定せず、警告として扱う）。
    """
    if tool_name != "Write":
        return False
    file_path_raw = tool_input.get("file_path")
    if not isinstance(file_path_raw, str) or not is_plan_file(file_path_raw):
        return False
    content = tool_input.get("content")
    if not isinstance(content, str):
        return False

    changes_match = re.search(r"^## 変更内容\s*\n(.*?)(?=^## |\Z)", content, re.MULTILINE | re.DOTALL)
    if not changes_match:
        return False
    changes_body = changes_match.group(1)

    has_workaround = any(term in changes_body for term in _WORKAROUND_TERMS) or (
        _WORKAROUND_FAILURE_PATTERN.search(changes_body) is not None
    )
    if not has_workaround:
        return False

    memo_path = _workaround_memo_path(file_path_raw)
    if not memo_path.exists():
        print(
            _llm_notice(
                f"warning: workaround-related terms were detected under plan file `## 変更内容`,"
                f" but `{memo_path}` does not exist."
                f" Record the root-cause candidates, whether a root-cause fix is viable, and if not,"
                f" the reason it is not viable, in that memo file before retrying Write.",
                tag="warn",
            ),
            file=sys.stderr,
        )
        return True

    try:
        memo_content = memo_path.read_text(encoding="utf-8")
    except OSError:
        memo_content = ""

    missing_items = [
        item
        for item in _WORKAROUND_REQUIRED_ITEMS
        if not _workaround_item_has_body(memo_content, item, _WORKAROUND_REQUIRED_ITEMS)
    ]
    if missing_items:
        print(
            _llm_notice(
                f"warning: `{memo_path}` is missing body content for required items {missing_items}."
                f" Fill in the root-cause candidates, whether a root-cause fix is viable, and if not,"
                f" the reason it is not viable, before retrying Write.",
                tag="warn",
            ),
            file=sys.stderr,
        )
        return True

    return False


# --- plan file書き込み時の文書サイズ上限対象wc -l実測値記録漏れをブロック ---


def _check_plan_file_size_limit_target_wc_l_recorded(
    tool_name: str,
    tool_input: dict,
) -> bool:
    """Plan fileをWriteする際に、文書サイズ上限対象ファイルのwc -l実測値記載漏れをブロックする。

    判定条件:

    - `tool_name`が`Write`
    - 対象の`file_path`が計画ファイル（`is_plan_file`が真）
    - `tool_input["content"]`が文字列
    - `## 変更内容`配下に文書サイズ上限対象パスが列挙されている
    - 対象パスの実ファイル行数が220行以上
    - `## 調査結果`または`### エージェント判断`に対象ファイル基名と実測値±2の数値が共存しない

    対象ファイルが220行未満の場合、またはパスが`_plan_format.AGENT_DOC_TARGET_PATTERNS`・
    `_plan_format.AGENT_DOC_TARGET_BASENAMES`にマッチしない場合はブロックしない。
    """
    try:
        if tool_name != "Write":
            return False
        file_path_raw = tool_input.get("file_path")
        if not isinstance(file_path_raw, str) or not is_plan_file(file_path_raw):
            return False
        content = tool_input.get("content")
        if not isinstance(content, str):
            return False

        # `## 変更内容`配下の本文を切り出す（次の`##`行直前まで）
        changes_match = re.search(r"^## 変更内容\s*\n(.*?)(?=^## |\Z)", content, re.MULTILINE | re.DOTALL)
        if not changes_match:
            return False
        changes_body = changes_match.group(1)

        # バッククォート内のパスを抽出
        candidate_paths = re.findall(r"`([^`]+)`", changes_body)

        # `## 調査結果`と`### エージェント判断`の本文を結合（検索用）
        findings_match = re.search(r"^## 調査結果\s*\n(.*?)(?=^## |\Z)", content, re.MULTILINE | re.DOTALL)
        findings_body = findings_match.group(1) if findings_match else ""
        judgment_match = re.search(r"^### エージェント判断\s*\n(.*?)(?=^### |^## |\Z)", content, re.MULTILINE | re.DOTALL)
        judgment_body = judgment_match.group(1) if judgment_match else ""
        search_body = findings_body + "\n" + judgment_body
        # `\b`はUnicode `\w`に含まれる日本語文字（「行」等）との境界を検出しないため
        # `\d+`で数字列を抽出する。search_bodyはループ全体で不変のため事前計算する。
        # 既知の限界: search_body内の無関係な数値（他ファイル行数・issue番号等）が
        # actual_lines ± 2 の範囲に偶然一致すると偽陰性（誤通過）が生じる。
        # 実運用での影響は限定的と判断し許容する。
        numbers_in_body = [int(m) for m in re.findall(r"\d+", search_body)]

        cwd = pathlib.Path.cwd()
        for path_str in candidate_paths:
            basename = pathlib.Path(path_str).name

            # パターン照合で文書サイズ上限対象かを判定
            if not _plan_format.is_agent_doc_target_file(path_str):
                continue

            # 実ファイルが存在し220行以上かを確認
            real_path = cwd / path_str
            if not real_path.exists():
                continue
            try:
                with real_path.open(encoding="utf-8", errors="replace") as f:
                    actual_lines = sum(1 for _ in f)
            except OSError:
                continue
            if actual_lines < 220:
                continue

            # search_bodyに基名と実測値±2の数値が共存するかを判定
            if basename not in search_body:
                print(
                    _llm_notice(
                        f"warning: plan file `## 変更内容` includes size-limit-target file `{basename}`,"
                        f" but no wc -l measured value is recorded under `## 調査結果` or `### エージェント判断`."
                        f" Expected: {actual_lines} (±2 tolerance, i.e. {actual_lines - 2}-{actual_lines + 2})."
                        f" Record the actual line count of `{basename}` under `## 調査結果` or `### エージェント判断`"
                        f" before retrying Write.",
                        tag="warn",
                    ),
                    file=sys.stderr,
                )
                return True

            # 基名が含まれる場合は実測値±2の数値が近傍に存在するかを確認
            low, high = actual_lines - 2, actual_lines + 2
            if not any(low <= n <= high for n in numbers_in_body):
                print(
                    _llm_notice(
                        f"warning: plan file `## 変更内容` includes size-limit-target file `{basename}`,"
                        f" but the number recorded under `## 調査結果` or `### エージェント判断` does not match"
                        f" the actual measurement."
                        f" Expected: {actual_lines} (±2 tolerance, i.e. {actual_lines - 2}-{actual_lines + 2})."
                        f" Update the recorded number in the plan file to the actual line count before retrying Write.",
                        tag="warn",
                    ),
                    file=sys.stderr,
                )
                return True

    except Exception:  # noqa: BLE001 -- 実ファイル読み込み失敗等は安全側として通過させる
        return False

    return False


# --- 規範対象ドキュメントへのメタ規範新設編集時の遡及スキャン記録チェック (FB4) ---

# 汎用禁止形: バレット行頭記号直後から句点・改行終端の禁止動詞までを検出する。
_RETROACTIVE_SCAN_GENERIC_PROHIBITION_PATTERN = re.compile(
    r"^\s*-\s+[^\n]{0,80}(しない|禁止する|発行しない|省略しない)(。|$)", re.MULTILINE
)
# 全称禁止形: 「いかなる理由（例: X）があっても〜しない」形式。
_RETROACTIVE_SCAN_UNIVERSAL_PROHIBITION_PATTERN = re.compile(r"いかなる理由(?:（[^）]*）)?があっても[^\n]{0,80}しない")
# 新規節見出し: 規範文書への新規セクション追加を検出する。
_RETROACTIVE_SCAN_NEW_HEADING_PATTERN = re.compile(r"^##[#]* .+$", re.MULTILINE)

_RETROACTIVE_SCAN_HEADING = "遡及スキャン結果"
_RETROACTIVE_SCAN_REQUIRED_ITEMS: tuple[str, ...] = ("対象パターン", "検出件数", "対応方針")


def _detect_new_meta_norm(old: str, new: str) -> bool:
    """new側にold側と比べて新規のメタ規範パターン（禁止形バレット・新規節見出し）が追加されたか判定する。"""
    if _RETROACTIVE_SCAN_UNIVERSAL_PROHIBITION_PATTERN.search(new) and not (
        _RETROACTIVE_SCAN_UNIVERSAL_PROHIBITION_PATTERN.search(old)
    ):
        return True
    if len(_RETROACTIVE_SCAN_GENERIC_PROHIBITION_PATTERN.findall(new)) > len(
        _RETROACTIVE_SCAN_GENERIC_PROHIBITION_PATTERN.findall(old)
    ):
        return True
    return len(_RETROACTIVE_SCAN_NEW_HEADING_PATTERN.findall(new)) > len(_RETROACTIVE_SCAN_NEW_HEADING_PATTERN.findall(old))


def _plan_file_has_retroactive_scan_record(plan_file_path: str) -> bool:
    """現在の計画ファイルの`## 調査結果`配下に`### 遡及スキャン結果`小見出しと必須3項目の記述があるか判定する。"""
    try:
        content = pathlib.Path(plan_file_path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False
    body = _plan_format.extract_h2_section_body(content, "調査結果")
    in_target_h3 = False
    section_lines: list[str] = []
    for _, line in body:
        if line.startswith("### "):
            in_target_h3 = line[4:].strip() == _RETROACTIVE_SCAN_HEADING
            continue
        if in_target_h3:
            section_lines.append(line)
    if not section_lines:
        return False
    section_text = "\n".join(section_lines)
    return all(item in section_text for item in _RETROACTIVE_SCAN_REQUIRED_ITEMS)


def _check_plan_file_retroactive_scan_recorded(
    tool_name: str,
    tool_input: dict,
    session_id: str,
) -> str | None:
    """規範対象ドキュメントへのメタ規範新設編集時、現在の計画ファイルの遡及スキャン記録未整備の違反メッセージを返す。

    戻り値契約: 違反メッセージ`str`または`None`。呼び出し元が統合報告する。

    判定条件:

    - `tool_name`が`Write` / `Edit` / `MultiEdit`のいずれか
    - 対象の`file_path`が文書サイズ上限対象パターン（`_plan_format.AGENT_DOC_TARGET_PATTERNS` /
      `_plan_format.AGENT_DOC_TARGET_BASENAMES`）に一致する規範対象ドキュメント（計画ファイル自身は対象外）
    - 新規/既存内容の比較で`_detect_new_meta_norm`が真
      （全称禁止形の新規出現、汎用禁止形バレットの増加、新規節見出しの増加のいずれか）
    - `session_id`のセッション状態から取得した`current_plan_file_path`の
      `## 調査結果`配下`### 遡及スキャン結果`小見出しに必須3項目（対象パターン・検出件数・対応方針）が
      記述されていない

    計画ファイルパスが未記録の場合は判定不能として通過させる（安全側でブロックしない）。
    """
    if tool_name not in ("Write", "Edit", "MultiEdit"):
        return None
    file_path_raw = tool_input.get("file_path")
    file_path = file_path_raw if isinstance(file_path_raw, str) else ""
    if not file_path or is_plan_file(file_path):
        return None
    if not _plan_format.is_agent_doc_target_file(file_path):
        return None

    detected = False
    if tool_name == "Write":
        content = tool_input.get("content")
        if isinstance(content, str):
            try:
                old_content = pathlib.Path(file_path).read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                old_content = ""
            detected = _detect_new_meta_norm(old_content, content)
    elif tool_name == "Edit":
        old_string = tool_input.get("old_string") or ""
        new_string = tool_input.get("new_string")
        if isinstance(new_string, str):
            old_string = old_string if isinstance(old_string, str) else ""
            detected = _detect_new_meta_norm(old_string, new_string)
    else:  # MultiEdit
        edits = tool_input.get("edits") or []
        if isinstance(edits, list):
            for edit in edits:
                if not isinstance(edit, dict):
                    continue
                old_string = edit.get("old_string") or ""
                new_string = edit.get("new_string")
                if not isinstance(new_string, str):
                    continue
                old_string = old_string if isinstance(old_string, str) else ""
                if _detect_new_meta_norm(old_string, new_string):
                    detected = True
                    break
    if not detected:
        return None

    if not session_id:
        return None
    state = read_state(session_id)
    plan_file_path = state.get("current_plan_file_path")
    if not isinstance(plan_file_path, str) or not plan_file_path:
        return None
    if _plan_file_has_retroactive_scan_record(plan_file_path):
        return None
    return _llm_notice(
        f"blocked: detected a new meta-norm pattern being added to {file_path},"
        f" but plan file {plan_file_path} does not record the required items"
        f" (target pattern, detection count, remediation policy) under the"
        f" `### 遡及スキャン結果` sub-heading of its `## 調査結果` section."
        f" Follow skills/plan-mode/references/norm-revision-checklist.md '規範対象範囲の網羅確認' section,"
        f" record the retroactive scan results in the plan file, then retry the edit.",
        tag="block",
    )


# --- `plan-file-creator`の整合性チェック（2サブエージェント/codexレビュー）完了チェック ---

# Skillツールの`skill`引数として許容するplan-modeスキル名。
# posttooluse.pyの`_PLAN_MODE_SKILL_NAMES`と対応させる。
_PLAN_MODE_SKILL_NAMES: frozenset[str] = frozenset({"agent-toolkit:plan-mode", "plan-mode"})
# Agent/Taskツールの`subagent_type`引数として許容するplan-impl-executor識別子。
# フルネームと短縮名の両方を許容する。
_PLAN_IMPL_EXECUTOR_SUBAGENT_TYPES: frozenset[str] = frozenset({"agent-toolkit:plan-impl-executor", "plan-impl-executor"})

# `_process_loop_log`による起動時刻記録の対象サブエージェント種別（fb-1）。
# `process-feedbacks`実行時のplan-impl系観測基盤で使う。フルネームと短縮名の両方を許容する。
# `posttooluse.py`側の同名定数（終了時刻記録用）と対応させる。
_TRACKED_SUBAGENT_TYPES: frozenset[str] = frozenset(
    {
        "plan-impl-executor",
        "agent-toolkit:plan-impl-executor",
        "plan-implementer",
        "agent-toolkit:plan-implementer",
        "plan-codex-implementer",
        "agent-toolkit:plan-codex-implementer",
        "plan-impl-reviewer",
        "agent-toolkit:plan-impl-reviewer",
        "plan-codex-reviewer",
        "agent-toolkit:plan-codex-reviewer",
        "plan-reviewer",
        "agent-toolkit:plan-reviewer",
        "plan-spec-reviewer",
        "agent-toolkit:plan-spec-reviewer",
        "agent-doc-validator",
        "agent-toolkit:agent-doc-validator",
        "plan-file-creator",
        "agent-toolkit:plan-file-creator",
    }
)

# `plan-file-creator`の整合性チェックの完遂を示すセッション状態フラグ。
# 各フラグはposttooluse.pyが対応するAgent/Skill起動を観測して記録する
# （`agent-toolkit:agent-standards`スキル「セッション状態フラグ」節が全フラグ一覧のSSOT）。
_PROCESS7_COMPLETION_FLAGS: tuple[str, ...] = (
    "plan_reviewer_invoked",
    "codex_review_invoked",
)

# agent-doc-validatorの条件付き必須化対象ファイル群の判定に使う、計画ファイル全文走査時の
# フォールバックパターン。`### 対象ファイル一覧`節が抽出できる通常時は
# `_plan_format.extract_target_files_from_changes`でパス一覧を取得し、
# 各パスを`_plan_format.is_agent_facing_md`（拡張子・パス部品ベースの厳密判定。
# posttooluse.pyの対象種別判定と共有するSSOT実装）で判定する。
# 節が見つからない書式崩れの計画ファイルに限り、本パターンで全文を粗く走査する
# （安全側の判定。誤って見落とすリスクを優先して回避する）。
_AGENT_DOC_TARGET_FILE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"agent-toolkit/rules/"),
    re.compile(r"\.claude/rules/"),
    re.compile(r"\.claude/skills/"),
    re.compile(r"agent-toolkit/agents/"),
    re.compile(r"agent-toolkit/skills/"),
    re.compile(r"\.chezmoi-source/dot_claude/rules/"),
    re.compile(r"\.chezmoi-source/dot_claude/skills/"),
    re.compile(r"AGENTS\.md"),
    re.compile(r"CLAUDE\.md"),
)


def _should_require_agent_doc_validator(plan_file_content: str) -> bool:
    """計画ファイル内容から`agent_doc_validator_invoked`フラグの必須化要否を判定する。

    `## 変更内容`配下`### 対象ファイル一覧`にコーディングエージェント向け文書
    （`_plan_format.is_agent_facing_md`が真を返すパス）が1件でも列挙されている場合に真を返す。
    `### 対象ファイル一覧`節が見つからない場合は計画ファイル全文を`_AGENT_DOC_TARGET_FILE_PATTERNS`で
    走査する（安全側の判定）。
    """
    section_body = _plan_format.extract_h2_section_body(plan_file_content, "変更内容")
    has_target_list_heading = any(
        line.startswith("### ") and line[4:].strip() == "対象ファイル一覧" for _, line in section_body
    )
    if has_target_list_heading:
        target_paths = _plan_format.extract_target_files_from_changes(plan_file_content)
        return any(_plan_format.is_agent_facing_md(path) for path in target_paths)
    return any(pattern.search(plan_file_content) is not None for pattern in _AGENT_DOC_TARGET_FILE_PATTERNS)


def _current_plan_file_requires_agent_doc_validator(state: dict) -> bool:
    """セッション状態が保持する現在の計画ファイルパスを読み、`agent_doc_validator_invoked`の要否を判定する。

    計画ファイルパス未記録・読み込み失敗の場合は要否判定不能として偽を返す（安全側でブロックしない）。
    `current_plan_file_path`はposttooluse.pyがplan file編集検出時に記録する。
    """
    plan_file_path = state.get("current_plan_file_path")
    if not isinstance(plan_file_path, str) or not plan_file_path:
        return False
    try:
        content = pathlib.Path(plan_file_path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False
    return _should_require_agent_doc_validator(content)


def _check_plan_prep_skills_block_enter_plan_mode(tool_name: str, session_id: str) -> bool:
    """process-feedbacks・plan-and-add-feedback経由でのplan-modeネスト起動時のEnterPlanMode発行をブロックする。

    判定条件:

    - `tool_name`が`EnterPlanMode`
    - `session_id`が空でない
    - セッション状態の`process_feedbacks_skill_invoked`または`plan_and_add_feedback_skill_invoked`が真

    process-feedbacks・plan-and-add-feedback両スキルはplan mode外で実行する規範
    （`agent-toolkit/skills/plan-mode/SKILL.md`「plan mode移行の前提」バレット）を機械化する。
    `process_feedbacks_skill_invoked`のフラグリセットは`agent-toolkit/scripts/posttooluse.py`が
    `process-feedbacks-finish`起動検知時と`process-feedbacks`再起動時に担う。
    `plan_and_add_feedback_skill_invoked`のリセットは同スクリプトが
    `add-feedback`起動検知時（plan-and-add-feedbackの終端工程）に担う。
    """
    if tool_name != "EnterPlanMode":
        return False
    if not session_id:
        return False
    state = read_state(session_id)
    process_feedbacks_invoked = state.get("process_feedbacks_skill_invoked", False)
    plan_and_add_feedback_invoked = state.get("plan_and_add_feedback_skill_invoked", False)
    if not process_feedbacks_invoked and not plan_and_add_feedback_invoked:
        return False
    reset_guidance = ""
    if process_feedbacks_invoked:
        reset_guidance += " Reset the process-feedbacks flag by invoking the `agent-toolkit:process-feedbacks-finish` skill."
    if plan_and_add_feedback_invoked:
        reset_guidance += " Reset the plan-and-add-feedback flag by invoking the `agent-toolkit:add-feedback` skill."
    print(
        _llm_notice(
            "blocked: issuing EnterPlanMode from within the process-feedbacks or plan-and-add-feedback skill"
            " violates the plan-mode norm"
            " (agent-toolkit/skills/plan-mode/SKILL.md 'plan mode移行の前提' bullet)."
            " Run these skills outside plan mode." + reset_guidance,
            tag="block",
        ),
        file=sys.stderr,
    )
    return True


def _check_plan_file_bump_step_when_agent_toolkit_target(tool_name: str, tool_input: dict) -> None:
    """計画ファイルの対象ファイル一覧に`agent-toolkit/`配下パスがある場合にbump記載を要求する。

    判定は`_plan_format.has_bump_step_when_required`へ委譲する（SSOT）。
    違反時はwarn降格の`_llm_notice`を`stderr`へ出力し、ブロックは採用しない
    （`agent-toolkit-edit`スキル「bump不要時のみ省略可」文言との整合を保つ）。
    """
    if tool_name not in _PLAN_FILE_EDIT_TOOLS:
        return
    file_path_raw = tool_input.get("file_path")
    if not isinstance(file_path_raw, str) or not is_plan_file(file_path_raw):
        return
    content = _materialize_post_edit_content(tool_name, tool_input, file_path_raw)
    if content is None:
        return
    if _plan_format.has_bump_step_when_required(content):
        return
    print(
        _llm_notice(
            f"plan file {file_path_raw}: the target file list includes paths under `agent-toolkit/`,"
            " but the `## 実行方法` body does not include an `agent_toolkit_bump.py` step."
            " Follow `.claude/skills/agent-toolkit-edit/SKILL.md` 'バージョン更新' section and add a bump step"
            " to the execution procedure (ignore this warning if a bump is not required).",
            tag="warn",
        ),
        file=sys.stderr,
    )


def _check_plan_file_manifest_when_bump_step(tool_name: str, tool_input: dict) -> None:
    """計画ファイル`## 実行方法`にbump stepがある場合、対象ファイル一覧のmanifest記載を要求する。

    判定は`_plan_format.has_manifest_files_when_bump_step_present`へ委譲する（SSOT）。
    違反時はwarn降格の`_llm_notice`を`stderr`へ出力し、ブロックは採用しない
    （既存`_check_plan_file_bump_step_when_agent_toolkit_target`と同分類）。
    """
    if tool_name not in _PLAN_FILE_EDIT_TOOLS:
        return
    file_path_raw = tool_input.get("file_path")
    if not isinstance(file_path_raw, str) or not is_plan_file(file_path_raw):
        return
    content = _materialize_post_edit_content(tool_name, tool_input, file_path_raw)
    if content is None:
        return
    if _plan_format.has_manifest_files_when_bump_step_present(content):
        return
    print(
        _llm_notice(
            f"plan file {file_path_raw}: the `## 実行方法` body records a bump step,"
            " but the target file list is missing both manifests."
            " Follow `.claude/skills/agent-toolkit-edit/SKILL.md` 'バージョン更新' section and add both manifests to"
            " the target file list.",
            tag="warn",
        ),
        file=sys.stderr,
    )


def _check_plan_file_target_file_paths_relative(tool_name: str, tool_input: dict) -> None:
    """計画ファイルの対象ファイル一覧に絶対パスまたは親ディレクトリ参照がある場合に警告する。

    判定は`_plan_format.find_invalid_target_file_paths`へ委譲する（SSOT）。
    違反時はwarn降格の`_llm_notice`を`stderr`へ出力し、ブロックは採用しない。
    """
    if tool_name not in _PLAN_FILE_EDIT_TOOLS:
        return
    file_path_raw = tool_input.get("file_path")
    if not isinstance(file_path_raw, str) or not is_plan_file(file_path_raw):
        return
    content = _materialize_post_edit_content(tool_name, tool_input, file_path_raw)
    if content is None:
        return
    invalid = _plan_format.find_invalid_target_file_paths(content)
    if not invalid:
        return
    joined = ", ".join(f"`{p}`" for p in invalid)
    print(
        _llm_notice(
            f"plan file {file_path_raw}: entries containing absolute paths or parent-directory references were"
            f" detected under `## 変更内容 > ### 対象ファイル一覧`: {joined}."
            f" Rewrite them as full paths relative to the project root"
            f" (see `skills/plan-mode/references/plan-file-guidelines.md`).",
            tag="warn",
        ),
        file=sys.stderr,
    )


def _check_process7_completion_before_exit_plan_mode(session_id: str, state: dict | None = None) -> bool:
    """ExitPlanModeまたは`plan-impl-executor`起動時、`plan-file-creator`の整合性チェック完了未達をブロックする。

    `plan-impl-executor`起動時は`_check_process7_completion_for_plan_impl_executor_agent`
    経由で呼ばれる。

    判定条件:

    - `session_id`が空でない（空ならセッション状態を取得できず判定不能のためスキップ）
    - セッション状態の`plan_mode_skill_invoked`が真
      （plan-modeスキルを使わない文脈では`plan-file-creator`の整合性チェックの完遂義務が生じないため対象外）
    - `_PROCESS7_COMPLETION_FLAGS`のいずれかが偽。
      計画の対象ファイル一覧にコーディングエージェント向け文書対象ファイルが含まれる場合は、
      `agent_doc_validator_invoked`も必須フラグに加える
      （`_should_require_agent_doc_validator`参照。無条件必須化はしない）

    未起動フラグは1回のブロックメッセージへ全件列挙する。
    `state`を渡した場合はセッション状態の再読み込みを省略する。
    """
    if not session_id:
        return False
    if state is None:
        state = read_state(session_id)
    if not state.get("plan_mode_skill_invoked", False):
        return False
    required_flags = list(_PROCESS7_COMPLETION_FLAGS)
    if _current_plan_file_requires_agent_doc_validator(state):
        required_flags.append("agent_doc_validator_invoked")
    missing = [flag for flag in required_flags if not state.get(flag, False)]
    if not missing:
        return False
    print(
        _llm_notice(
            "blocked: attempting to exit plan mode or invoke `plan-impl-executor`"
            " before completing the plan-file-creator integrity check"
            " (plan-reviewer / codex review)."
            f" Missing flags: {missing}."
            " See agent-toolkit/skills/plan-mode/references/integrity-checks.md"
            " '整合性チェック・codexレビューの実施手順' section.",
            tag="block",
        ),
        file=sys.stderr,
    )
    return True


_PLAN_FILE_PATH_IN_BACKTICK_RE = re.compile(r"`([^`\n]*\.claude[/\\]plans[/\\][^`\n]+\.md)`")
_PLAN_FILE_PATH_IN_PROMPT_RE = re.compile(r"[^\s`]*\.claude[/\\]plans[/\\][^\s`]+\.md")


def _extract_referenced_plan_file_path(prompt: str) -> str | None:
    r"""起動プロンプト本文から計画ファイルパス（`.claude/plans/*.md`）を抽出する。

    パス区切りは`/`と`\\`の双方を許容する。
    バッククォート囲み表記を優先し、囲み内は空白を含めて丸ごと抽出する
    （空白を含むホームディレクトリ名での部分抽出誤りを防ぐため）。
    一意に定まらない場合（0件または2件以上の異なるパスが検出された場合）は`None`を返す。
    呼び出し側は`None`を「抽出不能」として安全側（従来どおりブロック判定）に扱う。
    """
    matches = {m.group(1) for m in _PLAN_FILE_PATH_IN_BACKTICK_RE.finditer(prompt)}
    if not matches:
        matches = {m.group(0) for m in _PLAN_FILE_PATH_IN_PROMPT_RE.finditer(prompt)}
    if len(matches) != 1:
        return None
    return next(iter(matches))


def _normalize_plan_file_path(path_text: str) -> pathlib.Path | None:
    """計画ファイルパスの比較用正規化（`~`展開と絶対化）を行う。

    正規化に失敗した場合（`expanduser`が未解決ユーザーで例外を送出する場合等）は`None`を返す。
    呼び出し側は`None`を「抽出不能」と同じ従来判定へ倒す。
    """
    try:
        return pathlib.Path(path_text).expanduser().resolve(strict=False)
    except (OSError, RuntimeError, ValueError):
        return None


def _check_process7_completion_for_plan_impl_executor_agent(session_id: str, tool_input: dict) -> bool:
    """`plan-impl-executor`系Agent/Task起動時、現行計画パスへの起動時のみ`plan-file-creator`の整合性チェック完了未達をブロックする。

    起動プロンプトの`prompt`欄から計画ファイルパスを抽出し、正規化のうえセッション状態の
    `current_plan_file_path`と一致する場合のみ`_check_process7_completion_before_exit_plan_mode`を適用する。
    別セッションでplan-modeにより完遂済みの計画（例:`plan-and-add-feedback`投入の計画実装型フィードバック）を
    指す起動は、当該計画ファイルが実在する場合に限り、当該セッションの`plan-file-creator`の整合性チェック未達を理由にブロックしない。
    `prompt`が文字列でない場合、パスが一意に抽出できない場合、
    `current_plan_file_path`が未記録・非文字列の場合、正規化に失敗した場合、
    または不一致の参照パスが実在しない場合は安全側として従来どおり判定する
    （実在確認が無いと、任意の非実在パスを記述するだけで`plan-file-creator`の整合性チェック未達を回避できてしまうため）。
    """
    if not session_id:
        return False
    prompt = tool_input.get("prompt")
    if not isinstance(prompt, str):
        return _check_process7_completion_before_exit_plan_mode(session_id)
    referenced_path = _extract_referenced_plan_file_path(prompt)
    if referenced_path is None:
        return _check_process7_completion_before_exit_plan_mode(session_id)
    state = read_state(session_id)
    current_path = state.get("current_plan_file_path")
    if not isinstance(current_path, str) or not current_path:
        return _check_process7_completion_before_exit_plan_mode(session_id, state)
    referenced = _normalize_plan_file_path(referenced_path)
    current = _normalize_plan_file_path(current_path)
    if referenced is None or current is None:
        return _check_process7_completion_before_exit_plan_mode(session_id, state)
    if referenced != current:
        if not referenced.is_file():
            return _check_process7_completion_before_exit_plan_mode(session_id, state)
        return False
    return _check_process7_completion_before_exit_plan_mode(session_id, state)


def _reset_process7_completion_flags(session_id: str) -> None:
    """`agent-toolkit:plan-mode`スキル起動を検出した際に`plan-file-creator`の整合性チェック完了フラグをリセットする。

    新計画への着手の合図として`_PROCESS7_COMPLETION_FLAGS`と条件付きフラグ
    `agent_doc_validator_invoked`を偽へ戻す。前計画の`current_plan_file_path`も
    新計画の対象ファイル判定へ誤流用しないよう消去する。
    """
    if not session_id:
        return

    def _reset(current: dict) -> dict | None:
        changed = False
        for flag in (
            *_PROCESS7_COMPLETION_FLAGS,
            "agent_doc_validator_invoked",
            "plan_codex_reviewer_invoked",
            "plan_codex_reviewer_blocked",
        ):
            if current.get(flag, False):
                current[flag] = False
                changed = True
        if current.pop("current_plan_file_path", None) is not None:
            changed = True
        # 前計画のcodex thread参照を新計画へ持ち越さない。
        if current.pop("recorded_codex_thread_id", None) is not None:
            changed = True
        # 直接編集連続checkの状態も新計画へ持ち越さない。
        if current.get("plan_file_written", False):
            current["plan_file_written"] = False
            changed = True
        if current.get("direct_agent_toolkit_edit_count", 0) != 0:
            current["direct_agent_toolkit_edit_count"] = 0
            changed = True
        if current.get("last_agent_toolkit_edit_path") is not None:
            current["last_agent_toolkit_edit_path"] = None
            changed = True
        return current if changed else None

    update_state(session_id, _reset)


# --- Bash: heredoc内のパターンを除外するヘルパー ---


def _likely_real_command(command: str, pos: int) -> bool:
    """マッチ位置がシェルコマンド文脈にあるかヒューリスティックで判定する。

    heredoc（`<<`）がマッチ位置より前にある場合、マッチはリテラル文字列の
    一部である可能性が高いため偽を返す。
    `python3 -c` / `cat <<`等でファイル内容を書き込むケースの誤検出を防ぐ。
    """
    prefix = command[:pos]
    return "<<" not in prefix


# --- Bash: 関連定数（git commit検出）---

_GIT_COMMIT_PATTERN = re.compile(r"\bgit\s+commit\b")


# --- Bash: git amend / rebaseをlog未確認でブロック ---


def _check_bash_amend_rebase_without_log(command: str, session_id: str, cwd: str) -> bool:
    """Git commit --amend / git rebaseをgit log未確認で実行しようとした場合にブロックする。

    amend / rebaseは既存コミットを書き換えるため、直前にgit log --decorateで
    コミット状態（特にプッシュ済みかどうか）を確認する必要がある。
    ファイル編集・commit・rebase・push・Stopが介在すると確認状態をリセットする。
    ユーザーが裏でpushしている可能性があるためリセット対象に含める。

    `git_log_checked`はcwd別に管理する辞書`{cwd: True}`形式を採用する。
    旧形式のbool値（`True` / `False`）はcwd空文字列環境向けの後方互換として
    そのまま参照する。
    判定は`extract_git_events`の結果を消費し、各git呼び出しの実効cwd
    （`cd`・`pushd`・`git -C`の影響を反映）ごとに行う。
    """
    targets: list[tuple[str, str]] = []
    for event in extract_git_events(command, cwd):
        if event.subcommand == "commit" and "--amend" in event.subcommand_args:
            targets.append((event.cwd, "git commit --amend"))
        elif event.subcommand == "rebase":
            targets.append((event.cwd, "git rebase"))
    if not targets:
        return False
    state = read_state(session_id)
    log_state = state.get("git_log_checked", False)
    for event_cwd, op in targets:
        if isinstance(log_state, dict):
            if event_cwd and log_state.get(event_cwd, False):
                continue
        elif log_state:
            continue
        print(
            _llm_notice(
                f"blocked: {op}."
                f" Run `git log --oneline --decorate` first to confirm commit state before amend/rebase"
                f" (especially, do NOT amend/rebase commits that have already been pushed)."
            ),
            file=sys.stderr,
        )
        return True
    return False


# --- Bash: git push 前 amend後dirty状態のブロック ---


def _check_bash_git_push_after_amend_with_dirty_status(command: str, session_id: str, cwd: str) -> bool:
    """Git push 実行前に amend直後の未コミット差分残置を機械的にブロックする。

    posttooluse側で git commit --amend / --fixup 成功時に設定した
    cwd別の`amend_pending_status_check`フラグがTrueで、かつ現在の作業ツリーに追跡ファイル
    未コミット差分がある場合、pushをブロックして目視・機械両面での確認を促す。
    フラグが立っていないか差分がない場合はブロックしない。
    差分なし時は該当cwdのフラグも解除して通過させるが、解除対象は実送出pushに限定する
    （`git push --dry-run`など送出しないpush系サブコマンドではdirty時blockを実施しclean時は解除せず状態を保つ）。
    差分検出は共有ヘルパー`_git_status.has_tracked_dirty`（`git -C <cwd> status --porcelain`実行）を使い、
    未追跡ファイル（`??`行）を除いた出力行が1件以上あればdirtyと判定する。
    cwd解析は既存の`extract_git_events(command, cwd)`ヘルパーで`git -C <path>`および
    `cd <path> && git push`両形式に対応する（cwd別辞書の実効cwd参照を統一）。
    """
    push_events = [event for event in extract_git_events(command, cwd) if event.subcommand == "push"]
    if not push_events:
        return False
    state = read_state(session_id)
    flags = state.get(_git_status.AMEND_PENDING_FLAG_KEY)
    if not isinstance(flags, dict):
        return False
    for event in push_events:
        if not flags.get(event.cwd, False):
            continue
        if not event.cwd:
            continue
        dirty = _git_status.has_tracked_dirty(event.cwd)
        if dirty is None:
            continue
        if dirty:
            print(
                _llm_notice(
                    f"blocked: git push after `git commit --amend` / `--fixup` with uncommitted tracked changes"
                    f" in {event.cwd}."
                    f" Run `git status` to review, then either `git add` + `git commit --amend`"
                    f" (or `--fixup=<sha>`) to fold the residual diff into the amended commit,"
                    f" or create a follow-up commit before pushing.",
                    tag="block",
                ),
                file=sys.stderr,
            )
            return True
        if _git_status.git_push_is_real_send(event.subcommand_args):
            event_cwd = event.cwd

            def _reset(current: dict, target_cwd: str = event_cwd) -> dict | None:
                current_flags = current.get(_git_status.AMEND_PENDING_FLAG_KEY)
                if not isinstance(current_flags, dict) or not current_flags.get(target_cwd, False):
                    return None
                current_flags[target_cwd] = False
                current[_git_status.AMEND_PENDING_FLAG_KEY] = current_flags
                return current

            update_state(session_id, _reset)
    return False


# --- Bash: 一括ステージ実行時の未編集ファイル警告 ---


def _has_a_flag(args: list[str]) -> bool:
    """`git commit`の`-a`フラグ検出。`--all`、または短フラグクラスタ内の`a`を検出する。

    `-am`・`-amx`等の連結ショートフラグにも一致する。
    簡略化: 値付きフラグ（`-S<key-id>`等でクラスタ内に`a`が現れる形）は誤検出しうる。
    `git commit`の`-S<value>`は`-S <value>`形式でも受け付けるため、
    実運用ではまず短フラグクラスタに値が続かないため許容する。
    見直し契機: 誤警告報告が発生した場合。
    """
    for tok in args:
        if tok == "--all":
            return True
        if tok.startswith("-") and not tok.startswith("--") and "a" in tok[1:]:
            return True
    return False


def _detect_bulk_stage_mode(event: GitEvent) -> str | None:
    """一括ステージ操作の検出。該当時はモード名を返す。

    - `git add -A` / `git add --all` / `git add .`: `include_untracked`
    - `git add -u` / `git add --update`: `tracked_only`
    - `git commit -a` / `git commit --all` / `git commit -am`等: `tracked_only`
    """
    args = event.subcommand_args
    if event.subcommand == "add":
        for tok in args:
            if tok in ("-A", "--all", "."):
                return "include_untracked"
        for tok in args:
            if tok in ("-u", "--update"):
                return "tracked_only"
        return None
    if event.subcommand == "commit":
        if _has_a_flag(args):
            return "tracked_only"
        return None
    return None


def _parse_git_status_short(stdout: str, mode: str) -> set[str]:
    """`git status --short`出力から変更ファイルの相対パス集合を返す。

    `mode == "tracked_only"`のときは`??`（未追跡）行を除外する。
    リネーム行`R  old -> new`は新パスを採用する。
    簡略化: クォート付きパス（`core.quotepath`有効時のUnicodeエスケープ等）は
    先頭・末尾のダブルクォート除去のみで内部のエスケープは非対応。
    見直し契機: エスケープを含むパスで誤検出報告が発生した場合。
    """
    files: set[str] = set()
    for line in stdout.splitlines():
        if len(line) < 4:
            continue
        prefix = line[:2]
        if mode == "tracked_only" and prefix == "??":
            continue
        path = line[3:].strip()
        if " -> " in path:
            path = path.split(" -> ", 1)[1].strip()
        if path.startswith('"') and path.endswith('"') and len(path) >= 2:
            path = path[1:-1]
        if path:
            files.add(path)
    return files


def _normalize_to_relative(path: str, cwd: str) -> str:
    """絶対パスを`cwd`起点の相対パスへ正規化する。相対パスは`pathlib.Path`のみ適用する。"""
    if not path:
        return path
    p = pathlib.Path(path)
    if p.is_absolute() and cwd:
        try:
            return str(p.relative_to(pathlib.Path(cwd), walk_up=True))
        except ValueError:
            return str(p)
    return str(p)


def _check_bash_bulk_stage_with_unedited_files(
    command: str,
    session_id: str,
    payload_cwd: str,
) -> dict | None:
    """一括ステージ実行時に自セッション未編集の変更が含まれる場合の警告JSONを返す。

    `git add -A/--all/.` は未追跡を含む集合、`git add -u/--update` と
    `git commit -a/--all/-am`等は追跡済みのみを対象として作業ツリー変更を判定する。
    セッション状態の`session_edited_files`集合との差集合が空でない場合、
    個別ファイル指定への切替を促すwarnをhookSpecificOutputで返す。
    """
    for event in extract_git_events(command, payload_cwd):
        mode = _detect_bulk_stage_mode(event)
        if mode is None:
            continue
        effective_cwd = event.cwd or payload_cwd
        if not effective_cwd:
            continue
        try:
            proc = subprocess.run(
                ["git", "status", "--short"],
                cwd=effective_cwd,
                capture_output=True,
                text=True,
                check=False,
            )
        except (OSError, FileNotFoundError):
            continue
        if proc.returncode != 0:
            continue
        changed = _parse_git_status_short(proc.stdout, mode)
        if not changed:
            continue
        state = read_state(session_id)
        edited_raw = state.get("session_edited_files", []) or []
        edited: set[str] = set()
        for entry in edited_raw:
            if isinstance(entry, str) and entry:
                edited.add(_normalize_to_relative(entry, effective_cwd))
        changed_norm = {_normalize_to_relative(p, effective_cwd) for p in changed}
        unedited = changed_norm - edited
        if not unedited:
            continue
        sample = sorted(unedited)[:5]
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "allow",
                "additionalContext": _llm_notice(
                    "warn: bulk staging includes files that were not edited in this session."
                    f" Un-edited candidates: {sample}."
                    " Consider switching to per-file staging (`git add <file>`).",
                    tag="warn",
                ),
            },
        }
    return None


# --- Bash: uv run python <path>形式の起動ブロック ---

# 副作用の理由:
# cwdのpyproject.tomlが[tool.uv]のみで[project]セクションを持たない場合、
# `uv run python <path>`はcwdをプロジェクト解決対象として扱い`.venv`と
# `uv.lock`を生成する（uvの仕様）。
# エージェントがPEP 723スクリプトを誤って`uv run python <path>`形式で起動する
# 事故を予防的にblockする。
#
# 判定の優先順位:
#
# 1. `uv run`と`python`の間（uv run自身のオプション位置）に`--script`または
#    `--no-project`が現れる場合は許容する（cwdの依存解決を行わないため副作用なし）。
# 2. cwd変更経路（Bashの`cd` / `pushd`先行・`uv --directory` / `uv --project`）
#    が無く、cwdのpyproject.tomlが[project]セクションを持つPythonプロジェクト
#    の場合は許容する（`uv run python -c '...'`等の正規利用を妨げない）。
# 3. それ以外はblockする。
#
# cwd変更経路を伴う場合はpayload上のcwdを判定根拠に採用できないため、Python
# プロジェクト判定をスキップしてblock側に倒す（副作用の有無を確実に判定できない
# ため安全側の挙動とする）。
# 環境変数経由のcwd / project切り替え（UV_WORKING_DIR / UV_PROJECT）は
# 利用頻度が低く実装コストに見合わないため対応スコープ外とする。

_UV_RUN_PYTHON_BLOCK_MSG = (
    "blocked: `uv run python <path>` style invocation."
    " In a non-Python project (pyproject.toml without a [project] section, or absent),"
    " uv treats the cwd as a project and generates `.venv` and `uv.lock` as a side effect."
    " Alternatives:"
    " (1) for a PEP 723 script, use `uv run --script <path>` or invoke the executable shebang directly;"
    " (2) to skip cwd project resolution, use `uv run --no-project python ...`;"
    " (3) inside a Python project, `cd` to the project root before running."
)

_ENV_ASSIGN_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z_0-9]*=")
_PYTHON_TOKEN_PATTERN = re.compile(r"^python[0-9.]*(?:\.exe)?$", re.IGNORECASE)
_PYPROJECT_PROJECT_SECTION_PATTERN = re.compile(r"(?m)^\[project(?:\.[\w\-]+)?\]\s*$")


def _check_bash_uv_run_python(command: str, cwd: str) -> bool:
    """`uv run python <path>`形式の起動を非Pythonプロジェクトでブロックする。

    判定詳細は本関数の冒頭コメントを参照する。真を返すとblock（exit 2）。
    """
    # heredocを含むコマンドは本文中のリテラル混入で誤検出する余地があるため通過させる。
    if "<<" in command:
        return False
    segments = _split_bash_segments(command)
    cwd_changed_before = False
    for segment in segments:
        try:
            tokens = shlex.split(segment, posix=True)
        except ValueError:
            return False
        info = _parse_uv_run_python(tokens)
        if info is not None:
            has_script_or_no_project, directory_or_project_overridden = info
            if not has_script_or_no_project and (
                directory_or_project_overridden or cwd_changed_before or not _cwd_is_python_project(cwd)
            ):
                print(_llm_notice(_UV_RUN_PYTHON_BLOCK_MSG), file=sys.stderr)
                return True
        if _segment_changes_cwd(tokens):
            cwd_changed_before = True
    return False


def _split_bash_segments(command: str) -> list[str]:
    """Bashコマンドを`;` / `&&` / `||` / `|` / `&`で分割する。

    クォート（`'` / `"`）内のメタ文字は分割対象外とする。
    バックスラッシュエスケープやheredocは厳密に扱わないため、heredocを含む
    コマンドは呼び出し側で除外する想定。
    """
    segments: list[str] = []
    buf: list[str] = []
    in_single = False
    in_double = False
    i = 0
    while i < len(command):
        c = command[i]
        if in_single:
            buf.append(c)
            if c == "'":
                in_single = False
            i += 1
            continue
        if in_double:
            buf.append(c)
            if c == '"':
                in_double = False
            i += 1
            continue
        if c == "'":
            in_single = True
            buf.append(c)
            i += 1
            continue
        if c == '"':
            in_double = True
            buf.append(c)
            i += 1
            continue
        if c in ("&", "|") and i + 1 < len(command) and command[i + 1] == c:
            segments.append("".join(buf))
            buf = []
            i += 2
            continue
        if c in (";", "&", "|"):
            segments.append("".join(buf))
            buf = []
            i += 1
            continue
        buf.append(c)
        i += 1
    if buf:
        segments.append("".join(buf))
    return [s.strip() for s in segments if s.strip()]


def _skip_env_assignments(tokens: list[str], start: int) -> int:
    """先頭の`KEY=VALUE`形式の環境変数代入をスキップした次の位置を返す。"""
    i = start
    while i < len(tokens) and _ENV_ASSIGN_PATTERN.match(tokens[i]):
        i += 1
    return i


def _segment_changes_cwd(tokens: list[str]) -> bool:
    """セグメント先頭のコマンドが`cd` / `pushd` / `popd`の場合に真を返す。"""
    i = _skip_env_assignments(tokens, 0)
    if i >= len(tokens):
        return False
    return tokens[i] in ("cd", "pushd", "popd")


def _is_python_token(token: str) -> bool:
    """`python` / `python3` / `python3.12`などの実行ファイル名トークンの場合に真を返す。"""
    return _PYTHON_TOKEN_PATTERN.match(token) is not None


def _parse_uv_run_python(tokens: list[str]) -> tuple[bool, bool] | None:
    """`uv [...] run [...] python`構造をtokensから検出する。

    構造を検出した場合は`(has_script_or_no_project, directory_or_project_overridden)`を返す。
    対象構造でなければNoneを返す。
    `--script` / `--no-project`は`uv`トークンと`python`トークンの間に
    出現する場合のみ「uv runのオプション」として扱う（`python`以降に書かれた
    場合は`python`の引数として解釈されるため対象外）。
    """
    i = _skip_env_assignments(tokens, 0)
    if i >= len(tokens) or tokens[i] != "uv":
        return None
    uv_idx = i
    python_idx: int | None = None
    for j in range(uv_idx + 1, len(tokens)):
        if _is_python_token(tokens[j]):
            python_idx = j
            break
    if python_idx is None:
        return None
    has_run_between = any(tokens[j] == "run" for j in range(uv_idx + 1, python_idx))
    if not has_run_between:
        return None
    has_script_or_no_project = False
    directory_or_project_overridden = False
    for tok in tokens[uv_idx + 1 : python_idx]:
        if tok in ("--script", "--no-project"):
            has_script_or_no_project = True
        elif tok in ("--directory", "--project") or tok.startswith("--directory=") or tok.startswith("--project="):
            directory_or_project_overridden = True
    return has_script_or_no_project, directory_or_project_overridden


def _cwd_is_python_project(cwd: str) -> bool:
    """cwdの`pyproject.toml`が`[project]`セクションを持つ場合に真を返す。

    `pyproject.toml`不在・読み込み失敗・`[project]`セクション欠如の場合は偽を返す。
    """
    if not cwd:
        return False
    try:
        text = (pathlib.Path(cwd) / "pyproject.toml").read_text(encoding="utf-8", errors="replace")
    except (OSError, ValueError):
        return False
    return _PYPROJECT_PROJECT_SECTION_PATTERN.search(text) is not None


# --- Bash: git共通ヘルパー ---


def _run_git_lines(args: list[str], cwd: str) -> list[str] | None:
    """git出力を行リストで返す。失敗時はNone。"""
    try:
        result = subprocess.run(args, capture_output=True, text=True, check=False, cwd=cwd, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return [line for line in result.stdout.splitlines() if line.strip()]


# --- Bash: git commit未検証警告 ---


_GIT_COMMIT_INCLUDE_WORKTREE_PATTERN = re.compile(r"(?:^|\s)(?:-\w*a\w*|--all)\b")


def _is_docs_only_commit(command: str, cwd: str) -> bool:
    """コミット対象のファイルが全てMarkdownの場合に真を返す。

    docs-only変更では手動テストを省略しpre-commit側のtextlint / markdownlintに
    委ねる運用を想定しており、その場合に未検証警告を抑制する。

    `git commit -a` / `--all`等のコマンドでは作業ツリー側の変更も対象となるため、
    stagedとworking treeを切り分けて判定する。
    `cwd`不在やgit呼び出し失敗時は偽を返して警告を継続する。
    """
    if not cwd:
        return False
    match = _GIT_COMMIT_PATTERN.search(command)
    if match is None:
        return False
    tail = command[match.end() :]
    for delimiter in (";", "|", "&&"):
        pos = tail.find(delimiter)
        if pos != -1:
            tail = tail[:pos]
    include_working_tree = _GIT_COMMIT_INCLUDE_WORKTREE_PATTERN.search(tail) is not None
    args = ["git", "diff", "--name-only", "HEAD"] if include_working_tree else ["git", "diff", "--cached", "--name-only"]
    files = _run_git_lines(args, cwd)
    if not files:
        return False
    return all(path.lower().endswith(".md") for path in files)


def _check_bash_git_commit(command: str, session_id: str, cwd: str) -> dict | None:
    """テスト未実行のままgit commitする場合に警告JSONを返す。

    テスト実行済み（stateの`test_executed`が真）の場合はスキップする。
    状態ファイル不在時は`test_executed` = falseとして扱い警告を表示する。
    コミット対象が全てMarkdownファイルの場合はpre-commit側に検証を委ねる運用を想定してスキップする。
    """
    match = _GIT_COMMIT_PATTERN.search(command)
    if match is None or not _likely_real_command(command, match.start()):
        return None
    state = read_state(session_id)
    if state.get("test_executed", False):
        return None
    if _is_docs_only_commit(command, cwd):
        return None
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "allow",
            "additionalContext": _llm_notice(
                "committing without running tests. Follow the verify-then-commit procedure in 01-agent.md and run tests first.",
                tag="warn",
            ),
        },
    }


# --- Bash: agent-toolkit/配下のversion bump漏れ警告 ---

_AGENT_TOOLKIT_PREFIX = "agent-toolkit/"
_AGENT_TOOLKIT_PLUGIN_MANIFEST = "agent-toolkit/.claude-plugin/plugin.json"
_AGENT_TOOLKIT_TEST_SUFFIX = "_test.py"
_AGENT_TOOLKIT_SCRIPTS_PREFIX = "agent-toolkit/scripts/"


def _check_bash_agent_toolkit_version_bump(command: str, cwd: str) -> dict | None:
    """agent-toolkit/配下の変更をコミットする際にversion bump漏れを警告する。

    判定:

    1. `git commit`を検出した場合のみ動作する
    2. ステージ済みファイルに`agent-toolkit/`配下を含まない、または
       `agent-toolkit/scripts/*_test.py`のみの場合は警告しない
    3. ステージ済み差分に`agent-toolkit/.claude-plugin/plugin.json`を
       含む場合は警告しない
    4. 未プッシュ範囲（`@{u}..HEAD`）に`agent-toolkit/.claude-plugin/plugin.json`
       を変更したコミットがある場合は警告しない（upstream未設定時はスキップ）
    5. 上記いずれにも該当しない場合、warn JSONを返す
    """
    match = _GIT_COMMIT_PATTERN.search(command)
    if match is None or not _likely_real_command(command, match.start()):
        return None
    if not cwd:
        return None

    staged = _run_git_lines(["git", "diff", "--cached", "--name-only"], cwd)
    if staged is None or not staged:
        return None
    agent_toolkit_files = [p for p in staged if p.startswith(_AGENT_TOOLKIT_PREFIX)]
    if not agent_toolkit_files:
        return None
    non_test_files = [
        p
        for p in agent_toolkit_files
        if not (p.startswith(_AGENT_TOOLKIT_SCRIPTS_PREFIX) and p.endswith(_AGENT_TOOLKIT_TEST_SUFFIX))
    ]
    if not non_test_files:
        return None
    if _AGENT_TOOLKIT_PLUGIN_MANIFEST in staged:
        return None

    unpushed = _run_git_lines(
        ["git", "rev-list", "@{u}..HEAD", "--", _AGENT_TOOLKIT_PLUGIN_MANIFEST],
        cwd,
    )
    if unpushed:
        return None

    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "allow",
            "additionalContext": _llm_notice(
                "agent-toolkit/ files are staged but"
                " `agent-toolkit/.claude-plugin/plugin.json` `version` is unchanged"
                " in this commit and the unpushed range."
                " If user-facing behavior changes (hook script, skill, agent definition,"
                " rule file, etc.), bump the `version` field in plugin.json"
                " (and keep `.claude-plugin/marketplace.json` in sync) before committing.",
                tag="warn",
            ),
        },
    }


# --- Bash: git log --decorate自動付与 ---

_GIT_LOG_INSERT_REGEX = re.compile(r"\bgit\s+log\b")


def _check_bash_git_log_decorate(command: str, tool_input: dict) -> dict | None:
    r"""Git logに--decorateがない場合、自動で挿入したupdatedInputを返す。

    `extract_git_events`の結果から`subcommand == "log"`かつ`subcommand_args`に
    `--decorate`を含まない最初のイベントを対象とする。
    コマンド本文上の挿入位置は同順に並ぶ`git\\s+log`マッチから取得する。
    heredoc内のリテラル一致は`_likely_real_command`で除外する。
    """
    log_events = [event for event in extract_git_events(command, "") if event.subcommand == "log"]
    target_index = next(
        (i for i, event in enumerate(log_events) if "--decorate" not in event.subcommand_args),
        None,
    )
    if target_index is None:
        return None
    matches = [m for m in _GIT_LOG_INSERT_REGEX.finditer(command) if _likely_real_command(command, m.start())]
    if target_index >= len(matches):
        return None
    match = matches[target_index]
    updated_command = command[: match.end()] + " --decorate" + command[match.end() :]
    updated_input = dict(tool_input)
    updated_input["command"] = updated_command
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "allow",
            "updatedInput": updated_input,
        },
        "systemMessage": "[agent-toolkit] auto-inserted --decorate into git log.",
    }


# --- Bash: codex exec未決事項の念押し ---

_CODEX_EXEC_PATTERN = re.compile(r"\bcodex\s+exec\b")
_CODEX_RESUME_PATTERN_PRE = re.compile(r"\bcodex\s+exec\s+resume\b")


def _check_bash_codex_exec(command: str) -> dict | None:
    """Codex exec（resume以外）を検出した場合に未決事項確認の念押しメッセージを返す。"""
    exec_match = _CODEX_EXEC_PATTERN.search(command)
    if exec_match is None or not _likely_real_command(command, exec_match.start()):
        return None
    if _CODEX_RESUME_PATTERN_PRE.search(command):
        return None
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "allow",
            "additionalContext": _llm_notice(
                "submitting plan file to codex review."
                " Pre-submission check: are there any decisions made by assumption"
                " rather than user confirmation?"
                " Resolve any open questions with the user before proceeding."
            ),
        },
    }


# --- mcp__codex__codex / mcp__codex__codex-reply: isSidechainプローブ ---


def _record_iss_sidechain_probe(
    session_id: str,
    tool_name: str,
    payload: dict,
) -> None:
    """多重ネスト構成でのisSidechain実値採取用のデバッグログ記録。

    暫定機構: fb7 (20260719-074241-001.md) の実サンプル採取が目的。
    十分なサンプルが集まり代替判定機構が実装された時点で本ヘルパーは削除する。
    ログ出力先はtempfile.gettempdir()起点でsession_id単位に分離する
    （_stop_gate.pyの_stop_log_path先例に揃える）。
    ローテは_file_lock.rotate_if_neededを再利用する。
    """
    try:
        log_dir = pathlib.Path(tempfile.gettempdir())
        safe_session_id = re.sub(r"[^A-Za-z0-9_-]", "_", session_id or "unknown")
        log_path = log_dir / f"claude-agent-toolkit-issidechain-{safe_session_id}.log"
        _rotate_if_needed(log_path, max_bytes=1_000_000, generations=1)
        state = read_state(session_id) if session_id else {}
        entry = {
            "timestamp": datetime.datetime.now(datetime.UTC).isoformat(),
            "session_id": session_id,
            "tool_name": tool_name,
            "isSidechain": payload.get("isSidechain"),
            "transcript_path": payload.get("transcript_path"),
            "cwd": payload.get("cwd"),
            "current_plan_file_path": state.get("current_plan_file_path") if isinstance(state, dict) else None,
        }
        with log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass


# --- mcp__codex__codex: codex-review.md未読ブロック ---


def _check_codex_review_not_read(state: dict) -> bool:
    """codex-review.mdが未読の場合にブロックする。ブロック時Trueを返す。"""
    if state.get("codex_review_read", False):
        return False
    print(
        _llm_notice(
            "codex-review.md has not been read in this session."
            " Read skills/plan-mode/references/codex-review.md before calling mcp__codex__codex.",
            tag="block",
        ),
        file=sys.stderr,
    )
    return True


def _check_codex_mcp_via_plan_codex_reviewer(state: dict, *, tool_name: str) -> bool:
    """plan-codex-reviewerサブエージェント経由の実施履歴が無い直接呼び出しをブロックする。

    `plan_codex_reviewer_invoked`(成功起動記録)と`plan_codex_reviewer_blocked`(起動失敗記録)の
    いずれも偽の場合にTrueを返す（ブロック方向）。auto mode下でサブエージェント経由が
    ブロックされた場合は`plan_codex_reviewer_blocked`が真となるため直接呼び出しが許容される
    （`codex-review.md`「実行経路」節の既存例外条件と整合）。
    `tool_name`は通知文で呼び出し元ツール名（`mcp__codex__codex`または`mcp__codex__codex-reply`）を
    明示するために使う。
    """
    if state.get("plan_codex_reviewer_invoked", False):
        return False
    if state.get("plan_codex_reviewer_blocked", False):
        return False
    print(
        _llm_notice(
            f"{tool_name} call is blocked because it did not go through `agent-toolkit:plan-codex-reviewer` subagent."
            " The default path is via `agent-toolkit:plan-codex-reviewer` subagent"
            " (see `agent-toolkit/skills/plan-mode/references/codex-review.md` execution route section)."
            " Invoke the subagent first. If the subagent invocation is blocked by the environment,"
            " it will set `plan_codex_reviewer_blocked` and this hook will allow the direct call.",
            tag="block",
        ),
        file=sys.stderr,
    )
    return True


# --- mcp__codex__codex: sandbox自動修正 ---


def _check_codex_mcp_sandbox(tool_input: dict) -> dict:
    """Codex MCP呼び出しのsandboxを常にdanger-full-accessへ強制固定する。

    本環境ではread-only・workspace-writeでcodexプロセスがハングして復帰しないため、
    呼び出し側の指定値によらずdanger-full-access固定運用とする。
    呼び出し側は`sandbox`パラメーターを渡す必要が無い
    （渡しても本フックが上書きするため、指定は無視される）。

    設計意図（回帰予防）: 過去に「利用者の明示指定を尊重する」形へ変更された履歴があるが、
    本環境ではハング回避を優先し安全側の強制固定を採用する。
    フィードバック反映等で「利用者の明示指定を尊重する」形へ再度変更しないこと。
    """
    updated_input = dict(tool_input)
    already_correct = tool_input.get("sandbox") == "danger-full-access"
    updated_input["sandbox"] = "danger-full-access"
    result: dict = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "allow",
            "updatedInput": updated_input,
        },
    }
    if not already_correct:
        result["systemMessage"] = "[agent-toolkit] forced codex MCP sandbox to danger-full-access."
    return result


def _check_askuserquestion_scope_escalation(tool_input: dict) -> tuple[str, str] | None:
    """AskUserQuestion入力から縮退誘発フレーズを検出して該当カテゴリとマッチ文言を返す。

    対象は`questions[].options[].label`、`questions[].options[].description`の各テキスト。
    `questions[].question`と`questions[].header`はユーザーへの状況説明性質を持つため対象外とする
    （エージェントの意思表明は選択肢側に現れる前提）。
    検出時は最初に一致したパターンの`(category, matched_phrase)`を返す。未検出時はNone。
    入力の構造が想定外（questionsが配列でないなど）の場合は検査不能としてNoneを返す。
    ブロックメッセージへのマッチ文言含有は`agent-toolkit:agent-standards`
    「コンテキスト汚染の回避」節の例外規定に従う。
    """
    questions = tool_input.get("questions")
    if not isinstance(questions, list):
        return None
    for question in questions:
        if not isinstance(question, dict):
            continue
        options = question.get("options")
        if not isinstance(options, list):
            continue
        for option in options:
            if not isinstance(option, dict):
                continue
            for field in ("label", "description"):
                text = option.get(field)
                if isinstance(text, str):
                    match_result = _match_scope_escalation(text, exclude_categories={"pattern-conformance"})
                    if match_result is not None:
                        return match_result
    return None


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:  # noqa: BLE001 -- pluginが破損して編集できなくなる事故を避けるため広範に捕捉
        # 予期せぬ例外は安全側として通過させる。デバッグのためスタックトレースはstderrに出力する。
        traceback.print_exc()
        sys.exit(0)
