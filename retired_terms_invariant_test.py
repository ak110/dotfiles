"""撤去した名称と是正した定義の無い呼称が、許容箇所の外のGit追跡ファイルへ現れないことを検査する。

撤去を規範本文の置換だけで伝えると、過去のセッション記録、終端済みのWI、旧形式の計画およびコミットメッセージに残る
旧名や定義の無い呼称を読んだ書き手が、同じ語を現行の文書へ書き戻す。書き戻しをこのテストの失敗として検出する。

名称を撤去するときと、正式名を持つ対象を指していた定義の無い呼称を是正したときは、
`_RETIRED_TERMS`へその語、正式名または置き換え先および許容箇所を1件加える。
許容箇所は現行の読み取り互換を定める箇所（旧名の定数、その互換を説明する文、旧形式の基準文書、
旧形式を読む互換テスト）に限る。互換を定めない箇所を許容すると、その箇所への書き戻しを検出できない。
走査対象は`git grep`が扱うGit追跡ファイルの作業ツリー上の内容とし、隠しディレクトリも含める。
"""

from __future__ import annotations

import dataclasses
import pathlib
import subprocess

import pytest

_ROOT = pathlib.Path(__file__).resolve().parent


@dataclasses.dataclass(frozen=True)
class _AllowedLocation:
    """撤去語が残ってよい箇所。"""

    path_pattern: str
    """リポジトリ相対パスへ`PurePosixPath.match`で一致を判定するパターン。"""
    line_substring: str | None = None
    """指定した場合は、この文字列を含む行だけを許容する。"""
    line_exact: str | None = None
    """指定した場合は、この行だけを許容する。"""

    def allows(self, path: str, line: str) -> bool:
        """指定の行を許容するかを返す。"""
        if not pathlib.PurePosixPath(path).match(self.path_pattern):
            return False
        return (self.line_substring is None or self.line_substring in line) and (
            self.line_exact is None or self.line_exact == line
        )


@dataclasses.dataclass(frozen=True)
class _RetiredTerm:
    """撤去した名称と、その正式名および許容箇所。"""

    term: str
    replacement: str
    allowed: tuple[_AllowedLocation, ...]


# 命名の是正で撤去した語のうち、旧形式を読む互換の実装や外部の固定文言として残す箇所。
_NAMING_EXTRA_ALLOWED: dict[str, tuple[_AllowedLocation, ...]] = {
    "証拠行": (
        # 既存の達成根拠へ付加された標識を読み、観測本文と分けて比較する箇所。
        _AllowedLocation("agent-toolkit/skills/review-standards/scripts/check_exec_review_evidence.py", 'rf"証拠行'),
        _AllowedLocation(
            "agent-toolkit/skills/review-standards/scripts/check_exec_review_evidence_test.py", '"証拠行 {source}"'
        ),
        _AllowedLocation("agent-toolkit/share/exec-review.subagent.md", "`証拠行`"),
        _AllowedLocation("docs/development/design-review-evidence.md", "「証拠行」"),
    ),
    "計画レビュー": (
        # 旧形式の計画と計画レビュー表を読む互換の実装とそのテスト
        _AllowedLocation("agent-toolkit/agent_toolkit/_plan/structure/*.py"),
        _AllowedLocation("agent-toolkit/agent_toolkit/_atk/review_table.py"),
        _AllowedLocation("agent-toolkit/agent_toolkit/_atk/serve/plans/roots.py"),
        _AllowedLocation("*_test.py"),
    ),
    "統合後検証": (
        # 旧形式の計画を読む構造定数と、その互換を確かめるテスト、旧形式の基準文書
        _AllowedLocation("agent-toolkit/agent_toolkit/_plan/structure/constants.py"),
        _AllowedLocation("agent-toolkit/agent_toolkit/_plan/structure/references_test.py"),
        _AllowedLocation("agent-toolkit/skills/plan-mode/references/legacy-plan-file-standards.md"),
    ),
    "基準コミット": (
        # 旧形式の計画の別名を読む互換の実装とそのテスト
        _AllowedLocation("agent-toolkit/agent_toolkit/_plan/structure/parsing.py"),
        _AllowedLocation("agent-toolkit/agent_toolkit/_plan/structure/markdown_test.py"),
    ),
    "起動種別": (
        # `mode:`へ改める前の宣言行を読む互換の実装とそのテスト
        _AllowedLocation("agent-toolkit/agent_toolkit/_agents_server/task_documents.py", "_LEGACY_LAUNCH_KIND_PREFIX"),
        _AllowedLocation("agent-toolkit/agent_toolkit/agents_server_mcp_test.py", "起動種別:"),
        _AllowedLocation("agent-toolkit/agent_toolkit/agents_server_mcp_test.py", "起動種別: explore"),
    ),
    "背景ジョブ": (
        # 監査記録の見出しと対応させる節の見出しと、その節への参照
        _AllowedLocation("*.md", "背景ジョブの起動形（Claude Code）"),
        _AllowedLocation("*.md", "背景ジョブ完了通知と実プロセスの終了順"),
    ),
    "プロジェクト指示": (
        # 監査記録の見出しの引用
        _AllowedLocation("*.md", "プロジェクト指示のAGENTS.md対応"),
        _AllowedLocation("*.md", "プロジェクト指示のCLAUDE.mdアダプター"),
    ),
    "Skillツール": (
        # 計画本文の旧表記を検出する正規表現とその入力例
        _AllowedLocation("agent-toolkit/skills/plan-mode/scripts/check_plan_file.py"),
        _AllowedLocation("agent-toolkit/skills/plan-mode/scripts/check_plan_file_test.py"),
    ),
    "鮮度情報": (
        # `atk wi list --with-staleness`のオプション説明（ヘルプの説明として維持する）
        _AllowedLocation("agent-toolkit/agent_toolkit/atk.py"),
    ),
    "自律実行": (
        # ユーザーの設定から取り除く旧版の文面と一致させる文字列
        _AllowedLocation("pytools/_internal/update_claude_settings.py"),
    ),
    "振り返り担当": (
        # 撤去した担当を説明する設計の記録
        _AllowedLocation("docs/development/design-session-review.md", "振り返り担当"),
    ),
}

_RETIRED_TERMS = (
    _RetiredTerm(
        term="近接検証",
        replacement="変更範囲の検証",
        allowed=(
            # 旧形式の計画を読むための構造定数（`PLAN_LEGACY_*`）
            _AllowedLocation("agent-toolkit/agent_toolkit/_plan/structure/constants.py"),
            # 既存計画の旧名を読み取り互換として受理することを説明する文
            _AllowedLocation("agent-toolkit/skills/plan-mode/references/plan-file-standards.md", "読み取り互換"),
            # 旧形式の計画の基準文書
            _AllowedLocation("agent-toolkit/skills/plan-mode/references/legacy-plan-file-standards.md"),
            # 旧形式の計画を読む互換のテスト
            _AllowedLocation("*_test.py"),
        ),
    ),
    # 範囲のあいまいな人の呼称（2026年10月3日にユーザーがユーザーとエンドユーザーの包含関係を確定して是正）。
    # 置き換え先は`agent-toolkit/skills/writing-standards/references/notation-rules.md`「日本語の表記ルール」を正とする。
    _RetiredTerm(
        term="利用者",
        replacement="ユーザー、エンドユーザー、dotfilesユーザーなど対象を添えた呼称、消費主体、実行中のOSアカウントまたは呼び出し元",
        allowed=(
            # 改称前の計画の列名と素材種別を読む構造定数と読取処理
            _AllowedLocation("agent-toolkit/agent_toolkit/_plan/structure/constants.py"),
            _AllowedLocation("agent-toolkit/agent_toolkit/_plan/structure/sections.py", "利用者"),
            # 旧形式の計画を作成する試験入力と、その読み取り互換を確かめるテスト
            _AllowedLocation("agent-toolkit/agent_toolkit/_plan/fixture.py", "利用者合意"),
            _AllowedLocation("*_test.py", "利用者合意"),
            _AllowedLocation("*_test.py", "利用者指示"),
            _AllowedLocation("*_test.py", "利用者と入口"),
            # 旧形式の計画の基準文書
            _AllowedLocation("agent-toolkit/skills/plan-mode/references/legacy-plan-file-standards.md", "利用者と入口"),
            # 旧列名を構造の名前として扱う文体検査
            _AllowedLocation("scripts/check_agent_doc_tone.py", "利用者と入口"),
            # 呼称の使用停止を定める規定と、その方針記録
            _AllowedLocation("agent-toolkit/skills/writing-standards/references/notation-rules.md", "新しい説明文に使わない"),
            _AllowedLocation("docs/development/concepts-principles.md", "新しい説明文に使わず"),
            _AllowedLocation(
                "README.md",
                line_exact=(
                    "- [docs/guide/index.md](docs/guide/index.md): "
                    "利用者向け（Claude Code/Codex設定・pytools・SSH・セキュリティ）"
                ),
            ),
            _AllowedLocation("docs/index.md", line_exact="- [docs/guide/index.md](guide/index.md): 利用者向け"),
            _AllowedLocation("docs/guide/index.md", line_exact="# 利用者向けガイド"),
            _AllowedLocation(
                ".chezmoi-source/dot_claude/skills/ak110-projects-operations/references/doc-structure.md",
                line_exact=(
                    "配置する場合は見出しを「利用者向けガイド」で揃える。"
                    "docs/development/index.mdの見出しは「開発者向けガイド」で揃える。"
                ),
            ),
            _AllowedLocation(
                "docs/development/concepts-principles.md",
                line_exact=(
                    "- 利用者向け文書の誤記または適用対象のスタイル違反は"
                    "実害ありとして必ず是正し、低頻度リスクの費用比較から除外する"
                ),
            ),
            # 登録した語の不在を確かめる本テスト
            _AllowedLocation("retired_terms_invariant_test.py"),
        ),
    ),
    # 計画実装型AWIと計画化の手順は後継なし（2026年9月13日に廃止）。
    *(
        _RetiredTerm(
            term=term,
            replacement="なし（2026年9月13日に廃止）",
            allowed=(
                # 日付の付いた過去の障害記録
                _AllowedLocation("docs/development/incidents-*.md"),
                # 廃止を決めたユーザーの方針記録
                _AllowedLocation("docs/development/concepts-principles.md"),
                # 廃止を記す方針記録の行
                _AllowedLocation("docs/development/concepts-workflows.md", "2026年9月13日のユーザー指示で廃止した"),
                # 撤去の不在を確かめるテスト
                _AllowedLocation("*_test.py"),
            ),
        )
        for term in ("計画実装型", "convert-to-plan")
    ),
    # `atk plans migrate`による旧保存先からの計画移行は後継なし（2026年9月13日に撤去）。
    *(
        _RetiredTerm(
            term=term,
            replacement="なし（2026年9月13日に撤去）",
            allowed=(
                # 撤去の不在を確かめるテスト
                _AllowedLocation("*_test.py"),
            ),
        )
        for term in ("plans migrate", "migrate_plans", "旧保存先")
    ),
    # 正式名を持つ対象を指していた定義の無い呼称（2026年10月2日に是正）。コミットメッセージには残り続けるため登録する。
    *(
        _RetiredTerm(
            term=term,
            replacement=replacement,
            allowed=(
                # 登録した語の不在を確かめる本テスト
                _AllowedLocation("retired_terms_invariant_test.py"),
                *extra_allowed,
            ),
        )
        for term, replacement, extra_allowed in (
            ("確認スキル", "`agent-toolkit:user-confirmation-and-report`", ()),
            ("委譲スキル", "`agent-toolkit:delegation`", ()),
            ("工程スキル", "`agent-toolkit:delegation`以外のスキルなど、指す範囲を説明する句", ()),
            ("自律終了スキル", "`atk agents-exit-session`の起動記録", ()),
            ("終了スキル", "`atk agents-exit-session`、または終了工程", ()),
            (
                "規範スキル",
                "受信者が適用する作成・レビューの規範を定めるスキルなど、指す範囲を説明する句",
                # 文章作法の悪い例として示す例文
                (
                    _AllowedLocation(
                        ".chezmoi-source/dot_gemini/antigravity-cli/skills/japanese-tech-writing/SKILL.md", "悪い例"
                    ),
                ),
            ),
            ("実行レビュー証拠", "実行レビューの入力`完成条件証拠`", ()),
        )
    ),
    # 規範を横断して使う定義の無い名前と別名（2026年10月2日にユーザーが命名の方針を確定して是正）。
    # 置き換え先と定義は`agent-toolkit/skills/writing-standards/references/defined-names.md`を正とする。
    # 日付付きの経緯記録は当時の名前のまま残す。
    *(
        _RetiredTerm(
            term=term,
            replacement=replacement,
            allowed=(
                # 日付付きの経緯記録
                _AllowedLocation("docs/development/concepts*.md"),
                _AllowedLocation("docs/development/incidents*.md"),
                _AllowedLocation("docs/development/audit-records.md"),
                # 登録した語の不在を確かめる本テスト
                _AllowedLocation("retired_terms_invariant_test.py"),
                *_NAMING_EXTRA_ALLOWED.get(term, ()),
            ),
        )
        for term, replacement in (
            # 工程・レビュー・検証・判定・担当・主体の名前
            ("統合担当", "レーン担当（文脈により「統合を行うレーン担当」）"),
            ("レーン統合担当", "レーン担当"),
            ("実装工程", "実行工程"),
            ("統合後検証", "全体検証"),
            ("実装レビュー", "実行レビュー（plans.pyの「独立CI実装レビュー表」は「CI対応レビュー指摘管理表」）"),
            ("起草担当", "「本文を起草する主体」（WI投入の文脈はWI投入担当）"),
            ("独立調査", "「手順1の調査」"),
            ("是正担当", "「別の委譲先」"),
            ("両レビュー", "「実行レビューとユーザビリティレビュー」"),
            ("軽量レビュー", "一括置換後レビュー"),
            ("差分レビュー", "一括置換後レビュー（担当名は一括置換後レビュー担当。bulk-replace-review.*.mdの題名を含む）"),
            ("単一実行レビュー", "「競合解消後の実行レビュー（1回）」"),
            ("検証担当", "「独立文脈レビューによる問い直し」"),
            ("選定検証", "「pickerの出力の検収」"),
            ("汎用判定", "「プッシュ済み判定」"),
            ("準備工程", "「計画作成の手順1」または「`実装開始`の受領後」"),
            ("更新段階", "「ウォームアップを呼び出した更新処理」"),
            ("共有判定器", "`stop_gate.py`の`is_pending_async_work`"),
            ("共有判定", "`stop_gate.py`の`is_pending_async_work`"),
            ("委譲主体", "委譲元"),
            ("最上位主体", "「最上位セッションのメイン」"),
            ("自律実行主体", "「自律モードで動く主体」"),
            ("自律実行", "自律モード"),
            ("既存担当", "元担当"),
            ("固有終端工程", "「プロジェクト固有の公開後の操作」"),
            ("固有工程", "「プロジェクト固有の公開後の操作」"),
            ("固有の終端順序", "「プロジェクト固有の公開後の操作の順序」"),
            ("独立レビュー", "独立文脈レビュー"),
            ("通常完了の返却形式", "入力名`通常完了の報告様式`"),
            ("英語検知通知", "「`response_language_check`の通知」"),
            ("投入前チェック", "「`agent-toolkit:wi-standards`「投入と取得」手順1の読み直し」"),
            ("レビュー調整", "「`share/review-loop-coordination.md`の手順」"),
            ("調整手順", "「`share/review-loop-coordination.md`の手順」"),
            ("読者ごとの探索担当", "読者別探索担当"),
            ("振り返り担当", "現行の主体（メイン）"),
            ("Challenger", "前提を疑う観点"),
            ("処理回", "process-wiの1回の実行"),
            ("分岐検証", "「`references/testing.md`「分岐条件の効果の裏付け」による検証」"),
            ("成立確認", "「外部の既存挙動の再現観測（wi-standards「通常AWIの本文」）」"),
            ("非再現時の判断", "外部の既存挙動の再現観測が再現しない場合の扱い"),
            ("計画実装", "「実行」工程または`exec.subagent.md`の手順"),
            ("計画レビュー", "実行レビュー（計画のレビュー工程は廃止）"),
            ("調整担当", "`share/review-loop-coordination.md`の手順を行う主体"),
            ("調整主体", "`share/review-loop-coordination.md`の手順を行う主体"),
            ("固有の終端工程", "プロジェクト固有の公開後の操作"),
            # 文書・規範・契約・条件の名前
            ("規範文書", "エージェント向け文書"),
            ("コーディングエージェント向け文書", "エージェント向け文書"),
            ("規定文書", "エージェント向け文書"),
            ("プロジェクト指示", "プロジェクト規範またはプロジェクト方針"),
            ("共有規範", "常時規範"),
            ("常設規範", "常時規範"),
            ("常駐規範", "常時規範"),
            ("執筆規範", "作成規範"),
            ("違反契約", "「違反した規定」"),
            ("計画契約", "「対応する計画」"),
            ("一般継続契約", "「runtime-routing.mdが定める継続の規定」"),
            ("変更契約", "「変更した契約」"),
            ("実行時固有契約", "「実行ホスト別の参照資料の規定」"),
            ("失敗契約", "「失敗した検証が確かめる契約」"),
            ("CI失敗契約", "「同スキルのreferences/ci-failure-handling.md」"),
            ("回答欄契約", "「references/uwi-format.mdが定める質問と回答欄の書式」"),
            ("質問・回答欄契約", "「references/uwi-format.mdが定める質問と回答欄の書式」"),
            ("外部可視結果", "外部可視の結果"),
            ("履歴契約", "「history-rewrite.mdの規定」"),
            ("履歴書換え契約", "「history-rewrite.mdの規定」"),
            ("原因分析契約", "「agent-toolkit:bugfixのreferences/root-cause-analysis.mdの条件」"),
            ("延期`adopt`契約", "「lane-integration.subagent.mdの延期adoptの条件」"),
            ("本文契約", "「agent-toolkit:wi-standards「通常AWIの本文」の要件」"),
            ("process-wi契約", "「agent-toolkit:process-wiの規定の読み替え」"),
            ("分割規範", "「share/rules-main.codex.md「Codex固有の入出力」の分割取得の規定」"),
            ("公開契約基準", "説明へ"),
            ("導入目的の記録", "導入目的の調査記録"),
            # 表・記録・報告の名前
            ("永続表", "レビュー指摘管理表"),
            ("独立CI実行レビュー表", "CI対応レビュー指摘管理表"),
            ("CI実行レビュー表", "CI対応レビュー指摘管理表"),
            ("独立CI実装レビュー表", "CI対応レビュー指摘管理表"),
            ("会話記録", "セッション記録"),
            ("親記録", "メイン記録"),
            ("子記録", "サブエージェント記録"),
            ("旧証拠", "「前回の完成条件証拠」"),
            ("原因欄", "「`直接的原因`行」"),
            ("実施記録", "検証記録"),
            ("引継ぎ記録", "引き継ぎ記録"),
            ("固定報告", "「`agent-toolkit:completion-report`の完了報告」"),
            ("固定完了報告", "「`agent-toolkit:completion-report`の完了報告」"),
            ("品質想起通知", "`QUALITY_CHECKPOINT_NOTICE`"),
            ("完了値", "返却値"),
            ("固定された返却値", "返却値"),
            ("背景移行通知", "「バックグラウンドタスクへの移行通知」"),
            ("履歴文書", "経緯記録"),
            ("証拠行", "`完成条件証拠`とその要素名"),
            ("証拠JSON", "`完成条件証拠`"),
            ("問題候補の一覧", "`candidates.md`"),
            ("阻害要因", "「続行できない理由」"),
            ("阻害条件", "「続行できない理由」"),
            ("背景作業", "バックグラウンドタスク"),
            ("背景ジョブ", "バックグラウンドタスク"),
            ("背景タスク", "バックグラウンドタスク"),
            # 領域・root・リポジトリ・状態の名前
            ("管理対象一時領域", "managed-temp"),
            ("管理対象領域", "managed-temp"),
            ("作業用一時領域", "managed-temp"),
            ("管理対象一時ファイル", "「managed-tempの中のファイル」"),
            ("管理対象一時複製", "「managed-tempへの複製」"),
            ("管理root", "「managed-tempのroot」"),
            ("セッション領域", "セッションのmanaged-temp"),
            ("子領域", "「managed-temp直下の作業ディレクトリ」"),
            ("書込領域", "「書き込むファイルと節」"),
            ("担当領域", "「書き込むファイルと節」"),
            ("作業root", "`~/.claude/plans`"),
            ("計画作業root", "`~/.claude/plans`"),
            ("作業計画root", "`~/.claude/plans`"),
            ("保存root", "`private-notes/plans/`"),
            ("資源root", "「`<役割名>.parent.md`と`<役割名>.subagent.md`を読み込むplugin root」"),
            ("plugin資源root", "plugin root"),
            ("候補root", "「plugin rootの候補」"),
            ("専用root", "`agent-toolkit-codex/`"),
            ("Codex専用root", "`agent-toolkit-codex/`"),
            ("会話root", "ルートsession"),
            ("Git共通領域", "「Git共通ディレクトリ（`--git-common-dir`）」"),
            ("Git共通dir", "「Git共通ディレクトリ（`--git-common-dir`）」"),
            ("Git管理領域", "「Gitディレクトリ（`--git-dir`）」"),
            ("変更前状態", "更新前状態"),
            ("キュー管理リポジトリ", "private-notes"),
            ("作業対象リポジトリ", "対象リポジトリ"),
            # 委譲の名前
            ("起動文", "委譲プロンプト"),
            ("起動プロンプト", "委譲プロンプト"),
            ("能動送付", "「`SendMessage`での送信」"),
            ("起動API", "`agents_server`の`start`"),
            ("役割種別", "`mode`"),
            ("起動種別", "`mode`"),
            ("固定指示", "`share/agents-server-*.md`のファイル名"),
            ("呼び元用文書", "`<役割名>.parent.md`"),
            ("親文書", "`<役割名>.parent.md`"),
            ("親用文書", "`<役割名>.parent.md`"),
            ("parent文書", "`<役割名>.parent.md`"),
            ("タスク文書", "`<役割名>.subagent.md`"),
            ("受信者タスク文書", "`<役割名>.subagent.md`"),
            ("受信者用文書", "`<役割名>.subagent.md`"),
            ("呼び先用文書", "`<役割名>.subagent.md`"),
            ("受信側文書", "`<役割名>.subagent.md`"),
            ("委譲先タスク文書", "`<役割名>.subagent.md`"),
            ("担当タスク文書", "`<役割名>.subagent.md`"),
            ("タスク指示文書", "`<役割名>.subagent.md`"),
            ("委譲文書", "`<役割名>.parent.md`と`<役割名>.subagent.md`"),
            ("親子文書", "`<役割名>.parent.md`と`<役割名>.subagent.md`"),
            ("指示文書ルート", "「起点の文書（`SKILL.md`、agent定義または`<役割名>.subagent.md`）」"),
            ("固定タスク契約", "「`<役割名>.subagent.md`が定める権限と入力」"),
            ("タスク文書起動", "`<役割名>.subagent.md`を指定する起動"),
            # WIと計画の処理の名前
            ("固定集合", "処理対象WI"),
            ("選定済み集合", "処理対象WI"),
            ("固定済み選定集合", "処理対象WI"),
            ("固定済みの選定結果", "処理対象WI"),
            ("追加集合", "「追加した処理対象WI」"),
            ("起点OID", "開始時のHEAD"),
            ("作業開始HEAD", "開始時のHEAD"),
            ("開始時HEAD", "開始時のHEAD"),
            ("処理開始OID", "開始時のHEAD"),
            ("起点commitOID", "`修正系列の開始時のHEAD`"),
            ("直接実装項目", "直接実装対象"),
            ("ペア命名規則", "ファイル名規則"),
            ("検体", "テストコード"),
            ("直下環境", "説明へ"),
            ("初期候補", "開始時候補"),
            ("初回選定の候補", "開始時候補"),
            ("基準コミット", "ベースコミット"),
            ("編集主体", "「変更を確定した主体」"),
            ("手動起動のセッション", "まとめ処理型"),
            ("バグ計画", "計画ファイル（バグ）"),
            # 識別子がある対象の名前
            ("Skill機能", "`agent-toolkit:<スキル名>`を起動する形"),
            ("Skillツール", "`agent-toolkit:<スキル名>`を起動する形、またはツール名`Skill`"),
            ("常駐処理", "process-loop（`atk wi process-loop`）"),
            ("抽出器", "`atk run-script session-review-evidence`"),
            ("準備スクリプト", "`atk run-script session-review-prepare`"),
            ("証拠を確認するコマンド", "`atk run-script exec-review-evidence-check`"),
            ("上流投入情報", "選定結果の欄`上流投入`"),
            ("バリデーター", "`atk run-script review-contract`"),
            ("pendingの取得", "`atk review-audit pending`"),
            ("管理CLI", "コマンド名"),
            ("リモート補助処理", "`atk_serve_*_remote_helper.py`"),
            ("スキル補助処理", "ファイル名"),
            ("計画ファイル基準", "`plan-file-standards.md`"),
            ("鮮度情報", "選定結果の欄`鮮度`または`atk wi list`の`staleness`"),
            ("境界標識", "`atk-auto`"),
            ("AWI処理スキル", "`agent-toolkit:process-wi`"),
            ("認識合わせスキル", "`agent-toolkit:user-confirmation-and-report`「認識合わせ」"),
            ("即時報告", "即時通知（`agent-toolkit/share/rules-subagent.md`「確認事項の即時通知」）"),
            ("編集スキル", "スキル名"),
            ("担当スキル", "個別のスキル名"),
            ("外部依存未達", "`dependency-unmet-external`"),
            ("コミット帰属文字列", "`Co-Authored-By:`"),
            ("帰属情報", "`Co-Authored-By:`"),
            ("保存依存", "`depends_on`"),
            ("口調の対比集", "ファイル名"),
            ("lint緩和の判定", "`lint-relax-criteria.md`"),
            ("選定結果ファイル", "入力名`選定結果の出力先ファイル`"),
            ("固定出力ファイル", "入力名`選定結果の出力先ファイル`"),
            ("直前push OID", "なし（入力`直前にpushしたcommit`ごと撤去）"),
            ("managed設定", "説明へ"),
            ("従来の取得と判定", "説明へ"),
        )
    ),
)


def _find_violations(repo_root: pathlib.Path, terms: tuple[_RetiredTerm, ...] = _RETIRED_TERMS) -> list[str]:
    """許容箇所の外に現れた撤去語を`<パス>:<行番号>`付きのメッセージで返す。"""
    violations: list[str] = []
    for retired in terms:
        proc = subprocess.run(
            ["git", "-C", str(repo_root), "grep", "-n", "-I", "-F", "-z", "--no-color", "--full-name", "-e", retired.term],
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
        # git grepは一致が無い場合に終了コード1を返す。
        if proc.returncode == 1:
            continue
        assert proc.returncode == 0, proc.stderr
        for record in proc.stdout.splitlines():
            path, line_number, line = record.split("\0", 2)
            if any(location.allows(path, line) for location in retired.allowed):
                continue
            violations.append(
                f"{path}:{line_number}: 撤去した名称「{retired.term}」が残っている。正式名「{retired.replacement}」へ置き換える"
            )
    return violations


def _init_repo(root: pathlib.Path, files: dict[str, str]) -> None:
    """一時ディレクトリへファイルを置き、Gitの追跡対象へ加える。"""
    subprocess.run(["git", "init", "-q", str(root)], check=True, capture_output=True, text=True, encoding="utf-8")
    for relative, content in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", "--", *files], check=True, capture_output=True, text=True, encoding="utf-8")


def test_retired_terms_absent_outside_allowed_locations() -> None:
    """リポジトリのGit追跡ファイルに、許容箇所の外の撤去語が無い。"""
    violations = _find_violations(_ROOT)
    assert not violations, "\n".join(violations)


def test_reader_documents_do_not_use_end_user_term() -> None:
    """成果物を使う人が読む案内に内部の役割名を置かない。"""
    files = [_ROOT / "README.md", _ROOT / "docs/index.md", *(_ROOT / "docs/guide").rglob("*.md")]
    violations = [str(path.relative_to(_ROOT)) for path in files if "エンドユーザー" in path.read_text(encoding="utf-8")]
    assert not violations, violations


def test_retired_term_outside_allowed_location_is_reported(tmp_path: pathlib.Path) -> None:
    """許容箇所の外と、行条件を満たさない許容ファイルの行を、パス・行番号・正式名つきで報告する。"""
    _init_repo(
        tmp_path,
        {
            ".claude/skills/example/SKILL.md": "# 例\n\nレーンの近接検証へテストを含める。\n",
            "agent-toolkit/skills/plan-mode/references/plan-file-standards.md": "近接検証の1行表を使う。\n",
        },
    )

    violations = _find_violations(tmp_path)

    assert violations == [
        ".claude/skills/example/SKILL.md:3: 撤去した名称「近接検証」が残っている。正式名「変更範囲の検証」へ置き換える",
        "agent-toolkit/skills/plan-mode/references/plan-file-standards.md:1: "
        "撤去した名称「近接検証」が残っている。正式名「変更範囲の検証」へ置き換える",
    ]


def test_retired_term_in_allowed_locations_is_accepted(tmp_path: pathlib.Path) -> None:
    """読み取り互換を定める許容箇所だけに撤去語が残る場合は違反としない。"""
    _init_repo(
        tmp_path,
        {
            "agent-toolkit/agent_toolkit/_plan/structure/constants.py": 'LEGACY = ("近接検証",)\n',
            "agent-toolkit/skills/plan-mode/references/plan-file-standards.md": (
                "旧名「近接検証」の表は読み取り互換で受理する。\n"
            ),
            "agent-toolkit/skills/plan-mode/references/legacy-plan-file-standards.md": "- `近接検証`列\n",
            "agent-toolkit/agent_toolkit/_plan/structure/parsing_test.py": 'LEGACY = "近接検証"\n',
        },
    )

    assert not _find_violations(tmp_path)


@pytest.mark.parametrize(
    ("relative", "body", "rejected"),
    [
        ("docs/development/design-review-evidence.md", "過去の標識「証拠行」を読む。\n", False),
        ("docs/development/design-review-evidence.md", "証拠行を新たに記す。\n", True),
        ("docs/development/design-review-evidence.md", "振り返り担当を起動する。\n", True),
        ("docs/development/design-session-review.md", "撤去した振り返り担当の経緯。\n", False),
        ("docs/development/design-session-review.md", "起草担当を追加する。\n", True),
        ("docs/development/design-cli.md", "標識「証拠行」を読む。\n", True),
        ("docs/development/design.md", "標識「証拠行」を読む。\n", True),
        ("docs/development/design.md", "振り返り担当の経緯。\n", True),
    ],
)
def test_moved_design_records_preserve_allowed_line_scope(
    tmp_path: pathlib.Path, relative: str, body: str, rejected: bool
) -> None:
    """歴史説明の移動先でも、行条件と語ごとの許容を保ち、索引への再使用を拒否する。"""
    _init_repo(tmp_path, {relative: body})

    violations = _find_violations(tmp_path)

    assert bool(violations) == rejected
    if rejected:
        assert len(violations) == 1
        assert violations[0].startswith(f"{relative}:1:")
