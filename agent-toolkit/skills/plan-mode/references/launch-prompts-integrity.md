---
# 同期注記: plan-reviewer雛形内「メイン側実施済み観点の内訳」欄の趣旨は
# integrity-checks.md「工程7の実施手順」節・plan-reviewer.md「入力」節と
# 意図的に重複する（構成は各ファイルの用途に合わせる）
# 改訂時は3ファイルを同時更新する
# named subagent能動送付規定は `agent-toolkit/rules/03-claude-code.md`「サブエージェントの活用」節・
# `agent-toolkit/references/plan-impl/launch-prompts-drafting.md`・
# `agent-toolkit/skills/process-feedbacks/references/explore-template.md`「Explore委譲雛形」節配下「制約」ブロック
# と意図的に重複する
# 改訂時は4ファイルを同時更新する
# 本ファイル本文の2雛形は `background` 起動・`name` 指定を伴わない前提運用のため、
# named subagent能動送付規定は本ファイル本文へは追加せず、frontmatter同期注記のみを配置する
---

# 工程7サブエージェント起動プロンプト雛形

`agent-toolkit:plan-mode`スキル工程7で起動する各サブエージェント
（うち`agent-doc-validator`は条件付き起動）の起動プロンプトを、
コピー可能な完全コードブロックで集約する。
`integrity-checks.md`「工程7の実施手順」節から機械転記して使用する。
共通の遵守事項として、いずれの雛形も次を含める。

- 呼び出し元の暗黙前提（メイン側の会話履歴・拡張思考・他サブエージェント出力）を排除する
- 独立コンテキストで完結する形で情報を漏れなく含める
- 入力欠落時は該当サブエージェント定義の規定に従い出力冒頭で欠落事実を明示報告する
- 参照すべき規範スキル: 各サブエージェント定義のfrontmatter`skills:`欄に列挙されたスキルを
  起動プロンプトへ漏れなく明記する（`plan-reviewer`は`agent-toolkit:writing-standards`・
  `agent-toolkit:agent-standards`・`agent-toolkit:review-standards`、
  `agent-doc-validator`は`agent-toolkit:agent-standards`・`agent-toolkit:writing-standards`・
  `agent-toolkit:review-standards`）
- 制約: 実行可能ツール範囲は各サブエージェント定義のfrontmatter`tools:`欄に従う。
  破壊的操作（git commit・git push・rm・ファイル書き換え等）は禁止。
  git操作全般（stash・checkout・reset等の作業ツリー変更を含む）は禁止
- 起動プロンプトへは`agent-toolkit/agents/*.md`の規定参照
  （「`## 出力`節に従う」等）を書かない。
  当該定義ファイルはサブエージェント起動時にシステムプロンプトとして自動読込されるため再指示は冗長となる。
  呼び出し元の後続処理が完了報告書式に依存する場合は、
  書式期待を呼び出し元文書（本ファイル・`agent-toolkit/agents/plan-impl-executor.md`等）に自己完結で記述する

## plan-reviewer雛形

`plan-reviewer`サブエージェントの起動プロンプトは次のとおり。

    以下の計画ファイルについて「計画文内・他ファイルとの整合」「変更履歴と変更内容の対応照合」
    「機械チェック適合性」「編集対象スキル固有規定の事前適用」「サブエージェント連携の設計整合性」
    「対象ファイル現状との突合による単体品質・日本語表現の重大不備」の独立コンテキスト点検を実施してください。
    対象ファイルは部分読解を避け必ず全体を`Read`で取得してから評価してください。

    - 計画ファイル: {計画ファイル絶対パス}
    - 対象ファイル現状の絶対パスリスト: {`## 変更内容`「対象ファイル一覧」の各ファイル絶対パス}
    - 関連参照ファイル: {改訂・拡張対象の既存規範ファイルパス一覧。空の場合は「なし」と明記}
    - メイン側担当節の実施有無: {実施済み | 未実施}。
    - メイン側実施済み観点の内訳を次に示す。
      - 機械チェック実施結果: {check_plan_file.pyの実行結果（違反件数・該当箇所）を転記}。
      - 遡及スキャン結果: {norm-revision-checklist.md規定に従い記述された内容を転記}。
      - 横断grep確認結果: {関連参照確認の実施内容と検出件数を転記}。
    - 転記された実施済み観点はplan-reviewer側で再点検を省略し、未カバー観点へ集中する。
      転記されない観点は既存規定に従い継続点検する。
    - 前回指摘一覧: {前回レビュー完了報告本文から重大度・区分・箇所・内容を通番付きで機械転記。
      要約不可。前回未実施の場合は「なし（初回）」と明記。継続レビュー時にメイン側が転記する}
    - 担当観点: 計画文内・他ファイルとの整合・変更履歴と変更内容の対応照合・機械チェック適合性・
      編集対象スキル固有規定の事前適用・サブエージェント連携の設計整合性・
      対象ファイル現状との突合による単体品質・日本語表現の重大不備（体裁・表記は軽微指摘として扱う）
    - 参照すべき規範スキル: `agent-toolkit:writing-standards`・`agent-toolkit:agent-standards`・
      `agent-toolkit:review-standards`

    ## 制約

    - 担当観点ごとに網羅的に列挙し、指摘なしも明示する
    - 重大度ラベルは`agent-toolkit:review-standards`に従う
    - 差分起因・既存違反の2区分で報告する
    - 実行可能ツール範囲: Skill・Read・Grep・Glob・Bash（frontmatter`tools:`欄準拠）
    - ファイル変更・git操作（stash・checkout・reset・commit・push等）は禁止
    - 「指摘対象外」規定は`agent-toolkit:review-standards`「計画ファイル文脈での例外」節をSSOTとする
    - 対象ファイル現状との突合観点は、計画が成立しない致命的な単体品質・日本語表現の不備検出に限定する。
      体裁・表記の指摘は軽微指摘として扱う
    - 再レビュー起動時（毎回独立コンテキストで新規起動される）は計画ファイル本文の反映確認および
      反映で新たに生じた差分の追加指摘のみを対象とする。対象ファイル本体は前回レビュー時点の状態を基準とし、
      `plan-impl-executor`起動前のため実装適用は本レビューの対象外とする

## agent-doc-validator雛形

`agent-doc-validator`サブエージェントの起動プロンプトは次のとおり。

    以下の計画ファイル本文と対象ファイル現状を突合し、`01-agent.md`方針および`agent-standards`
    スキル方針への適合性を独立にレビューしてください。

    - 計画ファイル絶対パス: {計画ファイル絶対パス}
    - 対象ファイル一覧: {コーディングエージェント向け文書のパス列挙}
    - 参照すべき規範スキル: `agent-toolkit:agent-standards`・`agent-toolkit:writing-standards`・
      `agent-toolkit:review-standards`
    - 前回指摘一覧: {前回レビュー完了報告本文から重大度・区分・箇所・内容を通番付きで機械転記。
      要約不可。前回未実施の場合は「なし（初回）」と明記。継続レビュー時にメイン側が転記する}

    ## 制約

    - 本レビューは`plan-impl-executor`起動前の事前レビューであり、対象ファイル現状は
      計画本文の追記が未反映の状態である。追記内容が対象ファイルに存在しないこと自体を
      欠落・致命的問題として指摘対象にせず、計画本文の追記案と対象ファイル現状（改訂前）
      の突合による事前品質評価に集中する
    - 担当観点および品質最優先の原則の各点検済み印を冒頭サマリーへ必ず出力する
    - 出力形式は`agent-toolkit:review-standards`スキルの規定に従う
    - 実行可能ツール範囲: Read・Grep・Glob・Bash・Skill（frontmatter`tools:`欄準拠）
    - ファイル変更・git操作（stash・checkout・reset・commit・push等）は禁止
    - 記述スタイル・実装細部への指摘も対象内とする（「指摘対象外」規定は適用しない）
    - 再レビュー起動時（毎回独立コンテキストで新規起動される）は計画ファイル本文の反映確認および
      反映で新たに生じた差分の追加指摘のみを対象とする。対象ファイル本体は前回レビュー時点の状態を基準とし、
      `plan-impl-executor`起動前のため実装適用は本レビューの対象外とする
