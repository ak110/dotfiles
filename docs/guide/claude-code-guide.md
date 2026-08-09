# agent-toolkit導入ガイド（Agent Plugins・Claude Code・Codex）

agent-toolkitはAgent Plugins、Claude Code、Codexで共有できるコーディングエージェント向けツールキットである。
Claude Codeは対話、フック、ルールの読み込み、作業全体の統括を担う。Codex CLIはMCP経由の
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
  参照検証器が受理する15スキルとpyfltr MCPを可搬部分として提供する
- Claude Code・Codex: 同じ`skills/`を利用し、形式固有のmanifest、ルール、フック、実行資源を追加する。
  固有frontmatterフィールドを受理するクライアントでは残る2スキルも共有できる

Agent Pluginsが定義しないClaude Code・Codex固有のディレクトリも同じルートに存在する。
Agent Plugins互換クライアントは未対応の資源を可搬部分として扱わない。

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
agent-toolkitプラグイン、Codex MCP、`atk`ラッパーが設定される。
再実行すると最新版へ同期される。

両インストーラーは再実行時にもCodexプラグインを更新する。
`codex plugin add`の前後で導入済みプラグインのversionとenabledを比較し、いずれかが変化すると、
後続処理の成否にかかわらずstderrの最終行へ
`codex app-server daemon restart`を表示する。後続処理が失敗した場合は、エラーを先に表示し、
非0の終了状態を維持する。進行中のセッションを保護するため、セッションの完了後に案内されたコマンドを実行する。
導入前後の状態が同じ場合、状態を確認できない場合、プラグイン追加前の処理または
`codex plugin add`自体が失敗した場合は、再起動案内を表示しない。

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
5. User scopeにCodex MCPが無い場合だけ登録する
6. `atk`ラッパーを配置する

`~/.claude.json`トップレベルに既存のCodex MCPがある場合は、そのUser scope登録を上書きしない。
Local scopeまたはProject scopeだけに同名登録がある場合はUser scopeへ追加する。
dotfiles配布利用者が`chezmoi apply`を実行すると、完全なUser scopeのCodex MCP定義へ
2時間のper-server timeoutを付加する。この設定はCodex MCPだけに適用する。

## 設定確認

次のコマンドで導入結果を確認できる。

```bash
claude mcp get codex
codex plugin list
claude plugin list
```

上から順に、Claude Codeから利用するCodex MCP、Codexプラグイン、Claude Codeプラグインの状態を表示する。

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

Claude Codeを使う場合は次のコマンドを実行する。
`--orchestrator`を省略した場合もClaude Codeを使う。

```bash
atk mq process-loop --orchestrator=claude
```

Codexを使う場合は次のコマンドを実行する。

```bash
atk mq process-loop --orchestrator=codex
```

登録経路は、要求の難易度と、計画を事前にレビューしたいかどうかで選ぶ。

| 登録経路 | 選ぶ場面 |
| --- | --- |
| `atk mq add` | 依頼内容が既に固まっており、本文をそのまま登録したい |
| `/agent-toolkit:add-feedback` | 依頼内容を対話で確定してから登録したい |
| `/agent-toolkit:plan-and-add-feedback` | 計画の作成とレビューまで先に済ませ、実装だけを自律実行へ渡したい |

## 運用と保守

計画作成・実装・レビューではCodex経路を標準とする。Codexが一時的に利用できない場合だけ、
Claude Codeのサブエージェントを回復経路として使用する。

更新時はクイックスタートのインストーラーを再実行する。アンインストール時は双方のプラグインを除去し、
`~/.claude/rules/agent-toolkit/`と生成された`atk`ラッパーを削除する。Codex MCPは利用者の既存設定を
保護するため自動削除しない。

自動登録に失敗した場合は、設定ファイルのJSON構造を修復してから次の診断・復旧コマンドを実行する。

```bash
claude mcp add --scope user codex -- codex mcp-server
```

dotfiles配布利用者は手動登録後に次のコマンドを実行し、Codex MCPへ2時間のtimeoutを反映する。

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

Claude Code向けagent-toolkitプラグインは以下のフックを常時有効化する。

- 文字化け（U+FFFD）混入・LF改行のみの`.ps1`への書き込み・自動生成物の手編集をブロック
- 日本語を含む書き込み内容へのハングル・キリル文字の混入をブロック
- シークレットらしき値やホームディレクトリの絶対パスのハードコードを警告・ブロック
- 口語的な日本語表現や営業文書調・宣伝調のフレーズ・主観的修飾語の混入を警告し書き直しを促す
- メインエージェント応答に占める日本語文字の比率が閾値未満のときに警告し、短文ステータス報告の英語化を抑制する
- テスト未実行のままの`git commit`を警告
- `agent-toolkit/`配下のファイルを含む`git commit`で`plugin.json`のversion未変更を警告
- `git log`実行時に`--decorate`オプションを自動挿入する
- `codex exec`実行前に未決事項の確認を促す
- `sleep`直後に状態確認を連結するBash入力をブロックし、1回の待機ループへ誘導する
- メインセッションでは`agent-toolkit:delegation`の起動記録`delegation_skill_invoked`が無い
  `mcp__codex__codex`・`mcp__codex__codex-reply`の呼び出しをブロック
- codex呼び出しのサンドボックス指定を削除・弱体化する編集をブロック
- AgentまたはTaskツール起動時のnameパラメーター指定をブロック
- 計画レビューでは、メインセッションが機械チェック・修正系と総合レビュー系へ直接委譲する
- 計画実装では、`plan-impl-executor`がコミット単位ごとのwriterと2つの読み取り専用reviewerを管理し、
  writerがcommit、呼び出し元がpushとCI確認を担当
- 未コミット変更がある場合のStop時に`git status`をユーザーへ表示
- APIエラー停止後の入力待ち時にツール呼び出しの解析失敗をベルとデスクトップ通知で警告
- APIエラーでのターン終了の発生種別をログへ記録
- plan mode中で最初のツール呼び出しがplan-modeスキル以外の場合に警告
- `AskUserQuestion`の各フィールド（質問本文・ヘッダー・選択肢のラベルや説明）に
  作業量・残コンテキスト・既存パターン踏襲・工程省略宣言などを根拠とした縮退誘発フレーズが含まれる場合にブロック
- 計画ファイルは`変更履歴`、`目的`、`対応方針`と最終の`進捗ログ`を固定の人間向け領域として検査し、
  実装資料、変更内容、実行方法、完了条件を扱う実装者向け領域だけ構成変更を許容する
- `目的`直下の計画メタ情報では起動経路、対象リポジトリ、作業種別、ベースコミットの4項目と記法を検査する
- 変更履歴の5列表、進捗ログの3列表、提示素材の逐語転記、合意表の原文参照、実施内容表の3値分類、バグ調査表の固定14行、
  恒久化・リファクタリング・類似見直しの検討実体を検査する
- 対象ファイル一覧は実装者向け領域の通常箇条書きをSSOTとし、空、重複、危険パス、基準コミット上の状態矛盾を検査する
- 既存計画は計画メタ情報と実装者向け領域（対象ファイル一覧を含む）の旧配置を読み取り互換として扱い、
  新規計画は新しい正規形だけを受理する
- 計画レビューは欠落した事実と阻害される判断・検証・成果が対応する指摘だけを受理し、体裁や記述量を指摘理由にしない
- 規範文書の本文中にある他ファイルの節参照が実在しない場合に警告
- Gitワークツリー配下のコーディングエージェント向け文書や`~/.claude/plans/`への書き込み時に確認ダイアログを自動許可

Codex向けagent-toolkitプラグインは、公式契約を確認済みの次の3イベントだけを配布する。

- `PermissionRequest`: BashのCodex起動条件を検査する
- `UserPromptSubmit`: 手動スキル起動の状態と振り返り対象を記録する
- `Stop`: 作業完了時に同一セッションの振り返りを一度だけ継続する

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
  - 計画確定時は人間向け固定領域の見出しと表、計画メタ情報、対象一覧、参照実在を機械検査する
  - バグ対応計画は計画メタ情報の固定記法から判定し、固定14行の調査表で4原因区分、
    原因起点の類似見直し、是正・横展開・再発防止を記録する
  - 変更履歴は5列表で解決済み論点の指摘内容と採否を安定IDで保持し、再レビューでの根拠なき再指摘を防ぐ
  - 進捗ログは日時・完了した工程・結果の3列表で実装工程の作業状況を追跡し、異常終了からの再開に使う
- `agent-toolkit:review-standards`: コードレビュー・ドキュメントレビュー実施時の判断基準（レビュアー側心得）
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
  独立した読み取り専用advisorが恒久改善候補を評価する

## 更新方法

ルールファイル・プラグインとも頻繁に更新されるため、定期的に最新化する。

「ツールキットのインストール」のワンライナーを再実行すると更新される。
dotfiles（chezmoi）管理下のマシンでは`chezmoi apply`を実行しても更新できる。
いずれの単体インストーラーも初回導入専用ではない。Codexプラグインの更新後に表示される
`codex app-server daemon restart`は、進行中のCodexセッションを完了してから実行する。
