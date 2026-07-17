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
以下は01-agent.md「品質最優先」・04-styles.md「日本語の品質を保つ」原則の具体例（列挙外の場面でも原則に従う）。

### 記述の簡潔さ

- コーディングエージェントが既に知っていることや、ツール経由で調べればすぐ分かることは書かない
- 各行について「削除するとコーディングエージェントの判断が一貫しなくなる」と言えなければ削除する
- 同じ趣旨を複数の表現で繰り返さない。ただし文章のリズム・文意の遠近感に寄与する反復・接続は保持する
- 既にコンテキストに読み込み済みと想定されるルール・スキル
  （自動ロードされる`AGENTS.md`等・呼び出し済みスキルの自ファイル間参照・プリロード済みスキル）に対する言及を繰り返さない
  - 「詳細は～スキルを参照」などの参照誘導は避ける
    （サブエージェントが独立コンテキストで読む場面は参照先スキル名・節名のパスを明示する）
  - 自動ロード対象外の外部ドキュメント（公式ドキュメント・他リポジトリのファイル等）へのリンクは対象外

### 不可逆操作の呼び出し集約

セッション終了・外部送信・削除など不可逆な副作用を持つスキルへの遷移指示は、単一の呼び出し元スキルへ集約する。
起動経路が複数あるスキル（ユーザー手動起動・フック起動等）へ分散させると、意図と無関係に不可逆操作が誘発される。

- 不可逆操作スキル側は呼び出し元を実名列挙する形で`description`・起動条件節へ明記し、列挙外からの遷移指示を追加しない
- 不可逆操作スキルへの遷移が必要な場合は既存の集約先スキルへ委譲し、
  振り返り・報告など不可逆操作を本来伴わないスキルの末尾へ完了ついでの遷移指示を追加しない

### 文書サイズ上限

対象は`AGENTS.md`・`CLAUDE.md`・ルール・各`SKILL.md`・サブエージェント定義・`references/`など、よく読む文書とする。
作業ごとに作成・廃棄される計画ファイル（`~/.claude/plans/*.md`・`docs/v{next}/plans/*.md`）は本上限の対象外。
行数の算入基準・上限を設ける理由は`agent-toolkit:trim-agent-docs`を参照する。

- 本文の物理行数は200行を目安とし、220行超過時点で計画スコープに縮減を組み込む
- 縮減手順・判定基準・縮減根拠4類型・references/分離判定基準は`agent-toolkit:trim-agent-docs`を参照する
- 対象ファイルは現行と改訂後の`wc -l`実測値を`## 調査結果`へ記載し、220行超過時は既存節縮減を本計画スコープへ組み込み220行以下への収束を実装完了条件とする
- 最終形の文面検査は計画本文への書き込み後チェックと、実装後の対象ファイルへの`uvx pyfltr run-for-agent`で代替する
- 実装後レビューの指摘対象は220行超過の実測違反のみとし、事前予防は計画段階の`wc -l`実測で行う。
  計画段階のレビューでは220行以下の収束幅の細部（数行単位の超過）を指摘対象に含めない
- 200行と220行の使い分けの根拠は次のとおり。220行は実装後レビュー時のハードリミットとし、実装後220行超過のみを違反として扱う。
  200行は計画段階の目安とし、200-220行の帯は許容バッファとみなす。計画時の見込み行数の厳密なカウントは求めない
  （見込み行数の細部算出は時間的コストが高く、実装後220行以下であれば直ちに問題としないため）

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
- 機械チェック用辞書の検出語そのものをスキル本文・テストコードへ転記しない（生成確率を上げる逆効果のため）。
  hookのブロックメッセージは本規定の例外とする。ブロックの原因となったマッチ文言を利用者が特定できる形で本文に含めてよい
  （原因不明のブロックが利用者にとってのクイズ状態を招く事象の回避を優先する）
- `references/`配下の隔離ファイルは危険語彙・禁止パターンを含む悪い例の格納専用とする。
  検出パターン仕様の説明文（`scope-escalation-phrases.md`本文等）はメイン・Exploreサブエージェント
  双方のRead参照を許容する。
  検出パターンの生の羅列（`_scope_escalation_test_inputs.txt`等のテストデータ）は隔離を保つ。
  確認はExploreサブエージェント経由、修正はclaudeサブエージェント経由に限る（`plan-implementer`は指名しない）。
  新規追加時はスキル配下へ配置し、pyfltr内蔵`colloquial-check`の除外設定・`pyproject.toml`の`extend-exclude`も同時に整備する
- 規範文書本文で機械検出カテゴリの規範論を扱う場合、検出パターン相当の語句の直接転記を避ける。
  代替は「代表フレーズは`references/scope-escalation-phrases.md`の`{カテゴリ名}`を参照する」形式の参照誘導とする。
  加えて意味を保った言い換え（同義動詞への置換・上位概念名詞への抽象化・観測事象記述への書き換え）を許容する

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
- 規範改訂時は禁止列挙の追記だけでなく、同一命題を反復する既存列挙への統合・圧縮可否を必ず点検する。
  同一命題が複数の禁止バレットへ分散している場合は単一の肯定原則へ集約し、各バレットは具体例として圧縮する
- 変換テンプレは`references/positive-form-templates.md`を参照する

### 目的記述の明示

スキル・規範本文は冒頭で目的（適用判断の核となる動機）を本文冒頭で1〜2文で明示する。
frontmatter `description`には目的記述を書かず、対象作業と発火条件のみを書く（詳細は`references/agent-skills.md`「YAML frontmatter」節）。

## タスク固有で読み込む補足情報

- `references/agent-skills.md`: スキル編集時
- `references/check-script-design.md`: 機械チェックスクリプト新設・改修時
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

- 環境変数`AGENT_TOOLKIT_PRIVATE_NOTES`（`atk fb`管理repoのroot、既定`~/private-notes/`）・
  `AGENT_TOOLKIT_STOP_GATE_DEBUG`（`_stop_gate`デバッグ出力）

### rules階層のフラット構造

`agent-toolkit/rules/`・`agent-toolkit/agents/`配下はサブディレクトリを作成せずフラット構造を維持する（サブディレクトリ内の`.md`が誤検出を招く回避策）。補助情報はrules用が`agent-toolkit/skills/*/references/`配下、agents用が`agent-toolkit/references/<用途名>/`配下とする。

### セッション状態フラグ

`agent-toolkit`プラグインは状態ファイル`{tempdir}/claude-agent-toolkit-{session_id}.json`を本節SSOTとして共有する。
運用の一般論は`references/claude-hooks.md`「セッション状態ファイル」節を参照し、書き込み元・読み取り元の対応を保って更新する。

- `test_executed`: PostToolUse(Bash)が記録。`git commit`未検証警告の抑制に使う
- `git_status_checked`: PostToolUse(Bash)が`git status`/`git log`/`git diff`観測時に記録
- `git_log_checked`: PostToolUse(Bash)が`git log`観測時に記録。commit/rebase/push/編集時リセット、amend/rebase前確認に使う
- `amend_pending_status_check`: cwd別辞書。PostToolUse(Bash)がamend/fixup成功時に記録し、実送出push成功時にリセットする。
  PreToolUse(Bash)側もpush前dirty検査clean通過時にリセットする（`--dry-run`・`-n`除く）
- `plan_mode_skill_invoked`: PostToolUse(Skill)／UserPromptSubmit（スラッシュ）で`agent-toolkit:plan-mode`呼び出しを記録する。plan file検査と最初ツール警告抑制に使う
- `session_review_invoked`: PostToolUse(Skill)／UserPromptSubmit（スラッシュ）で振り返りスキルを記録。対象は配布物`agent-toolkit:session-review`・個人フック`session-review-dotfiles`。Stop hook重複抑止・辞書リセットに使う
- `agent_toolkit_edit_skill_invoked`: dotfiles個人フックが`agent-toolkit-edit`呼び出しを記録する。未起動時のPreToolUse警告抑制と配布物`pretooluse.py`のRead隔離ブロックの例外判定に使う
- dotfiles個人フック管理: `session_review_extension_pending`（`session-review-dotfiles`使用記録、配布物Stop hook重複送出抑制）、
  `autonomous_exit_invoked`（`agent-toolkit:exit-session`呼び出し記録、`DOTFILES_AUTONOMOUS_EXIT_REQUIRED=1`下のStop hook未呼び出し判定用）
- `process_feedbacks_skill_invoked`: PostToolUse(Skill)／UserPromptSubmit（スラッシュ）で`agent-toolkit:process-feedbacks`・各短縮スラッシュを記録。
  Stop hookの拡張照合カテゴリ有効化判定と、PreToolUse(EnterPlanMode)ブロック判定に使う。PostToolUseは`process-feedbacks-finish`スキル起動検知時に偽へ戻し、`process-feedbacks`再起動時に真へ強制上書きする
- サブエージェント起動を検知する判定は`tool_name in ("Agent", "Task")`をSSOTとする
  （pretooluse・posttooluseとも同一。コード追加・改訂時は`grep -rn`で確認して同一集合を使う）
- 工程7完遂判定フラグ群はPostToolUse(Agent/Task)が記録する: `plan_reviewer_invoked`・`codex_review_invoked`・
  `agent_doc_validator_invoked`（末尾は文書対象時のみ必須。`plan_impl_reviewer_invoked`は`careful-review`・`quality-sweep`起動記録用に存続するが工程7の対象外）。
  `codex_review_invoked`は`plan-codex-reviewer`起動時、または`isSidechain`が偽の`mcp__codex__codex`完了時に記録する
- `plan_codex_reviewer_invoked`: `plan-codex-reviewer`サブエージェント起動時のみ真化する（PostToolUse(Agent/Task)が記録）。`mcp__codex__codex`直接呼び出し前の経路遵守検査に使う
- `plan_codex_reviewer_blocked`: `plan-codex-reviewer`起動失敗時（auto mode下のブロック等）に真化する。
  PostToolUseFailure・PermissionDenied（Agent/Task限定）が検出し、`mcp__codex__codex`直接呼び出しのauto mode例外条件に使う
- `recorded_codex_thread_id`: `mcp__codex__codex`成功時のPostToolUseが`tool_response.threadId`を記録する。
  `mcp__codex__codex-reply`のPreToolUseがthreadId一致検査で参照する（`plan_codex_reviewer_invoked`・
  `plan_codex_reviewer_blocked`・`recorded_codex_thread_id`の3件は新計画着手時に工程7完了フラグと共にリセットされる）
- `current_plan_file_path`: PostToolUse(Write/Edit/MultiEdit)が計画ファイル編集時のパスを記録。
  ExitPlanMode時の再読込と、`plan-impl-executor`系Agent起動時の起動プロンプト参照先パス一致判定に使う
- 計画ファイル未作成時の直接編集検知フラグ群（`plan_file_written`・`direct_agent_toolkit_edit_count`・`last_agent_toolkit_edit_path`）はPreToolUseが更新する。agent-toolkit配下編集連続を検知し、2件目warn・3件目blockとする
- 新規フラグ追加時は当該フラグの寿命（セッション終了まで維持・新計画着手時にリセット・
  特定イベントで消去等）を明示する。寿命が「新計画着手時にリセット」に該当する場合は
  既存のリセット関数（`pretooluse.py`の`_reset_process7_completion_flags`等）へ追加する

### 公式リファレンス

スキルの新規作成・hook実装では公式マーケットプレイス（`anthropics/claude-plugins-official`）を参照する。
対象は`skill-creator:skill-creator`と`plugin-dev`の
`skill-development`・`agent-development`・`hook-development`・`plugin-structure`。
referencesに記載のない仕様や認識相違は`https://code.claude.com/docs/ja/`配下を`WebFetch`で取得する。
参照対象は`memory.md`・`skills.md`・`sub-agents.md`・`hooks.md`・`plugins.md`・`plugins-reference.md`。
