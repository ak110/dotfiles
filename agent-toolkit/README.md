# agent-toolkit

コーディングエージェントへ作業を依頼し、調査・計画・実装・レビュー・公開まで進めるためのツールキットである。
スキル、行動規範、フック、委譲用の`agents_server` MCP、作業要求を管理する`atk` CLIを提供する。
後で自動処理する作業要求をAWI、ユーザーの回答・判断・操作待ちと結果報告をUWIと呼び、ユーザーの手元のキュー（private-notesリポジトリ）で管理する。

品質判断には[agent-basics](https://github.com/ak110/dotfiles/blob/master/agent-basics/README.md)を使う。
agent-toolkitは認可・WI・委譲・セッション管理・完了報告を担当し、agent-basicsはコード・設計・テスト・レビュー・文章の品質基準を担当する。
Claude Codeでは同じマーケットプレイスのagent-basicsが依存として導入される。Codexでは両プラグインを明示的に導入・更新する。
品質基準だけが必要な場合はagent-basicsを単独で利用できる。

## 対応ホストと機能

| ホスト | 利用できる機能と導入方法 |
| --- | --- |
| ローカルClaude Code・Claude DesktopのCode | 統合インストーラーで規範・スキル・フック・MCP・CLIを設定する |
| Codex | 統合インストーラーでプラグインと共有MCP・CLIを設定する。フックはユーザーによる信頼確認が必要 |
| claude.aiのChat・Claude DesktopのChat | マーケットプレイスまたはZIPからスキルを利用する。CLI・hook・local MCPは使えない |
| Claude DesktopのCowork | プラグインを追加する。hook・local MCPの利用にはホストの対応、uvとネットワーク、組織の許可が必要 |
| Claude Codeクラウド | 専用セットアップと環境変数を設定する。[クラウドの手順](https://github.com/ak110/dotfiles/blob/master/docs/guide/claude-code-guide.md#claude-codeクラウドの環境設定)を参照 |
| Agent Plugins対応ホスト | `plugin.json`を含むパッケージルートを指定し、ホストが対応するスキル・MCPを利用する |
| Antigravity CLI | dotfilesの更新処理が生成版`agent-toolkit-agy`と`agent-basics-agy`を登録する |

Agent Pluginsの可搬な構成はrootのmanifestとスキル・MCP定義である。
ホスト固有のフック、常時規範の配置、依存環境の準備は、対応ホストの導入手順に従う。
プラグインの追加だけで統合インストーラーの全設定が有効になるわけではない。
ClaudeのZIP作成・アップロードと各環境の設定は[詳細ガイド](https://github.com/ak110/dotfiles/blob/master/docs/guide/claude-code-guide.md#claudeの各環境への導入)を参照。

## 前提条件

統合インストーラーを使う場合は、次のCLIを公式手順で導入する。インストーラーはCLI本体を導入しない。

- [Claude Code](https://code.claude.com/docs/en/overview)の`claude`（2.1.163以上）
- [Codex CLI](https://developers.openai.com/codex/cli/)の`codex`
- [uv](https://docs.astral.sh/uv/getting-started/installation/)の`uv`

プラグインとPython依存パッケージの取得にはネットワーク接続が必要である。
委譲先を利用する場合は、使用するClaude・Codexのログインも済ませる。
追加料金の発生するAPI接続を使う場合は、[接続の使い分け](https://github.com/ak110/dotfiles/blob/master/docs/guide/claude-code-guide.md#接続の使い分け)で認証と使用順を設定する。

## 導入

Linuxでは次を実行する。

```bash
curl -fsSL https://raw.githubusercontent.com/ak110/dotfiles/master/install-claude.sh | bash
```

Windowsでは次を実行する。

```cmd
powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://raw.githubusercontent.com/ak110/dotfiles/master/install-claude.ps1 | iex"
```

両インストーラーはClaude CodeとCodexの双方へ、プラグイン、規範、共有`agents_server` MCP、`atk`コマンドを設定する。
Claude Codeでは常時規範を`~/.claude/rules/agent-toolkit/`へ配置し、プラグインと併用する。
前提の3コマンドのいずれかが見つからない場合は、何も書き込まずにエラー終了する。
Codexの既存`~/.codex/AGENTS.md`は保持し、dotfiles固有のグローバル規範と共有リンクは配置しない。
配置とホスト固有の注意は[Codex利用ガイド](https://github.com/ak110/dotfiles/blob/master/docs/guide/codex-guide.md)を参照。

Claude Codeでは初回に`/plugin`の`Marketplaces`タブで`ak110-dotfiles`を選び、`Enable auto-update`を有効にする。
インストーラーは`~/.local/bin/atk`（Windowsでは`atk.cmd`）へラッパーを配置する。
`~/.local/bin`がPATHに含まれないという警告が出た場合は、そのディレクトリをPATHへ追加して新しいターミナルを開く。

## 導入確認

新しいターミナルで次を実行し、両プラグインの導入状態と`atk`の起動を確認する。

```bash
claude plugin list
codex plugin list
atk --help
```

一覧で`agent-toolkit`と`agent-basics`が導入・有効化されていることを確認する。
新しい会話のスキル一覧で`agent-toolkit:process-wi`などを確認し、MCPの接続状態も確認する。
Codexのフックが未信頼・変更済みの場合は、[フックの信頼確認](https://github.com/ak110/dotfiles/blob/master/docs/guide/codex-guide.md#フックの信頼確認)を済ませて新しいセッションを開始する。

## 最初の操作

まず対象リポジトリでエージェントへ目的と要求を直接伝えて作業を依頼できる。
後で処理する要求を登録する場合は、Claude Codeで次を入力する（Codexでは先頭の`/`を`$`へ置き換える）。

```text
/agent-toolkit:add-awi-by-user <依頼内容>
```

登録したAWIを同じリポジトリでまとめて処理する。

```text
/agent-toolkit:process-wi single-lane
```

手元のキューを画面で確認・編集し、UWIへ回答する場合は次を実行し、表示されたURLを開く。終了は`Ctrl+C`で行う。

```bash
atk serve
```

常駐実行、複数リポジトリ、定期実行、CLIからの登録・回答は[推奨ワークフロー](https://github.com/ak110/dotfiles/blob/master/docs/guide/claude-code-guide.md#推奨ワークフロー)を参照。
工程ごとのモデルは[工程別モデル設定](https://github.com/ak110/dotfiles/blob/master/docs/guide/claude-code-guide.md#工程別モデル設定)で変更できる。

## 構成と機能

- [skills/](skills/): WI起草・処理、委譲、commit、振り返り、会話の出力など、作業に応じて起動する手順
- [rules/](rules/): 行動原則と実行運用の常時規範。部分的な変更はユーザー・プロジェクトの規範やプロンプトで指定できる
- [フックの構成](https://github.com/ak110/dotfiles/tree/master/agent-toolkit/hooks): 操作前の警告・遮断、セッション開始時と圧縮後の規範案内、日本語での応答・完了報告の確認など。対応範囲はホストごとに異なる
  - Claude Codeではツールの確認ダイアログを自動で許可し、許可した要求をログへ残す
- `agents_server` MCP: 調査・実装・レビューを別のエージェントへ委譲する
- `pyfltr` MCP: lint・テスト・横断検索・横断置換を実行する。pyfltr自体の事前導入は不要
- `atk`: キュー、設定、委譲先、Web画面などを操作するCLI

警告や遮断が表示された場合は、本文が示す次の操作に従う。
ホストごとのフックの対応範囲は[フックの設計資料](https://github.com/ak110/dotfiles/blob/master/docs/development/design-hooks.md#フックごとの処理とcodexの対応範囲)を参照。

## 更新と削除

統合インストーラーの導入コマンドを再実行すると最新版へ更新される。
実行中のセッションは更新前の版を使い続けるため、更新後は新しいセッションを開始する。
Codexのdaemon再起動を案内された場合は、[プラグイン更新の反映](https://github.com/ak110/dotfiles/blob/master/docs/guide/codex-guide.md#プラグイン更新の反映)に従う。

削除する場合はClaude CodeとCodexのプラグイン管理からagent-toolkitを除去し、`~/.claude/rules/agent-toolkit/`と`~/.local/bin`の`atk`ラッパーを削除する。
agent-basicsは単独利用できるため、引き続き使うかに応じて保持または除去する。
共有MCPの設定を確認し、今回の導入で追加した`agents_server`を除去する。独自に追加した設定と作業記録は保持する。
dotfiles管理下の環境の更新・移行補足は[詳細ガイド](https://github.com/ak110/dotfiles/blob/master/docs/guide/claude-code-guide.md#更新と削除)を参照。

## ライセンス

[MIT License](LICENSE)。更新履歴は[Git履歴](https://github.com/ak110/dotfiles/commits/master/agent-toolkit)を参照。
