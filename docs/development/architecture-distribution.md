# アーキテクチャ: ホスト別配布とOpenCode

`agent-toolkit/`はAgent Plugins、Claude Code、Codexが共有するプラグインルートである。
`skills/`の原本を共有し、ホストごとのmanifest、hook、MCP設定を生成する。
全エージェントへ常に読み込ませる規範ファイルは配らず、規範は入口のスキル`agent-toolkit:atk-mode`と手順書が持つ。
OpenCodeの対話ホストには、同じ原本からスキルと入口の起動を求める案内を導入する。

| 対象 | 役割と生成方法 |
| --- | --- |
| `.claude-plugin/plugin.json`・`.mcp.json` | Claude Code向け設定であり、metadataとClaude専用を含むMCP server定義の大元 |
| `plugin.json`・`.mcp.codex.json`・`mcp.json` | Agent Plugins v1向け生成物。大元の設定から共有許可済みserverだけを固定schemaへ写像する |
| `.codex-plugin/plugin.json`・`hooks/hooks.codex.json` | Codex向け生成物。大元の設定から許可済みの要素だけを写像する |
| `agents/`・`hooks/`・`scripts/` | Claude Code・Codex・配布処理が使う固有資源。Agent Pluginsの可搬要素としては扱わない |
| OpenCodeの`skills/`と`agent-toolkit.md` | post-applyが共有スキルと入口の案内をグローバル検出先へ同期する |

新規engineの追加時にホスト導入の要否と理由を判断・記録する手順は、`.claude/skills/agent-toolkit-edit/SKILL.md`が定める。

dotfilesはClaude Code・Agent Plugins向けの`agent-toolkit/`を元にし、Codex向けには`agent-toolkit-codex/`を生成する。
`agent-toolkit-codex/`はAgent Plugins用の直下`plugin.json`と`mcp.json`を除き、`.codex-plugin/plugin.json`、hook、skill、Python実装、lockfileその他の実行資源を通常ファイルとして含む。
Codex 0.154.0はプラグイン導入時にsourceをsnapshotするため、`agent-toolkit-codex/`は相対シンボリックリンクを含めない。
`.agents/plugins/marketplace.json`だけが`./agent-toolkit-codex`を参照し、Claude CodeとAgent Pluginsは引き続き`agent-toolkit/`を参照する。

`atk-hook`はlive sessionが保持する`PLUGIN_ROOT`を優先し、その変数が指す版付きCodex cacheが欠落した場合は、Claude Code側に残る同版cacheを同じCodex絶対パスへ原子的に復旧する。
同版が無い場合はClaude Codeの現行導入版からhookを起動し、編集途中のdotfiles作業ツリーは自動fallbackに使わない。
現行versionの導入と有効状態の管理はCodex公式CLIへ委ね、旧版保存台帳は設けない。
共有するCodex hook定義は、ホストが渡す`${PLUGIN_ROOT}/scripts/launch.py`からその版のhookを起動する。
dotfilesが生成する`agent-toolkit-codex/`のhook定義は、個人binの`atk-hook`を安定した起動コマンドにし、インストーラーは`codex plugin add`より先にこのコマンドを配置する。

Codexは`.codex-plugin/plugin.json`が指すMCP設定の`${PLUGIN_ROOT}`を展開せず、相対の`cwd`だけをplugin rootから解決する。
`cwd`の無いserverは会話のcwdで起動する（Codex 0.170の`codex-mcp/src/plugin_config.rs`で確認）。
Codex向け`agents_server`は`cwd`をplugin rootに固定し、Claude Code向けの`${CLAUDE_PLUGIN_ROOT}`展開をCodexの起動契約へ流用しない。
pyfltrは対象を省いた呼び出しで会話のcwdのプロジェクトを調べるため、`cwd`をplugin rootへ固定しない。
dotfilesが生成する`agent-toolkit-codex/`のMCP定義は、`${PLUGIN_ROOT}`を`codex plugin add`の導入先である`$CODEX_HOME/plugins/cache/<marketplace名>/agent-toolkit/<版>`の絶対パスへ展開する。
展開する前はCodexのpyfltr MCPが会話のcwdからの相対パス`${PLUGIN_ROOT}/scripts/launch.py`を開けず、起動のたびに失敗していた（2026-11-18にLinux、2026-11-19にWindowsのCodex 0.170で観測）。

既存の外部参照を維持するため、公開互換インストーラーである`install-claude.sh`・`install-claude.ps1`と`docs/guide/claude-code-guide.md`の名前はClaude Code・Codex統合後も変更しない。

| 導入方法 | マーケットプレイス | 設定対象 |
| --- | --- | --- |
| 単体インストーラー | Gitマーケットプレイス`ak110/dotfiles` | 双方のプラグイン、共有`agents_server` MCP、`atk`、旧版が配置した規範の削除 |
| dotfiles `post_apply` | ローカル生成物 | 単体導入の対象に加え、Codex向け`AGENTS.md`（個人用の規範の埋め込み）と共有リンク、OpenCode向けスキルと入口の案内 |

## OpenCodeの対話ホストへの導入

`pytools/_internal/install_opencode_toolkit.py`は、OpenCode CLI導入後のfullモードの後処理として動く。
`skills/agent-toolkit-<名前>/`と`skills/agent-basics-<名前>/`へスキル本体と付属資源をコピーする。
独自スキルは保持し、専用の所有markerがある配布先だけで更新・廃止した資源を同期する。
`agent-toolkit.md`には`atk-auto`境界の内側に、作業の前と会話の圧縮の後に`agent-toolkit-atk-mode`のスキルを読む案内と、plugin rootとスキル名の対応だけを置き、規範の本文は載せない。
グローバル`AGENTS.md`とClaude互換規範の読込順位、既存のprovider・permission・mcpなどの値を保持する。
この導入はMCP登録、TS hook移植と単体インストーラーの対象追加を含まない。
ホストが定める検出と読込の契約は[skills](https://opencode.ai/docs/skills/)と[rules](https://opencode.ai/docs/rules/)にある。
