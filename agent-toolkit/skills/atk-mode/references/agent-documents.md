# エージェント向け文書の置き場所と配送

agent-toolkitのエージェント向け文書を置く場所と、各起動区分のエージェントへ届く経路を定める。
作り方の基準は`agent-basics:writing-standards/references/agent-documents-basics.md`にある。
スキルの基準は`agent-basics:writing-standards/references/agent-skills.md`、役割文書とサブエージェント定義の基準は`agent-basics:writing-standards/references/sub-agents.md`にある。
本書は、それらへagent-toolkitが加える差分（置き場所、配送、スキルの実装、委譲先の文脈）だけを持つ。

## 置き場所と配送

agent-toolkitの規範は、全エージェントへ常に読み込ませるファイル（常時規範）を使わずに、読む主体と読む契機に合わせて次のどこかへ置く（パスは`agent-toolkit:atk-mode`のディレクトリから）。

| 内容 | 置き場所 | 読む契機 |
| --- | --- | --- |
| 全ての作業に当てはまる原則の要旨、硬い制約、ユーザーの方針、作業と手順書の表 | 入口の`SKILL.md`（5,000トークン以内） | 作業の前と会話の圧縮の後に起動する |
| 1つの作業の流れ（メインの報告、委譲の起動・待機・受領・復旧、commitなど） | `playbooks/` | 入口の表の条件が成立した時 |
| 1主題の形式の定義 | `references/` | 手順書の段が指した時 |
| ホストに固有の操作 | `playbooks/host-claude-code.md`、`playbooks/host-codex.md`（差の一覧は`references/hosts.md`） | そのホストで操作する時 |
| 委譲先に共通の制約と返却の決まり | `playbooks/subagent.md` | 委譲先の起動の本文として届く |
| 役割ごとの作業と返却形式、委譲元の起動と検収 | `roles/<役割名>.subagent.md`、`roles/<役割名>.parent.md` | 前者は起動の本文として届き、後者は委譲元が起動の前に読む |
| 実装者だけが使う仕組みの内部の仕様 | リポジトリの`docs/development/` | 配布しない |

- 全エージェントが要る内容を足す時は、入口の上限に収まるかを先に確かめ、収まらなければ入口の表から選ぶ手順書へ置く。
  全ての作業の読む量が増えるため、常に読み込ませるファイルの新設と、hookの通知への規範の本文の差し込みは避ける。
- 委譲元と委譲先の双方が守る受け渡し（状態行、続行不能の区分、通知、追加報告の行）は`playbooks/subagent.md`に置き、役割文書には役割に固有の結果欄と差分だけを書く。
- hookの通知、CLIの次の操作、MCPのツールの説明文に書くのは、検出した事実と求める操作だけとする。
- dotfilesで規範を編集する時の層の地図は`.claude/skills/edit-dotfiles-norms/references/norm-layer-map.md`にある。

### 入口を届ける経路

入口は、ホストの自動の起動と圧縮の後の挙動に頼らずに成り立つ経路で届ける。
Claude Codeの`Agent`ツールの委譲先でスキルが使えるかは確かめられておらず、圧縮の前に起動したスキルの本文が圧縮の後の文脈に無かった観測もあるためである。

- Claude Code、Codex、Antigravityのメイン: SessionStartのhookが入口の起動を求める1行を出し、圧縮の後の通知が入口と進めていた手順書の読み直しを求める。
- OpenCodeのメイン（hookとMCPを登録しない）: 導入時に設定の`instructions`へ登録するファイルが、plugin rootの読み替えと、作業の前と圧縮の後に`agent-toolkit-atk-mode`のスキルを読むことを数行で案内する。
- `agents_server`の委譲先（全modeと全ホスト）: 起動の本文に`playbooks/subagent.md`の内容と役割文書が入る。
- ホストの組み込み委譲機能の委譲先（`Agent`ツール、Codexの`spawn_agent`）: agent-toolkitの本文は自動では届かないため、委譲元が依頼文で`/atk-mode subagent <役割名>`の起動か役割文書の絶対パスを示す。
- 常駐のAWI処理と`atk run-skill`: 起動入力の本文に、起動する作業と引数を書く。

### 配送範囲表

各起動区分のエージェントへどの文書が届くかを示す。
値は実装から取り出しており、表と実装が一致しない時は実装を正として表を直す。
出所は、hookの`agent_toolkit/_hooks/rules_context.py`と、`agent_toolkit/_agents_server/`の各backendと`launch_prompts.py`である。
ホストごとの生成と導入は`scripts/sync_codex_agents.py`、`pytools/_internal/antigravity_plugin.py`、`pytools/_internal/install_opencode_toolkit.py`が担う。

| 文書 | Claude Codeのメイン | Codexのメイン | `agents_server`の`task`・`delegate` | `agents_server`の`explore`・`write`・`shell` | `Agent`ツールの委譲先 | Codexの組み込み委譲先 |
| --- | --- | --- | --- | --- | --- | --- |
| agent-toolkitのスキル（入口を含む） | 届く | 届く | 届く（Antigravityは有効なプラグインから。OpenCodeは自動登録せず、plugin rootの`skills/`から明示的に読む） | Claudeは届かない（`skills=[]`）。Codexは`write`・`shell`だけ（`explore`はプラグイン無効）。Antigravityは届く | 未確認 | 未確認 |
| `playbooks/subagent.md`と役割文書 | 届かない | 届かない | 起動の本文 | 起動の本文 | 依頼文の指示で読む | 依頼文の指示で読む |
| ユーザーが`~/.claude/rules/`に置いた規範 | 届く（ユーザー規範） | `~/.codex/AGENTS.md`の読込指示（直下の`*.local.md`だけ） | Claudeはユーザー設定の読込元、Codexはdeveloper指示、Antigravityは起動・再開の指示、OpenCodeは指示の先頭 | 同左 | 親のメモリー階層（組み込みの`Explore`・`Plan`を除く） | `~/.codex/AGENTS.md`の読込指示（直下の`*.local.md`だけ） |
| 作業ディレクトリのプロジェクト規範（`AGENTS.md`など） | 届く | 届く | Claude、Codex、Antigravityは届く。OpenCodeは未確認 | Claudeは届かない。Codexは届かない（`project_doc_max_bytes=0`）。Antigravityは届く。OpenCodeは未確認 | 届く（組み込みの`Explore`・`Plan`を除く） | 届く |

- `~/.claude/rules/myprojects.md`のようにClaude Code向けに配布する規範は、Codexでは`agents_server`の委譲先だけに届く。
- `~/.claude/CLAUDE.md`は、Claudeの各起動区分へはユーザー設定の読込元から、Codexのエージェントへは`~/.codex/AGENTS.md`の読込指示から届く。
- Antigravityのメインと委譲先は、`~/.gemini/GEMINI.md`と作業ディレクトリの`AGENTS.md`も読む。
- OpenCodeの委譲先でプロジェクト規範が要る時は、要る本文を依頼文へ含める。
- `atk wi process-loop`が対話CLIで起動する統括はメインの列、統括が`agents_server`で起動するリポジトリ担当は`task`・`delegate`の列の配送を受ける。
  リポジトリ担当の対象リポジトリでのメインとしての責務は、`agent-toolkit:atk-mode/playbooks/process-wi-all-repos.md`「担当の役割」が定める。
- プロジェクト規範の自動読込の行は実機の観測による。
  記録は`docs/development/audit-records-agent-documents.md`の「agent-basics/skills/writing-standards/references/agent-documents-basics.md：責務と構成：2026年10月6日」にある。

## スキルの実装

agent-toolkitのスキルと手順書を書く時の差分である。

### スキル間操作

ユーザーへの完了報告の内容と形式は`agent-toolkit:atk-mode/playbooks/report-completion.md`が定める。
手順書と個別のスキルの中では、完了報告の手順書の実行を指示するだけにとどめる。
手順書の側で報告を完結させると工程がそこで終わり、完了報告の手順が実行されなくなるためである。

### ユーザーが起動するスキルの着手手順

着手前に確かめたい事項は、実際に必要になった時点で尋ねるほか、デフォルトの動作を採用して進め、採用した動作を事後の報告で伝えてもよい。
確認を実際に必要になった時点で行う規則は`agent-basics:writing-standards/references/agent-skills.md`「ユーザーが起動するスキルの着手手順」にある。

## コンテキスト境界

委譲先の文脈は委譲元から独立しており、委譲元の会話履歴、読み込み済みのファイル、起動済みのスキルは委譲先の入力の外にある。
下位の担当を起動する場合も同じである。

- `agents_server`の委譲先が受け取るのは、依頼文と、起動の本文（`playbooks/subagent.md`の内容と役割文書）と、本書「配送範囲表」の各行に限られる。
  委譲先が要る入力は依頼文に書く。
- 会話履歴を継ぐforkは例外であり、選び方と起動は`agent-toolkit:atk-mode/playbooks/delegation.md`、Codexの`fork_turns`の指定は`agent-toolkit:atk-mode/playbooks/host-codex.md`に従う。
  役割文書を渡す起動は、会話履歴を継がない独立の文脈で行う。
- 会話履歴、プロジェクトの`AGENTS.md`、hookの通知に規範が含まれる場合も、委譲先に共通の決まりは起動の本文で届ける。
  委譲先に共通の決まりの原本は`playbooks/subagent.md`の1つであり、`agents_server`の全modeとCodexとOpenCodeの起動へ同じ原本から届ける。
- Claude Codeに固有の委譲先向けの記述は、Claude Codeの委譲先だけへ届ける。

監査記録は`docs/development/audit-records-delegation.md`の「agent-toolkit/skills/delegation/references/sub-agents-context.md：コンテキスト境界：2026年9月27日」にある。
