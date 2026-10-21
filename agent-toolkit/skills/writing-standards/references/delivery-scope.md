# 規範の配送範囲

本書は規範と資料がどの起動区分のエージェントへ届くかを示す配送範囲表を提供する。常時規範の定義と置き場所の分類は`agent-documents-basics.md`「置き場所と配送」が定める。

配送範囲表は、常時規範、配送文（`agents_server`が委譲先の起動時に渡す`share/agents-server-*.md`）、作業ディレクトリのプロジェクト規範およびagent-toolkitのスキルがどのエージェントへ届くかを示す次の表である。
値は実装から取る。取得元は`agent_toolkit/`配下の`_hooks/rules_context.py`と`_agents_server/`の各backendと`launch_prompts.py`である。
Codexの生成本文には`scripts/sync_codex_agents.py`も使う。表と実装が一致しない場合は実装を正として表を直す。

| 文書 | Claude Codeのメイン | Codexのメイン | `Agent`ツールのサブエージェント | `agents_server`の`task`・`delegate`（Claude） | `agents_server`の`task`・`delegate`（Codex） | `agents_server`の`task`・`delegate`（Antigravity） | `explore`・`write`・`shell`（Claude） | `explore`・`write`・`shell`（Codex） | `explore`・`write`・`shell`（Antigravity） | Codexの組み込み委譲先 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `rules/`配下 | 届く（ユーザー規範） | 届く（`~/.codex/AGENTS.md`） | 届く（親のメモリー階層。組み込み`Explore`・`Plan`を除く） | 届く（ユーザー設定の読込元） | 届く（`~/.codex/AGENTS.md`） | 届かない | 届く（ユーザー設定の読込元） | 届く（`~/.codex/AGENTS.md`） | 届かない | 届く（`~/.codex/AGENTS.md`） |
| ユーザーが`~/.claude/rules/`に置いた規範ファイル（`~/.codex/AGENTS.md`が埋め込まないもの） | 届く（ユーザー規範） | 届く（`~/.codex/AGENTS.md`の読込指示） | 届く（親のメモリー階層。組み込み`Explore`・`Plan`を除く） | 届く（ユーザー設定の読込元） | 届く（developer指示） | 届かない | 届く（ユーザー設定の読込元） | 届く（developer指示） | 届かない | 届く（`~/.codex/AGENTS.md`の読込指示） |
| `share/rules-main.md` | 届く（SessionStart） | 届く（SessionStart） | 届かない | 届かない | 届かない | 届かない | 届かない | 届かない | 届かない | 届かない |
| `share/rules-main.claude-code.md` | 届く（SessionStart） | 届かない | 届かない | 届かない | 届かない | 届かない | 届かない | 届かない | 届かない | 届かない |
| `share/rules-main.codex.md` | 届かない | 届く（SessionStart） | 届かない | 届かない | 届かない | 届かない | 届かない | 届かない | 届かない | 届かない |
| `share/rules-common.codex.md` | 届かない | 届く（`~/.codex/AGENTS.md`） | 届かない | 届かない | 届く（`~/.codex/AGENTS.md`） | 届かない | 届かない | 届く（`~/.codex/AGENTS.md`） | 届かない | 届く（`~/.codex/AGENTS.md`） |
| `share/rules-subagent.md` | 届かない | 届かない | 届く（SubagentStart） | 届く（システム指示） | 届く（developer指示） | 届く（指示の先頭） | 届かない | 届かない | 届かない | 届く（SubagentStart） |
| `share/rules-subagent.claude-code.md` | 届かない | 届かない | 届く（SubagentStart） | 届く（システム指示） | 届かない | 届かない | 届かない | 届かない | 届かない | 届かない |
| `share/agents-server-delegate-notice.md` | 届かない | 届かない | 届かない | 届く | 届く | 届く | 届く | 届く | 届く | 届かない |
| `share/agents-server-delegate.md` | 届かない | 届かない | 届かない | 届く | 届く | 届く | 届かない | 届かない | 届かない | 届かない |
| `share/agents-server-explore.md`・`agents-server-write.md`・`agents-server-shell.md` | 届かない | 届かない | 届かない | 届かない | 届かない | 届かない | 届く（同名のmodeだけ） | 届く（同名のmodeだけ） | 届く（同名のmodeだけ） | 届かない |
| `share/agents-server-auto-resume.md` | 届かない | 届かない | 届かない | 届く | 届く | 届かない | 届く | 届く | 届かない | 届かない |
| 作業ディレクトリのプロジェクト規範（`AGENTS.md`など） | 届く | 届く | 届く（親のメモリー階層。組み込み`Explore`・`Plan`を除く） | 届く（プロジェクト設定の読込元） | 届く | 届く | 届かない | 届かない（`project_doc_max_bytes=0`） | 届く | 届く |
| agent-toolkitのスキル | 届く | 届く | 未確認 | 届く | 届く | 届かない | 届かない（`skills=[]`） | `write`・`shell`へ届く。`explore`へは届かない（プラグイン無効） | 未確認 | 未確認 |

OpenCodeの配送範囲は次の表とする。`task`は役割のmodeを使い、それぞれ対応する列に属する。

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

Codexの列の`rules/`配下と`share/rules-common.codex.md`の値は`~/.codex/AGENTS.md`を配置した環境に限る（このファイルはCodex単体のインストーラーの配置対象外である）。
Antigravityの委譲先は対応する起動区分で`~/.gemini/GEMINI.md`と作業ディレクトリの`AGENTS.md`を読む。
表のAntigravityの軽量起動列は`explore`・`write`・`shell`へ適用する。
`atk wi process-loop all-repos`のリポジトリ担当は`task`・`delegate`の列の配送を受ける。
担当は対象リポジトリのメインを担うため、`share/rules-main.md`とホスト別の`rules-main.*.md`を着手時に明示的に読む（`agent-toolkit:process-wi`の`references/all-repos-orchestration.md`「担当の役割」）。
全backendのexploreは通常の読取コマンドと読取MCPを使い、変更禁止の責務を共通プロンプトで伝える。
Codexはプラグイン由来のスキルを非配送に保ち、pyfltr MCPを配布定義から直接のMCP設定へ渡す。
Codex・Claudeの`explore`・`write`・`shell`への開始と再開の指示には、serverが使う実在するPython 3の起動情報も添える（標準ライブラリだけの短い照会用で、実行権限は追加しない。通常の`delegate`とAntigravityは対象外）。
ユーザーが`~/.claude/rules/`に置いた規範ファイルの行の読込指示は、Codexのメインと組み込み委譲先へは`~/.claude/rules/`直下の`*.local.md`に限る。`~/.claude/rules/myprojects.md`のようにClaude Code向けに配布するファイルは`agents_server`の委譲先へだけ届く。`~/.claude/CLAUDE.md`はこの行の連結の対象外とし、Claudeの各起動区分へはユーザー設定の読込元から、Codexのエージェントへは`~/.codex/AGENTS.md`の読込指示で届く。
Antigravityの列とプロジェクト規範・スキルの行は実機の観測から取った。
観測記録は`docs/development/audit-records.md`の「agent-toolkit/skills/writing-standards/references/agent-documents-basics.md：責務と構成：2026年10月6日」にある。
