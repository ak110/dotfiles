# 共有原本とplugin manifestの設計記録

[設計記録の索引](design.md)

## 共有原本とCodex固有の上書き

Codexのコマンド回収は、外側の`functions.exec`のcellと内側の`exec_command`のsessionを別の実行識別子として扱う。
返却本文とともに`exit_code`と`session_id`を保持し、cellは`functions.wait`、sessionは`write_stdin`で同じ起動の終了まで回収する。
`session_id`がある間に同じ待機コマンドを再発行すると、先行実行を回収する前に次の待機も開始するため採用しない。
契約の定義元は`agent-toolkit/skills/atk-mode/playbooks/host-codex.md`「Codex固有の入出力」へ集約する。

hook・MCP定義などホスト別に明確に分離された資源を除き、Claude CodeとCodexに共通するルール・スキルは`agent-toolkit/`の共有原本で定義する。
Codexだけの公開能力との差分は上書きとして置き、共通の入口と手順書へCodex固有の条件を持ち込まない。
Codexに固有の操作は、全主体向けとメイン向けを`agent-toolkit/skills/atk-mode/playbooks/host-codex.md`の1ファイルへ置き、冒頭の「読む節」の表が条件と主体ごとに読む節を選ばせる。
本文を自動では配らず読む主体が節を選ぶため、メイン向けの節を同じファイルへ置いても委譲先へは届かない。
ホスト間の差の一覧は`agent-toolkit/skills/atk-mode/references/hosts.md`にある。
Codex基礎指示の上書きは、確認・待機・並列化・ツール利用前説明のホスト契約をCodex側へ閉じ込め、Claude Codeの共通契約を変更しない。
Codexの委譲は、`spawn_agent`・`send_message`・`followup_task`・`wait_agent`・`interrupt_agent`を使うネイティブ方式とagents_server方式を分ける。
ネイティブ方式では`fork_turns`が選ぶ会話履歴、worktreeの`AGENTS.md`、`SubagentStart` hookが所在を示す`playbooks/subagent.md`を独立した入力とする。
agents_serverの委譲は`_agents_server/launch_prompts.py`が同じ`playbooks/subagent.md`から切り出した節を起動の本文として配送する。
各起動区分の委譲先へ届く規範は`agent-toolkit:atk-mode/references/agent-documents.md`「配送範囲表」が示す。
Claude Codeに固有の操作は`playbooks/host-claude-code.md`へ置き、Codexの主体には読ませない。
この境界をhookの実起動、agents_serverのprompt構成、Codex manifestの生成テストで確認し、規範本文を方式ごとに複製しない。
生成物は手編集せず、原本と正式生成器を更新して同期する。

共有ルールをCodex固有条件で分岐する案は、Claude Codeへ不要な差分を配布して共通契約を曖昧にするため採用しない。
共有文書をCodex用に複製する案は、原本・生成器・自動テストの同期対象を増やすため採用しない。
Codex事情を共有ルールへ直接改訂する案は、Claude Codeへホスト固有の挙動を波及させるため採用しない。

## plugin manifestと生成物

公開動作を追加または変更するpluginは、各pluginの`.claude-plugin/plugin.json`（`agent-toolkit/`と`agent-basics/`のそれぞれ）を版数の基準とする。
版数は`agent_toolkit_bump.py --plugin <plugin名> minor`などの正式な手順で更新する。
`.claude-plugin/marketplace.json`の各plugin記述は、同名pluginの基準の版数と一致させる。
対象にできるpluginは`marketplace.json`の`plugins[]`から導き、pluginを追加しても版数更新の手段を別に用意しない。
同ディレクトリは`agent-toolkit/`を原本とする自己完結型の通常ディレクトリであり、Agent Plugins用の直下`plugin.json`と`mcp.json`を除外する。
`.agents/plugins/marketplace.json`のCodex local sourceだけを`./agent-toolkit-codex`へ向け、Claude CodeとAgent Pluginsのsourceは`agent-toolkit/`のまま維持する。
生成後は同scriptの`--check`でClaude Code向け原本、marketplace記述、Codex向け派生manifest、`agent-toolkit-codex/`全体の一致を確認する。
プラグインマニフェストの検証は、Claude Code向けとCodex向けで到達できる保証の水準が異なるため手順を分ける。
Claude Code向けは`claude plugin validate --strict`を`pyfltr`のカスタムlinter`claude-plugin-validate`から実行し、未知フィールドとメタデータ欠落を失敗として扱う。
Codex向けはCodex同梱の`plugin-creator/scripts/validate_plugin.py`を試験から実行し、指摘の集合が既知の2件と完全に一致することを確かめる。
この試験は`pytools/_internal/codex_plugin_manifests_projection_test.py`の`test_codex_plugin_validator_reports_only_known_schema_deviations`である。
Codexの検証器を無条件の合格条件にしないのは、同梱資料が`hooks`をマニフェストの正規フィールドとして定義しながら、検証の節では未対応フィールドとして拒否すると述べ、同一資料内で矛盾しているためである。
資料はローカル導入での受理を保証も否定もせず、現行manifestのままの導入状態は`installed: true`かつ`enabled: true`である。
そこで既知の逸脱の集合を固定し、集合が変化した時点で失敗させて再判断の契機とする。
知識境界として、期待する逸脱の集合と許容根拠はテスト側が持ち、マニフェストの生成規則は`pytools/_internal/codex_plugin_manifests.py`が持つ。
却下した代替案は、`hooks`を除去し`mcpServers`を`.mcp.json`へ解決させて検証器を無条件に合格させる案である。
Codexのプラグインフック機能を失い、`agent-toolkit/.mcp.json`との名前衝突を解消する追加設計を要する一方、得られるのは資料上の保証が無い体裁上の適合だけであるため採用しない。
