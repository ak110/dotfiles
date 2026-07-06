#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["pyfltr>=3.14.1"]
# ///
# pylint: disable=too-many-lines  # ハンドラ網羅のためチェック実装が多く、分割するとモジュール間の依存関係が複雑化するため許容する
r"""Claude Code plugin agent-toolkit: PreToolUse統合フック。

任意ツールの実行前に以下のチェックを順に実行する。
block系checkは1プロセスで直列実行し、最初の違反でexit 2する。
warn種別のcheckはstderrまたはstdoutに警告を表示しつつ処理を継続する。
auto-fix種別のcheckは`updatedInput`でツール入力を自動書き換えする。

統合しているチェック:

任意ツール:

- メインエージェント応答の日本語文字比率が閾値未満の場合の警告/ブロック (warn/block)
- plan-modeスキル未起動のままのplan file編集（Write/Edit/MultiEdit）のブロック (block)
- plan file編集前の必須リファレンス（textlint-violations.md / plan-file-guidelines.md）未読のブロック (block)
- plan fileのWriteで文書サイズ上限対象ファイルのwc -l実測値記録漏れのブロック (block)
- 規範対象ドキュメントへのメタ規範新設編集時、計画ファイルの遡及スキャン結果記録未整備のブロック (block)
- plan fileのWriteで事前lint検査未実施のブロック (block)
- plan fileのWrite/Edit/MultiEditで対象ファイル一覧とH3見出しの1対1対応違反のブロック (block)
- plan fileのWriteでワークアラウンド語検出時のscratchpad事前検討記録未整備のブロック (block)

ExitPlanMode:

- 工程7（plan-reviewer / naive-executor / plan-impl-reviewer / codexレビュー、
  対象ファイル一覧にコーディングエージェント向け文書を含む計画では条件付きでagent-doc-validatorも追加）
  完了未達のブロック (block)

mcp__codex__codex:

- codex-review.md未読時のブロック (block)
- `sandbox`パラメーターの`danger-full-access`自動修正 (auto-fix)
- 全チェック通過時の強制承認 (auto-approve)

mcp__codex__codex-reply:

- 無条件の強制承認 (auto-approve)

Bash:

- git amend / rebase直前に`git log`未確認のブロック (block)
- 非Pythonプロジェクトでの`uv run python <path>`形式起動のブロック (block)
- `git commit`未検証警告 (warn)
- `agent-toolkit/`配下のコミット時のversion bump漏れ警告 (warn)
- `git log --decorate`の自動付与 (auto-fix)
- `codex exec`の未決事項念押し (warn)

AskUserQuestion:

- 縮退誘発フレーズ（作業量・残コンテキスト等を根拠とした分割可否相談・進め方確認）の検出 (block)

Skill:

- `agent-toolkit:plan-mode`起動時の工程7完了フラグリセット（新計画着手の合図） (auto-fix)
- `agent-toolkit:plan-impl`起動時の工程7完了未達のブロック (block)

Write / Edit / MultiEdit:

- 文字化け（U+FFFD）検出 (block)
- `.ps1` / `.ps1.tmpl`へのLF-only書き込み検出 (block)
- lockfile / 生成物ディレクトリの直接編集 (block)
- シークレット / 鍵ファイルの直接編集 (block)
- `agent-toolkit/rules/`配下・`agent-toolkit/skills/**/SKILL.md`・計画ファイルへの
  scope-escalationフレーズ転記検出 (block)
- manifestファイルの手編集 (warn)
- ホームディレクトリの絶対パス混入 (warn)
- 口語的な日本語表現の混入 (warn)
- 「Xを根拠にYしない」「Xを理由にYしない」形式のメタ規範文言の増加 (warn)

各チェックの詳細仕様（対象パターン・エラー文言・例外条件）は対応する実装関数のdocstringを参照する。
block系checkの検査対象は「新規に書き込まれる側」（`content` / `new_string`）のみ。
`old_string`は既存内容の修正・削除を妨げないため検査しない。
Edit/MultiEditのscope-escalation checkはフレーズ出現回数の増加のみを検出する（既存保持時の誤検出を解消）。
"""

import json
import os
import pathlib
import re
import shlex
import subprocess
import sys
import tempfile
import traceback
from collections.abc import Iterator

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import _plan_format  # noqa: E402  # pylint: disable=wrong-import-position,import-error
import _response_language_check  # noqa: E402  # pylint: disable=wrong-import-position,import-error
from _bash_command_parser import (  # noqa: E402  # pylint: disable=wrong-import-position,import-error
    extract_git_events,
)
from _message_format import llm_notice as _llm_notice_base  # noqa: E402  # pylint: disable=wrong-import-position,import-error
from _plan_file import compute_prelint_hashes, is_plan_file  # noqa: E402  # pylint: disable=wrong-import-position,import-error
from _session_state import read_state, update_state  # noqa: E402  # pylint: disable=wrong-import-position,import-error
from pyfltr.colloquial import check as _colloquial_check  # noqa: E402  # pylint: disable=wrong-import-position

# U+FFFD（REPLACEMENT CHARACTER）: UTF-8デコード失敗時の代替文字
_REPLACEMENT_CHAR = "\ufffd"

# 文書サイズ上限チェック対象パターン（計画ファイルの`## 変更内容`に列挙されたパスの照合に使用）。
# agent-toolkit配下のルール・スキル・エージェント定義ファイルと、グローバルルールファイルが対象。
_SIZE_LIMIT_TARGET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"agent-toolkit/rules/[^/]+\.md$"),
    re.compile(r"agent-toolkit/skills/[^/]+/SKILL\.md$"),
    re.compile(r"agent-toolkit/skills/[^/]+/references/.+\.md$"),
    re.compile(r"agent-toolkit/agents/.+\.md$"),
    re.compile(r"\.chezmoi-source/dot_claude/rules/.+\.md$"),
)
# basenameで照合する文書サイズ上限対象ファイル名
_SIZE_LIMIT_TARGET_BASENAMES: frozenset[str] = frozenset({"AGENTS.md", "CLAUDE.md"})

# このスクリプトの hook 識別子。
_HOOK_ID = "agent-toolkit/pretooluse"

# AskUserQuestion向け縮退誘発フレーズ検出パターン。
# 01-agent.md「セッション分割・別計画化は禁止する」節および「縮退表明は発行しない」項目で禁止される、
# 作業量・残コンテキスト・所要時間・修正コスト等を根拠としたユーザーへの打診、
# および規範違反を明示認識せず工程を省略・割愛する宣言を機械検出する。
_SCOPE_ESCALATION_PHRASES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("workload", re.compile(r"作業量(的|面)?(で|に|が)?(困難|厳しい|多い|膨大|大きい)")),
    ("single-session", re.compile(r"1?セッション(内|で)(の|に)?(完遂|完了|終わら|収まら|終わり)")),
    (
        "approach-confirm",
        re.compile(
            r"(進め方を(確認|相談|決め|聞|教え)|完遂か最小限か|完遂は時間がかかる|規範遵守で時間がかかる|どう進めるべきか指示)"
        ),
    ),
    ("split-execution", re.compile(r"分割(して|で)(進|対応|実装|完了|処理)")),
    (
        "context-shortage",
        re.compile(
            r"(残(り)?コンテキスト|ターン数(が増|を踏まえ|の自己推定|の上限)|対話往復(が増|を踏まえ|の上限)|これ以上のターン"
            r"|コンテキスト容量[^、。\n]{0,10}(超え|上回|不足)|実装完遂リスク[^、。\n]{0,10}(上回|超え|見合わ)"
            r"|次回?サイクル[^、。\n]{0,5}(持ち越し|送り|回))"
        ),
    ),
    (
        "defer-onset",
        re.compile(r"((着手|対応|実装)(を)?(延期|後回し|別途|別計画)|別作業(と|扱い|化)|別(issue|チケット|PR)(と|扱い|化))"),
    ),
    ("priority-consult", re.compile(r"(優先順位|スコープ|範囲)[^、。\n]{0,8}(相談|確認|聞|委ね|任せ|決め)")),
    ("scope-volume", re.compile(r"(対象|作業)(件数|範囲)が(多|広|膨大)")),
    (
        "pattern-conformance",
        re.compile(
            r"(既存パターン踏襲|本計画外|本計画スコープ外|広範改修要|現状維持|本タスク範囲外|本タスクスコープ外|本作業(範囲|スコープ)外"
            r"|主要[^、。\n]{0,10}(に絞|のみ実施|のみ対応|限定)"
            r"|代表(箇所|例|事例)[^、。\n]{0,8}(のみ|に限|限定)"
            r"|本サイクル[^、。\n]{0,10}(のみ|限定|に限)"
            r"|(今後|次回)(の)?改訂[^、。\n]{0,10}(随時|順次)?[^、。\n]{0,5}(是正|対応|反映))"
        ),
    ),
    (
        "process-omission",
        re.compile(
            r"(規範違反(として|を)(扱う|認識)|規範違反と認識した上で|規範違反を承知|規範違反は次回|規範チェック[^、。\n]{0,10}スキップ|工程省略|工程を省略|割愛(する|します)|本計画[^、。\n]{0,20}省略(する|します))"
        ),
    ),
    (
        "process-scale",
        re.compile(
            r"工程(?:規模|数|ceremony|セレモニー)?[^、。\n]{0,10}(多すぎ|大きすぎ|膨大|重すぎ|簡略化|圧縮する|省略する|バイパスする|スキップする)"
            r"|セレモニー[^、。\n]{0,10}(重すぎ|省略する|スキップする)"
            r"|(plan-mode|規範)[^、。\n]{0,10}ceremony[^、。\n]{0,10}(省略する|スキップする|バイパスする)"
        ),
    ),
    (
        "mitigation-in-adoption",
        re.compile(
            r"(機械化[^、。\n]{0,5}除外|Write検査[^、。\n]{0,5}(過剰|除外)|軽減版[^、。\n]{0,5}(採用|扱い)"
            r"|過剰部分[^、。\n]{0,5}除外|縮退版[^、。\n]{0,5}(採用|扱い)"
            r"|(実効性|実装コスト)[^、。\n]{0,15}(コスト高|見合わ))"
        ),
    ),
    (
        "async-wait",
        re.compile(
            r"((完了通知|完了報告)[^、。\n]{0,10}(待つ|待機|待って)"
            r"|サブエージェント[^、。\n]{0,10}(終了|完了|応答|通知)[^、。\n]{0,10}(待つ|待機|待って))"
        ),
    ),
    (
        "quality-gate-count",
        re.compile(
            r"hookブロック[^、。\n]*繰り返|lint違反[^、。\n]*膨大|違反件数[^、。\n]*進行困難|ブロック回数を踏まえ|修正量が多い"
        ),
    ),
    (
        "quality-tradeoff",
        re.compile(r"(規模が大きすぎ|品質(が|を)?維持(が|を)?(できない|困難)|工数(対効果|見合い|見合わ))"),
    ),
    (
        "next-cycle-defer",
        re.compile(
            r"((次(サイクル|セッション|計画)|別セッション|独立(の)?セッション)(で|に)?(扱う|再評価|再検討|対応|送り)"
            r"|(スコープ|テーマ|計画)を超える"
            r"|今回(の)?(スコープ|対応|対象)(外|から外)"
            r"|(影響(が)?(大き|大)い|影響大)(ため|のため|により)"
            r"|現行アーキテクチャ(の)?(大幅|根本)(な?)(見直し|改修)"
            r"|後続(作業|対応|PR|チケット|issue)(で|に|へ)?(委ね|扱う|対応|送)"
            r"|次回対応(と|扱い|とする|に回す))"
        ),
    ),
    (
        "fabricated-metrics",
        re.compile(
            r"(約|およそ)?\d{1,3}\s*%[^、。\n]{0,10}(消費|使用|埋)"
            r"|残り[^、。\n]{0,10}\d[\d,]*\s*(トークン|token)"
            r"|経過\s*\d+\s*(時間|分)"
            r"|約\s*\d+[kK]?トークン"
            r"|\d+時間(経過|相当)"
        ),
    ),
)


# scope-escalation違反検出時に代替表現例をエラーメッセージへ添える辞書。
# 各カテゴリごとに最大2件の代替表現例を保持する。
# 代替表現の意図: 縮退・先送りに帰結する発話ではなく、規範に即した観測事象の記述・完遂宣言・
# 根本対応の提示へ書き直すよう誘導する。
_SCOPE_ESCALATION_ALTERNATIVES: dict[str, tuple[str, ...]] = {
    "workload": ("観測可能な技術的制約を根拠に述べる", "同一セッション内で完遂する方針を述べる"),
    "single-session": ("同一セッション内で完遂する方針を述べる", "計画分割の対象は同一セッション内に限定する"),
    "approach-confirm": ("技術的最適案を第1選択肢に置いた選択肢を提示する", "自律実行できる範囲は自律決定する"),
    "split-execution": ("同一セッション内での複数計画ファイル併用として提示する", "並列サブエージェント委譲として提示する"),
    "context-shortage": ("公式仕様に基づく制約のみを根拠に述べる", "自己推定を根拠とした打診を発行しない"),
    "defer-onset": ("同一計画内で対処項目として組み込む", "根本原因への対応方針を提示する"),
    "priority-consult": ("技術的最適順序を第1提案として提示する", "自律判断で順序を決めて着手する"),
    "scope-volume": ("並列サブエージェント委譲による分担案を提示する", "自律実行で完遂する方針を述べる"),
    "pattern-conformance": ("既存違反も同一計画内で是正対象として組み込む", "根本対応案を主提案として提示する"),
    "process-omission": ("各工程の実施義務を果たす", "各工程は実施対象として扱う"),
    "process-scale": ("工程数に依らず全工程を実施する", "規範上の必須工程は完遂対象として扱う"),
    "mitigation-in-adoption": ("原文どおり採用するか不採用とする二択", "反映内容の縮小は不採用根拠にならない"),
    "async-wait": ("進捗中間報告・状態確認・別作業への切替で動作を継続する",),
    "quality-gate-count": (
        "品質ゲートのブロックを正常動作として扱い1件ずつ修正して再試行する方針を述べる",
        "ブロック回数・違反件数・修正量を遂行可能性の判断材料にしない方針を述べる",
    ),
    "quality-tradeoff": ("観測可能な技術的不成立の根拠を述べる", "同一計画内で完遂する方針を述べる"),
    "next-cycle-defer": ("同一計画内で対処項目として組み込む", "同一セッション内で完遂する"),
    "fabricated-metrics": ("実測値を扱わず、定性的な進捗記述に留める", "実施済み工程・残工程・観測事象で進捗を述べる"),
}


def _format_scope_escalation_alternatives(category: str) -> str:
    """scope-escalationカテゴリに対応する代替表現例を1行文字列で返す。

    エラーメッセージ末尾へ添えるための整形ヘルパー。
    対応するカテゴリが存在しない場合は空文字列を返す。
    """
    alternatives = _SCOPE_ESCALATION_ALTERNATIVES.get(category)
    if not alternatives:
        return ""
    joined = "／".join(f"『{item}』" for item in alternatives)
    return f" 代替表現例: {joined}。"


def _scope_escalation_agent_md_reference(category: str) -> str:
    """scope-escalationカテゴリに対応する参照先規範節の文言を返す。

    `mitigation-in-adoption`は反映内容の縮小をフィードバック採否の場面で扱うため
    `agent-toolkit/skills/apply-feedback/SKILL.md`「採用時の反映内容の縮小禁止」節を参照する。
    他カテゴリは`agent-toolkit/rules/01-agent.md`「セッション分割・別計画化は禁止する」節を参照する。
    """
    if category == "mitigation-in-adoption":
        return "agent-toolkit/skills/apply-feedback/SKILL.md「採用時の反映内容の縮小禁止」節"
    return "agent-toolkit/rules/01-agent.md「セッション分割・別計画化は禁止する」節"


def _llm_notice(body: str, *, tag: str = "") -> str:
    """コーディングエージェント宛てメッセージを標準プレフィックス/サフィックス付きで整形する。"""
    return _llm_notice_base(body, _HOOK_ID, tag=tag)


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

    # plan file編集前の必須リファレンス未読の場合はブロック
    if _check_plan_file_required_reads_first(tool_name, tool_input, session_id):
        return 2

    # plan fileのWriteで文書サイズ上限対象ファイルのwc -l実測値記録漏れがある場合はブロック
    if _check_plan_file_size_limit_target_wc_l_recorded(tool_name, tool_input):
        return 2

    # 規範対象ドキュメントへのメタ規範新設編集時、計画ファイルの遡及スキャン記録未整備をブロック
    if _check_plan_file_retroactive_scan_recorded(tool_name, tool_input, session_id):
        return 2

    # plan fileのWrite時にscratchpad事前lint検査未実施の場合はブロック
    if _check_plan_file_prelint_passed(tool_name, tool_input, session_id):
        return 2

    # plan fileのWrite時にH2節順違反がある場合はブロック
    if _check_plan_file_h2_section_order(tool_name, tool_input):
        return 2

    # plan fileのWrite/Edit/MultiEdit時に対象ファイル一覧とH3見出しの1対1対応違反をブロック
    if _check_plan_file_target_files_h3_correspondence(tool_name, tool_input):
        return 2

    # plan fileのWrite/Edit/MultiEdit時に絶対行番号トークン直書きをブロック
    if _check_plan_file_absolute_line_numbers(tool_name, tool_input):
        return 2

    # plan fileのWrite時にワークアラウンド語検出に伴うscratchpad事前検討記録未整備をブロック
    if _check_workaround_scratchpad_gate(tool_name, tool_input):
        return 2

    # ExitPlanMode: 工程7（4サブエージェント/codexレビュー）の完了未達をブロック
    if tool_name == "ExitPlanMode":
        if _check_process7_completion_before_exit_plan_mode(session_id):
            return 2
        flush_pending_language_warning()
        return 0

    # Skill: plan-mode起動時は工程7完了フラグをリセット、plan-impl起動時は工程7完了未達をブロック
    if tool_name == "Skill":
        skill_name = tool_input.get("skill")
        if isinstance(skill_name, str) and skill_name in _PLAN_MODE_SKILL_NAMES:
            _reset_process7_completion_flags(session_id)
        elif (
            isinstance(skill_name, str)
            and skill_name in _PLAN_IMPL_SKILL_NAMES
            and _check_process7_completion_before_exit_plan_mode(session_id)
        ):
            return 2
        flush_pending_language_warning()
        return 0

    # AskUserQuestion: 縮退誘発フレーズ検出
    if tool_name == "AskUserQuestion":
        category = _check_askuserquestion_scope_escalation(tool_input)
        if category is not None:
            print(
                _llm_notice(
                    f"blocked: AskUserQuestionに縮退誘発フレーズ（カテゴリ: {category}）を検出。"
                    f"{_scope_escalation_agent_md_reference(category)}を参照。"
                    f"カテゴリ定義は`agent-toolkit:agent-standards`配下"
                    f"`references/scope-escalation-phrases.md`の隔離リファレンスを参照。"
                    f"{_format_scope_escalation_alternatives(category)}",
                ),
                file=sys.stderr,
            )
            return 2
        flush_pending_language_warning()
        return 0

    # mcp__codex__codex: codex-review.md未読ブロック + sandbox自動修正 + 強制承認
    if tool_name == "mcp__codex__codex":
        if _check_codex_review_not_read(session_id):
            return 2
        result = _check_codex_mcp_sandbox(tool_input)
        if result is not None:
            emit_json(result)
            return 0
        emit_json(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "allow",
                },
            }
        )
        return 0

    # mcp__codex__codex-reply: 無条件の強制承認
    if tool_name == "mcp__codex__codex-reply":
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

    # --- warn系check（stderrに警告のみ、exit codeは0のまま）---
    _check_manifest(tool_name, file_path)
    _check_home_path(tool_name, fields, file_path)
    _check_colloquial(tool_name, fields, file_path)
    _check_style_negation(tool_name, tool_input, file_path)

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


def _match_scope_escalation_increase(old: str, new: str) -> str | None:
    """new側でフレーズ出現回数がold側より増加したカテゴリ識別子を返す。

    `_SCOPE_ESCALATION_PHRASES`を走査し、各パターンのfindall件数を比較する。
    new側件数がold側件数を上回るカテゴリを最初に検出した時点で当該識別子を返す。
    既存文字列の保持時はold=new同数となり通過する（既存保持部分での誤検出を防ぐ）。
    増加が無い場合はNoneを返す。
    """
    for category, pattern in _SCOPE_ESCALATION_PHRASES:
        if len(pattern.findall(new)) > len(pattern.findall(old)):
            return category
    return None


def _check_scope_escalation_in_doc_edit(tool_name: str, tool_input: dict, file_path: str) -> bool:
    """対象ドキュメントへの編集時、フレーズ出現回数の増加を検出した場合にblockする。

    対象は`agent-toolkit/rules/`配下・`agent-toolkit/skills/**/SKILL.md`（`references/`配下を除く）・
    計画ファイル（`~/.claude/plans/`直下）。
    Edit/MultiEditは`_match_scope_escalation_increase`でnew側件数 > old側件数のカテゴリを検出する。
    既存文字列の保持時は件数同数で通過する（誤検出解消）。
    Writeは`content`全文を検査する。
    判定パターンは`_SCOPE_ESCALATION_PHRASES`を再利用しAskUserQuestion checkと同一の検出基準とする。
    `agent-toolkit:agent-standards`「コンテキスト汚染の回避」節に従い、検出フレーズ本文は通知へ転記せず
    カテゴリ識別子のみを通知する。
    """
    if not _is_scope_escalation_target_doc(file_path):
        return False
    detection: tuple[str, str] | None = None
    if tool_name == "Write":
        content = tool_input.get("content")
        if isinstance(content, str):
            category = _match_scope_escalation(content)
            if category is not None:
                detection = ("content", category)
    elif tool_name == "Edit":
        old_string = tool_input.get("old_string") or ""
        new_string = tool_input.get("new_string") or ""
        if isinstance(new_string, str):
            old_string = old_string if isinstance(old_string, str) else ""
            category = _match_scope_escalation_increase(old_string, new_string)
            if category is not None:
                detection = ("new_string", category)
    elif tool_name == "MultiEdit":
        edits = tool_input.get("edits") or []
        if isinstance(edits, list):
            for index, edit in enumerate(edits):
                if not isinstance(edit, dict):
                    continue
                old_string = edit.get("old_string") or ""
                new_string = edit.get("new_string") or ""
                if not isinstance(new_string, str):
                    continue
                old_string = old_string if isinstance(old_string, str) else ""
                category = _match_scope_escalation_increase(old_string, new_string)
                if category is not None:
                    detection = (f"edits[{index}].new_string", category)
                    break
    if detection is None:
        return False
    field, category = detection
    print(
        _llm_notice(
            f"blocked: scope-escalation phrase (category: {category})"
            f" detected in {tool_name}.{field}. Target: {file_path}."
            f" {_scope_escalation_agent_md_reference(category)}を参照。"
            f" agent-toolkit/skills/agent-standards/SKILL.md「コンテキスト汚染の回避」節および"
            f" `references/scope-escalation-phrases.md`の隔離規定を参照。"
            f" 検出パターン本文をスキル本文・ルール本文・テストコードへ転記しない。"
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
                    f" no metaphorical verbs) per 03-styles.md 「日本語の品質を保つ」 section."
                    f" Target: {file_path}",
                    tag="warn",
                ),
                file=sys.stderr,
            )
            return True
    return False


# --- 「Xを根拠にYしない」形式の増加検出 (warn, FB10) ---

# 03-styles.md「日本語の品質を保つ」節が指摘する誤読リスクのある禁止規定形式。
# 「Xでなければ`Y`してよい」と誤読される可能性があるため、全称否定形への書き換えを推奨する。
_STYLE_NEGATION_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"([^、\s]{1,20})を根拠に([^、\s]{1,20})しない"),
    re.compile(r"([^、\s]{1,20})を理由に([^、\s]{1,20})しない"),
)


def _is_style_negation_target_doc(file_path: str) -> bool:
    """対象ドキュメント（文書サイズ上限対象と同一の判定基準）への編集かを判定する。"""
    if not file_path:
        return False
    normalized = file_path.replace("\\", "/")
    if any(pat.search(normalized) for pat in _SIZE_LIMIT_TARGET_PATTERNS):
        return True
    return pathlib.Path(normalized).name in _SIZE_LIMIT_TARGET_BASENAMES


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
            f"『Xを根拠にYしない』『Xを理由にYしない』形式のメタ規範文言の増加を{tool_name}で検出。"
            f" Target: {file_path}."
            " 「Xでなければ`Y`してよい」と誤読される可能性がある。"
            " 全称否定形（『いかなる理由（例: X）があってもYしない』）への書き換えを検討する。"
            " 03-styles.md「日本語の品質を保つ」節を参照。",
            tag="warn",
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
    plan file編集に至るまでは警告を出さない
    （`apply-feedback`等の他スキル呼び出し・通常のRead・Bash操作は素通りする）。
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
) -> bool:
    """Plan fileを編集しようとした際に`_PLAN_FILE_REQUIRED_READS`の未読要素がある場合にブロックする。

    判定条件:

    - `session_id`が空でない（空ならセッション状態を取得できず判定不能のためスキップ）
    - `tool_name`が`Write` / `Edit` / `MultiEdit`のいずれか
    - 対象の`file_path`が`~/.claude/plans/`直下の計画ファイル
    - `_PLAN_FILE_REQUIRED_READS`のいずれかのフラグがセッション状態上で偽

    各リファレンスを一度Readするとフラグが設定され、以降の判定から除外される。
    未読要素が複数ある場合も1回のブロックメッセージへ全件列挙する
    （段階的な複数回ブロックを避けるため）。
    `permission_mode`の値に依らず適用する（plan mode外でも計画ファイル編集時には同様に違反が起こり得るため）。
    """
    if not session_id:
        return False
    if tool_name not in _PLAN_FILE_EDIT_TOOLS:
        return False
    file_path_raw = tool_input.get("file_path")
    if not isinstance(file_path_raw, str) or not is_plan_file(file_path_raw):
        return False
    state = read_state(session_id)
    unread = [
        (skill_name, reference_path, purpose_sentence)
        for flag_name, skill_name, reference_path, purpose_sentence in _PLAN_FILE_REQUIRED_READS
        if not state.get(flag_name, False)
    ]
    if not unread:
        return False
    lines = [
        f"- `{skill_name}` reference `{reference_path}`: {purpose_sentence}"
        for skill_name, reference_path, purpose_sentence in unread
    ]
    print(
        _llm_notice(
            "blocked: attempting to edit a plan file without reading required references.\n"
            "Read them first, then retry the plan file edit.\n" + "\n".join(lines),
            tag="block",
        ),
        file=sys.stderr,
    )
    return True


# --- plan file書き込み時の事前lint検査未実施をブロック ---


def _check_plan_file_prelint_passed(
    tool_name: str,
    tool_input: dict,
    session_id: str,
) -> bool:
    """Plan fileのWrite時にscratchpad事前lint検査未実施をブロックする。

    ブロック条件（すべて満たすとき`True`を返してWriteをブロックする）:

    - `session_id`が空でない（空ならセッション状態を取得できず判定不能のためスキップ）
    - `tool_name`が`Write`
    - 対象の`file_path`が`~/.claude/plans/`直下の計画ファイル
    - `tool_input["content"]`が文字列
    - `_plan_file.compute_prelint_hashes`で算出した2種類のSHA256のうち、
      `plan_prelint_passed`（pyfltr成功記録）と
      `plan_prelint_passed_line_width`（check_line_width.py成功記録）の双方に
      一致するハッシュが登録されていない

    pyfltrとcheck_line_widthの双方の成功記録が必要。
    pyfltrのみ成功・check_line_widthのみ成功の状態ではブロックされる。
    """
    # テスト時のバイパス: 環境変数で明示指定された場合のみ。プロダクションでは未設定のため副作用なし
    if os.environ.get("AGENT_TOOLKIT_PRELINT_TEST_BYPASS") == "1":
        return False
    if not session_id:
        return False
    if tool_name != "Write":
        return False
    file_path_raw = tool_input.get("file_path")
    if not isinstance(file_path_raw, str) or not is_plan_file(file_path_raw):
        return False
    content = tool_input.get("content")
    if not isinstance(content, str):
        return False
    state = read_state(session_id)
    passed = state.get("plan_prelint_passed", [])
    if not isinstance(passed, list):
        passed = []
    passed_set = set(passed)
    passed_lw = state.get("plan_prelint_passed_line_width", [])
    if not isinstance(passed_lw, list):
        passed_lw = []
    passed_lw_set = set(passed_lw)
    full_sha, stripped_sha = compute_prelint_hashes(content)
    pyfltr_ok = full_sha in passed_set or stripped_sha in passed_set
    line_width_ok = full_sha in passed_lw_set or stripped_sha in passed_lw_set
    if pyfltr_ok and line_width_ok:
        return False
    print(
        _llm_notice(
            "blocked: attempting to write a plan file without prior lint check."
            " Output the plan body (excluding fenced code blocks inside `## 背景`) to a scratchpad file,"
            " then run BOTH"
            " `uvx pyfltr run-for-agent --commands=textlint,markdownlint,typos,colloquial-check"
            " --enable=colloquial-check <scratchpad_path>`"
            " and the `check_line_width.py` script bundled with `agent-toolkit:writing-standards`"
            " until both pass, then retry the Write."
            " Prefer `cp <scratchpad_path> <plan_path>` for the retry"
            " only when the scratchpad content already passed BOTH pre-lint checks above."
            " See plan-mode/references/plan-file-guidelines.md for details.",
            tag="block",
        ),
        file=sys.stderr,
    )
    return True


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
            f"blocked: plan file H2 section order violation: {violation_str}"
            f" Required order: {list(_plan_format.PLAN_REQUIRED_H2)}."
            " Fix the section order and retry the Write.",
            tag="block",
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
    target_files = {p.strip().strip("`") for p in _plan_format.extract_target_files_from_changes(content)}
    if not target_files:
        return False
    h3_headings = {
        h.strip().strip("`")
        for h in _plan_format.extract_h3_headings_under_h2(content, "変更内容")
        if h.strip().strip("`") != _TARGET_FILE_LIST_HEADING
    }
    missing_h3 = target_files - h3_headings
    extra_h3 = h3_headings - target_files
    if not missing_h3 and not extra_h3:
        return False
    parts = []
    if missing_h3:
        parts.append(f"H3見出し未対応の対象ファイル: {sorted(missing_h3)}")
    if extra_h3:
        parts.append(f"対象ファイル一覧に無いH3見出し: {sorted(extra_h3)}")
    print(
        _llm_notice(
            "blocked: plan file `## 変更内容`配下の対象ファイル一覧とH3見出しが1対1で対応していない。"
            f" {' '.join(parts)}."
            " 各対象ファイルに対応するH3見出しを追加するか、不要なH3見出し・対象ファイル記載を削除する。",
            tag="block",
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
            "blocked: plan file body contains absolute line-number references"
            " (per plan-file-guidelines.md absolute-numbers norm)."
            " Use section names or heading references instead,"
            " or annotate the token with '<!-- line-ref-ok -->' under '## 調査結果'."
            f" Matches: {shown_str}{tail}.",
            tag="block",
        ),
        file=sys.stderr,
    )
    return True


# --- plan fileのワークアラウンド語検出時のscratchpad事前検討記録チェック ---

# 検出対象語。フォールバック・回避策的な対応の温存を検出する。
_WORKAROUND_TERMS: tuple[str, ...] = ("フォールバック", "回避策", "迂回", "失敗時対処")
_WORKAROUND_FAILURE_PATTERN = re.compile(r"が失敗する場合は.{0,30}?する")
_WORKAROUND_REQUIRED_ITEMS: tuple[str, ...] = ("根本原因の候補", "根本対応が成立するか", "成立しない場合の理由")


def _workaround_scratchpad_memo_path(plan_file_path: str) -> pathlib.Path:
    """計画ファイルパスからワークアラウンド事前検討メモのscratchpadパスを導出する。

    計画ファイル自身のstem（拡張子を除いたbasename）をキーとして使い、
    `{tempdir}/scratchpad/workaround-check-<plan_file_stem>.md`を返す。
    計画ファイル1件につきメモ1件が対応するため、由来を問わず全ての計画ファイルへ一律適用できる。
    """
    stem = pathlib.Path(plan_file_path).stem
    return pathlib.Path(tempfile.gettempdir()) / "scratchpad" / f"workaround-check-{stem}.md"


def _check_workaround_scratchpad_gate(tool_name: str, tool_input: dict) -> bool:
    """Plan fileのWrite時、ワークアラウンド語検出に伴うscratchpad事前検討記録の未整備をブロックする。

    判定条件:

    - `tool_name`が`Write`
    - 対象の`file_path`が`~/.claude/plans/`直下の計画ファイル
    - `tool_input["content"]`が文字列
    - `## 変更内容`セクション本文にワークアラウンド語（`_WORKAROUND_TERMS`または`_WORKAROUND_FAILURE_PATTERN`）が出現する

    上記を満たす場合、`_workaround_scratchpad_memo_path`が計画ファイルパスから導出する
    `{tempdir}/scratchpad/workaround-check-<plan_file_stem>.md`の存在と
    必須3項目（`_WORKAROUND_REQUIRED_ITEMS`）の記入を検査する。
    ファイル不在または必須項目の記入漏れがあればブロックする。
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

    scratchpad_path = _workaround_scratchpad_memo_path(file_path_raw)
    if not scratchpad_path.exists():
        print(
            _llm_notice(
                f"blocked: plan file `## 変更内容`にワークアラウンド語を検出したが"
                f" `{scratchpad_path}` が存在しない。"
                f" 根本原因の候補・根本対応が成立するか・成立しない場合の理由を"
                f" scratchpadファイルへ記録してから再度Writeする。",
                tag="block",
            ),
            file=sys.stderr,
        )
        return True

    try:
        scratchpad_content = scratchpad_path.read_text(encoding="utf-8")
    except OSError:
        scratchpad_content = ""

    missing_items = [item for item in _WORKAROUND_REQUIRED_ITEMS if item not in scratchpad_content]
    if missing_items:
        print(
            _llm_notice(
                f"blocked: `{scratchpad_path}` に必須項目 {missing_items} の記入がない。"
                f" 根本原因の候補・根本対応が成立するか・成立しない場合の理由を記入してから再度Writeする。",
                tag="block",
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
    - 対象パスの実ファイル行数が200行以上
    - `## 調査結果`または`### エージェント判断`に対象ファイル基名と実測値±2の数値が共存しない

    対象ファイルが200行未満の場合、またはパスが`_SIZE_LIMIT_TARGET_PATTERNS`・
    `_SIZE_LIMIT_TARGET_BASENAMES`にマッチしない場合はブロックしない。
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
            is_target = any(pat.search(path_str) for pat in _SIZE_LIMIT_TARGET_PATTERNS)
            if not is_target:
                is_target = basename in _SIZE_LIMIT_TARGET_BASENAMES
            if not is_target:
                continue

            # 実ファイルが存在し200行以上かを確認
            real_path = cwd / path_str
            if not real_path.exists():
                continue
            try:
                with real_path.open(encoding="utf-8", errors="replace") as f:
                    actual_lines = sum(1 for _ in f)
            except OSError:
                continue
            if actual_lines < 200:
                continue

            # search_bodyに基名と実測値±2の数値が共存するかを判定
            if basename not in search_body:
                print(
                    _llm_notice(
                        f"blocked: 計画ファイルの`## 変更内容`に文書サイズ上限対象ファイル`{basename}`が含まれるが、"
                        f"`## 調査結果`または`### エージェント判断`にwc -l実測値の記載が見当たらない。"
                        f"期待値: {actual_lines}（±2許容、すなわち{actual_lines - 2}〜{actual_lines + 2}）。"
                        f"計画ファイルの`## 調査結果`または`### エージェント判断`に"
                        f"`{basename}`の実測行数を追記してから再度Writeする。",
                        tag="block",
                    ),
                    file=sys.stderr,
                )
                return True

            # 基名が含まれる場合は実測値±2の数値が近傍に存在するかを確認
            low, high = actual_lines - 2, actual_lines + 2
            if not any(low <= n <= high for n in numbers_in_body):
                print(
                    _llm_notice(
                        f"blocked: 計画ファイルの`## 変更内容`に文書サイズ上限対象ファイル`{basename}`が含まれるが、"
                        f"`## 調査結果`または`### エージェント判断`に記載の数値が実測値と一致しない。"
                        f"期待値: {actual_lines}（±2許容、すなわち{actual_lines - 2}〜{actual_lines + 2}）。"
                        f"計画ファイルの該当箇所を実測行数に更新してから再度Writeする。",
                        tag="block",
                    ),
                    file=sys.stderr,
                )
                return True

    except Exception:  # noqa: BLE001 -- 実ファイル読み込み失敗等は安全側として通過させる
        return False

    return False


# --- 規範対象ドキュメントへのメタ規範新設編集時の遡及スキャン記録チェック (FB4) ---

# 汎用禁止形: バレット末尾または新規節見出し直下バレットで句点・改行終端の禁止動詞を検出する。
_RETROACTIVE_SCAN_GENERIC_PROHIBITION_PATTERN = re.compile(
    r"[^\n]{0,80}(しない|禁止する|発行しない|省略しない)(。|$)", re.MULTILINE
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
) -> bool:
    """規範対象ドキュメントへのメタ規範新設編集時、現在の計画ファイルの遡及スキャン記録未整備をブロックする。

    判定条件:

    - `tool_name`が`Write` / `Edit` / `MultiEdit`のいずれか
    - 対象の`file_path`が文書サイズ上限対象パターン（`_SIZE_LIMIT_TARGET_PATTERNS` /
      `_SIZE_LIMIT_TARGET_BASENAMES`）に一致する規範対象ドキュメント（計画ファイル自身は対象外）
    - 新規/既存内容の比較で`_detect_new_meta_norm`が真
      （全称禁止形の新規出現、汎用禁止形バレットの増加、新規節見出しの増加のいずれか）
    - `session_id`のセッション状態から取得した`current_plan_file_path`の
      `## 調査結果`配下`### 遡及スキャン結果`小見出しに必須3項目（対象パターン・検出件数・対応方針）が
      記述されていない

    計画ファイルパスが未記録の場合は判定不能として通過させる（安全側でブロックしない）。
    """
    if tool_name not in ("Write", "Edit", "MultiEdit"):
        return False
    file_path_raw = tool_input.get("file_path")
    file_path = file_path_raw if isinstance(file_path_raw, str) else ""
    if not file_path or is_plan_file(file_path):
        return False
    normalized = file_path.replace("\\", "/")
    is_target = any(pat.search(normalized) for pat in _SIZE_LIMIT_TARGET_PATTERNS) or (
        pathlib.Path(normalized).name in _SIZE_LIMIT_TARGET_BASENAMES
    )
    if not is_target:
        return False

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
        return False

    if not session_id:
        return False
    state = read_state(session_id)
    plan_file_path = state.get("current_plan_file_path")
    if not isinstance(plan_file_path, str) or not plan_file_path:
        return False
    if _plan_file_has_retroactive_scan_record(plan_file_path):
        return False
    print(
        _llm_notice(
            f"blocked: {file_path}へのメタ規範新設パターンの編集を検出したが、"
            f"計画ファイル{plan_file_path}の`## 調査結果`配下`### 遡及スキャン結果`小見出しに"
            f"必須項目（対象パターン・検出件数・対応方針）の記述が見当たらない。"
            f"skills/plan-mode/references/norm-revision-checklist.md「規範対象範囲の網羅確認」節に従い、"
            f"遡及スキャン結果を計画ファイルへ記載してから再度編集する。",
            tag="block",
        ),
        file=sys.stderr,
    )
    return True


# --- 工程7（4サブエージェント/codexレビュー）完了チェック ---

# Skillツールの`skill`引数として許容するplan-mode / plan-implスキル名。
# posttooluse.pyの`_PLAN_MODE_SKILL_NAMES`と対応させる。
_PLAN_MODE_SKILL_NAMES: frozenset[str] = frozenset({"agent-toolkit:plan-mode", "plan-mode"})
_PLAN_IMPL_SKILL_NAMES: frozenset[str] = frozenset({"agent-toolkit:plan-impl", "plan-impl"})

# 工程7の完遂を示すセッション状態フラグ。
# 各フラグはposttooluse.pyが対応するAgent/Skill起動を観測して記録する
# （`agent-toolkit:agent-standards`スキル「セッション状態フラグ」節が全フラグ一覧のSSOT）。
_PROCESS7_COMPLETION_FLAGS: tuple[str, ...] = (
    "plan_reviewer_invoked",
    "naive_executor_invoked",
    "plan_impl_reviewer_invoked",
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


def _check_process7_completion_before_exit_plan_mode(session_id: str) -> bool:
    """ExitPlanMode呼び出しまたは`agent-toolkit:plan-impl`起動時、工程7完了未達をブロックする。

    判定条件:

    - `session_id`が空でない（空ならセッション状態を取得できず判定不能のためスキップ）
    - セッション状態の`plan_mode_skill_invoked`が真
      （plan-modeスキルを使わない文脈では工程7の完遂義務が生じないため対象外）
    - `_PROCESS7_COMPLETION_FLAGS`のいずれかが偽。
      計画の対象ファイル一覧にコーディングエージェント向け文書対象ファイルが含まれる場合は、
      `agent_doc_validator_invoked`も必須フラグに加える
      （`_should_require_agent_doc_validator`参照。無条件必須化はしない）

    未起動フラグは1回のブロックメッセージへ全件列挙する。
    """
    if not session_id:
        return False
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
            "blocked: attempting to exit plan mode or invoke `agent-toolkit:plan-impl`"
            " before completing Phase 7 (plan-reviewer / naive-executor /"
            " plan-impl-reviewer / codex review)."
            f" Missing flags: {missing}."
            " See agent-toolkit/skills/plan-mode/references/integrity-checks.md「工程7の実施手順」節.",
            tag="block",
        ),
        file=sys.stderr,
    )
    return True


def _reset_process7_completion_flags(session_id: str) -> None:
    """`agent-toolkit:plan-mode`スキル起動を検出した際に工程7完了フラグをリセットする。

    新計画への着手の合図として`_PROCESS7_COMPLETION_FLAGS`と条件付きフラグ
    `agent_doc_validator_invoked`を偽へ戻す。前計画の`current_plan_file_path`も
    新計画の対象ファイル判定へ誤流用しないよう消去する。
    """
    if not session_id:
        return

    def _reset(current: dict) -> dict | None:
        changed = False
        for flag in (*_PROCESS7_COMPLETION_FLAGS, "agent_doc_validator_invoked"):
            if current.get(flag, False):
                current[flag] = False
                changed = True
        if current.pop("current_plan_file_path", None) is not None:
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
        "systemMessage": "[agent-toolkit] git logに--decorateを自動的に挿入しました。",
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


# --- mcp__codex__codex: codex-review.md未読ブロック ---


def _check_codex_review_not_read(session_id: str) -> bool:
    """codex-review.mdが未読の場合にブロックする。ブロック時Trueを返す。"""
    state = read_state(session_id)
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


# --- mcp__codex__codex: sandbox自動修正 ---


def _check_codex_mcp_sandbox(tool_input: dict) -> dict | None:
    """Codex MCP呼び出しのsandboxがdanger-full-accessでなければ自動修正する。"""
    sandbox = tool_input.get("sandbox")
    if sandbox == "danger-full-access":
        return None
    updated_input = dict(tool_input)
    updated_input["sandbox"] = "danger-full-access"
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "allow",
            "updatedInput": updated_input,
        },
        "systemMessage": "[agent-toolkit] codex MCPのsandboxをdanger-full-accessに自動修正しました。",
    }


def _check_askuserquestion_scope_escalation(tool_input: dict) -> str | None:
    """AskUserQuestion入力から縮退誘発フレーズを検出して該当カテゴリ識別子を返す。

    対象は`questions[].question`、`questions[].header`、
    `questions[].options[].label`、`questions[].options[].description`の各テキスト。
    検出時は最初に一致したパターンのカテゴリ識別子を返す。未検出時はNone。
    入力の構造が想定外（questionsが配列でないなど）の場合は検査不能としてNoneを返す。
    検出フレーズ本文はメッセージへ転記せず、カテゴリ識別子のみで通知する
    （`agent-toolkit:agent-standards`「コンテキスト汚染の回避」節に従う）。
    """
    questions = tool_input.get("questions")
    if not isinstance(questions, list):
        return None
    for question in questions:
        if not isinstance(question, dict):
            continue
        for field in ("question", "header"):
            text = question.get(field)
            if isinstance(text, str):
                category = _match_scope_escalation(text)
                if category is not None:
                    return category
        options = question.get("options")
        if not isinstance(options, list):
            continue
        for option in options:
            if not isinstance(option, dict):
                continue
            for field in ("label", "description"):
                text = option.get(field)
                if isinstance(text, str):
                    category = _match_scope_escalation(text)
                    if category is not None:
                        return category
    return None


def _match_scope_escalation(text: str) -> str | None:
    """テキストへ`_SCOPE_ESCALATION_PHRASES`を照合し、最初に一致したカテゴリ識別子を返す。"""
    for category, pattern in _SCOPE_ESCALATION_PHRASES:
        if pattern.search(text) is not None:
            return category
    return None


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:  # noqa: BLE001 -- pluginが破損して編集できなくなる事故を避けるため広範に捕捉
        # 予期せぬ例外は安全側として通過させる。デバッグのためスタックトレースはstderrに出力する。
        traceback.print_exc()
        sys.exit(0)
