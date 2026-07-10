---
name: agent-standards
description: >
  コーディングエージェント向け文書（`AGENTS.md`・`CLAUDE.md`・`.agents/`配下・
  `.claude/rules/`・`.claude/skills/`・hooks関連ファイルなど）の
  新規作成・修正・計画・レビュー時に`agent-toolkit:writing-standards`と必ず併用して呼び出す。
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
以下は01-agent.md「品質最優先」・03-styles.md「日本語の品質を保つ」原則の具体例（列挙外の場面でも原則に従う）。

### 記述の簡潔さ

- コーディングエージェントが既に知っていることや、ツール経由で調べればすぐ分かることは書かない
- 各行について「削除するとコーディングエージェントの判断が一貫しなくなる」と言えなければ削除する
- 同じ趣旨を複数の表現で繰り返さない。ただし文章のリズム・文意の遠近感に寄与する反復・接続は保持する
- 既にコンテキストに読み込み済みと想定されるルール・スキル
  （自動ロードされる`AGENTS.md`等・呼び出し済みスキルの自ファイル間参照・プリロード済みスキル）に対する言及を繰り返さない
  - 「詳細は～スキルを参照」などの参照誘導は避ける
    （サブエージェントが独立コンテキストで読む場面は参照先スキル名・節名のパスを明示する）
  - 自動ロード対象外の外部ドキュメント（公式ドキュメント・他リポジトリのファイル等）へのリンクは対象外

### 文書サイズ上限

対象は`AGENTS.md`・`CLAUDE.md`・ルール・各`SKILL.md`・サブエージェント定義・`references/`など、よく読む文書とする。
作業ごとに作成・廃棄される計画ファイル（`~/.claude/plans/*.md`・`docs/v{next}/plans/*.md`）は本上限の対象外。
行数の算入基準・上限を設ける理由は`agent-toolkit:trim-agent-docs`を参照する。

- 本文の物理行数は200行以下を許容し、200行超過時点で計画スコープに縮減を組み込む
- 縮減手順・判定基準・縮減根拠4類型・references/分離判定基準は`agent-toolkit:trim-agent-docs`を参照する
- 対象ファイルは現行と改訂後の`wc -l`実測値を`## 調査結果`へ記載し、
  200行超過時は既存節縮減を本計画スコープへ組み込み200行以下への収束を実装完了条件とする
- 計画段階でscratchpadへ最終形を組み立て`uvx pyfltr run-for-agent --no-fix`で検査する
  （検査対象の設定ファイル・相対パス参照先もscratchpadへ同時コピーする）
- 実装後レビューの指摘対象は200行超過の実測違反のみとし、事前予防は計画段階の`wc -l`実測で行う

### メタ記述の禁止

スキル・エージェント・ルールなどコーディングエージェントへ直接読み込まれる文書は、エージェントへの直接指示として書く。
実行判断に寄与しないメタ情報は本文に書かず、frontmatter内コメント（`#`行）に書く。
他スキルとの併用関係・優先順位・適用範囲・呼び出し関係など実行判断に直接寄与する関係性情報、
「本スキル」「本ドキュメント」などの自己言及は本文へ書いてよい。
frontmatterコメントへは管理方針・編集判断・運用変化・編集者向け同期手順を置く。

意図的重複・同期要件を新設する場合は、frontmatter同期注記コメントを重複対象の全ファイルへ同時に追加する。

### 横断指針の配置

複数フェーズ・複数文脈に跨る指針は独立節として手順リスト直後など適用範囲が明確な位置に置く
（限定見出し配下では狭く解釈されるため）。

### 適用範囲・条件の明示

規範を新規作成・改訂する際は、当該規範の適用範囲・除外対象・前提条件を明示する。曖昧なまま規範化しない。

- 複数のディレクトリ・モジュール・配布物が並ぶ文脈ではどの対象に適用しどの対象に適用しないかを明示する。
  配布物と非配布物が混在する場合は配布物境界を明示し、非配布物固有の依存を配布物側へ持ち込まない原則を併記する。
  親項目が複数の対象を束ねる場合はサブ項目の適用対象が親項目と一致するか・狭まるかも明示する
- 適用範囲は最も広く取れる範囲を初期案とし、限定する場合は根拠を併記する
- 拡張・縮小の余地がある規範は改訂の都度、範囲表現を再点検する（限定根拠の欠落は後続改訂で対象漏れを招く）
- 規範本文は独立コンテキストで参照されるため、会話文脈依存の指示語（「今回」「先ほど」等）や
  自己参照系の指示語（「本規則」「本節」「これ」「上記」「同節」等）を含めず、具体対象を確定できる名詞句で記述する。
  `grep -n`等の操作指示では検索対象・パス・パターンを、条件節の帰結は主語・目的語を明示する
- 判断条件・例外条件・許容条件を新設する場合は観測可能な事実（コマンド終了状態・ファイルの有無・ユーザー合意等）のみで記述する
  （自己推定量（コンテキスト消費・作業量・所要時間・複雑さ等）は条件に含めない）

### サンプル・テンプレート本文の純度

サンプル計画ファイル・テンプレート・記述例コードブロック内には成果物本文に書くべき内容のみを置く
（手引きはサンプル外へ分離する）。

### コンテキスト汚染の回避

- 悪い例の記述可否はコンテキスト汚染リスクの有無で判断する。汚染リスクが高い悪い例（生成確率を上げる語彙）は
  `references/`配下の隔離ファイルへ置き、リスクが低い対比例（語順・主述・構成等）は本文に直接書いてよい
- 機械チェック用辞書の検出語そのものをスキル本文・テストコード・hookメッセージへ転記しない（生成確率を上げる逆効果のため）
- `references/`配下の隔離ファイルは危険語彙・禁止パターンを含む悪い例の格納専用とし、
  メインから直接読み込ませず、確認はExploreサブエージェント・修正は`plan-implementer`経由とする。
  新規追加時はスキル配下へ配置し、pyfltr内蔵`colloquial-check`の除外設定・`pyproject.toml`の`extend-exclude`も同時に整備する
- 規範文書本文で機械検出カテゴリの規範論を扱う場合、検出パターン相当の語句の直接転記を避ける。
  代替は「代表フレーズは`references/scope-escalation-phrases.md`の`{カテゴリ名}`を参照する」形式の参照誘導とする

### 既知情報・冗長記述の排除

- 学習データ・システムプロンプト既知の一般指針を書かない原則は「記述の簡潔さ」節と共通する（用語は原語優先で訳語不統一を回避）
- 細則の列挙は上位指針へ集約し、詳細手順はスキルのreferences配下へ退避してトリガー時のみロードする。
  同一概念も複数箇所で長文展開せず用語定義は1箇所に示す（禁止列挙の統合基準は「肯定形優先の記述」節に従う）。
  参照先へ集約する改訂では移設元には参照のみを置き、移設内容の要約・括弧書き列挙を残さない
- 短い権威表現（用語・規範用語）で長文説明を置き換える場合、独立した第三者が用語1語のみから同一行動を取れるかを確認する
  （行動に影響する規範の置き換えではサブエージェント検証を推奨する）

### 肯定形優先の記述

規範文書を追記・改訂する場合は、実施すべき正規フローの肯定的な記述を優先する。

- 禁止規定を追加する前に、同じ再発防止を肯定形で表現できないか先に試みる
- 新規追加の禁止規定は同一節内で対応する推奨事項を必ず併記する（既存規定も改訂時に遡及して推奨併記または肯定形化を検討する）
- 変換テンプレは`references/positive-form-templates.md`を参照する

### 目的記述の明示

スキル・規範本文は冒頭で目的（適用判断の核となる動機）を本文冒頭またはfrontmatter `description`で1〜2文で明示する。

## タスク固有で読み込む補足情報

- `references/agent-skills.md`: スキル編集時
- `references/claude-hooks.md`（hook編集時）・`references/auto-mode.md`（auto mode編集時）: Claude Code固有

## サブエージェント連携の設計

サブエージェント設計・改訂時の観点は`references/subagent-collaboration.md`に集約する。

## Claude Code固有事項

### メカニズム選択

新規挙動・制約は次の対応で振り分ける。

- 常時参照される横断的標準: `CLAUDE.md`・`AGENTS.md`本文、または`.claude/rules/`配下のpaths未指定ルール
- 特定パス・特定モジュール限定の規則: `.claude/rules/`配下のルールファイル（frontmatterで`paths`指定）
- 手順・チェックリスト・段階的ワークフロー: スキル
- 決定論的強制・コマンドブロック・自動lint・通知: hook（不可逆禁止は`permissions`併用）
- 重い調査・冗長出力の隔離: サブエージェント

`.claude/rules/`配下の評価手順（paths未指定は起動時に常時ロードされる）:

- タスク固有で頻度が低いものは`.claude/skills/`配下へ移す
- 特定モジュール・特定ディレクトリ群でのみ参照すれば十分なものは`paths`で対象を限定する
- 全コード・全ドキュメントに横断的に関わるものはfrontmatter無しのまま残す
- 既存ルールへ節を追加する場合、追記内容の適用範囲が`paths`と一致するかを確認し、不一致なら別ルールへ分離するか`paths`を見直す

### 識別子と環境変数

機械可読な識別子のprefixは配置レイヤに揃え、配布物の環境変数は`AGENT_TOOLKIT_<PURPOSE>`形式で本節にSSOTを置く。
配布物完結は`AGENT_TOOLKIT_`、個人環境完結は`DOTFILES_`。外部名前空間（`CLAUDE_`等）は不採用。

- 環境変数`AGENT_TOOLKIT_PRIVATE_NOTES`: `atk fb`管理repoのroot（既定`~/private-notes/`）
- 環境変数`AGENT_TOOLKIT_PRELINT_TEST_BYPASS`: 事前lint一部無効化（テスト用）
- 環境変数`AGENT_TOOLKIT_STOP_GATE_DEBUG`: `_stop_gate`デバッグ出力

### rules階層のフラット構造

`agent-toolkit/rules/`配下はサブディレクトリを作成せずフラット構造を維持する
（補助情報は`agent-toolkit/skills/*/references/`配下へ置く）。

### セッション状態フラグ

`agent-toolkit`プラグインは状態ファイル`{tempdir}/claude-agent-toolkit-{session_id}.json`を本節SSOTとして共有する。
運用の一般論は`references/claude-hooks.md`「セッション状態ファイル」節を参照し、書き込み元・読み取り元の対応を保って更新する。

- `test_executed`: PostToolUse(Bash)が記録。`git commit`未検証警告の抑制に使う
- `git_status_checked`: PostToolUse(Bash)が`git status`/`git log`/`git diff`観測時に記録
- `git_log_checked`: PostToolUse(Bash)が`git log`観測時に記録。commit/rebase/push/編集時リセット、amend/rebase前確認に使う
- `plan_mode_skill_invoked`: PostToolUse（Skill）／UserPromptSubmit（スラッシュ）で`agent-toolkit:plan-mode`呼び出しを記録。
  plan file検査と最初ツール警告抑制に使う
- `session_review_invoked`: PostToolUse（Skill）／UserPromptSubmit（スラッシュ）で振り返りスキルを記録。
  対象は配布物`agent-toolkit:session-review`と個人フック`session-review-dotfiles`。
  Stop hook重複抑止・辞書リセットに使う
- `agent_toolkit_edit_skill_invoked`: dotfiles個人フックが`agent-toolkit-edit`呼び出しを記録。
  未起動時のPreToolUse警告抑制と配布物`pretooluse.py`のRead隔離ブロックの例外判定に使う
- dotfiles個人フック管理:
  - `session_review_extension_pending`: `session-review-dotfiles`使用を記録（配布物Stop hookの重複送出抑制）
  - `autonomous_exit_invoked`: `agent-toolkit:exit-session`呼び出しを記録。
    個人フックStop hookが`DOTFILES_AUTONOMOUS_EXIT_REQUIRED=1`環境下での未呼出判定に使う
- `apply_feedback_skill_invoked` / `process_feedbacks_skill_invoked`:
  PostToolUse（Skill）／UserPromptSubmit（スラッシュ）で該当を記録。
  対象は`agent-toolkit:apply-feedback`・`agent-toolkit:process-feedbacks`・各短縮スラッシュ。
  Stop hookの拡張照合カテゴリ有効化判定に使う
- サブエージェント起動を検知する判定は`tool_name in ("Agent", "Task")`をSSOTとする
  （pretooluse・posttooluseとも同一。コード追加・改訂時は`grep -rn`で確認して同一集合を使う）
- 工程7完遂判定フラグ群はPostToolUse(Agent/Task)が記録する: `plan_reviewer_invoked`・`naive_executor_invoked`・
  `plan_impl_reviewer_invoked`・`agent_doc_validator_invoked`（末尾のみエージェント向け文書対象時に必須）
  `codex_review_invoked`はPostToolUse（Skill、または`codex_impl_invoked`未設定時の`mcp__codex__codex`完了）
  ／UserPromptSubmit（`/plan-codex-review`）が記録する
- `codex_impl_invoked`: PostToolUse（Skill）／UserPromptSubmit（スラッシュ）で`codex-impl`呼び出しを記録。
  実装用途`mcp__codex__codex`の許可判定に使う
- `current_plan_file_path`: PostToolUse(Write/Edit/MultiEdit)が計画ファイル編集時のパスを記録。ExitPlanMode時の再読込に使う
- 計画ファイル未作成時の直接編集検知フラグ群は`plan_file_written`／`direct_agent_toolkit_edit_count`／
  `last_agent_toolkit_edit_path`。PreToolUseが更新し、agent-toolkit配下編集連続を検知（2件目でwarn・3件目でblock）

### 公式リファレンス

スキルの新規作成・hook実装では公式マーケットプレイス（`anthropics/claude-plugins-official`）を参照する。
対象は`skill-creator:skill-creator`と`plugin-dev`の
`skill-development`・`agent-development`・`hook-development`・`plugin-structure`。
referencesに記載のない仕様や認識相違は`https://code.claude.com/docs/ja/`配下を`WebFetch`で取得する。
参照対象は`memory.md`・`skills.md`・`sub-agents.md`・`hooks.md`・`plugins.md`・`plugins-reference.md`。
