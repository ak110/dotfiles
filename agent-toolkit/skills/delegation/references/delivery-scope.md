# 規範の配送範囲

本書は規範と資料がどの起動区分のエージェントへ届くかを示す配送範囲表を提供する。常時規範の定義と置き場所の分類は`agent-toolkit:delegation/references/agent-documents-basics.md`「置き場所と配送」が定める。

配送範囲表は、常時規範、配送文（`agents_server`が委譲先の起動時に渡す`share/agents-server-*.md`）、作業ディレクトリのプロジェクト規範、ならびにagent-toolkitのスキルが各エージェントへ届くかどうかを整理したものである。
表内の値は実装コードから抽出している。取得元は`agent_toolkit/`配下の`_hooks/rules_context.py`、`_agents_server/`の各backend、および`launch_prompts.py`である。
Codexの生成本文については`scripts/sync_codex_agents.py`も参照する。表と実装に乖離が生じた場合は、実装を正として表側を修正する。

| 文書 | Claude Codeのメイン | Codexのメイン | `Agent`ツールのサブエージェント | `agents_server`の`task`・`delegate`（Claude） | `agents_server`の`task`・`delegate`（Codex） | `agents_server`の`task`・`delegate`（Antigravity） | `explore`・`write`・`shell`（Claude） | `explore`・`write`・`shell`（Codex） | `explore`・`write`・`shell`（Antigravity） | Codexの組み込み委譲先 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `rules/`配下 | 届く（ユーザー規範） | 届く（`~/.codex/AGENTS.md`） | 届く（親のメモリー階層。組み込み`Explore`・`Plan`を除く） | 届く（ユーザー設定の読込元） | 届く（`~/.codex/AGENTS.md`） | 届く（有効なプラグインの常時規範） | 届く（ユーザー設定の読込元） | 届く（`~/.codex/AGENTS.md`） | 届く（有効なプラグインの常時規範） | 届く（`~/.codex/AGENTS.md`） |
| ユーザーが`~/.claude/rules/`に置いた規範ファイル（`~/.codex/AGENTS.md`が埋め込まないもの） | 届く（ユーザー規範） | 届く（`~/.codex/AGENTS.md`の読込指示） | 届く（親のメモリー階層。組み込み`Explore`・`Plan`を除く） | 届く（ユーザー設定の読込元） | 届く（developer指示） | 届く（起動・再開指示） | 届く（ユーザー設定の読込元） | 届く（developer指示） | 届く（起動・再開指示） | 届く（`~/.codex/AGENTS.md`の読込指示） |
| `share/rules-main.md` | 届く（SessionStart） | 届く（SessionStart） | 届かない | 届かない | 届かない | 届かない | 届かない | 届かない | 届かない | 届かない |
| `share/rules-main.claude-code.md` | 届く（SessionStart） | 届かない | 届かない | 届かない | 届かない | 届かない | 届かない | 届かない | 届かない | 届かない |
| `share/rules-main.codex.md` | 届かない | 届く（SessionStart） | 届かない | 届かない | 届かない | 届かない | 届かない | 届かない | 届かない | 届かない |
| `share/rules-common.codex.md` | 届かない | 届く（`~/.codex/AGENTS.md`） | 届かない | 届かない | 届く（`~/.codex/AGENTS.md`） | 届かない | 届かない | 届く（`~/.codex/AGENTS.md`） | 届かない | 届く（`~/.codex/AGENTS.md`） |
| `share/rules-subagent.md` | 届かない | 届かない | 届く（SubagentStart） | 届く（システム指示） | 届く（developer指示） | 届く（指示の先頭） | 返却契約短節のみ届く | 返却契約短節のみ届く | 返却契約短節のみ届く | 届く（SubagentStart） |
| `share/rules-subagent.claude-code.md` | 届かない | 届かない | 届く（SubagentStart） | 届く（システム指示） | 届かない | 届かない | 届かない | 届かない | 届かない | 届かない |
| `share/agents-server-delegate-notice.md` | 届かない | 届かない | 届かない | 届く | 届く | 届く | 届く | 届く | 届く | 届かない |
| `share/agents-server-base.md` | 届かない | 届かない | 届かない | 届く（`system_prompt`のベース） | 届く（`baseInstructions`） | 届かない | 届かない | 届く（`baseInstructions`） | 届かない | 届かない |
| `share/agents-server-delegate.md` | 届かない | 届かない | 届かない | 届く | 届く | 届く | 届かない | 届かない | 届かない | 届かない |
| `share/agents-server-explore.md`・`agents-server-write.md`・`agents-server-shell.md` | 届かない | 届かない | 届かない | 届かない | 届かない | 届かない | 届く（同名のmodeだけ） | 届く（同名のmodeだけ） | 届く（同名のmodeだけ） | 届かない |
| `share/agents-server-auto-resume.md` | 届かない | 届かない | 届かない | 届く | 届く | 届く | 届く | 届く | 届く | 届かない |
| 作業ディレクトリのプロジェクト規範（`AGENTS.md`など） | 届く | 届く | 届く（親のメモリー階層。組み込み`Explore`・`Plan`を除く） | 届く（プロジェクト設定の読込元） | 届く | 届く | 届かない | 届かない（`project_doc_max_bytes=0`） | 届く | 届く |
| agent-toolkitのスキル | 届く | 届く | 未確認 | 届く | 届く | 届く（有効なプラグイン） | 届かない（`skills=[]`） | `write`・`shell`へ届く。`explore`へは届かない（プラグイン無効） | 届く（有効なプラグイン） | 未確認 |

OpenCodeの配送範囲は以下の表のとおりである。通常起動と軽量起動の区分は`agent-toolkit:delegation`の`references/runtime-routing.md`「実行手段」に従う。`task`は役割ごとのmodeを使用し、それぞれ対応する列に属する。

返却契約短節は`rules-subagent.md`「返却形式の受け渡し」を単一の原本とし、`launch_prompts.py`が軽量起動の開始・再開本文へ加える。通常側は全文内の同じ節を受け取る。短節は完了・続行不能・通知・部分成果・追加行を自己完結して定め、既存の配送範囲とmodeごとの権限を保つ。OpenCodeも同節を1回だけ注入する。

| 文書 | OpenCodeの通常起動 | OpenCodeの軽量起動 |
| --- | --- | --- |
| `rules/`配下・ユーザーが`~/.claude/rules/`に置いた規範・`share/rules-subagent.md` | 届く（指示の先頭） | 届く（指示の先頭） |
| `share/rules-main.md`とホスト別の`rules-main.*.md`・`rules-common.codex.md`・`rules-subagent.claude-code.md` | 届かない | 届かない |
| `share/agents-server-delegate-notice.md` | 届く | 届く |
| `share/agents-server-delegate.md` | 届く | 届かない |
| `share/agents-server-explore.md`・`agents-server-write.md`・`agents-server-shell.md` | 届かない | 届く（同名のmodeだけ） |
| `share/agents-server-auto-resume.md` | 届かない | 届かない |
| 作業ディレクトリのプロジェクト規範 | CLI自身の読込は未確認。必要な本文は委譲入力へ含める | 同左 |
| agent-toolkitのスキル | 自動登録しない。必要なスキルは配送されたplugin rootの`skills/`から明示的に読む | 同左 |

Codexの列における`rules/`配下および`share/rules-common.codex.md`の値は、`~/.codex/AGENTS.md`が配置されている環境に限定される（このファイルはCodex単体のインストーラーによる配置対象外である）。
Antigravityの委譲先は、対応する起動区分において`~/.gemini/GEMINI.md`と作業ディレクトリの`AGENTS.md`を読み込む。
表中のAntigravityの軽量起動列は、`explore`、`write`、`shell`に適用する。
`atk wi process-loop`が対話CLIで起動する`agent-toolkit:process-wi all-repos`の統括担当は、対応するホストのメインの列の配送を受ける。
統括担当が`agents_server`で起動するリポジトリ担当は、`task`と`delegate`の列の配送を受ける。
リポジトリ担当は着手時に`share/rules-main.md`とホスト別の`rules-main.*.md`を明示的に読む。
対象リポジトリのメインとしての責務は、`agent-toolkit:process-wi`の`references/all-repos-orchestration.md`「担当の役割」が定める。
全backendのexploreは通常の読取コマンドと読取MCPを使用し、変更禁止の責務を共通プロンプトで伝達する。
Codexはプラグイン由来のスキルを非配送とし、pyfltr MCPを配布定義から直接MCP設定へ渡す。
CodexとClaudeの`explore`、`write`、`shell`への開始・再開指示には、serverが使用する実在のPython 3起動情報も添える（標準ライブラリのみを用いる短い照会用であり、実行権限を追加するものではない。通常の`delegate`とAntigravityは対象外）。
ユーザーが`~/.claude/rules/`に配置した規範ファイルの行における読込指示は、Codexのメインと組み込み委譲先に対しては`~/.claude/rules/`直下の`*.local.md`に限定する。`~/.claude/rules/myprojects.md`のようにClaude Code向けに配布するファイルは、`agents_server`の委譲先のみに届く。`~/.claude/CLAUDE.md`はこの行の連結対象外であり、Claudeの各起動区分へはユーザー設定の読込元から、Codexのエージェントへは`~/.codex/AGENTS.md`の読込指示を通じて届く。
Antigravityのプラグイン配送は`antigravity_plugin.py`、起動指示はAntigravity backendと`launch_prompts.py`を根拠とする。
プロジェクト規範の自動読込に関する行は、実機での観測結果に基づく。
観測記録は`docs/development/audit-records.md`の「agent-basics/skills/writing-standards/references/agent-documents-basics.md：責務と構成：2026年10月6日」に記載されている。

## 名指し参照の読込方法

名指し参照は`../SKILL.md`「名指し参照の読込方法」に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-toolkit:process-wi/references/all-repos.md` | 判断 | `read:agent-toolkit:*/skills/process-wi/references/all-repos.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-toolkit:delegation/references/agent-documents-basics.md#「置き場所と配送」` | 判断 | `section:agent-toolkit:*/skills/delegation/references/agent-documents-basics.md#「置き場所と配送」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-toolkit:delegation/references/runtime-routing.md#「実行手段」` | 判断 | `section:agent-toolkit:*/skills/delegation/references/runtime-routing.md#「実行手段」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-toolkit:process-wi/references/all-repos-orchestration.md#「担当の役割」` | 判断 | `section:agent-toolkit:*/skills/process-wi/references/all-repos-orchestration.md#「担当の役割」` |

## Antigravityの通常起動

Antigravityのメインエージェントは、有効化した`agent-toolkit-agy`プラグインから共通の`rules/`、スキル、ならびにagentsを読み込む。
生成器は常時規範を`rules/AGENTS.md`と追加のalways-onファイルへ分割する。
`share/rules-main.md`は6イベントのhookアダプターが起動時に読み込み、`PreInvocation`の動的コンテキストとして配送する。
ホスト別のClaude CodeとCodex規範は通常起動へは配送しない。
`agents_server`の委譲先へ配送する本文は、上表と各backendの委譲プロンプトで定義する。

Antigravityの委譲起動と再開では、通常・軽量を問わず保持されたplugin root、CLIの起動情報、ならびに`AUTO_RESUME_NOTICE`を渡す。役割別規範の境界は共通の`LAUNCH_SYSTEM_PROMPTS`に従う。
ユーザーの`~/.claude/CLAUDE.md`と`~/.claude/rules/`配下（`agent-toolkit/`を除く）は起動・再開の指示へ境界付きで埋め込み、共通の常時規範は有効なプラグインから届ける。
