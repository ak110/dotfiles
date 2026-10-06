---
name: external-write-review
user-invocable: false
description: >
  PR・MR・イシューの作成と本文編集、コメント、レビュー返信、Release本文、チャットやメールへのMCP経由の送信など、第三者が読む外部サービスへエージェントが起草または加筆した人間向けの文面を送る直前に起動する。
---

# 外部サービスへの投稿前レビュー

本スキルは投稿後に第三者へ届く文面を、起草者と独立した読み取り専用の担当が投稿前に検証する手順を提供する。
投稿する主体が対象の判定、レビューの依頼、指摘の採否および投稿を担う。
投稿操作の認可は`agent-toolkit:user-confirmation-and-report`に従って判定する。

## 読込表

次の時点または条件が成立したら、その操作の前に同じ行の資料を全文読む。

| 時点または条件 | 全文読む資料 |
| --- | --- |
| 対象の文面を確定し、レビュー担当を起動する前 | `${CLAUDE_PLUGIN_ROOT}/share/external-write-review.parent.md` |

## 対象と入力

第三者が読む外部サービスへエージェントが起草または加筆した人間向け文面を対象とする。
ユーザーが逐語で渡した文面、git push、コミットメッセージおよびprivate-notesのWI本文は対象外とする。
git pushとコミットメッセージは`agent-toolkit:commit`、WI本文は`agent-toolkit:wi-standards`とWI投入担当（`${CLAUDE_PLUGIN_ROOT}/share/add-wi.parent.md`）の契約に従う。

投稿する主体は確定した文面をUTF-8のファイルへ保存し、投稿先と投稿の目的、文面が根拠とする差分・観測結果・関連ファイルの所在を対応付ける。

## 起動と受領

投稿する主体は読込表の`${CLAUDE_PLUGIN_ROOT}/share/external-write-review.parent.md`に従ってレビュー担当を起動し、結果を受領する。
レビュー担当の観点、読者像および返却形式は`external-write-review.subagent.md`が定めるため、委譲プロンプトへ書かない。
