# agent-toolkit 利用ガイド

本リポジトリでは、Claude Codeの動作をカスタマイズするスキルなどのセット「agent-toolkit」を提供する。

## コンセプト

1. 標準動作のカスタマイズ — 判断基準が曖昧な場面での事前相談の徹底、lint抑制時のユーザー確認の必須化、
   検証からコミットまでの流れの自動化など、Claude Codeの具体的な動作を変更する
2. 品質水準の維持 — コードスタイルや設計が乱れたプロジェクトでは
   LLMも既存コードに引きずられて同水準のコードを生成する（割れ窓理論）。
   各言語のモダンなイディオム・禁止パターン・セキュリティ注意点・テスト方針を明示し、
   プロジェクトの初期状態によらず一定の品質水準を維持する

Anthropic公式のsuperpowersスキルと重複する内容は多いが、
日本語環境での確実なトリガーと大規模開発向けの細かな制御のために独自に作成している。
性質上、頻繁な改訂が発生する。

agent-toolkitはルールファイルとプラグインの2つのコンポーネントで構成される。

- ルールファイル — `~/.claude/rules/agent-toolkit/`に配置されるルールファイル。
  自動読み込みされ、行動原則・運用方針・記述スタイルなどの共通指示を提供する
- プラグイン — Claude Codeのuser scopeにインストールするプラグイン。
  フック・スキルを提供し、場面に応じたオンデマンドの機能拡張を担う

両者は相互依存しており、基本的に同時に導入する前提。

部分的に動作を変えたい場合は、
ユーザー側の`~/.claude/CLAUDE.md`・プロジェクトの`CLAUDE.md`・プロンプトでの指示などで上書きできる。
優先度はルールファイル側に明記している。

## クイックスタート

### 1. uvのインストール

プラグインは[uv](https://docs.astral.sh/uv/)に依存する。
事前にインストールが必要。

- Linux: `curl -fsSL https://astral.sh/uv/install.sh | sh`
- Windows: `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"`

### 2. ツールキットのインストール

先にClaude Codeをインストール。
インストーラーは`claude` CLIの存在を前提としており、未検出時はエラー終了する。

ツールキットのインストールには以下のワンライナーを実行。

- Linux:

    ```bash
    curl -fsSL https://raw.githubusercontent.com/ak110/dotfiles/master/install-claude.sh | bash
    ```

- Windows:

    ```cmd
    powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://raw.githubusercontent.com/ak110/dotfiles/master/install-claude.ps1 | iex
    ```

ルールファイルが`~/.claude/rules/agent-toolkit/`へ配置され、
agent-toolkitプラグインがuser scopeへインストール・更新される。
再実行すると最新版へ同期される。

インストール後、非公式のプラグインマーケットプレイスはデフォルトで自動更新が無効のため、初回のみ手動で有効化が必要。

1. Claude Code内で`/plugin`を実行
2. `Marketplaces`タブで`ak110-dotfiles`を選択
3. `Enable auto-update`を選択

### 3. Claude Codeの推奨設定

以下の設定を適用しておく。

#### `~/.claude/settings.json`

- `autoMemoryEnabled`: `false`（自動メモリー機能を無効化）
- `showClearContextOnPlanAccept`: `true`（plan mode承認時にコンテキストクリアの選択肢を表示）
- `env.CLAUDE_CODE_NO_FLICKER`: `"1"`（画面のちらつきを抑制）
- `permissions`: 許可・拒否するツールやパターンを記述
 （[例](https://github.com/ak110/dotfiles/blob/master/share/claude_settings_json_managed.json)）

#### `/config`コマンド

- `Verbose output`: 有効
- `Default permission mode`: `Plan mode`
- `Language`: `Japanese`

#### `/plugin`コマンド

claude-plugins-officialから以下を導入。

- 推奨: `context7`・`typescript-lsp`
- 任意: `claude-md-management`・`skill-creator`
- 無効: `pyright-lsp`（Claude Codeがインストールを推奨するが誤動作が発生するため、インストール後に`Disable`）

#### VSCode設定（任意）

ターミナル内での右クリックによる意図しない貼り付けを防ぐ場合は、`settings.json`へ以下を追加。

```json
{
  "terminal.integrated.rightClickBehavior": "nothing"
}
```

### 4. codex MCPサーバーのセットアップ（推奨）

`plan-mode`スキルはcodex MCPによる計画ファイルレビューを前提としている。
以下のコマンドで登録しておくと、計画ファイル作成時のレビューが自動で利用できる。

```bash
claude mcp add --scope=user codex codex mcp-server
```

dotfiles利用者は`update-dotfiles`/`chezmoi apply`の後処理で自動登録される（既登録時はスキップ）。
codex CLI自体のセットアップは別途実施。

## 構成と機能

### 常時有効な仕組み

ルールファイル（`~/.claude/rules/agent-toolkit/`配下）は自動ロードされる。
`agent.md`が基本原則・運用方針・検証とコミットの流れを、
`styles.md`が記述スタイル指針を提供する。
`styles.md`の文体の核はJIS規格・公的な標準仕様書のスタイルとし、
対話型UI向けの敬体はNHKの案内放送原稿のスタイルを例外として割り当てる。

agent-toolkitプラグインは以下のフックを常時有効化する。

- 文字化け（U+FFFD）混入・LF改行のみの`.ps1`への書き込み・自動生成物の手編集をブロック
- シークレットらしき値やホームディレクトリの絶対パスのハードコードを警告・ブロック
- 口語的な日本語表現や営業文書調・宣伝調のフレーズ・主観的修飾語の混入を警告し、
  フォーマルな書き言葉への書き直しを促す
- テスト未実行のままの`git commit`を警告
- `git log`に`--decorate`を自動挿入
- `codex exec`実行前に未決事項の確認を促す
- 未コミット変更がある場合のStop時に`git status`をユーザーへ表示
- plan mode中で最初のツール呼び出しがplan-modeスキル以外の場合に警告（セッションあたり1回）
- plan-modeスキル呼び出し済みのとき、`~/.claude/plans/*.md`の構成崩れを検査して警告
- 修正指示やcodexレビュー不合格の多さに応じてCLAUDE.md更新を提案

### オンデマンドのスキル

該当作業に着手したとき自動的にロードされる。手動で呼び出すこともできる。

- `/coding-standards` — コードの新規作成・修正・レビュー時の品質基準とテスト方針
- `/writing-standards` — ドキュメントやClaude Code設定系ファイルの品質基準
- `/plan-mode` — plan mode開始時・複雑な指示受領時・バグ調査時の計画ファイル作成とcodexレビュー運用
- `/careful-impl` — 計画ファイルの`実装方式`が`careful-impl`の場合に呼び出す。
  サブエージェント分散実装と全コミット完了後の集約レビューを行う
- `/tidy-unpushed-commits` — 未プッシュコミットのsquash・reorder・メッセージ書き直し
- `/pyfltr-usage` — pyfltrの使い方・出力解釈のリファレンス
- `/pytilpack-usage` — pytilpackのモジュール構成とAPI参照のリファレンス
- `/gitlab-ci-usage` — `.gitlab-ci.yml`編集時のキーワード仕様・典型パターンのリファレンス
- `/spec-driven` — 大規模な機能追加・改修向けの軽量SDDワークフロー
- `/export-for-resume` — 議論が発散したときの現状スナップショットを出力する

### 明示呼び出し専用のスキル

- `/spec-driven-init` — 既存プロジェクトへのspec-driven導入（現行版ドキュメントの初版起こし）
- `/spec-driven-promote` — 次版ドキュメントの作業版を現行版ドキュメントへマージ統合

## 更新方法

ルールファイル・プラグインとも頻繁に更新されるため、定期的に最新化。

「ツールキットのインストール」のワンライナーを再実行すると更新される。
dotfiles（chezmoi）管理下のマシンでは`chezmoi apply`を実行しても更新できる。
