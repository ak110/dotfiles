# ホスト間の規範配置と適用境界の設計記録

本書は[設計記録の索引](design.md)から主題別に分割した記録であり、機構の目的、構造の理由、知識境界と却下した代替案を保持する。
実行時に適用する規範は、各節が参照する現行のルールファイルとスキルが定める。

## Claude CodeとCodexの規範配置

### 共有原本とCodex固有の上書き

hook・MCP定義などホスト別に明確に分離された資源を除き、Claude CodeとCodexに共通するルール・スキルは`agent-toolkit/`の共有原本で定義する。
Codexだけの公開能力との差分は上書きとして置き、常時規範へCodex固有の条件を持ち込まない。Codexの全主体へ適用する差分は`agent-toolkit/share/rules-common.codex.md`へ置き、`scripts/sync_codex_agents.py`が`~/.codex/AGENTS.md`へ埋め込む。Codexのメインだけへ適用する差分は`agent-toolkit/share/rules-main.codex.md`へ置き、`SessionStart` hookがCodexのメインへ`rules-main.md`の後に加える。`~/.codex/AGENTS.md`はCodexのサブエージェントと委譲先にも届くため、メイン向けの差分を同じファイルへ置くとメイン専用の条文が全主体へ届く。
Codex基礎指示の上書きは、確認・待機・並列化・ツール利用前説明のホスト契約をCodex側へ閉じ込め、Claude Codeの共通契約を変更しない。
Codexの委譲は、`spawn_agent`・`send_message`・`followup_task`・`wait_agent`・`interrupt_agent`を使うネイティブ方式とagents_server方式を分ける。ネイティブ方式では`fork_turns`が選ぶ会話履歴、worktreeの`AGENTS.md`、`SubagentStart` hookが配送する`rules-subagent.md`を独立した入力とする。agents_serverの通常委譲は`_agents_server/launch_prompts.py`から同じ共通委譲先規範を配送する。各起動区分の委譲先へ届く規範は`agent-toolkit/skills/writing-standards/references/delivery-scope.md`の配送範囲表が示す。Claude Code固有の委譲先規範はCodexへ配送しない。この境界をhookの実起動、agents_serverのprompt構成、Codex manifestの生成テストで確認し、規範本文を方式ごとに複製しない。
`scripts/sync_codex_agents.py`はCodex基礎指示と共有ルールから生成物を作成し、`scripts/sync_generated_files.py`が正式な一括生成の窓口となる。
`scripts/sync_codex_agents_test.py`は共有原本と生成物の同期、共有契約の保持およびCodex固有上書きの配置を確かめる。
生成物は手編集せず、原本と正式生成器を更新して同期する。

共有ルールをCodex固有条件で分岐する案は、Claude Codeへ不要な差分を配布して共通契約を曖昧にするため採用しない。
共有文書をCodex用に複製する案は、原本・生成器・自動テストの同期対象を増やすため採用しない。
Codex事情を共有ルールへ直接改訂する案は、Claude Codeへホスト固有の挙動を波及させるため採用しない。

### plugin manifestと生成物

公開動作を追加または変更するpluginは、`agent-toolkit/.claude-plugin/plugin.json`を版数の基準とし、`agent_toolkit_bump.py minor`などの正式な手順で版数を更新する。
`.claude-plugin/marketplace.json`のplugin記述は基準の版数と一致させる。
`agent-toolkit/plugin.json`と`agent-toolkit/.codex-plugin/plugin.json`は`sync_generated_files.py`で生成する。
`agent-toolkit-codex/`はGitで追跡せず、post-applyがCodex plugin導入の直前に`pytools/_internal/codex_plugin_manifests.py`で生成する。
同ディレクトリは`agent-toolkit/`を原本とする自己完結型の通常ディレクトリであり、Agent Plugins用の直下`plugin.json`と`mcp.json`を除外する。
`.agents/plugins/marketplace.json`のCodex local sourceだけを`./agent-toolkit-codex`へ向け、Claude CodeとAgent Pluginsのsourceは`agent-toolkit/`のまま維持する。
手動生成には`scripts/sync_codex_plugin_manifests.py`を実行する。
生成後は同scriptの`--check`でClaude Code向け原本、marketplace記述、Codex向け派生manifest、`agent-toolkit-codex/`全体の一致を確認する。
プラグインマニフェストの検証は、Claude Code向けとCodex向けで到達できる保証の水準が異なるため手順を分ける。
Claude Code向けは`claude plugin validate --strict`を`pyfltr`のカスタムlinter`claude-plugin-validate`から実行し、未知フィールドとメタデータ欠落を失敗として扱う。
Codex向けはCodex同梱の`plugin-creator/scripts/validate_plugin.py`を`pytools/_internal/codex_plugin_manifests_test.py`から実行し、指摘の集合が既知の2件と完全に一致することを確かめる。
Codexの検証器を無条件の合格条件にしないのは、同梱資料が`hooks`をマニフェストの正規フィールドとして定義しながら、検証の節では未対応フィールドとして拒否すると述べ、同一資料内で矛盾しているためである。
資料はローカル導入での受理を保証も否定もせず、現行manifestのままの導入状態は`installed: true`かつ`enabled: true`である。
そこで検証器の指摘を削除するのではなく既知の逸脱の集合を固定し、集合が変化した時点で失敗させて再判断の契機とする。
知識境界として、期待する逸脱の集合と許容根拠はテスト側が持ち、マニフェストの生成規則は`pytools/_internal/codex_plugin_manifests.py`が持つ。
却下した代替案は、`hooks`を除去し`mcpServers`を`.mcp.json`へ解決させて検証器を無条件に合格させる案である。Codexのプラグインフック機能を失い、`agent-toolkit/.mcp.json`との名前衝突を解消する追加設計を要する一方、得られるのは資料上の保証が無い体裁上の適合だけであるため採用しない。

### Codexのlocal plugin導入

dotfilesの`post_apply`によるローカルagent-toolkit導入は、Codex CLIを準備した後に`agent-toolkit-codex/`を生成し、マーケットプレイス登録とplugin導入をCodex公式CLIへ委譲する。
`agent-toolkit-codex/`の生成に失敗した場合は、post-applyのstep失敗として永続logへ記録し、全体を非0で終了する。
`install_codex_plugins.py`は原本manifestの版数と`codex plugin list --json`の導入状態を比較し、未導入、無効、版数不一致のいずれかの場合だけ`codex plugin add <plugin-id>`を実行する。
CLI成功後は同コマンドで`codex plugin list --json`を取得し、版数一致と有効状態を検証する。
実際のaddまたはupdateとhook状態の確認を完了した後、daemonが稼働中であれば再起動方針を1回だけ適用する。
環境変数を指定しない更新では手動再起動の案内を表示し、`DOTFILES_CODEX_DAEMON_AUTO_RESTART=1`を明示した更新だけ`codex app-server daemon restart`を自動実行する。
自動再起動の成功と失敗は終了コードとともにupdate-dotfilesログへ記録し、失敗時はplugin導入を巻き戻さず手動案内へ戻す。
無変更、marketplace登録だけの変更、daemon停止中および不要pluginの除去は自動再起動の対象に含めない。
`atk config`へ設定を追加する案は、dotfiles更新処理だけが消費する真偽値のためにagent-toolkit pluginとpytoolsの設定契約を結合するので採用しない。
公式資料の[Plugins](https://developers.openai.com/plugins/build/plugins)はローカルpluginを`~/.codex/plugins/cache/<marketplace>/<plugin>/<version>/`へ導入し、マーケットプレイス登録元ではなく導入先の実体をCodexが読み込むと定める。
そのため、`install_codex_plugins.py`はcacheを直接編集せず、Codexが管理する現行versionだけを導入先として利用する。
CLI導入後の検証が終わるまでlegacy skillリンクを除去せず、リンク除去に失敗した場合だけ変更前snapshotから復元する。
`plugin add`後に失敗してもCodexの管理状態を自前で書き戻さない。
Codex側では外部pluginを導入せず、不要な`compact-plus@compact-plus`が導入済みの場合だけ`codex plugin remove`で除去する。
除去成功時にdaemonが稼働していれば再起動案内を追加し、一覧取得または除去に失敗した場合はローカルplugin処理を継続する。
Codex側の外部marketplace登録、外部plugin導入、取得元検証は対応先がないため撤去する。
Claude Code側の外部plugin管理は`install_claude_plugins.py`で維持する。
post-apply案内とWindowsのjunction除去処理は維持する。
cache version台帳、全過去versionの保持、POSIXの原本接続、Windowsの原本接続とファイル同期、cache退避・置換・復元は、Codex CLIが実体導入を担当するため削除する。

シンボリックリンク方式は代替案として却下する。
Codex 0.154.0はroot直下のAgent Plugins用`plugin.json`を`.codex-plugin/plugin.json`より優先し、同一rootから導入した場合は`hooks/list`が0件となる。
また、相対シンボリックリンクを含むCodex専用wrapperを公式CLIで導入すると、snapshotには`.codex-plugin`だけが残り、リンク先のhook・skill・実行資源が含まれない。
そのため、`agent-toolkit-codex/`は全資源を通常ファイルとして生成し、公式CLIへsnapshotと版数別cacheの管理を委ねる。
検証条件と再検証手順は「[docs/development/design-hosts.md：Claude CodeとCodexの規範配置：2026年9月13日](audit-records.md#docsdevelopmentdesign-hostsmdclaude-codeとcodexの規範配置2026年9月13日)」を参照する。

### ルールの配置と配送

`agent-toolkit/rules/`配下にはメインエージェント、サブエージェントおよび委譲先の全てへ適用する条文だけを置く。
メインエージェントだけに適用する条文は`agent-toolkit/share/rules-main.md`へ置き、Claude Code固有分は`agent-toolkit/share/rules-main.claude-code.md`へ置く。
サブエージェントと委譲先だけに適用する条文は`agent-toolkit/share/rules-subagent.md`へ置く。
メイン向け条文は`SessionStart`フック（`agent-toolkit/agent_toolkit/_hooks/rules_context.py`）が全ての`source`で文脈へ追加し、会話圧縮の後も再度追加する。
サブエージェント向け条文は`SubagentStart`フックが全てのagent種別へ追加する。`agents_server`の通常起動の委譲先には、`agent-toolkit/agent_toolkit/_agents_server/launch_prompts.py`の`DELEGATE_SYSTEM_PROMPT`が同じ条文を連結し、両backendのシステム指示として渡す。
`SessionStart`フックは`AGENT_TOOLKIT_DELEGATED_SESSION`が`1`の場合と`AGENT_TOOLKIT_OWNER_SESSION`が設定されている場合を`agents_server`の子と判定し、メイン向け条文を追加しない。
後者を併用するのは、Codex backendがstatusline表示の判定のために子のApp Serverから前者を除くためである。
知識境界として、条文の本文は`share/`配下の各ファイルが持ち、フックと`agents_server`は本文を読んで渡すだけで内容を判定しない。
Codex向けAGENTS.mdの生成器は`rules/`配下の共通条文だけを埋め込み、`share/`配下の条文を埋め込まない。
Claude Codeのフック出力は1件あたり10,000文字で切り詰められるため、メイン向け条文の合計を同上限内に保つテストを`agent-toolkit/agent_toolkit/_hooks/rules_context_test.py`へ置く。
却下した代替案は3つある。
Codexメイン向け条文をAGENTS.mdへ生成器で埋め込む案は、Codexのサブエージェントにもメイン向け条文が届き、両ホストで配布手段が非対称になるため採らない。
委譲先向け条文もフックだけで届ける案は、Codex backendの委譲先でフックの信頼登録と環境変数の印に依存し、未設定の環境で条文が欠落するため採らない。
Codex backendの子のApp Serverへ`AGENT_TOOLKIT_DELEGATED_SESSION`を渡す案はClaude委譲先の中で起動したCodex委譲先がstatusline表示でClaude委譲先と誤判定されるため採らない。

Claude Codeが自動で読み込む規範は、Codexでは次のいずれかの手段で届けるか、対象外とする理由を記録する。
Codexへの配送手段を追加・変更する主体は、この対応で届かない規範が無いかを確かめる。
読込指示は`agent-toolkit/share/rules-common.codex.md`「Claude Code向けに置かれた規範の読込」が定める。

| Claude Codeの自動読込対象 | Codexでの届け方 |
| --- | --- |
| `~/.claude/rules/agent-toolkit/`配下（`agent-toolkit/rules/`の共通条文） | `~/.codex/AGENTS.md`への埋め込み（`scripts/sync_codex_agents.py`） |
| `share/`の主体別条文（`rules-main.md`・`rules-subagent.md`） | `SessionStart`・`SubagentStart` hookと、`agents_server`の委譲先では`developerInstructions` |
| `~/.claude/rules/myprojects-common.md` | `~/.codex/AGENTS.md`への埋め込み（同スクリプトの`PERSONAL_SOURCE`） |
| `~/.claude/rules/myprojects.md` | Codexのメインと組み込み委譲先には届けない。ホスト別のプロジェクト一覧と同期方針であり、`myprojects-common.md`冒頭がClaude Codeへの配布と定めるためである |
| `~/.claude/rules/`直下の`*.local.md`と`~/.claude/CLAUDE.md` | `~/.codex/AGENTS.md`からの読込指示 |
| プロジェクト直下の`.claude/rules/`、`AGENTS.md`が無い場合の`CLAUDE.md`、`CLAUDE.local.md` | `~/.codex/AGENTS.md`からの読込指示 |

ユーザー単位の`*.local.md`と`~/.claude/CLAUDE.md`の2種類は実行ホストごとにユーザーが置くファイルであり、リポジトリから生成する`~/.codex/AGENTS.md`へ埋め込めない。読込指示は実行時のファイルの有無に依存しないため、プロジェクト直下の`.claude/rules/`と同じ手段を選ぶ。
2026年10月6日に、`agents_server`で起動したCodexの委譲先が、`~/.claude/rules/`直下の`*.local.md`が使えないと定めたコマンドを実行して失敗した。当時の読込指示はプロジェクト直下の3種だけを挙げ、ユーザー単位の規範を含めていなかった。
却下した代替案は次の2つである。
`SessionStart`・`SubagentStart` hookで`*.local.md`の本文を注入する案は、出力量がホストごとに変わり、hookの出力上限を外せるhandlerの条件（出力量が条文ファイルで固定され、上限を確かめるテストで拘束される）を満たさない。作業に該当しないファイルも毎回の起動で文脈へ入り、hookの信頼登録への依存もCodexのメインに残る。
`agents_server`の`developerInstructions`へ本文を連結する手段だけで届ける案は、Codexのメインとネイティブのサブエージェントに届かず不足する。この連結は`agents_server`の委譲先へ届ける手段として併用する（`design-agents-server.md`「委譲先への指示と起動時の規範」）。起動時の指示に同じファイルの本文が境界付きで含まれる場合は、読込指示の側で読み直さない。

`agents_server`の`start`で起動したClaude backendの委譲先は、`~/.claude/rules/agent-toolkit/`配下の3ファイルの全文をユーザー規範として保持し、起動時のシステム指示と区別する。
また、`agent-toolkit/share/rules-subagent.md`を起動時のシステム指示として保持する。
`agent-toolkit/share/rules-main.md`は保持しない。
2026年9月7日に`agents_server`の`start`で委譲先を1件起動し、受領したエージェント向け文書の一覧と個別条文の有無を返させて確認した。
再検証は同じ起動を1件行い、`02-agent-operations.md`の見出しと`02-agent-operations.md`の任意の条文の有無を返させる。
委譲先へ届く条文の範囲は実際に動かして確定し、届いている条文を`agent-toolkit/share/rules-subagent.md`へ重複して置かない。

## 認証情報の扱いとauto mode検証

ユーザーの認証情報ファイルは、設定ディレクトリを変えない場合にホストが読む位置に残し、委譲の作業領域へ移さない。
配布設定はClaude Codeの組み込み`Read`を拒否し、常時規範は委譲元と委譲先による再配置を禁止する。
委譲手順は認証を要する検証で設定ディレクトリを変えず、ホストが通常読む認証情報を使う。
組み込み`Read`の拒否はBash経由の複製を遮断しないため、本設計は配布設定・常時規範・委譲手順の3層へ防止の役割を分担させる。

認証を要するauto mode検証では、`auto-mode config`が現在の設定反映を確認する。
配布元の対象固有テストは対象の配布元が所有する契約をチェックし、比較可能な`critique`だけが新規・悪化指摘を検出する。
各チェックは担当する契約だけを知り、対象固有テストをauto mode classifierの判定のテストとして扱わない。
比較不能な総合出力または実在しないauto mode classifierのテストを必須にする案は、変更の成否を識別できないため採用しない。

## ユーザーとの認識合わせ

`agent-toolkit:user-confirmation-and-report`「認識合わせ」は、メインが理解している目標と問題を自分の言葉で提示し、
その理解をその場で確かめる起点を担う。質問の構成、回答の取得、無回答時の退避は同スキルの「確認要否の判定」「手段の選択」が扱い、回答後の見直しは既存のユーザー指摘の受領へ接続する。
協調・自律の両モードを対象とし、ホストの質問機能と期限の差を共通の確認経路で扱う。

2026年10月6日まで、この手順は独立したスキル`agent-toolkit:realign-with-user`が持ち、自律モードの扱いを同スキルと`agent-toolkit:user-confirmation-and-report`が互いに参照し合っていた。確認規範を1つのスキルへ集約するユーザーの指示と確認回答（同日）により、手順を`agent-toolkit:user-confirmation-and-report`の節と「手段の選択」の表の認識合わせの行へ移し、スキルを廃止した。`/agent-toolkit:realign-with-user`による手動起動が無くなることは、同じ確認回答で了承済みである。
確認の要否は同スキル「確認要否の判定」が、目的・期待結果・認可の確定と、その範囲内の手段の選択を分けて決める。2026年10月3日まで、ユーザーだけが持つ値の違いで推奨案が変わるかの1判定とし、値ごとの推奨案を書けない問いを確認不要へ閉じていた。この扱いは未特定の不確実性と、確認済みで推奨が変わらない状態を区別できず、UWI書式とホスト別規範にも同じ扱いが再掲されていた。共通の判定を一般化し、直接消費側を参照へそろえた。技術判断をユーザーへ委ねる反復を防ぐ従来の目的は、目的と認可が確定した範囲の手段を自ら決める扱いで保つ。確認を一律に増やす案と、手順の追記だけで済ませる案は採らない。

## 規範の集約先と参照方向

WI処理の運用形態、登録と回答、振り返りからの投入の流れをエージェントが横断して参照するため、`agent-toolkit:workflow-overview`をオンデマンドの知識スキルとして置く。人間向けの`docs/guide/claude-code-guide.md`はエンドユーザーの導入と操作の案内を保つ。プラグインからdotfiles固有のガイドを参照できない配布範囲と、全工程が読む`share/workflow-phases.md`の読込費用を分けるためである。ガイドだけを読む契機の追加、ガイドの内容をプラグインへ移して縮める案、工程責務の契約へ運用概要を追記する案は、この境界または読込費用を守れないため採用しない。

複数の実行主体が使う契約は、契約本文を1箇所の基準文書へ集約し、各主体の文書には読む時点と利用場面固有の差分だけを残す。一括取得は`agent-toolkit:add-awi-by-user`、採否レコードは`agent-toolkit:process-wi`、履歴書換えは`agent-toolkit:commit`、レビュー観点は`agent-toolkit:review-standards`が定める。スキル本体には起動判断、工程順序および停止条件を残し、実施時だけ必要な表記規則や欄仕様は`references/`へ分離する。

判断から一意に導出できる実装順序・分解粒度・コマンド列は、安全性、データ保全または公開契約を保護する場合を除き、各エージェント文書へ常設しない。

委譲元は割当と認可を知り、委譲先は自身の実行手順と出力を知る。この知識境界を維持するため、同じ契約を`<役割名>.parent.md`とagent定義へ複製しない。定義元の指定と本文複製を併存させる案は同期の抜けを残すため採用しない。共通文書を契約ごとに新設する案も参照段数を増やすため、既存の責務所有者へ集約できる場合は採用しない。

委譲の名前付き入力は、`agents_server`の`start`、委譲先での取得結果、`<役割名>.subagent.md`が定める権限と入力および他の受領値から導出できない状態だけを持つ。省略時の値と行が無い場合の解釈は`<役割名>.subagent.md`が定め、`<役割名>.parent.md`はその省略時の値と異なる場合だけ送信する。この分離は値の二重所有を避けるための契約であり、`start`の`cwd`や委譲先がGitから得る値を送信一覧へ再掲しない。

委譲の継続可否条件は`agent-toolkit/skills/delegation/references/runtime-routing.md`「継続と新規起動」だけで定め、他の文書へ再掲しない。
`agent-toolkit/share/exec.parent.md`は工程順と渡す入力だけを定め、判定条件は同節を参照する。
同じ判定条件を複数の文書へ複製した結果、暫定修正で1文書だけが旧規定のまま残り、実装担当が矛盾を報告する事象が発生したためである。

サブエージェントを起動する手順は、`<役割名>.parent.md`と`<役割名>.subagent.md`のペアへ分け、委譲先の所在にかかわらずプラグインの`share/`配下へ置く。複数のスキルから同じ役割を起動する場合も、いずれの委譲元も`${CLAUDE_PLUGIN_ROOT}/share/<ファイル名>`という同じ形式で参照先を解決できる。`<役割名>.parent.md`は起動手順、渡す入力、受領する報告と検収条件だけを持ち、委譲先固有の作業手順を複製しない。`<役割名>.subagent.md`は責務、入力、実行手順および出力形式だけを持ち、委譲元の割当・認可判断を複製しない。
却下した代替案は、`<役割名>.subagent.md`を各スキルの`references/`配下へ置く案である。複数のスキルから参照される役割では文書の所在が一意に定まらず、同じ役割の起動手順が2箇所へ分かれるため採らない。

スキル本体が持つ規範を`references/`配下へ移す統合では、`references/`配下の資料が互いに規範を委ねる状態が構造的に生じる。このため、資料間の委譲は`SKILL.md`が同じ読込条件で併読を定める兄弟資料に限って認め、併読を定めない兄弟資料へは規範を委ねない。委譲の可否を決める責務は`SKILL.md`の読込条件が持ち、委ねる側と委ねられる側を同じ工程へ並べる。
移設対象の全参照を各資料へ自己完結させる案は採用しない。同じ規範本文を複数の資料へ複製することになり、定義の単一性を失うためである。

失敗・拒否・上限超過・遮断のときだけ実行する回復手順は異常が起きたときに読み手へ届く手段へ置く。異常の有無にかかわらず全文を読む文書には起動契機だけを残す（`agent-toolkit:writing-standards`の`references/agent-documents-additions.md`「失敗時の回復手順」）。この配置の根拠は常時の文脈に置いた失敗の教訓が平常時の成績を下げるという報告である。論文arXiv 2610.02994は、失敗の教訓を常時の文脈から除くとWebShopとMind2Web Replayの成績が上がったと報告する。評価したモデルはQwen3.5-9BとGPT-OSS-120Bに限られるため、規範側は努力目標として扱う。

## 場面別手順と外部投稿前レビュー

managed-temp、品質確認、検索の手順は、それぞれ`agent-toolkit:managed-temp`、`agent-toolkit:check-execution`、`agent-toolkit:search`が所有する。作業ツリー外の秘匿値ファイルは`agent-toolkit:secret-files`が扱う。作業ツリー内のファイルは通常の手段で扱い、外部の認証情報を参照する場合は実体の位置で内外を判定する。実行時に必要な場面を各スキルの`description`で示す。手順本文を場面別スキルにまとめ、常時規範から長文を読む工程を減らす。委譲の親子契約は前節の境界に従って引き続き`share/`へ置く。スキル起動の判定は`description`に依存するため、常時規範からの決定的な読込契機は持たない。

第三者が読む外部サービスへ起草した文面を投稿する前には`agent-toolkit:external-write-review`を用いる。投稿する主体が文面、投稿先、目的と根拠の所在を確定し、読み取り専用の投稿前レビュー担当へ文脈を持たない読者の観点で内容の確認を依頼する。投稿前レビュー担当は文面の採否と投稿認可を持たず、投稿主体が指摘を反映して送信する。投稿先ごとにレビュー手順を複製する案と常時規範から別の`share/`手順を読む案は、同じ判断の保守先と読込量を増やすため採用しない。
