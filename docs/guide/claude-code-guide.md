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
  参照検証器が受理する16スキルとpyfltr MCPを可搬部分として提供する
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
`codex app-server daemon version`でdaemonの稼働状態を確認する。状態確認が成功した場合だけ、
後続処理の成否にかかわらずstderrの最終行へ`codex app-server daemon restart`を表示する。
後続処理が失敗した場合は、エラーを先に表示し、
非0の終了状態を維持する。進行中のセッションを保護するため、セッションの完了後に案内されたコマンドを実行する。
導入前後の状態が同じ場合、状態を確認できない場合、プラグイン追加前の処理または
`codex plugin add`自体が失敗した場合は、再起動案内を表示しない。daemonが未起動の場合や
daemonの状態確認に失敗した場合も表示しない。

更新前のCodexプラグインキャッシュにある安全なversion名は、Codex管理キャッシュ外の台帳へ保存される。
台帳の配置先は`$CODEX_HOME/plugins/cache-compat/ak110-dotfiles/agent-toolkit/versions`である。
`CODEX_HOME`が未設定の場合は`~/.codex`を使用する。
更新後は、旧version名を現行versionの実体へ直接向ける互換リンクとして復元する。
LinuxとmacOSでは相対シンボリックリンク、Windowsでは管理者権限を必要としないディレクトリジャンクションを使う。
起動済みまたは再開したセッションは、保持している旧絶対パスからフックスクリプトを引き続き実行できる。

再起動案内は新versionを後続セッションへ反映するために表示される。
互換リンクは既存セッションの旧実行先を保持するため、daemonを自動終了しない運用と併用する。
旧version名と同名の通常entryがある場合は置換せず、台帳を保持したままインストーラーが失敗する。
競合を解消して同じversionのインストーラーを再実行すると、台帳から互換リンクを復元できる。
旧versionの除去は[Codexのstore実装](https://github.com/openai/codex/blob/main/codex-rs/core-plugins/src/store.rs)に基づく。
旧絶対パスを保持するセッションの事象は[Codex issue #25285](https://github.com/openai/codex/issues/25285)でも報告されている。

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

Codexの`SessionStart`・`PreCompact`・`PostCompact`は、対応するClaude Code側の実装が無いため新設しない。

計画ファイルと計画運用に関する検査は、上表の`PreToolUse`・`PostToolUse`が扱う。

- 計画ファイルは`概要`、`実施内容`、`提示素材`、`変更履歴`、任意の`バグ調査結果`、
  `恒久化・リファクタリング内容`、`実装資料`、`完了条件`、`進捗ログ`を固定H2として検査する。
  自由な見出しは`実装資料`配下のH3以下だけを許容する
- `概要`直下の計画メタ情報では起動経路、対象リポジトリ、作業種別、ベースコミットの4項目と記法を検査する
- 変更履歴の5列表、進捗ログの3列表、提示素材の逐語転記、合意表の原文参照、実施内容表の3値分類、バグ調査表の固定14行、
  恒久化・リファクタリング・類似見直しの検討実体を検査する
- 変更対象と変更内容は`実装資料`の変更説明へ集約し、独立した対象ファイル一覧や関連検査を持たない
- 計画レビューは欠落した事実と阻害される判断・検証・成果が対応する指摘だけを受理し、体裁や記述量を指摘理由にしない
- 計画レビューでは、メインセッションが機械チェック・修正系と総合レビュー系へ直接委譲する
- 計画実装では、`plan-impl-executor`がコミット単位ごとのwriterと2つの読み取り専用reviewerを管理し、
  writerがcommit、呼び出し元がpushとCI確認を担当

このほか、メインエージェント応答に占める日本語文字の比率が閾値未満のときの警告、
`agent-toolkit:delegation`未起動での委譲開始のブロック、`AskUserQuestion`への縮退誘発フレーズ混入のブロックを
Claude Codeで有効化する。

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
  独立した読み取り専用advisorが恒久改善候補を評価する

## 更新方法

ルールファイル・プラグインとも頻繁に更新されるため、定期的に最新化する。

「ツールキットのインストール」のワンライナーを再実行すると更新される。
dotfiles（chezmoi）管理下のマシンでは`chezmoi apply`を実行しても更新できる。
いずれの単体インストーラーも初回導入専用ではない。Codexプラグインの更新後、daemonが稼働中の場合だけ
`codex app-server daemon restart`を表示する。進行中のCodexセッションを完了してから実行する。
