# ルールの配置と配送の設計記録

[設計記録の索引](design.md)から参照する、ルールの配置と配送の記録である。

## ルールの配置と配送

agent-toolkitの規範は、全エージェントへ常に読み込ませるファイルを持たない。
全ての作業に当てはまる原則と硬い制約は入口のスキル`agent-toolkit/skills/atk-mode/SKILL.md`に置き、作業ごとの手順は入口の表から選ぶ`playbooks/`と`references/`に置く。
委譲先に共通の制約と返却の決まりは`playbooks/subagent.md`に置く。
置き場所と読む契機の表と、起動区分ごとの配送範囲表は`agent-toolkit/skills/atk-mode/references/agent-documents.md`が持つ。

`SessionStart`フックは、メインへ入口の起動を求める1行だけを出し、委譲先へは圧縮の後に`playbooks/subagent.md`と役割文書の読み直しを求める1行だけを出す。
`SubagentStart`フックは、全ホストで`playbooks/subagent.md`の絶対パスを示す1行を出す。
`agents_server`の委譲先は、`agent_toolkit/_agents_server/launch_prompts.py`が`playbooks/subagent.md`から見出しと標識で切り出した節を起動の本文として受け取る。
起動の本文には、通常の委譲で使う全体作業契約と、役割文書が宣言した資料の全文も入る（[読込表と起動時の配送](read-triggers-contract.md)「起動時の配送」）。
委譲先の判定は`agent_toolkit/_common/delegated_session.py`が持ち、`AGENT_TOOLKIT_DELEGATED_SESSION`が`1`の場合と`AGENT_TOOLKIT_OWNER_SESSION`が設定されている場合を`agents_server`の子とする。
後者を併用するのは、Codex backendがstatusline表示の判定のために子のApp Serverから前者を除くためである。
知識境界として、条文の本文は入口と手順書が持ち、フックは所在を示すだけ、`agents_server`は原本から節を切り出し、宣言された資料を入れるだけで、内容を判定しない。
Claude Codeの`additionalContext`文字列は10,000文字の上限へ合わせ、最大構成の追加文脈を同上限内に保つテストを`agent-toolkit/agent_toolkit/_hooks/rules_context_startup_test.py`へ置く。
ファイル保存と2,000文字のプレビューは別の出力契約として扱い、それぞれの観測条件と保証範囲を監査記録へ残す。
Codex backendの子のApp Serverへ`AGENT_TOOLKIT_DELEGATED_SESSION`を渡す案はClaude委譲先の中で起動したCodex委譲先がstatusline表示でClaude委譲先と誤判定されるため採らない。
全エージェントへ常に読み込ませる規範ファイルを廃止した経緯は、撤去のcommit本文にある。

Claude Codeが自動で読み込むユーザーとプロジェクトの規範は、Codexでは次のいずれかの手段で届けるか、対象外とする理由を記録する。
Codexへの配送手段を追加・変更する主体は、この対応で届かない規範が無いかを確かめる。
読込指示は`agent-toolkit/skills/atk-mode/playbooks/host-codex.md`「Claude Code向けに配置された規範の読込」が定め、Codexの各主体は入口から同書へ到達する。

| Claude Codeの自動読込対象 | Codexでの届け方 |
| --- | --- |
| `~/.claude/rules/myprojects-common.md` | `~/.codex/AGENTS.md`への埋め込み（`scripts/sync_codex_agents.py`の`PERSONAL_SOURCE`） |
| `~/.claude/rules/myprojects.md` | Codexのメインと組み込み委譲先には届けない。ホスト別のプロジェクト一覧と同期方針であり、`myprojects-common.md`冒頭がClaude Codeへの配布と定めるためである |
| `~/.claude/rules/`直下の`*.local.md`と`~/.claude/CLAUDE.md` | `host-codex.md`の読込指示 |
| プロジェクト直下の`.claude/rules/` | `host-codex.md`の読込指示 |
| 作業ディレクトリからルートまでの各ディレクトリの`CLAUDE.local.md`と、同階層に`AGENTS.md`が無い場合の`CLAUDE.md` | `host-codex.md`の読込指示 |

ユーザー単位の`*.local.md`と`~/.claude/CLAUDE.md`の2種類は実行ホストごとにユーザーが置くファイルであり、リポジトリから生成する`~/.codex/AGENTS.md`へ埋め込めない。
dotfilesがホストを限って配る`*.local.md`もある。
例は`.chezmoi-source/dot_claude/rules/ssh-peer-hosts.local.md.tmpl`で、`.chezmoidata.toml`の役割`ssh_peers`のホストだけに本文が出て、他のホストではファイルが作られない。
ホストごとに中身が違うため、これも同様に埋め込めない。
読込指示は実行時のファイルの有無によらず共通であるため、プロジェクト直下の`.claude/rules/`と同じ手段を選ぶ。
2026年10月6日に、`agents_server`で起動したCodexの委譲先が、`~/.claude/rules/`直下の`*.local.md`が使えないと定めたコマンドを実行して失敗した。
当時の読込指示はプロジェクト直下の3種だけを挙げ、ユーザー単位の規範を含めていなかった。
却下した代替案は次の2つである。
`SessionStart`・`SubagentStart` hookで`*.local.md`の本文を注入する案は、出力量がホストごとに変わる。
hookの通知へ規範の本文を差し込まない方針（`agent-toolkit/skills/atk-mode/references/agent-documents.md`「置き場所と配送」）にも反する。
作業に該当しないファイルも毎回の起動で文脈へ入り、hookの信頼登録を要する条件もCodexのメインに残る。
`agents_server`の`developerInstructions`へ本文を連結する手段だけで届ける案は、Codexのメインとネイティブのサブエージェントに届かず不足する。
この連結は`agents_server`の委譲先へ届ける手段として併用する（`design-agents-server-instructions.md`「委譲先への指示と起動時の規範」）。
会話圧縮の後は、読込指示が対象とする規範本文を再取得する。
ツールで読んだ本文は会話履歴の一部であり、圧縮で文脈から失われるためである。
起動・再開指示に同じファイルの本文が境界付きで含まれる場合は、読込指示の側で読み直さない。

容量超過時の整理は`agent-basics/skills/writing-standards/references/agent-documents-additions.md`「記述量の水準」に従う。
SessionStartの整理後の水準は、長いパス・最大の追加指示・作業契約の有無を組み合わせた最終出力で8,000文字以下とする。
超過時は固定の案内と可変の契約・パス・起動入力を分けて確認し、大入力の退避と予算内配送を維持する。
この水準はエージェントが選んだ整理時の目安であり、10,000文字の合否上限は変えない。
上限との差の2,000文字は段落の追記を吸収する余裕の見積もりであり、固定部分の必須条件を削る根拠にしない。
再検証は`agent-toolkit/agent_toolkit/_hooks/rules_context_startup_test.py`が使うパス長・OS・契約の組合せで最終`additionalContext`の文字数を比べ、8,000文字との比較は超過を是正する時だけ行う。
`agents_server`の各ツール説明とserver instructionsは4,096文字を合否上限とし、超過を是正する時だけ各3,200文字以下を整理後の目安にする（エージェントの選定値）。
上限以下の説明をこの目安だけで削らず、`tool_descriptions_test.py`の合否上限と全引数説明の検査を保つ。
超過時の再検証は、`list_tools`の登録済み説明とinstructionsを取得し、必須引数と最小例を残して目安へ到達したかを確かめる。
