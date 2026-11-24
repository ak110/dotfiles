# agent-toolkit導入ガイド（Agent Plugins・Claude Code・Codex）

本書はagent-toolkitの詳細設定・操作とdotfiles固有の運用を扱う。
概要・導入・初回確認は[agent-toolkitのREADME](../../agent-toolkit/README.md)を参照。

## コンセプト

用途、品質基準との責務分担、AWI・UWIの意味は[README](../../agent-toolkit/README.md)を参照。
入口のスキル`atk-mode`と作業ごとのスキル・フック・MCPを併用する構成と、ホストごとの機能範囲は[対応ホストと機能](../../agent-toolkit/README.md#対応ホストと機能)にまとめている。

## 前提条件

単体導入に必要なCLIは[READMEの前提条件](../../agent-toolkit/README.md#前提条件)を参照。
dotfilesユーザーでは、`chezmoi apply`後の処理がClaude Code（Anthropic公式ネイティブ版）とCodex CLIを導入・更新する。
Windowsでは、更新で置き換えるClaude CodeやCodexの実体が実行中の場合は停止せず、更新を次回へ延期する。
対象はClaude Codeの`~/.local/bin/claude.exe`と、公式インストーラーが導入したCodexである。
除去するCodexのmise版が実行中の場合は、mise版の除去だけを次回へ延期する。
デスクトップアプリ、Codexのapp-server daemon、npxで起動した版は実行中でも延期の理由にしない。
延期の表示は、原因のプロセスのPIDと読めた実行パスを示し、実行を確かめたのか、権限が無く読めず否定できないだけなのかを区別する。
除去を延期した場合は、表示されたプロセスが除去する版なら終了してから、表示された削除コマンドをPowerShellで実行して除去する。
除去するまで旧版がPATHで先に見つかる環境では、表示された更新済みの起動先をフルパスで起動する。
旧npm版とその起動ファイルは自動では削除しない。
旧npm版などがPATHで先に見つかる間は、警告に表示された正規の起動先をフルパスで指定する。
旧npm版が不要なら、起動中の旧版を終了し、パッケージ内や同名の起動ファイルから自分で配置・編集したファイルを退避してから、警告の削除コマンドを手動で実行する。
npmの導入先やパッケージ情報（`package.json`）を確認できない警告は削除コマンドを表示しない。
警告が示すnpmや`package.json`を直してから`update-dotfiles`を再実行し、削除コマンドが表示された場合だけ実行する。
本体が見つからない起動ファイルは、不要と確認できたものだけ別の場所へ移す。

## クイックスタート

### ツールキットのインストール

[READMEの導入](../../agent-toolkit/README.md#導入)にある統合インストーラーを実行し、[導入確認](../../agent-toolkit/README.md#導入確認)と[最初の操作](../../agent-toolkit/README.md#最初の操作)へ進む。
dotfilesユーザーは`chezmoi apply`で同じプラグインを設定する。

### Claude Codeのモデル別ランチャー

`opus`、`sonnet`、`fable`、`poteto-mode`は、それぞれ既定のClaudeモデルを選ぶ。
`--model VALUE`または`--model=VALUE`で指定すると、既定のモデルを渡さない。
`fable`のfallbackと`poteto-mode`のプラグイン設定は変わらない。
`opus --help`などはランチャー自身のヘルプを表示する。
本体のヘルプは`claude --help`で表示する。
モデル別ランチャーは、手動で導入した開発機能を保ちながら、claude.aiの自動連携とChrome連携を止める。
引数の先頭に`--full`を置くと、追加で機能を止めずに起動する。

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

セッション開始時と会話の圧縮の後は、フックが入口のスキル`atk-mode`の起動を求める1行を通知する。
エージェントはこの通知を受けて入口と作業ごとの手順書を読み、常時読み込む規範の配置は使わない。
スキル・フック・MCPの役割は[READMEの構成と機能](../../agent-toolkit/README.md#構成と機能)を参照。
各フックの判定条件とCodexでの対応範囲は[design-hooks-host-support.md](../development/design-hooks-host-support.md#フックごとの処理とcodexの対応範囲)、Codexでの信頼操作は[Codex利用ガイド](codex-guide.md#フックの信頼確認)を参照。

### 手動で起動する主なスキル

最初に使う登録・処理の操作は[READMEの最初の操作](../../agent-toolkit/README.md#最初の操作)、運用ごとの例は[推奨ワークフロー](claude-code-workflows.md#推奨ワークフロー)を参照。
Claude Codeでは`/`、Codexでは`$`を付けて起動する。

- `agent-toolkit:atk-mode <作業> [入力]`: 作業の入口。
  `add-awi-by-user`、`implement`、`process-user-comments`、`process-wi`、`review-session`、`export-session`を作業の名前として渡せる。
  AWIの登録は`/agent-toolkit:atk-mode add-awi-by-user <要求>`、AWIの処理は`/agent-toolkit:atk-mode process-wi [single-lane|all-repos]`で起動する
- `agent-toolkit:atk-mode review-session`: 現在のセッションを振り返り、原因と恒久対策をAWIとして登録する。
  後ろに着眼点を追加できる
- `agent-toolkit:atk-mode review-session batch`: 振り返り待ちのセッションをまとめて分析する。
  既定で自動起動し、`atk config set session_review_auto_start false`で停止できる
- `agent-toolkit:atk-mode review-session meta`・`research`: 週次のメタ分析と外部情報の調査。
  日曜14時（ホストの現地時刻）の週枠ごとに1回、`atk serve`の常駐か`atk weekly install`で登録したOSのスケジューラから起動し、取り逃した回はセッションの開始・終了で追う。
  ホストごとに`weekly_meta_auto_start`（初期値true。`session_review_auto_start`がfalseでも止まる）と`weekly_research_auto_start`（初期値false）で切り替え、同じキューを使うホストが複数あれば1台を残して止める。
  外部情報の調査は `atk config repos` の登録先を対象とする。
  起動するホストでは、そのうち1件以上に実在するGit作業ツリーを登録する。
  Xの購読はWIを保存するprivate-notesの `research-subscriptions.json` にあり、同じキューを使うホスト間で同期する。
  結果UWIの回答で変更できる。
  直接追加するには `atk run-script research-sources -- --add-account=<アカウント>` または `--add-query=<検索語>` を使う。
  削除には `--remove-account` または `--remove-query` を使う。
  旧設定ファイルの購読は、共有先にまだ無いアカウントと検索語を追加する。
  旧調査先が未登録なら、AWIでの変更を受け付ける設定で登録するが、自動巡回は有効にしない。
  X APIは `weekly_research_x_api` を `true` にするまで呼ばない。
  最低取得枠が上限に収まる場合は全購読を毎週読み、超える場合は週ごとに対象を入れ替える。
  Xの検索は直近7日が対象のため、輪番で読まなかった週の投稿は取得できないことがある。
  `X_BEARER_TOKEN`は`atk config get config_dir`が返すディレクトリの`x-api.env`に置く。
  旧設定で指定したファイルは自動で移る。
  1回の上限は100投稿・20ユーザー
- `agent-toolkit:atk-mode export-session`: Claude CodeとCodexのセッション記録をMarkdownへ出力する

## 更新と削除

一般の更新・削除は[READMEの更新と削除](../../agent-toolkit/README.md#更新と削除)を参照。
dotfiles管理下のマシンでは`chezmoi apply`でも更新できる。
ここでは既存環境の移行時に必要な補足を扱う。

AWIの登録は、スキル`add-awi-by-user`を直接起動する形から`/agent-toolkit:atk-mode add-awi-by-user`へ変わった（Codexでは`$atk-mode add-awi-by-user`）。
古い名前のスキルは無くなったため、更新後は新しい形で起動する。
振り返りとセッション記録の出力も、スキル`review-session`・`export-session`を直接起動する形から入口`atk-mode`の作業へ変わった。
起動は`/agent-toolkit:atk-mode review-session`・`/agent-toolkit:atk-mode export-session`とする（Codexでは`$atk-mode review-session`など）。
定期実行の`atk run-skill agent-toolkit:review-session --args batch`は、そのまま使える。
あわせて、内部用のスキル`wi-standards`、`user-confirmation-and-report`、`refine-prompt`、`review-external-writes`を廃止した。
内容は入口`atk-mode`の配下の手順書へ移した。
プラグインとして導入した環境と`update-dotfiles`で更新する環境では、更新時に古いスキルのディレクトリも置き換わる。
スキルのディレクトリを手作業で複製した環境では、古いスキルのディレクトリを削除する。
対象は`add-awi-by-user`、`wi-standards`、`user-confirmation-and-report`、`review-session`、`export-session`、`refine-prompt`、`review-external-writes`である。

接続ごとに管理する旧版から共有管理サービスへ初めて更新する場合、起動中のClaude Code・Codexの会話は終了まで旧接続を使う。
旧版には状態を引き継ぐ通信機能が無いため、会話を強制終了せず、次の通常起動から新版へ移る。
両ホストで旧stdio停止後の自動再接続は確認していないため、初回移行のために旧接続を停止しない。
この制約は初回の移行に限られる。
共有管理サービス導入後の更新では、進行中のターンが終了した後、その会話の次の入力を新版が処理する。
切替のための入力や追加のモデル呼び出しは行わない。

`agents_server`のClaude委譲先は、有効なプラグインと設定ファイルの変更を監視する。
更新を検知すると進行中のturnの終了を待ち、同じ接続でプラグインを再読み込みしてから次の入力を処理する。
再読み込みに失敗した場合は診断ログを確認し、表示されたプラグインや設定の問題を解消する。
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
