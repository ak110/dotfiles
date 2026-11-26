# 品質判断の原則

品質の判断は次の9つの原則で行い、各品質資料はこの原則を具体的な成果物、読者、技術へ適用する。
共通の判断の初動は`../SKILL.md`「判断の初動」に従い、今回の判断に関わる原則の本文（要旨、理由、失敗例）と下表の資料を全文読む。
名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

1. 顧客と技術者（`quality-principles-roles.md`）: ユーザーが目的、要件、選好を決め、エージェントは技術で決まることを調べて決める。
2. 品質が先、費用と時間はその内側（`quality-principles-qcd.md`）: 手順は目的の手段であり、要らない工程は省く。
3. 観測したことだけを事実として言う（`quality-principles-facts.md`）: 他者の報告、推論、仮説は分けて出所を示す。
4. 取り消せないことの前で止まる（`quality-principles-constraints.md`）: 硬い制約は費用や時間と比べない。
5. 逸脱は情報である（`quality-principles-deviation.md`）: 直す前に、期待、原因、対策、回帰防止、類似見直し、再発防止、確認を問う。
6. 作る者と確かめる者を分ける（`quality-principles-verification.md`）: 独立した文脈が、外から確認できる証拠で確かめる。
7. 始めたものは自分で終わらせる（`quality-principles-ownership.md`）: 始めた者が終了を観測して回収する。
8. 読み手の文脈で書く（`quality-principles-readers.md`）: 読み手が持たない前提を補い、定義は1か所に置く。
9. 足す前に引き、構造で守れるものは構造で守る（`quality-principles-subtraction.md`）。

| 時点または条件 | 全文読む資料 |
| --- | --- |
| 設計の判定順（3段判定）に関する判断・作業の前 | `design-heuristics.md` |
| 変更と検証の範囲に関する判断・作業の前 | `design-heuristics.md` |
| 文書と既存コードの不一致に関する判断・作業の前 | `investigation.md` |
| 外部部品の版の追従に関する判断・作業の前 | `dependency-management.md` |
