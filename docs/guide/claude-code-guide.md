# agent-toolkit導入ガイド（Agent Plugins・Claude Code・Codex）

agent-toolkitはAgent Plugins、Claude Code、Codexで共有できるコーディングエージェント向けツールキットである。
Claude Codeは対話、フック、ルールの読み込み、作業全体の統括を担う。Codex CLIは`agents_server` MCP経由の
調査・実装・レビューに加え、Codexセッションで共有スキルを直接実行する。uvは配布スクリプトと
`atk`コマンドの実行基盤である。

## コンセプト

1. 標準動作のカスタマイズ: 判断基準が曖昧な場面での事前相談の徹底、lint抑制時のユーザー確認の必須化、
   検証からコミットまでの流れの自動化などコーディングエージェントの動作を変更する。
   auto mode下でも確認・計画工程を省略しない方針を維持する
2. 品質水準の維持: コードスタイルや設計が乱れたプロジェクトではコーディングエージェントも
   既存コードの影響を受けて同水準のコードを生成する（割れ窓理論）。
   各言語のイディオム、セキュリティ、テスト方針などの一般的な作法は方向性と代表語で示して想起を促し、
   プロジェクト固有の事実と守るべき制約は具体的に示して品質水準を維持する
3. 知識の補完: LLMの学習データに含まれない情報を補う。
   Claude Code関連の仕様は改訂が頻繁なため、`agent-toolkit:writing-standards`の
   `references/agent-skills.md`と`references/claude-hooks.md`で現行仕様を参照できるようにする。
   個人製作のツール（pytilpackなど）は学習データに含まれないため、
   `agent-toolkit:pytilpack-usage`等でリファレンスを提供する

Anthropic公式のsuperpowersスキルと重複する内容は多いが、
日本語環境での確実なトリガーと大規模開発向けの細かな制御のために独自に作成している。
性質上、頻繁な改訂が発生する。

agent-toolkitはルールファイル、共通のプラグインパッケージ、Codex専用生成rootで構成される。

- ルールファイル: `~/.claude/rules/agent-toolkit/`に配置されるルールファイル。
  自動読み込みされ、行動原則・運用方針・言語表現などの共通指示を提供する
- Agent Plugins: `agent-toolkit/`をパッケージルートとして扱う。
  Agent Plugins仕様の範囲で利用できるスキルとpyfltr MCPを提供する
- Claude Code: `agent-toolkit/`を参照し、Claude固有のmanifest、ルール、フック、実行資源を利用する
- Codex: `agent-toolkit/`を元に生成した`agent-toolkit-codex/`を参照する。
  Agent Plugins用のroot manifestを除外し、Codex固有manifestから同じskill、ルール、フック、実行資源へ到達する

`agent-toolkit-codex/`はCodex 0.154.0のsnapshotへ全資源を含めるため、相対シンボリックリンクを使わない。

ルールファイルとプラグインは相互依存しており、基本的に同時に導入することを前提とする。

部分的に動作を変えたい場合は、
ユーザー側の`~/.claude/CLAUDE.md`・プロジェクトの`AGENTS.md`・プロンプトでの指示などで上書きできる。
優先度はルールファイル側に明記している。

本書ではエージェントへ渡す作業の単位を2種類の略語で呼ぶ。
AWIはユーザーまたはエージェントが登録し、後で自動処理する作業要求である。
UWIは処理中にユーザーの判断が必要になった事項を記録する確認事項であり、ユーザーの回答はエージェントが元の作業へ反映する。
どちらもユーザーの手元のキューで管理し、対象リポジトリには保存しない。

## 前提条件

単体インストーラーを実行する前に、次の3コマンドを公式手順で導入する。
インストーラーはCLI本体を導入しない。

- `claude`: [Claude Code](https://docs.anthropic.com/ja/docs/claude-code/overview)のCLI
- `codex`: [Codex CLI](https://developers.openai.com/codex/cli/)本体
- `uv`: [uv](https://docs.astral.sh/uv/)による配布スクリプト実行環境

Stopフックが`hookSpecificOutput.additionalContext`を利用するため、Claude Code 2.1.163以上を要求する。
agent-toolkit単体のユーザーでは非強制の前提条件、dotfiles配布の管理設定では`requiredMinimumVersion`で強制する。

## クイックスタート

dotfilesユーザーでは、`chezmoi apply`後の処理がAnthropic公式ネイティブ版を管理する。
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
agent-toolkitプラグイン、共有`agents_server` MCP、`atk`ラッパーが設定される。
再実行すると最新版へ同期される。

両インストーラーは再実行時にCodexプラグインも更新する。
更新でCodexプラグインの状態が変化し、app-server daemonの稼働を確認できた場合は、daemonの再起動コマンドが案内される。
実行中のセッションは導入時のsnapshotを参照するため、案内されたコマンドはセッションの完了後に実行する。
専用生成rootと再起動案内の条件は[Codex利用ガイド](codex-guide.md)の「プラグイン更新の反映」を参照。

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
5. User scopeに残る旧Codex MCPの完全一致定義を確認し、該当時だけ移行する
6. `atk`ラッパーを配置する

新しいMCPサーバーはplugin内の共有`agents_server`であり、User scopeへ登録しない。
インストーラーと`chezmoi apply`後処理は過去の手順で登録されたUser scope
`codex`が`codex mcp-server`の完全一致定義である場合だけ、直前に再確認して削除する。
追加フィールド、別のcommand・args・timeout、Local・Project scopeの設定は変更しない。
plugin更新だけの処理では外部設定を変更せず、必要な場合は診断と手動削除手順を表示する。

## 設定確認

次のコマンドで導入結果を確認できる。

```bash
claude mcp get codex
codex plugin list
claude plugin list
```

`claude mcp get codex`は旧User scope定義の有無を確認する診断である。`agents_server` MCPは
Claude CodeまたはCodex pluginから読み込まれるため、`codex plugin list`と`claude plugin list`で各pluginの状態を確認する。

委譲の状態は次のコマンドで確かめる。
`atk agents list`は実行中と保持中の委譲先を一覧し、`--watch`を付けると約2秒ごとに表示を更新する。
`atk agents logs <session-id>`は選んだ委譲先の記録を表示し、`--follow`を付けると追記分を表示し続ける。
`atk agents wait`は委譲先の終端を待ち、回収した結果を表示する。
`agents_server`の公開ツール（`start`、`send_message`、`kill`、`list`、`show`、`stop`）の入力と応答の契約は[design-agents-server.md](../development/design-agents-server.md)を参照。

## 工程別モデル設定

計画作成・実装・レビューではCodexの利用を標準とする。Codexが一時的に利用できない場合だけ、
Claude Codeのサブエージェントを代替として使用する。

工程別モデル設定の各キーは、`<engine>:<model>[/<effort>]`をASCIIカンマで区切った複数候補を受け取る。
先頭の候補から順に起動を試み、モデル実行環境の可用性に起因する失敗を観測した場合だけ次の候補へ進む。
Claude CodeのWeekly limitと5時間の利用上限で拒否された場合は次の候補へ進まず、解除まで待って同じClaudeで作業を続ける。`agents_server`、`atk wi process-loop`、`atk run-skill`および`atk commit`のいずれも手動での再送は要らず、待機中は解除待ちの種類と解除予定時刻を`show`・`list`・statuslineまたは端末へ示す。待機を止める場合は`agents_server`では`kill`、CLIでは中断操作を使う。
変更できるキーと対応する起動は次のとおり。

| キー | 対応する起動 |
| --- | --- |
| `high_tier_model` | 計画・実装・修正・AWI投入・公開工程の終端・自動コードレビュー監査 |
| `medium_tier_model` | 実行レビュー・プロンプト評価・`model_type="medium_tier"`を指定した`start`の`explore` |
| `low_tier_model` | `model_type`を省略した`start`の`explore`と`shell`・軽量な`mode:`を宣言した`<役割名>.subagent.md`の`task` |
| `write_model` | `start`の`write` |
| `orchestrate_model` | `atk wi process-loop`・`atk run-skill` |
| `codex_fast_mode` | `agents_server`が起動するCodexの速度（`true`はfast mode、`false`は標準、未設定時は`false`） |

`atk config show`はパス4行、モデル設定5行と`codex_fast_mode`の1行を表示し、`atk config get`は設定値をそのまま返す。
`atk config set codex_fast_mode true`でfast modeを有効にし、`false`で標準速度へ戻す。設定は次のturnから反映され、MCPサーバーの再起動は不要である。Codexのモデル表示には`@fast`を付け、effortがあれば`codex:<model>/<effort>@fast`とする。
Codex系列名は委譲の起動時にモデルIDへ解決され、採用値は`agents_server`の`show`で確認できる。
`AGENT_TOOLKIT_CONFIG_<キー名の大文字>`の環境変数に空でない値を設定すると、`atk config show`と`atk config get`は環境変数の値を返す。
委譲の起動にもこの値を使う。環境変数は保存済みの設定より優先し、変数を解除すると保存済みの設定へ戻る。
`atk config set`は保存先だけを更新するため、同名の環境変数がある間は設定した値が実効値にならない。
`atk config apply-preset <プリセット名>`は`high_tier_model`・`medium_tier_model`・`low_tier_model`・`orchestrate_model`の4キーを1回の実行で一括保存する。`write_model`と`codex_fast_mode`はプリセットの対象外とする。
受理するプリセット名は`codex-balanced`、`codex-primary`、`claude-balanced`、`claude-primary`とする。主に使うengineがcodexとclaudeのどちらかと、上位のモデルを割り当てるキーの有無で選ぶ。
`atk config apply-preset show`または引数を付けない`atk config apply-preset`を実行すると、設定ファイルを変更せずに4プリセットそれぞれが保存する4キーの値を表示する。設定を保存するのはプリセット名を指定した実行だけである。
未知の名前を指定した実行は終了コード2で終わり、受理する値を表示する。
設定を保存していない環境では`codex-balanced`と同じ候補列を使う。

## 選定結果の確認設定

対象リポジトリの`pyproject.toml`に`[tool.agent-toolkit.pick-wi-check]`の`norm-spec`を置ける。置いた場合は`agent-toolkit:process-wi`の選定結果を確かめる`atk run-script pick-wi-check`が、条件に当たる項目の`プロジェクト規範の指定`の欠落も報告する。
各条件は`paths`と`suffixes`か`agent-doc = true`（エージェント向け文書）で対象のパスを選ぶ。`require-paths = true`は当たった対象ファイルのパスを、`require-text`は指定の文字列を、`プロジェクト規範の指定`へ書くことを求める。
表を置かないリポジトリでは指定を求めない。設定の構文と型の誤りは終了コード2で、設定の場所と直し方を示す。

## Claude Codeの推奨設定

以下の設定を適用することを推奨する。

### `~/.claude/settings.json`

- `autoMemoryEnabled`: `false`（自動メモリー機能を無効化）
- `showClearContextOnPlanAccept`: `true`（plan mode承認時にコンテキストクリアの選択肢を表示）
- `env.CLAUDE_BASH_MAINTAIN_PROJECT_WORKING_DIR`: `"1"`（Bash呼び出しごとに作業ディレクトリをプロジェクトへ戻す）
- `env.CLAUDE_CODE_NO_FLICKER`: `"1"`（画面のちらつきを抑制）
- `env.PLAYWRIGHT_MCP_OUTPUT_DIR`: ホーム配下の`.cache/playwright-mcp`の絶対パス（Playwright MCPが作業中のリポジトリへ`.playwright-mcp/`を残さないようにする）
- `permissions`: 許可・拒否するツールやパターンを記述
 （[例](https://github.com/ak110/dotfiles/blob/master/share/claude_settings_json_managed.json)）

### `/config`コマンド

- `Verbose output`: 有効
- `Default permission mode`: `Auto mode`
- `Language`: `Japanese`

### `/plugin`コマンド

claude-plugins-officialのプラグインは次の方針で扱う。

- 推奨: `context7`・`typescript-lsp`（`npm install -g typescript-language-server typescript`が必要）
- 任意: `skill-creator`
- 無効: `pyright-lsp`（Claude Codeがインストールを推奨するが誤動作が発生するため、インストール後に`Disable`）
- 無効: `claude-md-management`（`CLAUDE.local.md`を`.claude.local.md`と誤認し、`AGENTS.md`を走査しないため、インストール済みなら自動で無効化）

### VSCode設定（任意）

ターミナル内での右クリックによる意図しない貼り付けを防ぐ場合は、`settings.json`へ以下を追加。

```json
{
  "terminal.integrated.rightClickBehavior": "nothing"
}
```

## 推奨ワークフロー

作業の進め方は次の4パターンを推奨する。要件がどこまで固まっているかと、AWIの発生頻度で選ぶ。決まったスキルを定期的に実行したい場合は定期実行型を選ぶ。

### 対話型

エージェントへ作業を直接依頼する。エージェントは原則として計画ファイルを内部資料として作成する。既存の値や文字列の差し替えだけで完結し、恒久化・リファクタリングの候補が構造上生じない変更だけ作成を省く。
要件と公開範囲へ既存の認可を適用し、実装、検証、公開まで進める。
ユーザーだけの選好・認可・取得できない入力が残る場合は、その事項を着手前に確認する。技術的に確定できる事項はエージェントが判断して報告する。
対話の途中で要件が変わる作業や、方針をその場で確定したい作業に向く。

### 自律型

AWI処理の常駐実行（`atk wi process-loop`）を起動し、依頼したい内容を要求として登録する。
登録した要求は、調査・計画・実装・レビュー・公開まで順に自動で処理される。
選定はClaude CodeとCodexのどちらもメインが直接行い、別の選定担当は起動しない。圧縮ツールが提供されるClaude Codeでは全初期通常レーンの初回起動後に一度だけ会話を圧縮する。複数段階なら最後の段階の全起動後に行い、通知後、メインが同じ工程を続ける。予約後の待機は背景で開始してターンを終える。ツールが無い環境では、その旨を記録して圧縮せず待機する。Codex、通常0件、段階間、追加レーンと公開待機では、この圧縮を起動しない。
要件を本文だけで説明できる作業に向く。
要求を登録するときは、先行する要求の成果が無いと安全に実施できないか完成を判定できない場合だけ、その先行する要求への依存を設定する。

自律型でも対話型と同じ基準で確認の要否を判定し、技術と既存認可で確定する事項はエージェントが実施して報告する。
処理中にユーザーの判断が必要になった事項はUWIとして記録され、回答に依存する部分だけを保留して他の作業を続ける。
`atk wi process-loop`から起動したセッションでは、事前に承認を要する事項が回答期限付きの質問として表示されることもあり、期限内に回答が無ければUWIへ移る。
UWIへの回答は後述の「UWIへの回答と状態確認」の操作で行う。

オーケストレーター・モデル・effortは`atk config`の`orchestrate_model`へ設定する。
書式は`<claude|codex>:<model>[/<effort>]`をASCIIカンマで区切った候補列で、前述の工程別モデル設定と同じく先頭の候補から順に起動を試す。
設定していない場合は`claude:opus[1m]/medium,codex:sol/medium`を使う。
Claude Codeを使う場合は設定を変更せず、次のコマンドを実行する。

```bash
atk wi process-loop
```

Codexへ切り替える場合は、設定を保存してから起動する。常駐中に保存した設定は、次のセッションの起動前に反映される。

```bash
atk config set orchestrate_model codex:gpt-6-sol/medium
atk wi process-loop
```

dotfiles以外のリポジトリでworktree隔離を使う場合は、`atk wi process-loop --worktree[=NAME]`を指定する。
`NAME`を省略すると`process-loop`を使い、dotfilesリポジトリではオプションを指定しなくても自動的にworktreeを使う。

CI失敗だけを1回確認してAWIへ投入する場合は、エージェントが次のコマンドを呼べる。対象を省略すると現在の作業ツリーを使う。

```bash
atk wi check-alerts --target-repo /home/user/repo
```

常駐監視と同じCI収集・重複除外・保存を使い、実行後に終了する。投入0件も成功なら終了コード0、全体または一部の取得失敗は投入件数と取得不能の理由を分けて示し、終了コード1となる。
process-loop、process-wiとDependabot監査は起動しない。呼び出す時機は各環境が選び、process-wiの1回の実行への定期組込みはこのコマンドが行わない。

### まとめ処理型

AWIが常時発生しないリポジトリでは、常駐実行を起動せず、数件のAWIがたまった時点で
`/agent-toolkit:single-lane-process`を手動で起動してまとめて処理する。
起動したセッションの中で、調査から実装、レビュー、公開までが進む。
計画を要するAWIが含まれる場合も、同じセッションの中で計画の作成から実装まで進む。
複数の対象リポジトリを扱う場合は、計画と実行レビューを対象worktreeごとに分ける。同じ対象worktreeの計画対象と直接実装対象は、同じ実行レビューでまとめて確認する。

自律型とまとめ処理型は、AWIの発生頻度と常駐実行の要否で選ぶ。
常駐実行を動かし続けるだけのAWIが継続して発生する場合は自律型を選ぶ。
数日に数件の頻度でしか発生しない場合はまとめ処理型を選ぶ。

### 定期実行型

ログの点検のように、決まったスキルを数日に1回などの間隔で自律的に実行させたい場合は、cronなどの定期実行へ`atk run-skill`を登録する。
`atk run-skill`はWIキューを経由せずに、指定した対象リポジトリで指定したスキルを自律モードのセッションとして1回実行し、終了コードを返す。
モデルの選択、利用上限の解除待ち、同じスキルの多重起動の防止、時間上限（省略時6時間）とログの保存はコマンドが行う。

cronへ登録する例を次に示す。cronは端末のシェルと異なる`PATH`で起動するため、`atk`・`claude`・`codex`の実行ファイルの場所を`PATH`へ含める。
標準出力を`/dev/null`へ捨てると、成功時は何も届かず、失敗時だけ標準エラーの失敗行がcronのメール通知で届く。

```bash
PATH=/home/user/.local/bin:/usr/local/bin:/usr/bin:/bin
0 3 */3 * * atk run-skill --target-repo /home/user/repo my-skill >/dev/null
```

毎回の結果は報告用UWIとして届く。
破壊的な操作など判断が要る対応は、実施せずに事前承認型UWIとして届く。
そのUWIへ回答すると、同じ対象リポジトリと同じスキルの次回の`atk run-skill`がその対応を実施してUWIを終端する。
未回答の間は、同じ対応のUWIを重ねて投入せず、報告用UWIの要対応の欄へ既存のUWIを示す。
これらのUWIは`atk wi process-loop`と`/agent-toolkit:single-lane-process`の処理対象にならない。
実行ごとのログは`atk config get state_dir`が示すディレクトリの`run-skill/`に保存され、30日を過ぎたログは次の実行で削除される。
手動起動で標準エラーが端末につながる場合は、開始時にログの絶対パスと子の識別子、実行中に発言とtool呼出の要約が標準エラーへ表示される。
標準出力だけをファイルへ向けた場合も表示する。cronなど端末の無い起動では追加表示しない。
ログには端末に表示した進捗と、子の標準出力・標準エラーの生診断を残す。思考内容や生JSON全体は端末に表示しない。
オプションと終了コードは`atk run-skill --help`で確認する。

### 登録方法

登録方法は要求が既に確定しているか、対話で確定する必要があるか、別環境の項目をまとめて移行・復元するかで選ぶ。

| 登録方法 | 選ぶ場面 |
| --- | --- |
| `atk wi add` | 依頼内容が既に固まっており、本文をそのまま登録したい |
| `atk wi add --batch` | 別環境の`atk wi show --all`の出力を複数件まとめて移行・復元したい |
| `/agent-toolkit:add-awi-by-user` | 依頼内容を対話で確定してから登録したい |

一括での移行・復元は`atk serve`の新規追加ダイアログでも種別「一括登録（show形式）」から実行できる。
種別を`awi`または`uwi`のまま`atk wi show`の出力を送った場合も、本文がshow形式の構造を持てば単件として保存せず一括登録として取り込み、その旨を通知に示す。
show形式の構造とは、直後にfrontmatterが続く`### <ファイル名>.md`の行を持ち、その行より前が空行と`# awi`・`# uwi`・`## target_repo: ...`の見出しだけで構成される本文を指す。
このときtarget-repo欄とUWI用の入力欄の値は使わない。

登録したAWIの計画、実装と実行レビューはエージェントが進める。登録者が行う操作は、UWIへの回答と、次に述べる登録済み項目の修正である。
実行レビューの役割と証拠の扱いの設計は[design-review-evidence.md](../development/design-review-evidence.md)、実行時の手順は`agent-toolkit/share/exec-review.subagent.md`にある。

登録済みの未終端WIを直したい場合や、既存WIの前提へ懸念がある場合は、エージェントが対話や調査より先にその項目を`atk wi hold`で自動処理から外し、更新した本文を確かめてから`atk wi unhold`で戻す。処理中の項目は保留せず、変更内容を新しい項目として登録する。

`atk wi reject`はprocess-loopが要求の全てを不採用と確定した場合だけに使用する。
採用内容を統合した元項目または別リポジトリへ移管した元項目は、統合先または移管先をnoteへ記録して`atk wi rm --force`で除去する。
技術的な失敗、入力不足および外部条件待ちは、不採用にせずactive項目として保持する。ユーザーの回答を待つ項目は保留し（処理中の項目は`atk wi hold --state=processing <ファイル名>`、それ以外は`atk wi hold <ファイル名>`）、回答後に`atk wi unhold`で自動処理へ戻す。

### atk serveの3画面

画面を使うには、ターミナルで`atk serve`を実行してサーバーを起動する。起動ログの`atk serveを http://<ホスト>:<ポート>/ で配信します`に表示されたURLをブラウザーで開く。待受のホストとポートを指定しない場合は`127.0.0.1`のポート28766（Windowsでは28876）で待ち受ける。変更には`--host`・`--port`（例: `atk serve --port=28767`）、環境変数`AGENT_TOOLKIT_SERVE_HOST`・`AGENT_TOOLKIT_SERVE_PORT`、設定ファイル`serve.toml`を使い、この順に優先する。前景で起動した`atk serve`を終了するには、そのターミナルで`Ctrl+C`を押す。

待受の設定と画面の参照元の設定は、どちらも同じ設定ファイル`serve.toml`に書く。
環境変数`AGENT_TOOLKIT_SERVE_CONFIG`にファイルのパスを指定した場合は、そのファイルを読む。
指定しない場合は、OSごとに次の場所のファイルを読み、ファイルが無ければ設定ファイルなしで起動する。

- Linux: `~/.config/agent-toolkit/serve.toml`。環境変数`XDG_CONFIG_HOME`に絶対パスを設定した場合は`$XDG_CONFIG_HOME/agent-toolkit/serve.toml`
- macOS: `~/Library/Application Support/agent-toolkit/serve.toml`。環境変数`XDG_CONFIG_HOME`に絶対パスを設定した場合は`$XDG_CONFIG_HOME/agent-toolkit/serve.toml`
- Windows: `%LOCALAPPDATA%\agent-toolkit\serve.toml`

`atk serve`は同じナビゲーションから「WI」「計画ファイル」「セッション」の3画面を提供する。WI画面はキューの登録・編集・状態遷移を扱い、計画ファイル画面はローカルと設定済みリモートホストの計画ファイルを一覧・全文検索してMarkdownを表示する。セッション画面はClaude CodeとCodexの保存済みセッションを作業ディレクトリと開始時刻で一覧し、最初のユーザー発話を検索対象に使う。右ペインには発話・思考・ツール呼び出しを時系列に表示する。アシスタントの発言は見出し・箇条書き・表・コードをMarkdownとして整形して表示し、それ以外の本文は記号を残した等幅で表示する。実行環境が挿入した本文は「自動挿入」として折りたたみ、一覧の検索対象となる最初の発話からも除く。計画ファイルとセッションの参照元は、前述の設定ファイル`serve.toml`の`[plans]`・`[sessions]`で指定する。未指定の場合、計画ファイル画面はprivate-notesの`plans`と`~/.claude/plans`の両方を参照し、セッション画面は`~/.claude`と、`CODEX_HOME`が空のときは`~/.codex`を参照する。

WI画面で状態が`needs-verify`と表示される項目は、反映後の観測だけが残っているため、観測できる時刻まで`inbox`へ戻されたAWIである。保存状態は`inbox`のままで、状態フィルターの`inbox`にも含まれ、次の`agent-toolkit:process-wi`が観測から再開する。

`cooldown`は冷却期限までは自動処理で次に選ばれる対象にならないinboxのAWI・UWIを示す。バッジはinboxと同じ青色で、マウスを乗せるとJSTの解除日時を確認できる。needs-verifyにも当たる項目ではcooldownを優先し、期限を迎えるとneeds-verifyへ、通常の項目はinboxへ戻る。画面を開いたままでも表示は更新される。保存状態はinboxのままで、状態フィルター・件数・操作の可否も保存状態に従う。holdなどinbox以外の項目は、冷却期限があっても元の状態を表示する。

セッション画面を開いたままでも、ローカルと設定済みリモートホストで新しいセッションの記録が保存されると一覧へ自動で加わり、表示中の記録へ追記されたイベントも再読み込みせずに右ペインへ現れる。右ペインは記録の先頭100件を描画し、末尾までスクロールすると続きを自動で描画して、記録の最後のイベントまで読み進められる。右ペインの更新では、開いたイベント、描画した件数およびスクロール位置を保つ。末尾を表示している間は追記に合わせて末尾へ進み、それより上を読んでいる間は位置を保ち、末尾まで読み進めた時点で追記したイベントが現れる。
一覧への掲載は、記録にユーザーのロールを持つ行が1件でもあるかで決める。ユーザーのロールを持つ行が無い記録（Codexを起動しただけで入力しなかった記録など）は一覧に出ず、その行が保存されると一覧へ加わる。実行環境の挿入本文だけを持つ記録は一覧に残るが、最初の発話の検索値は空になる。リモートホストの自動更新には、リモートホスト側のdotfilesの更新が必要である。

### エージェント由来AWIへのユーザーコメント

`atk serve`の詳細画面では、inboxまたはholdにあるエージェント由来のawiにユーザーコメントの編集操作が表示される。エージェント由来とは、frontmatterの`source`が設定されており、その値が`human`でないものを指す。
コメントがない場合は末尾の`## ユーザーコメント`節へ追記し、既存のコメントを保存すると同じ節だけを置換する。
コメント欄を空にして保存すると、`## ユーザーコメント`節を削除する。節がない項目で空のまま保存した場合は本文を変更しない。
コードフェンス外のH2を含めるコメント、同名節が複数ある本文、末尾以外に予約節がある本文は保存できない。
processing、UWI、終端項目および人間由来の項目では操作を使用できない。

ユーザーコメントに操作、対象および範囲を明記すると、その範囲の外部操作に対する承認として処理される。
一般的な「進めて」だけでは外部操作の範囲が確定しないため、実行してよい操作を具体的に記載する。

### UWIへの回答と状態確認

未回答UWIは`atk wi answer`で順に確認して回答できる。ファイル名と回答を指定する場合は次の形式を使う。

```bash
atk wi answer <UWIファイル名> '<回答本文>'
```

回答後はUWIが先に終端し、そのUWIを待っていたAWIが次回の処理対象へ戻る。1つのAWIを複数のUWIが待つ場合は、全てのUWIへ回答した後の処理で戻る。
処理中の`agent-toolkit:process-wi`のセッションへ「UWI回答した」のように回答を告げると、そのUWIと待っていたAWIを同じ実行で処理する。告げていない回答は次回の処理で取り込まれる。
現在の項目は`atk wi list --status=active`で確認できる。
自動処理へ渡せる状態だけを確認する場合は`--status=processable`、未回答UWIだけを確認する場合は`--type=uwi --answered=no`を指定する。
対象リポジトリはカレントディレクトリが属するリポジトリとなる。全てのリポジトリの項目を確認する場合は`--target-repo=all`を指定する。

## atkコマンドのPATH設定

`agent-toolkit`プラグインは`atk`ラッパースクリプトを`agent-toolkit/bin/atk`に配置する。
Claude Codeがマーケットプレイス経由でインストールした実体は
`~/.claude/plugins/cache/<marketplace-name>/agent-toolkit/<version>/bin/atk`にある。
バージョン部は更新ごとに変わる。追随処理は`install-claude.sh`（Linux/macOS）または`install-claude.ps1`（Windows）で自動化する。

- Linux/macOS: `~/.local/bin/atk`に最新バージョンを動的解決するラッパーを配置する
- Windows: `~/.local/bin/atk.cmd`に同等のバッチラッパーを配置する
- いずれも`~/.local/bin`がPATHに含まれていない環境では警告を表示する

dotfilesユーザーは`chezmoi apply`で`~/dotfiles/agent-toolkit/bin`がPATHへ自動配置されるため、上記スクリプトの実行は不要。

## 構成と機能

### 常時有効な仕組み

ルールファイル（`~/.claude/rules/agent-toolkit/`配下）は自動ロードされる。
`01-agent.md`が基本原則・運用方針・言語表現・検証とコミットの流れを提供する。
ルールファイルには、メインエージェント、サブエージェントおよび委譲先の全てへ適用する条文だけを置く。
メインエージェントだけに適用する条文は`share/rules-main.md`と`share/rules-main.claude-code.md`、サブエージェントと委譲先だけに適用する条文は`share/rules-subagent.md`に置き、フックと`agents_server`が起動時に文脈へ追加する。
文体の核はJIS規格・公的な標準仕様書のスタイルとし、
対話型UI向けの敬体はNHKの案内放送原稿のスタイルを例外として割り当てる。

agent-toolkitプラグインはpyfltrのMCPサーバーを同梱する。
プラグインを導入するとClaude CodeとCodexの双方で自動的に利用可能になり、
lintやテストの実行・横断検索・横断置換・実行履歴の参照をシェルを経由せずに呼び出せる。
サーバーは`uvx`でpyfltrを取得して起動するため、pyfltr自体の事前インストールは要らない。

agent-toolkitは以下のフックを常時有効化する。
識別子はイベント名と処理名の組で示し、`plugin`はagent-toolkitプラグインの配布分、
`個人設定`はdotfilesユーザーの`~/.claude/settings.json`へ配布される分を指す。
Codex欄の「対応」「部分対応」「非対応」は、Codex 0.154.0の実機検証で確認した範囲を示す。

| フック識別子 | 観測できる働き | Claude対応状況 | Codex対応状況 |
| --- | --- | --- | --- |
| plugin `PreToolUse/pretooluse` | 元へ戻せない結果を生む操作と、入力から機械的に判定できる明らかな誤り（編集内容の文字化け、lockfileの直接編集、atkの出力のパイプとリダイレクトなど）を実行前に警告または遮断する。commit本文を確定でき、2行目が空行でない場合は遮断し、件名の直後へ空行を加える操作を案内する。commitの帰属行（`Co-Authored-By`）はエージェントが書き、このフックが実行中のモデルと推論量に一致するかを確かめる | 対応 | 部分対応 |
| plugin `PostToolUse/posttooluse` | 成功したツール実行の結果を記録し、計画ファイルの書き込み後に計画構造の自動チェックを案内する。メインの成功した前景Bashのcommit・push後にはcompletion-reportの起動条件を通知する。そのセッションが投入したUWIへの回答を通知し、上限を超えて退避されたシェル出力を保存先と次の操作を示す本文へ置き換える | 対応 | 部分対応 |
| plugin `SessionStart/rules_context` | セッションの開始、再開、`/clear`および会話圧縮の後に、メインエージェントだけに適用する条文を文脈へ追加する | 対応 | 対応 |
| plugin `SubagentStart/rules_context` | サブエージェントの起動時に、サブエージェントと委譲先だけに適用する条文を文脈へ追加する | 対応 | 対応 |
| plugin `SubagentStop/subagent_stop_advisor` | 空の完了報告での終了をブロックする | 対応 | 対応 |
| plugin `SessionEnd/session_end_cleanup` | 期限を過ぎたセッション状態を回収する | 対応 | 対応 |
| plugin `Stop/stop` | 報告が足りないまま作業を終えようとすると、足りない報告段階と報告本文の不備を示して同じターンを続けさせる。委譲先や待機コマンドを待つ間は終了を許す | 対応 | 対応 |
| plugin `UserPromptSubmit/user_prompt_submit` | 発話の内容を現物で確かめる手順や、発話に応じて適用する規範の所在を示す注記を返す | 対応 | 対応 |
| plugin `PermissionRequest/permissionrequest_codex` | BashからのCodex起動条件を検証する | 非対応 | 対応 |
| plugin `PermissionRequest/permissionrequest` | 全ツールの確認ダイアログを自動許可し、許可した要求をログへ記録する | 対応 | 非対応 |
| plugin `PostToolUseFailure/posttooluse` | Bashの背景実行が失敗した場合も、そのタスクをバックグラウンドタスクの記録へ残す | 対応 | 非対応 |
| plugin `PermissionDenied/posttooluse` | 許可拒否時に状態を変更せず終了する | 対応 | 非対応 |
| plugin `StopFailure/stopfailure_notifier` | APIエラーでターンが終わったことをベルとデスクトップ通知で伝える | 対応 | 非対応 |
| 個人設定 `PreToolUse/pretooluse` | dotfilesの配布元ファイルと個人の命名規約に基づき、編集前にチェックする | 対応 | 非対応 |

各フックの判定条件と、Codexでの対応範囲の詳細は[design-hooks.md](../development/design-hooks.md)「フックごとの処理とCodexの対応範囲」を参照。

Claude CodeとCodexでは、`git rev-parse --short HEAD HEAD~1`のように、`--short`や`--verify`の後へリビジョンを複数並べると、PreToolUseが実行前に遮断する。通知に従い、同じオプションを付けたままリビジョンを1つずつ実行する。

Claude CodeのPreToolUseは、エージェント向け文書の編集と、書込先を確定できるBash操作の前に、未起動のwriting-standardsを文脈ごとに1回案内する。親子の起動済み記録は分け、Codexはこの警告と記録の対象から外す。
commit・push後のPostToolUseの案内は、成果報告へ進むメインへcompletion-reportの適用条件を示す。Claude CodeではSkillの起動、Codexでは通知されたSKILL.mdの読取へ進む。失敗・背景・dry-run・help・委譲先は対象外で、後続の該当操作にも案内する。

plugin `PreToolUse/pretooluse`がatkの呼び出しを遮断するのは、静的に分かるatkの実行位置と出力の接続先に限る。
検索語やheredoc本文に現れたatkと、ホストが管理する背景実行は遮断しない。ただし区切り語を引用しないheredocの本文にあるコマンド置換は、`bash`が実行するため置換の中身によらず遮断する。
この限定と、一般の検証コマンドの出力切り詰めチェックを復元しなかった理由は、[フックの責務境界](../development/design-hooks.md#フックの責務境界)を参照。
遮断されたときは、次の操作で同じ目的を達成できる。

- `atk agents wait`はシェルの`&`と標準出力の破棄を外して単独で発行する。
  Claude Codeで背景で待つ場合はBashの`run_in_background`を使い、返されたtask識別子で結果を受領する。
- atkの出力はパイプにもリダイレクトにもつながず、単独で呼び出す。出力量はサブコマンドの対象限定で減らし、生成側が返す標準出力・標準エラーの保存先から全量を読む。エージェント環境で量によらず保存されるのは、`atk agents wait`の回収結果と複数件の`atk wi show`、`atk run-script session-review-evidence`の`--user-events`であり、短い単発の照会は直接表示される。保存後の標準出力には、`atk agents wait`では通知・終端の内訳と直接表示できる量の本文が、`--user-events`では発話ごとの記録位置と本文の冒頭を示す`発話:`行が続く。表記診断の詳細は`標準エラー保存先:`が示す別のファイルに保持される。人の端末は直接表示と各ストリームのリダイレクトを使える。
  保存した本文の選別は別の呼び出しで行う。

Codexの`SessionStart`は`startup`・`resume`・`clear`・`compact`の全てで条文を追加し、`compact`では`QUALITY_CHECKPOINT_NOTICE`も追加する。
pluginをインストールまたは更新した後は、Codexの`/hooks`で、導入済みagent-toolkit pluginをsourceとする9イベントの定義を確認して信頼する。
登録集合は`SessionStart`、`SubagentStart`、`PreToolUse`、`PostToolUse`、`PermissionRequest`、`UserPromptSubmit`、`Stop`、`SubagentStop`、`SessionEnd`である。
登録が0件の場合は信頼操作へ進まず、`agent-toolkit-codex/`のmanifest選択を確認する。信頼前は登録済みHookがスキップされるため、条文の追加と圧縮後通知は発火しない。
信頼後に新しいセッションを開始し、最初の応答の前に自動生成通知が現れることを確認する。

### `~/.claude/plans`の計画ファイルと実行レビュー後の`private-notes/plans/`への保存

メインが`agent-toolkit:plan-mode`を起動した場合、新規計画は内部の保存処理を使って`~/.claude/plans`へ1ファイルとして保存される。

```text
~/.claude/plans/dd-HHmm_<日本語の簡潔な名詞>.md
```

`agent-toolkit:process-wi`のレーンでは`dd-HHmm_process-wi_レーンNN.md`（`NN`は2桁）とする。
`agent-toolkit:single-lane-process`では`dd-HHmm_single-lane-process.md`とし、同じ名前が既にある場合だけ`-<小文字16進数4桁>`が付く。

viewerのパスコピーは選択中の実体を指すため、作業中は`~/.claude/plans/...`、保存後はprivate-notes側の可搬表記を返す。
実装後レビューが収束した後だけ、`~/.claude/plans`からの相対パスを指定して計画バンドルを保存する。

```bash
atk plans commit dd-HHmm_<名詞>.md
```

`atk plans commit`は同じstemの計画、バグ調査ファイルおよびレビュー指摘管理表を、計画ファイルの作成日に対応する`private-notes/plans/yyyy/MM/`へ移動し、対象限定commit・pushの成功後に作業側を回収する。失敗時は作業側を保持する。
pushを行わずローカルcommitまでで止める場合は`--skip-push`を指定する。この場合もローカルcommitの成功後に作業側を回収する。
計画作成基準は`agent-toolkit:plan-mode`の`plan-file-standards.md`、可搬参照の書式は同スキルの`plan-file-storage.md`が定める。

計画ファイルの固定見出し、進捗ログおよびレビュー指摘管理表の規則は`agent-toolkit:plan-mode`が定め、計画ファイルの書き込み時に上表の`PreToolUse`・`PostToolUse`が構造をチェックする。

### オンデマンドのスキル

該当作業に着手したときエージェントが起動する。Claude Codeで`/`を付けて手動起動できるのは、`user-invocable: false`を持たないスキルだけである。Codexの`$`による手動起動はこの設定の対象外とする。

- `agent-toolkit:writing-standards`: ドキュメントとコード内コメント、コードとテストコード、エージェント向け文書の品質基準。成果物の種別ごとに`references/`配下の資料を読み分ける
- `agent-toolkit:refine-prompt`: プロンプトの指摘を独立した実行者から集め、共通の文書基準で改善案を組み立てる
- `agent-toolkit:commit`: git commit作業（通常commit・amend・fixup）の手順とConventional Commits規約
- `agent-toolkit:bugfix`: バグ対応時の2系統4段階の原因分析、類似見直し、対策・横展開・再発防止の判断基準
- `agent-toolkit:delegation`: 起動方式の選択、継続、停滞検知または複数主体調整が必要な高度な委譲の手順。
  自動的に適用せず、対象工程が高度な委譲の条件を持つ場合にだけ読み込む
- `agent-toolkit:plan-mode`: 計画ファイル作成と、実装後の実行レビューを含む実行引き継ぎ
  - 計画確定時は計画構造の自動チェックで固定H2と表、計画メタ情報、見出し階層、参照実在を確認する
  - ユーザー発言は`## 変更履歴`、実装時の進捗は`## 進捗ログ`へ記録する
  - 実行レビューでは計画ファイルと同じディレクトリの`<計画stem>.exec-review.tsv`をレビュー担当が作成・更新し、全ラウンドで同じ表を使う
  - バグ対応計画は計画メタ情報の固定記法から判定し、関連WIの`## 原因分析`に記録した原因と調査を参照する。入力WIが無い場合とWIの原因分析を訂正・追加する場合だけ、計画ファイル（バグ）の原因分析表と固定の調査表で両要因を分析し、原因起点の類似見直し、対策・横展開・再発防止を記録する
  - 変更履歴は見出しごとの記録でレビュー指摘をラウンド単位に集約し、指摘原文・個別採否・対応内容はレビュー指摘管理表へ記録し、変更履歴には書かない
  - 進捗ログは日時・完了した工程・結果の3列表で実行工程の作業状況を追跡し、異常終了からの再開に使う
- `agent-toolkit:review-standards`: レビュー担当とレビューイーの判断基準。
  レビュー担当にはコードレビュー・ドキュメントレビューの実施基準を、レビュー指摘、改善提案、ユーザーの割り込み・是正要求と想定外の発見を受領したレビューイーには修正要否の立証、安全な修正、自己点検と公開可能性の検証基準を与える
- `agent-toolkit:wi-standards`: AWIとUWIの本文、由来、状態、承認および投入の共通規範
- `agent-toolkit:workflow-overview`: 対話型、自律型、まとめ処理型をまたぐWI運用の全体像と、運用変更時に確認する主体・操作・適用先
- `agent-toolkit:add-awi-by-user`: ユーザーの要件を対話で確定し、AWIまたはUWIを手動投入する
- `agent-toolkit:single-lane-process`: AWIをレーンへ分けずに、1回の起動で対応する作業ツリーへ実装して終端する。計画を要する項目は同じセッションの中で計画の作成から実装まで進め、複数リポジトリでは計画と実行レビューを対象worktreeごとに分ける
- `agent-toolkit:process-wi`: 選定工程（選定とレーン分け）、レーン工程（並列レーン実行）、公開工程（全レーン後のpush・CI・終了）の3段階でAWIを処理する。広域AWIが独立項目を1つのレーンへ連結する場合は完了見込みを比較し、独立レーンを先行して統合した後、広域AWIの後段レーンを現行HEADから開始する。同じ段階で同じ定義を変更するレーンは並行させない。
  処理中にユーザーが追加を明示したAWIと、処理中のセッションへ回答を告げたUWIは同じ実行へ加える。初期選定中なら候補集合へ合流する。選定後なら追加選定を検収して元の選定結果へ保存する。指示の無い新着はprocess-wiの次の実行で扱う。
  計画を要する通常レーンは1つの計画を使い、レーン担当が計画・実装・変更範囲の検証を続けて行った後、実行レビューを要求する。
  計画を省くレーンはWIの要求と完成条件を基準に実装・レビューする。計画がある場合は自動チェックでWI集合の一致と人間由来行の根拠欄を確認する。人間由来の縮小、エージェント向け文書の編集、明示禁止条件のいずれかがある場合だけメインの判断を待つ。
  実装不要またはholdの項目は計画やworktreeを作成せず終端する。要求の不採用と既存の変更による充足は計画工程で確定する。
  前回の実行で中断したAWIは既存の計画の続きから再開し、新しく選ばれたAWIとは別に扱う。終端しなかったAWIの計画も実行の終わりに保存され、次の実行で作業場所へ戻される。
  各レーンはffマージ直後に`adopt`と後始末を完了し、固有指示で延期した項目だけを全レーン後の終端工程で処理する。反映後の新プロセスでしか完成条件を観測できない場合は、観測可能なら終端し、できなければ実装済み計画の再開位置を残して次の実行で観測する
- `agent-toolkit:pytilpack-usage`: pytilpackのモジュール構成とAPI参照のリファレンス
- `agent-toolkit:gitlab-ci-usage`: `.gitlab-ci.yml`編集時のキーワード仕様・典型パターンのリファレンス
- `atk agents-exit-session`: ユーザー指示時または自律モードのスキル完遂時に、現在のClaude CodeまたはCodexの対話セッションへ終了を要求するCLI。管理設定の`CLAUDE_CODE_ENABLE_FUNCTION_HOOKS=1`が有効でagent-toolkitのFunction hooks moduleを読み込んだClaude Codeでは、ターンの完了後に`/exit`を実行してセッション記録の末尾まで残す。moduleが読み込まれていないClaude CodeとCodexでは従来のプロセス停止方式を使う。
  （本体を一意に識別できない実行環境では停止せず、終了理由と対話CLIの終了案内を最終応答としてターンを完了する）
- `compact_conversation`: Function hooks moduleを読み込んだClaude Codeのメインが会話圧縮を予約するツール。完全名は`mcp__agent-toolkit__compact_conversation`で、任意の文字列`instructions`に保持する情報や再開指示を渡せる。省略すると追加指示なしで圧縮する。対話UIと非対話の`claude -p`に対応し、Codexと委譲先では使わない。受付応答は圧縮完了を示さない。予約後は現在のターンを終え、ホストの圧縮成功表示を確認する。未完了の予約を重ねても実行は増えず、成功・失敗後は再予約できる。失敗は同じ会話へ原因と再予約可能である旨が通知される。バックグラウンドタスクを待つ場合は予約後に背景待機を開始してターンを終え、通知後に継続する
- `send_to_user`: 管理設定の`CLAUDE_CODE_ENABLE_FUNCTION_HOOKS=1`が有効でagent-toolkitのFunction hooks moduleを読み込んだClaude Codeで、メインが使えるツール。途中報告と最後の回答を運び、画面へMarkdownとして表示する。完全名は`mcp__agent-toolkit__send_to_user`で、初回から読み込む定義を登録する。遅延一覧にだけ現れる場合は`ToolSearch`の`select:mcp__agent-toolkit__send_to_user`で読み込む。ツール呼び出しより前の地の文は要約に置き換わることがあるため、このツールで送る。確認質問には既存の質問手段を使う。moduleを読み込まないClaude CodeとCodexでは、ターンを終える本文へ書く
- `agent-toolkit:completion-report`: メインの作業完了時に、成果と振り返り結果を固定形式で報告する。対策のAWIを投入する場合は`## 振り返り結果の予告`で予告し、投入後にAWI投入結果を報告する。投入が無い場合は振り返り結果を報告する。最後の報告だけを「以上で、このセッションの作業は全て終わりました」で結ぶ。途中の回答や割り込みへの返答の後に残りの報告段階へ進まない停止と、報告本文に不備がある停止はStopフックが遮断して戻す。報告本文の不備は、対策行に根拠（AWIのファイル名・投入予定・同一セッションの実装）が無いこと、見送りの判定済み行の根拠の欠落か未確定、未確定行の照会・再現・残る理由の欠落、AWI投入結果報告に残った投入予定の4つである。ユーザーの中止・置換の指示は、その指示が作用する作業だけを止める
- `agent-toolkit:export-session`: `atk agents logs`でClaude CodeとCodexの記録をmarkdownへ出力し、一括変換も行う
- `agent-toolkit:session-review`: セッションで交わされた会話の流れと問題候補を調べ、原因と恒久対策を確定して、対策を作業依頼（AWI）として投入する。手動または`agent-toolkit:completion-report`から起動し、メインが同じセッション内で分析する

## 更新と削除

ルールファイル・プラグインとも頻繁に更新されるため、定期的に最新化する。

「ツールキットのインストール」のワンライナーを再実行すると更新される。
dotfiles（chezmoi）管理下のマシンでは`chezmoi apply`を実行しても更新できる。
いずれの単体インストーラーも初回導入専用ではない。`agents_server`はClaude CodeまたはCodex pluginが
セッション単位でstdioプロセスとして所有するため、共有daemonの再起動は行わない。
Codex plugin本体の更新で既存セッションへ反映できない場合は、Codex pluginの案内に従い、
進行中の作業を回収してから新しいセッションを開始する。

アンインストール時は双方のプラグインを除去し、
`~/.claude/rules/agent-toolkit/`と生成された`atk`ラッパーを削除する。plugin更新だけでは旧Codex MCPを
自動削除しない。単体インストーラーまたは`chezmoi apply`後処理では完全一致した旧User scope定義だけを移行する。

自動登録に失敗した場合は、設定ファイルのJSON構造を修復してから次の診断・復旧コマンドを実行する。

```bash
claude mcp get codex
claude mcp remove --scope user codex
```

上記の削除は、`claude mcp get codex`で旧`codex mcp-server`定義を確認した場合だけ実行する。
custom定義を削除する場合はユーザーが内容を確認してから明示的に実行する。2時間のtimeoutは新しい設定へ引き継がない。

```bash
chezmoi apply
```

Codex単独セッションとdotfiles固有の配布内容については[Codex利用ガイド](codex-guide.md)を参照してください。
