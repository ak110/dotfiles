# レビューイーの品質基準と運用：同じ指摘の反復

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

## 同じ指摘の反復

再レビュー時に指摘を受け取った場合は、修正方針を確定する前に`atk review-table show <表の絶対パス> --track <対象track> --format=jsonl`を実行し、これまでの全ラウンドの指摘と応答内容を確認する。
現在のラウンドのサマリーに記載されている過去のラウンドや行番号の情報を手がかりにして、今回の指摘と過去の指摘について、違反とされた規定や発生条件を比較する。
同じ論点で修正が繰り返されている場合は、ユーザー自身の判断によって意図や変更範囲が分かれる場合に限り、変更前にユーザーへ確認する。
修正回数が多いことだけを理由に確認へ送らない。

## round 3以降の方式見直し

修正案の比較や再レビューの適用範囲については、`agent-basics:review-standards/references/reviewee-verification.md`「方式見直しと再レビュー」に従う。
各指摘の既存の応答ラベルに理由と結論を記録し、要件や認可の変更が必要な場合は`agent-toolkit:/share/review-loop-coordination.md`のエスカレーション手順へ報告する。
