---
name: agent-toolkit-edit
user-invocable: false
description: >
  `agent-toolkit/`配下のプラグイン（スキル・サブエージェント・フックスクリプト・marketplace記述）、
  `agent-toolkit/rules/`配下のルールファイル（配布先`~/.claude/rules/agent-toolkit/`）、
  `.claude-plugin/marketplace.json`を編集するときに使う。
  版数更新・marketplace管理・セッション状態フラグの扱いを含む。
---

# agent-toolkit（Agent Plugins・Claude Code・Codex）

本スキルは`agent-toolkit/`配下の配布物と`.claude-plugin/marketplace.json`を編集するエージェントへ、ファイル構成と参照方向、付帯作業の扱い、版数更新、配布と同期の規約を提供する。
エージェント向け文書の改訂に共通する手順（編集前に読む記録、規範を削除・縮小するときの消失確認など）は`dotfiles-norm-edit`が扱う。

## 読込表

| 時点または条件 | 全文読む資料 |
| --- | --- |
| WI処理の工程や運用を担うスキル、`share/`配下の`<役割名>.subagent.md`または`atk wi`の実装を編集する前 | `docs/guide/claude-code-guide.md`のうち編集対象と同期する節（少なくとも「推奨ワークフロー」）。あわせて`agent-toolkit:workflow-overview`を起動する |
| `agent-toolkit/agent_toolkit/agents_server_mcp.py`、`agent-toolkit/agent_toolkit/_agents_server/`配下または`rust/claude-statusline/src/agents_server.rs`の実装を変更または調査する前 | `references/agents-server-shared-state.md` |
| `agents_server`が起動した委譲先が動かない事象（起動の失敗、初期化の未到達、委譲先の無応答、結果の欠落）を調査する前 | `references/agents-server-investigation.md` |
| `agent-toolkit/agent_toolkit/_atk/serve/static/`配下のCSS・HTML・JavaScriptを変更する前 | `references/atk-serve-static.md` |
| 次のいずれかを変更する前と、作業ツリーで改訂した配布物の挙動を確かめる前: `agent-toolkit/agent_toolkit/`・`agent-toolkit/scripts/`の配置とimport、`atk`のサブコマンドと出力、MCPサーバー識別子とツール名、hookの実装と登録、プラグイン内の実行時パス、権限設定、marketplaceと配布の手順 | `references/distribution-and-hooks.md` |
| `agent-toolkit/`配下を変更対象に含む計画を起草する前 | `references/version-bump.md`の「plan modeでの取り扱い」節 |
| 全レーン後に版数を更新する時点と、`references/version-bump.md`の手順へ入る前 | `agent-toolkit/skills/process-wi/references/finish-session.md` |

## ファイル構成と参照方向

- `agent-toolkit/`: Agent Plugins・Claude Code・Codexが共有するプラグインルート
- `agent-toolkit/rules/`: ルールファイル（`01-agent.md`は基本原則、`02-agent-operations.md`は製品横断の実行運用）。メイン、サブエージェント、委譲先の全てへ適用する条文だけを置き、サブディレクトリを設けない（`scripts/gen_install_files.py`は直下の`*.md`だけを配布一覧へ列挙する）。配布先と同期は`dotfiles-repo-layout`「ディレクトリ構造の注意」が扱う
- `agent-toolkit/share/rules-main.md`・`rules-main.claude-code.md`・`rules-main.codex.md`: メイン向けの共通規範とホスト別規範
- `agent-toolkit/share/rules-common.codex.md`: Codexの全エージェント向けの規範
- `agent-toolkit/share/rules-subagent.md`・`rules-subagent.claude-code.md`: 委譲先向けの共通規範とClaude Code固有規範（Codex固有差分が要るときは`rules-subagent.codex.md`を追加する）。振り分けは`agent-toolkit:writing-standards`の`references/agent-documents-additions.md`「規範追記時の判定」に従う
- 配布物完結の環境変数は`AGENT_TOOLKIT_<PURPOSE>`、個人環境完結は`DOTFILES_`を接頭辞とする。一覧は`<plugin root>/skills/writing-standards/references/claude-hooks.md`にある

参照方向はdotfilesリポジトリ→プラグインと、プラグイン↔ルールファイルを許容する。
`pytools/`はagent-toolkitを同じリポジトリからeditable導入で解決するため、`_`で始まるprivateなサブパッケージもimportしてよい。privateなモジュールを移動・改名する変更では同じ変更単位で`pytools/`のimportもそろえる。
配置は読込時点と成立条件の依存先で決める（`agent-toolkit:writing-standards`の`references/agent-documents-basics.md`「責務と構成」）。プロジェクト固有の前提（ツール・データ・命名・CI・運用など）に依存する内容はプロジェクト側へ置き、固有要素を同種の任意要素へ置換しても判定・工程順・停止条件が成立する内容だけを配布物候補とする。

## 付帯作業の扱い

`agent-toolkit/rules/`、`agent-toolkit/skills/`、`agent-toolkit/share/`配下のエージェント向け文書はエンドユーザーへ配布する成果物そのものである。対象はルール、`SKILL.md`、`references/`、`<役割名>.parent.md`と`<役割名>.subagent.md`である。これらの改訂そのものを目的とする作業は主作業として扱い、変更目的ごとにcommitとWIを分ける。他の開発が必要にしたこれらの文書の改訂と整理は`agent-toolkit/rules/01-agent.md`の付帯作業に当たり、関連する開発と同じ計画、WIおよびcommitで完了する。開発完了後の振り返りが独立に発見した作業だけは別の作業とする。

## agents_serverの共有状態

共有状態ごとの保存先と読み書きの担当は`references/agents-server-shared-state.md`が保持する。保存先、更新できる主体または状態ディレクトリ配下のファイル種別を変える実装では、同書を同じ変更単位で更新する。

## agents_serverの委譲不具合の調査

`references/agents-server-investigation.md`は記録の所在、切り分けの順序、外部プロセスでの再現手順を保持する。記録の所在、診断項目または委譲先CLIへ与える引数を変える実装では、同書を同じ変更単位で更新する。

## 配布物としての記述方針

配布先をdotfilesユーザーに限定せず、設定値・ディレクトリ構成など異なり得る前提は条件付きで書く。

- 本文・docstring・コメントは配布物自身の挙動・仕様を扱い、個人フックとの連携などエンドユーザー環境側の設計を含めない
- agent-toolkit同梱スキルは`agent-toolkit:review-standards`のようにプラグイン名で修飾した完全名で書く（努力目標）。`.claude/skills/`のプロジェクトローカルスキルは素のスキル名、サブエージェント名は短縮しない完全名称で書く
- 配布物が参照するSSOTは配布物内に置き、dotfiles固有ファイルと非配布ファイルを参照先にしない。例外は実際に測った値を根拠とする条文が指す監査記録`docs/development/audit-records.md`（失効判定でだけ読むため配布物の外へ置く。記録先は`agent-toolkit:writing-standards`の`references/investigation.md`が定める）
- 編集時に`pytools/claude_hook/pretooluse.py`の固有名チェックが個人環境固有の識別子を検出したら、一般化した表現へ置き換える
- 配布物スキル本文ではhookの挙動をエンドユーザーが観測できる結果として示し、内部実装（ハッシュ比較、状態フラグの書き込みなど）は書かない（努力目標）。SSOTとして状態フラグやhook間連携を集約する`<plugin root>/skills/writing-standards/references/session-state-and-flags.md`等は例外とする

スキル・サブエージェントの編集では次を守る。

- 名前付きサブエージェントの定義と委譲プロンプトには`agent-toolkit:writing-standards`の`references/agent-skills.md`を適用する
- 委譲元の起動・受領契約とagent定義は組で更新する。単一呼び元はその工程の規範へ統合し、共有する起動契約は`<役割名>.parent.md`を保持できる。起動設定は`<役割名>.subagent.md`のfrontmatterが所有し、委譲元は起動準備のために定義本文を取得しない
- 独立に読み込まれる文書間の重複の採否は`agent-toolkit:writing-standards`の`references/agent-documents-basics.md`「定義の所有者」に従う
- 実行レビューの契約を変更する場合は、`agent-toolkit:process-wi`側と`agent-toolkit:plan-mode`でメインが委譲元となる側を同じ変更単位で更新する（両系統は共通化していない）

## スキル間の連携

`single-lane-process`側が共通契約を参照して上書きを定める一方向の依存とする。`agent-toolkit/skills/single-lane-process/`以外の`agent-toolkit/`配下のエージェント向け文書は`single-lane-process`を名指ししない（努力目標。共通側の改訂を不要にする）。起動名を伝える`docs/`配下の案内と方針記録は対象外とする。

## バージョン更新

本節は`agent-toolkit/`配下（agent-toolkitプラグイン配布物）だけを対象とし、詳細は`references/version-bump.md`にある。
同じ版数のままでは`claude plugin update`が「最新です」と返して配信されないため、エンドユーザーに届く変更はpush前に版数を更新する。

- 版数は`scripts/agent_toolkit_bump.py {patch|minor|major}`で更新する。区分は`references/version-bump.md`「判定基準」、実行する主体と時点は同「plan modeでの取り扱い」に従う（`agent-toolkit:process-wi`のレーンは区分を計画へ記録するだけで、終端担当が全レーンのマージ後に1回実行する）。rebase・merge時の競合は同「競合解決と統合後の確認」に従う
- `version`と`description`は2つの定義元で同一に保つ（`description`は手で同期する）。定義元は`agent-toolkit/.claude-plugin/plugin.json`と、`.claude-plugin/marketplace.json`の`plugins[]`内`name == "agent-toolkit"`のエントリである。`manifest_ssot_invariant_test.py`が検証する
- Agent Plugins向け`plugin.json`・`mcp.json`とCodex向けmanifestは、この2ファイルと`agent-toolkit/.mcp.json`から`scripts/sync_codex_plugin_manifests.py`が生成する。生成物を手で編集しない
- 検証は`dotfiles-development`「開発手順」の特定ファイルに限定する実行形で、SSOTテストを含めて行う

## 同期先ドキュメント

- 複数ファイルにまたがる機構または委譲構造を新設・変更する実装では、`docs/development/design.md`の索引から該当主題の本文を選び、目的、構造の理由、知識境界、却下した代替案を追加・更新する（努力目標）。新しい主題は主題別ファイルへ置いて索引の対応表へ行を足し、索引自体には節の見出しや機構の詳細を書かない
- `agent-toolkit/skills/workflow-overview/SKILL.md`と`docs/guide/claude-code-guide.md`「推奨ワークフロー」は、運用形態、WIの登録方法、回答の流れを同じ変更単位でそろえる
- `docs/guide/claude-code-guide.md`「設定確認」節のチェック内容要約は、要約が変わる変更（check追加・削除、検出範囲の大きな変更、依存ツールの変更、新規プラグイン追加など）で更新する
- `install-claude.sh`の`FILES`、`install-claude.ps1`の`$files`、`agent-toolkit/rules/`配下のmd一覧は完全一致を保つ。`install_script_ssot_invariant_test.py`が検証し、`uv run python scripts/sync_generated_files.py`が同期する
- 配布物スキルの外部インターフェース（判定区分・出力フォーマット・後始末コマンド分岐・サマリー表現など）を追加・削除・改名する場合は、呼び出し元を検索して連携先の記述を同じ計画で更新する
- `agent-toolkit/rules/01-agent.md`・`02-agent-operations.md`と`agent-toolkit/share/rules-common.codex.md`は`.chezmoi-source/dot_codex/AGENTS.md`の生成元である。生成は`uv run python scripts/sync_generated_files.py`で行い、計画への記載は`references/version-bump.md`「plan modeでの取り扱い」の派生物の規則に従う
- `agent-toolkit/share/rules-main*.md`と`rules-subagent*.md`の編集は生成差分も配布一覧の変更も生まないが、版数更新は要る。各起動区分へ届く規範は`agent-toolkit:writing-standards`の`references/delivery-scope.md`の配送範囲表が示す（軽量なagents_server委譲へ`rules-subagent.md`を配らない境界はテストが保持する）
- 計画ファイルの見出し、固定H3、表の行名は`agent-toolkit/agent_toolkit/_plan/structure/constants.py`の構造定数が定める。改訂時は構造定数を変え、同じ名称を持つ記述も同じ変更単位でそろえる。そろえる対象は`agent-toolkit/skills/plan-mode/references/plan-file-standards.md`と`agent-toolkit/share/`配下の`<役割名>.subagent.md`である。`docs/development/`の該当設計資料と`concepts.md`、`docs/guide/claude-code-guide.md`も含む。改訂前の名称は読み取り互換の構造定数として残し、新規作成と改訂の処理でだけ拒否する
- 構造定数を持たず`plan-file-standards.md`だけが必須とする見出しは同ファイルに従う。稼働中の計画を自動チェックで不合格にしないためにこの形を選んだ場合は、理由を計画へ記録する

## セッション状態フラグ

`agent-toolkit`プラグインの全フラグ一覧のSSOTは`agent-toolkit:writing-standards`の`references/session-state-and-flags.md`であり、フラグを追加・変更する際は同ファイルを更新する。
hookの実装・編集とセッション状態の設計・変更では`agent-toolkit:writing-standards`を起動する（hook実装の基準を含む。エージェントの行動規範は役割別ルールか作業別スキルに置く）。
