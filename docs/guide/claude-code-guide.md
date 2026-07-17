# agent-toolkit 利用ガイド

本リポジトリでは、Claude Codeの動作をカスタマイズするスキルなどのセット「agent-toolkit」を提供する。

## コンセプト

1. 標準動作のカスタマイズ: 判断基準が曖昧な場面での事前相談の徹底、lint抑制時のユーザー確認の必須化、
   検証からコミットまでの流れの自動化などClaude Codeの動作を変更する。
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

agent-toolkitはルールファイルとプラグインの2つのコンポーネントで構成される。

- ルールファイル: `~/.claude/rules/agent-toolkit/`に配置されるルールファイル。
  自動読み込みされ、行動原則・運用方針・言語表現などの共通指示を提供する
- プラグイン: Claude Codeのuser scopeにインストールするプラグイン。
  フック・スキルを提供し、場面に応じたオンデマンドの機能拡張を担う

両者は相互依存しており、基本的に同時に導入することを前提とする。

部分的に動作を変えたい場合は、
ユーザー側の`~/.claude/CLAUDE.md`・プロジェクトの`CLAUDE.md`・プロンプトでの指示などで上書きできる。
優先度はルールファイル側に明記している。

## クイックスタート

### 1. uvのインストール

プラグインは[uv](https://docs.astral.sh/uv/)に依存する。
事前にインストールしておく。

- Linux: `curl -fsSL https://astral.sh/uv/install.sh | sh`
- Windows: `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"`

### 2. ツールキットのインストール

先にClaude Codeをインストールしておく。
インストーラーは`claude` CLIの存在を前提としており、未検出時はエラー終了する。

Stopフックが`hookSpecificOutput.additionalContext`を利用するため、Claude Code 2.1.163以上を要求する。
プラグイン単体利用者では非強制の前提条件、dotfiles配布の管理設定では`requiredMinimumVersion`で強制する。

ツールキットのインストールには以下のワンライナーを実行する。

- Linux:

    ```bash
    curl -fsSL https://raw.githubusercontent.com/ak110/dotfiles/master/install-claude.sh | bash
    ```

- Windows:

    ```cmd
    powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://raw.githubusercontent.com/ak110/dotfiles/master/install-claude.ps1 | iex"
    ```

ルールファイルが`~/.claude/rules/agent-toolkit/`へ配置され、
agent-toolkitプラグインがuser scopeへインストール・更新される。
再実行すると最新版へ同期される。

インストール後、非公式のプラグインマーケットプレイスはデフォルトで自動更新が無効のため、初回のみ手動で有効化する。

1. Claude Code内で`/plugin`を実行
2. `Marketplaces`タブで`ak110-dotfiles`を選択
3. `Enable auto-update`を選択

### 3. Claude Codeの推奨設定

以下の設定を適用することを推奨する。

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

claude-plugins-officialから以下を導入する。

- 推奨: `context7`・`typescript-lsp`（`npm install -g typescript-language-server typescript`が必要）
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

`agent-toolkit:plan-mode`スキルはcodex MCPによる計画ファイルレビューを前提とする。
以下のコマンドで登録しておくと、計画ファイル作成時のレビューが自動で利用できる。

```bash
claude mcp add --scope=user codex codex mcp-server
```

codex CLI自体のセットアップは別途実施する。

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

agent-toolkitプラグインは以下のフックを常時有効化する。

- 文字化け（U+FFFD）混入・LF改行のみの`.ps1`への書き込み・自動生成物の手編集をブロック
- シークレットらしき値やホームディレクトリの絶対パスのハードコードを警告・ブロック
- 口語的な日本語表現や営業文書調・宣伝調のフレーズ・主観的修飾語の混入を警告し書き直しを促す
- メインエージェント応答に占める日本語文字の比率が閾値未満のときに警告し、短文ステータス報告の英語化を抑制する
- テスト未実行のままの`git commit`を警告
- `agent-toolkit/`配下のファイルを含む`git commit`で`plugin.json`のversion未変更を警告
- `git log`実行時に`--decorate`オプションを自動挿入する
- `codex exec`実行前に未決事項の確認を促す
- `plan-codex-reviewer`サブエージェントを経ない`mcp__codex__codex`・`mcp__codex__codex-reply`の呼び出しをブロック
- 未コミット変更がある場合のStop時に`git status`をユーザーへ表示
- APIエラー停止後の入力待ち時にツール呼び出しの解析失敗をベルとデスクトップ通知で警告
- APIエラーでのターン終了の発生種別をログへ記録
- plan mode中で最初のツール呼び出しがplan-modeスキル以外の場合に警告
- `AskUserQuestion`の各フィールド（質問本文・ヘッダー・選択肢のラベルや説明）に
  作業量・残コンテキスト・既存パターン踏襲・工程省略宣言などを根拠とした縮退誘発フレーズが含まれる場合にブロック
- plan-modeスキル呼び出し済みのとき、`~/.claude/plans/*.md`の構成崩れと計画ファイル本文
  （`## 調査結果`配下を除く）の絶対行番号の直書きを検査して警告
- 計画ファイルの`## 変更履歴`記載内容と`## 変更内容`側対象ファイル一覧・H3見出しとの対応欠落をブロック
- 計画ファイル編集前の参照ドキュメント
 （`references/textlint-violations.md`・`references/plan-file-guidelines.md`）未読をブロック
- 修正指示やcodexレビュー不合格の多さに応じてCLAUDE.md更新を提案
- Gitワークツリー配下のコーディングエージェント向け文書や`~/.claude/plans/`への書き込み時に確認ダイアログを自動許可

### オンデマンドのスキル

該当作業に着手したとき自動的にロードされる。手動で呼び出すこともできる。

- `/coding-standards`: コードの新規作成・修正・レビュー時の品質基準とテスト方針
- `/writing-standards`: Markdown・README・技術文書などのドキュメントとコード内コメントの品質基準
- `/agent-standards`: コーディングエージェント向け文書固有の品質基準
- `/commit`: git commit作業（通常commit・amend・fixup）の手順とConventional Commits規約
- `/plan-mode`: plan mode開始時・複雑な指示受領時・バグ調査時の計画ファイル作成とcodexレビュー運用
- `/careful-review`: レビューワークフロー
- `/pyfltr-usage`: pyfltrの使い方・出力解釈のリファレンス
- `/pytilpack-usage`: pytilpackのモジュール構成とAPI参照のリファレンス
- `/gitlab-ci-usage`: `.gitlab-ci.yml`編集時のキーワード仕様・典型パターンのリファレンス
- `/export-for-resume`: 決定事項・未決事項・進捗を整理した引き継ぎ用Markdownを出力する
- `/shell-exec`: 複数のシェルコマンド実行を要する定型作業をhaikuのサブエージェントへ委譲する
- `/exit-session`: ユーザー指示時または自律実行スキル完遂時にClaude Codeのセッション自体を終了する

### 明示呼び出し専用のスキル

- `/overhaul-project`: プロジェクト全体の網羅的改善（コード改善・ドキュメント整備・足元整備）
- `/quality-sweep`: `plan-impl-reviewer`観点で既存不良を網羅的に検出し`claude`サブエージェントへ修正を分担委譲する

## 更新方法

ルールファイル・プラグインとも頻繁に更新されるため、定期的に最新化する。

「ツールキットのインストール」のワンライナーを再実行すると更新される。
dotfiles（chezmoi）管理下のマシンでは`chezmoi apply`を実行しても更新できる。
