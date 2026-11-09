# agent-toolkitの実装と配布の規約：worktreeでの編集時の注意

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 機構や外部インターフェースの変更に伴う文書の追随先を確定する前 | `agent-documents.md` | 判断 | `read:*/skills/agent-toolkit-edit/references/agent-documents.md` |
| Claudeの子入力の寿命とserver保持版を判断する前 | `agents-server-shared-state-retention.md` | 判断 | `read:*/.claude/skills/agent-toolkit-edit/references/agents-server-shared-state-retention.md` |
| hookの実装や登録を新設・変更する前 | `claude-hooks.md` | 判断 | `read:*/.claude/skills/agent-toolkit-edit/references/claude-hooks.md` |
| hookの遮断・警告の判定を新設・変更する前 | `claude-hooks-block-warn.md` | 判断 | `read:*/.claude/skills/agent-toolkit-edit/references/claude-hooks-block-warn.md` |
| hookの出力フィールドを選択・変更する前 | `claude-hooks-output.md` | 判断 | `read:*/.claude/skills/agent-toolkit-edit/references/claude-hooks-output.md` |
| hook通知の本文（`fix`・次の操作を含む）またはformatterを新設・変更する前 | `claude-hooks-messages.md` | 判断 | `read:*/.claude/skills/agent-toolkit-edit/references/claude-hooks-messages.md` |

## worktreeでの編集時の注意

別の作業用ワークツリー（worktree）で編集した配布物の内容は、配布処理と再起動して初めて実行中のフックや自動チェックに反映される。
現在稼働しているバージョンは、`~/.claude/plugins/installed_plugins.json`の`installPath`を参照して確認する（`~/.claude/plugins/data/`は展開先ではない点に注意する）。
すでに保持しているplugin rootが無効になった場合は、このファイルから再度パスを解決する。
フックによって新たに処理がブロックされたときは、現在の作業ツリーのファイルと稼働中のバージョンのファイルを比較した上で対応方針を決める。

常駐型のMCPサーバー（`agents_server_mcp.py`など）はプロセス起動時に読み込んだモジュールをメモリー上に保持し続けるため、`agent-toolkit/agent_toolkit/`配下の変更はプロセスを再起動した後に反映される。
修正後も同一の現象が発生する場合は、修正が無効であると判断する前に、該当プロセスが読み込んでいるバージョンを特定する。
判定にあたっては、起動スクリプトのパスと起動時刻の双方を確認する（別の作業ツリーやplugin rootから起動されたプロセスは、ファイル修正後に起動していても修正前の内容を読み込んでいる場合がある）。

- `ps -eo pid,cmd`を実行して起動スクリプトの絶対パスが対象の配布先と一致することを確認した上で、`stat -c %Y /proc/<PID>`で得られるプロセスの起動時刻と、該当パス配下の対象ファイルの最終更新時刻を比較する。
  ファイルの更新時刻の方が新しければ、修正前の古い版がプロセスに保持されていると判断できる。
  なお、`ps -o lstart=`はロケール設定によって出力形式が変わるため比較処理には使用しない。
- プロセスがすでに終了しており当時の実行パスや起動時刻が確認できない場合は、バージョン差異を原因と断定することを避け、現行の配布先において事象が再現するかどうかのみを結論とする。

## marketplace管理

- `update-dotfiles`（`chezmoi apply`の事後処理）は、`pytools/_internal/install_claude_plugins.py`を実行してClaude Code向けのagent-toolkitを自動的にインストールおよび更新する。
  `agent-toolkit/share/rules/common/`配下のルールは`post_apply`の`sync_agent_toolkit_rules`によって配布先へ同期される。
  Codex向けのdotfiles固有スキルや`docs`ディレクトリは、`setup_codex_links`によって原本へのシンボリックリンクが作成される。
- 生成ファイルの一括同期は、`uv run python scripts/sync_generated_files.py`を実行して行う（サブコマンドとして`python`を明示する必要がある。詳細は`docs/development/operations.md`を参照する）。
- マーケットプレイスの登録形式について、初期セットアップ（bootstrap）時には`install-claude.sh`や`install-claude.ps1`がGitHub参照型として登録する。
  その後の`chezmoi apply`の事後処理によってローカルディレクトリ参照型（絶対パス参照）へと維持管理される（GitHub型の設定が残っている場合も自動で移行される）。
  ローカル環境での編集内容は、`chezmoi apply`（または`update-dotfiles`）を実行した上でClaude Codeを再起動するか、`/reload-plugins`コマンドを実行することで即座に反映される（この際、バージョン番号の更新は不要である）。
- Codex向けの生成ファイルである`agent-toolkit/.codex-plugin/plugin.json`および`.agents/plugins/marketplace.json`は、prek経由で実行されるpyfltrの`sync-generated-files`によって毎回自動で再生成される。
  また、Codex用のフック定義は、`pytools/_internal/codex_plugin_manifests.py`がイベント名、マッチャー、入力契約を検証した許可リストに基づいて生成する（手動実行は`scripts/sync_codex_plugin_manifests.py`で行う）。
  `chezmoi apply`の事後処理がCodexのマーケットプレイスを登録し、プラグインの導入と更新を完了させる。
