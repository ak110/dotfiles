---
name: external-write-review
user-invocable: false
description: >
  PR・MR・イシューの作成と本文編集、コメント、レビュー返信、Release本文、チャットやメールへのMCP経由の送信など、第三者が読む外部サービスへエージェントが起草または加筆した人間向けの文面を送る直前に起動する。
---

# 外部サービスへの投稿前レビュー

第三者へ届く文面を、投稿前に起草者と独立した読み取り専用の担当に検証させる。
対象の判定、レビューの依頼、指摘の採否と投稿は投稿するエージェントが担い、投稿操作の認可は`agent-toolkit:user-confirmation-and-report`で判定する。

## 読込表

次の時点または条件が成立したら、その操作の前に同じ行の資料を全文読む。

| 時点または条件 | 全文読む資料 |
| --- | --- |
| 対象の文面を確定し、レビュー担当を起動する前 | `${CLAUDE_PLUGIN_ROOT}/share/external-write-review.parent.md` |

## 対象

第三者が読む外部サービスへエージェントが起草または加筆した人間向け文面を対象とする。
ユーザーが逐語で渡した文面、git push、コミットメッセージ（`agent-toolkit:commit`）は対象外とする。
private-notesのWI本文も対象外であり、`agent-toolkit:wi-standards`と`${CLAUDE_PLUGIN_ROOT}/share/add-wi.parent.md`に従う。
