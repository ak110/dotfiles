---
# 同期注記: 「指摘対象外」規定は agent-toolkit/skills/review-standards/SKILL.md
# 「計画ファイル文脈での例外」節と意図的に重複する。改訂時は2ファイルを同時更新する。
# 同期注記: plan-reviewer雛形内「メイン側実施済み観点の内訳」欄の文言は
# integrity-checks.md「工程7の実施手順」節・plan-reviewer.md「入力」節と意図的に重複する。
# 改訂時は3ファイルを同時更新する。
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
  `plan-impl-reviewer`は`agent-toolkit:review-standards`・`agent-toolkit:writing-standards`、
  `agent-doc-validator`は`agent-toolkit:agent-standards`・`agent-toolkit:writing-standards`・
  `agent-toolkit:review-standards`、`naive-executor`は`skills: []`のため明記対象なし）
- 制約: 実行可能ツール範囲は各サブエージェント定義のfrontmatter`tools:`欄に従う。
  破壊的操作（git commit・git push・rm・ファイル書き換え等）は禁止。
  git操作全般（stash・checkout・reset等の作業ツリー変更を含む）は禁止
- 起動プロンプトへは`agent-toolkit/agents/*.md`の規定参照
  （「`## 出力`節に従う」等）を書かない。
  当該定義ファイルはサブエージェント起動時にシステムプロンプトとして自動読込されるため再指示は冗長となる。
  呼び出し元の後続処理が完了報告書式に依存する場合は、
  書式期待を呼び出し元文書（本ファイル・`plan-impl/SKILL.md`等）に自己完結で記述する

## plan-reviewer雛形

`plan-reviewer`サブエージェントの起動プロンプトは次のとおり。

    以下の計画ファイルについて「計画文内・他ファイルとの整合」「変更履歴と変更内容の対応照合」
    「機械チェック適合性」「編集対象スキル固有規定の事前適用」「サブエージェント連携の設計整合性」の
    独立コンテキスト点検を実施してください。

    - 計画ファイル: {計画ファイル絶対パス}
    - 関連参照ファイル: {改訂・拡張対象の既存規範ファイルパス一覧。空の場合は「なし」と明記}
    - メイン側担当節の実施有無: {実施済み | 未実施}。
    - メイン側実施済み観点の内訳を次に示す。
      - 機械チェック実施結果を次に示す。
        {check_line_width・check_dash・check_line_ref・check_wc_projectionの各実行結果を列挙}。
        {textlint・markdownlint・colloquial-check・typosの各実行結果を列挙}。
      - 遡及スキャン結果: {norm-revision-checklist.md規定に従い記述された内容を転記}。
      - 横断grep確認結果: {関連参照確認の実施内容と検出件数を転記}。
    - 転記された実施済み観点はplan-reviewer側で再点検を省略し、未カバー観点へ集中する。
    - 担当観点: 計画文内・他ファイルとの整合・変更履歴と変更内容の対応照合・機械チェック適合性・
      編集対象スキル固有規定の事前適用・サブエージェント連携の設計整合性
    - 参照すべき規範スキル: `agent-toolkit:writing-standards`・`agent-toolkit:agent-standards`・
      `agent-toolkit:review-standards`

    ## 制約

    - 担当観点ごとに網羅的に列挙し、指摘なしも明示する
    - 重大度ラベルは`agent-toolkit:review-standards`に従う
    - 差分起因・既存違反の2区分で報告する
    - 実行可能ツール範囲: Skill・Read・Grep・Glob・Bash（frontmatter`tools:`欄準拠）
    - ファイル変更・git操作（stash・checkout・reset・commit・push等）は禁止
    - 以下は指摘対象外とする
      - 記述スタイル（章構成・段落構成・表現選択・書き方）への指摘。記述間の矛盾は対象に含む
      - 実装時にエージェントが判断可能な細部への指摘
        （変数名・エラーメッセージ文言・小規模なループ構造・局所的な制御フローなど）

## naive-executor雛形

naive-executorは`agent-toolkit:review-standards`を使用しないため、
雛形本文に「指摘対象外」規定を追記しない（全カテゴリの愚直な指摘が担当範囲であるため）。
`naive-executor`サブエージェントの起動プロンプトは次のとおり。

    以下の計画ファイルを愚直に読解し、暗黙補完・参照不能・手順抜け・複数解釈の
    全カテゴリで指摘リストを返してください。

    - 対象テキスト: {計画ファイル絶対パス}
    - 実行コンテキスト: これは計画ファイルである。あなたはこの計画に従って実装する実行者である
    - 抽出観点: 暗黙補完・参照不能・手順抜け・複数解釈の全カテゴリ
    - 担当範囲区分: 全項目担当

    ## 制約

    - 改善案・修正案は返さない（指摘リストのみ）
    - 全体サマリーは含めない
    - 実行可能ツール範囲: Skill・Read・Grep・Glob・Bash（frontmatter`tools:`欄準拠）
    - ファイル変更・git操作（stash・checkout・reset・commit・push等）は禁止

## plan-impl-reviewer雛形

`plan-impl-reviewer`サブエージェントの起動プロンプトは次のとおり。

    以下の改訂後最終形（一時ファイル群）について、コード・ドキュメント・日本語表現の
    単体品質を事前レビューしてください。

    - 一時ファイル絶対パスと正本パスの対応表: {表}
      （大規模計画（対象ファイル数概ね10以上または新規ファイル5以上）では既定で
      計画ファイル本文の`## 変更内容`H3配下の設計要件記述コードブロックを対象とする。
      境界解釈は`integrity-checks.md`「工程7の実施手順」節末尾の代替経路発動条件段落に従う）
    - 差分取得基準SHA: {計画着手前コミットSHA}
    - 担当観点: コード単体品質・ドキュメント単体品質・日本語表現
    - 参照すべき規範スキル: `agent-toolkit:review-standards`・`agent-toolkit:writing-standards`

    ## 制約

    - 担当観点ごとに冒頭サマリーの点検済み印を必ず出力する
    - 重大度ラベルは`agent-toolkit:review-standards`に従う
    - 差分起因・既存違反の2区分で報告する
    - 実行可能ツール範囲: Skill・Read・Grep・Glob・Bash（frontmatter`tools:`欄準拠）
    - ファイル変更・git操作（stash・checkout・reset・commit・push等）は禁止
    - レビュー対象は改訂後最終形、または大規模計画時は`## 変更内容`H3の設計要件記述コードブロックとする
    - 改訂後最終形審査時は「指摘対象外」規定を適用せず通常の単体品質基準で評価する
      （実際に配布・保存される成果物のため記述スタイル・実装細部も対象内）
    - 設計要件記述を審査する場合も記述スタイル・実装細部への指摘を対象内とする

上記の設計要件記述に対する審査の扱いは、`integrity-checks.md`「工程7の実施手順」節の代替経路と整合させる。

## agent-doc-validator雛形

`agent-doc-validator`サブエージェントの起動プロンプトは次のとおり。

    以下の改訂後最終形について、`01-agent.md`方針および`agent-standards`スキル方針への
    適合性を独立にレビューしてください。

    - 計画ファイル絶対パス: {計画ファイル絶対パス}
    - 対象ファイル一覧: {コーディングエージェント向け文書のパス列挙}
    - 参照すべき規範スキル: `agent-toolkit:agent-standards`・`agent-toolkit:writing-standards`・
      `agent-toolkit:review-standards`

    ## 制約

    - 担当観点および品質最優先の原則の各点検済み印を冒頭サマリーへ必ず出力する
    - 出力形式は`agent-toolkit:review-standards`スキルの規定に従う
    - 実行可能ツール範囲: Read・Grep・Glob・Bash・Skill（frontmatter`tools:`欄準拠）
    - ファイル変更・git操作（stash・checkout・reset・commit・push等）は禁止
    - レビュー対象は改訂後最終形（実際に配布・保存される成果物）であり、
      廃棄される計画ファイル本文ではないため「指摘対象外」規定は適用しない
      （記述スタイル・実装細部も含め通常の単体品質基準で評価する）
