# 文書の役割と種類

品質基準の正式な提供元は`agent-basics:writing-standards/references/document-types.md`である。呼び出し元の取得条件と注目箇所を引き継ぎ、本書と正式な資料を全文読む。節指定がある場合は、指定された見出しを持つ資料に注目する。両方に同名の見出しがあれば両方に注目する。

以下はagent-toolkit固有の運用条件である。

## 名指し参照の読込方法

名指し参照は`../SKILL.md`「名指し参照の読込方法」に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:writing-standards/references/document-types.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/document-types.md` |

## 運用条件

| 文書 | 主な読者と役割 | 伝える内容と詳細規定 |
| --- | --- | --- |
| 計画ファイル | 実行担当者が変更を進めるための作業用文書 | 要件、変更内容、検証手順、終了条件と進捗の対応関係。形式は`agent-toolkit:write-plan`に従う |
| AWI・UWI | ユーザーと実行担当者が要求や確認事項を共有するための作業用文書 | AWIの対象と完成条件、UWIの問いと判断材料。形式は`agent-toolkit:wi-standards`に従う |
| 実行レビュー指摘管理表 | 実装担当者とレビュー担当者が是正状況を追跡するための作業用文書 | 指摘内容、根拠、採否、対応結果。形式は`agent-basics:review-standards`に従う |
| コミットメッセージ | 開発者が変更の目的を履歴から確認するための記録 | 変更の対象と理由を件名および必要な本文で示す。形式は`agent-toolkit:commit`に従う |
| PR・MRの説明 | レビュアーや関係者へ審査を依頼するための連絡文書 | 変更の目的、得られる結果、確認済みの範囲。文章表現は`agent-basics:writing-standards/references/writing.md`「文書種別ごとの注意点」の「業務・仕様文書」に従い、投稿前の確認は`agent-toolkit:review-external-writes`に従う |
| Release本文 | エンドユーザーや開発者へ配布済みの変更を伝えるための記録 | エンドユーザーに届く変更内容、必要な操作、注意点。投稿前の確認は`agent-toolkit:review-external-writes`に従う |
