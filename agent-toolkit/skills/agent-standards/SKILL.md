---
name: agent-standards
description: >
  コーディングエージェント向け文書（`AGENTS.md`・`CLAUDE.md`・`.agents/`配下・
  `.claude/rules/`・`.claude/skills/`・hooks関連ファイルなど）の
  新規作成・修正・計画・レビュー時に`agent-toolkit:writing-standards`と必ず併用して呼び出す。
# 編集時の注意点: 本スキルはwriting-standardsとの併用前提の補完スキルで単独では不足する。
---

# コーディングエージェント向け文書品質

コーディングエージェント向け文書固有の記述原則。
一般ドキュメント品質方針は`agent-toolkit:writing-standards`が担当し、本スキルと併用する。

## 適用範囲

対象はコーディングエージェント向け文書一般に共通する記述原則とし、Claude Code環境を前提として書いてよい。
特定実装に固有の機能・制約・ツール名（`AskUserQuestion`・hookなど）も
`## Claude Code固有事項`節への分離なしに本文へ直接書いてよい（他のコーディングエージェントは読み替える前提とする）。

## 共通の記述原則

対象文書はコーディングエージェントが直接読み込むため、人間の読者を想定した説明文・前置き・装飾は書かない。
以下は01-agent.md「品質最優先」原則および03-styles.md「日本語の品質を保つ」原則の具体例であり網羅規定ではないが、
列挙外の場面でも原則に従う。

### 記述の簡潔さ

- コーディングエージェントが既に知っていることや、ツール経由で調べればすぐ分かることは書かない
- 各行について「削除するとコーディングエージェントの判断が一貫しなくなる」と言えなければ削除する
- 同じ趣旨を複数の表現で繰り返さない。ただし文章のリズム・文意の遠近感に寄与する反復・接続は保持する
- 既にコンテキストに読み込み済みと想定されるルールやスキルに対する言及を繰り返さない。
  対象は自動ロードされる`AGENTS.md`等・呼び出し済みスキルの自ファイル間参照・プリロード済みスキルとする
  - 「詳細は～スキルを参照」などの参照誘導は避ける。ただしサブエージェントが独立コンテキストで読む場面
    （スキル本文・サブエージェント定義）は参照先スキル名・節名のパスを明示する
  - 自動ロード対象外の外部ドキュメント（公式ドキュメント・他リポジトリのファイル・未ロードのMarkdownなど）へのリンクは対象外

### 文書サイズ上限

対象はコーディングエージェントが常時または高頻度で本文を直接読み込む文書
（`01-agent.md`・`02-claude-code.md`・各`SKILL.md`・サブエージェント定義・スキル配下の`references/`など）。
作業ごとに作成・廃棄される計画ファイル（`~/.claude/plans/*.md`・`docs/v{next}/plans/*.md`）は本上限の対象外とする。
本文の物理行数は219行以下を許容、220行以上を違反とする。frontmatterおよびYAMLヘッダ行も行数に算入する。
コーディングエージェントによっては文書先頭の一定行数（例: 219行）のみを読み込む実装があり、超過は指示の読み込み漏れを招く。

- 縮減手順・判定基準・縮減根拠4類型は`agent-toolkit:trim-agent-docs`を参照する
- 改訂の計画作成時、または本節対象ファイルが計画の対象ファイル一覧に含まれる場合は、
  現行行数と改訂後見込み行数の両方を`wc -l`で実測する。`cp`で正本をscratchpadへ複製し`Edit`適用後に実測し、
  `## 調査結果`へ現行行数、`## 変更内容`へ再確認手順を記載する
- 現行または改訂後の実測値が219行を超過する場合、追記量にかかわらず既存節縮減を本計画スコープへ組み込み、
  219行以下への収束を実装完了条件に含める。本上限は対象ファイル単体の評価基準であり、
  計画の縮退・分割・先送りの根拠としない
- 計画段階で改訂後の最終形を組み立て、`uvx pyfltr run-for-agent --no-fix`でtextlintの`sentence-length`
  （120字上限）等の違反を検査し解消する。検査対象の設定ファイル・相対パス参照先もscratchpadへ同時コピーする
  （コピー漏れは行幅違反・リンク切れの擬陽性を招く）
- 実装後レビュー（`agent-doc-validator`）の指摘対象は220行以上の実測違反のみとし、
  境界近接（200〜219行）・書きやすさ改善・予防的縮減は指摘対象外とする。
  事前予防は計画段階の役割とし、`agent-toolkit:plan-mode`工程6-1の`wc -l`実測手順で対策を確定する

### メタ記述の禁止

スキル・エージェント・ルールなどコーディングエージェントへ直接読み込まれる文書は、エージェントへの直接指示として書く。
実行判断に寄与しないメタ情報は本文に書かず、frontmatter内コメント（`#`行）に書く。
他スキルとの併用関係・優先順位・適用範囲・呼び出し関係など実行判断に直接寄与する関係性情報、
「本スキル」「本ドキュメント」などの自己言及は本文へ書いてよい。
frontmatterコメントへ書くべきメタ記述の類型と代表例は次の通り。

- 管理方針・編集判断・運用変化・編集者向け同期手順: 集約先・記述取捨選択・過去との変化・反映手順・編集経緯・
  棲み分けを語る言い回し（例:「〜をSSOTとしてまとめている」「以前は〜だった」）

意図的重複・同期要件を新設する場合は、frontmatter同期注記コメントを重複対象の全ファイルへ同時に追加する
（片側のみの追加は同期破綻リスクを残す）。

### 横断指針の配置

複数フェーズ・複数文脈に跨る指針は独立節として手順リスト直後など適用範囲が明確な位置に置く
（特定フェーズ・特定文脈の見出し配下に置くと、見出し階層から限定指針として狭く解釈されるため）。

### 適用範囲・条件の明示

規範を新規作成・改訂する際は、当該規範の適用範囲・除外対象・前提条件を明示する。曖昧なまま規範化しない。

- 複数のディレクトリ・モジュール・配布物が並ぶ文脈では、どの対象に適用しどの対象に適用しないかを明示する
  （配布物と非配布物が混在する場合は配布物境界を明示し、非配布物固有の依存を配布物側へ持ち込まない原則を併記する）
- 親項目が複数の対象を束ねるサブ項目を持つ場合、サブ項目の適用対象が親項目と一致するか・狭まるかを明示する
- 適用範囲は最も広く取れる範囲を初期案とし、限定する場合は根拠を併記する。拡張・縮小の余地がある規範は
  改訂時に範囲表現を再確認する（限定理由を書かないまま放置すると後続の改訂で対象漏れが発生する）
- 規範本文は独立コンテキストで参照されるため、追加文面案に会話文脈依存の指示語（「今回」「先ほど」など）を含めず、
  具体対象を確定できる名詞句で記述する
- 規範文書・フィードバック本文・計画ファイルで判断条件・例外条件・許容条件を新設する場合は、観測可能な事実
  （コマンド終了状態・ファイルの有無・ユーザー合意の要否・対象範囲の重なり等）のみで記述する。自己推定量
  （コンテキスト消費・作業量・所要時間・複雑さ等）は条件に含めない

### サンプル・テンプレート本文の純度

サンプル計画ファイル・テンプレート・記述例コードブロック内には成果物本文に書くべき内容のみを置く。
手引き（メタ説明文）の混入は転記誤読を招くため、手引きはサンプル外（節見出し配下の地の文・別節）に分離する。

### コンテキスト汚染の回避

- 悪い例の記述可否はコンテキスト汚染リスクの有無で判断する。汚染リスクが高い悪い例（生成確率を上げる語彙）は
  `references/`配下の隔離ファイルへ置き、リスクが低い対比例（語順・主述・構成等）は本文に直接書いてよい
- 機械チェック用辞書の検出語そのものをスキル本文・テストコード・hookメッセージへ転記しない（生成確率を上げる逆効果のため）
- `references/`配下の隔離ファイルは危険語彙・禁止パターンを含む悪い例の格納専用とする。メインエージェントから直接読み込ませず、
  確認はExploreサブエージェント・修正は`plan-implementer`経由とし、転記禁止で機械チェック対象からも除外する
  - 新規追加時は既存例（`scope-escalation-phrases.md`等）と同じくスキルの`references/`配下へ配置する。
    pyfltr内蔵`colloquial-check`の除外設定・`pyproject.toml`の`extend-exclude`も同時に整備する

### 既知情報・冗長記述の排除

- 学習データ・システムプロンプト既知の一般指針を書かない原則は「記述の簡潔さ」節と共通する（用語は原語優先で訳語不統一を回避）
- 細則の列挙は上位指針へ集約し、詳細手順はスキルのreferences配下へ退避してトリガー時のみロードする。
  同一概念も複数箇所で長文展開せず用語定義は1箇所に示す（禁止列挙の統合基準は「肯定形優先の記述」節に従う）
- 短い権威表現（用語・規範用語）で長文説明を置き換える場合、独立した第三者のコーディングエージェントが
  用語1語のみから同一行動を取れるかを確認する（行動に影響する規範の置き換えではサブエージェント検証を推奨する）

### 肯定形優先の記述

規範文書を追記・改訂する場合は、実施すべき正規フローの肯定的な記述を優先する。

- 禁止規定を追加する前に、同じ再発防止を肯定形で表現できないか先に試みる
- 新規追加の禁止規定は同一節内で対応する推奨事項を必ず併記する
- 既存禁止規定は改訂時に遡及して推奨事項の併記または肯定形化を検討する
- 変換テンプレは`references/positive-form-templates.md`を参照する

### 目的記述の明示

スキル・規範本文は冒頭で目的（適用判断の核となる動機）を1〜2文で明示する。
本文冒頭またはfrontmatter `description`のいずれかで充足し、手順のみで目的を欠く本文は改訂時に追加する。

## タスク固有で読み込む補足情報

- `references/agent-skills.md`: スキル編集時
- `references/claude-hooks.md`（hook編集時）・`references/auto-mode.md`（auto mode編集時）: Claude Code固有

## サブエージェント連携の設計

サブエージェント（Agentツール・forkスキル等で独立コンテキスト起動される主体）を含む
設計・改訂時の観点は`references/subagent-collaboration.md`に集約する。
起動プロンプトの必要情報網羅・独立コンテキスト前提・規範スキル明示等の各項目を規定する。

## Claude Code固有事項

本節はClaude Code固有の機能・制約に依存する記述を扱う（「適用範囲」節の前提に従い読み替える）。

### メカニズム選択

新規挙動・制約は次の対応で振り分ける。
指示文は確率的にしか従われないため、決定論的強制が必要な制約は地の文で書かずhookで機械的に実装する。

- 常時参照される横断的標準: `CLAUDE.md`・`AGENTS.md`本文、または`.claude/rules/`配下のpaths未指定ルール
- 特定パス・特定モジュール限定の規則: `.claude/rules/`配下のルールファイル（frontmatterで`paths`指定）
- 手順・チェックリスト・段階的ワークフロー: スキル
- 決定論的強制・コマンドブロック・自動lint・通知: hook（不可逆禁止は`permissions`併用）
- 重い調査・冗長出力の隔離: サブエージェント

`.claude/rules/`配下の新規作成・見直し時の評価手順（paths未指定は起動時に常時ロードされる）。

- 常にコンテキストに必要かを判断する。タスク固有で頻度が低いものは`.claude/skills/`配下へ移す
- 特定モジュール・特定ディレクトリ群でのみ参照すれば十分なものは`paths`で対象を限定する
- 全コード・全ドキュメントに横断的に関わるものはfrontmatter無しのまま残す
- 既存ルールへ節を追加する場合、追記内容の適用範囲が`paths`と一致するかを確認し、不一致なら別ルールへ分離するか`paths`を見直す

### 機械可読な識別子のprefix

機械可読な識別子（環境変数・設定キー・セッション状態フラグ等）のprefixは主管コンポーネントの配置レイヤに揃える。

- 配布物（`agent-toolkit/`配下）で完結する識別子は`AGENT_TOOLKIT_`相当、dotfiles個人環境（`.chezmoi-source/`配下等）
  で完結する識別子は`DOTFILES_`相当を用いる。外部実行環境の名前空間（`CLAUDE_`等）は衝突リスクのため用いない

### rules階層のフラット構造

`agent-toolkit/rules/`配下はサブディレクトリを作らずフラット構造を維持する
（自動ロード対象の規範ファイル集約先であり階層化はSkillとの混同を招く）。
サブディレクトリ配置が必要な補助情報は`agent-toolkit/skills/*/references/`配下へ配置する。

### セッション状態フラグ

`agent-toolkit`プラグインのフックはセッション単位の状態ファイルを介する。
パスは`{tempdir}/claude-agent-toolkit-{session_id}.json`とし、PreToolUseとPostToolUse間で情報を共有する。
状態ファイル運用の一般論は`references/claude-hooks.md`「セッション状態ファイル」節を参照する。
本節を`agent-toolkit`が扱う全フラグ一覧のSSOTとし、追加・変更時は書き込み元・読み取り元の対応関係を保って更新する。

- `test_executed`: PostToolUse(Bash)が記録。`git commit`未検証警告の抑制に使う
- `git_status_checked`: PostToolUse(Bash)が`git status`/`git log`/`git diff`観測時に記録
- `git_log_checked`: PostToolUse(Bash)が`git log`観測時に記録。amend/rebase直前確認に使い、
  commit/rebase/push/ファイル編集観測時にリセットする
- `plan_mode_skill_invoked`: PostToolUse(Skill)が`agent-toolkit:plan-mode`呼び出しを記録。
  plan file形式検査の有効化と最初ツール警告の抑制に使う
- `session_review_invoked`: PostToolUse(Skill)が振り返りスキル呼び出しをキーごとに記録する辞書。
  `agent-toolkit:session-review`は配布物、`session-review-dotfiles`は個人フックが記録し、
  各Stop hookの重複抑止とEnterPlanMode時の辞書リセット（配布物担当）に使う
- `agent_toolkit_edit_skill_invoked`: dotfiles個人フックが`agent-toolkit-edit`呼び出しを記録。
  未起動時のPreToolUse警告抑制に使う（書き込み元・読み取り元とも個人フックで配布プラグインは関与しない）
- `session_review_extension_pending`: dotfiles個人フックが`agent-toolkit:*`または`session-review-dotfiles`使用を記録する。
  配布物Stop hook（`stop_advisor.py`）が重複送出抑制に使う（個人フック不在時は未設定のまま従来通り動作する）
- `autonomous_exit_invoked`: dotfiles個人フックが`agent-toolkit:exit-session`呼び出しを記録。
  個人フックStop hookが`DOTFILES_AUTONOMOUS_EXIT_REQUIRED=1`環境下での未呼出判定に使う
  （リセットしない。新規セッションは別`session_id`の状態ファイルで旧フラグを再読み取りしない）
- サブエージェント起動を検知する判定は`tool_name in ("Agent", "Task")`をSSOTとする（pretooluse・posttooluseとも同一）
- 工程7完遂判定フラグ群: `plan_reviewer_invoked` / `naive_executor_invoked` /
  `plan_impl_reviewer_invoked` / `agent_doc_validator_invoked`はPostToolUse(Agent/Task)が記録し、
  `codex_review_invoked`はPostToolUse（Skill呼び出しまたは`mcp__codex__codex`完了）が記録する
  （`agent_doc_validator_invoked`はコーディングエージェント向け文書対象時のみ必須）
- `current_plan_file_path`: PostToolUse(Write/Edit/MultiEdit)が計画ファイル編集時のパスを記録。
  ExitPlanMode時に上記フラグの条件付き必須化判定で計画ファイル本文を再読込するために使う
- 計画ファイル未作成時の直接編集検知フラグ群: `plan_file_written` / `direct_agent_toolkit_edit_count` /
  `last_agent_toolkit_edit_path`はPreToolUse(Write/Edit/MultiEdit)が更新する
  （plan-mode起動後の計画ファイル未作成でのagent-toolkit配下編集連続を検知、2件目でwarn・3件目でblock）

### 公式リファレンス

スキルの新規作成・hook実装など該当する作業では公式マーケットプレイス（`anthropics/claude-plugins-official`）を参照する。
対象は`skill-creator:skill-creator`・`plugin-dev:skill-development`・`plugin-dev:agent-development`とする。
`plugin-dev:hook-development`・`plugin-dev:plugin-structure`も対象とする。
referencesに記載のない仕様や新機能、ユーザー指示との認識相違は以下を`WebFetch`で取得する（`.md`付与でMarkdown形式）。
Memory: `https://code.claude.com/docs/ja/memory.md`。Skills: `https://code.claude.com/docs/ja/skills.md`。
Subagents: `https://code.claude.com/docs/ja/sub-agents.md`。Hooks: `https://code.claude.com/docs/ja/hooks.md`。
Plugins: `https://code.claude.com/docs/ja/plugins.md`と`https://code.claude.com/docs/ja/plugins-reference.md`。
