# 工程7サブエージェント起動プロンプト雛形

`agent-toolkit:plan-mode`スキル工程7で起動する4サブエージェント
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

## plan-reviewer雛形

`plan-reviewer`サブエージェントの起動プロンプトは次のとおり。

    以下の計画ファイルについて「計画文内・他ファイルとの整合」「機械チェック適合性」
    「編集対象スキル固有規定の事前適用」「サブエージェント連携の設計整合性」の独立コンテキスト点検を実施してください。

    - 計画ファイル: {計画ファイル絶対パス}
    - 関連参照ファイル: {改訂・拡張対象の既存規範ファイルパス一覧。空の場合は「なし」と明記}
    - メイン側担当節の実施有無: {実施済み | 未実施}（ユーザー発話・提示素材照合・横断grep確認等の
      integrity-checks.md「メイン側担当部分」節の各項目）
    - 担当観点: 計画文内・他ファイルとの整合・機械チェック適合性・編集対象スキル固有規定の事前適用・
      サブエージェント連携の設計整合性
    - 参照すべき規範スキル: `agent-toolkit:writing-standards`・`agent-toolkit:agent-standards`・
      `agent-toolkit:review-standards`
    - 完了報告書式: `agent-toolkit/agents/plan-reviewer.md`の`## 出力`節に従う

    ## 制約

    - 担当観点ごとに網羅的に列挙し、指摘なしも明示する
    - 重大度ラベルは`agent-toolkit:review-standards`に従う
    - 差分起因・既存違反の2区分で報告する
    - 実行可能ツール範囲: Skill・Read・Grep・Glob・Bash（frontmatter`tools:`欄準拠）
    - ファイル変更・git操作（stash・checkout・reset・commit・push等）は禁止

## naive-executor雛形

`naive-executor`サブエージェントの起動プロンプトは次のとおり。

    以下の計画ファイルを愚直に読解し、暗黙補完・参照不能・手順抜け・複数解釈の
    4カテゴリ全件で指摘リストを返してください。

    - 対象テキスト: {計画ファイル絶対パス}
    - 実行コンテキスト: これは計画ファイルである。あなたはこの計画に従って実装する実行者である
    - 抽出観点: 暗黙補完・参照不能・手順抜け・複数解釈の4カテゴリ全件
    - 担当範囲区分: 全項目担当
    - 完了報告書式: `agent-toolkit/agents/naive-executor.md`の規定に従う

    ## 制約

    - 改善案・修正案は返さない（指摘リストのみ）
    - 出力形式は`agent-toolkit/agents/naive-executor.md`の既存出力仕様に従う（全体サマリー禁止）
    - 実行可能ツール範囲: Skill・Read・Grep・Glob・Bash（frontmatter`tools:`欄準拠）
    - ファイル変更・git操作（stash・checkout・reset・commit・push等）は禁止

## plan-impl-reviewer雛形

`plan-impl-reviewer`サブエージェントの起動プロンプトは次のとおり。

    以下の改訂後最終形（一時ファイル群）について、コード・ドキュメント・日本語表現の
    単体品質を事前レビューしてください。

    - 一時ファイル絶対パスと正本パスの対応表: {表}
    - 差分取得基準SHA: {計画着手前コミットSHA}
    - 担当観点: コード単体品質・ドキュメント単体品質・日本語表現
    - 参照すべき規範スキル: `agent-toolkit:review-standards`・`agent-toolkit:writing-standards`
    - 完了報告書式: `agent-toolkit/agents/plan-impl-reviewer.md`の`## 出力`節に従う

    ## 制約

    - 担当観点3カテゴリごとに冒頭サマリーの点検済み印を必ず出力する
    - 重大度ラベルは`agent-toolkit:review-standards`に従う
    - 差分起因・既存違反の2区分で報告する
    - 実行可能ツール範囲: Skill・Read・Grep・Glob・Bash（frontmatter`tools:`欄準拠）
    - ファイル変更・git操作（stash・checkout・reset・commit・push等）は禁止

## agent-doc-validator雛形

`agent-doc-validator`サブエージェントの起動プロンプトは次のとおり。

    以下の改訂後最終形について、`01-agent.md`方針および`agent-standards`スキル方針への
    適合性を独立にレビューしてください。

    - 計画ファイル絶対パス: {計画ファイル絶対パス}
    - 対象ファイル一覧: {コーディングエージェント向け文書のパス列挙}
    - 担当観点: `agent-toolkit/agents/agent-doc-validator.md`「## 担当観点」節の10観点＋品質最優先の原則適合性
    - 参照すべき規範スキル: `agent-toolkit:agent-standards`・`agent-toolkit:writing-standards`・
      `agent-toolkit:review-standards`
    - 完了報告書式: `agent-toolkit/agents/agent-doc-validator.md`の`## 出力形式`節に従う

    ## 制約

    - 担当観点10件＋品質最優先原則の各点検済み印を冒頭サマリーへ必ず出力する
    - 出力形式は`agent-toolkit:review-standards`スキルの規定に従う
    - 実行可能ツール範囲: Read・Grep・Glob・Bash・Skill（frontmatter`tools:`欄準拠）
    - ファイル変更・git操作（stash・checkout・reset・commit・push等）は禁止
