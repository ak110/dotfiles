---
name: agent-toolkit-edit
user-invocable: false
description: >
  `agent-toolkit/`配下のプラグイン（スキル・サブエージェント・フックスクリプト・marketplace記述）、
  `agent-toolkit/rules/`配下のルールファイル（配布先`~/.claude/rules/agent-toolkit/`）、
  `.claude-plugin/marketplace.json`を編集するときに使う。エージェント向け文書の記述を削除または縮小するときにも使う。
  版数更新・marketplace管理・セッション状態フラグの扱いを含む。
---

# agent-toolkit（Agent Plugins・Claude Code・Codex）

本スキルは`agent-toolkit/`配下の配布物と`.claude-plugin/marketplace.json`を編集する主体へ、ファイル構成と参照方向、規範を削除・縮小するときの消失確認、版数更新、配布と同期の手順を提供する。

## 読込表

| 時点または条件 | 全文読む資料 |
| --- | --- |
| エージェント向け文書を編集する前 | `docs/development/concepts.md`と`docs/development/incidents.md`。編集する主体が自身で全文を読む。要約、見出し一覧、部分読取および別主体の読取結果は全文読了に当たらない |
| WI処理の工程や運用を担うスキル、`share/`配下の`<役割名>.subagent.md`または`atk wi`の実装を編集する前 | `docs/guide/claude-code-guide.md`のうち編集対象と同期する節（少なくとも「推奨ワークフロー」） |
| `agent-toolkit/agent_toolkit/agents_server_mcp.py`、`agent-toolkit/agent_toolkit/_agents_server/`配下または`rust/claude-statusline/src/agents_server.rs`の実装を変更または調査する前 | `references/agents-server-shared-state.md` |
| `agents_server`が起動した委譲先が動かない事象（起動の失敗、初期化の未到達、委譲先の無応答、委譲先が返す結果の欠落）を調査する前 | `references/agents-server-investigation.md` |
| `agent-toolkit/agent_toolkit/_atk/serve/static/`配下のCSS・HTML・JavaScriptを変更する前 | `references/atk-serve-static.md` |
| 次のいずれかを変更する前と、作業ツリーで改訂した配布物の挙動を確かめる前: `agent-toolkit/agent_toolkit/`・`agent-toolkit/scripts/`の配置とimport、`atk`のサブコマンドと出力、MCPサーバー識別子とツール名、hookの実装と登録、プラグイン内の実行時パス、権限設定、marketplaceと配布の手順 | `references/distribution-and-hooks.md` |
| `agent-toolkit/`配下を変更対象に含む計画を起草する前 | `references/version-bump.md`の「plan modeでの取り扱い」節 |
| 全レーン後に版数を更新する時点と、`references/version-bump.md`の手順へ入る前 | `agent-toolkit/skills/process-wi/references/finish-session.md` |

WI処理の工程や運用を担うスキル、`share/`配下の`<役割名>.subagent.md`または`atk wi`の実装を編集する場合は、編集前に`agent-toolkit:workflow-overview`を起動する。

## ファイル構成と参照方向

- `agent-toolkit/`配下: Agent Plugins・Claude Code・Codexが共有するプラグインルート
- `agent-toolkit/rules/`配下: ルールファイル（`01-agent.md`は基本原則、`02-agent-operations.md`は製品横断の実行運用を担う）
- `~/.claude/rules/agent-toolkit/`: ルールファイルの配布先（直接編集不可）。編集は配布元の`agent-toolkit/rules/`へ行う
- `agent-toolkit/rules/`配下はサブディレクトリを設けずフラット構造を保ち、メインエージェント、サブエージェントおよび委譲先の全てへ適用する条文だけを置く
  （`scripts/gen-install-files.py`がrules直下の`*.md`だけを配布一覧へ列挙するため）
  - サブディレクトリへ置いたルールファイルは配布一覧に入らず、配布先へ届かない
- `agent-toolkit/share/rules-main.md`・`rules-main.claude-code.md`・`rules-main.codex.md`: メイン向けの共通規範とホスト別規範
- `agent-toolkit/share/rules-subagent.md`・`rules-subagent.claude-code.md`: 委譲先向けの共通規範とClaude Code固有規範。Codex委譲先の固有差分が必要になった場合は`rules-subagent.codex.md`を追加する
  振り分けの判定は`agent-toolkit:writing-standards`の`references/agent-documents-additions.md`「規範追記時の判定」に従う
- 配布物完結の環境変数は`AGENT_TOOLKIT_<PURPOSE>`形式とする
  （代表例は`AGENT_TOOLKIT_PRIVATE_NOTES`。`atk wi`管理repoのroot。未設定時は`~/private-notes/`）。
  個人環境完結は`DOTFILES_`を使う。個別の環境変数の一覧と用途は
  `<plugin root>/skills/writing-standards/references/claude-hooks.md`が扱う

参照方向はdotfilesリポジトリ→プラグイン、およびプラグイン↔ルールファイルを許容する。
配置先は「いつコンテキストへ読み込ませたいか」で判断する。

- 常時ロードする指針と特定タスクでのみ必要な指針の振り分けは`agent-toolkit:writing-standards`の`references/agent-documents-basics.md`「責務と構成」に従う
- 配置先は規範の成立条件が依存する対象で判定し、表層識別子はその判定の入力から外す
- プロジェクト固有のツール、データ、命名、CI、運用手順へ依存する内容はプロジェクト側へ置く
- 固有要素を同種の任意要素へ置換しても判定基準、工程順序、停止条件が成立する内容だけを配布物候補とする

### 付帯作業の扱い

`agent-toolkit/rules/`、`agent-toolkit/skills/`、`agent-toolkit/share/`配下のエージェント向け文書は、エンドユーザーへ配布する成果物そのものである。対象はルール、`SKILL.md`、`references/`、`<役割名>.parent.md`と`<役割名>.subagent.md`である。これらの改訂そのものを目的とする作業は主作業として扱い、変更目的ごとにcommitとWIを分ける。他の開発が必要にしたこれらの文書の改訂と整理は`agent-toolkit/rules/01-agent.md`の付帯作業に当たり、関連する開発と同じ計画、WIおよびcommitで完了する。開発完了後の振り返りが独立に発見した作業だけは別の作業として扱う。

### agents_serverの共有状態

読込表の同じ行が挙げる実装の共有状態ごとに正とする保存先と、読む主体・更新できる主体の対応は`references/agents-server-shared-state.md`が保持する。
状態を正とする保存先、更新できる主体または状態ディレクトリ配下のファイル種別を変える実装では、同書を同じ変更単位で更新する。

### agents_serverの委譲不具合の調査

`references/agents-server-investigation.md`は観測できる記録の所在、切り分けの順序、外部プロセスでの再現手順を保持する。
記録の所在、`agents_server`の診断項目または委譲先CLIへ与える引数を変える実装では、同書を同じ変更単位で更新する。

## 規範を削除・縮小するときの消失確認

`agent-toolkit/rules/`、`agent-toolkit/skills/`、`agent-toolkit/share/`、`AGENTS.md`、`.claude/skills/`などのエージェント向け文書の記述を削除または縮小する編集では、目的にかかわらずベースcommitとの差分を確認する。削除した価値、適用範囲、条件、例外を特定し、削除の理由をcommit本文へ残す。統合を理由とする場合は、統合先の適用範囲が元の範囲を含むことを確認する。含まない場合は統合先を整えるか、削除を取りやめる。

削除・縮小する行の由来は次の順に調べる。

1. 文面の微修正をまたいで一致する部分文字列を選び、`git log --follow -S '<本文の部分文字列>' -- <ファイル>`かパスを限定しない`git log -S`で初出を調べる。`git blame`は最後に行へ触れたcommitを示し、パスを限定した`git log -S`は改名前の履歴を含まないため、どちらも単独で初出の判定に使わない
2. 検索結果の最古の導入commitと、その行を復元したcommitのいずれかに`Co-Authored-By`か`Claude-Session` trailerが無い場合は、同じ対象リポジトリの終端済みキュー項目を`atk wi grep --state all`で探す。検索には特徴的な語や反映先パスを使う
3. 候補の`adopt`記録が導入commitのOIDを持つか、そのOIDを進捗ログに持つ計画の`関連WI`が候補を挙げる場合だけ、候補との対応を裏付ける
4. 対応する要求単位が`agent-toolkit:wi-standards`「由来と承認」によりエージェント由来と確定したときは、項目名、OID対応および由来の根拠を計画の進捗ログ（計画なしでは引き継ぎ記録、どちらも無い作業ではユーザーへの報告）へ記録して保護の対象から外す。commit本文には第1段落のとおり削除・縮小の理由を書き、キュー項目のファイル名などの内部識別子は書かない
5. 対応を裏付けられない場合、人間由来を含む場合、または由来を分離できない場合は、作者を確定できない規範としてユーザーが書いた規範と同じく保護し、編集前にユーザー確認（事前承認）する。過去のCodex commitやエージェントの付け忘れにはtrailerの無いものが多いため、由来を裏付けられない場合は確認が余分に増えても保護を優先する

trailerの有無だけでは作者を確定できないため、報告、AWI本文および判断の根拠では、そのcommitをユーザーのcommitと結論づけない。

事前承認で守る対象は、削除・縮小によって行の価値、適用範囲、条件または例外のいずれかが失われる編集である。作者を確定できない行でも、次のいずれかを確かめた削除・縮小は事前承認を求めず、確かめた内容を第1段落の削除の理由と同じcommit本文へ書く。対応するキュー項目が人間由来の要求単位を含むなど人間由来と確定した行と、次のいずれも確かめられない行は、前掲の手順5のとおり編集前に事前承認する。

- 移設・統合: 変更後の文書の移設先または統合先の箇所（パスと節）が、削除する行の価値、適用範囲、条件および例外を全て含む。第1段落の統合先の包含の確認でこれを判定し、commit本文へ移設先の箇所と要素ごとの対応を書く
- 失効: 削除する行が説明または規定する実装、機能、工程またはファイルが現行のリポジトリに無いことを、その識別子の固定文字列検索で確かめた。その対象を撤去したcommitが既にあるか、撤去が処理中の要求の認可の範囲で同じ変更に含まれる。commit本文へ失効した対象、検索の内容と撤去したcommitを書く

## 配布物としての記述方針

配布先のエンドユーザーは本リポジトリのdotfilesユーザーとは限らないため、手元プロジェクト固有の前提は条件付きで書く。

- 自己言及的な表現・特定設定値の前提・特定ディレクトリ構成の前提を決め打ちせず、
  異なり得る条件は条件付き表現（「`～`設定が有効な場合、」など）で書く
- 仕様参照としてのルール名・設定キー名・選択肢の説明は記述してよい
- 配布物のdocstring・コメント・本文には配布物自身の挙動・仕様のみを記述する。
  エンドユーザー環境側の連携設計（個人フックとの優先順序など）は記述の対象から外す
- 本リポジトリの文書でagent-toolkit同梱スキルを指す表記は、`agent-toolkit:review-standards`のようにプラグイン名で修飾した完全名で書く（努力目標。素のスキル名は同名スキルの探索を招く）。
  `.claude/skills/`配下のプロジェクトローカルスキルはプラグイン修飾を付けず素のスキル名で書き、
  サブエージェント名は起動指示・地の文とも短縮せず完全名称で書く
- 配布物内の記述が参照するSSOTは配布物内に配置する。参照先はdotfiles固有ファイルと非配布対象ファイルの外から選ぶ
  - 例外: 実際に測った値を根拠とする条文が指す監査記録（`docs/development/audit-records.md`）は本規定の対象外とする。この記録は条文の失効判定でだけ読むため、判断のたびに読む条文から分離して配布物の外へ置く。記録先は`agent-toolkit:writing-standards`の`references/agent-documents-additions.md`「規範追記時の判定」が定める
- 配布物文面は実ファイル編集時に`pytools/claude_hook/pretooluse.py`の固有名チェックを適用し、
  検出した個人環境固有の識別子を一般化表現へ置き換える
- 配布物スキル本文では、hookの挙動をエンドユーザーが観測できる結果（特定操作がブロックされる・警告が返る等）として提示する。
  ハッシュ値の比較・SHA256記録・ブロック機構・状態フラグ書き込みなどの内部実装の説明は、提示の対象から外す（努力目標。エンドユーザーが観測する結果に限定すると、実装変更に本文が引きずられない）。
  - 例外: SSOT目的で状態フラグ一覧・hook間連携仕様を集約する資料
    （`<plugin root>/skills/writing-standards/references/session-state-and-flags.md`等）は本規定の対象外とする

スキル・サブエージェント編集時は次を守る。

- 名前付きサブエージェントの定義と委譲プロンプトを編集する場合は、`agent-toolkit:writing-standards`の
  `agent-toolkit/skills/writing-standards/references/agent-skills.md`を適用する
- 委譲元の専用の参照文書を起動契約、agent定義を委譲先の恒常手順としてペアで更新する
- 委譲元のスキルと参照文書からagent定義をReadする手順を除外する
- 独立に読み込まれる文書間の重複は、参照だけでは実行判断に必要な情報が欠ける場合に許容する
  （努力目標。重複は保守の手間を増やす）
- 相互参照が発生する共通観点は横断スキル配下`references/`へ集約してよい
- 並行する手順を別スキルに新設する際は、既存スキルの表記との整合を確認する
- 「実行時エラーで判明する仕様」「具体例」は再発リスクと影響度を踏まえて保持判断する
- 実行レビューの契約を変更する場合は、`agent-toolkit:process-wi`側と、`agent-toolkit:plan-mode`でメインが委譲元となる側の双方を同じ変更単位で更新する。両系統は共通化しておらず、片方だけの改訂は運用差を生む

## スキル間の連携

`agent-toolkit/skills/single-lane-process/`配下以外の`agent-toolkit/`配下のエージェント向け文書は、`single-lane-process`を名指ししない（努力目標。共通側が読み替え先を知らない一方向依存に保つと、改訂が片側で済む）。共通契約と`agent-toolkit:process-wi`側は読み替え先を知らない一方向の依存とし、`single-lane-process`側から共通契約を参照して上書きを定める。エンドユーザーが起動名を知る必要がある`docs/`配下の案内と方針記録は対象外とする。

## バージョン更新

本節のバージョン更新規定は`agent-toolkit/`配下（agent-toolkitプラグイン配布物）のみを対象とする。
詳細手順は`references/version-bump.md`に集約する。
`agent-toolkit/`配下を変更対象に含む計画では、読込表の行が挙げる「plan modeでの取り扱い」節に従い、
`## 要件・外部仕様`へ記載すべきファイル群を確定する。
rebase・merge時の版数競合は`references/version-bump.md`「競合解決と統合後の確認」節に従って解決する。
`version`／`description`は以下の箇所で完全に同一文字列に保つ。

- `agent-toolkit/.claude-plugin/plugin.json`
- `.claude-plugin/marketplace.json`の`plugins[]`内`name == "agent-toolkit"`のエントリ
整合性は`manifest_ssot_invariant_test.py`が確かめ、不整合があれば`uv run --frozen pyfltr run`が自動的に失敗する。
Agent Plugins向け`plugin.json`・`mcp.json`とCodex向けmanifestは、この2ファイルと
`agent-toolkit/.mcp.json`をもとに`scripts/sync_codex_plugin_manifests.py`が生成する。
Agent Plugins・Codex向け生成物を手動編集してはならない。変更は生成元の更新と生成器の実行で行う。

## 同期先ドキュメント

複数ファイルへまたがる機構または委譲構造を新設または変更する実装では、`docs/development/design.md`の索引で該当主題の本文と節を選び、目的、構造の理由、知識境界および却下した代替案を追加・更新する（努力目標。構造の理由と却下した案を残すと後の変更で同じ検討を繰り返さずに済む）。
既存の主題はその本文の節へ追記する。独立した新しい主題は主題別ファイルへ置き、索引の対応表へ行を追加する。索引には節の見出しや機構の詳細を追記せず、分類の具体は索引の対応表を参照する。

`agent-toolkit/skills/workflow-overview/SKILL.md`と`docs/guide/claude-code-guide.md`「推奨ワークフロー」の一方で、運用形態、WIの登録方法、回答の流れのいずれかを変更した場合は、他方も同じ変更単位でそろえる。

- エージェント向け文書を編集する主体は、読込表の行が挙げる方針と障害対策の記録との整合を、編集前に確認する。
  全文取得の手段は`agent-toolkit/rules/02-agent-operations.md`「ツール・コマンド運用」に従う。
  編集中に新たな障害または確定した意向が生じた場合は、対応する文書を更新する
- `docs/guide/claude-code-guide.md`「設定確認」節のチェック内容要約は、要約が変わる変更時に更新する。
  対象は新しいcheck追加・既存check削除・検出範囲の大きな変更・依存ツールの変更・新規プラグイン追加を含む
- `install-claude.sh`の`FILES`・`install-claude.ps1`の`$files`・
  `agent-toolkit/rules/`配下のmdファイル一覧は完全一致を保つ
  （整合性は`install_script_ssot_invariant_test.py`が検証し、`scripts/gen-install-files.py`を含む`uv run python scripts/sync_generated_files.py`が一覧を自動同期する）
- 配布物スキル本体の外部インターフェース（判定区分・出力フォーマット・後始末コマンド分岐・サマリー表現など）へ
  新規追加・削除・改名を加える場合は連携整合を保つ。
  既知の呼び出し元スキル群を`grep -rn`で洗い出し、連携先の対応記述を同一計画内で同時更新する
- `agent-toolkit/rules/01-agent.md`と`02-agent-operations.md`の編集は`.chezmoi-source/dot_codex/AGENTS.md`の再生成差分を生じさせる。
  計画への記載は`references/version-bump.md`「plan modeでの取り扱い」の派生物の記載規則に従い、生成コマンドは`uv run python scripts/sync_generated_files.py`とする
- `agent-toolkit/share/rules-main.codex.md`は`scripts/sync_codex_agents.py`の生成元であり、その編集は`.chezmoi-source/dot_codex/AGENTS.md`の再生成差分を生じさせる。生成コマンドは`uv run python scripts/sync_generated_files.py`とする
- `agent-toolkit/share/rules-main.md`と`rules-main.claude-code.md`、`rules-subagent.md`とホスト別の`rules-subagent.*.md`の編集は生成差分もClaude配布一覧の変更も生じさせない。`rules-subagent.md`はClaude CodeとCodexのSubagentStart hookおよびagents_serverの通常委譲へ配る。Claude Code固有規範はClaude Codeだけへ配る。Codex hookの起動コマンドは`scripts/sync_codex_plugin_manifests.py`が生成するmanifestで同期する。軽量なagents_serverでの委譲へ常時規範を配らない境界もテストコードで保持する。
  バージョン更新の規定は適用する
- 計画ファイルの見出し、固定H3および表の行名は、`agent-toolkit/agent_toolkit/_plan/structure/constants.py`が定める。
  対象は同ファイルが構造定数として名称を持つものとする。
  改訂時は同ファイルの構造定数を変更する。
  `agent-toolkit/skills/plan-mode/references/plan-file-standards.md`、`agent-toolkit/share/`配下の`<役割名>.subagent.md`、
  `docs/development/design.md`の索引から選ぶ該当主題の資料、`docs/development/concepts.md`および`docs/guide/claude-code-guide.md`のうち、同じ名称を持つ記述も同じ変更単位でそろえる。
  改訂前の名称は読み取り互換用の構造定数として残し、新規作成や改訂の処理でだけ拒否する
- 構造定数を持たず`agent-toolkit/skills/plan-mode/references/plan-file-standards.md`だけが必須とする見出しは、同ファイルの記述に従う。
  稼働中の計画を自動チェックで不合格にする変更を避ける必要がある場合に選び、選んだ理由を計画へ記録する

## セッション状態フラグ

`agent-toolkit`プラグインが定義する全フラグ一覧のSSOTは`agent-toolkit:writing-standards`の
`references/session-state-and-flags.md`に置く。フラグを追加・変更する際は同ファイルを更新する。
`agent-toolkit:writing-standards`は文章・コードの作成基準を持ち、hook実装の基準もここに含む。エージェントの行動自体の規範は、実行主体別ルールまたは作業別スキルに置く。

hookの実装・編集とセッション状態の設計・変更では`agent-toolkit:writing-standards`を起動する。

## 編集手順

push前にbumpが必須（同じバージョンでは`claude plugin update`が「最新です」と返しエンドユーザーへ配信されないため）。

1. 「バージョン更新」の判定基準に該当する場合は`scripts/agent_toolkit_bump.py {patch|minor|major}`で版数を更新する。
   実行する主体と時点は`references/version-bump.md`「plan modeでの取り扱い」に従う。
   `agent-toolkit:process-wi`のレーンは実行せず版数区分を計画へ記録し、終端担当が全レーンのマージ後に1回実行する
2. `description`を変更する場合はSSOTの2ファイルを手で同期する
3. Agent Plugins・Codex向け派生JSONを「バージョン更新」節の生成器で同期する
4. `docs/guide/claude-code-guide.md`のチェック内容リストは「同期先ドキュメント」節に従って更新する
5. `dotfiles-development`「開発手順」の特定ファイルに限定する実行形で、SSOTテストを含むテストが成功することを確認する
6. 変更をコミットする

## コミットメッセージ方針と.gitmessage

`<plugin root>/skills/commit/SKILL.md`のコミットメッセージ方針と`.gitmessage`は配布範囲が異なるため意図的に重複させる。SSOT化しない。
