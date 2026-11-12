# プラグイン文書の編集と参照追随

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

## 付帯作業の扱い

`agent-toolkit/skills/`、`agent-toolkit/share/`（規範原本を含む）配下のエージェント向け文書は、エンドユーザーへ配布する成果物そのものである。
対象にはルールファイル、`SKILL.md`、`references/`配下の文書、`<役割名>.parent.md`、`<役割名>.subagent.md`が含まれる。
これらの改訂自体を目的とするタスクは主作業として扱い、変更の目的ごとにコミットとWIを分ける。
他の機能開発に伴って必要となった文書の改訂や整理は付帯作業に該当するため、関連する開発と同一の計画、WI、コミットで完了させる。
開発完了後の振り返りによって独立して見つかった作業のみ、別作業として切り出す。

## スキルとサブエージェントの編集

スキルやサブエージェントを編集する際は、次の事項を守る。

- 名前付きサブエージェントの定義や委譲プロンプトには、`agent-basics:writing-standards`の`references/agent-skills.md`を適用する。
- 委譲元での起動・受領契約とサブエージェント定義はセットで更新する。
  単一の呼び出し元からのみ使われる契約はその工程の規範へ統合し、複数の呼び出し元で共有する起動契約は`<役割名>.parent.md`として分離できる。
  起動設定は`<役割名>.subagent.md`のfrontmatterで管理し、委譲元が起動準備のために定義本文を取得しなくても済む構造を保つ。
- 独立して読み込まれる文書間で内容が重複する場合の採否は、`agent-basics:writing-standards/references/agent-documents-basics-ownership.md`「定義の所有者」に従う。
- 実行レビューの契約を変更する場合は、`agent-toolkit:process-wi`側と、`agent-toolkit:write-plan`でメインが委譲元となる側の双方を同一の変更単位で更新する（両系統は共通化されていない）。

## スキル間の連携

process-wiのモード固有資料は、共通契約を参照して読み替えを定義する。
共通側の工程はモード固有の資料へ依存させず、起動時に引数に合う資料を選択する。

## 同期先ドキュメント

- engineを追加する際は、委譲先と対話ホストのそれぞれへの導入要否を判断し、採用した配布範囲と導入を見送った理由を構成・設計記録に残す。
- 複数ファイルにまたがる機構や委譲構造を新設・変更する場合は、`docs/development/design.md`の索引から該当する主題の本文を選び、目的、構造を採用した理由、知識境界、却下した代替案を追記または更新する（努力目標）。
  新たな主題は主題別ファイルを作成して索引テーブルに行を追加し、索引ファイル自体には詳細な見出しや機構の解説を書かない。
- `agent-toolkit/skills/workflow-overview/SKILL.md`はエージェントの作業工程に関する契約を定義している。
  ユーザーの運用形態、WIの登録方法、または回答操作が変わる場合は、`docs/guide/claude-code-workflows.md`「推奨ワークフロー」も同一の変更単位で揃える。
- `docs/guide/claude-code-models.md`「設定確認」は、ユーザーが実行する確認コマンド、期待される結果、または復旧手順が変わる場合に更新する。
- `install-claude.sh`の`FILES`、`install-claude.ps1`の`$files`、および`agent-toolkit/share/rules/common/`配下のMarkdownファイル一覧は完全一致を保つ。
  この整合性は`install_script_ssot_invariant_test.py`で検証し、`uv run python scripts/sync_generated_files.py`で同期する。
- 配布物スキルの外部インターフェース（判定区分、出力フォーマット、後始末コマンドの分岐、サマリー表現など）を追加・削除・変更する場合は、呼び出し元を検索し、連携先の記述も同一の計画内で更新する。
- `.chezmoi-source/dot_codex/AGENTS.md`の生成元は、`agent-toolkit/share/rules/`の`common/01-agent.md`・`common/02-agent-operations.md`・`rules-common.codex.md`である。
  同期は`uv run python scripts/sync_generated_files.py`で行い、計画への記載は`version-bump-planning.md`「plan modeでの取り扱い」の派生物の規則に従う。
- `agent-toolkit/share/rules/rules-main*.md`や`rules-subagent*.md`の編集はファイルの自動生成や配布一覧の変更を伴わないが、プラグインのバージョン更新は必要である。
  各起動区分へどの規範が届くかは、`agent-toolkit:delegation/references/delivery-scope.md`にある配送範囲表を参照する。
  軽量なagents_server委譲先には`rules-subagent.md`の共通返却契約短節を自動配送し、全文を省く境界と各modeの権限を配送テストで保持する。
- 読込表の宣言は各文書の本文の表に置く。
  宣言と参照先への読込行は`agent-toolkit/read_triggers_invariant_test.py`で確かめる。
  読込表の別ファイル化の再発は`agent_basics_standalone_invariant_test.py`で確かめる。
  書き方は`agent-basics:writing-standards/references/agent-documents-basics-read-tables.md`「読込表」に従う。
  agent-toolkitの文書では、`agent-toolkit:/share/agent-documents-operations.md`「読込表の宣言」にも従う。
- 計画ファイルの見出し、固定H3、表の行名を改訂する場合は`plan-structure-constants.md`に従う。

## agent-basicsとの責務境界

成果物の品質基準は`agent-basics/skills/`へ配置し、toolkit側からは品質スキルと自身の運用契約を参照する。
basics側には常駐フック、MCP、ホームディレクトリ向けの運用規範を含めない。
basicsのオリジナルマニフェストは`agent-basics/.claude-plugin/plugin.json`であり、掲載原本は同一marketplace内のagent-basicsエントリである。
独立したバージョン番号と説明文（description）の2箇所を揃え（版数は`version-bump-execution.md`「バージョン更新」の手順で`--plugin agent-basics`を指定して更新する）、ルートマニフェストおよびCodexマニフェストは既存の生成スクリプトで再生成する。
toolkitのClaude依存関係の配列を、Agent Pluginsのルートマニフェストへ複製することは避ける。

`agent-basics/`の文書を編集したときは、agent-basicsだけを導入したエージェントを読者として単体利用を確かめる。
本文中の用語、手順、参照先が、agent-basicsの中で定義されているか一般的な語であるかを1つずつ確かめる。
agent-toolkitの工程名、WI系の語、`atk`の操作、役割文書や`share/`の名前などを前提なしに使っていれば、一般的な語へ置き換えるか、プロジェクトがその運用を定める場合に従う条件付きの記述にする。
agent-toolkitの判断に今も使う内容は、agent-toolkit側の同名スキルの参照資料へ移す。
参照先の実在とagent-toolkitの名前空間・コマンドへの依存は`agent_basics_standalone_invariant_test.py`で確かめられる。
新しく持ち込んだ未定義の用語は、この確認で捉える。

agent-toolkitのリソースを編集する前は`agent-basics:writing-standards`も適用し、同スキルの読込表から配送、状態、起動、記録に関する運用条件を確認する。
すでに取得済みの品質に関する記述は再利用する。

## エージェント向け文書の技術的条文

観測に基づく技術的条文の新設・改訂は`agent-basics:writing-standards/references/investigation-technical-clauses.md`に従う。
本リポジトリでは、観測の記録を`docs/development/audit-records.md`の索引から選ぶ記録ファイルへ置き、見出しを`<ファイルパス>：<節名>：<日付>`とする。
記録には観測日、観測時のバージョン、再検証手段の3項目を書き、条文の末尾に記録の所在を示す文を1文置く。
常時規範（`AGENTS.md`など）には記録の見出しだけを索引として書く。
条文を別の場所へ移動したときは、見出しのファイルパスと節名も移動先へ直す。

## 本文参照の取得条件

| 時点または条件 | 全文読む資料 |
| --- | --- |
| バージョン更新の詳細手順：競合解決と統合後の確認の基準を適用する前 | `version-bump-execution.md` |
| バージョン更新の詳細手順：plan modeでの取り扱いの基準を適用する前 | `version-bump-planning.md` |
