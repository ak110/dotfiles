# Codex利用ガイド

Codexはagent-toolkitの標準構成に含まれる。単体インストーラーはCodexプラグインと、
Claude CodeからCodexを呼び出すCodex MCPを設定する。

単体インストーラーは既存の`~/.codex/AGENTS.md`を保護するため、dotfiles固有のグローバル
`AGENTS.md`と共有リンク群を展開しない。dotfiles利用者は`update-dotfiles`または`chezmoi apply`により、
Codex向け`AGENTS.md`、共有ルール・スキルのリンク、プラグイン、Codex MCPを一括設定する。
プラグイン導入後は、次の手順で更新を反映する。

## フィードバックの常駐処理

次のコマンドはCodexの対話UIを起動し、対象リポジトリのフィードバックを継続して処理する。

```bash
atk mq process-loop --orchestrator=codex
```

開始時点の項目に加え、処理中に追加されたready項目も同じセッションで順次処理する。
ready項目がなくなると、終了時の`session-review`を1回実行してgoalを完了する。
goal完了後に対話UIで`/exit`を入力すると、親の監視ループへ戻る。

## プラグイン更新の反映

Codexプラグインはバージョン付きキャッシュへ導入される。
`update-dotfiles`はローカルagent-toolkitのバージョンと有効状態が一致する場合、再導入を省略する。
ローカルまたは外部のプラグインを実際に追加または更新した場合と、公開インストーラーで
`codex plugin add`前後のversionまたはenabledが変化した場合に限り、次の再起動コマンドを案内する。

```bash
codex app-server daemon restart
```

公開インストーラーでは、プラグイン追加後のCodex MCP登録または`atk`配置が失敗した場合も、
エラーの後の最終行へ再起動コマンドを表示し、非0の終了状態を維持する。
進行中のセッションを保護するため、app-server daemonは自動再起動しない。

マーケットプレイスの登録だけが変化した場合、公開インストーラーで導入前後の状態が同じ場合、
状態を確認できない場合、プラグイン追加前の処理または追加自体が失敗した場合、外部プラグインが
導入済みの場合は、再起動案内を表示しない。
案内されたコマンドは、Codex、Codex MCP、remote-controlを利用する実行中セッションの終了後に実行する。
daemonを利用しない既存のCLI・IDEセッションは、作業完了後に新しいセッションを開始する。

### フックの信頼確認

Codexはplugin同梱フックの定義が変わると、利用者が再び信頼するまで当該フックをスキップする。
プラグイン更新後は新しいCodexセッションで`/hooks`を実行し、次の3イベントだけが含まれることを確認する。

- `PermissionRequest`
- `UserPromptSubmit`
- `Stop`

表示内容を確認してフックを信頼する。
信頼後は、作業完了時のStopが同じセッションを継続し、セッション振り返りを起動する。
起動済み状態または読み取り可能なtranscriptから振り返りの起動済みを確認できる間は、
追加の振り返りを起動せずターンを終了する。状態が14日を超えて更新されず回収され、
かつtranscriptを利用できない場合は、同じセッションでも再び振り返りへ誘導されることがある。
手動で振り返る場合は`$agent-toolkit:session-review`を実行する。

## Codex CLI本体

dotfiles配布利用者では、`chezmoi apply`後の処理がCodexの公式インストーラーを非対話で実行する。
未導入時はスタンドアローン版を導入し、導入済みの場合は最新版へ更新する。
管理対象パッケージは`~/.codex/packages/standalone/`へ配置される。
可視コマンドの既定配置先はLinuxとmacOSで`~/.local/bin`、Windowsで`%LOCALAPPDATA%\Programs\OpenAI\Codex\bin`である。
`CODEX_HOME`を設定した場合、パッケージは`$CODEX_HOME/packages/standalone`へ配置される。
`CODEX_HOME`に指定するディレクトリは、インストーラーの実行前に作成する必要がある。
`CODEX_INSTALL_DIR`を設定した場合、可視コマンドはそのディレクトリへ配置される。
WindowsではPowerShell 7の利用を推奨する。
導入処理は`pwsh`を優先し、見つからない場合は`powershell`へフォールバックする。
Windows PowerShellでは、環境によって公式インストーラーが使用する`Get-FileHash`を解決できず、導入に失敗する。

スタンドアローン版の起動を確認した後、mise npmバックエンドの全版を除去する。
PATHから解決される非正規npm版も、package帰属を確認したうえで除去する。
PATH外の非アクティブNode環境は自動削除しない。
認証情報、設定、セッションは除去しない。
WindowsでCodexが実行中の場合は停止せず、導入、更新、旧版の整理を次回へ延期する。

旧版の整理を`chezmoi apply`後の処理が担うのは、公式インストーラーが非対話実行時に競合するnpm版を残すためである。
公式インストーラーは競合版を検出したうえで削除の可否を対話で確認し、非対話実行では否定を既定値とする。
競合版を検出した実行では、シェルの起動ファイルへPATH設定が追記される場合がある。
起動ファイルはchezmoiの配布対象であるため、追記された内容は次回の`chezmoi apply`で配布内容へ戻る。

## 推奨構成

配布内容は以下の構成とする。

- `~/.codex/AGENTS.md`: Codex向けの基本記述と、agent-toolkitの基本原則・製品横断の実行運用を埋め込む
- `~/.codex/agent-toolkit/rules/99-claude-code.md`: リンク先には配置するが、Claude Code固有規範のためCodex向けAGENTS.mdへ埋め込まない
- `~/.codex/agent-toolkit/rules`: Claude Code側のagent-toolkitルール原本へのシンボリックリンク
- `~/.codex/skills/*`: `agent-toolkit/skills/*`および`.chezmoi-source/dot_claude/skills/*`へのシンボリックリンク
- プロジェクト直下の`.agents/skills`: プロジェクト専用スキルディレクトリへのシンボリックリンク

CodexはClaude Codeの`CLAUDE.md`や`.claude/rules/`を同じ読み込み規則では扱わない。
そのため、常時ロードの入口は`AGENTS.md`へ集約し、本文は原本ファイルを参照する形にする。
ファイルコピーで同期すると改訂漏れが発生するため、共有対象はリンクで配布する。
chezmoiの`symlink_`はWindowsで特権不足により失敗するため採用しない。
代わりに`chezmoi apply`後処理（`pytools.post_apply`）の専用ステップがリンクを生成する。
Linux/macOSではシンボリックリンク、Windowsではディレクトリジャンクションを使う。

プロジェクト直下の`.claude/rules/`と`.claude/skills/`は、Claude Codeでは自動ロード・自動検出される。
Codexでは同じ挙動を前提にできないため、Codex側のプロジェクト専用スキルは`.agents/skills/`へ配置する。
`.claude/skills/`の原本を再利用する場合も、コピーせず`.agents/skills -> .claude/skills`のシンボリックリンクにする。
`.claude/rules/`はCodex側に対応する専用ディレクトリへ移さず、`~/.codex/AGENTS.md`から該当ファイルを読むよう指示する。

`~/.codex/rules`はCodexの承認ルール用ディレクトリであり、Claude CodeのMarkdownルールとは互換性がない。
agent-toolkitのMarkdownルールは`~/.codex/agent-toolkit/rules`に配置する。

プロジェクト固有設定は、原則として`AGENTS.md`を実体ファイル、
`CLAUDE.md`をClaude Codeのfile import記法`@AGENTS.md`を含むアダプターとして配置する。
両方を実体ファイルとすることで、コピー欠落やシンボリックリンク非対応環境での事故を回避する。
Codex専用の差分が必要な場合のみ、`AGENTS.md`本体に分岐記述を追加する。
