# メインが書くプロジェクト規範の指定の手順

## メインが書くプロジェクト規範の指定

`agent-toolkit:atk-mode`の作業`process-wi`の選定工程において、メインエージェントはAWIごとに`プロジェクト規範の指定`を記述する。
受け渡しの形式は`agent-toolkit/skills/atk-mode/references/process-wi-selection-format.md`に従う。
記載内容は、`pyproject.toml`の`[tool.agent-toolkit.pick-wi-check]`にある`norm-spec`の条件に基づいて組み立てる。
各条件では`paths`と`suffixes`、または`agent-doc`によって対象を判定し、`require-paths`は該当したファイルのパス、`require-text`は指定された文字列の記載を求める。
`atk run-script pick-wi-check`を実行すると、同じ設定を用いて指定の漏れを検出できる。
適用する規範は作業ツリー原本の絶対パス、適用する節、版を指定し、担当者自身が判断前に読み込む。
指定した資料の本文についても、同様に同じ原本を参照させる。

- `.claude/skills/edit-dotfiles-norms/SKILL.md`「変更した規範の自セッション適用」で挙げた対象規範を変更するAWIには、変更対象ファイルのリポジトリ相対パスを記述する。
  どの条件にも当てはまらないAWIについては`なし`と記載する。
- 反映先に本リポジトリのエージェント向け文書を含むAWI（`agent_toolkit._plan.structure`の`is_agent_doc_target_file`が真となるもの）には、変更を確定するエージェントが計画の採否を判断する前に読むべき資料を明記する。
  `docs/development/concepts.md`の全文を読む。
  さらに`concepts.md`の該当する決定は見出しから本文へ進み、変更対象の名前で`git log --grep`を検索して同じ害の再発を防ぐ条件をcommit本文から読む。
  読んだ成果は、`.claude/skills/edit-dotfiles-norms/SKILL.md`「既存の方針との照合」が定める守る条件と今回の採否・検証の対応として残す。
- `references/norm-followup.md`「対象の判定」と「工程と担当」に従い、規範の追従の対象をAWI本文から判定する。
  選定時に対象か未判断なら、同資料が指定する公開後の操作の順序を選定に残す。
- レーン担当者は選定結果から自レーンの指定を確認するため、委譲プロンプトで同じ要求を再掲することは省く（努力目標）。
  指定が`なし`以外の項目を担当するレーンには、その項目のファイル名と対象ファイルのパスを渡す。
- レーン内で規範の変更を確定したエージェントは、同じレーンの以降の委譲先に対して、`.claude/skills/edit-dotfiles-norms/SKILL.md`「変更した規範の自セッション適用」で述べた原本の絶対パス、適用する節、版を引き継ぐ。
  並行して動いている他のレーンへの伝播は範囲外とする。
