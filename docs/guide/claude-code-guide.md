# agent-toolkit導入ガイド（Agent Plugins・Claude Code・Codex）

agent-toolkitはAgent Plugins、Claude Code、Codexで共有できるコーディングエージェント向けツールキットである。
Claude Codeは対話、フック、ルールの読み込み、作業全体の統括を担う。Codex CLIはCodex App Server MCP経由の
調査・実装・レビューに加え、Codexセッションで共有スキルを直接実行する。uvは配布スクリプトと
`atk`コマンドの実行基盤である。

## コンセプト

1. 標準動作のカスタマイズ: 判断基準が曖昧な場面での事前相談の徹底、lint抑制時のユーザー確認の必須化、
   検証からコミットまでの流れの自動化などコーディングエージェントの動作を変更する。
   auto mode下でも確認・計画工程を省略しない方針を維持する
2. 品質水準の維持: コードスタイルや設計が乱れたプロジェクトではコーディングエージェントも
   既存コードの影響を受けて同水準のコードを生成する（割れ窓理論）。
   各言語のモダンなイディオム・禁止パターン・セキュリティ注意点・テスト方針を明示し品質水準を維持する
3. 知識の補完: LLMの学習データに含まれない情報を補う。
   Claude Code関連の仕様は改訂が頻繁なため、`agent-toolkit:agent-standards`スキル配下の
   `references/agent-skills.md`・`references/claude-hooks.md`で現行仕様を参照できるようにする。
   個人製作のツール（pyfltr・pytilpackなど）は学習データに含まれないため、
   `agent-toolkit:pyfltr-usage`・`agent-toolkit:pytilpack-usage`等でリファレンスを提供する

Anthropic公式のsuperpowersスキルと重複する内容は多いが、
日本語環境での確実なトリガーと大規模開発向けの細かな制御のために独自に作成している。
性質上、頻繁な改訂が発生する。

agent-toolkitはルールファイルと3形式で共有するプラグインルートで構成される。

- ルールファイル: `~/.claude/rules/agent-toolkit/`に配置されるルールファイル。
  自動読み込みされ、行動原則・運用方針・言語表現などの共通指示を提供する
- Agent Plugins: `agent-toolkit/`をパッケージルートとして扱う。
  Agent Plugins仕様の範囲で利用できるスキルとpyfltr MCPを提供する
- Claude Code・Codex: 同じ`skills/`を利用し、形式固有のmanifest、ルール、フック、実行資源を追加する。
  固有のフィールドを受理するクライアントでは、`skills/`のすべてのスキルを共有できる

Agent Pluginsが定義しないClaude Code・Codex固有のディレクトリも同じルートに存在する。
Agent Plugins互換クライアントは、それらの未対応の資源を読み込まない。

両者は相互依存しており、基本的に同時に導入することを前提とする。

部分的に動作を変えたい場合は、
ユーザー側の`~/.claude/CLAUDE.md`・プロジェクトの`CLAUDE.md`・プロンプトでの指示などで上書きできる。
優先度はルールファイル側に明記している。

## 前提条件

単体インストーラーを実行する前に、次の3コマンドを公式手順で導入する。
インストーラーはCLI本体を導入しない。

- `claude`: [Claude Code](https://docs.anthropic.com/ja/docs/claude-code/overview)のCLI
- `codex`: [Codex CLI](https://developers.openai.com/codex/cli/)本体
- `uv`: [uv](https://docs.astral.sh/uv/)による配布スクリプト実行環境

Stopフックが`hookSpecificOutput.additionalContext`を利用するため、Claude Code 2.1.163以上を要求する。
プラグイン単体利用者では非強制の前提条件、dotfiles配布の管理設定では`requiredMinimumVersion`で強制する。

## クイックスタート

dotfiles配布利用者では、`chezmoi apply`後の処理がAnthropic公式ネイティブ版を管理する。
未導入時は公式インストーラーで導入し、導入済みの場合は`claude update`で更新する。
WindowsでClaude Codeが実行中の場合は停止せず、更新と旧npm版の整理を次回へ延期する。

Agent Plugins互換クライアントでは、`agent-toolkit/`をプラグインのパッケージルートとして指定する。
導入操作はクライアントごとに異なるため、利用するクライアントの公式手順を参照する。

### ツールキットのインストール

`install-claude.sh`と`install-claude.ps1`は公開済みの互換名を維持しているが、現在はClaude Codeと
Codexの双方を設定する。3コマンドのいずれかを検出できない場合は、ファイルを書き込まずエラー終了する。

ツールキットのインストールには以下のワンライナーを実行する。

- Linux:

    ```bash
    curl -fsSL https://raw.githubusercontent.com/ak110/dotfiles/master/install-claude.sh | bash
    ```

- Windows:

    ```cmd
    powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://raw.githubusercontent.com/ak110/dotfiles/master/install-claude.ps1 | iex"
    ```

ルールファイルが`~/.claude/rules/agent-toolkit/`へ配置され、Claude CodeとCodexの
agent-toolkitプラグイン、Claude Code専用のCodex App Server MCP、`atk`ラッパーが設定される。
再実行すると最新版へ同期される。

両インストーラーは再実行時にCodexプラグインも更新する。
更新でCodexプラグインの状態が変化し、app-server daemonの稼働を確認できた場合は、daemonの再起動コマンドが案内される。
実行中のセッションは互換リンクにより更新前の実行先を保つため、案内されたコマンドはセッションの完了後に実行する。
互換リンクの仕組みと再起動案内の条件は[Codex利用ガイド](codex-guide.md)の「プラグイン更新の反映」を参照。

インストール後、非公式のプラグインマーケットプレイスはデフォルトで自動更新が無効のため、初回のみ手動で有効化する。

1. Claude Code内で`/plugin`を実行
2. `Marketplaces`タブで`ak110-dotfiles`を選択
3. `Enable auto-update`を選択

## 処理順序

インストーラーは次の順で設定する。途中で必須処理が失敗した場合は非0で終了する。

1. `claude`、`codex`、`uv`の存在を確認する
2. Claude Codeルールを原子的に配置する
3. Claude Codeのマーケットプレイスとプラグインを設定する
4. Codexのマーケットプレイスとプラグインを設定する
5. User scopeに残る旧Codex MCPの完全一致定義を照合し、該当時だけ移行する
6. `atk`ラッパーを配置する

新しいMCPサーバーはClaude Code plugin内の`codex_app_server`であり、User scopeへ登録しない。
インストーラーと`chezmoi apply`後処理は、過去の手順で登録されたUser scope
`codex`が`codex mcp-server`の完全一致定義である場合だけ、直前に再照合して削除する。
追加フィールド、別のcommand・args・timeout、Local・Project scopeの設定は変更しない。
plugin更新だけの経路では外部設定を変更せず、必要な場合は診断と手動削除手順を表示する。

## 設定確認

次のコマンドで導入結果を確認できる。

```bash
claude mcp get codex
codex plugin list
claude plugin list
```

`claude mcp get codex`は旧User scope定義の有無を確認する診断である。Codex App Server MCPは
Claude Code pluginから読み込まれるため、`codex plugin list`と`claude plugin list`で各pluginの状態を確認する。

Codex委譲は次の5ツールで行う。`codex_start`は既存ディレクトリを絶対パスで指定して即時に
`session_id`を返し、`codex_status`または`codex_wait`で状態を観測する。状態応答の`result_available`で結果回収可否を確認する。
`codex_wait`は公開terminal statusで復帰し、既定timeoutは300秒である。非対応server requestによる`failed`でも
`result_available=false`なら`turn/completed`未受信のため`codex_result`が拒否される。
`result_available=true`を確認してから`codex_result`を再実行する。
`codex_result`で終端結果を回収した後、同じ`session_id`へ`codex_start_reply`で次のturnを開始する。
App Serverへ渡す`approvalPolicy=never`と`dangerFullAccess`はMCP内部で固定され、承認・停止・一覧操作は公開しない。

## Claude Codeの推奨設定

以下の設定を適用することを推奨する。

### `~/.claude/settings.json`

- `autoMemoryEnabled`: `false`（自動メモリー機能を無効化）
- `showClearContextOnPlanAccept`: `true`（plan mode承認時にコンテキストクリアの選択肢を表示）
- `env.CLAUDE_CODE_NO_FLICKER`: `"1"`（画面のちらつきを抑制）
- `permissions`: 許可・拒否するツールやパターンを記述
 （[例](https://github.com/ak110/dotfiles/blob/master/share/claude_settings_json_managed.json)）

### `/config`コマンド

- `Verbose output`: 有効
- `Default permission mode`: `Plan mode`
- `Language`: `Japanese`

### `/plugin`コマンド

claude-plugins-officialから以下を導入する。

- 推奨: `context7`・`typescript-lsp`（`npm install -g typescript-language-server typescript`が必要）
- 任意: `claude-md-management`・`skill-creator`
- 無効: `pyright-lsp`（Claude Codeがインストールを推奨するが誤動作が発生するため、インストール後に`Disable`）

### VSCode設定（任意）

ターミナル内での右クリックによる意図しない貼り付けを防ぐ場合は、`settings.json`へ以下を追加。

```json
{
  "terminal.integrated.rightClickBehavior": "nothing"
}
```

## 推奨ワークフロー

作業の進め方は次の2パターンを推奨する。要件がどこまで固まっているかで選ぶ。

### 対話型

計画作成のスキル（`/agent-toolkit:plan-mode`）を手動で起動して計画ファイルを作成し、
内容を確認して承認したうえで実装まで進める。
対話の途中で要件が変わる作業や、方針をその場で確定したい作業に向く。

### 自律型

フィードバック処理の常駐実行（`atk mq process-loop`）を起動し、依頼したい内容を要求として登録する。
登録した要求は、調査・計画・実装・レビュー・公開まで順に自動で処理される。
要件を本文だけで説明できる作業に向く。

オーケストレーター・モデル・effortは`atk config`の`orchestrate_model`へ設定する。
書式は`<claude|codex>:<model>[/<effort>]`、既定値は`claude:opus[1m]/medium`（Claude Code）である。
Claude Codeを使う場合は設定を変更せず、次のコマンドを実行する。

```bash
atk mq process-loop
```

Codexへ切り替える場合は、設定を保存してから起動する。設定は以後の起動へ適用される。

```bash
atk config set orchestrate_model codex:gpt-5.6-sol/medium
atk mq process-loop
```

dotfiles以外のリポジトリでworktree隔離を使う場合は、`atk mq process-loop --worktree[=NAME]`を指定する。
`NAME`を省略すると`process-loop`を使い、dotfilesリポジトリではオプションを指定しなくても自動的にworktreeを使う。

登録経路は、要求の難易度と、計画を事前にレビューしたいかどうかで選ぶ。
一括での移行・復元は`atk serve`の新規追加ダイアログでも種別「一括登録（show形式）」から実行できる。

| 登録経路 | 選ぶ場面 |
| --- | --- |
| `atk mq add` | 依頼内容が既に固まっており、本文をそのまま登録したい |
| `atk mq add --batch` | 別環境の`atk mq show --all`の出力を複数件まとめて移行・復元したい |
| `/agent-toolkit:add-feedback` | 依頼内容を対話で確定してから登録したい |
| `/agent-toolkit:plan-and-add-feedback` | 計画の作成とレビューまで先に済ませ、実装だけを自律実行へ渡したい |

## 運用と保守

計画作成・実装・レビューではCodex経路を標準とする。Codexが一時的に利用できない場合だけ、
Claude Codeのサブエージェントを回復経路として使用する。

更新時はクイックスタートのインストーラーを再実行する。アンインストール時は双方のプラグインを除去し、
`~/.claude/rules/agent-toolkit/`と生成された`atk`ラッパーを削除する。plugin更新だけでは旧Codex MCPを
自動削除しない。単体インストーラーまたは`chezmoi apply`後処理では、完全一致した旧User scope定義だけを移行する。

自動登録に失敗した場合は、設定ファイルのJSON構造を修復してから次の診断・復旧コマンドを実行する。

```bash
claude mcp get codex
claude mcp remove --scope user codex
```

上記の削除は、`claude mcp get codex`で旧`codex mcp-server`定義を確認した場合だけ実行する。
custom定義を削除する場合は利用者が内容を確認してから明示的に実行する。2時間のtimeoutは新経路へ引き継がない。

```bash
chezmoi apply
```

Codex単独セッションとdotfiles固有の配布内容については[Codex利用ガイド](codex-guide.md)を参照してください。

## atkコマンドのPATH設定

`agent-toolkit`プラグインは`atk`ラッパースクリプトを`agent-toolkit/bin/atk`に配置する。
Claude Codeがマーケットプレイス経由でインストールした実体は
`~/.claude/plugins/cache/<marketplace-name>/agent-toolkit/<version>/bin/atk`にある。
バージョン部は更新ごとに変わる。追随処理は`install-claude.sh`（Linux/macOS）または`install-claude.ps1`（Windows）で自動化する。

- Linux/macOS: `~/.local/bin/atk`に最新バージョンを動的解決するラッパーを配置する
- Windows: `~/.local/bin/atk.cmd`に同等のバッチラッパーを配置する
- いずれも`~/.local/bin`がPATHに含まれていない環境では警告を表示する

dotfiles配布利用者は`chezmoi apply`で`~/dotfiles/agent-toolkit/bin`がPATHへ自動配置されるため、上記スクリプトの実行は不要。

## 構成と機能

### 常時有効な仕組み

ルールファイル（`~/.claude/rules/agent-toolkit/`配下）は自動ロードされる。
`01-agent.md`が基本原則・運用方針・言語表現・検証とコミットの流れを提供する。
文体の核はJIS規格・公的な標準仕様書のスタイルとし、
対話型UI向けの敬体はNHKの案内放送原稿のスタイルを例外として割り当てる。

agent-toolkitプラグインはpyfltrのMCPサーバーを同梱する。
プラグインを導入するとClaude CodeとCodexの双方で自動的に利用可能になり、
検査の実行・横断検索・横断置換・実行履歴の参照をシェルを経由せずに呼び出せる。
サーバーは`uvx`でpyfltrを取得して起動するため、pyfltr自体の事前インストールは要らない。

agent-toolkitは以下のフックを常時有効化する。
識別子はイベント名と処理名の組で示し、`plugin`はagent-toolkitプラグインの配布分、
`個人設定`はdotfiles利用者の`~/.claude/settings.json`へ配布される分を指す。
Codex欄の「対応」「部分対応」「非対応」は、Codex 0.147.0の公式契約で確認した範囲を示す。

| フック識別子 | 処理概要 | Claude対応状況 | Codex対応状況 |
| --- | --- | --- | --- |
| plugin `PreToolUse/pretooluse` | 編集内容とコマンドの事前検査。文字化け・他言語文字の混入・LF改行のみの`.ps1`書き込み・lockfileやシークレットの直接編集・codexサンドボックス指定の弱体化をブロックし、口語表現・ホーム絶対パスの混入・自動生成manifestの手編集を警告する。Bashでは`sleep`直後の状態確認連結・`uv run python`の誤用・パターン一致によるプロセス終了をブロックし、出力の切り詰め・version未更新・未検証コミット・一括ステージ・`codex exec`前の未決事項を警告し、`git log`へ`--decorate`を自動挿入する | 対応 | 部分対応。編集検査は口語表現・文字化け・他言語文字・ホーム絶対パス・lockfile・シークレット・manifest・否定規定表現・サンドボックス保護に対応する。`.ps1`改行、frontmatter同期注記と本文節参照の実在検証はpatch入力から判定できないため非対応。Bash検査は現在の入力とcwdだけで判定するものと、編集成功状態による一括ステージ警告に対応する。コマンドの終了コードを取得できないため、`git log`確認・amend後の状態・検証実行に依存する検査は非対応 |
| plugin `PostToolUse/posttooluse` | 成功したツール実行の観測結果を記録する。編集ファイル・計画ファイルの記録、条件付き禁止形の警告、検証実行・`git log`確認・amend後状態の記録、回答済みTBDの通知を行う | 対応 | 部分対応。成功した編集の対象記録、計画ファイル記録、条件付き禁止形の警告に対応する。シェル実行の終了コードが届かないため、検証実行とgit状態の記録は非対応 |
| plugin `SubagentStop/subagent_stop_advisor` | 空の完了報告での終了をブロックし、登録済み調整役では未消化の子エージェント起動もブロックする | 対応 | 対応。空の完了報告のブロックに対応する。調整役の子エージェント検査は、完了判定へ利用できる安定したtranscript契約が無いため非対応 |
| plugin `SessionEnd/session_end_cleanup` | 期限を過ぎたセッション状態を回収し、会話破棄時だけ当該セッションの状態を削除する | 対応 | 対応。終了理由が`other`固定のため、期限切れ状態の回収だけを実行する |
| plugin `SessionStart/session_start` | plugin更新だけの経路で残る旧Codex User scope MCPを読み取り専用で診断し、手動削除手順を案内する | 対応 | 非対応。Codex向けmanifestへは配布しない |
| plugin `Stop/stop_advisor` | 作業完了時に同一セッションの振り返りを一度だけ継続し、未コミット変更があれば`git status`の件数を表示する | 対応 | 対応 |
| plugin `UserPromptSubmit/user_prompt_submit` | 手動スキル起動の状態と振り返り対象を記録する | 対応 | 対応 |
| plugin `PermissionRequest/permissionrequest_codex` | BashからのCodex起動条件を検査する | 対応 | 対応 |
| plugin `PermissionRequest/permissionrequest` | コーディングエージェント向け文書や`~/.claude/plans/`への書き込みなど、確認ダイアログを自動許可する | 対応 | 非対応。Claude固有の入力と広い自動許可を前提とし、Codexには限定済みの`permissionrequest_codex`があるため配布しない |
| plugin `SubagentStart/subagent_start_tracker` | 委譲調整役の起動を記録し、SubagentStopの完了判定へ接続する | 対応 | 非対応。Codexの`agent_type`はspawn時のrole名又は`default`であり、現行の公開入力から追跡対象を識別できない |
| plugin `PostToolUseFailure/posttooluse` | ツール失敗時に状態を変更せず終了する | 対応 | 非対応。対応するイベントが存在しない |
| plugin `PermissionDenied/posttooluse` | 許可拒否時に状態を変更せず終了する | 対応 | 非対応。対応するイベントが存在しない |
| plugin `StopFailure/stopfailure_notifier` | APIエラーでのターン終了をベルとデスクトップ通知で知らせ、発生種別をログへ記録する | 対応 | 非対応。対応するイベントが存在しない |
| 個人設定 `PreToolUse/pretooluse` | dotfilesの配布元ファイルと個人の命名規約に基づく編集前検査 | 対応 | 非対応。dotfiles固有の配布構成に依存するため、プラグインへ移さない |
| 個人設定 `PostToolUse/posttooluse` | 参照文書へのReadとスキル起動をセッション状態へ記録する | 対応 | 非対応。同上 |
| 個人設定 `Stop/autonomous_exit` | 自律実行の終了契約に従ってセッションを終了する | 対応 | 非対応。Claude Code固有の自律終了契約に依存する |

Codexの`SessionStart`・`PreCompact`・`PostCompact`は、対応するCodex向け実装が無いため新設しない。

計画ファイルと計画運用に関する検査は、上表の`PreToolUse`・`PostToolUse`が扱う。

- 計画ファイルは`概要`、`実施内容`、`提示素材`、`変更履歴`、任意の`バグ調査結果`、
  `恒久化・リファクタリング内容`、`実装資料`、`完了条件`、`進捗ログ`を固定H2として検査する。
  自由な見出しは`実装資料`配下のH3以下だけを許容する
- `概要`直下の計画メタ情報では起動経路、対象リポジトリ、作業種別、ベースコミットの4項目と記法を検査し、
  ベースコミットは対象リポジトリのcommitとして解決できることも検査する
- 変更履歴の5列表、進捗ログの3列表、提示素材の素材表・要求表、合意表の素材・要求参照、実施内容表の3値分類、
  旧形式の提示素材を読み取り互換として受理した場合のwarning、バグ調査表の固定14行、
  恒久化・リファクタリング・類似見直しの検討実体を検査する
- 変更対象と変更内容は`実装資料`の変更説明へ集約し、独立した対象ファイル一覧や関連検査を持たない
- 計画レビューは欠落した事実と阻害される判断・検証・成果が対応する指摘だけを受理し、体裁や記述量を指摘理由にしない
- 計画レビューでは、メインセッションが機械チェック・修正系と総合レビュー系へ直接委譲する
- 計画実装では、`plan-impl-executor`がコミット単位ごとの書込担当と2つの読み取り専用のレビュー担当を管理し、
  書込担当がcommit、呼び出し元がpushとCI確認を担当

このほか、メインエージェント応答の記述言語の警告、
`agent-toolkit:delegation`未起動での委譲開始のブロック、`AskUserQuestion`への縮退誘発フレーズ混入のブロックを
Claude Codeで有効化する。
記述言語の警告は、応答が日本語文字を含まず英単語を2語以上含む記述であるとき、
応答の冒頭が英語の談話標識であるとき、日本語文字の比率が閾値未満のときに返す。
英単語が1語だけの記述は、識別子やコマンド名の単独提示と区別できないため警告しない。
委譲を伴う工程のスキルを起動した時点で`agent-toolkit:delegation`が未起動の場合は、起動を促す案内を返す。

### オンデマンドのスキル

該当作業に着手したとき自動的にロードされる。Claude Codeは`/`、Codexは`$`を付けて手動でも呼び出せる。

- `agent-toolkit:coding-standards`: コードの新規作成・修正・レビュー時の品質基準とテスト方針
- `agent-toolkit:writing-standards`: Markdown・README・技術文書などのドキュメントとコード内コメントの品質基準
- `agent-toolkit:agent-standards`: コーディングエージェント向け文書固有の品質基準
- `agent-toolkit:commit`: git commit作業（通常commit・amend・fixup）の手順とConventional Commits規約
- `agent-toolkit:bugfix`: バグ対応時の原因区分、類似見直し、是正・横展開・再発防止の判断基準
- `agent-toolkit:delegation`: 受信者への依頼契約と、必要な場合だけ読む実行経路別の委譲手順。
  委譲する場面で他スキルから自動的にロードされ、手動では呼び出せない
- `agent-toolkit:plan-mode`: 計画ファイル作成と、実装後の二系統レビューを含む実行引き継ぎ
  - 計画確定時は固定H2と表、計画メタ情報、見出し階層、参照実在を機械検査する
  - バグ対応計画は計画メタ情報の固定記法から判定し、固定14行の調査表で4原因区分、
    原因起点の類似見直し、是正・横展開・再発防止を記録する
  - 変更履歴は5列表で解決済み論点の指摘内容と採否を安定IDで保持し、再レビューでの根拠なき再指摘を防ぐ
  - 進捗ログは日時・完了した工程・結果の3列表で実装工程の作業状況を追跡し、異常終了からの再開に使う
- `agent-toolkit:review-standards`: コードレビュー・ドキュメントレビュー実施時の判断基準（レビュアー側心得）
- `agent-toolkit:reviewee-standards`: レビュー指摘受領時の採否・修正・自己点検の判断基準（レビューイー側心得）
- `agent-toolkit:add-feedback`: 利用者向け要件を対話で確定し、計画ファイルを作成せず通常型フィードバックを投入する
- `agent-toolkit:process-feedbacks`: 未分類または本文変更済みの項目だけを分類し、
  保存済みメタデータから依存、上限、競合を機械計算して処理順を決める
- `agent-toolkit:plan-and-add-feedback`: 計画作成からレビューまでを実施し、実装の代わりにフィードバック投入で終える運用
- `agent-toolkit:pyfltr-usage`: pyfltrの使い方・出力解釈のリファレンス
- `agent-toolkit:pytilpack-usage`: pytilpackのモジュール構成とAPI参照のリファレンス
- `agent-toolkit:gitlab-ci-usage`: `.gitlab-ci.yml`編集時のキーワード仕様・典型パターンのリファレンス
- `agent-toolkit:shell-exec`: 長出力が予想されるコマンド列をサブエージェントへ委譲し、
  メインへ終了状態と要約だけを返す
- `agent-toolkit:exit-session`: ユーザー指示時または自律実行スキル完遂時にClaude Codeのセッション自体を終了する
  （Claude Code以外のホストでは本体プロセスを停止せず、終了理由を最終応答としてターンを完了させる）

### 明示呼び出し専用のスキル

- `agent-toolkit:session-review`: セッションの振り返り。ユーザー手動起動またはStopフックからの明示的な呼び出し指示でのみ起動し、
  独立した読み取り専用の`session-review-advisor`が恒久改善候補を評価する

## 更新方法

ルールファイル・プラグインとも頻繁に更新されるため、定期的に最新化する。

「ツールキットのインストール」のワンライナーを再実行すると更新される。
dotfiles（chezmoi）管理下のマシンでは`chezmoi apply`を実行しても更新できる。
いずれの単体インストーラーも初回導入専用ではない。Codex App ServerはClaude Code pluginが
セッション単位でstdio子プロセスとして所有するため、共有daemonの再起動は行わない。
Codex plugin本体の更新で既存セッションへ反映できない場合は、Codex pluginの案内に従い、
進行中の作業を回収してから新しいセッションを開始する。
