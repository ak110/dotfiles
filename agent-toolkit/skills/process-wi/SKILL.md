---
name: process-wi
description: >
  対象リポジトリのAWIを計画、実装、レビュー、公開まで処理するときに起動する。
  single-lane引数で主作業ツリーの単一処理、all-repos引数で登録全体の処理を行う。
  常駐process-loopの統括担当もall-reposで起動する。
---

# AWIの処理

引数の最初の語を確認し、共通工程を始める前に次の表から1つの処理資料を全文読む。
先頭語が完全一致する場合だけモードとして消費し、残りを追加指示として保持する。
先頭の空白と語の区切りには空白・タブ・改行を使える。
一致しない場合と引数なしは通常のレーン処理とし、引数全体を追加指示として扱う。
選んだ資料の工程に従い、他のモードの処理資料はそのモードへ切り替える時だけ読む。

## 開始後に追加されたWIの確認

選定を担当するメインは、実行開始時刻とタイムゾーンを既存の作業記録へ残す。
all-reposでは統括の巡回開始時刻を各リポジトリの担当へ引き継ぐ。
30分点検ごとと公開工程の開始前に、`references/immediate-fix.md`「開始後に追加されたWIの確認」に従って新着WIを判定する。
対象はall-reposの統括、各リポジトリの選定メイン、single-laneのメインとする。

## 即時対応

即時対応は、次のセッションで誤った行動をしないために行う。
範囲は、残る工程か次のセッションで停止・遮断、誤った判断や成果物、手戻りを生む箇所への最小限の直接修正と、その検証までとする。
テストの追加、独立した文脈でのレビュー、根本原因の分析、類似見直し、再発防止策は、正式対応として次のセッション用AWIへ分ける。
既存のAWIを使う場合は即時対応の内容を書き足す。
即時対応では通常レーンの計画・実行レビュー・公開の工程へ進まない。
対象を選ぶときは、指示が示す範囲の要求をすべて挙げ、1件ずつ「直さないと、残る工程か次のセッションで停止・遮断、誤った判断や成果物、手戻りが生じるか」で判定し、該当する項目を全件対象にする。
担当・基点・時機の表と正式対応AWIへ引き継ぐ項目は`references/immediate-fix.md`に従う。

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 30分点検を行う時と公開工程を始める前 | `agent-toolkit:process-wi/references/immediate-fix.md` | 判断 | `read:agent-toolkit:*/skills/process-wi/references/immediate-fix.md` |
| ユーザーが時機の指示として「即時対応」や同義の表現を使った時と、即時対応の対象・担当・基点・時機を決める前 | `references/immediate-fix.md` | 判断 | `read:agent-toolkit:*/skills/process-wi/references/immediate-fix.md` |

## 読込表

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 | 資料選択子 |
| --- | --- | --- | --- | --- |
| 最初の語がsingle-laneと完全一致する時 | `references/single-lane.md` | 判断 | `read:agent-toolkit:*/skills/process-wi/references/single-lane.md` | |
| 最初の語がall-reposと完全一致する時 | `references/all-repos.md` | 判断 | `read:agent-toolkit:*/skills/process-wi/references/all-repos.md` | |
| 上記の語に一致しない時、または引数なしの時 | `references/lanes.md` | 判断 | `read:agent-toolkit:*/skills/process-wi/references/lanes.md` | |
