# 規範の配送範囲

本書はエージェント向け文書の置き場所を選ぶ主体と、ある主体へ届く規範を確かめる主体へ、規範と資料がどの起動区分の主体へ届くかを示す配送範囲表を提供する。常時規範の定義と置き場所の分類は`agent-documents-basics.md`「置き場所と配送」が定める。

配送範囲表は、常時規範、配送文（`agents_server`が委譲先の起動時に渡す`share/agents-server-*.md`）、作業ディレクトリのプロジェクト規範およびagent-toolkitのスキルがどの主体へ届くかを示す次の表である。値は実装から取る。取得元は`agent_toolkit/`配下の`_hooks/rules_context.py`と`_agents_server/`の`launch_prompts.py`・`claude.py`・`codex.py`・`antigravity.py`と、`scripts/sync_codex_agents.py`である。規定を置く文書を選ぶときと、ある主体へ届く規範を確かめるときにこの表を使う。表の値と実装が一致しない場合は実装を正として表を直す

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
| agent-toolkitのスキル | 届く | 届く | 未確認 | 届く | 届く | 届かない | 届かない（`skills=[]`） | 届く | 未確認 | 未確認 |

Codexの列の`rules/`配下と`share/rules-common.codex.md`の値は`~/.codex/AGENTS.md`を配置した環境に限る（Codex単体のインストーラーはこのファイルを配置しない）。
Antigravityの委譲先は起動区分によらず`~/.gemini/GEMINI.md`と作業ディレクトリの`AGENTS.md`を読む。
ユーザーが`~/.claude/rules/`に置いた規範ファイルの行の読込指示は、Codexのメインと組み込み委譲先へは`~/.claude/rules/`直下の`*.local.md`に限る。`~/.claude/rules/myprojects.md`のようにClaude Code向けに配布するファイルは`agents_server`の委譲先へだけ届く。`~/.claude/CLAUDE.md`はこの行の連結の対象外とし、Claudeの各起動区分へはユーザー設定の読込元から、Codexの主体へは`~/.codex/AGENTS.md`の読込指示で届く。
Antigravityの列とプロジェクト規範・スキルの行は実機の観測から取った。
観測記録は`docs/development/audit-records.md`の「agent-toolkit/skills/writing-standards/references/agent-documents-basics.md：責務と構成：2026年10月6日」にある。
