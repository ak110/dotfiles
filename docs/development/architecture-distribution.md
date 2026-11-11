# アーキテクチャ: ホスト別配布とOpenCode

## agent-toolkitのホスト別配布

`agent-toolkit/`はAgent Plugins、Claude Code、Codexが共有するプラグインルートである。
`skills/`の原本を共有し、ホストごとのmanifest、hook、MCP設定と常時規範の配置を生成する。
OpenCodeの対話ホストには、同じ原本からスキルと常時規範を導入する。

| 対象 | 役割と生成方法 |
| --- | --- |
| `.claude-plugin/plugin.json`・`.mcp.json` | Claude Code向け設定であり、metadataとClaude専用を含むMCP server定義の大元 |
| `plugin.json`・`.mcp.codex.json`・`mcp.json` | Agent Plugins v1向け生成物。大元の設定から共有許可済みserverだけを固定schemaへ写像する |
| `.codex-plugin/plugin.json`・`hooks/hooks.codex.json` | Codex向け生成物。大元の設定から許可済みの要素だけを写像する |
| `agents/`・`hooks/`・`scripts/`・`share/` | Claude Code・Codex・配布処理が使う固有資源。Agent Pluginsの可搬要素としては扱わない |
| OpenCodeの`skills/`と`agent-toolkit.md` | post-applyが共有スキルと常時規範をグローバル検出先へ同期する |

新規engineの追加時にホスト導入の要否と理由を判断・記録する手順は、`.claude/skills/agent-toolkit-edit/SKILL.md`が定める。

`pytools/_internal/codex_plugin_manifests.py`がAgent PluginsとCodexの生成物の生成と差の確認を担い、生成器の起動スクリプト`scripts/sync_codex_plugin_manifests.py`とpost-applyがこれを使う。
Codex向け`agents_server`はplugin rootを作業ディレクトリに固定する。
起動定義は`uv run --no-config --no-project --python 3.15 python scripts/launch.py agent_toolkit/agents_server_mcp.py`として生成する。
Claude Code向けの`${CLAUDE_PLUGIN_ROOT}`展開はCodexの起動契約へ流用しない。
`scripts/sync_generated_files.py`は同生成器を統合実行し、生成物を冪等に更新する。

dotfilesはClaude Code・Agent Plugins向けの`agent-toolkit/`を元にし、Codex向けには`agent-toolkit-codex/`を生成する。
`agent-toolkit-codex/`はAgent Plugins用の直下`plugin.json`と`mcp.json`を除き、`.codex-plugin/plugin.json`、hook、skill、Python実装、lockfileその他の実行資源を通常ファイルとして含む。
Codex 0.154.0はプラグイン導入時にsourceをsnapshotするため、`agent-toolkit-codex/`は相対シンボリックリンクを含めない。
`.agents/plugins/marketplace.json`だけが`./agent-toolkit-codex`を参照し、Claude CodeとAgent Pluginsは引き続き`agent-toolkit/`を参照する。
`agent-toolkit-codex/`はGitで追跡せず、`update-dotfiles`のpost-applyがCodex plugin導入の直前に生成する。
手動で再生成する場合は`scripts/sync_codex_plugin_manifests.py`を実行し、`--check`で大元の定義との一致を確認する。
生成に失敗した場合はpost-applyが非0で終了し、失敗したstep名と詳細を更新logへ記録する。

共有するCodex hook定義は、ホストが渡す`${PLUGIN_ROOT}/scripts/launch.py`からその版のhookを起動する。
dotfilesが生成する`agent-toolkit-codex/`のhook定義は、個人binの`atk-hook`を安定した起動コマンドにする。
インストーラーは`codex plugin add`より先にこのコマンドを配置する。
`atk-hook`はlive sessionが保持する`PLUGIN_ROOT`を優先し、その変数が指す版付きCodex cacheが欠落した場合は、Claude Code側に残る同版cacheを同じCodex絶対パスへ原子的に復旧する。
同版が無い場合はClaude Codeの現行導入版からhookを起動し、編集途中のdotfiles作業ツリーは自動fallbackに使わない。
更新前の全通常versionディレクトリを複製し、導入後の状態確認とdaemon処理を含む更新関数内のCodex CLI呼出が全て終了した後に、CLIが削除した旧versionを同じ絶対パスへ復元する。
現行versionの導入と有効状態の管理はCodex公式CLIへ委ね、旧版保存台帳は設けない。

agent-toolkitには、公開互換インストーラーである`install-claude.sh`・`install-claude.ps1`を使う単体導入と、
chezmoiの`post_apply`を使うdotfiles導入がある。
既存の外部参照を維持するため、インストーラーと
`docs/guide/claude-code-guide.md`の名前はClaude Code・Codex統合後も変更しない。

| 導入方法 | マーケットプレイス | 設定対象 |
| --- | --- | --- |
| 単体インストーラー | Gitマーケットプレイス`ak110/dotfiles` | Claude Codeルール、双方のプラグイン、共有`agents_server` MCP、`atk` |
| dotfiles `post_apply` | ローカル生成物 | 単体導入の対象に加え、Codex向け`AGENTS.md`と共有リンク、OpenCode向けスキルと常時規範 |

- agent-toolkitのCodex向けskillsはplugin marketplace経由で配布する。
  Agent Plugins・Codex向けmanifestは
  Claude Code向けmanifestを元にして`scripts/sync_generated_files.py`で生成する
- `setup_codex_links.py`はdotfiles固有スキルと`docs`だけをリンクする。
  `agent-toolkit/share/rules/common/`は`sync_agent_toolkit_rules.py`が配布先へ同期する
- Codex hookはイベント名、matcher、入力契約を確認した許可表へ登録したものだけを派生manifestへ含める

### OpenCodeの対話ホストへの導入

`pytools/_internal/install_opencode_toolkit.py`は、OpenCode CLI導入後のfullモードの後処理として動く。
Linux・Windowsとも`XDG_CONFIG_HOME`、未設定なら`~/.config`配下の`opencode/`を使い、CLI不在時は案内してスキップする。
`OPENCODE_CONFIG_DIR`が指定されている場合は、その設定ディレクトリを優先する。
`skills/agent-toolkit-<名前>/`と`skills/agent-basics-<名前>/`へスキル本体と付属資源をコピーする。
同名スキルの衝突を避ける名前空間を付け、frontmatterの`name`をディレクトリ名へ合わせる。
本文と付属資源は原本を保持し、手動起動専用スキルには`metadata["opencode/autoinvoke"]: "false"`を加える。
独自スキルは保持し、専用の所有markerがある配布先だけで更新・廃止した資源を同期する。

常時規範は`agent-toolkit.md`へ`atk-auto`境界とともに集約し、元の規範本文、plugin rootとスキル名の対応を保持する。
スキル本文のroot変数と相対参照は、原本のplugin rootとスキルディレクトリを起点に解決する。
`opencode.jsonc`、既存設定が`opencode.json`だけならそのファイルの`instructions`へ、専用規範の絶対パスを1回だけ追加する。
グローバル`AGENTS.md`とClaude互換規範の読込順位、既存のprovider・permission・mcpなどの値を保持する。
この導入はMCP登録、TS hook移植と単体インストーラーの対象追加を含まない。
ホストが定める検出と読込の契約は[skills](https://opencode.ai/docs/skills/)と[rules](https://opencode.ai/docs/rules/)にある。
