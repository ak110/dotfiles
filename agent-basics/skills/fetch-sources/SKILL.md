---
name: fetch-sources
user-invocable: false
description: >
  Webページ、X投稿、提示されたURLなどの外部資料について、本文・日時・添付画像を取得または確認する直前に起動する。
  執筆や実装を伴わない調査と、提示されたURLの本文取得も対象とする。
---

# 外部資料の取得

本スキルは、外部資料を調査の根拠として取得するエージェントに向けて、資料ごとの取得手順を提供する手順型のスキルである。
取得した内容を成果物や回答の事実主張に使う場合の引用・事実確認・一次資料による裏付けの基準は、`agent-basics:writing-standards`の`references/investigation.md`に従う。

資料の取得には、Web取得ツールやCLIなどの通信手段と、保存した応答を読む手段が必要である。取得手段が無い環境では、対象の同定と取得手順を整理して返し、取得していない本文は未取得として扱う。説明文の作成時は[文章の品質基準](references/quality-standards.md)を確認する。

## 名指し参照の読込方法

本文で名指しした資料と読込表の資料は、条件が成立したらその判断や操作の前に自分で全文読み、指定箇所に注目してから、その基準で判断する。読込表の列の使い方は`agent-basics:writing-standards`の`references/quality-principles.md`「読込表の読み方」に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `references/quality-standards.md` | 判断 | `read:agent-basics:*/skills/fetch-sources/references/quality-standards.md` |
| 取得した内容を事実主張・引用の根拠に使う前 | `../writing-standards/references/investigation.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/investigation.md` |
| 読込表と本文の名指し参照を読む前 | `agent-basics:writing-standards/references/quality-principles.md`の「読込表の読み方」 | 判断 | `section:agent-basics:*/skills/writing-standards/references/quality-principles.md#「読込表の読み方」` |

## 読込表

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 | 資料選択子 |
| --- | --- | --- | --- | --- |
| Xの投稿本文・日時・添付写真を取得する前 | `references/x-posts.md` | 判断 | `read:agent-basics:*/skills/fetch-sources/references/x-posts.md` | |
