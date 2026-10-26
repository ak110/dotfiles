# 文書の役割と種類

品質基準の正式な提供元は`agent-basics:writing-standards`の`references/document-types.md`である。呼び出し元が指定した条件と読み込み範囲は、同名の正式な品質資料へ引き継ぐ。節の指定がある場合は移設先の実見出しへ接続し、指定がない場合は従来どおり全文を読み込む契約を保つ。

以下はagent-toolkit固有の運用条件である。呼び出し元の条件や読み込み範囲に応じて、この文書に残る運用節を参照する。

## 名指し参照の読込方法

本文の各参照における適用条件を守り、選定した資料の指定範囲を取得してから、その基準に基づく具体的な判断へ進む。起動時の行に示された資料は、起動本文とともに配送される。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:writing-standards/references/document-types.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/document-types.md` |

## 運用条件

| 文書 | 主な読者と役割 | 伝える内容と詳細規定 |
| --- | --- | --- |
| 計画ファイル | 実行担当者が変更を進めるための作業用文書 | 要件、変更内容、検証手順、終端条件と進捗の対応関係。形式は`agent-toolkit:plan-mode`に従う |
| AWI・UWI | ユーザーと実行担当者が要求や確認事項を共有するための作業用文書 | AWIの対象と完成条件、UWIの問いと判断材料。形式は`agent-toolkit:wi-standards`に従う |
| 実行レビュー指摘管理表 | 実装担当者とレビュー担当者が是正状況を追跡するための作業用文書 | 指摘内容、根拠、採否、対応結果。形式は`agent-toolkit:review-standards`に従う |
| コミットメッセージ | 開発者が変更の目的を履歴から確認するための記録 | 変更の対象と理由を件名および必要な本文で示す。形式は`agent-toolkit:commit`に従う |
| PR・MRの説明 | レビュアーや関係者へ審査を依頼するための連絡文書 | 変更の目的、得られる結果、確認済みの範囲。文章表現は`agent-basics:writing-standards/references/writing.md`「文書種別ごとの注意点」の「業務・仕様文書」に従い、投稿前の確認は`agent-toolkit:external-write-review`に従う |
| Release本文 | エンドユーザーや開発者へ配布済みの変更を伝えるための記録 | エンドユーザーに届く変更内容、必要な操作、注意点。投稿前の確認は`agent-toolkit:external-write-review`に従う |
