# agent-toolkit導入ガイド（Agent Plugins・Claude Code・Codex）

本書はagent-toolkitの詳細設定・操作とdotfiles固有の運用を扱う。
概要・導入・初回確認は[agent-toolkitのREADME](../../agent-toolkit/README.md)を参照。

## コンセプト

用途、品質基準との責務分担、AWI・UWIの意味は[README](../../agent-toolkit/README.md)を参照。
常時規範とスキル・フック・MCPを併用する構成と、ホストごとの機能範囲は[対応ホストと機能](../../agent-toolkit/README.md#対応ホストと機能)にまとめている。

## 前提条件

単体導入に必要なCLIは[READMEの前提条件](../../agent-toolkit/README.md#前提条件)を参照。
dotfilesユーザーでは、`chezmoi apply`後の処理がClaude Code（Anthropic公式ネイティブ版）とCodex CLIを導入・更新する。
WindowsでClaude CodeやCodexが実行中の場合は停止せず、更新を次回へ延期する。

## クイックスタート

### ツールキットのインストール

[READMEの導入](../../agent-toolkit/README.md#導入)にある統合インストーラーを実行し、[導入確認](../../agent-toolkit/README.md#導入確認)と[最初の操作](../../agent-toolkit/README.md#最初の操作)へ進む。
dotfilesユーザーは`chezmoi apply`で同じプラグインを設定する。

## 詳細設定と操作

- [環境別の導入](claude-code-installation.md)
- [設定確認と工程別モデル設定](claude-code-models.md)
- [接続の使い分け](claude-code-connections.md)
- [接続の設定例](claude-code-connection-examples.md)
- [Claude Codeの推奨設定](claude-code-settings.md)
- [推奨ワークフロー](claude-code-workflows.md)
- [WIの登録と回答](claude-code-wi.md)
- [atk serveの画面と選定設定](claude-code-serve.md)

## atkコマンドのPATH設定

インストーラーで導入するユーザーは[READMEの導入](../../agent-toolkit/README.md#導入)を参照。
dotfilesユーザーは`chezmoi apply`で`~/dotfiles/agent-toolkit/scripts`がPATHへ追加される。

## 構成と機能

### ModsでClaude Codeを拡張する

Modsは画面やツール呼び出しを拡張するプラグインである。
既存のstatuslineで足りる状態表示、`atk agents`の委譲操作、[費用集計](claude-code-models.md#費用とレビュー指標の集計)はそのまま使い、独自のペインや操作が必要な場合にModを検討する。
作成は対話セッションで`/plugin-authoring`を起動して希望を伝える。
APIと[公式サンプル](https://code.claude.com/docs/en/plugins/mods/overview#try-a-sample-mod)、保存・再利用の手順は[公式作成ガイド](https://code.claude.com/docs/en/plugins/mods/create)を参照。

試す前にコードを確認する。
Modは実行中のOSアカウントの権限で動くため、信頼できるものだけを読み込む。
読み込み確認・停止・再読み込みは[設定ガイド](claude-code-settings.md#modsの確認と停止)を参照。
対話CLIの画面と、画面を描画しない`claude -p`・Agent SDKでは利用範囲が異なる（[公式の対応範囲](https://code.claude.com/docs/en/plugins/mods/overview#where-mods-run)）。
プラグインを読み込む非対話セッションでもhooksは実行されるが、ペインなどの表示は利用できない。

### 常時有効な仕組み

規範・フック・MCPの役割は[READMEの構成と機能](../../agent-toolkit/README.md#構成と機能)を参照。
各フックの判定条件とCodexでの対応範囲は[design-hooks-host-support.md](../development/design-hooks-host-support.md#フックごとの処理とcodexの対応範囲)、Codexでの信頼操作は[Codex利用ガイド](codex-guide.md#フックの信頼確認)を参照。

### 手動で起動する主なスキル

最初に使う登録・処理の操作は[READMEの最初の操作](../../agent-toolkit/README.md#最初の操作)、運用ごとの例は[推奨ワークフロー](claude-code-workflows.md#推奨ワークフロー)を参照。
Claude Codeでは`/`、Codexでは`$`を付けて起動する。

- `agent-toolkit:atk-mode <作業> [入力]`: 作業の入口。
  `add-awi-by-user`、`process-wi`、`review-session`、`export-session`を作業の名前として渡せる。
  作業ごとのスキルは順に入口の配下へ移すため、移行が済むまでは今の名前での起動も使える
- `agent-toolkit:review-session`: 現在のセッションを振り返り、原因と恒久対策をAWIとして登録する。
  引数で着眼点を追加できる
- `agent-toolkit:review-session batch`: 振り返り待ちのセッションをまとめて分析する。
  既定で自動起動し、`atk config set session_review_auto_start false`で停止できる
- `agent-toolkit:export-session`: Claude CodeとCodexのセッション記録をMarkdownへ出力する

## 更新と削除

一般の更新・削除は[READMEの更新と削除](../../agent-toolkit/README.md#更新と削除)を参照。
dotfiles管理下のマシンでは`chezmoi apply`でも更新できる。
ここでは既存環境の移行時に必要な補足を扱う。

接続ごとに管理する旧版から共有管理サービスへ初めて更新する場合、起動中のClaude Code・Codexの会話は終了まで旧接続を使う。
旧版には状態を引き継ぐ通信機能が無いため、会話を強制終了せず、次の通常起動から新版へ移る。
両ホストで旧stdio停止後の自動再接続は確認していないため、初回移行のために旧接続を停止しない。
この制約は初回の移行に限られる。
共有管理サービス導入後の更新では、進行中のターンが終了した後、その会話の次の入力を新版が処理する。
切替のための入力や追加のモデル呼び出しは行わない。

`agents_server`のClaude委譲先は、有効なプラグインと設定ファイルの変更を監視する。
更新を検知すると進行中のturnの終了を待ち、同じ接続でプラグインを再読込してから次の入力を処理する。
再読込に失敗した場合は診断ログを確認し、表示されたプラグインや設定の問題を解消する。
この自動反映は`agents_server`のClaude委譲先が対象で、ユーザーが直接起動したClaude Codeの会話は含まない。

Codexの会話から実行する`update-dotfiles`は更新処理を独立したjobとして実行する。
更新後の結果取得とdaemonの反映条件は[Codex利用ガイド](codex-guide.md#プラグイン更新の反映)を参照。

過去の手順で登録したUser scopeのMCP定義`codex`（`codex mcp-server`）は、インストーラーと`chezmoi apply`が自動で除去する。
自動で除去できなかった場合は、次のコマンドで定義を確認し、旧`codex mcp-server`定義だけを削除する。
独自に追加した定義は内容を確認してから削除する。

```bash
claude mcp get codex
claude mcp remove --scope user codex
```

Codex単独セッションとdotfiles固有の配布内容については[Codex利用ガイド](codex-guide.md)を参照。

## 品質基準だけを導入する

品質スキルの単独導入と適用条件は[agent-basicsのREADME](../../agent-basics/README.md)を参照。
