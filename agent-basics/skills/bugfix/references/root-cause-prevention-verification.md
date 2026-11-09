# 再発防止の選定と完了：類似見直しと再発防止の完了

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 再発防止セルフチェック観点の追加・保守を判断する前 | `recurrence-self-check.md` | 判断 | `read:agent-basics:*/skills/bugfix/references/recurrence-self-check.md` |
| 本文の条件に従い「直接的原因と事象時点の確定」を判断する前 | `root-cause-analysis-evidence.md` | 判断 | `read:agent-basics:*/skills/bugfix/references/root-cause-analysis-evidence.md` |
| 本文の条件に従い「QとC・Dの区別」「原因分析の品質確認」を判断する前 | `root-cause-severity-quality.md`、`root-cause-severity-impact.md` | 判断 | `read:agent-basics:*/skills/bugfix/references/root-cause-severity-quality.md`、`read:agent-basics:*/skills/bugfix/references/root-cause-severity-impact.md` |
| 本文の条件に従い「処置の階層」を判断する前 | `root-cause-actions-hierarchy.md` | 判断 | `read:agent-basics:*/skills/bugfix/references/root-cause-actions-hierarchy.md` |
| 本文の条件に従い「類似見直し」を判断する前 | `agent-basics:bugfix/references/root-cause-remediation.md` | 判断 | `read:agent-basics:*/skills/bugfix/references/root-cause-remediation.md` |
| 自動検出の成立条件を判定する前（hookを候補にする場合） | `../../writing-standards/references/claude-hooks-block-warn-eligibility.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/claude-hooks-block-warn-eligibility.md` |
| 自動検出の成立条件を判定する前（lint・チェックスクリプト・テストを候補にする場合） | `agent-basics:writing-standards/references/check-script-design.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/check-script-design.md` |
| 既存の規範・手順で対応済みかを判定する前 | `../../writing-standards/references/agent-documents-basics-ownership.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/agent-documents-basics-ownership.md` |

## 類似見直しと再発防止の完了

すべての重大度で、`root-cause-remediation.md`の絞り込み型手順を完了し、候補ごとの分類・処置の要否と調査の限界を確定する。
採用した処置は、対象への適用、根本原因の各要素への作用、今回の条件下での有効性を確認して完了とする。
不採用の処置は、`root-cause-prevention-requirements.md`「再発防止策の必須性」に定める候補・費用対効果・残存リスクの記録によって採否を確定し、明示要件や硬い制約の未達とは区別する。
処置が作用する対象および完了条件として確認する対象は、`root-cause-remediation.md`「類似見直し」の段階で定めた原因条件に基づく集合と比較し、狭い範囲だけに限定して一致させたものと混同しないようにする。
軽微な問題については、観測された欠陥の修正を確認した上で、調査した範囲と限界、許容する残存リスク、ならびに再発防止策の採否を明示すれば作業を終了できる。
是正すべき範囲と未知の問題の調査を終了する基準は、`root-cause-severity-impact.md`「QとC・Dの区別」に従う。
評価や記録、後続タスクの登録、規範の条文やテストの存在、報告フォーマットの充足だけに基づく完了判定は対象外とする。
後続のタスクへの登録や他リポジトリへの依頼はあくまで処置の引き継ぎであり、引き継ぎ先と処置のステータスを記録した上で、作業完了とは区別して扱う。
調査、別件の登録、各修正、再発防止の適用と有効性確認はそれぞれの完了を区別する。
投入工程は調査と必要な引き継ぎで完了でき、元の修正も独立別件の実装を待たずに完了できる。
同じ根本原因に対する処置全体の未完了は記録に残す。
報告には、「問題を再発させない」という目標に対して実際にどのような処置を講じ、どの範囲まで有効性を確認したかを明記する。
完了の判定結果は、原因分析記録の類似見直し結果欄および再発防止策欄に記録する。

## 再発防止としてのテスト

過去に起きた問題と同じ事象が再発した場合は、前回の処置が対象としていた入力値、呼び出し元、発生条件と今回の事象を比較し、過去に作成されたテストが今回の問題箇所まで実際に届いているか（実行されているか）を確認する。
前回のテストでカバーできていなかった条件とその理由を見逃し要因および類似見直しに反映し、今回の発生条件も確実に捉えられる処置を選択する。

テストコードも`root-cause-actions-hierarchy.md`「処置の階層」に定めた基準に従って分類する。
実際に観測された入力や呼び出し元を用いて、観測された誤りを再現・検出するテストは「回帰防止策」として扱う。
これに対し「再発防止テスト」は、同節の持ち込みの試験に基づき、根本原因の要素を確実に検出できることを確認したものを指す。
どちらのテストであっても、不具合を修正する前の版でテストが正しく失敗することを確認する（修正前のコードで失敗しない場合は、テストが目的の条件分岐に到達していないと判断してテストを見直す）。
テストの期待値は要求仕様や公開契約から導き出し、現在の実装コードの出力結果、メッセージ文言、設定値などをそのまま固定しただけのテストは、単なる実装追従の記録として扱う。
テストの期待値自体が誤っていたことが見逃し要因である場合は、そのテストを本来の契約に基づいた期待値に修正するか、不要なテストとして削除する。
自動チェックを新設する場合は、判定の対象となるデータを対象ツールの公開APIや実行結果から直接取得し、ツールの内部的な選択ロジックや分類条件を再実装することは避ける。
ネットワーク、キャッシュ、実行権限など環境によって結果が異なる要因は、実際の対象環境で動作を確認し、計画書の完了条件に記載する。
修正前のコードを実際に実行し、狙ったアサーションによって失敗したことを検収する。
実行対象の版と差分の成立、失敗理由の確認、および作業ツリーの一時的な書き換え・復元は、`../../writing-standards/references/testing-conditions.md`「分岐条件の効果の裏付け」に従う。

## 本文参照の取得条件

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 再発防止の選定と完了：再発防止策の必須性の基準を適用する前 | `root-cause-prevention-requirements.md` | 判断 | `read:agent-basics:*/skills/bugfix/references/root-cause-prevention-requirements.md` |
