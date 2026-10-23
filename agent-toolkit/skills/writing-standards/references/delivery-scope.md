# 規範の配送範囲

本書は規範と資料がどの起動区分のエージェントへ届くかを示す配送範囲表を提供する。常時規範の定義と置き場所の分類は`agent-documents-basics.md`「置き場所と配送」が定める。

配送範囲表は、常時規範、配送文（`agents_server`が委譲先の起動時に渡す`share/agents-server-*.md`）、作業ディレクトリのプロジェクト規範およびagent-toolkitのスキルがどのエージェントへ届くかを示す次の表である。
値は実装から取る。取得元は`agent_toolkit/`配下の`_hooks/rules_context.py`と`_agents_server/`の各backendと`launch_prompts.py`である。
Codexの生成本文には`scripts/sync_codex_agents.py`も使う。表と実装が一致しない場合は実装を正として表を直す。

| 文書 | Claude Codeのメイン | Codexのメイン | `Agent`ツールのサブエージェント | `agents_server`の`task`・`delegate`（Claude） | `agents_server`の`task`・`delegate`（Codex） | `agents_server`の`task`・`delegate`（Antigravity） | `explore`・`write`・`shell`（Claude） | `explore`・`write`・`shell`（Codex） | `explore`・`write`・`shell`（Antigravity） | Codexの組み込み委譲先 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `rules/`配下 | 届く（ユーザー規範） | 届く（`~/.codex/AGENTS.md`） | 届く（親のメモリー階層。組み込み`Explore`・`Plan`を除く） | 届く（ユーザー設定の読込元） | 届く（`~/.codex/AGENTS.md`） | 届く（有効なプラグインの常時規範） | 届く（ユーザー設定の読込元） | 届く（`~/.codex/AGENTS.md`） | 届く（有効なプラグインの常時規範） | 届く（`~/.codex/AGENTS.md`） |
| ユーザーが`~/.claude/rules/`に置いた規範ファイル（`~/.codex/AGENTS.md`が埋め込まないもの） | 届く（ユーザー規範） | 届く（`~/.codex/AGENTS.md`の読込指示） | 届く（親のメモリー階層。組み込み`Explore`・`Plan`を除く） | 届く（ユーザー設定の読込元） | 届く（developer指示） | 届く（起動・再開指示） | 届く（ユーザー設定の読込元） | 届く（developer指示） | 届く（起動・再開指示） | 届く（`~/.codex/AGENTS.md`の読込指示） |
| `share/rules-main.md` | 届く（SessionStart） | 届く（SessionStart） | 届かない | 届かない | 届かない | 届かない | 届かない | 届かない | 届かない | 届かない |
| `share/rules-main.claude-code.md` | 届く（SessionStart） | 届かない | 届かない | 届かない | 届かない | 届かない | 届かない | 届かない | 届かない | 届かない |
| `share/rules-main.codex.md` | 届かない | 届く（SessionStart） | 届かない | 届かない | 届かない | 届かない | 届かない | 届かない | 届かない | 届かない |
| `share/rules-common.codex.md` | 届かない | 届く（`~/.codex/AGENTS.md`） | 届かない | 届かない | 届く（`~/.codex/AGENTS.md`） | 届かない | 届かない | 届く（`~/.codex/AGENTS.md`） | 届かない | 届く（`~/.codex/AGENTS.md`） |
| `share/rules-subagent.md` | 届かない | 届かない | 届く（SubagentStart） | 届く（システム指示） | 届く（developer指示） | 届く（指示の先頭） | 届かない | 届かない | 届かない | 届く（SubagentStart） |
| `share/rules-subagent.claude-code.md` | 届かない | 届かない | 届く（SubagentStart） | 届く（システム指示） | 届かない | 届かない | 届かない | 届かない | 届かない | 届かない |
| `share/agents-server-delegate-notice.md` | 届かない | 届かない | 届かない | 届く | 届く | 届く | 届く | 届く | 届く | 届かない |
| `share/agents-server-base.md` | 届かない | 届かない | 届かない | 届く（`system_prompt`のベース） | 届く（`baseInstructions`） | 届かない | 届かない | 届く（`baseInstructions`） | 届かない | 届かない |
| `share/agents-server-delegate.md` | 届かない | 届かない | 届かない | 届く | 届く | 届く | 届かない | 届かない | 届かない | 届かない |
| `share/agents-server-explore.md`・`agents-server-write.md`・`agents-server-shell.md` | 届かない | 届かない | 届かない | 届かない | 届かない | 届かない | 届く（同名のmodeだけ） | 届く（同名のmodeだけ） | 届く（同名のmodeだけ） | 届かない |
| `share/agents-server-auto-resume.md` | 届かない | 届かない | 届かない | 届く | 届く | 届く | 届く | 届く | 届く | 届かない |
| 作業ディレクトリのプロジェクト規範（`AGENTS.md`など） | 届く | 届く | 届く（親のメモリー階層。組み込み`Explore`・`Plan`を除く） | 届く（プロジェクト設定の読込元） | 届く | 届く | 届かない | 届かない（`project_doc_max_bytes=0`） | 届く | 届く |
| agent-toolkitのスキル | 届く | 届く | 未確認 | 届く | 届く | 届く（有効なプラグイン） | 届かない（`skills=[]`） | `write`・`shell`へ届く。`explore`へは届かない（プラグイン無効） | 届く（有効なプラグイン） | 未確認 |

OpenCodeの配送範囲は次の表とする。通常起動と軽量起動の区分は`agent-toolkit:delegation`の`references/runtime-routing.md`「実行手段」に従う。`task`は役割のmodeを使い、それぞれ対応する列に属する。

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
Antigravityのプラグイン配送は`antigravity_plugin.py`、起動指示はAntigravity backendと`launch_prompts.py`を根拠とする。
プロジェクト規範の自動読込の行は実機の観測から取った。
観測記録は`docs/development/audit-records.md`の「agent-toolkit/skills/writing-standards/references/agent-documents-basics.md：責務と構成：2026年10月6日」にある。

## 名指し参照の読込方法

本文の各参照の適用条件を保ち、選んだ資料の指定範囲を取得してから、その基準を使う具体的な判断へ進む。起動時の行の資料は起動本文とともに届く。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-documents-basics.md#「置き場所と配送」` | 判断 | `section:agent-toolkit:*/skills/writing-standards/references/agent-documents-basics.md#「置き場所と配送」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../delegation/references/runtime-routing.md#「実行手段」` | 判断 | `section:agent-toolkit:*/skills/delegation/references/runtime-routing.md#「実行手段」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../process-wi/references/all-repos-orchestration.md#「担当の役割」` | 判断 | `section:agent-toolkit:*/skills/process-wi/references/all-repos-orchestration.md#「担当の役割」` |

## Antigravityの通常起動

Antigravityのメインは有効化した`agent-toolkit-agy`プラグインから
共通の`rules/`、スキルとagentsを読み込む。
生成器は常時規範を`rules/AGENTS.md`と追加のalways-onファイルへ分割する。
`share/rules-main.md`は6イベントのhookアダプターが起動時に読み、
`PreInvocation`の動的コンテキストとして配送する。
ホスト別のClaude Code・Codex規範は通常起動へ配送しない。
`agents_server`の委譲先へ配送する本文は、上表と各backendの委譲プロンプトが定める。

Antigravityの委譲起動と再開は、通常・軽量とも保持したplugin root、CLIの起動情報と
`AUTO_RESUME_NOTICE`を渡す。役割別規範の境界は共通の`LAUNCH_SYSTEM_PROMPTS`に従う。
ユーザーの`~/.claude/CLAUDE.md`と`~/.claude/rules/`配下（`agent-toolkit/`を除く）は
起動・再開の指示へ境界付きで埋め込み、共通常時規範は有効なプラグインから届ける。
