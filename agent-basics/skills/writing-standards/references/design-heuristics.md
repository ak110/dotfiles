# 設計判断の手順と領域別の設計観点

本書では、設計判断を確定するエージェントおよび設計とコードをレビューするエージェントに向けて、判断の基準と手順を示す。
前半では、領域を問わず共通して適用する設計判断の手順を定める。
後半では、頻出する設計領域ごとに保護すべき価値と観点をまとめ、発火条件に該当する節を設計判断の確定時に参照する。

共通の設計判断では、「設計案の予備選別」から「設計品質の劣化確認」までを各節の適用条件に従って確認する。
領域別の節は、`writing-standards`の読込表に記載された並行処理、検索画面、閾値、キャッシュ、非同期処理、データベース、API、認証、アップロード、状態変更などの条件が成立する場合に、判断を下す前に参照する。
作業項目の側から参照する場合も、必要な共通節および該当領域の節に接続して利用する。

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

## 条件別資料

| 時点または条件 | 全文読む資料 |
| --- | --- |
| 設計案の予備選別に関する判断・作業の前 | `design-heuristics-screening.md` |
| 存在意義の点検に関する判断・作業の前 | `design-heuristics-necessity.md` |
| 自作機構の兆候と自問に関する判断・作業の前 | `design-heuristics-mechanisms.md` |
| 責務帰属（SRP）分析の観点に関する判断・作業の前 | `design-heuristics-responsibility.md` |
| 問題と手段の比例性に関する判断・作業の前 | `design-heuristics-proportionality.md` |
| 案の採否と実施順の判定に関する判断・作業の前 | `design-heuristics-decisions.md` |
| 設計品質の劣化確認に関する判断・作業の前 | `design-heuristics-quality.md` |
