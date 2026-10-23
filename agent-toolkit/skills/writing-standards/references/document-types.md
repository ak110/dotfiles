# 文書の役割と種類

品質基準の正式な提供元は`agent-basics:writing-standards`の`references/document-types.md`である。呼出元が指定した条件と読込範囲を、同名の正式な品質資料へ引き継ぐ。節指定がある場合は移設先の実見出しへ接続し、指定がない場合は従来の全文読込契約を保つ。

以下はagent-toolkitが所有する運用条件である。呼出元の条件と読込範囲に従い、ここに残る運用節を補う。

## 名指し参照の読込方法

本文の各参照の適用条件を保ち、選んだ資料の指定範囲を取得してから、その基準を使う具体的な判断へ進む。起動時の行の資料は起動本文とともに届く。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:writing-standards/references/document-types.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/document-types.md` |

## 運用条件

| 文書 | 主な読者と役割 | 伝える内容と詳細規定 |
| --- | --- | --- |
| 計画ファイル | 実行担当が変更を進める作業用 | 要件、変更、検証、終端と進捗の対応。形式は`agent-toolkit:plan-mode` |
| AWI・UWI | ユーザーと実行担当が要求と確認を共有する作業用 | AWIの対象と完成条件、UWIの問いと判断材料。形式は`agent-toolkit:wi-standards` |
| 実行レビュー指摘管理表 | 実装担当とレビュー担当が是正を追跡する作業用 | 指摘、根拠、採否と対応結果。形式は`agent-toolkit:review-standards` |
| コミットメッセージ | 開発者が変更目的を履歴からたどる記録 | 何をなぜ変更したかを件名と必要な本文で示す。形式は`agent-toolkit:commit` |
| PR・MRの説明 | レビュアーと関係者へ審査を依頼する連絡 | 変更の目的、届く結果と確認済みの範囲。文章は`agent-basics:writing-standards/references/writing.md`「文書種別ごとの注意点」の「業務・仕様文書」、投稿前の確認は`agent-toolkit:external-write-review` |
| Release本文 | エンドユーザーと開発者へ配布済みの変更を伝える記録 | エンドユーザーへ届く変更、必要な操作と注意点。投稿前の確認は`agent-toolkit:external-write-review` |
