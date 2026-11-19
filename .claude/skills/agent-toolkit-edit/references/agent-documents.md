# 配布する文書の同期と責務の境界

`agent-toolkit/`と`agent-basics/`のエージェント向け文書（ルールファイル、`SKILL.md`、`references/`、`<役割名>.parent.md`、`<役割名>.subagent.md`）を改める時の決まりである。
扱うのは、作業の分け方、同期の対象と生成物とテスト、`agent-basics`と`agent-toolkit`の責務の境界、監査記録の置き場所である。
文書の作り方の一般の基準は`agent-basics:writing-standards/references/agent-documents-basics.md`にある。

## 付帯作業の扱い

これらの文書は、エンドユーザーへ配る成果物そのものである。
改訂そのものを目的とする作業は主作業として扱い、目的ごとにcommitとWIを分ける。
他の機能の開発に伴う改訂と整理は付帯作業として、その開発と同じ計画、WI、commitで終える。
開発を終えた後の振り返りで独立して見つかった作業だけを、別の作業として切り出す。

## スキルとサブエージェントの編集

- 実行レビューの契約を変える時は、`agent-toolkit:atk-mode`の作業`process-wi`の側と、`agent-toolkit:atk-mode/playbooks/write-plan.md`でメインが委譲元になる側の両方を同じ変更で直す（両系統は別々に実装されている）。
- process-wiのモード固有の資料は、共通の契約を参照して読み替えを定義する。
  共通の側の工程はモード固有の資料に依存させず、起動時に引数に合う資料を選ぶ。

## 同期先ドキュメント

- engineを足す時は、委譲先と対話のホストのそれぞれへの導入の要否を判断し、採った配布範囲と導入を見送った理由を構成・設計の記録に残す。
- 複数のファイルにまたがる機構や委譲の構造を新設・変更する時は、`docs/development/design.md`の索引から主題の本文を選び、目的、構造を選んだ理由、知識の境界、却下した代替案を追記・更新する（努力目標。後の変更で設計の判断を見直せるようにするため）。
  新しい主題は主題別のファイルを作って索引の表へ行を足し、詳細の見出しと機構の解説は主題別のファイルに置く。
- `agent-toolkit/skills/atk-mode/references/workflow-overview.md`は、エージェントの作業工程の契約を定める。
  ユーザーの運用形態、WIの登録方法、回答の操作が変わる時は、`docs/guide/claude-code-workflows.md`「推奨ワークフロー」も同じ変更でそろえる。
- `docs/guide/claude-code-models.md`「設定確認」は、ユーザーが実行する確認のコマンド、期待する結果、復旧の手順が変わる時に直す。
- `.chezmoi-source/dot_codex/AGENTS.md`は、個人用の規範`.chezmoi-source/dot_claude/rules/myprojects-common.md`を`scripts/sync_codex_agents.py`で埋め込んで生成する。
  agent-toolkitの規範は埋め込まない（Codexのメインへは入口`agent-toolkit:atk-mode`がSessionStartのhookの案内で届く）。
  `uv run python scripts/sync_generated_files.py`で同期し、計画では生成物として扱う。
- 入口`agent-toolkit/skills/atk-mode/SKILL.md`と`playbooks/subagent.md`の編集は生成物を変えないが、版数の更新は要る。
  各起動の区分へ届く文書は`agent-toolkit:atk-mode/references/agent-documents.md`「配送範囲表」で確かめる。
  `playbooks/subagent.md`は、`agents_server`が全modeの委譲先の起動の本文へ組み立てる原本であり、`Agent`ツールの委譲先はSubagentStartの通知の案内で読む。
- 読込表は各文書の本文の表に置く。
  読込表の別ファイル化の再発は`agent_basics_standalone_invariant_test.py`が検出する。

## agent-basicsとの責務境界

成果物の品質基準は`agent-basics/skills/`へ置き、toolkitの側からは品質のスキルと自身の運用の契約を参照する。
常駐のhook、MCP、ホームディレクトリ向けの運用の規範は、toolkitの側に置く。
basicsの元のマニフェストは`agent-basics/.claude-plugin/plugin.json`で、掲載の原本は同じmarketplaceのagent-basicsのエントリである。
独立した版数と`description`の2か所をそろえ（版数の更新では`--plugin agent-basics`を指定する）、ルートのマニフェストとCodexのマニフェストは既存の生成スクリプトで再生成する。
toolkitのClaudeの依存の配列を、Agent Pluginsのルートのマニフェストへ複製しない。

`agent-basics/`の文書を編集した時は、agent-basicsだけを導入したエージェントを読者として、単体で使えることを確かめる。

1. 本文の用語、手順、参照先が、agent-basicsの中で定義されているか一般の語かを1つずつ確かめる。
2. agent-toolkitの工程名、WI系の語、`atk`の操作、役割文書や`atk-mode`の手順書の名前などを前提なしに使っていれば、一般の語へ置き換えるか、プロジェクトがその運用を定める場合に従う条件付きの記述にする。
3. agent-toolkitの判断に今も使う内容は、agent-toolkitの側の同名のスキルの参照資料へ移す。

参照先の実在とagent-toolkitの名前空間・コマンドへの依存は`agent_basics_standalone_invariant_test.py`が検出するが、新しく持ち込んだ未定義の用語はこの確認で捉える。

`agent-basics/skills/bugfix/`の`SKILL.md`か確認観点を変える時は、各確認観点の末尾の「この観点が防ぐ失敗例」を変更後の本文へ当てる。
`SKILL.md`と、その条件が指す確認観点だけを渡した独立した文脈へ失敗例の場面を示し、同じ誤りへ進まないことを確かめる。
文章を足す変更では、`agent-basics:bugfix/references/prevention.md`「案を比べる順」に従い、同じ変更で消すか統合する文章を示す。

## エージェント向け文書の技術的条文

観測に基づく技術的条文の新設と改訂は、`agent-basics:writing-standards/references/investigation.md`「エージェント向け文書の技術的条文」に従い、このリポジトリでは次のとおり記録する。

1. 観測の記録は`docs/development/audit-records.md`の索引から選ぶ記録ファイルへ置き、見出しを`<ファイルパス>：<節名>：<日付>`とする。
   記録には観測日、観測時の版、再検証の手段の3項目だけを書く。
2. 条文の本文には、観測の環境とモデルの版を書かない（対象の版は除く）。
3. 記録の所在の示し方は、文書の配布の範囲で分ける。
   - `agent-basics`の配布物（`agent-basics/skills/`配下）は条文に記録の所在の文を置かない。
     `agent-basics`だけを導入したエンドユーザーにも届き、`docs/development/`を指しても参照先へ届かないためである。
     プロジェクト規範（`AGENTS.md`など）と同じく、記録の見出しのファイルパスと節名が索引になる。
     試験は、見出しのファイルと節名が実在することを確かめる。
   - `agent-toolkit`の配布物と、配布しない文書（`.claude/skills/`、コード）は、条文の末尾に記録の所在を示す文を1文置く。
4. 条文を改める・削る前は、`git grep -n '<ファイルパス>' -- 'docs/development/audit-records*.md'`で根拠の記録を探す。
5. 条文の節名を変えた時と、条文を移した時は、記録の見出しのファイルパスと節名も同じ変更で直す。
