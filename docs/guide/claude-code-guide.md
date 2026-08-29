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
agent-toolkitプラグイン、共有`agents_server` MCP、`atk`ラッパーが設定される。
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

新しいMCPサーバーはplugin内の共有`agents_server`であり、User scopeへ登録しない。
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

`claude mcp get codex`は旧User scope定義の有無を確認する診断である。`agents_server` MCPは
Claude CodeまたはCodex pluginから読み込まれるため、`codex plugin list`と`claude plugin list`で各pluginの状態を確認する。

委譲は`start`・`wait`・`send_message`・`kill`の4ツールで行う。`start`は`engine`（`codex`または`claude`）、
`prompt`、既存ディレクトリの絶対`cwd`、必要に応じて`model`と`effort`を受け取り、完了を待たず`session_id`を返す。
`wait`はtimeoutまで状態を観測し、終端時は結果本文を同じ応答から取得する。通常の既定は270秒であり、固有のtimeout要件がなければ引数を省略して通常既定を使う。`timeout=0`は待機せず現状態を返し、
終端結果の再取得も同じ本文を返す。`send_message(session_id, prompt, timeout=270)`は実行中turnへsteerし、終端済みturnでは結果回収を前提に
同じsessionでreplyを開始する。send_messageの通常の既定は270秒であり、固有のtimeout要件がなければ引数を省略して通常既定を使う。timeoutは追加指示の配送結果が確定するまでの待機上限であり、委譲先の応答生成の完了は待たない。`0`以下は受理しない。上限到達時は配送の成否が確定しないため`wait`で状態を確認する。`kill(session_id, timeout=270)`は実行中turnだけを中断する。killの通常の既定は270秒であり、固有のtimeout要件がなければ引数を省略して通常既定を使う。`timeout=0`は要求配送後の現状態を返す。`timeout=0`でも中断要求の配送と`turn_control_lock`の取得には270秒の上限を適用し、終端は待たない。上限に達した場合は、中断要求が未配送か配送の成否が確定しないかを区別した`TimeoutError`を返し、sessionとbackend processは破棄しない。
正のtimeoutは終端結果を待つが、timeout超過時もsessionを破棄しないため、`wait`で状態を確認し、終端後は`send_message`で同じsessionを再開できる。終端結果の保持期限30分を過ぎた場合と、sessionを所有する実行主体が終了した場合のいずれも、同じ`send_message`が保持済みの実効条件から会話を暗黙に再開する。
成功応答の`kill_requested`は中断要求の受理事実を示し、自然終端を中断成功へ置き換えない。MCP内部で承認・一覧操作は公開しない。

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
フィードバックは人間向けの作業要求であり、実装を伴う場合は変更量にかかわらず計画とレビューを経る。

| 登録経路 | 選ぶ場面 |
| --- | --- |
| `atk mq add` | 依頼内容が既に固まっており、本文をそのまま登録したい |
| `atk mq add --batch` | 別環境の`atk mq show --all`の出力を複数件まとめて移行・復元したい |
| `/agent-toolkit:add-feedback` | 依頼内容を対話で確定してから登録したい |
| `/agent-toolkit:plan-and-add-feedback` | 計画の作成とレビューまで先に済ませ、実装だけを自律実行へ渡したい |

### 通常型ファイル名を指定した計画作成

通常型フィードバックのファイル名を1件以上指定するときは、次の形式で計画作成を依頼する。

```text
/agent-toolkit:plan-and-add-feedback <通常型フィードバックファイル名>...
```

指定した項目は、計画調査の前に同一対象リポジトリの`planning`状態へ一括移動する。
`planning`状態の項目は一覧と詳細で確認できるが、計画が完成するまで`process-loop`の実装対象にならない。
すべての入力はファイル名昇順で1つの計画へ統合される。
レビュー収束後は最古の項目が計画型へ変換されて`inbox`へ移り、残りの統合元が`rm --force`で除去される。
入力が1件だけの場合はrmを呼ばず、計画型のinbox項目だけを残す。
計画型inbox項目を実装する場合は、明示的な`atk mq start-processing`又は`process-loop`が処理を開始する。

計画型編集前に中断した場合は、同じ入力で再開するか、`atk mq return-to-inbox <filename>... --state=planning`でinboxへ戻す。
計画型への変換開始後は最古の計画型inbox項目を移動せず、保存済みの状態から滞留commitのpush又は残りの統合元の除去を再開する。
ファイル名を指定しない自然言語の依頼は、従来どおり新しい計画型フィードバックとして登録される。

`atk mq reject`は、process-loopが要求の全てを不採用と確定した場合だけに使用する。
採用内容を統合した元項目又は別リポジトリへ移管した元項目は、統合先又は移管先をnoteへ記録して`atk mq rm --force`で除去する。
技術的な失敗、入力不足及び外部条件待ちは、不採用にせずTBD依存を持つactive項目として保持する。

### session-reviewのユーザーコメント

`atk serve`の詳細画面では、inboxにある`source: session-review`のfeedbackだけにユーザーコメントの編集操作が表示される。
コメントがない場合は末尾の`## ユーザーコメント`節へ追記し、既存のコメントを保存すると同じ節だけを置換する。
空のコメントによる節削除はできない。
コメント本文にコードフェンス外のH2を含める入力、同名節が複数ある入力又は末尾でない入力は保存できない。
planning、processing、TBD、終端項目及び別sourceの項目では操作を使用できない。

ユーザーコメントに操作、対象及び範囲を明記すると、その範囲の外部操作に対する承認として処理される。
一般的な「進めて」だけでは外部操作の範囲が確定しないため、実行してよい操作を具体的に記載する。

### TBDへの回答と状態確認

未回答TBDは`atk mq answer`で順に確認して回答できる。ファイル名と回答を指定する場合は次の形式を使う。

```bash
atk mq answer <TBDファイル名> '<回答本文>' --target-repo=<対象リポジトリ>
```

回答後はTBDが先に終端し、そのTBDを待っていたフィードバックが次回の処理対象へ戻る。
現在の項目は`atk mq list --status=active --target-repo=<対象リポジトリ>`で確認できる。
自動処理へ渡せる状態だけを確認する場合は`--status=processable`、未回答TBDだけを確認する場合は`--type=tbd --answered=no`を指定する。

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
| plugin `PostToolUse/posttooluse` | 成功したツール実行の観測結果を記録する。編集ファイル・計画ファイルの記録、条件付き禁止形の警告、検証実行・`git log`確認・amend後状態の記録、回答済みTBDの通知を行う | 対応 | 部分対応。成功した編集の対象記録、計画ファイル記録、条件付き禁止形の警告に対応する。新形式計画の2ファイルがそろったwhole-writeでは、非遮断の品質想起通知も返す。シェル実行の終了コードが届かないため、検証実行とgit状態の記録は非対応 |
| plugin `SessionStart/quality_checkpoint` | Codexの圧縮後に品質想起通知を追加する | 非対応。Claude Code向け`hooks.json`へ登録しない | 対応。`source=compact`だけを対象にし、非遮断の追加文脈を返す |
| plugin `SubagentStop/subagent_stop_advisor` | 空の完了報告と英語主体の完了報告での終了をブロックする | 対応 | 対応。空の完了報告のブロックに対応する。言語検査は`reason`の配送先と再提出の成立を確認できないため非対応 |
| plugin `SessionEnd/session_end_cleanup` | 期限を過ぎたセッション状態を回収し、会話破棄時だけ当該セッションの状態を削除する | 対応 | 対応。終了理由が`other`固定のため、期限切れ状態の回収だけを実行する |
| plugin `Stop/autonomous_exit` | process-loop環境で`completion-report`後の`exit-session`呼び出し漏れをblockする | 対応 | 非対応 |
| plugin `UserPromptSubmit/user_prompt_submit` | process modeと計画タイトルに必要な状態だけを記録する | 対応 | 対応 |
| plugin `PermissionRequest/permissionrequest_codex` | BashからのCodex起動条件を検査する | 対応 | 対応 |
| plugin `PermissionRequest/permissionrequest` | コーディングエージェント向け文書や`~/.claude/plans/`への書き込みなど、確認ダイアログを自動許可する | 対応 | 非対応。Claude固有の入力と広い自動許可を前提とし、Codexには限定済みの`permissionrequest_codex`があるため配布しない |
| plugin `SubagentStart/subagent_start_tracker` | 委譲調整役の起動を記録し、SubagentStopの完了判定へ接続する | 対応 | 非対応。Codexの`agent_type`はspawn時のrole名又は`default`であり、現行の公開入力から追跡対象を識別できない |
| plugin `PostToolUseFailure/posttooluse` | ツール失敗時に状態を変更せず終了する | 対応 | 非対応。対応するイベントが存在しない |
| plugin `PermissionDenied/posttooluse` | 許可拒否時に状態を変更せず終了する | 対応 | 非対応。対応するイベントが存在しない |
| plugin `StopFailure/stopfailure_notifier` | APIエラーでのターン終了をベルとデスクトップ通知で知らせ、発生種別をログへ記録する | 対応 | 非対応。対応するイベントが存在しない |
| 個人設定 `PreToolUse/pretooluse` | dotfilesの配布元ファイルと個人の命名規約に基づく編集前検査 | 対応 | 非対応。dotfiles固有の配布構成に依存するため、プラグインへ移さない |
| 個人設定 `PostToolUse/posttooluse` | 参照文書へのReadとスキル起動をセッション状態へ記録する | 対応 | 非対応。同上 |

Codexの`SessionStart`は、`source=compact`に限り品質想起通知へ対応する。`startup`・`resume`・`clear`では通知しない。
pluginをインストールまたは更新した後は、Codexの`/hooks`で、導入済みagent-toolkit pluginをsourceとする`SessionStart(compact)`定義と`quality_checkpoint` commandを確認して信頼する。
信頼前は変更済みHookがスキップされるため、圧縮後通知は発火しない。
信頼後に`/compact`を実行し、次のモデル継続前に自動生成通知が現れることを確認する。

計画ファイルと計画運用に関する検査は、上表の`PreToolUse`・`PostToolUse`が扱う。

- 計画ファイルはメイン側とdetail側の2ファイル構成とする
  メイン側は、実施内容、4種類の由来、採否、非採用理由、フィードバック/TBDのファイル名、利用者発言の逐語変更履歴を検査する
- 新規の固定H2は`## エージェント判断`、`## 変更履歴（計画時）`及び`## 進捗ログ（実行時）`を含む正規順とし、旧見出しは読み取り互換として受理する
  エージェント提案行には判断説明を1対1で対応させ、提案が無い場合は`なし`とする
- detail側は、説明的な名前を持つ実装単位、先行依存、統合順、実装資料、完了条件を検査する
- 新規書式では素材ID、要求ID、履歴ID、独立した除外・保持表、実装単位IDを生成しない
  既存の単一ファイル形式と素材・要求IDを持つ二ファイル形式はwarning付きの読み取り互換として受理する
- バグ対応では分離先のバグ調査ファイルと固定14行表を検査する
  変更対象と変更内容はdetail側へ集約し、計画時の検索結果と文面成果物の修正後全文は計画レビューで具体性を照合する
- 変更対象と変更内容は`実装資料`の変更説明へ集約し、独立した対象ファイル一覧や関連検査を持たない
- 計画変更履歴、実行時進捗ログ及びレビュー指摘管理表は用途別に保持し、行数又はラウンド数を相互照合しない
- 計画レビューは欠落した事実と阻害される判断・検証・成果が対応する指摘だけを受理し、体裁や記述量を指摘理由にしない
- 計画レビューでは、メインセッションが機械チェック・修正系と計画レビュー系へ直接委譲する
- 計画実装では、`plan-impl-executor`がコミット単位ごとの実装担当と単一の読み取り専用実装レビュー担当を管理する。
  各レビューラウンドはラウンド番号と指摘件数だけを返し、第3ラウンド以降はメインがレビュー指摘管理表を確認して介入する。
  `merge_request`の許可後は最後の作業担当が最新ベースへのrebase、必要な競合記録と再レビュー、ffマージ、`adopt`及び後始末までを担当する

このほか、メインエージェント応答の記述言語の警告と、`AskUserQuestion`への縮退誘発フレーズ混入のブロックを
Claude Codeで有効化する。
記述言語の警告は、応答が日本語文字を含まず英単語を2語以上含む記述であるとき、
応答の冒頭が英語の談話標識であるとき、日本語文字の比率が閾値未満のときに返す。
英単語が1語だけの記述は、識別子やコマンド名の単独提示と区別できないため警告しない。
単発の委譲は常設規範の基本委譲契約を適用し、経路選択、継続、停滞検知又は複数主体調整が必要な場合だけ
`agent-toolkit:delegation`の経路固有契約を適用する。

### オンデマンドのスキル

該当作業に着手したとき自動的にロードされる。Claude Codeは`/`、Codexは`$`を付けて手動でも呼び出せる。

- `agent-toolkit:coding-standards`: コードの新規作成・修正・レビュー時の品質基準とテスト方針
- `agent-toolkit:writing-standards`: Markdown・README・技術文書などのドキュメントとコード内コメントの品質基準
- `agent-toolkit:agent-standards`: コーディングエージェント向け文書固有の品質基準
- `agent-toolkit:commit`: git commit作業（通常commit・amend・fixup）の手順とConventional Commits規約
- `agent-toolkit:bugfix`: バグ対応時の原因区分、類似見直し、是正・横展開・再発防止の判断基準
- `agent-toolkit:delegation`: 経路選択、継続、停滞検知又は複数主体調整が必要な高度な委譲の手順。
  自動的に適用せず、対象工程が高度な経路条件を持つ場合にだけ読み込む
- `agent-toolkit:plan-mode`: 計画ファイル作成と、実装後の実装レビューを含む実行引き継ぎ
  - 計画確定時は計画構造検査で固定H2と表、計画メタ情報、見出し階層、参照実在を確認する
  - 計画時の変更履歴は`## 変更履歴（計画時）`、実装時の進捗は`## 進捗ログ（実行時）`へ分離し、旧見出しは読み取り互換とする
  - 計画レビューでは計画stemと同じレビュー表、実装レビューでは専用managed temp領域の`review.tsv`をレビュー担当が作成・更新し、全ラウンドで同じ表を使う
  - 初回起動後の追送利用者発言は起草担当が逐語の真正性を保証し、レビュー担当は本文との対応だけを照合する
  - バグ対応計画は計画メタ情報の固定記法から判定し、バグ調査ファイルの固定14行表で4原因区分、原因起点の類似見直し、是正・横展開・再発防止を記録する
  - 変更履歴は5列表でレビュー指摘を系統・ラウンド単位に集約し、指摘原文・個別採否・対応内容はレビュー指摘管理表を正本とする
  - 進捗ログは日時・完了した工程・結果の3列表で実装工程の作業状況を追跡し、異常終了からの再開に使う
  - 実装単位は成果、commit、対象ファイル集合及び近接検証が独立する場合だけ分割し、判断から一意に導出できる詳細手順は安全性・データ保全・公開契約に必要な場合だけ常設する
- `agent-toolkit:review-standards`: コードレビュー・ドキュメントレビュー実施時の判断基準（レビュー担当側心得）
- `agent-toolkit:reviewee-standards`: レビュー指摘、改善提案、ユーザーの割り込み・是正要求と想定外の発見について、修正要否の立証、安全な修正、自己点検と公開可能性を検証する判断基準
- `agent-toolkit:feedback-standards`: フィードバックとTBDの本文、由来、状態、承認及び投入の共通規範
- `agent-toolkit:add-feedback`: 利用者向け要件を対話で確定し、通常型フィードバック又はTBDを手動投入する
- `agent-toolkit:process-feedbacks`: ①選定とレーン分け、②並列レーン実行、③全レーン後のpush・CI・終了の3段階でフィードバックを処理する。
  計画型は既存計画を、通常型は1レーン1計画を使い、全ての実装要求に計画・計画レビュー・実装・実装レビューを要求する。
  実装不要、既存実装で充足済み、reject又はholdの項目は計画やworktreeを作成せず終端する。
  各レーンはffマージ直後に`adopt`と後始末を完了し、固有指示で延期した項目だけを全レーン後の終端工程で処理する
- `agent-toolkit:plan-and-add-feedback`: 計画作成からレビューまでを実施し、実装の代わりにフィードバック投入で終える運用
- `agent-toolkit:pyfltr-usage`: pyfltrの使い方・出力解釈のリファレンス
- `agent-toolkit:pytilpack-usage`: pytilpackのモジュール構成とAPI参照のリファレンス
- `agent-toolkit:gitlab-ci-usage`: `.gitlab-ci.yml`編集時のキーワード仕様・典型パターンのリファレンス
- `agent-toolkit:shell-exec`: 長出力が予想されるコマンド列をサブエージェントへ委譲し、
  メインへ終了状態と要約だけを返す
- `agent-toolkit:exit-session`: ユーザー指示時又は自律実行スキル完遂時に、一意に識別できるClaude Code若しくはCodexの本体プロセスへ停止を要求する。
  （本体を一意に識別できない実行環境では停止せず、終了理由と対話CLIの終了案内を最終応答としてターンを完了する）
- `agent-toolkit:completion-report`: メインの作業完了時に、成果と振り返り結果を固定形式で1回だけ報告する
- `agent-toolkit:session-review`: 通常の読み取り専用サブエージェントがセッション全体の問題候補を列挙し、メインが列挙証拠から原因と恒久対策を確定する。手動起動又は`completion-report`から起動する

## 更新方法

ルールファイル・プラグインとも頻繁に更新されるため、定期的に最新化する。

「ツールキットのインストール」のワンライナーを再実行すると更新される。
dotfiles（chezmoi）管理下のマシンでは`chezmoi apply`を実行しても更新できる。
いずれの単体インストーラーも初回導入専用ではない。`agents_server`はClaude CodeまたはCodex pluginが
セッション単位でstdioプロセスとして所有するため、共有daemonの再起動は行わない。
Codex plugin本体の更新で既存セッションへ反映できない場合は、Codex pluginの案内に従い、
進行中の作業を回収してから新しいセッションを開始する。
